"""Board-resident closeout child. Runtime PYNQ access starts only after one-shot gates."""
import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import closeout_worker as closeout

DIAG50 = closeout.DIAG50
if str(DIAG50) not in sys.path:
    sys.path.insert(0, str(DIAG50))
import child as diagnostic_child
import session as diagnostic_session
from core import Journal, base

GUARD = closeout.GUARD
PARENT_GUARD = closeout.PARENT_GUARD
stdout_lock = threading.Lock()
transport_lost = threading.Event()
retained_worker = None
retained_locks = []


def emit(row):
    with stdout_lock:
        try:
            print(json.dumps(row, sort_keys=True), file=sys.__stdout__, flush=True)
        except (BrokenPipeError, OSError):
            transport_lost.set()
            raise RuntimeError("Supervisor transport lost; no further hardware access")


def hold():
    # Keep the child, locks, and any unverified DMA buffer alive after failure.
    while True:
        time.sleep(1)


def install_signal_guard():
    def disconnected(_signum, _frame):
        transport_lost.set()
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), disconnected)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approve-once", action="store_true")
    parser.add_argument("--mode", choices=tuple(closeout.MODE_STAGES), required=True)
    parser.add_argument("--authorize-stage", choices=tuple(closeout.MODE_STAGES), required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args(argv)
    if not args.execute or not args.approve_once or args.authorize_stage != args.mode:
        parser.error("Execution requires --execute, --approve-once, and matching mode authorization")
    return args


def check_supervision_start(mode, journal, manifest_sha):
    expected = {"mode": mode, "manifest_sha256": manifest_sha}
    if (journal.read_json("supervision-" + mode + "-consumed") != expected
            or journal.exists("supervision-" + mode + "-pass")):
        raise base.PreflightError("Fresh consumed supervisor authorization is required")
    if journal.exists("terminal-fail"):
        raise base.PreflightError("Closeout terminal-fail marker blocks execution")
    position = closeout.MODE_ORDER.index(mode)
    for prior in closeout.MODE_ORDER[:position]:
        marker = journal.read_json("mode-" + prior + "-consumed")
        passed = journal.read_json("mode-" + prior + "-pass")
        supervised = {"mode": prior, "manifest_sha256": manifest_sha}
        if (not isinstance(marker, dict) or marker.get("mode") != prior
                or marker.get("manifest_sha256") != manifest_sha
                or not isinstance(passed, dict) or passed.get("mode") != prior
                or passed.get("manifest_sha256") != manifest_sha
                or passed.get("boot_id") != closeout.BOOT_ID
                or journal.read_json("supervision-" + prior + "-consumed") != supervised
                or journal.read_json("supervision-" + prior + "-pass") != supervised):
            raise base.PreflightError("Prior closeout mode PASS is incomplete: " + prior)
    for later in closeout.MODE_ORDER[position + 1:]:
        if journal.exists("mode-" + later + "-consumed"):
            raise base.PreflightError("Closeout mode was consumed out of order: " + later)
    if journal.exists("mode-" + mode + "-consumed"):
        raise base.PreflightError("Closeout mode attempt was already consumed: " + mode)


def _lock(path):
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def main(argv=None):
    args = parse_args(argv)
    diagnostic_session.require_secure_session()
    config = closeout.load_config(Path(__file__).resolve().parent,
                                  expected_manifest_sha256=args.manifest_sha256)
    parent_journal = Journal(PARENT_GUARD)
    journal = Journal(GUARD)
    closeout.validate_parent_baseline(
        parent_journal, boot_id=closeout.BOOT_ID,
        executor_manifest_sha256=config.parent_manifest_sha256,
        bit_sha256=closeout.diagnostic_core.BIT_SHA)
    check_supervision_start(args.mode, journal, config.manifest_sha256)

    # Own both scopes for the entire process. No second diagnostic50 or closeout
    # process may inspect or touch the board while this child is active.
    retained_locks.extend((
        _lock(PARENT_GUARD / "exclusive.lock"),
        _lock(GUARD / "exclusive.lock"),
    ))
    install_signal_guard()
    sys.stdout = (GUARD / "library-stdout.log").open("a", buffering=1, encoding="utf-8")

    api = diagnostic_child.BoardAPI()
    retained_worker = closeout.CloseoutWorker(
        config, args.mode, api, journal, parent_journal, api.environment,
        emit=lambda row: emit({"kind": "event", **row}),
        may_progress=lambda: not transport_lost.is_set())

    def heartbeat():
        while True:
            time.sleep(0.5)
            try:
                emit({"kind": "heartbeat", "state": retained_worker.state,
                      "pid": os.getpid(), "monotonic_s": time.monotonic()})
            except BaseException:
                transport_lost.set()
                return
    threading.Thread(target=heartbeat, daemon=True).start()

    try:
        for line in sys.stdin:
            command = json.loads(line)
            if (not isinstance(command, dict)
                    or set(command) != {"stage", "report", "deadline_monotonic_s"}):
                raise ValueError("Invalid supervisor command schema")
            stage = command["stage"]
            retained_worker.timeout_s = closeout.stage_timeout(stage)
            result = retained_worker.command(
                stage, command["report"],
                deadline_monotonic_s=command["deadline_monotonic_s"])
            if stage == "ENV" and result.get("ok"):
                result["boot_id"] = retained_worker.env["boot_id"]
            emit({"kind": "result", **result})
            if not result.get("ok"):
                hold()
            if retained_worker.state == "PASS":
                return 0
        retained_worker.state = "FAIL"
        retained_worker._persist_terminal_failure(
            "CHILD_DISCONNECT", "Supervisor disconnected before terminal PASS")
        hold()
    except BaseException as exc:
        if retained_worker is not None:
            retained_worker.state = "FAIL"
            retained_worker._persist_terminal_failure(
                "CHILD", type(exc).__name__ + ": " + str(exc))
        else:
            for target in (journal, parent_journal):
                try:
                    target.claim("terminal-fail", {
                        "mode": args.mode, "stage": "CHILD_START",
                        "error": type(exc).__name__ + ": " + str(exc),
                        "monotonic_s": time.monotonic(),
                    })
                except BaseException:
                    pass
        hold()


if __name__ == "__main__":
    raise SystemExit(main())
