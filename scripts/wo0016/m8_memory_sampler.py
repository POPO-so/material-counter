"""Read-only, one-second /proc sampler for a separately authorized probe run."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import select
import sys
import time
import uuid
from typing import Any, TextIO

SAMPLE_INTERVAL_S = 1.0
MEM_AVAILABLE_FLOOR_KIB = 122880
CMA_FREE_FLOOR_KIB = 32768
_MEM_FIELDS = ("MemAvailable", "MemFree", "CmaFree", "CmaTotal")
_STATUS_FIELDS = ("VmRSS", "VmHWM")


def parse_starttime(stat_text: str) -> int:
    """Parse /proc/PID/stat field 22, preserving comm strings with spaces or ')'s."""
    text = stat_text.strip()
    close = text.rfind(")")
    if close < 0:
        raise ValueError("malformed_proc_stat_comm")
    prefix = text[:close + 1]
    if re.match(r"^\d+ \(.*\)$", prefix) is None:
        raise ValueError("malformed_proc_stat_prefix")
    fields_from_state = text[close + 1:].split()
    # fields_from_state[0] is field 3 (state), so field 22 has index 19.
    if len(fields_from_state) <= 19:
        raise ValueError("proc_stat_missing_starttime")
    raw_starttime = fields_from_state[19]
    if not raw_starttime.isdecimal():
        raise ValueError("invalid_proc_stat_starttime")
    return int(raw_starttime)


def _read_starttime(stat_path: Path, expected_pid: int) -> int:
    """Read stat field 22 and verify that the record still names the requested PID."""
    text = stat_path.read_text(encoding="ascii").strip()
    close = text.rfind(")")
    if close < 0:
        raise ValueError("malformed_proc_stat_comm")
    prefix = text[:close + 1]
    match = re.match(r"^(\d+) \(.*\)$", prefix)
    if match is None:
        raise ValueError("malformed_proc_stat_prefix")
    if int(match.group(1)) != expected_pid:
        raise ValueError("proc_stat_pid_mismatch")
    return parse_starttime(text)


def _read_kib_fields(path: Path, names: tuple[str, ...]) -> tuple[dict[str, int | None], list[str]]:
    """Read selected Linux kB fields, keeping valid values when another is absent."""
    values: dict[str, int | None] = {name: None for name in names}
    errors: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="ascii").splitlines():
        key, separator, raw = line.partition(":")
        if key not in values:
            continue
        if key in seen:
            values[key] = None
            errors.append(f"duplicate_{key}")
            continue
        seen.add(key)
        parts = raw.split()
        if not separator or len(parts) != 2 or parts[1] != "kB" \
                or not parts[0].isdecimal():
            errors.append(f"invalid_{key}_format")
            continue
        values[key] = int(parts[0])
    for key, value in values.items():
        if value is None and key not in {e.removeprefix("invalid_").removesuffix("_format")
                                         for e in errors}:
            errors.append(f"missing_{key}")
    return values, errors


def sample_once(pid: int, starttime: int, boot_id: str, *,
                proc_root: str | Path = Path("/proc"),
                clock_ns=time.monotonic_ns) -> dict[str, Any]:
    """Capture a process and system-memory sample without substituting missing data."""
    root = Path(proc_root)
    sample: dict[str, Any] = {
        "status": "OK", "pid": pid, "starttime_ticks": starttime,
        "boot_id": boot_id, "monotonic_ns": int(clock_ns()),
        "process_kib": {name: None for name in _STATUS_FIELDS},
        "meminfo_kib": {name: None for name in _MEM_FIELDS}, "errors": [],
    }
    errors: list[str] = sample["errors"]
    stat_path = root / str(pid) / "stat"
    boot_path = root / "sys/kernel/random/boot_id"

    try:
        before_boot = boot_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        before_boot = None
        errors.append(f"boot_id_read_failed:{type(exc).__name__}")
    try:
        before_starttime = _read_starttime(stat_path, pid)
    except (OSError, ValueError, UnicodeError) as exc:
        before_starttime = None
        errors.append(str(exc) if isinstance(exc, ValueError)
                      else f"target_stat_read_failed:{type(exc).__name__}")

    # Do not read a process status file unless its PID/start time matched beforehand.
    if before_boot == boot_id and before_starttime == starttime:
        try:
            process, field_errors = _read_kib_fields(
                root / str(pid) / "status", _STATUS_FIELDS)
            sample["process_kib"] = process
            errors.extend(field_errors)
        except (OSError, UnicodeError) as exc:
            errors.append(f"target_status_read_failed:{type(exc).__name__}")
    else:
        if before_boot != boot_id and "boot_id_read_failed:" not in " ".join(errors):
            errors.append("boot_id_changed")
        if before_starttime != starttime and not any(
                error.startswith("target_stat_read_failed:") for error in errors):
            errors.append("target_starttime_changed")

    try:
        meminfo, field_errors = _read_kib_fields(root / "meminfo", _MEM_FIELDS)
        sample["meminfo_kib"] = meminfo
        errors.extend(field_errors)
    except (OSError, UnicodeError) as exc:
        errors.append(f"meminfo_read_failed:{type(exc).__name__}")

    try:
        after_starttime = _read_starttime(stat_path, pid)
    except (OSError, ValueError, UnicodeError) as exc:
        after_starttime = None
        errors.append(str(exc) if isinstance(exc, ValueError)
                      else f"target_stat_recheck_failed:{type(exc).__name__}")
    try:
        after_boot = boot_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        after_boot = None
        errors.append(f"boot_id_recheck_failed:{type(exc).__name__}")
    if before_boot == boot_id and after_boot != boot_id:
        errors.append("boot_id_changed")
    if before_starttime == starttime and after_starttime != starttime:
        errors.append("target_starttime_changed")

    meminfo = sample["meminfo_kib"]
    if meminfo["MemAvailable"] is not None \
            and meminfo["MemAvailable"] < MEM_AVAILABLE_FLOOR_KIB:
        errors.append("memavailable_below_122880_kib")
    if meminfo["CmaFree"] is not None and meminfo["CmaFree"] < CMA_FREE_FLOOR_KIB:
        errors.append("cmafree_below_32768_kib")
    cma_free = meminfo["CmaFree"]
    cma_total = meminfo["CmaTotal"]
    if cma_free is not None and cma_total is not None and cma_free > cma_total:
        errors.append("cmafree_exceeds_cmatotal")
    process = sample["process_kib"]
    vmrss = process["VmRSS"]
    vmhwm = process["VmHWM"]
    if vmrss is not None and vmhwm is not None and vmhwm < vmrss:
        errors.append("vmhwm_below_vmrss")
    if errors:
        sample["status"] = "ERROR"
        sample["errors"] = list(dict.fromkeys(errors))
    return sample


class _JsonlJournal:
    """Create an exclusive private journal and durably append each JSON record."""

    def __init__(self, output: Path):
        if not output.is_absolute():
            raise ValueError("sampler output must be an absolute path")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        fd = os.open(str(output), flags, 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            else:  # Allows offline tests on Windows; Linux uses fchmod above.
                os.chmod(output, 0o600)
            self.stream = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    def write(self, record: dict[str, Any]) -> None:
        self.stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self) -> None:
        self.stream.close()


def _emit(stream: TextIO, record: dict[str, Any]) -> None:
    stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False) + "\n")
    stream.flush()


def _validate_sample_args(pid: int, starttime: int, boot_id: str,
                          sampler_pid: int) -> None:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("target pid must be a positive integer")
    if isinstance(starttime, bool) or not isinstance(starttime, int) or starttime <= 0:
        raise ValueError("target starttime must be a positive integer")
    if pid == sampler_pid:
        raise ValueError("sampler cannot target itself")
    try:
        parsed = uuid.UUID(boot_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("boot id must be a UUID") from exc
    if str(parsed) != boot_id:
        raise ValueError("boot id must use canonical lowercase UUID form")


def _sample_errors(sample: dict[str, Any]) -> list[str]:
    errors = sample.get("errors")
    return list(errors) if isinstance(errors, list) else ["sample_error_details_missing"]


class _SampleSummary:
    """Constant-space aggregation for an arbitrarily long sampling session."""

    __slots__ = ("sample_count", "first_monotonic_ns", "last_monotonic_ns",
                 "peak_vmrss_kib", "max_vmhwm_kib",
                 "minimum_memavailable_kib", "minimum_cmafree_kib")

    def __init__(self) -> None:
        self.sample_count = 0
        self.first_monotonic_ns: int | None = None
        self.last_monotonic_ns: int | None = None
        self.peak_vmrss_kib: int | None = None
        self.max_vmhwm_kib: int | None = None
        self.minimum_memavailable_kib: int | None = None
        self.minimum_cmafree_kib: int | None = None

    @staticmethod
    def _max(current: int | None, value: int | None) -> int | None:
        if value is None:
            return current
        return value if current is None else max(current, value)

    @staticmethod
    def _min(current: int | None, value: int | None) -> int | None:
        if value is None:
            return current
        return value if current is None else min(current, value)

    def add(self, sample: dict[str, Any]) -> None:
        monotonic_ns = sample["monotonic_ns"]
        if self.sample_count == 0:
            self.first_monotonic_ns = monotonic_ns
        self.sample_count += 1
        self.last_monotonic_ns = monotonic_ns
        process = sample["process_kib"]
        memory = sample["meminfo_kib"]
        self.peak_vmrss_kib = self._max(self.peak_vmrss_kib, process["VmRSS"])
        self.max_vmhwm_kib = self._max(self.max_vmhwm_kib, process["VmHWM"])
        self.minimum_memavailable_kib = self._min(
            self.minimum_memavailable_kib, memory["MemAvailable"])
        self.minimum_cmafree_kib = self._min(
            self.minimum_cmafree_kib, memory["CmaFree"])

    def as_dict(self, *, status: str, final_valid: bool, sampler_pid: int,
                target_pid: int, starttime: int, boot_id: str,
                errors: list[str]) -> dict[str, Any]:
        return {"status": status, "final_valid": final_valid,
                "sampler_pid": sampler_pid, "target_pid": target_pid,
                "target_starttime_ticks": starttime, "boot_id": boot_id,
                "sample_count": self.sample_count, "error_count": len(errors),
                "errors": list(dict.fromkeys(errors)),
                "sampling_interval_s": SAMPLE_INTERVAL_S,
                "observed_start_monotonic_ns": self.first_monotonic_ns,
                "observed_end_monotonic_ns": self.last_monotonic_ns,
                "peak_observed_vmrss_kib": self.peak_vmrss_kib,
                "max_observed_vmhwm_kib": self.max_vmhwm_kib,
                "minimum_observed_memavailable_kib": self.minimum_memavailable_kib,
                "minimum_observed_cmafree_kib": self.minimum_cmafree_kib,
                "claims_exact_process_peak": False}


def run_protocol(*, pid: int, starttime: int, boot_id: str,
                 output: str | Path, input_stream: TextIO = sys.stdin,
                 output_stream: TextIO = sys.stdout,
                 proc_root: str | Path = Path("/proc"),
                 sampler_pid: int | None = None,
                 clock_ns=time.monotonic_ns) -> int:
    """Sample one PID until stop, never signaling it or reading unrelated proc data."""
    sampler_pid = os.getpid() if sampler_pid is None else sampler_pid
    journal: _JsonlJournal | None = None
    errors: list[str] = []
    summary = _SampleSummary()
    completed_cleanly = False
    pending_final: dict[str, Any] | None = None
    journal_closed = False
    try:
        journal = _JsonlJournal(Path(output))
        journal.write({"event": "IDENTITY", "schema": 1, "sampler_pid": sampler_pid,
                       "pid": pid, "starttime_ticks": starttime, "boot_id": boot_id,
                       "sample_interval_s": SAMPLE_INTERVAL_S})
        _validate_sample_args(pid, starttime, boot_id, sampler_pid)
    except (OSError, ValueError, TypeError) as exc:
        message = f"sampler_start_failed:{type(exc).__name__}:{exc}"
        errors.append(message)
        if journal is not None:
            try:
                journal.write({"event": "ERROR", "status": "ERROR", "errors": [message]})
            except OSError:
                pass
            try:
                journal.close()
            except OSError:
                pass
        _emit(output_stream, {"event": "ERROR", "status": "ERROR", "errors": [message]})
        return 2

    def take_sample(event: str) -> dict[str, Any]:
        sample = sample_once(pid, starttime, boot_id,
                             proc_root=proc_root, clock_ns=clock_ns)
        summary.add(sample)
        valid = sample["status"] == "OK"
        record = {"event": event if valid else "ERROR", "status": sample["status"],
                  "sample_event": event, "sample": sample}
        if not valid:
            errors.extend(_sample_errors(sample))
        journal.write(record)
        return sample

    def emit_sample_error(sample: dict[str, Any]) -> None:
        _emit(output_stream, {"event": "ERROR", "status": "ERROR", "sample": sample,
                              "errors": _sample_errors(sample)})

    def emit_protocol_error(message: str) -> None:
        errors.append(message)
        record = {"event": "ERROR", "status": "ERROR", "errors": [message]}
        try:
            journal.write(record)
        except OSError:
            pass
        _emit(output_stream, record)

    try:
        baseline = take_sample("SAMPLE")
        if baseline["status"] != "OK":
            emit_sample_error(baseline)
        else:
            _emit(output_stream, {"event": "READY", "status": "OK", "sample": baseline})
            next_sample_at = time.monotonic() + SAMPLE_INTERVAL_S
            while True:
                timeout = max(0.0, next_sample_at - time.monotonic())
                try:
                    ready, _, _ = select.select([input_stream], [], [], timeout)
                except Exception as exc:
                    if isinstance(input_stream, io.StringIO):
                        # StringIO has no file descriptor; allow only this explicit test seam.
                        ready = [input_stream]
                    else:
                        emit_protocol_error(
                            f"parent_input_select_failed:{type(exc).__name__}")
                        break
                if not ready:
                    periodic = take_sample("SAMPLE")
                    if periodic["status"] != "OK":
                        emit_sample_error(periodic)
                        break
                    now = time.monotonic()
                    next_sample_at += SAMPLE_INTERVAL_S
                    if next_sample_at <= now:
                        next_sample_at = now + SAMPLE_INTERVAL_S
                    continue

                line = input_stream.readline()
                if line == "":
                    emit_protocol_error("input_eof_before_stop")
                    break
                try:
                    command = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    emit_protocol_error("invalid_parent_command_json")
                    break
                if not isinstance(command, dict):
                    emit_protocol_error("parent_command_must_be_object")
                    break
                action = command.get("command")
                label = command.get("label")
                if action not in {"checkpoint", "stop"} or not isinstance(label, str) \
                        or not label or len(label) > 96:
                    emit_protocol_error("invalid_parent_command")
                    break

                event = "CHECKPOINT" if action == "checkpoint" else "FINAL_SAMPLE"
                sample = take_sample(event)
                if sample["status"] != "OK":
                    emit_sample_error(sample)
                    break
                if action == "checkpoint":
                    _emit(output_stream, {"event": "CHECKPOINT", "status": "OK",
                                          "label": label, "sample": sample})
                    continue

                result = summary.as_dict(
                    status="OK", final_valid=True, sampler_pid=sampler_pid,
                    target_pid=pid, starttime=starttime, boot_id=boot_id,
                    errors=errors)
                journal.write({"event": "SUMMARY", "status": "OK", "summary": result})
                pending_final = {"event": "FINAL", "status": "OK", "label": label,
                                 "sample": sample, "summary": result}
                break
    except (OSError, ValueError, TypeError) as exc:
        emit_protocol_error(f"sampler_runtime_failed:{type(exc).__name__}:{exc}")
    finally:
        try:
            journal.close()
            journal_closed = True
        except Exception as exc:
            journal_closed = False
            close_error = f"journal_close_failed:{type(exc).__name__}"
            errors.append(close_error)
            _emit(output_stream, {"event": "ERROR", "status": "ERROR",
                                  "errors": [close_error]})
    if pending_final is not None and journal_closed:
        _emit(output_stream, pending_final)
        completed_cleanly = True
    return 0 if completed_cleanly and not errors else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--starttime", required=True, type=int)
    parser.add_argument("--boot-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return run_protocol(pid=args.pid, starttime=args.starttime,
                        boot_id=args.boot_id, output=args.output)


if __name__ == "__main__":
    raise SystemExit(main())
