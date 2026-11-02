"""Audit the route launch_runs WARNING-counter reset without touching the bound review module.

The generic ipcfg capture remains strict. This adapter is used only for the
route stage, where launch_runs spawns a child process that resets Vivado's
in-process message counter. The original FAIL receipt and its native-log byte
range are preserved; any ERROR or CRITICAL WARNING in that range fails closed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).with_name("joint_capture_review.py")
SPEC = importlib.util.spec_from_file_location("joint_capture_review_bound", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit(f"cannot load bound review module: {MODULE_PATH}")
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-receipt", type=Path, required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--code", type=int, required=True)
    parser.add_argument("--capture-error", required=True)
    parser.add_argument("--warning-before", type=int, required=True)
    parser.add_argument("--warning-after", type=int, required=True)
    parser.add_argument("--native-log", type=Path, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--messages-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if not args.messages_file.is_file():
        parser.error(f"--messages-file does not exist: {args.messages_file}")
    warning_messages = [line.rstrip("\r\n") for line in
                        args.messages_file.read_text(encoding="utf-8").splitlines()]
    capture = {
        "status": args.status,
        "code": args.code,
        "capture_error": args.capture_error,
        "raw": "",
        "expected_result": "",
        "warning_before": args.warning_before,
        "warning_after": args.warning_after,
        "native_log": str(args.native_log),
        "start": args.start,
        "end": args.end,
        "warning_messages": warning_messages,
    }

    try:
        result = REVIEW.audit_capture(
            capture,
            require_result=False,
            scope="route only; original ipcfg capture receipt remains FAIL",
        )
        code = 0
    except (REVIEW.AuditError, OSError) as exc:
        result = {
            "schema": "material-counter.jpeg-joint-capture-review.v1",
            "status": "FAIL_SUPPLEMENTAL_REVIEW",
            "scope": "route only; original ipcfg capture receipt remains unchanged",
            "reason": str(exc),
        }
        code = 2

    result["original_capture_receipt"] = str(args.capture_receipt.resolve())
    result["original_capture_receipt_sha256"] = REVIEW.sha256(args.capture_receipt.read_bytes())
    result["warning_messages_file"] = str(args.messages_file.resolve())
    result["warning_messages_sha256"] = REVIEW.sha256(args.messages_file.read_bytes())

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