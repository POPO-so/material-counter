"""Explicit, board-local one-shot controller for WO-0014 closeout modes."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIAG50 = HERE.parent / "diagnostic50"
if str(DIAG50) not in sys.path:
    sys.path.insert(0, str(DIAG50))

import closeout_worker as closeout
import core
from core import Journal, base
from deadline import run_bounded
from session import LocalHealth, require_secure_session
from supervisor import ProcessLink, REMOTE_PYTHON, Supervisor

GUARD = closeout.GUARD
PARENT_GUARD = closeout.PARENT_GUARD
retained_link = None


def authorize(mode, execute, authorize_stage):
    if mode not in closeout.MODE_STAGES:
        raise RuntimeError("Unknown closeout mode")
    if not execute:
        if authorize_stage is not None:
            raise RuntimeError("Offline validation does not accept an execution authorization")
        return
    if authorize_stage != mode:
        raise RuntimeError("Explicit matching --authorize-stage is required for execution")


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--mode", choices=tuple(closeout.MODE_STAGES), required=True)
    command.add_argument("--execute", action="store_true")
    command.add_argument("--authorize-stage", choices=tuple(closeout.MODE_STAGES))
    command.add_argument("--manifest-sha256", required=True)
    command.add_argument("--evidence-dir")
    return command


def _check_order(mode, journal, config):
    if journal.exists("terminal-fail"):
        raise RuntimeError("A prior closeout terminal failure permanently blocks execution")
    position = closeout.MODE_ORDER.index(mode)
    for prior in closeout.MODE_ORDER[:position]:
        prior_marker = journal.read_json("mode-" + prior + "-consumed")
        prior_pass = journal.read_json("mode-" + prior + "-pass")
        supervisor = {"mode": prior, "manifest_sha256": config.manifest_sha256}
        if (not isinstance(prior_marker, dict) or prior_marker.get("mode") != prior
                or prior_marker.get("manifest_sha256") != config.manifest_sha256
                or not isinstance(prior_pass, dict) or prior_pass.get("mode") != prior
                or prior_pass.get("manifest_sha256") != config.manifest_sha256
                or prior_pass.get("boot_id") != closeout.BOOT_ID
                or journal.read_json("supervision-" + prior + "-consumed") != supervisor
                or journal.read_json("supervision-" + prior + "-pass") != supervisor):
            raise RuntimeError("Prior closeout mode PASS is incomplete: " + prior)
    for later in closeout.MODE_ORDER[position + 1:]:
        if journal.exists("mode-" + later + "-consumed"):
            raise RuntimeError("A later closeout mode was consumed out of order: " + later)
    if (journal.exists("mode-" + mode + "-consumed")
            or journal.exists("supervision-" + mode + "-consumed")):
        raise RuntimeError("This closeout mode has already consumed its one-shot attempt")


def _claim_supervision(mode, config):
    def claim(ctx):
        parent_journal = Journal(PARENT_GUARD)
        closeout_journal = Journal(GUARD)
        closeout.validate_parent_baseline(
            parent_journal, boot_id=closeout.BOOT_ID,
            executor_manifest_sha256=config.parent_manifest_sha256,
            bit_sha256=core.BIT_SHA)
        _check_order(mode, closeout_journal, config)
        ctx.check_active()
        marker = {"mode": mode, "manifest_sha256": config.manifest_sha256}
        closeout_journal.claim("supervision-" + mode + "-consumed", marker)
        ctx.check_active()
        return closeout_journal, parent_journal, marker
    result = run_bounded(claim, 35.0, phase="CLOSEOUT_SUPERVISION_CLAIM")
    if result.status != "PASS":
        raise RuntimeError("One-shot supervisor claim " + result.status + ": " + str(result.error))
    return result.value


def _mark_terminal_failure(journals, mode, reason):
    for journal in journals:
        try:
            journal.claim("terminal-fail", {
                "mode": "wo0014-closeout:" + mode,
                "stage": "CONTROLLER",
                "error": reason,
                "monotonic_s": time.monotonic(),
            })
        except FileExistsError:
            pass
        except BaseException:
            pass


def main(argv=None):
    args = parser().parse_args(argv)
    authorize(args.mode, args.execute, args.authorize_stage)
    config = closeout.load_config(HERE, expected_manifest_sha256=args.manifest_sha256)
    if not args.execute:
        print(json.dumps({"status": "OFFLINE_READY", "hardware_attempted": False,
                          "mode": args.mode, "manifest_sha256": config.manifest_sha256,
                          "clock_mhz": closeout.PROFILE_FOR_MODE[args.mode],
                          "frame_ids": [row.frame_id for row in config.frames[args.mode]]}, indent=2))
        return 0
    if not args.evidence_dir:
        raise RuntimeError("Execution requires a fresh --evidence-dir")
    require_secure_session()
    closeout_journal, parent_journal, consumed = _claim_supervision(args.mode, config)
    health = LocalHealth()
    child_path = HERE / "closeout_child.py"
    argv_child = [REMOTE_PYTHON, "-u", str(child_path), "--execute", "--approve-once",
                  "--mode", args.mode, "--authorize-stage", args.mode,
                  "--manifest-sha256", config.manifest_sha256]
    # ProcessLink handles stage deadlines and retained process objects. The
    # closeout-only map is injected into the imported diagnostic50 module so
    # the inherited Supervisor requests precisely this mode's allowed stages.
    core.MODE_STAGES = closeout.MODE_STAGES
    link = ProcessLink(argv_child, args.evidence_dir, timeout_s=35.0)
    global retained_link
    retained_link = link
    result = Supervisor(link, health, mode=args.mode).run()
    if result.get("status") == "PASS":
        receipt = run_bounded(
            lambda ctx: closeout_journal.claim(
                "supervision-" + args.mode + "-pass", consumed),
            35.0, phase="CLOSEOUT_SUPERVISION_RECEIPT")
        if receipt.status != "PASS":
            result = link.stop("SUPERVISION_RECEIPT_" + receipt.status,
                               deadline=receipt.as_record())
    if result.get("status") == "PASS":
        final_health = run_bounded(lambda ctx: health(), 5.0, phase="CLOSEOUT_POST_HEALTH")
        if final_health.status != "PASS":
            result = link.stop("POST_HEALTH_" + final_health.status,
                               deadline=final_health.as_record())
        elif (not isinstance(final_health.value, dict)
              or any(final_health.value.get(key) is not True for key in ("ping", "ssh", "linux"))
              or final_health.value.get("boot_id") != closeout.BOOT_ID
              or final_health.value.get("new_kernel_warnings")):
            result = link.stop("POST_HEALTH_GATE_FAILED", report=final_health.value)
        else:
            result["post_health"] = final_health.value
            result["mode_receipt"] = closeout_journal.read_json("mode-" + args.mode + "-pass")
    if result.get("status") != "PASS":
        _mark_terminal_failure((closeout_journal, parent_journal), args.mode,
                               result.get("reason", "closeout supervision did not PASS"))
        print(json.dumps(result, indent=2), flush=True)
        # Do not kill or reconnect; preserve ProcessLink and child resources.
        while True:
            time.sleep(1)
    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
