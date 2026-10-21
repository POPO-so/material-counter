"""Offline Attempt #4 CCL replay and track identity audit.

Reads immutable captured evidence and the current host-testable MaskCounter.
It never opens board devices or edits source/evidence inputs.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "wo0016"))
from stream_counter import Detection, MaskCounter  # noqa: E402


BOOT = "6f13f0f0-4433-445f-a023-fa403685aeee"
RESULT = BASE / "home/xilinx/board-runtime-currentboot-v2/stageb2-attempt-4-result.json"
FRAMES = BASE / f"var/lib/wo0016/attempts/{BOOT}/attempt-4/frame-results.json"
COUNTER = ROOT / "scripts/wo0016/stream_counter.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_detection(item: dict) -> Detection:
    return Detection(
        x_min=int(item["x_min"]), x_max=int(item["x_max"]),
        y_min=int(item["y_min"]), y_max=int(item["y_max"]),
        area=int(item["area"]), centroid_x=float(item["centroid_x"]),
        centroid_y=float(item["centroid_y"]),
    )


def main() -> None:
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    rows = json.loads(FRAMES.read_text(encoding="utf-8"))
    cfg = result["counting_config"]
    counter = MaskCounter(
        width=result["camera_output_width"], height=result["camera_output_height"],
        line_y=cfg["line_y"], line_x=cfg["line_x"], count_axis=cfg["count_axis"],
        min_area=cfg["min_area"], max_area=cfg["max_area"],
        max_lateral_step=cfg["max_lateral_step"], max_step=cfg["max_step"],
        direction=cfg["direction"], hysteresis=cfg["hysteresis"],
        confirm_hits=cfg["confirm_hits"],
    )

    tracks: dict[int, list[dict]] = defaultdict(list)
    replay_event_rows: list[dict] = []
    exact_event_match = True
    per_frame_checks = []
    foreground = []
    close_pairs = []

    for row in rows:
        frame_id = int(row["frame_id"])
        detections = [read_detection(d) for d in row["detections"]]
        foreground.append({"frame_id": frame_id,
                           "pixels": int(row.get("roi_foreground_pixels", 0)),
                           "detections": len(detections)})
        replayed = counter.update_detections(frame_id, detections)
        expected_events = [
            (int(e["track_id"]), int(e["frame_id"]), float(e["centroid_x"]), float(e["centroid_y"]))
            for e in row["events"]
        ]
        actual_events = [
            (int(e.track_id), int(e.frame_id), float(e.centroid_x), float(e.centroid_y))
            for e in replayed.events
        ]
        frame_events_match = expected_events == actual_events
        exact_event_match &= frame_events_match
        per_frame_checks.append({
            "frame_id": frame_id,
            "recorded_total": int(row["total_count"]),
            "replayed_total": int(replayed.total_count),
            "recorded_detection_count": len(row["detections"]),
            "replayed_detection_count": len(replayed.detections),
            "events_match": frame_events_match,
        })

        for assoc in counter.last_associations:
            index = int(assoc["detection_index"])
            det = detections[index]
            tracks[int(assoc["track_id"])].append({
                "frame_id": frame_id,
                "centroid_x": det.centroid_x,
                "centroid_y": det.centroid_y,
                "area": det.area,
                "bbox": [det.x_min, det.y_min, det.x_max, det.y_max],
                "association": assoc["kind"],
                "dx": assoc["dx"],
                "dy": assoc["dy"],
            })

        if len(detections) >= 2:
            ordered = sorted(enumerate(detections), key=lambda p: p[1].centroid_x)
            for pos, (i, a) in enumerate(ordered):
                for j, b in ordered[pos + 1:]:
                    dx = b.centroid_x - a.centroid_x
                    dy = b.centroid_y - a.centroid_y
                    distance = math.hypot(dx, dy)
                    if distance <= 20.0:
                        close_pairs.append({
                            "frame_id": frame_id,
                            "detection_a_index": i, "detection_b_index": j,
                            "track_a": next((t for t, h in tracks.items()
                                             if h and h[-1]["frame_id"] == frame_id
                                             and h[-1]["centroid_x"] == a.centroid_x
                                             and h[-1]["centroid_y"] == a.centroid_y), None),
                            "track_b": next((t for t, h in tracks.items()
                                             if h and h[-1]["frame_id"] == frame_id
                                             and h[-1]["centroid_x"] == b.centroid_x
                                             and h[-1]["centroid_y"] == b.centroid_y), None),
                            "dx": dx, "dy": dy, "distance": distance,
                            "area_a": a.area, "area_b": b.area,
                        })

        for event in replayed.events:
            replay_event_rows.append({
                "track_id": int(event.track_id), "event_frame": int(event.frame_id),
                "event_centroid_x": float(event.centroid_x),
                "event_centroid_y": float(event.centroid_y),
            })

    recorded_final = int(result["total_count"])
    replayed_final = int(counter.total_count)
    by_track = {}
    for track_id, history in sorted(tracks.items()):
        events = [e for e in replay_event_rows if e["track_id"] == track_id]
        crossing = None
        for index, obs in enumerate(history):
            if obs["centroid_x"] >= cfg["line_x"]:
                previous = history[index - 1] if index else None
                if previous is not None and previous["centroid_x"] < cfg["line_x"]:
                    crossing = {
                        "previous_observation": previous,
                        "current_observation": obs,
                    }
                    break
        by_track[str(track_id)] = {
            "track_id": track_id,
            "first_frame": history[0]["frame_id"],
            "last_frame": history[-1]["frame_id"],
            "observation_count": len(history),
            "min_area": min(o["area"] for o in history),
            "max_area": max(o["area"] for o in history),
            "counted": bool(events),
            "event": events[0] if events else None,
            "line_x_crossing": crossing,
            "history": history,
        }

    replay_event_rows.sort(key=lambda e: (e["event_frame"], e["track_id"]))
    for i, event in enumerate(replay_event_rows):
        event["previous_event_frame"] = replay_event_rows[i - 1]["event_frame"] if i else None
        event["event_frame_interval"] = (
            event["event_frame"] - replay_event_rows[i - 1]["event_frame"] if i else None
        )
        hist = tracks[event["track_id"]]
        idx = next(j for j, obs in enumerate(hist) if obs["frame_id"] == event["event_frame"])
        event["previous_observation"] = hist[idx - 1] if idx else None
        event["current_observation"] = hist[idx]
        event["first_frame"] = hist[0]["frame_id"]
        event["last_frame"] = hist[-1]["frame_id"]
        event["min_area"] = min(o["area"] for o in hist)
        event["max_area"] = max(o["area"] for o in hist)

    episodes = []
    for item in foreground:
        if not item["pixels"]:
            continue
        if episodes and item["frame_id"] == episodes[-1]["last_frame"] + 1:
            episode = episodes[-1]
            episode["last_frame"] = item["frame_id"]
            episode["frames"] += 1
            episode["peak_foreground_pixels"] = max(episode["peak_foreground_pixels"], item["pixels"])
            episode["peak_frame"] = (item["frame_id"] if item["pixels"] > episode["peak_foreground_pixels"]
                                      else episode["peak_frame"])
            episode["max_components"] = max(episode["max_components"], item["detections"])
        else:
            episodes.append({"first_frame": item["frame_id"], "last_frame": item["frame_id"],
                             "frames": 1, "peak_foreground_pixels": item["pixels"],
                             "peak_frame": item["frame_id"], "max_components": item["detections"]})

    # Fix episode peak-frame bookkeeping without relying on mutation order.
    for episode in episodes:
        segment = [f for f in foreground if episode["first_frame"] <= f["frame_id"] <= episode["last_frame"]]
        peak = max(segment, key=lambda f: f["pixels"])
        episode["peak_foreground_pixels"] = peak["pixels"]
        episode["peak_frame"] = peak["frame_id"]

    max_area_component = max(
        ((d["area"], row["frame_id"], d) for row in rows for d in row["detections"]),
        default=(0, None, None),
    )
    summary = {
        "schema": 1,
        "attempt_id": "attempt-4",
        "boot_id": BOOT,
        "inputs": {
            "result_sha256": sha256(RESULT),
            "frame_results_sha256": sha256(FRAMES),
            "counter_source_sha256": sha256(COUNTER),
        },
        "counting_config": cfg,
        "frame_count": len(rows),
        "recorded_total_count": recorded_final,
        "replayed_total_count": replayed_final,
        "recorded_vs_replayed_events_exact_match": exact_event_match,
        "recorded_vs_replayed_frame_totals_exact_match": all(
            c["recorded_total"] == c["replayed_total"] and c["events_match"]
            for c in per_frame_checks
        ),
        "triggered_track_count": len(replay_event_rows),
        "triggered_tracks": replay_event_rows,
        "all_tracks": by_track,
        "foreground_episodes": episodes,
        "last_30_frames": foreground[-30:],
        "largest_qualified_component": {
            "area": max_area_component[0],
            "frame_id": max_area_component[1],
            "bbox": ([max_area_component[2]["x_min"], max_area_component[2]["y_min"],
                      max_area_component[2]["x_max"], max_area_component[2]["y_max"]]
                     if max_area_component[2] else None),
        },
        "near_20px_component_pairs": close_pairs,
        "per_frame_replay_checks": per_frame_checks,
        "interpretation_limit": (
            "This is a replay of captured qualified CCL detections, not pixels or video. "
            "It proves algorithm event reproduction, not physical nut identity."
        ),
    }

    (BASE / "track-analysis.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with (BASE / "track-analysis.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        fields = ["track_id", "first_frame", "last_frame", "event_frame", "previous_event_frame",
                  "event_frame_interval", "cross_x160_previous_frame", "cross_x160_previous_cx",
                  "cross_x160_previous_cy", "cross_x160_current_frame", "cross_x160_current_cx",
                  "cross_x160_current_cy", "event_previous_cx", "event_previous_cy",
                  "event_current_cx", "event_current_cy", "min_area", "max_area", "observation_count"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for event in replay_event_rows:
            crossing = by_track[str(event["track_id"])]["line_x_crossing"]
            prev_cross = crossing["previous_observation"] if crossing else None
            curr_cross = crossing["current_observation"] if crossing else None
            prev_event = event["previous_observation"]
            curr_event = event["current_observation"]
            writer.writerow({
                "track_id": event["track_id"], "first_frame": event["first_frame"],
                "last_frame": event["last_frame"], "event_frame": event["event_frame"],
                "previous_event_frame": event["previous_event_frame"],
                "event_frame_interval": event["event_frame_interval"],
                "cross_x160_previous_frame": prev_cross["frame_id"] if prev_cross else None,
                "cross_x160_previous_cx": prev_cross["centroid_x"] if prev_cross else None,
                "cross_x160_previous_cy": prev_cross["centroid_y"] if prev_cross else None,
                "cross_x160_current_frame": curr_cross["frame_id"] if curr_cross else None,
                "cross_x160_current_cx": curr_cross["centroid_x"] if curr_cross else None,
                "cross_x160_current_cy": curr_cross["centroid_y"] if curr_cross else None,
                "event_previous_cx": prev_event["centroid_x"] if prev_event else None,
                "event_previous_cy": prev_event["centroid_y"] if prev_event else None,
                "event_current_cx": curr_event["centroid_x"],
                "event_current_cy": curr_event["centroid_y"],
                "min_area": event["min_area"], "max_area": event["max_area"],
                "observation_count": len(tracks[event["track_id"]]),
            })

    def fmt_obs(obs: dict | None) -> str:
        if not obs:
            return "—"
        return f"f{obs['frame_id']} ({obs['centroid_x']:.2f}, {obs['centroid_y']:.2f}), area={obs['area']}"

    lines = [
        "# WO-0016 Attempt #4 轨迹离线核验",
        "",
        f"- Boot ID: `{BOOT}`",
        f"- 输入结果 SHA-256: `{summary['inputs']['result_sha256']}`",
        f"- 逐帧侧录 SHA-256: `{summary['inputs']['frame_results_sha256']}`",
        f"- 回放计数器 SHA-256: `{summary['inputs']['counter_source_sha256']}`",
        f"- 配置: `{json.dumps(cfg, sort_keys=True)}`",
        f"- 帧数: {len(rows)}；记录计数 {recorded_final}；回放计数 {replayed_final}；事件逐帧完全一致: **{exact_event_match}**。",
        "",
        "## 触发轨迹",
        "",
        "| Track | 首末帧 | 首次观测越过 X=160 | 计数触发帧 | 前一计数事件间隔 | 跨线前观测 | 跨线后观测 | 触发前观测 | 触发当前观测 | 面积范围 |",
        "|---:|---:|---|---:|---:|---|---|---|---|---:|",
    ]
    for e in replay_event_rows:
        track = by_track[str(e["track_id"])]
        crossing = track["line_x_crossing"]
        before = crossing["previous_observation"] if crossing else None
        after = crossing["current_observation"] if crossing else None
        lines.append(
            f"| T{e['track_id']} | {e['first_frame']}–{e['last_frame']} | "
            f"{(after['frame_id'] if after else '未观察到')} | {e['event_frame']} | "
            f"{e['event_frame_interval'] if e['event_frame_interval'] is not None else '—'} | "
            f"{fmt_obs(before)} | {fmt_obs(after)} | "
            f"{fmt_obs(e['previous_observation'])} | {fmt_obs(e['current_observation'])} | "
            f"{e['min_area']}–{e['max_area']} |"
        )
    lines += ["", "## 所有轨迹（含未触发）", "",
              "| Track | 首末帧 | 观测数 | min/max area | 计数触发 | X=160 穿越观测 |",
              "|---:|---:|---:|---:|---|---|"]
    for key, track in by_track.items():
        crossing = track["line_x_crossing"]
        crossing_text = (f"{fmt_obs(crossing['previous_observation'])} → {fmt_obs(crossing['current_observation'])}"
                         if crossing else "未观察到跨线")
        lines.append(f"| T{key} | {track['first_frame']}–{track['last_frame']} | {track['observation_count']} | "
                     f"{track['min_area']}–{track['max_area']} | {'是' if track['counted'] else '否'} | {crossing_text} |")
    lines += [
        "",
        "## 轨迹风险判读",
        "",
        "回放计数为 4。T3/T2 分别在帧 47、48 触发，事件只间隔 1 帧；两轨在帧 45–49 长时间并行，X 中心间距约 13.3–14.7 px、Y 偏差较小。"
        "T8/T9 在帧 156 同时触发，帧 155–158 的中心距约 14.4–14.9 px。按每秒投一颗的现场节奏，这两组属于明显的近距重复计数风险，不能当作四颗实体已被一一验证。"
        "它们也可能是紧邻的独立分量；无视频/掩膜不能最终定性为单颗螺母分裂。",
        "",
        "帧 93–104 是第三段弱前景（峰值 53 px）：T6 帧 96–97 面积 15–21 px，T7 帧 102–104 面积 19–53 px，均未穿越 X=160。它可能是暗弱目标或背景小块，侧录不足以鉴别。",
        "",
        "最后 30 帧中，帧 150–160 有活动，帧 161–164 的 ROI 前景均为 0；数据没有显示第五个目标进入剩余窗口。用户报告五颗已投，但一颗可能晚于采样结束，这一情况与侧录相容，尚未被侧录证明。",
        "",
        "## 活动段与近距分量", "",
              "| 活动帧段 | 峰值 ROI 前景 | 峰值帧 | 同帧最大合格组件数 |",
              "|---|---:|---:|---:|"]
    for episode in episodes:
        lines.append(f"| {episode['first_frame']}–{episode['last_frame']} | {episode['peak_foreground_pixels']} | "
                     f"{episode['peak_frame']} | {episode['max_components']} |")
    lines += ["", f"最大合格组件面积为 {max_area_component[0]} px（帧 {max_area_component[1]}），"
              "没有出现与 Attempt #3 帧 24–30 同量级的超大前景块。", "",
              "### 同帧相距不超过 20 px 的组件", "",
              "| 帧 | 组件中心距 | 面积 | 轨迹 |", "|---:|---:|---:|---|"]
    for pair in close_pairs:
        lines.append(f"| {pair['frame_id']} | {pair['distance']:.2f} px (dx={pair['dx']:.2f}, dy={pair['dy']:.2f}) | "
                     f"{pair['area_a']}/{pair['area_b']} | T{pair['track_a']}/T{pair['track_b']} |")
    lines += ["", "## 最后 30 帧前景", "",
              "| 帧 | ROI 前景像素 | 合格 CCL 数 |", "|---:|---:|---:|"]
    for f in foreground[-30:]:
        lines.append(f"| {f['frame_id']} | {f['pixels']} | {f['detections']} |")
    lines += ["", "## 证据边界", "",
              "这份回放使用保存的合格 CCL 组件逐帧调用同一版 `MaskCounter`，并与原始逐帧事件和累计计数比较。"
              "它能核实算法重复性与轨迹几何；没有相机视频或原始二值掩膜，因此不能仅凭 CCL 轨迹证明每条轨迹对应一颗实体螺母。"
              "用户报告五颗已投放，但有一颗可能晚于采样结束；现有数据不证明第五颗是否进入 165 帧窗口。", ""]
    (BASE / "track-analysis.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({
        "result_sha256": summary["inputs"]["result_sha256"],
        "frames_sha256": summary["inputs"]["frame_results_sha256"],
        "counter_sha256": summary["inputs"]["counter_source_sha256"],
        "frame_count": len(rows), "recorded_total": recorded_final,
        "replayed_total": replayed_final, "events_exact": exact_event_match,
        "triggered": replay_event_rows,
        "episodes": episodes,
        "largest_component": summary["largest_qualified_component"],
        "near_pairs": close_pairs,
        "tracks": {k: {kk: vv for kk, vv in t.items() if kk != "history"}
                   for k, t in by_track.items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
