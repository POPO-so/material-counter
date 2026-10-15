"""Additive, fail-closed WO-0014 board closeout executor."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
DIAG50 = HERE.parent / "diagnostic50"
if str(DIAG50) not in sys.path:
    sys.path.insert(0, str(DIAG50))

import core as diagnostic_core
from core import Journal, base


BOOT_ID = "52955bb3-db18-4505-b209-78cbf3969d89"
PARENT_GUARD = Path("/var/lib/wo0014-diagnostic50")
GUARD = Path("/var/lib/wo0014-diagnostic50/closeout-20261015")
PROFILE_FOR_MODE = {
    "overlay100": "100",
    "single100": "100",
    "multi50": "50",
    "multi100": "100",
}
MODE_ORDER = ("multi50", "overlay100", "single100", "multi100")
MODE_STAGES = {
    "overlay100": ("ENV", "LOAD", "HEALTH"),
    "single100": ("ENV", "HEALTH", "MIN_READ", "INIT", "SEND", "VERIFY", "RELEASE", "POST"),
    "multi50": (
        "ENV", "HEALTH", "MIN_READ", "INIT",
        "SEND", "VERIFY", "RELEASE", "HEALTH",
        "SEND", "VERIFY", "RELEASE", "HEALTH",
        "SEND", "VERIFY", "RELEASE", "POST",
    ),
    "multi100": (
        "ENV", "HEALTH", "MIN_READ", "INIT",
        "SEND", "VERIFY", "RELEASE", "HEALTH",
        "SEND", "VERIFY", "RELEASE", "HEALTH",
        "SEND", "VERIFY", "RELEASE", "POST",
    ),
}
SOURCE_FILES = {
    "closeout_worker.py": HERE / "closeout_worker.py",
    "closeout_child.py": HERE / "closeout_child.py",
    "closeout_session.py": HERE / "closeout_session.py",
    "diagnostic50/baseline_pynq_image_once.py": DIAG50 / "baseline_pynq_image_once.py",
    "diagnostic50/core.py": DIAG50 / "core.py",
    "diagnostic50/child.py": DIAG50 / "child.py",
    "diagnostic50/session.py": DIAG50 / "session.py",
    "diagnostic50/supervisor.py": DIAG50 / "supervisor.py",
    "diagnostic50/deadline.py": DIAG50 / "deadline.py",
    "diagnostic50/authentication.py": DIAG50 / "authentication.py",
    "diagnostic50/executor-manifest.json": DIAG50 / "executor-manifest.json",
}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
U32_MASK = 0xFFFFFFFF


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_sha(value, label):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value.lower()):
        raise base.PreflightError(label + " must be a lowercase SHA-256 digest")
    return value.lower()


def _asset(root: Path, spec, label: str) -> tuple[Path, str]:
    if not isinstance(spec, dict) or set(spec) != {"path", "sha256"}:
        raise base.PreflightError(label + " must contain exactly path and sha256")
    rel = spec["path"]
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        raise base.PreflightError(label + " path must be a non-empty relative path")
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise base.PreflightError(label + " path escapes the closeout bundle") from exc
    if not path.is_file():
        raise base.PreflightError(label + " file is missing: " + str(path))
    expected = _require_sha(spec["sha256"], label + " sha256")
    if _sha256(path) != expected:
        raise base.PreflightError(label + " SHA-256 mismatch")
    return path, expected


def verify_source_pins(root: Path, source_sha256) -> None:
    if not isinstance(source_sha256, dict) or set(source_sha256) != set(SOURCE_FILES):
        raise base.PreflightError("Reviewed closeout source pin set differs")
    for name, path in SOURCE_FILES.items():
        expected = _require_sha(source_sha256[name], "source_sha256." + name)
        if not path.is_file() or _sha256(path) != expected:
            raise base.PreflightError("Reviewed executor source changed: " + name)


def _packet_spec(root, spec, label):
    if not isinstance(spec, dict) or set(spec) != {"frame_id", "packet", "foreground_pixels"}:
        raise base.PreflightError(label + " must contain frame_id, packet and foreground_pixels")
    if type(spec["frame_id"]) is not int or not 0 <= spec["frame_id"] <= U32_MASK:
        raise base.PreflightError(label + " frame_id must be a uint32")
    foreground = spec["foreground_pixels"]
    if type(foreground) is not int or not 0 <= foreground <= base.PIXEL_COUNT:
        raise base.PreflightError(label + " foreground_pixels is outside the frame")
    path, packet_sha = _asset(root, spec["packet"], label + ".packet")
    return {"frame_id": spec["frame_id"], "packet_path": path,
            "packet_sha256": packet_sha, "foreground_pixels": foreground}


def load_config(root: Path, *, expected_manifest_sha256=None):
    """Validate the closeout manifest and every pinned offline input."""
    root = Path(root).resolve()
    manifest_path = root / "closeout-manifest.json"
    if not manifest_path.is_file():
        raise base.PreflightError("closeout-manifest.json is missing")
    manifest_sha = _sha256(manifest_path)
    if expected_manifest_sha256 is not None and manifest_sha != _require_sha(
            expected_manifest_sha256, "explicit manifest sha256"):
        raise base.PreflightError("Closeout manifest SHA differs from explicit authorization")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise base.PreflightError("Closeout manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise base.PreflightError("Unsupported closeout manifest schema")
    required = {"schema", "boot_id", "parent_50_bit_sha256",
                "parent_50_executor_manifest_sha256", "source_sha256", "profiles",
                "baseline_frame", "modes"}
    if set(manifest) != required:
        raise base.PreflightError("Closeout manifest fields differ from the reviewed schema")
    if manifest["boot_id"] != BOOT_ID:
        raise base.PreflightError("Closeout manifest boot identity differs from the confirmed boot")
    if manifest["parent_50_bit_sha256"] != diagnostic_core.BIT_SHA:
        raise base.PreflightError("Closeout manifest does not bind the reviewed 50 MHz BIT")
    parent_manifest_sha = _require_sha(
        manifest["parent_50_executor_manifest_sha256"], "parent executor manifest sha256")
    verify_source_pins(root, manifest["source_sha256"])
    diagnostic_core.verify_bundle(DIAG50, expected_manifest_sha=parent_manifest_sha)

    if not isinstance(manifest["profiles"], dict) or set(manifest["profiles"]) != {"50", "100"}:
        raise base.PreflightError("Closeout profiles must contain exactly 50 and 100 MHz")
    baseline_spec = manifest["baseline_frame"]
    if not isinstance(baseline_spec, dict) or set(baseline_spec) != {
            "frame_id", "packet", "foreground_pixels"} or baseline_spec["frame_id"] != 0:
        raise base.PreflightError("A reviewed frame-0 baseline packet is required")
    baseline = _packet_spec(root, baseline_spec, "baseline_frame")
    profiles = {}
    for clock in ("50", "100"):
        spec = manifest["profiles"][clock]
        if not isinstance(spec, dict) or set(spec) != {
                "clock_mhz", "build_id", "bit", "hwh", "pair_manifest"}:
            raise base.PreflightError("Profile fields differ for " + clock + " MHz")
        if spec["clock_mhz"] != int(clock) or not isinstance(spec["build_id"], str):
            raise base.PreflightError("Profile clock/build identity differs for " + clock + " MHz")
        bit_path, bit_sha = _asset(root, spec["bit"], clock + ".bit")
        hwh_path, hwh_sha = _asset(root, spec["hwh"], clock + ".hwh")
        pair_path, pair_sha = _asset(root, spec["pair_manifest"], clock + ".pair_manifest")
        if clock == "50":
            if (spec["build_id"] != "wo0014-hw-50mhz-20261014"
                    or bit_sha != diagnostic_core.BIT_SHA
                    or hwh_sha != diagnostic_core.HWH_SHA
                    or _sha256(Path(base.__file__)) != diagnostic_core.BASELINE_SHA):
                raise base.PreflightError("The reviewed 50 MHz source/artifact identity differs")
        baseline_plan = base.validate_inputs(bit_path, hwh_path, baseline["packet_path"], pair_path)
        if (baseline_plan.clock_mhz != int(clock) or baseline_plan.build_id != spec["build_id"]
                or baseline_plan.bit_sha256 != bit_sha or baseline_plan.hwh_sha256 != hwh_sha
                or baseline_plan.packet_sha256 != baseline["packet_sha256"]
                or baseline_plan.frame_id != 0
                or baseline_plan.expected_binary_checksum != baseline["foreground_pixels"] * 255):
            raise base.PreflightError("Frame-0 Golden or profile pair does not match validated inputs")
        profiles[clock] = SimpleNamespace(
            clock_mhz=int(clock), build_id=spec["build_id"], bit_path=bit_path,
            hwh_path=hwh_path, pair_manifest_path=pair_path, pair_manifest_sha256=pair_sha,
            bit_sha256=bit_sha, hwh_sha256=hwh_sha, baseline=baseline_plan)

    mode_specs = manifest["modes"]
    if not isinstance(mode_specs, dict) or set(mode_specs) != set(MODE_STAGES):
        raise base.PreflightError("Closeout mode list differs from the reviewed four modes")
    frames = {}
    for mode, clock in PROFILE_FOR_MODE.items():
        mode_spec = mode_specs[mode]
        if not isinstance(mode_spec, dict) or set(mode_spec) != {"packets"}:
            raise base.PreflightError("Mode packet list schema differs for " + mode)
        packet_specs = mode_spec["packets"]
        if not isinstance(packet_specs, list):
            raise base.PreflightError("Mode packets must be a list: " + mode)
        expected_ids = () if mode == "overlay100" else (0,) if mode == "single100" else (1, 2, 3)
        if tuple(item.get("frame_id") if isinstance(item, dict) else None
                 for item in packet_specs) != expected_ids:
            raise base.PreflightError("Unexpected packet count/order for " + mode)
        profile = profiles[clock]
        verified = []
        for index, raw in enumerate(packet_specs):
            packet = _packet_spec(root, raw, mode + ".packet." + str(index))
            plan = base.validate_inputs(profile.bit_path, profile.hwh_path,
                                        packet["packet_path"], profile.pair_manifest_path)
            if (plan.clock_mhz != profile.clock_mhz or plan.build_id != profile.build_id
                    or plan.packet_sha256 != packet["packet_sha256"]
                    or plan.frame_id != packet["frame_id"]
                    or plan.expected_binary_checksum != packet["foreground_pixels"] * 255):
                raise base.PreflightError("Packet or Golden differs from reviewed mode " + mode)
            verified.append(SimpleNamespace(plan=plan, frame_id=packet["frame_id"],
                                            foreground_pixels=packet["foreground_pixels"]))
        frames[mode] = tuple(verified)
    if (frames["single100"][0].plan.packet_sha256 != baseline["packet_sha256"]
            or frames["multi50"] and frames["multi100"] and
            tuple((f.frame_id, f.plan.packet_sha256, f.foreground_pixels) for f in frames["multi50"])
            != tuple((f.frame_id, f.plan.packet_sha256, f.foreground_pixels) for f in frames["multi100"])):
        raise base.PreflightError("100 MHz frame 0 and cross-clock multiframe packet lists must match")
    return SimpleNamespace(root=root, manifest=manifest, manifest_sha256=manifest_sha,
                           parent_manifest_sha256=parent_manifest_sha,
                           profiles=profiles, baseline=baseline, frames=frames)


def validate_parent_baseline(journal: Journal, *, boot_id: str,
                             executor_manifest_sha256: str, bit_sha256: str) -> None:
    """Require complete, same-boot PASS receipts from the existing diagnostic50 stages."""
    if journal.exists("terminal-fail"):
        raise base.PreflightError("Parent diagnostic50 terminal-fail marker blocks closeout")
    expected_overlay = {"schema": 1, "mode": "overlay-only", "boot_id": boot_id,
                        "bit_sha256": bit_sha256}
    expected_mmio = {"schema": 1, "mode": "mmio-read-only", "boot_id": boot_id,
                     "bit_sha256": bit_sha256}
    expected_consumed = {
        "overlay-attempt": {"count": 1, "clock_mhz": 50},
        "overlay-receipt": expected_overlay,
        "mmio-read-only-consumed": {"mode": "mmio-read-only", "boot_id": boot_id,
                                     "bit_sha256": bit_sha256},
        "mmio-receipt": expected_mmio,
        "dma-once-consumed": {"mode": "dma-once", "boot_id": boot_id,
                              "bit_sha256": bit_sha256},
    }
    for name, expected in expected_consumed.items():
        if journal.read_json(name) != expected:
            raise base.PreflightError("Required parent baseline receipt is missing or stale: " + name)
    send = journal.read_json("send-attempt")
    if (not isinstance(send, dict) or send.get("count") != 1
            or send.get("bytes") != base.PACKET_BYTES or send.get("boot_id") != boot_id
            or type(send.get("address")) is not int):
        raise base.PreflightError("Required parent DMA attempt receipt is missing or stale")
    for mode in ("overlay-only", "mmio-read-only", "dma-once"):
        expected = {"mode": mode, "manifest_sha256": executor_manifest_sha256}
        if (journal.read_json("supervision-" + mode + "-consumed") != expected
                or journal.read_json("supervision-" + mode + "-pass") != expected):
            raise base.PreflightError("Parent supervised PASS is incomplete: " + mode)


def _same_checker_state(expected, actual, *, ignore_stalled=False):
    keys = set(expected)
    if ignore_stalled:
        keys.discard("stalled_cycles")
    mismatch = {key: (expected[key], actual.get(key)) for key in keys
                if actual.get(key) != expected[key]}
    if mismatch:
        raise base.PreflightError("Checker baseline differs: " + repr(mismatch))


def validate_initial_checker(mode: str, checker: dict, baseline_plan) -> None:
    if mode == "single100":
        base._check_checker_ready(checker)
        if checker.get("frame_id") != 0 or checker.get("first_word") != 0 or checker.get("last_word") != 0:
            raise base.PreflightError("Fresh 100 MHz Checker identity fields are not zero")
        return
    expected = {
        "drain_enable": 1, "magic": base.PACKET_MAGIC, "status": 9,
        "error_code": 0, "frame_id": 0, "packet_done_count": 1,
        "packet_error_count": 0, "input_bytes": base.PACKET_BYTES,
        "beats": base.PACKET_BEATS, "tlast_count": 1,
        "first_word": base.PACKET_MAGIC, "last_word": base.TRAILER_END,
        "input_byte_checksum": baseline_plan.input_byte_checksum,
        "observed_pixels": base.PIXEL_COUNT, "sofs": 1, "eols": base.HEIGHT,
        "binary_checksum": baseline_plan.expected_binary_checksum,
        "accepted_output_total": base.PIXEL_COUNT,
    }
    _same_checker_state(expected, checker, ignore_stalled=True)


def validate_checker_increment(previous: dict, current: dict, packet_plan) -> None:
    """Check cumulative ingress counters and the SOF-reset output window for one packet."""
    expected = {
        "drain_enable": 1,
        "magic": base.PACKET_MAGIC,
        "status": base.STATUS_PACKET_DONE | base.STATUS_OUTPUT_COMPLETE,
        "error_code": 0,
        "frame_id": packet_plan.frame_id,
        "packet_done_count": previous["packet_done_count"] + 1,
        "packet_error_count": previous["packet_error_count"],
        "input_bytes": (previous["input_bytes"] + base.PACKET_BYTES) & U32_MASK,
        "beats": (previous["beats"] + base.PACKET_BEATS) & U32_MASK,
        "tlast_count": (previous["tlast_count"] + 1) & U32_MASK,
        "first_word": previous["first_word"] or base.PACKET_MAGIC,
        "last_word": base.TRAILER_END,
        "input_byte_checksum": (previous["input_byte_checksum"] +
                                 packet_plan.input_byte_checksum) & U32_MASK,
        "observed_pixels": base.PIXEL_COUNT,
        "sofs": 1,
        "eols": base.HEIGHT,
        "binary_checksum": packet_plan.expected_binary_checksum,
        "accepted_output_total": (previous["accepted_output_total"] +
                                  base.PIXEL_COUNT) & U32_MASK,
    }
    for name, expected_value in expected.items():
        if current.get(name) != expected_value:
            raise base.PreflightError(
                "Checker " + name + " mismatch: got " + repr(current.get(name)) +
                ", expected " + repr(expected_value))
    if current.get("stalled_cycles", -1) < previous.get("stalled_cycles", 0):
        raise base.PreflightError("Checker stalled_cycles moved backwards")


class CloseoutWorker(diagnostic_core.Worker):
    """Per-mode diagnostic50 Worker extension with sequential frame accounting."""

    def __init__(self, config, mode, api, journal, parent_journal, environment, *,
                 emit=None, may_progress=None):
        if mode not in MODE_STAGES:
            raise ValueError("unknown closeout mode")
        profile = config.profiles[PROFILE_FOR_MODE[mode]]
        super().__init__(profile.baseline, api, journal, environment,
                         fixed_identity=False, timeout_s=5.0, emit=emit,
                         may_progress=may_progress, mode="overlay-only")
        self.mode = mode
        self.stages = MODE_STAGES[mode]
        self.config = config
        self.parent_journal = parent_journal
        self.frame_index = 0
        self.previous_checker = None
        self.verified_checker = None
        self.frame_metrics = []
        self.frame_started = None
        self.length_started = None
        self.length_to_idle_s = None
        self.current_frame = None
        self.current_address = None
        self._frame_verified = False

    def _persist_terminal_failure(self, stage, error):
        super()._persist_terminal_failure(stage, error)
        try:
            self.parent_journal.claim("terminal-fail", {
                "mode": "wo0014-closeout", "stage": stage,
                "error": error, "monotonic_s": time.monotonic(),
            })
        except FileExistsError:
            pass
        except BaseException:
            pass

    def command(self, stage, report=None, *, deadline_monotonic_s=None):
        result = super().command(stage, report, deadline_monotonic_s=deadline_monotonic_s)
        if isinstance(result, dict):
            result["frame_metrics"] = list(self.frame_metrics)
            result["mode"] = self.mode
        return result

    def _mode_receipt(self, mode, *, require_supervision=True):
        expected = self.journal.read_json("mode-" + mode + "-pass")
        if (not isinstance(expected, dict) or expected.get("mode") != mode
                or expected.get("manifest_sha256") != self.config.manifest_sha256
                or expected.get("boot_id") != self.env["boot_id"]):
            raise base.PreflightError("Required prior closeout mode PASS is missing: " + mode)
        if require_supervision:
            supervised = {"mode": mode, "manifest_sha256": self.config.manifest_sha256}
            if (self.journal.read_json("supervision-" + mode + "-consumed") != supervised
                    or self.journal.read_json("supervision-" + mode + "-pass") != supervised):
                raise base.PreflightError("Prior closeout supervision is incomplete: " + mode)

    def _check_prior_modes(self):
        position = MODE_ORDER.index(self.mode)
        for mode in MODE_ORDER[:position]:
            self._mode_receipt(mode)
        for mode in MODE_ORDER[position + 1:]:
            if self.journal.exists("mode-" + mode + "-consumed"):
                raise base.PreflightError("Closeout mode was consumed out of order: " + mode)
        expected = {"mode": self.mode, "manifest_sha256": self.config.manifest_sha256}
        if (self.journal.read_json("supervision-" + self.mode + "-consumed") != expected
                or self.journal.exists("supervision-" + self.mode + "-pass")):
            raise base.PreflightError("Fresh matching one-shot supervisor authorization is required")

    def _validate_live_overlay(self, expected_sha):
        current = self.environment()
        if (current.get("exclusive") is not True or current.get("boot_id") != self.env["boot_id"]
                or current.get("loaded_bit_sha256") != expected_sha):
            raise base.PreflightError("Live boot/Overlay identity changed during closeout")
        return current

    def _claim_mode_pass(self):
        profile = self.config.profiles[PROFILE_FOR_MODE[self.mode]]
        self.journal.claim("mode-" + self.mode + "-pass", {
            "mode": self.mode,
            "manifest_sha256": self.config.manifest_sha256,
            "boot_id": self.env["boot_id"],
            "bit_sha256": profile.bit_sha256,
            "frame_metrics": list(self.frame_metrics),
        })

    def stage_ENV(self, _):
        validate_parent_baseline(
            self.parent_journal, boot_id=BOOT_ID,
            executor_manifest_sha256=self.config.parent_manifest_sha256,
            bit_sha256=diagnostic_core.BIT_SHA)
        self._check_prior_modes()
        current = self.environment()
        profile = self.config.profiles[PROFILE_FOR_MODE[self.mode]]
        expected_current = diagnostic_core.BIT_SHA if self.mode in ("multi50", "overlay100") \
            else self.config.profiles["100"].bit_sha256
        if (current.get("exclusive") is not True or current.get("boot_id") != BOOT_ID
                or current.get("loaded_bit_sha256") != expected_current):
            raise base.PreflightError("ENV boot or active Overlay does not match this closeout mode")
        self.env = current
        marker = {"mode": self.mode, "manifest_sha256": self.config.manifest_sha256,
                  "boot_id": current["boot_id"], "bit_sha256": profile.bit_sha256}
        self.journal.claim("mode-" + self.mode + "-consumed", marker)
        if self.mode in ("single100", "multi100"):
            self._mode_receipt("overlay100")
            overlay = self.journal.read_json("overlay100-receipt")
            if overlay != {"mode": "overlay100", "manifest_sha256": self.config.manifest_sha256,
                           "boot_id": BOOT_ID, "bit_sha256": profile.bit_sha256,
                           "hwh_sha256": profile.hwh_sha256}:
                raise base.PreflightError("Successful 100 MHz Overlay receipt is missing or stale")
        if self.mode == "multi100":
            self._mode_receipt("single100")
        self.record("CLOSEOUT_ENVIRONMENT", mode=self.mode, report=current,
                    manifest_sha256=self.config.manifest_sha256)

    def stage_LOAD(self, _):
        if self.mode != "overlay100":
            raise base.PreflightError("LOAD is valid only for overlay100")
        current = self._validate_live_overlay(diagnostic_core.BIT_SHA)
        profile = self.config.profiles["100"]
        if (base._sha256_file(profile.bit_path) != profile.bit_sha256
                or base._sha256_file(profile.hwh_path) != profile.hwh_sha256):
            raise base.PreflightError("100 MHz Overlay artifacts changed before download")
        self.journal.claim("overlay100-attempt", {
            "mode": self.mode, "manifest_sha256": self.config.manifest_sha256,
            "boot_id": current["boot_id"], "bit_sha256": profile.bit_sha256,
        })
        overlay = self.access("Overlay100", self.api.Overlay, str(profile.bit_path), download=True)
        bases = base._resolve_overlay_bases(overlay.ip_dict,
                                            profile.baseline.dma_base,
                                            profile.baseline.checker_base)
        self.record("OVERLAY100_RETURNED", bases=bases,
                    bit_sha256=profile.bit_sha256, hwh_sha256=profile.hwh_sha256)

    def stage_HEALTH(self, report):
        self.health(report)
        if self.mode == "overlay100":
            profile = self.config.profiles["100"]
            current = self._validate_live_overlay(profile.bit_sha256)
            if not self.journal.exists("overlay100-attempt"):
                raise base.PreflightError("100 MHz Overlay attempt marker is missing")
            self.journal.claim("overlay100-receipt", {
                "mode": "overlay100", "manifest_sha256": self.config.manifest_sha256,
                "boot_id": current["boot_id"], "bit_sha256": profile.bit_sha256,
                "hwh_sha256": profile.hwh_sha256,
            })
            self._claim_mode_pass()
            return
        expected_sha = self.config.profiles[PROFILE_FOR_MODE[self.mode]].bit_sha256
        self._validate_live_overlay(expected_sha)
        self.record("FRAME_HEALTH_ACCEPTED", frame_index=self.frame_index)

    def stage_MIN_READ(self, _):
        if self.mode == "overlay100":
            raise base.PreflightError("MMIO is prohibited in overlay100 mode")
        profile = self.config.profiles[PROFILE_FOR_MODE[self.mode]]
        self._validate_live_overlay(profile.bit_sha256)
        self.checker = self.access("MMIO.checker", self.api.MMIO,
                                   profile.baseline.checker_base, length=0x1000)
        magic = self.read(self.checker, 0x04, "checker.magic")
        if magic != base.PACKET_MAGIC:
            raise base.PreflightError("Checker minimal read magic mismatch")

    def stage_INIT(self, _):
        profile = self.config.profiles[PROFILE_FOR_MODE[self.mode]]
        self._validate_live_overlay(profile.bit_sha256)
        self.dma = self.access("MMIO.dma", self.api.MMIO,
                               profile.baseline.dma_base, length=0x1000)
        status = self.read(self.dma, 4, "init.dmasr")
        control = self.read(self.dma, 0, "init.dmacr")
        length = self.read(self.dma, 0x28, "init.length")
        if status & base.DMA_SR_ERROR_MASK or control & base.DMA_CR_RESET:
            raise base.PreflightError("DMA error/reset state at INIT")
        checker = self.checker_values()
        baseline = self.config.profiles["50" if self.mode == "multi50" else "100"].baseline
        validate_initial_checker(self.mode, checker, baseline)
        if self.mode == "single100":
            if status not in (0, base.DMA_SR_HALTED, base.DMA_SR_IDLE):
                raise base.PreflightError("Fresh 100 MHz DMA status is not cold-ready")
            if length != 0:
                raise base.PreflightError("Fresh 100 MHz DMA LENGTH is not zero")
            if status & base.DMA_SR_HALTED and control & base.DMA_CR_RUNSTOP:
                raise base.PreflightError("Halted fresh DMA unexpectedly has RUNSTOP set")
        else:
            state = base._check_dma_quiescent_status(status, "closeout INIT")
            if (state != base.DMA_SR_IDLE or status & base.DMA_SR_HALTED
                    or not control & base.DMA_CR_RUNSTOP or length != base.PACKET_BYTES):
                raise base.PreflightError("Continuous DMA INIT does not match the prior single-frame baseline")
        self.previous_checker = checker
        self.record("DMA_CHECKER_BASELINE", dmasr=status, dmacr=control,
                    length=length, checker=checker)

    def _current_target(self):
        frames = self.config.frames[self.mode]
        if not 0 <= self.frame_index < len(frames):
            raise base.PreflightError("No unconsumed packet remains in this closeout mode")
        return frames[self.frame_index]

    def _ensure_prior_checker_unchanged(self, actual):
        expected = self.previous_checker
        if expected is None:
            raise base.PreflightError("Checker baseline was not captured at INIT")
        keys = set(expected) - {"stalled_cycles"}
        mismatch = {key: (expected[key], actual.get(key)) for key in keys
                    if actual.get(key) != expected[key]}
        if mismatch or actual.get("stalled_cycles", -1) < expected.get("stalled_cycles", 0):
            raise base.PreflightError("Checker changed before the next packet: " + repr(mismatch))

    def _start_cold_dma_if_needed(self, status, control):
        if self.mode != "single100":
            state = base._check_dma_quiescent_status(status, "before closeout SA")
            if (state != base.DMA_SR_IDLE or status & base.DMA_SR_HALTED
                    or not control & base.DMA_CR_RUNSTOP or control & base.DMA_CR_RESET):
                raise base.PreflightError("Continuous DMA is not Idle with RUNSTOP before SA")
            return
        if status & base.DMA_SR_ERROR_MASK or status & base.DMA_SR_HALTED and control & base.DMA_CR_RUNSTOP:
            raise base.PreflightError("Cold DMA status/control is inconsistent before RUNSTOP")
        if control & base.DMA_CR_RESET:
            raise base.PreflightError("Cold DMA reset bit is set")
        if not control & base.DMA_CR_RUNSTOP:
            self.access("write.RUNSTOP", self.dma.write, 0, control | base.DMA_CR_RUNSTOP)
            deadline = min(self.deadline, time.monotonic() + 1.0)
            while True:
                status = self.read(self.dma, 4, "runstop.dmasr")
                if status & base.DMA_SR_ERROR_MASK:
                    raise base.PreflightError("DMA error while enabling RUNSTOP")
                if not status & base.DMA_SR_HALTED:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Cold DMA did not leave HALTED after RUNSTOP")
                time.sleep(min(0.005, self.remaining()))
        control = self.read(self.dma, 0, "runstop.dmacr")
        status = self.read(self.dma, 4, "runstop.final_dmasr")
        if (not control & base.DMA_CR_RUNSTOP or control & base.DMA_CR_RESET
                or status & base.DMA_SR_ERROR_MASK or status & base.DMA_SR_HALTED):
            raise base.PreflightError("Cold DMA did not reach a clean running state")

    def stage_SEND(self, _):
        target = self._current_target()
        profile = self.config.profiles[PROFILE_FOR_MODE[self.mode]]
        frame_tag = "frame-" + str(target.frame_id)
        marker = "send-" + self.mode + "-" + frame_tag + "-consumed"
        if self.journal.exists(marker):
            raise base.PreflightError("Per-frame send guard is already consumed: " + marker)
        self.journal.claim(marker, {
            "mode": self.mode, "frame_id": target.frame_id,
            "packet_sha256": target.plan.packet_sha256,
            "manifest_sha256": self.config.manifest_sha256,
            "boot_id": self.env["boot_id"],
        })
        self._validate_live_overlay(profile.bit_sha256)
        status = self.read(self.dma, 4, "before_frame.dmasr")
        control = self.read(self.dma, 0, "before_frame.dmacr")
        length = self.read(self.dma, 0x28, "before_frame.length")
        expected_length = 0 if self.mode == "single100" else base.PACKET_BYTES
        if length != expected_length:
            raise base.PreflightError("DMA LENGTH register differs before this frame")
        self._ensure_prior_checker_unchanged(self.checker_values())
        self._start_cold_dma_if_needed(status, control)
        self.current_frame = target
        self.frame_started = time.monotonic()
        self.length_to_idle_s = None
        self._frame_verified = False
        self.buffer = self.access("allocate", self.api.allocate,
                                  shape=(base.PACKET_BYTES,), dtype=self.api.np.uint8)
        flags = getattr(self.buffer, "flags", None)
        if flags is None or not getattr(flags, "c_contiguous", False):
            raise base.PreflightError("PYNQ buffer is not contiguous")
        self.current_address = base._buffer_address(self.buffer)
        base._validate_dma_address(self.current_address, base.PACKET_BYTES, len(self.buffer))
        self.buffer[:base.PACKET_BYTES] = self.api.np.frombuffer(target.plan.packet,
                                                                  dtype=self.api.np.uint8)
        if hashlib.sha256(bytes(self.buffer[:base.PACKET_BYTES])).hexdigest() != target.plan.packet_sha256:
            raise base.PreflightError("DMA buffer SHA differs from the reviewed packet")
        sync = self.access("sync_to_device", base._sync_to_device, self.buffer)
        self.record("FRAME_BUFFER_READY", frame_id=target.frame_id,
                    address=self.current_address, bytes=base.PACKET_BYTES, sync=sync)
        self.access("write.SA", self.dma.write, 0x18, self.current_address)
        if self.mode != "single100":
            ready_status = self.read(self.dma, 4, "before_length.dmasr")
            if ready_status & base.DMA_SR_ERROR_MASK:
                raise base.PreflightError("DMA has an error before LENGTH")
            if not ready_status & base.DMA_SR_IDLE or ready_status & base.DMA_SR_HALTED:
                raise base.PreflightError("DMA is not Idle before LENGTH")
            ready_control = self.read(self.dma, 0, "before_length.dmacr")
            if (not ready_control & base.DMA_CR_RUNSTOP
                    or ready_control & base.DMA_CR_RESET):
                raise base.PreflightError("DMA RUNSTOP/reset state is invalid before LENGTH")
        else:
            status = self.read(self.dma, 4, "cold_before_length.dmasr")
            control = self.read(self.dma, 0, "cold_before_length.dmacr")
            if (status & base.DMA_SR_ERROR_MASK or status & base.DMA_SR_HALTED
                    or not control & base.DMA_CR_RUNSTOP or control & base.DMA_CR_RESET):
                raise base.PreflightError("Cold DMA is not clean and running before LENGTH")
        self.length_started = time.monotonic()
        self.send_attempts += 1
        self.access("write.LENGTH", self.dma.write, 0x28, base.PACKET_BYTES)
        while True:
            status = self.read(self.dma, 4, "frame_poll.dmasr")
            if status & base.DMA_SR_ERROR_MASK:
                raise base.PreflightError("DMA error during frame transfer")
            if status & base.DMA_SR_HALTED:
                raise base.PreflightError("DMA halted during frame transfer")
            idle = bool(status & base.DMA_SR_IDLE)
            if idle and self.length_to_idle_s is None:
                self.length_to_idle_s = time.monotonic() - self.length_started
            done = self.read(self.checker, base.CHECKER_REGS["packet_done_count"],
                             "frame_poll.packet_done_count")
            frame_id = self.read(self.checker, base.CHECKER_REGS["frame_id"],
                                 "frame_poll.frame_id")
            observed = self.read(self.checker, base.CHECKER_REGS["observed_pixels"],
                                 "frame_poll.observed_pixels")
            accepted = self.read(self.checker, base.CHECKER_REGS["accepted_output_total"],
                                 "frame_poll.accepted_output_total")
            check_status = self.read(self.checker, base.CHECKER_REGS["status"],
                                     "frame_poll.status")
            if check_status & (base.STATUS_PACKET_ERROR | base.STATUS_CHECKER_ERROR):
                raise base.PreflightError("Checker reported a packet/pixel error during frame")
            if done > self.previous_checker["packet_done_count"] + 1:
                raise base.PreflightError("Checker packet_done_count advanced by more than one")
            if done == self.previous_checker["packet_done_count"] + 1:
                if frame_id != target.frame_id:
                    raise base.PreflightError("Checker frame_id differs after packet completion")
                expected_accepted = (self.previous_checker["accepted_output_total"] +
                                     base.PIXEL_COUNT) & U32_MASK
                if (idle and observed == base.PIXEL_COUNT and accepted == expected_accepted
                        and check_status == 9):
                    checker = self.checker_values()
                    validate_checker_increment(self.previous_checker, checker, target.plan)
                    if self.length_to_idle_s is None:
                        self.length_to_idle_s = time.monotonic() - self.length_started
                    self.verified_checker = checker
                    self.result = checker
                    self.record("FRAME_DMA_AND_CHECKER_READY", frame_id=target.frame_id,
                                dmasr=status, checker=checker,
                                length_write_to_dma_idle_s=self.length_to_idle_s)
                    return
            time.sleep(min(0.005, self.remaining()))

    def stage_VERIFY(self, _):
        if self.current_frame is None or self.verified_checker is None:
            raise base.PreflightError("SEND did not establish a fresh checker completion")
        target = self.current_frame
        dma = {name: self.read(self.dma, offset, "final." + name)
               for name, offset in (("dmacr", 0), ("dmasr", 4), ("sa", 0x18), ("length", 0x28))}
        if (dma["dmasr"] & base.DMA_SR_ERROR_MASK or dma["dmasr"] & 3 != base.DMA_SR_IDLE
                or not dma["dmasr"] & 0x1000
                or dma["dmacr"] & base.DMA_CR_RUNSTOP == 0 or dma["dmacr"] & base.DMA_CR_RESET
                or dma["sa"] != self.current_address or dma["length"] != base.PACKET_BYTES):
            raise base.PreflightError("Final DMA Idle/control/address/length evidence differs")
        checker = self.checker_values()
        validate_checker_increment(self.previous_checker, checker, target.plan)
        self.verified_checker = checker
        self._frame_verified = True
        metrics = {
            "frame_id": target.frame_id,
            "packet_sha256": target.plan.packet_sha256,
            "foreground_pixels": target.foreground_pixels,
            "clock_mhz": self.config.profiles[PROFILE_FOR_MODE[self.mode]].clock_mhz,
            "dmasr": dma["dmasr"],
            "length_write_to_dma_idle_s": self.length_to_idle_s,
            "length_write_to_checker_verified_s": time.monotonic() - self.length_started,
            "frame_start_to_checker_verified_s": time.monotonic() - self.frame_started,
            "checker": checker,
        }
        self.frame_metrics.append(metrics)
        self.record("FRAME_GOLDEN_MATCH", **metrics)

    def stage_RELEASE(self, _):
        if not self._frame_verified or self.current_frame is None:
            raise base.PreflightError("Frame buffer release is allowed only after Golden verification")
        target = self.current_frame
        self.access("freebuffer", base._release_buffer, self.buffer)
        self.buffer = None
        receipt = {
            "mode": self.mode, "frame_id": target.frame_id,
            "packet_sha256": target.plan.packet_sha256,
            "foreground_pixels": target.foreground_pixels,
            "manifest_sha256": self.config.manifest_sha256,
            "checker": self.verified_checker,
            "metrics": self.frame_metrics[-1],
        }
        self.journal.claim("frame-" + self.mode + "-" + str(target.frame_id) + "-pass", receipt)
        self.previous_checker = dict(self.verified_checker)
        self.frame_index += 1
        self.current_frame = None
        self.current_address = None
        self._frame_verified = False
        self.record("FRAME_BUFFER_RELEASED", frame_id=target.frame_id,
                    completed_frames=self.frame_index)

    def stage_POST(self, report):
        self.health(report)
        if self.mode == "overlay100":
            raise base.PreflightError("overlay100 has no POST stage")
        if (self.frame_index != len(self.config.frames[self.mode]) or self.buffer is not None
                or len(self.frame_metrics) != len(self.config.frames[self.mode])):
            raise base.PreflightError("Not every requested frame was verified and released")
        profile = self.config.profiles[PROFILE_FOR_MODE[self.mode]]
        self._validate_live_overlay(profile.bit_sha256)
        self._claim_mode_pass()
        self.record("CLOSEOUT_MODE_PASS", mode=self.mode, frame_metrics=self.frame_metrics)


def stage_timeout(stage):
    return 25.0 if stage == "LOAD" else 5.0
