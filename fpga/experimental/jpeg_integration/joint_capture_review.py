"""Independently audit narrowly scoped Vivado capture counter-reset cases."""

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
    r"WARNING emitted=(-?[0-9]+) exported=([0-9]+)$"
)
SEVERITY = re.compile(r"^\s*(CRITICAL WARNING|WARNING|ERROR):")
IP_SUBCORE_WARNING = re.compile(r"IP_Flow 19-(7090|3374)\].*for IP '[^']*/[^']*'")
UNSAFE_WARNING = re.compile(
    r"ignoring|ignored|does not exist|BD 41-1276|IP_Flow 19-(7090|3374)", re.IGNORECASE
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def windows_path_key(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value.replace("/", "\\")))


def audit_capture(receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate one capture receipt and exactly its recorded native-log byte slice."""
    scope = str(receipt.get("scope", "refresh_region"))
    if scope not in {"refresh_region", "synth", "run"}:
        raise AuditError("only the refresh_region, synth, and run scopes are supported")
    if receipt.get("status") != "FAIL" or receipt.get("code") != 0:
        raise AuditError("adaptation requires original capture status FAIL with Tcl code 0")

    match = CAPTURE_RESET.fullmatch(str(receipt.get("capture_error", "")))
    if not match:
        raise AuditError("capture_error is outside the reviewed WARNING-counter delta format")

    try:
        warning_before = int(receipt["warning_before"])
        warning_after = int(receipt["warning_after"])
        start = int(receipt["start"])
        end = int(receipt["end"])
        exported = int(match.group(2))
        emitted = int(match.group(1))
        native_log = Path(receipt["native_log"])
        expected_result = (
            str(receipt["expected_result"])
            if scope in {"refresh_region", "run"}
            else None
        )
        actual_result = str(receipt["raw"])
        expected_messages = [str(item).rstrip("\r\n") for item in receipt.get(
            "messages", receipt.get("warning_messages", [])
        )]
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditError(f"required capture evidence is missing or malformed: {exc}") from exc

    if not native_log.is_file():
        raise AuditError("recorded native log does not exist")
    payload = native_log.read_bytes()
    if start < 0 or end < start or end > len(payload):
        raise AuditError("recorded native-log byte range is missing or outside the log")
    if end > start and ((start and payload[start - 1] != 0x0A) or payload[end - 1] != 0x0A):
        raise AuditError("recorded native-log byte range starts or ends inside a line")
    if emitted != warning_after - warning_before:
        raise AuditError("warning counter delta does not match the recorded counter values")
    if scope == "refresh_region" and windows_path_key(actual_result) != windows_path_key(expected_result):
        raise AuditError("refresh_module_reference returned an unexpected result")
    if scope == "run" and actual_result != expected_result:
        raise AuditError("Vivado run did not return the expected completion marker")

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

    if counts["ERROR"] or (scope != "run" and counts["CRITICAL WARNING"]):
        raise AuditError("native-log range contains ERROR or CRITICAL WARNING")
    if not warning_lines and not (
        scope == "run" and exported == 0 and not expected_messages and not severity_lines
    ):
        raise AuditError("native-log range contains no verifiable WARNING lines")
    if scope == "refresh_region":
        if warning_lines != expected_messages:
            raise AuditError("native WARNING lines do not exactly match the original capture receipt")
    else:
        if severity_lines != expected_messages:
            raise AuditError("native severity lines do not exactly match the original capture receipt")
        try:
            critical_before = int(receipt["critical_before"])
            critical_after = int(receipt["critical_after"])
            error_before = int(receipt["error_before"])
            error_after = int(receipt["error_after"])
            reported_warnings = int(receipt["warning_count"])
            reported_critical = int(receipt["critical_count"])
            reported_errors = int(receipt["error_count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuditError(f"synth severity counters are missing or malformed: {exc}") from exc
        if reported_warnings != len(warning_lines):
            raise AuditError("synth WARNING count differs from the native-log byte range")
        if reported_critical != counts["CRITICAL WARNING"] or reported_errors != counts["ERROR"]:
            raise AuditError("synth fatal-severity counts differ from the native-log byte range")
        if critical_after - critical_before != reported_critical:
            raise AuditError("synth CRITICAL WARNING counter delta differs from the native-log byte range")
        if error_after - error_before != reported_errors:
            raise AuditError("synth ERROR counter delta differs from the native-log byte range")
        if scope == "synth":
            for line in warning_lines:
                if not IP_SUBCORE_WARNING.search(line) and UNSAFE_WARNING.search(line):
                    raise AuditError("synth native-log range contains an unsafe warning")
        else:
            try:
                error_before = int(receipt["error_before"])
                error_after = int(receipt["error_after"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AuditError(f"run ERROR counters are missing or malformed: {exc}") from exc
            if error_before != error_after or reported_errors:
                raise AuditError("run capture contains ERROR or a changed ERROR counter")
            if error_after - error_before != counts["ERROR"]:
                raise AuditError("run ERROR counter delta differs from the native-log byte range")
            if any(UNSAFE_WARNING.search(line) for line in warning_lines):
                raise AuditError("run native-log range contains an unsafe warning")
    if exported != len(warning_lines):
        raise AuditError("capture exported WARNING count does not match independently parsed lines")

    scope_description = {
        "refresh_region": "refresh_region only; original ipcfg capture receipt remains FAIL",
        "synth": "synth only; original ipcfg capture receipt remains FAIL",
        "run": "Vivado run reporting only; original ipcfg capture receipt remains FAIL",
    }[scope]
    status = (
        "REVIEW_REQUIRED"
        if scope == "run" and counts["CRITICAL WARNING"]
        else "PASS_SUPPLEMENTAL_REVIEW"
    )
    return {
        "schema": "material-counter.jpeg-joint-capture-review.v1",
        "status": status,
        "manager_review_required": status == "REVIEW_REQUIRED",
        "scope": scope_description,
        "original_capture_status": receipt["status"],
        "original_capture_code": receipt["code"],
        "capture_error": receipt["capture_error"],
        "expected_result": expected_result,
        "actual_result": actual_result,
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
        "capture_scope": scope,
        "reviewed_utc": datetime.now(timezone.utc).isoformat(),
        "independent_parse": (
            "PASS; exact recorded byte range decoded as UTF-8; severity lines match the original capture; ERROR count is zero"
            if scope == "run"
            else "PASS; exact recorded byte range decoded as UTF-8; WARNING lines equal original capture; no ERROR or CRITICAL WARNING"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-receipt", type=Path, required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--code", type=int, required=True)
    parser.add_argument("--capture-error", required=True)
    parser.add_argument("--raw-result", required=True)
    parser.add_argument("--expected-result")
    parser.add_argument("--scope", choices=("refresh_region", "synth", "run"), default="refresh_region")
    parser.add_argument("--warning-before", type=int, required=True)
    parser.add_argument("--warning-after", type=int, required=True)
    parser.add_argument("--critical-before", type=int)
    parser.add_argument("--critical-after", type=int)
    parser.add_argument("--error-before", type=int)
    parser.add_argument("--error-after", type=int)
    parser.add_argument("--warning-count", type=int)
    parser.add_argument("--critical-count", type=int)
    parser.add_argument("--error-count", type=int)
    parser.add_argument("--native-log", type=Path, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--messages-file", type=Path)
    parser.add_argument("--warning-message", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.messages_file:
        messages = args.messages_file.read_text(encoding="utf-8").splitlines()
    else:
        messages = args.warning_message
    capture = {
        "scope": args.scope,
        "status": args.status,
        "code": args.code,
        "capture_error": args.capture_error,
        "raw": args.raw_result,
        "warning_before": args.warning_before,
        "warning_after": args.warning_after,
        "native_log": str(args.native_log),
        "start": args.start,
        "end": args.end,
        "warning_messages": args.warning_message,
        "messages": messages,
    }
    if args.expected_result is not None:
        capture["expected_result"] = args.expected_result
    for name in (
        "critical_before", "critical_after", "error_before", "error_after",
        "warning_count", "critical_count", "error_count",
    ):
        value = getattr(args, name)
        if value is not None:
            capture[name] = value
    try:
        result = audit_capture(capture)
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
    if args.messages_file:
        message_payload = args.messages_file.read_bytes()
        result["capture_messages_file"] = str(args.messages_file.resolve())
        result["capture_messages_sha256"] = sha256(message_payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    outcome = "FAIL" if code else (
        "REQUIRED" if result.get("status") == "REVIEW_REQUIRED" else "PASS"
    )
    print(f"JOINT_CAPTURE_REVIEW_{outcome} {args.out}")
    if code:
        print(result["reason"], file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
