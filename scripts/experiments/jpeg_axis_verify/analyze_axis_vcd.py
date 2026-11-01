"""Count actual AXIS handshakes/stalls/gaps and verify stability in a port VCD.

Writes JSON to stdout so PowerShell can persist it in the external workdir.
Samples pre-edge values at every rising clock edge after reset release.
"""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vcd", required=True, type=Path)
    ap.add_argument("--jpeg-bytes", type=int, default=13420)
    ap.add_argument("--output-beats", type=int, default=64008)
    ap.add_argument("--transactions", type=int, default=1)
    args = ap.parse_args()
    scopes, codes, values = [], {}, {}
    wanted = {"ap_clk", "ap_rst_n"}
    for prefix in ("jpeg_axis_in", "gray_axis_out"):
        wanted.update(prefix + "_" + s for s in ("TVALID", "TREADY", "TDATA", "TKEEP", "TLAST"))
    channels = {p: {"beats": 0, "bytes": 0, "tlast_count": 0, "stalled_cycles": 0,
                    "gap_cycles": 0, "stability_errors": 0, "started": False,
                    "finished": False, "previous_stalled": None,
                    "frame_start_cycle": None, "frames": []}
                for p in ("jpeg_axis_in", "gray_axis_out")}
    cycles = 0
    current_time = 0
    pending = {}
    timescale = ""
    reading_timescale = False
    resets_after_start = 0

    def tick():
        nonlocal cycles, resets_after_start
        before = values.copy()
        values.update(pending)
        pending.clear()
        if before.get("ap_rst_n") == "1" and values.get("ap_rst_n") == "0" and any(c["started"] for c in channels.values()):
            resets_after_start += 1
        if before.get("ap_clk") != "0" or values.get("ap_clk") != "1":
            return
        if before.get("ap_rst_n") != "1":
            return
        cycles += 1
        for prefix, channel in channels.items():
            def val(s):
                raw = before.get(prefix + "_" + s, "x")
                return None if any(c in raw for c in "xzXZ") else int(raw, 2)
            valid, ready = val("TVALID"), val("TREADY")
            token = (valid, val("TDATA"), val("TKEEP"), val("TLAST"))
            if channel["previous_stalled"] is not None and token != channel["previous_stalled"]:
                channel["stability_errors"] += 1
            channel["previous_stalled"] = token if valid == 1 and ready == 0 else None
            if valid == 1 and ready == 0:
                channel["stalled_cycles"] += 1
            if channel["frame_start_cycle"] is not None and valid == 0:
                channel["gap_cycles"] += 1
            if valid == 1 and ready == 1:
                channel["started"] = True
                if channel["frame_start_cycle"] is None:
                    channel["frame_start_cycle"] = cycles
                channel["beats"] += 1
                keep = val("TKEEP")
                channel["bytes"] += bin(keep or 0).count("1")
                if val("TLAST") == 1:
                    channel["tlast_count"] += 1
                    channel["finished"] = True
                    channel["frames"].append({"first_beat_cycle": channel["frame_start_cycle"],
                                              "last_beat_cycle": cycles,
                                              "transfer_span_cycles": cycles - channel["frame_start_cycle"] + 1})
                    channel["frame_start_cycle"] = None

    with args.vcd.open("r", errors="replace") as stream:
        header = True
        for line in stream:
            line = line.strip()
            if header:
                if line.startswith("$scope"):
                    scopes.append(line.split()[2])
                elif line.startswith("$upscope"):
                    scopes.pop()
                elif line.startswith("$timescale"):
                    timescale = line.replace("$timescale", "").replace("$end", "").strip()
                    reading_timescale = not bool(timescale)
                elif reading_timescale and line and not line.startswith("$"):
                    timescale = line.replace("$end", "").strip()
                    reading_timescale = False
                elif line.startswith("$var"):
                    parts = line.split()
                    name = parts[4]
                    if name in wanted and "AESL_inst_jpeg_axis_gray" in scopes:
                        codes[parts[3]] = name
                elif line.startswith("$enddefinitions"):
                    header = False
                continue
            if not line or line.startswith("$"):
                continue
            if line.startswith("#"):
                tick()
                current_time = int(line[1:])
            elif line[0] in "bB":
                bits, code = line[1:].split()
                if code in codes:
                    pending[codes[code]] = bits
            elif line[0] in "01xXzZ" and line[1:] in codes:
                pending[codes[line[1:]]] = line[0]
        tick()
    missing = sorted(wanted - set(codes.values()))
    failures = []
    if missing:
        failures.append("missing ports: " + ", ".join(missing))
    for prefix, channel in channels.items():
        channel.pop("previous_stalled")
        if channel["tlast_count"] != args.transactions or channel["stability_errors"]:
            failures.append(prefix + ": TLAST count or stalled-token stability failed")
    if channels["jpeg_axis_in"]["bytes"] != args.jpeg_bytes:
        failures.append("input accepted byte count differs from sample")
    if channels["gray_axis_out"]["beats"] != args.output_beats:
        failures.append("output accepted beat count differs from packet")
    if not sum(c["stalled_cycles"] for c in channels.values()):
        failures.append("no actual AXIS backpressure observed")
    if resets_after_start:
        failures.append("reset asserted after a frame started; continuous recovery not established")
    end_to_end = []
    for index, (input_frame, output_frame) in enumerate(zip(channels["jpeg_axis_in"]["frames"], channels["gray_axis_out"]["frames"])):
        end_to_end.append({"transaction": index, "first_input_to_final_output_cycles": output_frame["last_beat_cycle"] - input_frame["first_beat_cycle"] + 1})
    result = {"status": "FAIL" if failures else "PASS", "vcd": str(args.vcd.resolve()),
              "sha256": hashlib.file_digest(args.vcd.open("rb"), "sha256").hexdigest(),
              "sampling": "values immediately before each rising ap_clk edge after ap_rst_n release",
              "timescale": timescale, "sampled_cycles": cycles, "final_timestamp": current_time,
              "channels": channels, "reset_assertions_after_first_frame_start": resets_after_start,
              "frame_latency": end_to_end, "failures": failures}
    print(json.dumps(result, indent=2))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
