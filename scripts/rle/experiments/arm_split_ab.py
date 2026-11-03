"""Bounded, offline ARM A/B for field-v1 max_split_area (1200, 2000 and 2400).

No hardware access: feeds saved RLE packets into the committed counter code.
Invoke under `nice -n 19` (the script also lowers its own priority).

The split meter wraps whichever entry point the counter module imports, so the
same benchmark runs against the legacy counter (`split_touching_component`) and
the OPT3A counter (`split_touching_component_verified`). Exactly one entry is
wrapped, never both, so calls and child counts cannot be double-counted.
"""
import argparse
import hashlib
import json
import os
import statistics
import sys
import time

import numpy as np

SPLIT_AREAS = (1200, 2000, 2400)


def rss_kib():
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return -1


def hwm_kib():
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return -1


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))], 3)


def _sha256_file(path):
    try:
        with open(path, "rb") as stream:
            return hashlib.sha256(stream.read()).hexdigest()
    except OSError:
        return None


class Meter:
    def __init__(self):
        self.calls = 0
        self.pieces = {1: 0, 2: 0, 3: 0, 4: 0}

    def reset(self):
        self.calls = 0
        self.pieces = {1: 0, 2: 0, 3: 0, 4: 0}


def install_split_meter(module, meter):
    """Instrument the counter's split entry; returns 'verified' or 'legacy'.

    Exactly one entry point is wrapped. OPT3A counters import
    ``split_touching_component_verified``; older counters import
    ``split_touching_component``. Wrapping both would double-count when a
    caller routes through one of them. Calls are counted before the splitter
    runs, matching the previous wrapper's behaviour on exceptions.
    """
    verified = getattr(module, "split_touching_component_verified", None)
    if verified is not None:
        def wrapped_verified(*args, **kwargs):
            meter.calls += 1
            partition = verified(*args, **kwargs)
            count = min(len(partition.pieces), 4)
            meter.pieces[count] = meter.pieces.get(count, 0) + 1
            return partition
        module.split_touching_component_verified = wrapped_verified
        return "verified"
    legacy = getattr(module, "split_touching_component", None)
    if legacy is not None:
        def wrapped_legacy(*args, **kwargs):
            meter.calls += 1
            pieces = legacy(*args, **kwargs)
            count = min(len(pieces), 4)
            meter.pieces[count] = meter.pieces.get(count, 0) + 1
            return pieces
        module.split_touching_component = wrapped_legacy
        return "legacy"
    raise RuntimeError("counter module has no split entry point")


def load_frames(samples_dir):
    manifest = json.load(open(os.path.join(samples_dir, "manifest.json"), encoding="utf-8"))
    out = []
    for item in manifest["frames"]:
        z = np.load(os.path.join(samples_dir, "frame-%d.rle.npz" % item["frame_id"]),
                    allow_pickle=False)
        out.append((item, z))
    return out


def run_value(RleCounter, FIELD_V1_SETTINGS, library, samples, value, rounds, meter):
    from scripts.rle.protocol import RleFrame
    settings = dict(FIELD_V1_SETTINGS)
    settings["max_split_area"] = value
    records = []
    for rnd in range(rounds):
        counter = RleCounter(library=library, discard_finished_tracks=True,
                             count_axis="x", line_x=320, direction=1, **settings)
        for item, z in samples:
            p = item["packet"]
            frame = RleFrame(width=int(p["width"]), height=int(p["height"]),
                             frame_id=int(p["frame_id"]), params_version=int(p["params_version"]),
                             roi=tuple(int(v) for v in p["roi"]), hw_timestamp=int(p["hw_timestamp"]),
                             threshold=int(p["threshold"]), intensity_offset=int(p["intensity_offset"]),
                             morph_mode=int(p["morph_mode"]), runs=z["runs"],
                             row_projection=z["row_projection"],
                             column_projection=z["column_projection"], total_fg=int(p["total_fg"]),
                             total_run_count=int(p["total_run_count"]), status=int(p["status"]),
                             input_error_code=int(p["input_error_code"]),
                             accepted_pixels=int(p["accepted_pixels"]),
                             actual_bytes=int(p["actual_bytes"]))
            before = rss_kib()
            meter.reset()
            w0, t0, c0 = time.perf_counter(), time.thread_time(), time.process_time()
            result = counter.update_rle(frame, capture_monotonic_ns=item["capture_monotonic_ns"])
            wall = (time.perf_counter() - w0) * 1000.0
            thread = (time.thread_time() - t0) * 1000.0
            proc = (time.process_time() - c0) * 1000.0
            records.append(dict(value=value, round=rnd, fid=frame.frame_id, wall_ms=round(wall, 3),
                                thread_ms=round(thread, 3), proc_ms=round(proc, 3),
                                rss_before_kib=before, rss_after_kib=rss_kib(),
                                rss_hwm_kib=hwm_kib(), split_calls=meter.calls,
                                split_pieces=dict(meter.pieces),
                                split_children=len(counter._split_origins),
                                det=len(result.detections) if result else None,
                                raw=len(result.raw_detections) if result else None,
                                counted=len(result.events) if result else None))
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", required=True, help="directory containing scripts/")
    ap.add_argument("--samples", required=True)
    ap.add_argument("--library", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    if args.rounds < 1:
        ap.error("--rounds must be positive")
    root = os.path.realpath(args.code_root)
    sys.path.insert(0, root)
    from scripts.rle.counter import RleCounter
    from scripts.rle import counter as counter_mod
    from scripts.wo0016 import split_touching as split_mod
    from scripts.standalone.count_controls import FIELD_V1_SETTINGS

    for module in (counter_mod, split_mod):
        expected = os.path.realpath(os.path.join(root, *module.__name__.split(".")) + ".py")
        if os.path.normcase(os.path.realpath(module.__file__)) != os.path.normcase(expected):
            ap.error("import did not use --code-root; run this file directly")

    try:
        os.nice(19)
    except (AttributeError, OSError):
        pass

    meter = Meter()
    split_meter_entry = install_split_meter(counter_mod, meter)
    samples = load_frames(args.samples)
    if not samples:
        ap.error("--samples contains no frames")

    report = {"schema": "m8-split-arm-admission.results.v1",
              "field_v1_max_split_area": FIELD_V1_SETTINGS.get("max_split_area"),
              "split_meter_entry": split_meter_entry,
              "code_root": os.path.abspath(args.code_root),
              "counter_file": counter_mod.__file__,
              "counter_sha256": _sha256_file(counter_mod.__file__),
              "split_touching_file": split_mod.__file__,
              "split_touching_sha256": _sha256_file(split_mod.__file__),
              "library_sha256": _sha256_file(args.library),
              "samples": len(samples), "rounds": args.rounds, "values": {}, "records": []}
    for value in SPLIT_AREAS:
        recs = run_value(RleCounter, FIELD_V1_SETTINGS, args.library, samples, value,
                         args.rounds, meter)
        wall = [r["wall_ms"] for r in recs]
        thread = [r["thread_ms"] for r in recs]
        report["values"][str(value)] = dict(
            wall_p50=pct(wall, 50), wall_p95=pct(wall, 95), wall_p99=pct(wall, 99),
            wall_max=round(max(wall), 3), thread_p50=pct(thread, 50), thread_p95=pct(thread, 95),
            thread_p99=pct(thread, 99), thread_max=round(max(thread), 3),
            split_calls=sum(r["split_calls"] for r in recs),
            split_children=sum(r["split_children"] for r in recs),
            max_det=max((r["det"] or 0) for r in recs),
            rss_hwm_kib=max(r["rss_hwm_kib"] for r in recs))
        report["records"].extend(recs)
    json.dump(report, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(report["values"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()