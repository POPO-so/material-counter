"""Independently audit the narrow refresh_module_reference capture reset case."""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
from pathlib import Path
import re
import sys
from datetime import datetime, timezone
from typing import Any


class AuditError(ValueError):
    pass


CAPTURE_RESET = re.compile(
    r"^CAPTURE_FAIL:native message export incomplete or counters changed: "
    r"WARNING emitted=(-[1-9][0-9]*) exported=([0-9]+)$"
)
SEVERITY = re.compile(r"^\s*(CRITICAL WARNING|WARNING|ERROR):")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def windows_path_key(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value.replace("/", "\\")))


def audit_capture(
    receipt: dict[str, Any],
    *,
    require_result: bool = True,
    scope: str = "refresh_region only; original ipcfg capture receipt remains FAIL",
) -> dict[str, Any]:
    """Validate one capture receipt and exactly its recorded native-log byte slice."""
    if receipt.get("status") != "FAIL" or receipt.get("code") != 0:
        raise AuditError("adaptation requires original capture status FAIL with Tcl code 0")

    match = CAPTURE_RESET.fullmatch(str(receipt.get("capture_error", "")))
    if not match:
        raise AuditError("capture_error is outside the sole allowed negative WARNING-counter reset")

    try:
        warning_before = int(receipt["warning_before"])
        warning_after = int(receipt["warning_after"])
        start = int(receipt["start"])
        end = int(receipt["end"])
        exported = int(match.group(2))
        emitted = int(match.group(1))
        native_log = Path(receipt["native_log"])
        expected_messages = [str(item).rstrip("\r\n") for item in receipt["warning_messages"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditError(f"required capture evidence is missing or malformed: {exc}") from exc

    expected_result = actual_result = None
    if require_result:
        try:
            expected_result = str(receipt["expected_result"])
            actual_result = str(receipt["raw"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuditError(f"required result evidence is missing or malformed: {exc}") from exc

    if not native_log.is_file():
        raise AuditError("recorded native log does not exist")
    payload = native_log.read_bytes()
    if start < 0 or end <= start or end > len(payload):
        raise AuditError("recorded native-log byte range is missing or outside the log")
    if (start and payload[start - 1] != 0x0A) or payload[end - 1] != 0x0A:
        raise AuditError("recorded native-log byte range starts or ends inside a line")
    if emitted != warning_after - warning_before or emitted >= 0:
        raise AuditError("warning counter delta does not match the recorded negative reset")
    if require_result and windows_path_key(actual_result) != windows_path_key(expected_result):
        raise AuditError("refresh_module_reference returned an unexpected result")

    selected = payload[start:end]
    try:
        lines = selected.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise AuditError("native-log byte range is not valid UTF-8") from exc

    counts = {"WARNING": 0, "CRITICAL WARNING": 0, "ERROR": 0}
    warning_lines: list[str] = []
    severity_lines: list[str] = []
    for line in lines:
        severity = SEVERITY.match(line)
        if severity:
            level = severity.group(1)
            counts[level] += 1
            severity_lines.append(line)
            if level == "WARNING":
                warning_lines.append(line)

    if counts["ERROR"] or counts["CRITICAL WARNING"]:
        raise AuditError("native-log range contains ERROR or CRITICAL WARNING")
    if not warning_lines:
        raise AuditError("native-log range contains no verifiable WARNING lines")
    if warning_lines != expected_messages:
        raise AuditError("native WARNING lines do not exactly match the original capture receipt")
    if exported != len(warning_lines):
        raise AuditError("capture exported WARNING count does not match independently parsed lines")

    result = {
        "schema": "material-counter.jpeg-joint-capture-review.v1",
        "status": "PASS_SUPPLEMENTAL_REVIEW",
        "scope": scope,
        "original_capture_status": receipt["status"],
        "original_capture_code": receipt["code"],
        "capture_error": receipt["capture_error"],
        "warning_counter_before": warning_before,
        "warning_counter_after": warning_after,
        "warning_counter_delta": emitted,
        "native_exported_warning_count": exported,
        "severity_counts": counts,
        "severity_lines": severity_lines,
        "native_log": str(native_log.resolve()),
        "native_log_sha256": sha256(payload),
        "native_log_sha256_scope_at_review": "entire log snapshot at this review time only; Vivado may append later",
        "native_log_bytes_at_review": len(payload),
        "native_log_byte_range": {"start": start, "end_exclusive": end},
        "native_log_range_sha256": sha256(selected),
        "native_log_range_bytes": len(selected),
        "reviewed_utc": datetime.now(timezone.utc).isoformat(),
        "independent_parse": "PASS; exact recorded byte range decoded as UTF-8; WARNING lines equal original capture; no ERROR or CRITICAL WARNING",
    }
    if require_result:
        result["expected_result"] = expected_result
        result["actual_result"] = actual_result
    return result


def audit_synth_capture(receipt: dict[str, Any]) -> dict[str, Any]:
    """Audit the launch_runs synthetic WARNING-counter reset for the synth stage.

    The result token is not compared here: the synth capture wraps a command
    block, not a single result-returning operation. All other strict checks
    (original FAIL receipt, exact native-log range, severity parse, no
    ERROR/CRITICAL WARNING) remain in force.
    """
    return audit_capture(
        receipt,
        require_result=False,
        scope="synth only; original ipcfg capture receipt remains FAIL",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-receipt", type=Path, required=True)
    parser.add_argument("--scope", choices=("refresh_region", "synth"), default="refresh_region")
    parser.add_argument("--status", required=True)
    parser.add_argument("--code", type=int, required=True)
    parser.add_argument("--capture-error", required=True)
    parser.add_argument("--raw-result", default="")
    parser.add_argument("--expected-result", default="")
    parser.add_argument("--warning-before", type=int, required=True)
    parser.add_argument("--warning-after", type=int, required=True)
    parser.add_argument("--native-log", type=Path, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--warning-message", action="append", default=[])
    parser.add_argument("--messages-file", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    warning_messages = list(args.warning_message)
    if args.messages_file is not None:
        if warning_messages:
            parser.error("use either --messages-file or --warning-message, not both")
        if not args.messages_file.is_file():
            parser.error(f"--messages-file does not exist: {args.messages_file}")
        warning_messages = [line.rstrip("\r\n") for line in
                            args.messages_file.read_text(encoding="utf-8").splitlines()]

    capture = {
        "status": args.status,
        "code": args.code,
        "capture_error": args.capture_error,
        "raw": args.raw_result,
        "expected_result": args.expected_result,
        "warning_before": args.warning_before,
        "warning_after": args.warning_after,
        "native_log": str(args.native_log),
        "start": args.start,
        "end": args.end,
        "warning_messages": warning_messages,
    }
    try:
        audit = audit_synth_capture if args.scope == "synth" else audit_capture
        result = audit(capture)
        code = 0
    except (AuditError, OSError) as exc:
        result = {
            "schema": "material-counter.jpeg-joint-capture-review.v1",
            "status": "FAIL_SUPPLEMENTAL_REVIEW",
            "scope": f"{args.scope} only; original ipcfg capture receipt remains unchanged",
            "reason": str(exc),
        }
        code = 2

    result["original_capture_receipt"] = str(args.capture_receipt.resolve())
    result["original_capture_receipt_sha256"] = sha256(args.capture_receipt.read_bytes())
    if args.messages_file is not None:
        result["warning_messages_file"] = str(args.messages_file.resolve())
        result["warning_messages_sha256"] = sha256(args.messages_file.read_bytes())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(f"JOINT_CAPTURE_REVIEW_{'PASS' if code == 0 else 'FAIL'} {args.out}")
    if code:
        print(result["reason"], file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
