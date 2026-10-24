"""Read-only sealed-mask comparison of baseline, default and opt-in counting.

This checks software equivalence, not physical object accuracy or board FPS.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

from mjpeg_preview import render_preview_frame
from run_stream_counter import _parser, counting_configuration, formal_runtime_sha256
from stream_counter import MaskCounter

RAW_SHA256 = "1cac1bdfe0c85677cb4f723757cd1609093dd93e00697c41f31b08d013e432aa"
MANIFEST_SHA256 = "1eab3ebb7775cd4cdacc6a3b41213c1ce394c9c12636c1088f70b5676cfb66df"


def _baseline_configuration(config):
    """Compare sealed defaults without options absent in the historical source."""
    return {key: value for key, value in config.items()
            if key not in ("enable_touching_split", "min_split_area", "max_split_area")}


def canonical(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items() if key != "roi_subtimings_ms"}
    if isinstance(value, (tuple, list)):
        return [canonical(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def snapshot(counter, result):
    return {"result": canonical(result), "associations": canonical(counter.last_associations),
            "rejected_detections": canonical(counter.rejected_detections),
            "rejected_contours": canonical(counter.rejected_contours)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--baseline-ref", default="6568a278")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    root = args.evidence_root.resolve(strict=True)
    output = args.output.resolve()
    if output == root or root in output.parents:
        parser.error("output must be outside sealed evidence")
    if args.rounds < 3:
        parser.error("at least three alternating timing rounds are required")
    repo = Path(__file__).resolve().parents[2]
    baseline_ref = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"], cwd=repo, text=True).strip()
    source = subprocess.check_output(
        ["git", "show", baseline_ref + ":scripts/wo0016/stream_counter.py"], cwd=repo)
    raw_path = root / "capture/frames_gray8_binary8.npz"
    raw_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if raw_hash != RAW_SHA256:
        raise ValueError("sealed -10 raw NPZ SHA-256 mismatch")
    with np.load(raw_path, allow_pickle=False) as archive:
        masks, grays = archive["raw_binary8"], archive["raw_gray8"]
    if (masks.shape != (300, 240, 320) or masks.dtype != np.uint8
            or grays.shape != masks.shape or grays.dtype != np.uint8
            or np.any((masks != 0) & (masks != 255))):
        raise ValueError("unexpected sealed mask/Gray8 geometry or pixel values")
    manifest_bytes = (root / "capture/manifest.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA256:
        raise ValueError("sealed -10 capture manifest SHA-256 mismatch")
    manifest = json.loads(manifest_bytes)
    config = {key: value for key, value in manifest["config"].items()
              if key in inspect.signature(MaskCounter).parameters}
    config["active_rows"] = [20, 220]
    defaults = counting_configuration(_parser().parse_args(["--counting-profile", "full-width-mixed"]))
    baseline_defaults = _baseline_configuration(defaults)
    if config != baseline_defaults:
        raise ValueError("sealed algorithm config differs from the full-width-mixed baseline")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="touching-baseline-") as temp:
        path = Path(temp) / "counter.py"
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("touching_replay_baseline", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        factories = {"baseline": (module.MaskCounter, config),
                     "default_off": (MaskCounter, defaults),
                     "enabled_280": (MaskCounter, dict(defaults, enable_touching_split=True))}
        counters = {key: factory(**options) for key, (factory, options) in factories.items()}
        diffs = {key: [] for key in factories if key != "baseline"}
        totals, events, samples = {}, {key: [] for key in factories}, {key: [] for key in factories}
        frame167 = {}
        for frame_id, mask in enumerate(masks):
            states = {}
            for name, counter in counters.items():
                result = counter.update(frame_id, mask)
                states[name] = snapshot(counter, result)
                events[name].extend(canonical(result.events))
                if frame_id == 167:
                    frame167[name] = canonical(result.detections)
            for name in diffs:
                if states[name] != states["baseline"]:
                    diffs[name].append({"frame_id": frame_id,
                                        "changed_fields": [key for key in states[name]
                                                           if states[name][key] != states["baseline"][key]]})
        totals = {name: counter.total_count for name, counter in counters.items()}
        for round_id in range(args.rounds + 1):
            names = list(factories) if round_id % 2 == 0 else list(reversed(factories))
            for name in names:
                factory, options = factories[name]
                counter = factory(**options)
                start = time.perf_counter()
                for frame_id, mask in enumerate(masks):
                    counter.update(frame_id, mask)
                elapsed = (time.perf_counter() - start) * 1000 / len(masks)
                if round_id:
                    samples[name].append(elapsed)
    preview = render_preview_frame(masks[167], total_count=totals["default_off"], line_y=120,
                                   count_axis="x", line_x=160, source_gray=grays[167],
                                   roi_top=20, roi_bottom=220)
    preview_path = output / "preview-x160-active-rows.png"
    if not cv2.imwrite(str(preview_path), preview):
        raise OSError("failed to write derived preview")
    receipt = {"scope": "LOCAL_OFFLINE_ONLY", "hardware_actions": 0,
               "baseline_commit": baseline_ref, "baseline_source_sha256": hashlib.sha256(source).hexdigest(),
               "tested_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
               "runtime_sha256": formal_runtime_sha256(), "raw_npz_sha256": raw_hash,
               "capture_manifest_sha256": MANIFEST_SHA256,
               "frames": len(masks), "comparison": "all FrameResult fields except timings, plus associations and rejection geometry",
               "different_frames": {name: len(items) for name, items in diffs.items()},
               "differences": diffs, "total_counts": totals, "events": events,
               "frame167_detections": frame167, "configs": {name: options for name, (_, options) in factories.items()},
               "timing_rounds": args.rounds, "host_median_ms_per_frame": {name: statistics.median(items) for name, items in samples.items()},
               "timing_samples_ms_per_frame": samples, "preview_sha256": hashlib.sha256(preview_path.read_bytes()).hexdigest(),
               "physical_identity_accuracy": "NOT_VERIFIED", "board_fps": None}
    (output / "replay-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf8")
    print(json.dumps({key: receipt[key] for key in ("frames", "different_frames", "total_counts", "host_median_ms_per_frame")}))
    if any(diffs.values()) or any(total != 12 for total in totals.values()):
        raise AssertionError("default or tuned opt-in counting differs from the sealed -10 baseline")


if __name__ == "__main__":
    main()
