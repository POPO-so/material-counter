"""Arm one isolated WO-0017 physical calibration lock without formal Permit use."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import run_stream_counter as runner
import physical_calibration_lock as calibration_lock


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only board admission followed by one new calibration-lock creation"
    )
    parser.add_argument("runner_args", nargs=argparse.REMAINDER,
                        help="arguments for run_stream_counter after --")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner_args = args.runner_args[1:] if args.runner_args[:1] == ["--"] else args.runner_args
    run_options = runner._parser().parse_args(runner_args)
    try:
        if os.environ.get("XILINX_XRT") != "/usr":
            raise ValueError("set XILINX_XRT=/usr before preparing the calibration lock")
        launcher_sha256 = hashlib.sha256(
            (runner.SCRIPT_DIR / "operator_gate_launcher.py").read_bytes()
        ).hexdigest()
        state = runner.validate_calibration_board_state(
            run_options, gate_launcher_sha256=launcher_sha256, require_lock=False
        )
        calibration = state["calibration"]
        ready = calibration_lock.write_ready_calibration_lock(
            calibration["lock_path"], calibration["identity"]
        )
        result = {
            "status": "CALIBRATION_LOCK_READY",
            "work_order": "WO-0017",
            "formal_permit_issued": False,
            "formal_permit_consumed": False,
            "calibration_id": run_options.calibration_id,
            "boot_id": state["plan"].boot_id,
            "lock_path": str(calibration["lock_path"]),
            "lock_sha256": ready["sha256"],
            "identity": calibration["identity"],
            "cma_free_kib": state["meminfo"]["CmaFree"],
            "camera_profile": state["camera_profile"],
            "camera_ownership": state["camera_ownership"],
            "dma_status": {key: f"0x{value:08x}"
                           for key, value in state["dma_status"].items()},
            "pynq_imported": False,
            "camera_opened": False,
            "dma_transfer_started": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, runner.preflight.PreflightError) as exc:
        print(json.dumps({"status": "CALIBRATION_PREP_REJECTED",
                          "pynq_imported": False, "camera_opened": False,
                          "dma_transfer_started": False,
                          "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
