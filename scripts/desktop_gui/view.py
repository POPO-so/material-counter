"""Tkinter view for the desktop material counter dashboard.

The controller owns all backend work. This module only renders snapshots and
forwards operator actions from the Tk main thread.
"""

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


class DesktopWindow:
    """Scrollable Tk view. Controller calls and rendering are main-thread only."""

    def __init__(self, root: tk.Misc, controller: Any):
        global tk, ttk, np, Image, ImageDraw, ImageTk
        import tkinter as tk
        from tkinter import ttk
        import numpy as np
        from PIL import Image, ImageDraw, ImageTk

        self.root = root
        self.controller = controller
        self.closing = False
        self._paused = False
        self._debug = False
        self._debug_last = 0.0
        self._resource_enabled = False
        self._resource_mode = "normal"
        self._view_mode = "overlay"
        self._frame: dict[str, Any] | None = None
        self._camera_controls_loaded = False
        self._frame_summary: tuple[int | None, int | None] = (None, None)
        self._photo_refs: dict[str, ImageTk.PhotoImage] = {}
        self._debug_photo_keys: set[str] = set()
        self._status: dict[str, Any] = {}

        root.title("FPGA 物料计数控制看板")
        root.minsize(600, 680)
        root.geometry("1160x900")
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._configure_style()

        shell = ttk.Frame(root)
        shell.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(shell, highlightthickness=0, background="#edf4fc")
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas, padding=(16, 14))
        self._body_window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_body)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self._build_ui()
        self.show_status({})

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#edf4fc")
        style.configure("Card.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("TLabel", background="#edf4fc", foreground="#142640", font=("Segoe UI", 10))
        style.configure("Card.TLabel", background="#ffffff", foreground="#142640")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), foreground="#0b4eae")
        style.configure("Metric.TLabel", font=("Segoe UI", 18, "bold"), foreground="#1467d8", background="#ffffff")
        style.configure("Count.TLabel", font=("Segoe UI", 36, "bold"), foreground="#ffffff", background="#176cdf")
        style.configure("Muted.TLabel", foreground="#536882", background="#ffffff")
        style.configure("Device.TLabel", font=("Segoe UI", 10, "bold"), background="#ffffff")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

    def _build_ui(self) -> None:
        self.body.columnconfigure(0, weight=1)
        header = ttk.Frame(self.body)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="FPGA 物料计数控制看板", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="PYNQ-Z2 视觉计数系统").grid(row=1, column=0, sticky="w")
        self.connection_var = tk.StringVar(value="状态接口未连接")
        ttk.Label(header, textvariable=self.connection_var).grid(row=0, column=1, sticky="e", padx=8)

        self.devices: dict[str, tk.StringVar] = {}
        device_card = self._card("设备状态")
        device_card.grid(row=1, column=0, sticky="ew", pady=5)
        for col, (key, label) in enumerate((("camera", "相机"), ("fpga", "FPGA"), ("dma", "DMA"), ("system", "系统"))):
            device_card.columnconfigure(col, weight=1)
            self.devices[key] = tk.StringVar(value=f"{label}：未连接")
            ttk.Label(device_card, textvariable=self.devices[key], style="Device.TLabel").grid(row=0, column=col, sticky="w", padx=10, pady=8)

        self._build_preview(row=2)
        self._build_metrics(row=3)
        self._build_debug(row=4)
        self._build_events(row=5)
        self._build_resources(row=6)
        self._build_controls(row=7)

    def _card(self, title: str, parent: tk.Misc | None = None) -> ttk.Frame:
        card = ttk.Frame(parent or self.body, style="Card.TFrame", padding=12)
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=title, style="Card.TLabel", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        return card

    def _build_preview(self, row: int) -> None:
        card = self._card("相机画面与跟踪")
        card.grid(row=row, column=0, sticky="ew", pady=5)
        card.columnconfigure(0, weight=1)
        top = ttk.Frame(card, style="Card.TFrame")
        top.grid(row=1, column=0, sticky="ew")
        self.preview_meta = tk.StringVar(value="画面 --    预览 -- FPS")
        ttk.Label(top, textvariable=self.preview_meta, style="Muted.TLabel").pack(side="right")
        self.preview_image = ttk.Label(card, text="等待预览画面", anchor="center", background="#f5f8fc")
        self.preview_image.grid(row=2, column=0, sticky="ew", pady=4)
        self.source_var = tk.StringVar(value="图像来源未确认")
        ttk.Label(card, textvariable=self.source_var, style="Muted.TLabel").grid(row=3, column=0, sticky="w")
        controls = ttk.Frame(card, style="Card.TFrame")
        controls.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(controls, text="显示模式", style="Card.TLabel").pack(side="left")
        self.view_mode_var = tk.StringVar(value="原图与叠加层")
        self.view_mode = ttk.Combobox(controls, state="readonly", width=20, textvariable=self.view_mode_var, values=("原图与叠加层", "Binary8 二值图"))
        self.view_mode.pack(side="left", padx=6)
        self.view_mode.bind("<<ComboboxSelected>>", self._set_view_mode)
        self.pause_button = ttk.Button(controls, text="暂停预览", command=self._toggle_pause)
        self.pause_button.pack(side="right")
        self.binary_image = ttk.Label(card, text="等待二值化画面", anchor="center", background="#101820", foreground="white")
        self.binary_image.grid(row=5, column=0, sticky="ew", pady=(8, 0))

    def _build_metrics(self, row: int) -> None:
        card = self._card("计数与运行指标")
        card.grid(row=row, column=0, sticky="ew", pady=5)
        card.columnconfigure(0, weight=1)
        self.count_var = tk.StringVar(value="--")
        count_box = tk.Frame(card, bg="#176cdf", padx=16, pady=8)
        count_box.grid(row=1, column=0, sticky="ew", pady=3)
        ttk.Label(count_box, text="累计计数 · 本次会话", foreground="#e5efff", background="#176cdf").pack()
        ttk.Label(count_box, textvariable=self.count_var, style="Count.TLabel").pack()
        self.count_note = tk.StringVar(value="等待真实计数数据")
        ttk.Label(count_box, textvariable=self.count_note, foreground="#e5efff", background="#176cdf").pack()
        self.metric_vars: dict[str, tk.StringVar] = {}
        metrics = ttk.Frame(card, style="Card.TFrame")
        metrics.grid(row=2, column=0, sticky="ew")
        names = (("speed", "输送速度"), ("area", "最近越线面积"), ("detections", "当前帧区域数"), ("fps", "实际处理帧率"), ("elapsed", "运行时长"), ("phase", "当前状态"))
        for index, (key, label) in enumerate(names):
            col, line = index % 3, index // 3
            metrics.columnconfigure(col, weight=1)
            cell = ttk.Frame(metrics, style="Card.TFrame", padding=6)
            cell.grid(row=line, column=col, sticky="nsew")
            ttk.Label(cell, text=label, style="Muted.TLabel").pack(anchor="w")
            self.metric_vars[key] = tk.StringVar(value="--")
            ttk.Label(cell, textvariable=self.metric_vars[key], style="Metric.TLabel").pack(anchor="w")
        self.resolution_var = tk.StringVar(value="画面 -- × -- · 预览 -- FPS")
        ttk.Label(card, textvariable=self.resolution_var, style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(4, 0))

    def _build_debug(self, row: int) -> None:
        card = self._card("图像处理调试")
        card.grid(row=row, column=0, sticky="ew", pady=5)
        bar = ttk.Frame(card, style="Card.TFrame")
        bar.grid(row=1, column=0, sticky="ew")
        self.debug_button = ttk.Button(bar, text="开启调试", command=self._toggle_debug)
        self.debug_button.pack(side="left")
        self.debug_status = tk.StringVar(value="连接服务后可开启")
        ttk.Label(bar, textvariable=self.debug_status, style="Muted.TLabel").pack(side="left", padx=8)
        self.debug_meta = tk.StringVar(value="帧号 --    来源 --")
        ttk.Label(card, textvariable=self.debug_meta, style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.debug_steps = ttk.Frame(card, style="Card.TFrame")
        self.debug_steps.grid(row=3, column=0, sticky="ew")

    def _build_events(self, row: int) -> None:
        card = self._card("系统事件 · 最近 30 条")
        card.grid(row=row, column=0, sticky="ew", pady=5)
        card.columnconfigure(0, weight=1)
        box = ttk.Frame(card, style="Card.TFrame")
        box.grid(row=1, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)
        self.events = tk.Listbox(box, height=7, activestyle="none", exportselection=False)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.events.yview)
        self.events.configure(yscrollcommand=scroll.set)
        self.events.grid(row=0, column=0, sticky="ew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.events.insert("end", "等待服务端状态事件…")

    def _build_resources(self, row: int) -> None:
        card = self._card("板端资源监视 · 独立采样，默认关闭")
        card.grid(row=row, column=0, sticky="ew", pady=5)
        controls = ttk.Frame(card, style="Card.TFrame")
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.resource_button = ttk.Button(controls, text="开启监视", command=self._toggle_resources)
        self.resource_button.pack(side="left")
        self.resource_mode_var = tk.StringVar(value="normal")
        self.resource_mode = ttk.Combobox(controls, state="readonly", width=12, textvariable=self.resource_mode_var, values=("normal", "detail"))
        self.resource_mode.pack(side="left", padx=8)
        self.resource_mode.bind("<<ComboboxSelected>>", self._set_resource_mode)
        self.resource_status = tk.StringVar(value="资源监视不可用")
        ttk.Label(controls, textvariable=self.resource_status, style="Muted.TLabel").pack(side="left", padx=6)
        self.resource_values: dict[str, tk.StringVar] = {}
        grid = ttk.Frame(card, style="Card.TFrame")
        grid.grid(row=2, column=0, sticky="ew")
        for column in range(3):
            grid.columnconfigure(column, weight=1)
        for index, (label, key, _) in enumerate(_RESOURCE_FIELDS):
            cell = ttk.Frame(grid, style="Card.TFrame", padding=6, relief="solid")
            cell.grid(row=index // 3, column=index % 3, sticky="nsew", padx=2, pady=2)
            ttk.Label(cell, text=label, style="Muted.TLabel").pack(anchor="w")
            self.resource_values[key] = tk.StringVar(value="--")
            ttk.Label(cell, textvariable=self.resource_values[key], style="Metric.TLabel").pack(anchor="w")
        self.resource_details = ttk.Frame(card, style="Card.TFrame")
        self.resource_details.grid(row=3, column=0, sticky="ew", pady=(8, 0))

    def _build_controls(self, row: int) -> None:
        card = self._card("监控操作")
        card.grid(row=row, column=0, sticky="ew", pady=5)
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=1, column=0, sticky="ew")
        self.start_button = ttk.Button(actions, text="开始采集", command=lambda: self._invoke("start"))
        self.stop_button = ttk.Button(actions, text="停止监控", command=lambda: self._invoke("stop"))
        self.reset_button = ttk.Button(actions, text="计数清零", command=lambda: self._invoke("reset"))
        self.start_button.pack(side="left", padx=(0, 6))
        self.stop_button.pack(side="left", padx=6)
        self.reset_button.pack(side="left", padx=6)
        self.update_button = ttk.Button(actions, text="更新后端 Python", command=lambda: self._invoke("update_backend"))
        self.update_button.pack(side="right")
        self.update_status = tk.StringVar(value="本机同步程序状态未提供")
        ttk.Label(card, textvariable=self.update_status, style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        calibration = ttk.LabelFrame(card, text="相机标定", padding=8)
        calibration.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        calibration.columnconfigure(1, weight=1)
        self.calibration = calibration
        self.exposure_var = tk.StringVar()
        self.brightness_var = tk.StringVar()
        ttk.Label(calibration, text="曝光时间（50–10000）").grid(row=0, column=0, sticky="w", padx=3, pady=3)
        self.exposure_entry = ttk.Entry(calibration, textvariable=self.exposure_var)
        self.exposure_entry.grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Label(calibration, text="亮度（-64–64）").grid(row=1, column=0, sticky="w", padx=3, pady=3)
        self.brightness_entry = ttk.Entry(calibration, textvariable=self.brightness_var)
        self.brightness_entry.grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        self.apply_button = ttk.Button(calibration, text="应用参数", command=self._apply_calibration)
        self.restore_button = ttk.Button(calibration, text="恢复基准参数", command=lambda: self._invoke_calibration({"reset": True}))
        self.apply_button.grid(row=2, column=0, padx=3, pady=5, sticky="w")
        self.restore_button.grid(row=2, column=1, padx=3, pady=5, sticky="w")
        self.feedback = tk.StringVar(value="")
        ttk.Label(card, textvariable=self.feedback, style="Muted.TLabel", wraplength=850).grid(row=4, column=0, sticky="w", pady=4)

    def _update_scroll_region(self, _event: Any = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_body(self, event: Any) -> None:
        self.canvas.itemconfigure(self._body_window, width=event.width)

    def _on_mousewheel(self, event: Any) -> None:
        if event.delta:
            self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def _on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        for button in (self.start_button, self.stop_button, self.reset_button, self.update_button, self.pause_button, self.debug_button, self.resource_button):
            button.configure(state="disabled")
        try:
            accepted = self.controller.close()
            if accepted is False:
                raise RuntimeError("控制器未能启动关闭流程")
            self.feedback.set("正在停止任务并关闭窗口…")
        except Exception as exc:
            self.closing = False
            self.feedback.set(f"关闭失败：{exc}")
            self.show_status(self._status)

    def _invoke(self, method: str) -> None:
        try:
            getattr(self.controller, method)()
            self.feedback.set("操作已提交")
        except Exception as exc:
            self.feedback.set(f"操作失败：{exc}")

    def _invoke_calibration(self, payload: dict[str, Any]) -> None:
        try:
            self.controller.calibrate(payload)
            self.feedback.set("标定操作已提交")
        except Exception as exc:
            self.feedback.set(f"标定操作失败：{exc}")

    def _apply_calibration(self) -> None:
        try:
            exposure = int(self.exposure_var.get())
            brightness = int(self.brightness_var.get())
            if not 50 <= exposure <= 10000 or not -64 <= brightness <= 64:
                raise ValueError("曝光或亮度超出允许范围")
        except ValueError as exc:
            self.feedback.set(f"参数无效：{exc}")
            return
        self._invoke_calibration({"exposure_time_absolute": exposure, "brightness": brightness})

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_button.configure(text="恢复预览" if self._paused else "暂停预览")
        self._invoke_value("set_preview_paused", self._paused)
        self._render_frame()

    def _set_view_mode(self, _event: Any = None) -> None:
        self._view_mode = "binary" if self.view_mode_var.get().startswith("Binary8") else "overlay"
        self._render_frame()

    def _toggle_debug(self) -> None:
        self._debug = not self._debug
        self._invoke_value("set_debug", self._debug)
        self._render_debug()

    def _toggle_resources(self) -> None:
        self._resource_enabled = not self._resource_enabled
        self._invoke_resource()

    def _set_resource_mode(self, _event: Any = None) -> None:
        self._resource_mode = self.resource_mode_var.get()
        self._invoke_resource()

    def _invoke_resource(self) -> None:
        try:
            self.controller.set_resource(self._resource_enabled, self._resource_mode)
        except Exception as exc:
            self.feedback.set(f"资源监视操作失败：{exc}")

    def _invoke_value(self, method: str, value: Any) -> None:
        try:
            getattr(self.controller, method)(value)
        except Exception as exc:
            self.feedback.set(f"操作失败：{exc}")

    def show_message(self, message: str) -> None:
        self.feedback.set(str(message))

    def show_status(self, data: dict[str, Any]) -> None:
        self._status = _mapping(data)
        telemetry = _mapping(data.get("telemetry"))
        preview = _mapping(data.get("preview"))
        capabilities = _mapping(data.get("capabilities"))
        debug = _mapping(data.get("debug"))
        resources = _mapping(data.get("resource_monitor"))
        connected = bool(data) and data.get("connected") is not False and data.get("online") is not False
        self.connection_var.set("状态接口已连接" if connected else "状态接口未连接")

        phase = str(telemetry.get("phase", "unknown"))
        for key, label in (("camera", "相机"), ("fpga", "FPGA"), ("dma", "DMA")):
            state = telemetry.get(key, "unknown")
            self.devices[key].set(f"{label}：{_STATE_LABELS.get(str(state), str(state) if state else '未连接')}")
        shown_phase = "stale" if phase == "running" and telemetry.get("stale") is True else phase
        self.devices["system"].set(f"系统：{_STATE_LABELS.get(shown_phase, '状态未提供')}")

        width, height = preview.get("width"), preview.get("height")
        valid_size = isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0
        self.preview_meta.set(f"画面 {width} × {height}    预览 {_fmt(preview.get('fps'))} FPS" if valid_size else f"画面 --    预览 {_fmt(preview.get('fps'))} FPS")
        self.resolution_var.set(f"画面 {width} × {height} · 预览 {_fmt(preview.get('fps'))} FPS" if valid_size else f"画面 -- × -- · 预览 {_fmt(preview.get('fps'))} FPS")
        self.source_var.set("预览已暂停 · 画面冻结" if self._paused else
                            str(telemetry.get("source", "图像来源未确认")))

        fresh = telemetry.get("stale") is False and _number(telemetry.get("last_frame_age_seconds")) is not None and telemetry.get("last_frame_age_seconds", 999) <= 5 and phase == "running"
        valid_source = telemetry.get("source") in ("software", "pl_dma")
        final = phase in ("complete", "stopped")
        count = telemetry.get("total_count")
        if not (self._frame is not None and not self._paused):
            self.count_var.set(str(count) if valid_source and (fresh or final) and isinstance(count, int) else _MISSING)
            self.metric_vars["detections"].set(str(telemetry.get("detections")) if isinstance(telemetry.get("detections"), int) and fresh else _MISSING)
        self.count_note.set("本次会话最终结果" if final else "已同步最新完成帧" if fresh else "尚无已处理帧")
        self.metric_vars["speed"].set(f"{_fmt(telemetry.get('cm_per_second'))} cm/s" if _number(telemetry.get("cm_per_second")) is not None and fresh else f"{_fmt(telemetry.get('px_per_frame'))} px/帧" if _number(telemetry.get("px_per_frame")) is not None and fresh else _MISSING)
        self.metric_vars["area"].set(f"{telemetry.get('last_crossing_area_px')} px" if isinstance(telemetry.get("last_crossing_area_px"), int) and (fresh or final) else _MISSING)
        self.metric_vars["fps"].set(_fmt(telemetry.get("processing_fps")) if fresh else _MISSING)
        self.metric_vars["elapsed"].set(format_duration(telemetry.get("elapsed_seconds")))
        self.metric_vars["phase"].set(_STATE_LABELS.get(shown_phase, "状态未提供"))

        events = recent_events(telemetry.get("events"))
        self.events.delete(0, "end")
        if not events:
            self.events.insert("end", "尚无服务端状态事件")
        else:
            for event in events:
                timestamp = _fmt(event.get("time"), 0) if _number(event.get("time")) is not None else _MISSING
                self.events.insert("end", f"{timestamp}  {str(event.get('message', ''))[:500]}")
            self.events.yview_moveto(1.0)

        self._set_capability_buttons(capabilities, connected)
        self.update_status.set("更新时将安全停止本次采集并重新部署" if capabilities.get("update_backend") is True
                               else "启动采集后可更新后端")
        self._update_calibration(data, capabilities, connected)
        self._update_resources(resources, data.get("resource_monitor") is not None)
        self._debug = debug.get("enabled") is True
        self.debug_button.configure(text="关闭调试" if self._debug else "开启调试", state="normal" if isinstance(debug.get("enabled"), bool) and connected and not self.closing else "disabled")
        self.debug_status.set("调试已开启" if self._debug else "调试已关闭" if isinstance(debug.get("enabled"), bool) else "服务端未提供调试状态")
        self._render_debug()
        if not self._frame:
            self.preview_image.configure(text="等待预览画面", image="")
            self.binary_image.configure(text="等待二值化画面", image="")

    def _set_capability_buttons(self, caps: dict[str, Any], connected: bool) -> None:
        capability_keys = (
            (self.start_button, ("start", "start_capture")),
            (self.stop_button, ("stop",)),
            (self.reset_button, ("reset",)),
            (self.view_mode, ("view_mode",)),
            (self.update_button, ("update_backend", "backend_update")),
        )
        for button, keys in capability_keys:
            enabled = connected and not self.closing and any(caps.get(key) is True for key in keys)
            button.configure(state="normal" if enabled else "disabled")

    def _update_calibration(self, data: dict[str, Any], caps: dict[str, Any], connected: bool) -> None:
        enabled = connected and not self.closing and caps.get("calibration") is True
        self.calibration.grid() if enabled else self.calibration.grid_remove()
        state = "normal" if enabled else "disabled"
        for widget in (self.exposure_entry, self.brightness_entry, self.apply_button, self.restore_button):
            widget.configure(state=state)
        controls = _mapping(data.get("camera_controls"))
        if controls and not self._camera_controls_loaded:
            exposure = controls.get("exposure_time_absolute")
            brightness = controls.get("brightness")
            if isinstance(exposure, int) and isinstance(brightness, int):
                self.exposure_var.set(str(exposure))
                self.brightness_var.set(str(brightness))
                self._camera_controls_loaded = True

    def _update_resources(self, resource: dict[str, Any], available: bool) -> None:
        metrics = _mapping(resource.get("metrics"))
        self._resource_enabled = resource.get("enabled") is True
        self._resource_mode = resource.get("mode") if resource.get("mode") in ("normal", "detail") else "normal"
        self.resource_mode_var.set(self._resource_mode)
        self.resource_button.configure(text="关闭监视" if self._resource_enabled else "开启监视", state="normal" if available and not self.closing else "disabled")
        self.resource_mode.configure(state="readonly" if available and not self.closing else "disabled")
        self.resource_status.set("正在采样" if self._resource_enabled else "采样已停止" if available else "资源监视不可用")
        if not available or not self._resource_enabled:
            for variable in self.resource_values.values():
                variable.set(_MISSING)
            for child in self.resource_details.winfo_children():
                child.destroy()
            return

        self.resource_values["cpu_percent"].set(f"{_fmt(metrics.get('cpu_percent'))}%" if resource_percent(metrics.get("cpu_percent")) is not None else _MISSING)
        mem = ("memory", "memory_total_kib", "memory_available_kib")
        cma = ("cma", "cma_total_kib", "cma_free_kib")
        disk = ("disk", "rootfs_total_bytes", "rootfs_used_bytes")
        for key, total_key, rest_key in (mem, cma, disk):
            total, rest = _number(metrics.get(total_key)), _number(metrics.get(rest_key))
            used = (total - rest) if key != "disk" else rest
            pct = (used * 100 / total) if total and used is not None and 0 <= used <= total else None
            self.resource_values[key].set(f"{pct:.1f}%" if pct is not None else _MISSING)
        link = _number(metrics.get("network_link_mbps"))
        for key, field in (("rx", "network_rx_bytes_per_second"), ("tx", "network_tx_bytes_per_second")):
            rate = _number(metrics.get(field))
            mbps = rate * 8 / 1_000_000 if rate is not None else None
            self.resource_values[key].set(f"{mbps:.2f} Mbps" if mbps is not None else _MISSING)

        for child in self.resource_details.winfo_children():
            child.destroy()
        if self._resource_mode != "detail":
            return
        detail = _mapping(resource.get("detail"))
        fpga = _mapping(detail.get("fpga"))
        rows = []
        for key, label in (("lut", "LUT"), ("ff", "FF 寄存器"), ("bram", "BRAM Tile"), ("dsp", "DSP"), ("lutram", "LUT 存储"), ("iob", "PL I/O"), ("bufg", "BUFG 时钟"), ("mmcm", "MMCM"), ("pll", "PLL")):
            values = fpga.get(key)
            if isinstance(values, (list, tuple)) and len(values) >= 2 and _number(values[0]) is not None and _number(values[1]) and values[1] > 0:
                rows.append((label, f"{values[0]} / {values[1]} · {values[0] * 100 / values[1]:.2f}%"))
            else:
                rows.append((label, "未提供"))
        dma = _mapping(detail.get("dma_status"))
        for label, value in (("DMA MM2S / S2MM", f"{dma.get('mm2s', _MISSING)} / {dma.get('s2mm', _MISSING)}"),
                             ("DDR 读写带宽", "未接入计数器"),
                             ("流水线", f"{detail.get('frames', _MISSING)} 帧 · {_fmt(detail.get('processing_fps'))} FPS"),
                             ("USB 相机", detail.get("camera", "未提供")),
                             ("SD 卡读 / 写", f"{_fmt(_number(metrics.get('sd_read_bytes_per_second')) * 8 / 1_000_000 if _number(metrics.get('sd_read_bytes_per_second')) is not None else None, 2, ' Mbps')} / {_fmt(_number(metrics.get('sd_write_bytes_per_second')) * 8 / 1_000_000 if _number(metrics.get('sd_write_bytes_per_second')) is not None else None, 2, ' Mbps')}"),
                             ("SoC 温度", f"{_fmt(metrics.get('soc_temperature_c'))} °C"), ("供电", "未接入传感器")):
            rows.append((label, str(value)))
        ttk.Label(self.resource_details, text="FPGA 位流资源与运行状态", style="Card.TLabel", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        for label, value in rows:
            ttk.Label(self.resource_details, text=f"{label}：{value}", style="Muted.TLabel", wraplength=850).pack(anchor="w", pady=1)

    def show_frame(self, frame: dict[str, Any]) -> None:
        self._frame = frame if isinstance(frame, dict) else None
        self._frame_summary = frame_summary(self._frame.get("result")) if self._frame else (None, None)
        if not self._paused:
            self._apply_frame_summary()
        self._render_frame()
        self._render_debug()

    def _apply_frame_summary(self) -> None:
        count, regions = self._frame_summary
        self.count_var.set(str(count) if count is not None else _MISSING)
        self.metric_vars["detections"].set(str(regions) if regions is not None else _MISSING)

    def _render_frame(self) -> None:
        frame = self._frame
        if not frame:
            self.preview_image.configure(text="等待预览画面", image="")
            self.binary_image.configure(text="等待二值化画面", image="")
            return
        if self._paused:
            self.source_var.set("预览已暂停 · 画面冻结")
            return
        try:
            gray = np.asarray(frame["gray"], dtype=np.uint8)
            mask = np.asarray(frame["mask"], dtype=np.uint8)
            if gray.shape != (240, 320) or mask.shape != (240, 320):
                raise ValueError("帧尺寸应为 320×240")
            overlay = Image.fromarray(gray, mode="L").convert("RGB")
            draw = ImageDraw.Draw(overlay)
            config = _mapping(frame.get("config"))
            self._draw_config_line(draw, config)
            result = _mapping(frame.get("result"))
            for kind, color in (("detections", "#00e0ff"), ("tracks", "#ffcc00")):
                items = result.get(kind, [])
                if not isinstance(items, list):
                    continue
                for item in items:
                    box = _box_from_item(item)
                    if box:
                        bounds = self._scale_box(box)
                        draw.rectangle(bounds, outline=color, width=2)
                        detail = _mapping(item)
                        nested = _mapping(detail.get("detection"))
                        label = str(detail.get("track_id", detail.get("id", nested.get("id", kind[:-1]))))
                        draw.text((bounds[0] + 2, max(0, bounds[1] - 13)), label, fill=color)
                    else:
                        center = self._center_from_item(item)
                        if center:
                            x, y = center
                            draw.ellipse((x - 4, y - 4, x + 4, y + 4), outline=color, width=2)
            selected = Image.fromarray(mask, mode="L").convert("RGB") if self._view_mode == "binary" else overlay
            self._set_image(self.preview_image, selected, "")
            self._set_image(self.binary_image, Image.fromarray(mask, mode="L").convert("RGB"), "")
            frame_id = frame.get("frame_id", _MISSING)
            source = frame.get("source", "来源未提供")
            self.source_var.set(f"帧 {frame_id} · {source}")
            fps = _mapping(self._status.get("preview")).get("fps")
            self.preview_meta.set(f"画面 320 × 240    预览 {_fmt(fps)} FPS    帧 {frame_id}")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            self.preview_image.configure(text=f"帧不可用：{exc}", image="")
            self.binary_image.configure(text="二值化画面不可用", image="")

    @staticmethod
    def _scale_box(box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = box
        if all(-1 <= value <= 1 for value in box):
            x1, x2, y1, y2 = x1 * 320, x2 * 320, y1 * 240, y2 * 240
        return tuple(int(round(value)) for value in (max(0, min(319, x1)), max(0, min(239, y1)), max(0, min(319, x2)), max(0, min(239, y2))))

    @staticmethod
    def _center_from_item(item: Any) -> tuple[int, int] | None:
        item = _mapping(item)
        nested = _mapping(item.get("detection"))
        x = _number(item.get("centroid_x", item.get("cx", item.get("center_x", nested.get("centroid_x")))))
        y = _number(item.get("centroid_y", item.get("cy", item.get("center_y", nested.get("centroid_y")))))
        if x is None or y is None:
            return None
        if 0 <= x <= 1 and 0 <= y <= 1:
            x, y = x * 320, y * 240
        return int(max(0, min(319, x))), int(max(0, min(239, y)))

    @staticmethod
    def _draw_config_line(draw: ImageDraw.ImageDraw, config: dict[str, Any]) -> None:
        if config.get("count_axis") == "x":
            x = _number(config.get("line_x"))
            if x is not None:
                if 0 <= x <= 1:
                    x *= 320
                draw.line((int(x), 0, int(x), 239), fill="#ff4d4d", width=2)
            return
        line = config.get("count_line", config.get("counting_line"))
        if isinstance(line, (list, tuple)) and len(line) >= 4:
            coords = [_number(value) for value in line[:4]]
            if all(value is not None for value in coords):
                if all(0 <= value <= 1 for value in coords):
                    coords = [coords[0] * 320, coords[1] * 240, coords[2] * 320, coords[3] * 240]
                draw.line(tuple(int(value) for value in coords), fill="#ff4d4d", width=2)
                return
        y = _number(config.get("line_y", config.get("count_line_y")))
        if y is not None:
            if 0 <= y <= 1:
                y *= 240
            draw.line((0, int(y), 319, int(y)), fill="#ff4d4d", width=2)

    def _set_image(self, widget: ttk.Label, image: Image.Image, empty_text: str, debug: bool = False) -> None:
        width = min(220 if debug else 640, max(320, self.canvas.winfo_width() - 56))
        if debug:
            width = 220
        height = max(1, round(width * image.height / image.width))
        rendered = image.resize((width, height), Image.Resampling.NEAREST)
        photo = ImageTk.PhotoImage(rendered)
        key = str(widget)
        self._photo_refs[key] = photo
        if debug:
            self._debug_photo_keys.add(key)
        widget.configure(image=photo, text=empty_text)

    def _render_debug(self) -> None:
        now = time.monotonic()
        if self._debug and self._frame is not None and self.debug_steps.winfo_children() and now - self._debug_last < 2:
            return
        self._debug_last = now if self._debug else 0.0
        for key in self._debug_photo_keys:
            self._photo_refs.pop(key, None)
        self._debug_photo_keys.clear()
        for child in self.debug_steps.winfo_children():
            child.destroy()
        if not self._debug:
            self.debug_meta.set("调试已关闭")
            return
        frame = self._frame
        if not frame:
            self.debug_meta.set("等待处理帧")
            return
        self.debug_meta.set(f"帧号 {frame.get('frame_id', _MISSING)}    来源 {frame.get('source', '来源未提供')}")
        try:
            gray_array = np.asarray(frame["gray"], dtype=np.uint8)
            mask_array = np.asarray(frame["mask"], dtype=np.uint8)
            if gray_array.shape != (240, 320) or mask_array.shape != (240, 320):
                raise ValueError("帧尺寸应为 320×240")
            gray = Image.fromarray(gray_array, mode="L").convert("RGB")
            mask = Image.fromarray(mask_array, mode="L").convert("RGB")
        except (KeyError, TypeError, ValueError):
            ttk.Label(self.debug_steps, text="此帧未提供可用的灰度图或二值图", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            return

        result = _mapping(frame.get("result"))
        config = _mapping(frame.get("config"))
        detections = result.get("detections")
        tracks = result.get("tracks")
        roi = roi_bounds(config)
        roi_mask = None
        if roi is not None:
            x1, y1, x2, y2 = roi
            if all(0 <= value <= 1 for value in roi):
                x1, x2, y1, y2 = x1 * 320, x2 * 320, y1 * 240, y2 * 240
            left, top = max(0, int(x1)), max(0, int(y1))
            right, bottom = min(320, int(x2)), min(240, int(y2))
            if right > left and bottom > top:
                roi_mask = np.zeros_like(mask_array)
                roi_mask[top:bottom, left:right] = mask_array[top:bottom, left:right]

        def annotate(base: Image.Image, items: Any, color: str, centers: bool = False) -> Image.Image | None:
            if not isinstance(items, list):
                return None
            canvas = base.copy()
            draw = ImageDraw.Draw(canvas)
            for item in items:
                bounds = _box_from_item(item)
                if bounds is not None:
                    draw.rectangle(self._scale_box(bounds), outline=color, width=2)
                center = self._center_from_item(item) if centers else None
                if center:
                    x, y = center
                    draw.ellipse((x - 4, y - 4, x + 4, y + 4), outline=color, width=2)
            return canvas

        area_overlay = annotate(gray, detections, "#00e0ff")
        tracking_overlay = annotate(gray, tracks, "#ffcc00", centers=True)
        roi_image = Image.fromarray(roi_mask, mode="L").convert("RGB") if roi_mask is not None else None
        steps = (
            ("1. 映射", None, gray, "映射前输入未随此帧提供", "已接收的 8 位灰度帧"),
            ("2. 阈值", gray, mask, "已接收的灰度输入", "同帧实际二值掩膜"),
            ("3. ROI", mask, roi_image, "同帧二值掩膜", "未提供有效 ROI 配置" if roi_image is None else "按配置 ROI 从同帧掩膜裁取"),
            ("4. 区域", roi_image if roi_image is not None else mask, area_overlay, "ROI 输出" if roi_image is not None else "同帧二值掩膜", "未提供区域检测数据" if area_overlay is None else "按同帧 detections 标出区域"),
            ("5. 跟踪", area_overlay, tracking_overlay, "未提供可用区域检测图" if area_overlay is None else "同帧区域标注", "未提供轨迹数据" if tracking_overlay is None else "按同帧 tracks 标出轨迹与质心"),
        )
        for row, (title, before, after, before_note, after_note) in enumerate(steps):
            card = ttk.Frame(self.debug_steps, style="Card.TFrame", padding=5)
            card.grid(row=row, column=0, sticky="ew", pady=3)
            card.columnconfigure(0, weight=1)
            card.columnconfigure(1, weight=1)
            ttk.Label(card, text=title, style="Card.TLabel", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
            for col, label_text, image, note in ((0, "处理前", before, before_note), (1, "处理后", after, after_note)):
                pane = ttk.Frame(card, style="Card.TFrame", padding=3)
                pane.grid(row=1, column=col, sticky="nsew", padx=2)
                ttk.Label(pane, text=label_text, style="Muted.TLabel").pack(anchor="w")
                if image is None:
                    ttk.Label(pane, text=f"不可用：{note}", style="Muted.TLabel", anchor="center", wraplength=300).pack(fill="x", pady=16)
                else:
                    image_label = ttk.Label(pane)
                    image_label.pack(fill="x")
                    self._set_image(image_label, image, "", debug=True)
                    ttk.Label(pane, text=note, style="Muted.TLabel", wraplength=300).pack(anchor="w")

