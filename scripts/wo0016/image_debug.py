"""Bounded, opt-in snapshots of the actual image processing path."""
import base64
import threading
import time

import cv2


class DebugSnapshots:
    def __init__(self, interval_s=2.0):
        self._lock = threading.Lock()
        self._enabled = False
        self._generation = 0
        self._last = 0.0
        self._interval = interval_s
        self._snapshot = None

    def set_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        with self._lock:
            self._enabled = enabled
            self._generation += 1
            self._snapshot = None
            self._last = 0.0
        return {"enabled": enabled}

    def snapshot(self):
        with self._lock:
            return self._snapshot or {"enabled": self._enabled, "frame_id": None,
                                      "source": None, "steps": []}

    def offer(self, result, gray, mask, **kwargs):
        try:
            self.capture(result, gray, mask, **kwargs)
        except Exception as exc:
            with self._lock:
                if self._enabled:
                    self._snapshot = {"enabled": True, "frame_id": None, "source": None,
                                      "steps": [], "error": str(exc)}

    def capture(self, result, gray, mask, *, source, source_gray=None, counter=None,
                mapping_note="映射后的 Gray8", threshold_note=None):
        with self._lock:
            now = time.monotonic()
            if not self._enabled or now - self._last < self._interval:
                return
            self._last = now
            generation = self._generation

        def jpeg(image):
            if image is None:
                return None
            ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ok:
                raise ValueError("debug image encoding failed")
            return 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii')

        roi = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        cv2.rectangle(roi, (0, result.roi_top), (mask.shape[1]-1, result.roi_bottom-1), (0, 220, 100), 1)
        regions = roi.copy()
        for detection in result.detections:
            cv2.rectangle(regions, (detection.x_min, detection.y_min),
                          (detection.x_max, detection.y_max), (0, 180, 255), 1)
            cv2.circle(regions, (int(detection.centroid_x), int(detection.centroid_y)), 2, (0, 0, 255), -1)
        tracks = regions.copy()
        if counter is not None:
            if counter.count_axis == 'x':
                cv2.line(tracks, (counter.line_x, 0), (counter.line_x, mask.shape[0]-1), (0, 0, 255), 1)
            else:
                cv2.line(tracks, (0, counter.line_y), (mask.shape[1]-1, counter.line_y), (0, 0, 255), 1)
        for track in result.tracks:
            detection = track.get('detection', {})
            x, y = int(detection.get('centroid_x', 0)), int(detection.get('centroid_y', 0))
            cv2.putText(tracks, str(track['track_id']), (x, y), cv2.FONT_HERSHEY_SIMPLEX, .4, (255, 200, 0), 1)
        src, mapped, binary, roi_image, region_image, track_image = map(jpeg, (source_gray, gray, mask, roi, regions, tracks))
        steps = [
            {"name": "图像映射", "before": src, "after": mapped, "note": ("原始相机帧未提供；" if src is None else "相机灰度图；") + mapping_note},
            {"name": "阈值分割", "before": mapped, "after": binary, "note": threshold_note or ("FPGA S2MM 输出" if source == "pl_dma" else "软件阈值输出")},
            {"name": "ROI 选择", "before": binary, "after": roi_image, "note": "绿色框为计数区域（叠加显示）"},
            {"name": "区域分析", "before": roi_image, "after": region_image, "note": "候选区域与中心点（叠加显示）"},
            {"name": "跟踪与计数", "before": region_image, "after": track_image, "note": f"轨迹 ID 与计数线（叠加显示），累计 {result.total_count}"},
        ]
        with self._lock:
            if self._enabled and self._generation == generation:
                self._snapshot = {"enabled": True, "frame_id": result.frame_id, "source": source, "steps": steps}
