"""Toolkit-independent formatting and frame metadata helpers."""

from __future__ import annotations

import math
import time
from typing import Any


_MISSING = "--"
_STATE_LABELS = {
    "frames_received": "有图像",
    "frame_verified": "帧已校验",
    "disabled": "未启用",
    "unknown": "未连接",
    "running": "运行中",
    "waiting": "等待采集",
    "stopping": "正在停止监控",
    "failed": "运行故障",
    "fail_hold": "DMA 故障保持",
    "complete": "已完成",
    "stopped": "已停止",
    "stale": "数据过期",
}
_RESOURCE_FIELDS = (
    ("CPU 总体", "cpu_percent", "percent"),
    ("系统内存", "memory", "memory"),
    ("CMA 内存池", "cma", "cma"),
    ("MicroSD 文件系统", "disk", "disk"),
    ("以太网接收", "rx", "network_rx"),
    ("以太网发送", "tx", "network_tx"),
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def format_duration(seconds: Any) -> str:
    value = _number(seconds)
    if value is None or value < 0:
        return _MISSING
    whole = int(value)
    return f"{whole // 3600:02d}:{(whole // 60) % 60:02d}:{whole % 60:02d}"


def resource_percent(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and 0 <= number <= 100 else None


def recent_events(events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)][-30:]


def _box_from_item(item: Any) -> tuple[float, float, float, float] | None:
    """Return x1,y1,x2,y2 from common track/detection box representations."""
    if not isinstance(item, dict):
        return None
    candidates = (item, _mapping(item.get("detection")))
    for candidate in candidates:
        box = candidate.get("bbox", candidate.get("box"))
        if isinstance(box, (list, tuple)) and len(box) >= 4:
            x1, y1, x2, y2 = (_number(part) for part in box[:4])
            if None not in (x1, y1, x2, y2):
                return (x1, y1, x2, y2)
        x1 = _number(candidate.get("x_min", candidate.get("x1", candidate.get("left"))))
        y1 = _number(candidate.get("y_min", candidate.get("y1", candidate.get("top"))))
        x2 = _number(candidate.get("x_max", candidate.get("x2", candidate.get("right"))))
        y2 = _number(candidate.get("y_max", candidate.get("y2", candidate.get("bottom"))))
        if None not in (x1, y1, x2, y2):
            return (x1, y1, x2, y2)
        x = _number(candidate.get("x"))
        y = _number(candidate.get("y"))
        width = _number(candidate.get("width", candidate.get("w")))
        height = _number(candidate.get("height", candidate.get("h")))
        if None not in (x, y, width, height):
            return (x, y, x + width, y + height)
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def frame_summary(result: Any) -> tuple[int | None, int | None]:
    """Return the frame's count and region count without inferring fake data."""
    result = _mapping(result)
    count = result.get("total_count", result.get("count"))
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        count = None
    region_count = result.get("detection_count", result.get("regions_count"))
    if isinstance(region_count, bool) or not isinstance(region_count, int) or region_count < 0:
        regions = result.get("detections", result.get("regions"))
        if isinstance(regions, list):
            region_count = len(regions)
        elif isinstance(regions, int) and not isinstance(regions, bool) and regions >= 0:
            region_count = regions
        else:
            region_count = None
    return count, region_count


def roi_bounds(config: Any) -> tuple[float, float, float, float] | None:
    config = _mapping(config)
    top, bottom = _number(config.get("roi_top")), _number(config.get("roi_bottom"))
    if top is not None and bottom is not None and 0 <= top < bottom <= 240:
        return (0, top, 320, bottom)
    roi = config.get("roi", config.get("region_of_interest"))
    if isinstance(roi, (list, tuple)) and len(roi) >= 4:
        values = tuple(_number(value) for value in roi[:4])
        if all(value is not None for value in values):
            return values  # type: ignore[return-value]
    return _box_from_item(roi) if roi is not None else _box_from_item(config)


def _fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    number = _number(value)
    return f"{number:.{digits}f}{suffix}" if number is not None else _MISSING
