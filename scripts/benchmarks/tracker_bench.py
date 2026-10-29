#!/usr/bin/env python3
"""Host-side tracker micro-benchmark: pristine LJ-01 vs the integrated tracker.

This file is intentionally not named ``test_*`` and is not imported by the
pytest regression; run it explicitly.

What it measures
----------------
Only the time spent inside ``MaskCounter.update_detections`` (detection
replay) or ``MaskCounter.update`` (mask frames) on this host's CPython.
Scenario construction, module loading and the memory pass are excluded from
timing. It is a micro-benchmark of tracker logic, not PYNQ throughput: it
says nothing about DMA, camera FPS, PL resources or on-board CPU. Use the
board harness for those.

Fairness
--------
* The baseline module is exported read-only from ``--baseline-rev``
  (default: 5b554617, the verified LJ-01 commit) or read from
  ``--baseline-script``.
* Both modules receive identical detection/mask sequences and constructor
  arguments; only ``stream_counter.py`` differs.
* Each module gets one untimed warm-up run; timed repeats are interleaved
  and the minimum is reported.

Reproduce
---------
    python scripts/benchmarks/tracker_bench.py
    python scripts/benchmarks/tracker_bench.py --repeats 5 --json-out bench.json
    python scripts/benchmarks/tracker_bench.py --baseline-script C:/path/to/stream_counter.py

Interpretation
--------------
* ``us/frame`` is the per-frame tracker cost; lower is better.
* ``tracks`` counts entries left in ``_tracks`` after the run. The LJ-01
  baseline never removes finished tracks, so this grows with run length;
  the integrated tracker keeps active tracks in ``_tracks`` and finished
  ones in a bounded ``_completed_tracks`` buffer (``retained``).
* ``peak_KiB`` comes from a separate tracemalloc pass and is not timed.
"""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import numpy as np

SETTINGS = {
    "width": 320,
    "height": 240,
    "count_axis": "y",
    "line_y": 120,
    "roi_mode": "full",
    "min_area": 12,
}

SCENARIOS = (
    {"name": "replay-2k", "kind": "replay", "frames": 2000},
    {"name": "replay-20k", "kind": "replay", "frames": 20000},
    {"name": "masks-2k", "kind": "masks", "frames": 2000},
)

BASELINE_REV = "5b554617"
BASELINE_PATH = "scripts/wo0016/stream_counter.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


def load_current(repo: Path):
    sys.path.insert(0, str(repo / "scripts" / "wo0016"))
    import stream_counter  # noqa: E402

    return stream_counter


def export_baseline(repo: Path, rev: str, workdir: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{rev}:{BASELINE_PATH}"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"cannot export {BASELINE_PATH} at {rev!r} from {repo}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    path = workdir / "baseline_stream_counter.py"
    path.write_bytes(result.stdout)
    return path


def replay_data(module, frames: int):
    data = []
    for frame_id in range(frames):
        detections = []
        for lane in range(3):
            y = 40 + ((frame_id * 3 + lane * 40) % 160)
            x = 60 + lane * 60
            detections.append(
                module.Detection(x, x + 9, y, y + 9, 100, x + 4.5, y + 4.5)
            )
        data.append(detections)
    return data


def mask_data(frames: int):
    data = []
    for frame_id in range(frames):
        mask = np.zeros((240, 320), np.uint8)
        for lane in range(3):
            y = 40 + ((frame_id * 3 + lane * 40) % 160)
            x = 60 + lane * 60
            mask[y:y + 10, x:x + 10] = 255
        data.append(mask)
    return data


def run_timed(module, kind: str, data):
    counter = module.MaskCounter(**SETTINGS)
    started = time.perf_counter()
    if kind == "replay":
        for frame_id, detections in enumerate(data):
            counter.update_detections(frame_id, detections)
    else:
        for frame_id, mask in enumerate(data):
            counter.update(frame_id, mask)
    return time.perf_counter() - started, counter


def run_memory(module, kind: str, data):
    gc.collect()
    tracemalloc.start()
    _, counter = run_timed(module, kind, data)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak, counter


def track_counts(counter):
    retained = getattr(counter, "_completed_tracks", None)
    return len(counter._tracks), None if retained is None else len(retained)


def build_data(modules, kind: str, frames: int):
    if kind == "replay":
        return {label: replay_data(module, frames) for label, module in modules.items()}
    shared = mask_data(frames)
    return {label: shared for label in modules}


def bench_scenario(modules, scenario, repeats):
    data = build_data(modules, scenario["kind"], scenario["frames"])
    for label, module in modules.items():  # untimed warm-up
        run_timed(module, scenario["kind"], data[label])

    best = {label: float("inf") for label in modules}
    counters = {}
    for _ in range(repeats):
        for label, module in modules.items():
            seconds, counter = run_timed(module, scenario["kind"], data[label])
            best[label] = min(best[label], seconds)
            counters[label] = counter

    rows = []
    for label, module in modules.items():
        peak, memory_counter = run_memory(module, scenario["kind"], data[label])
        tracks, retained = track_counts(counters[label])
        rows.append({
            "scenario": scenario["name"],
            "frames": scenario["frames"],
            "module": label,
            "seconds": best[label],
            "us_per_frame": best[label] * 1e6 / scenario["frames"],
            "frames_per_second": scenario["frames"] / best[label],
            "tracks": tracks,
            "retained": retained,
            "peak_traced_kib": peak / 1024.0,
            "memory_tracks": len(memory_counter._tracks),
        })
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2],
                        help="repository root (default: this worktree)")
    parser.add_argument("--baseline-rev", default=BASELINE_REV,
                        help=f"git revision of the pristine baseline (default: {BASELINE_REV})")
    parser.add_argument("--baseline-script", type=Path, default=None,
                        help="use this stream_counter.py instead of --baseline-rev")
    parser.add_argument("--repeats", type=int, default=3,
                        help="timed repeats per module and scenario (default: 3)")
    parser.add_argument("--json-out", type=Path, default=None,
                        help="write the full result as JSON")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    if not (repo / BASELINE_PATH).is_file():
        parser.error(f"{repo} does not look like the material-counter root")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    if args.baseline_script is not None:
        baseline_source = str(args.baseline_script.resolve())
        baseline_path = args.baseline_script.resolve()
    else:
        baseline_source = f"{args.baseline_rev}:{BASELINE_PATH}"
        tempdir = tempfile.TemporaryDirectory(prefix="tracker-bench-")
        baseline_path = export_baseline(repo, args.baseline_rev, Path(tempdir.name))

    # The baseline module falls back to flat imports (split_touching,
    # binary_stats) when it has no package; import the current module first so
    # scripts/wo0016 is on sys.path for both. Those helper modules are
    # byte-identical between the baseline and HEAD.
    current = load_current(repo)
    baseline = load_module(baseline_path, "baseline_stream_counter")
    modules = {"baseline": baseline, "current": current}

    meta = {
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "repo": str(repo),
        "baseline": baseline_source,
        "settings": SETTINGS,
        "scenarios": [dict(item) for item in SCENARIOS],
        "warmup_runs": 1,
        "timed_repeats": args.repeats,
        "timing_scope": "MaskCounter.update_detections/update only; "
                        "scenario construction and memory pass excluded",
        "note": "host CPython micro-benchmark, not PYNQ board throughput",
    }

    print(f"tracker micro-benchmark ({meta['python']} on {meta['platform']})")
    print(f"repo: {repo}")
    print(f"baseline: {baseline_source}")
    print(f"warm-up: 1 run/module; timed repeats: {args.repeats} (interleaved, minimum)")
    print(f"settings: {SETTINGS}")
    print()

    header = (f"{'scenario':<12} {'frames':>8} {'module':<10} {'us/frame':>10}"
              f" {'frames/s':>9} {'tracks':>7} {'retained':>9} {'peak_KiB':>10}")
    print(header)
    print("-" * len(header))

    rows = []
    for scenario in SCENARIOS:
        scenario_rows = bench_scenario(modules, scenario, args.repeats)
        rows.extend(scenario_rows)
        for row in scenario_rows:
            retained = "-" if row["retained"] is None else str(row["retained"])
            print(f"{row['scenario']:<12} {row['frames']:>8} {row['module']:<10}"
                  f" {row['us_per_frame']:>10.2f} {row['frames_per_second']:>9.1f}"
                  f" {row['tracks']:>7} {retained:>9} {row['peak_traced_kib']:>10.1f}")
        base = next(row for row in scenario_rows if row["module"] == "baseline")
        cand = next(row for row in scenario_rows if row["module"] == "current")
        print(f"{'':<12}delta current vs baseline: "
              f"{100.0 * (cand['seconds'] / base['seconds'] - 1.0):+.2f}%")
        print()

    print("Notes")
    print("- us/frame covers tracker calls only; input generation is pre-built and untimed.")
    print("- tracks = len(_tracks) after the run; baseline keeps finished tracks, so it grows.")
    print("- retained = bounded _completed_tracks length in the integrated tracker ('-' = absent).")
    print("- peak_KiB is a separate tracemalloc pass; it is not a timed measurement.")
    print("- This is a host micro-benchmark. It is not board FPS, CPU load or DMA evidence.")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\nJSON written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())