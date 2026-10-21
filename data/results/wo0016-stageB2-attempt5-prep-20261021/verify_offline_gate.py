"""Reproduce Attempt 5's CCL-only gate. Never accesses the board or cameras."""
from dataclasses import asdict
from pathlib import Path
import csv
import hashlib
import json
import sys
import tarfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
SOURCE = OUT.parent / "wo0016-stageB2-attempt4-20261020-6f13f0f0"
sys.path.insert(0, str(ROOT / "scripts/wo0016"))
from stream_counter import Detection, MaskCounter
from replay_recorded_components import replay
import run_stream_counter as runner


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, document):
    (OUT / name).write_text(json.dumps(document, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")


def main():
    before = json.loads((OUT / "attempt4-before-sha256.json").read_text(encoding="utf-8"))
    assert before == {str(p.relative_to(SOURCE)): sha(p) for p in SOURCE.rglob("*") if p.is_file()}
    original_manifest = json.loads((SOURCE / "SHA256_MANIFEST.json").read_text(encoding="utf-8"))
    for name, digest in original_manifest["files"].items():
        assert sha(SOURCE / name) == digest, name
    frames_path = next(SOURCE.rglob("frame-results.json"))
    frames = json.loads(frames_path.read_text(encoding="utf-8"))
    assert [f["frame_id"] for f in frames] == list(range(165))
    assert sha(frames_path) == "b17d868aa818fd6e156a9bf729dc24163ca98c604fd18a3151b60430d1bf9622"
    config0 = dict(count_axis="x", line_x=160, line_y=120, direction=1,
                   min_area=12, max_area=None, max_step=60., max_lateral_step=10.,
                   hysteresis=2., confirm_hits=2)
    flags = ["--count-axis", "x", "--line-x", "160", "--line-y", "120", "--max-step", "60",
             "--max-lateral-step", "18", "--min-area", "12", "--component-cluster-dx", "24",
             "--component-cluster-dy", "12", "--cooldown-frames", "3", "--cooldown-lateral", "15",
             "--edge-max-missing", "4"]
    config5 = runner.counting_configuration(runner._parser().parse_args(flags))
    configurations = {
        "original": config0,
        "dy18": {**config0, "max_lateral_step": 18},
        "cluster": {**config0, "component_cluster_dx": 24, "component_cluster_dy": 12},
        "cluster_cooldown": {**config0, "max_lateral_step": 18, "component_cluster_dx": 24,
                             "component_cluster_dy": 12, "cooldown_frames": 3, "cooldown_lateral": 15},
        "attempt5": config5,
    }
    results = {}
    for name, config in configurations.items():
        result = replay(frames, config)
        result.update(source_sha256=sha(frames_path), board_accessed=False,
                      evidence_scope="Qualified CCL observations only; gap crossings inferred; physical identity unverified")
        results[name] = result
        save("replay-"+name+".json", result)
    original = results["original"]
    assert original["replayed_total_count"] == 4
    expected_events = [event for frame in frames for event in frame["events"]]
    actual_events = [{k: v for k, v in event.items() if k != "inferred_gap"} for event in original["events"]]
    assert actual_events == expected_events
    assert [row["total_count"] for row in original["frame_totals"]] == [f["total_count"] for f in frames]
    fixed = results["attempt5"]
    assert fixed["replayed_total_count"] == 3
    assert [e["frame_id"] for e in fixed["events"]] == [48, 102, 156]
    assert [e["inferred_gap"] for e in fixed["events"]] == [False, True, False]
    segment_rows = []
    for start, end in ((42, 52), (93, 104), (150, 160)):
        matches = [m for m in fixed["associations"] if start <= m["frame_id"] <= end]
        tracks = sorted({m["track_id"] for m in matches})
        events = [e for e in fixed["events"] if start <= e["frame_id"] <= end]
        assert len(tracks) == len(events) == 1
        segment_rows.append(dict(start=start, end=end, track_ids=tracks, events=events))
    # Also verify the live mask and replay paths share the exact clustering and
    # event decisions. Historical masks cannot be reconstructed from CCL data.
    counter = MaskCounter(**config5)
    observations = []
    for frame in frames:
        result = counter.update_detections(frame["frame_id"], [Detection(**d) for d in frame["detections"]])
        observations.append(dict(frame_id=result.frame_id,
            raw_detections=[asdict(d) for d in result.raw_detections],
            merged_detections=[asdict(d) for d in result.detections],
            associations=counter.last_associations, events=[asdict(e) for e in result.events],
            suppressed_events=[asdict(e) for e in result.suppressed_events], total_count=result.total_count))
    save("merged-observations.json", observations)
    save("counting-config.json", config5)
    save("counting-flags.json", flags)
    selected = [f for f in frames if 42 <= f["frame_id"] <= 52 or 93 <= f["frame_id"] <= 104
                or 150 <= f["frame_id"] <= 160]
    # Keep the exact recorded data, including projections and foreground totals.
    save("selected-original-frames.json", selected)
    with (OUT / "qualified-ccl.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = ["frame_id", "roi_foreground_pixels", "ccl_executed", "component", "centroid_x",
                  "centroid_y", "area", "x_min", "y_min", "x_max", "y_max"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for f in selected:
            for index, detection in enumerate(f["detections"] or [{}]):
                writer.writerow(dict(frame_id=f["frame_id"], roi_foreground_pixels=f["roi_foreground_pixels"],
                    ccl_executed=f["ccl_executed"], component=index if detection else "NONE_QUALIFIED",
                    **detection))
    diagnosis = ["# Attempt #4 逐帧诊断（只含保存的合格 CCL）", "",
                 "bbox 为 [xmin,ymin,xmax,ymax]，端点均包含。小于 min_area 的组件几何未保存；没有视频/二值掩膜。", "",
                 "| 帧 | ROI前景 / CCL执行 | 保存的 (cx,cy,area,bbox) |", "|---:|---|---|"]
    for f in selected:
        detections = "; ".join(f"({d['centroid_x']:.6f}, {d['centroid_y']:.6f}, {d['area']}, "
                               f"[{d['x_min']},{d['y_min']},{d['x_max']},{d['y_max']}])" for d in f["detections"])
        diagnosis.append(f"| {f['frame_id']} | {f['roi_foreground_pixels']} / {f['ccl_executed']} | {detections or '无合格组件'} |")
    diagnosis += ["", "## 核实结论", "",
        "A：f47 联合框19×18、f48 19×17；f155 22×23、f156 22×21。质心相距约14px、同Y走廊，符合单颗尺寸的分裂几何。没有图像，不能确认中孔/反光成因或物理身份。", "",
        "B：f93–95/98–99总前景<12，跳过CCL；f100/101前景17/16，运行CCL但没有面积>=12的组件。f96/97与f102–104所有合格框y_min=96，触及ROI顶部。", "",
        "f96→97 dy=0.076px；f97→102 dy=2.208px。dy=10并未导致缺测；关键是f98–101四帧无合格观测，而默认max_missing=1。不能重建未保存的小组件，也不能离线重做阈值或扩ROI。", "",
        "边缘恢复仅保留已确认、已在线前武装且尚未计数的X轨迹：最多4个缺帧；同一ROI边界；整个gap的Y偏移<=18px；顺向且每帧位移<=60；已有速度预测残差<=60px；面积比0.25..4；新框尺寸<=27px；匹配须双向唯一。", "",
        "f97→102 dx=144.105px，dt=5；上一速度18.8px/帧，预测残差50.105px。f102事件明确标记inferred_gap=true，它是前后观测支持的推断跨线，不是补造f98–101观测。", "",
        "入口f42→43面积59→273超过原面积比4倍门。启用聚类配置时，仅对逐帧、进图边缘裁剪且新框<=27px的关联改比较bbox占用率，使42–52也保持同一轨迹。", "",
        "C：165/22.6726426014=7.27749秒；这是昨晚实测窗口。当前no-consume Stage B-2合同固定165帧，改变帧数须重新评审合同与许可。0~3.5秒投完只改善覆盖，仍不保证运输时间足够。"]
    (OUT / "diagnosis.md").write_text("\n".join(diagnosis)+"\n", encoding="utf-8")
    archive = OUT / "attempt4-preserved.tar.gz"
    if not archive.exists():
        with tarfile.open(archive, "x:gz") as stream:
            stream.add(SOURCE, arcname=SOURCE.name)
    # Verify every archived byte against the before-mutation snapshot.
    with tarfile.open(archive, "r:gz") as stream:
        stored = {}
        for member in stream.getmembers():
            if member.isfile():
                relative = str(Path(member.name).relative_to(SOURCE.name))
                stored[relative] = hashlib.sha256(stream.extractfile(member).read()).hexdigest()
        assert stored == before
    save("archive-verification.json", dict(original_files_unchanged=True, files=len(before),
         archive_sha256=sha(archive), archived_files_verified=True, original_attempt_status="FAIL_UNCHANGED"))
    save("offline-gate.json", dict(status="PASS_CCL_REPLAY_ONLY", frames=165,
         original_frame_totals_and_events_exact=True, original_total=4, replay_total=3,
         segments=segment_rows, observed_crossings=2, inferred_gap_crossings=1,
         suppressed_events=sum(len(f["suppressed_events"]) for f in observations),
         physical_identity_verified=False, five_nut_acceptance="NOT_ESTABLISHED",
         board_sampling_started=False, source_sha256=sha(frames_path)))
    print(json.dumps(dict(total=3, events=fixed["events"], segments=segment_rows,
                          original_files_unchanged=True), ensure_ascii=False))


if __name__ == "__main__":
    main()
