"""Native Qt view for the material counter; the controller owns all I/O."""
from __future__ import annotations

import time
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QMainWindow,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget)

from view_model import (_MISSING, _STATE_LABELS, _box_from_item, _fmt, _mapping,
                        _number, format_duration, frame_summary, recent_events,
                        resource_percent, roi_bounds)


class QtRootAdapter:
    """Give the existing controller Qt main-thread scheduling and safe close."""

    def __init__(self):
        self.window = None

    def bind(self, window):
        self.window = window

    def after(self, milliseconds, callback):
        QTimer.singleShot(milliseconds, callback)

    def destroy(self):
        if self.window is not None:
            self.window.allow_close = True
            self.window.close()


class ImageView(QLabel):
    """QLabel that retains an owned source pixmap while scaling to fit."""

    def __init__(self, placeholder, name):
        super().__init__(placeholder)
        self.setObjectName(name)
        self.setProperty("fluentRole", "videoSurface")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(160, 120)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(placeholder)
        self._source = None

    def set_image(self, image):
        if image is None:
            self._source = None
            self.clear()
            return
        rgb = np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8))
        height, width = rgb.shape[:2]
        owned = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self._source = QPixmap.fromImage(owned)
        self._rescale()

    def _rescale(self):
        if self._source is not None:
            target = self.size()
            if target.width() < 40 or target.height() < 40:
                target = self._source.size()
            self.setPixmap(self._source.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.FastTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()


def _label(text="", role=None):
    result = QLabel(text)
    if role:
        result.setObjectName(role)
    return result


def _button(text, callback, *, primary=False, name=None):
    result = QPushButton(text)
    if name:
        result.setObjectName(name)
    result.setAccessibleName(text)
    result.setToolTip(text)
    if primary:
        result.setProperty("fluentAppearance", "primary")
    result.clicked.connect(callback)
    return result


def _section(title):
    box = QFrame()
    box.setProperty("fluentRole", "card")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    title_label = _label(title, "sectionTitle")
    layout.addWidget(title_label)
    return box, layout


class DesktopWindow(QMainWindow):
    """Render only completed frame packets and retain existing controller actions."""

    def __init__(self, controller, theme=None):
        super().__init__()
        self.controller = controller
        self.theme = theme
        self.closing = False
        self.allow_close = False
        self._paused = False
        self._debug = False
        self._debug_last = 0.0
        self._resource_enabled = False
        self._resource_mode = "normal"
        self._view_mode = "overlay"
        self._frame = None
        self._status = {}
        self.current_frame_id = None
        self._camera_controls_loaded = False
        self.setWindowTitle("FPGA 物料计数控制看板")
        self.setMinimumSize(720, 600)
        self.resize(1280, 880)
        self._build_ui()
        self.show_status({})

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.setCentralWidget(scroll)
        body = QWidget()
        body.setObjectName("mainContent")
        scroll.setWidget(body)
        page = QVBoxLayout(body)
        page.setContentsMargins(24, 20, 24, 20)
        page.setSpacing(14)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(3)
        heading.addWidget(_label("物料计数控制看板", "pageTitle"))
        heading.addWidget(_label("PYNQ-Z2 视觉计数系统", "mutedText"))
        header.addLayout(heading, 1)
        self.connection_label = _label("状态接口未连接", "deviceStatus")
        header.addWidget(self.connection_label)
        self.theme_mode = QComboBox()
        self.theme_mode.setObjectName("themeMode")
        self.theme_mode.setAccessibleName("界面主题")
        for label, value in (("跟随系统", "system"), ("浅色", "light"),
                             ("深色", "dark"), ("高对比度", "high-contrast")):
            self.theme_mode.addItem(label, value)
        self.theme_mode.currentIndexChanged.connect(self._set_theme)
        header.addWidget(self.theme_mode)
        page.addLayout(header)

        devices = QHBoxLayout()
        devices.setSpacing(20)
        self.devices = {}
        for key, name in (("camera", "相机"), ("fpga", "FPGA"), ("dma", "DMA"), ("system", "系统")):
            label = _label(f"{name}：未连接", "deviceStatus")
            label.setAccessibleName(name + "状态")
            self.devices[key] = label
            devices.addWidget(label)
        devices.addStretch(1)
        page.addLayout(devices)

        main = QSplitter(Qt.Orientation.Horizontal)
        main.setChildrenCollapsible(False)
        visuals, visuals_layout = _section("相机画面与跟踪")
        meta = QHBoxLayout()
        self.preview_meta = _label("画面 -- · 预览 -- FPS", "mutedText")
        self.source_label = _label("图像来源未确认", "mutedText")
        meta.addWidget(self.source_label, 1)
        meta.addWidget(self.preview_meta)
        visuals_layout.addLayout(meta)
        images = QHBoxLayout()
        images.setSpacing(12)
        for title, name in (("相机画面", "previewImage"), ("二值化结果", "binaryImage")):
            panel = QVBoxLayout()
            panel.addWidget(_label(title, "mutedText"))
            image = ImageView("等待预览画面" if name == "previewImage" else "等待二值化画面", name)
            panel.addWidget(image, 1)
            images.addLayout(panel, 1)
            if name == "previewImage":
                self.preview_image = image
            else:
                self.binary_image = image
        visuals_layout.addLayout(images, 1)
        preview_actions = QHBoxLayout()
        preview_actions.addWidget(_label("画面显示", "mutedText"))
        self.view_mode = QComboBox()
        self.view_mode.setAccessibleName("画面显示模式")
        self.view_mode.addItem("原图与叠加层", "overlay")
        self.view_mode.addItem("Binary8 二值图", "binary")
        self.view_mode.currentIndexChanged.connect(self._set_view_mode)
        preview_actions.addWidget(self.view_mode)
        preview_actions.addStretch(1)
        self.pause_button = _button("暂停预览", self._toggle_pause, name="pausePreview")
        preview_actions.addWidget(self.pause_button)
        visuals_layout.addLayout(preview_actions)
        main.addWidget(visuals)

        summary, summary_layout = _section("累计计数")
        summary.setMinimumWidth(250)
        self.count_value = _label("--", "heroCount")
        self.count_value.setAccessibleName("累计计数")
        summary_layout.addWidget(self.count_value)
        self.count_note = _label("等待真实计数数据", "mutedText")
        summary_layout.addWidget(self.count_note)
        metrics = QGridLayout()
        metrics.setHorizontalSpacing(18)
        metrics.setVerticalSpacing(8)
        self.metric_values = {}
        for i, (key, name) in enumerate((("speed", "输送速度"), ("area", "最近越线面积"),
                ("detections", "当前帧区域数"), ("fps", "实际处理帧率"),
                ("elapsed", "运行时长"), ("phase", "当前状态"))):
            name_label = _label(name, "mutedText")
            value = _label("--", "metricValue")
            value.setAccessibleName(name)
            self.metric_values[key] = value
            metrics.addWidget(name_label, (i // 2) * 2, i % 2)
            metrics.addWidget(value, (i // 2) * 2 + 1, i % 2)
        summary_layout.addLayout(metrics)
        summary_layout.addStretch(1)
        self.start_button = _button("开始采集", lambda: self._invoke("start"), primary=True, name="startCapture")
        self.stop_button = _button("停止监控", lambda: self._invoke("stop"), name="stopMonitor")
        self.reset_button = _button("计数清零", lambda: self._invoke("reset"), name="resetCount")
        actions = QHBoxLayout()
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.reset_button)
        summary_layout.addLayout(actions)
        self.update_button = _button("更新后端 Python", lambda: self._invoke("update_backend"), name="updateBackend")
        summary_layout.addWidget(self.update_button)
        self.update_status = _label("启动采集后可更新后端", "mutedText")
        self.update_status.setWordWrap(True)
        summary_layout.addWidget(self.update_status)
        main.addWidget(summary)
        main.setStretchFactor(0, 3)
        main.setStretchFactor(1, 1)
        main.setSizes([850, 300])
        page.addWidget(main, 1)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("detailTabs")
        self._build_events()
        self._build_debug()
        self._build_resources()
        self._build_calibration()
        page.addWidget(self.tabs)
        self.feedback = _label("准备就绪", "mutedText")
        self.feedback.setWordWrap(True)
        self.feedback.setAccessibleName("操作反馈")
        page.addWidget(self.feedback)
        self.statusBar().showMessage("就绪")

    def _build_events(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(_label("最近 30 条系统事件", "sectionTitle"))
        self.events = QListWidget()
        self.events.setObjectName("eventsList")
        self.events.setAccessibleName("最近系统事件")
        self.events.setMinimumHeight(130)
        layout.addWidget(self.events)
        self.tabs.addTab(tab, "系统事件")

    def _build_debug(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.debug_button = _button("开启调试", self._toggle_debug, name="toggleDebug")
        row.addWidget(self.debug_button)
        self.debug_status = _label("连接服务后可开启", "mutedText")
        row.addWidget(self.debug_status, 1)
        self.debug_meta = _label("帧号 -- · 来源 --", "mutedText")
        row.addWidget(self.debug_meta)
        layout.addLayout(row)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setMinimumHeight(220)
        area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setSpacing(12)
        self.debug_steps = []
        for index, title in enumerate(("1. 图像映射", "2. 阈值分割", "3. ROI 选择", "4. 区域分析", "5. 跟踪与计数")):
            row_widget = QGroupBox(title)
            row_layout = QHBoxLayout(row_widget)
            pair = []
            for side in ("处理前", "处理后"):
                col = QVBoxLayout()
                col.addWidget(_label(side, "mutedText"))
                picture = ImageView("此步骤未提供画面", f"debug{index}{side}")
                picture.setMinimumSize(160, 120)
                col.addWidget(picture)
                note = _label("不可用", "mutedText")
                note.setWordWrap(True)
                col.addWidget(note)
                row_layout.addLayout(col, 1)
                pair.append((picture, note))
            grid.addWidget(row_widget, index, 0)
            self.debug_steps.append(pair)
        area.setWidget(content)
        self.debug_area = area
        layout.addWidget(area)
        self.tabs.addTab(tab, "图像处理调试")

    def _build_resources(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        bar = QHBoxLayout()
        self.resource_button = _button("开启监视", self._toggle_resources, name="resourceToggle")
        bar.addWidget(self.resource_button)
        self.resource_mode = QComboBox()
        self.resource_mode.setAccessibleName("板端资源监视显示模式")
        self.resource_mode.addItem("正常模式", "normal")
        self.resource_mode.addItem("详细模式", "detail")
        self.resource_mode.currentIndexChanged.connect(self._set_resource_mode)
        bar.addWidget(self.resource_mode)
        self.resource_status = _label("资源监视不可用", "mutedText")
        bar.addWidget(self.resource_status, 1)
        layout.addLayout(bar)
        grid = QGridLayout()
        self.resource_values = {}
        for index, (key, title) in enumerate((("cpu_percent", "CPU 总体"), ("memory", "系统内存"),
                ("cma", "CMA 内存池"), ("disk", "MicroSD 文件系统"),
                ("rx", "以太网接收"), ("tx", "以太网发送"))):
            grid.addWidget(_label(title, "mutedText"), 0, index)
            value = _label("--", "metricValue")
            value.setAccessibleName(title)
            self.resource_values[key] = value
            grid.addWidget(value, 1, index)
        layout.addLayout(grid)
        self.resource_details = QTextEdit()
        self.resource_details.setReadOnly(True)
        self.resource_details.setAccessibleName("FPGA 位流资源与运行状态")
        self.resource_details.setPlaceholderText("详细模式显示 FPGA 位流资源与运行状态")
        layout.addWidget(self.resource_details)
        self.tabs.addTab(tab, "板端资源监视")

    def _build_calibration(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(_label("相机标定", "sectionTitle"))
        form = QFormLayout()
        self.exposure_input = QSpinBox()
        self.exposure_input.setRange(50, 10000)
        self.exposure_input.setAccessibleName("曝光时间")
        self.brightness_input = QSpinBox()
        self.brightness_input.setRange(-64, 64)
        self.brightness_input.setAccessibleName("亮度")
        form.addRow("曝光时间", self.exposure_input)
        form.addRow("亮度", self.brightness_input)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.apply_button = _button("应用参数", self._apply_calibration, primary=True)
        self.restore_button = _button("恢复基准参数", lambda: self._invoke_value("calibrate", {"reset": True}))
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.restore_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(_label("参数仅在后端提供标定能力时启用。", "mutedText"))
        layout.addStretch(1)
        self.tabs.addTab(tab, "相机标定")

    def _set_theme(self, _index):
        if self.theme is not None:
            self.theme.apply(self.theme_mode.currentData())

    def _invoke(self, name):
        self._invoke_value(name)

    def _invoke_value(self, name, *args):
        try:
            getattr(self.controller, name)(*args)
            self.show_message("操作已提交")
        except Exception as exc:
            self.show_message(f"操作失败：{exc}")

    def _toggle_pause(self):
        self._paused = not self._paused
        self.pause_button.setText("恢复预览" if self._paused else "暂停预览")
        self._invoke_value("set_preview_paused", self._paused)
        if self._paused:
            self.source_label.setText("预览已暂停 · 画面冻结")
        elif self._frame:
            self._render_frame()

    def _set_view_mode(self, _index):
        self._view_mode = self.view_mode.currentData()
        if self._frame and not self._paused:
            self._render_frame()

    def _toggle_debug(self):
        self._debug = not self._debug
        self._invoke_value("set_debug", self._debug)
        self._render_debug(force=True)

    def _toggle_resources(self):
        self._resource_enabled = not self._resource_enabled
        self._invoke_value("set_resource", self._resource_enabled, self._resource_mode)

    def _set_resource_mode(self, _index):
        self._resource_mode = self.resource_mode.currentData()
        self._invoke_value("set_resource", self._resource_enabled, self._resource_mode)

    def _apply_calibration(self):
        self._invoke_value("calibrate", {"exposure_time_absolute": self.exposure_input.value(),
                                         "brightness": self.brightness_input.value()})

    def show_message(self, message):
        self.feedback.setText(str(message))
        self.statusBar().showMessage(str(message))

    def show_status(self, data):
        self._status = _mapping(data)
        telemetry = _mapping(data.get("telemetry"))
        preview = _mapping(data.get("preview"))
        caps = _mapping(data.get("capabilities"))
        connected = bool(data) and data.get("connected") is not False and data.get("online") is not False
        self.connection_label.setText("状态接口已连接" if connected else "状态接口未连接")
        phase = str(telemetry.get("phase", "unknown"))
        for key, label in (("camera", "相机"), ("fpga", "FPGA"), ("dma", "DMA")):
            state = str(telemetry.get(key, "unknown"))
            self.devices[key].setText(f"{label}：{_STATE_LABELS.get(state, state)}")
        shown = "stale" if phase == "running" and telemetry.get("stale") is True else phase
        self.devices["system"].setText("系统：" + _STATE_LABELS.get(shown, "状态未提供"))
        self.metric_values["phase"].setText(_STATE_LABELS.get(shown, "状态未提供"))
        fresh = (telemetry.get("stale") is False and _number(telemetry.get("last_frame_age_seconds")) is not None
                 and telemetry.get("last_frame_age_seconds", 999) <= 5 and phase == "running")
        final = phase in ("complete", "stopped")
        if self._frame is None or self._paused:
            count = telemetry.get("total_count")
            valid = telemetry.get("source") in ("software", "pl_dma", "pl_rle") and (fresh or final)
            self.count_value.setText(str(count) if valid and isinstance(count, int) else _MISSING)
            regions = telemetry.get("detections")
            self.metric_values["detections"].setText(str(regions) if fresh and isinstance(regions, int) else _MISSING)
        self.count_note.setText("本次会话最终结果" if final else "已同步最新完成帧" if fresh else "尚无已处理帧")
        speed = telemetry.get("cm_per_second")
        px_speed = telemetry.get("px_per_frame")
        self.metric_values["speed"].setText(f"{_fmt(speed)} cm/s（估算）" if fresh and _number(speed) is not None
            else f"{_fmt(px_speed)} px/帧" if fresh and _number(px_speed) is not None else _MISSING)
        area = telemetry.get("last_crossing_area_px")
        self.metric_values["area"].setText(f"{area} px" if isinstance(area, int) and (fresh or final) else _MISSING)
        self.metric_values["fps"].setText(_fmt(telemetry.get("processing_fps")) if fresh else _MISSING)
        self.metric_values["elapsed"].setText(format_duration(telemetry.get("elapsed_seconds")))
        width, height = preview.get("width"), preview.get("height")
        dimensions = f"{width} × {height}" if isinstance(width, int) and isinstance(height, int) else "--"
        self.preview_meta.setText(f"画面 {dimensions} · 预览 {_fmt(preview.get('fps'))} FPS")
        if self._paused:
            self.source_label.setText("预览已暂停 · 画面冻结")
        elif self._frame is None:
            self.source_label.setText(str(telemetry.get("source", "图像来源未确认")))

        self.events.clear()
        entries = recent_events(telemetry.get("events"))
        for entry in entries:
            self.events.addItem(str(entry.get("message", ""))[:500])
        if not entries:
            self.events.addItem("尚无服务端状态事件")
        for button, key in ((self.start_button, "start"), (self.stop_button, "stop"),
                            (self.reset_button, "reset"), (self.view_mode, "view_mode"),
                            (self.update_button, "update_backend")):
            button.setEnabled((connected or button is self.start_button) and not self.closing and caps.get(key) is True)
        self.update_status.setText("更新时将安全停止本次采集并重新部署" if caps.get("update_backend") is True else "启动采集后可更新后端")
        self.pause_button.setEnabled(not self.closing)
        calibration_enabled = connected and not self.closing and caps.get("calibration") is True
        for control in (self.exposure_input, self.brightness_input, self.apply_button, self.restore_button):
            control.setEnabled(calibration_enabled)
        controls = _mapping(data.get("camera_controls"))
        if not self._camera_controls_loaded and isinstance(controls.get("exposure_time_absolute"), int) and isinstance(controls.get("brightness"), int):
            self.exposure_input.setValue(controls["exposure_time_absolute"])
            self.brightness_input.setValue(controls["brightness"])
            self._camera_controls_loaded = True
        debug = _mapping(data.get("debug"))
        self._debug = debug.get("enabled") is True
        self.debug_button.setEnabled(connected and isinstance(debug.get("enabled"), bool) and not self.closing)
        self.debug_button.setText("关闭调试" if self._debug else "开启调试")
        self.debug_status.setText("调试已开启" if self._debug else "调试已关闭" if debug else "服务端未提供调试状态")
        self._update_resources(_mapping(data.get("resource_monitor")), data.get("resource_monitor") is not None)
        self._render_debug()

    def _update_resources(self, resource, available):
        metrics = _mapping(resource.get("metrics"))
        self._resource_enabled = resource.get("enabled") is True
        self._resource_mode = resource.get("mode") if resource.get("mode") in ("normal", "detail") else "normal"
        self.resource_button.setEnabled(available and not self.closing)
        self.resource_button.setText("关闭监视" if self._resource_enabled else "开启监视")
        self.resource_mode.setEnabled(available and not self.closing)
        self.resource_mode.blockSignals(True)
        self.resource_mode.setCurrentIndex(1 if self._resource_mode == "detail" else 0)
        self.resource_mode.blockSignals(False)
        self.resource_status.setText("正在采样" if self._resource_enabled else "采样已停止" if available else "资源监视不可用")
        for label in self.resource_values.values():
            label.setText(_MISSING)
        self.resource_details.clear()
        if not available or not self._resource_enabled:
            return
        cpu = resource_percent(metrics.get("cpu_percent"))
        self.resource_values["cpu_percent"].setText(f"{cpu:.1f}%" if cpu is not None else _MISSING)
        for key, total_key, part_key in (("memory", "memory_total_kib", "memory_available_kib"),
                ("cma", "cma_total_kib", "cma_free_kib"), ("disk", "rootfs_total_bytes", "rootfs_used_bytes")):
            total, part = _number(metrics.get(total_key)), _number(metrics.get(part_key))
            if total is not None and total > 0 and part is not None:
                used = part if key == "disk" else total - part
                if 0 <= used <= total:
                    self.resource_values[key].setText(f"{used * 100 / total:.1f}%")
        for key, field in (("rx", "network_rx_bytes_per_second"), ("tx", "network_tx_bytes_per_second")):
            value = _number(metrics.get(field))
            if value is not None:
                self.resource_values[key].setText(f"{value * 8 / 1_000_000:.2f} Mbps")
        if self._resource_mode == "detail":
            detail = _mapping(resource.get("detail"))
            fpga = _mapping(detail.get("fpga"))
            lines = ["FPGA 位流资源（静态；仅显示已核验数据）"]
            if fpga.get("source"):
                lines.append(f"数据来源：{fpga['source']}")
            if fpga.get("protocol"):
                lines.append(f"处理协议：{fpga['protocol']}")
            resolution = fpga.get("resolution")
            if isinstance(resolution, (tuple, list)) and len(resolution) == 2:
                lines.append(f"处理尺寸：{resolution[0]} × {resolution[1]}")
            for key, label in (("lut", "LUT"), ("ff", "FF 寄存器"), ("bram", "BRAM Tile"),
                    ("dsp", "DSP"), ("lutram", "LUT 存储"), ("iob", "PL I/O"),
                    ("bufg", "BUFG 时钟"), ("mmcm", "MMCM"), ("pll", "PLL")):
                values = fpga.get(key)
                if isinstance(values, (tuple, list)) and len(values) >= 2 and _number(values[0]) is not None and _number(values[1]) and values[1] > 0:
                    lines.append(f"{label}：{values[0]} / {values[1]} · {values[0] * 100 / values[1]:.2f}%")
                else:
                    lines.append(f"{label}：未提供")
            if fpga.get("timing"):
                lines.append(f"时序：{fpga['timing']}")
            lines += [f"相机：{detail.get('camera', '未提供')}",
                      f"DMA：{detail.get('dma_status', '未提供')}",
                      "DDR 带宽、流水线负载与供电：未接入"]
            self.resource_details.setPlainText("\n".join(lines))

    def show_frame(self, frame):
        if not isinstance(frame, dict) or self._paused:
            return
        self._frame = frame
        self.current_frame_id = frame.get("frame_id")
        count, regions = frame_summary(frame.get("result"))
        self.count_value.setText(str(count) if count is not None else _MISSING)
        self.metric_values["detections"].setText(str(regions) if regions is not None else _MISSING)
        self._render_frame()
        self._render_debug()

    @staticmethod
    def _scale_box(box, width=320, height=240):
        x1, y1, x2, y2 = box
        if all(-1 <= value <= 1 for value in box):
            x1, x2, y1, y2 = x1 * width, x2 * width, y1 * height, y2 * height
        return tuple(int(round(value)) for value in (
            max(0, min(width - 1, x1)), max(0, min(height - 1, y1)),
            max(0, min(width - 1, x2)), max(0, min(height - 1, y2))))

    @staticmethod
    def _center_from_item(item, width=320, height=240):
        item = _mapping(item)
        nested = _mapping(item.get("detection"))
        x = _number(item.get("centroid_x", item.get("cx", item.get("center_x", nested.get("centroid_x")))))
        y = _number(item.get("centroid_y", item.get("cy", item.get("center_y", nested.get("centroid_y")))))
        if x is None or y is None:
            return None
        if 0 <= x <= 1 and 0 <= y <= 1:
            x, y = x * width, y * height
        return int(max(0, min(width - 1, x))), int(max(0, min(height - 1, y)))

    @staticmethod
    def _draw_config_line(draw, config, width=320, height=240):
        if config.get("count_axis") == "x":
            x = _number(config.get("line_x"))
            if x is not None:
                x = int(x * width if 0 <= x <= 1 else x)
                draw.line((x, 0, x, height - 1), fill="#ff4d4d", width=2)
            return
        line = config.get("count_line", config.get("counting_line"))
        if isinstance(line, (list, tuple)) and len(line) >= 4:
            coords = [_number(value) for value in line[:4]]
            if all(value is not None for value in coords):
                if all(0 <= value <= 1 for value in coords):
                    coords = [coords[0] * width, coords[1] * height,
                              coords[2] * width, coords[3] * height]
                draw.line(tuple(int(value) for value in coords), fill="#ff4d4d", width=2)
                return
        y = _number(config.get("line_y", config.get("count_line_y")))
        if y is not None:
            y = int(y * height if 0 <= y <= 1 else y)
            draw.line((0, y, width - 1, y), fill="#ff4d4d", width=2)

    def _annotate(self, base, result, config, tracks=True):
        image = base.copy()
        draw = ImageDraw.Draw(image)
        width, height = image.size
        self._draw_config_line(draw, config, width, height)
        for key, color in (("detections", "#00e0ff"), ("tracks", "#ffcc00")):
            if key == "tracks" and not tracks:
                continue
            for item in result.get(key, []) if isinstance(result.get(key), list) else ():
                box = _box_from_item(item)
                if box:
                    bounds = self._scale_box(box, width, height)
                    draw.rectangle(bounds, outline=color, width=2)
                    track_id = _mapping(item).get("track_id")
                    if track_id is not None:
                        draw.text((bounds[0] + 2, max(0, bounds[1] - 13)), str(track_id), fill=color)
                else:
                    center = self._center_from_item(item, width, height)
                    if center:
                        x, y = center
                        draw.ellipse((x - 4, y - 4, x + 4, y + 4), outline=color, width=2)
        return image

    def _images(self):
        gray, mask = self._frame["gray"], self._frame["mask"]
        if (not isinstance(gray, np.ndarray) or not isinstance(mask, np.ndarray)
                or gray.ndim != 2 or mask.ndim != 2 or gray.dtype != np.uint8
                or mask.dtype != np.uint8 or gray.shape != mask.shape):
            raise ValueError("帧应包含尺寸一致的二维 uint8 灰度图和二值图")
        return Image.fromarray(gray).convert("RGB"), Image.fromarray(mask).convert("RGB")

    def _render_frame(self):
        try:
            gray, mask = self._images()
            overlay = self._annotate(gray, _mapping(self._frame.get("result")), _mapping(self._frame.get("config")))
            self.preview_image.set_image(mask if self._view_mode == "binary" else overlay)
            self.binary_image.set_image(mask)
            self.source_label.setText(f"帧 {self.current_frame_id} · {self._frame.get('source', '来源未提供')}")
        except (KeyError, TypeError, ValueError) as exc:
            self.show_message(f"帧不可用：{exc}")

    def _render_debug(self, force=False):
        self.debug_area.setVisible(self._debug)
        if not self._debug:
            self.debug_meta.setText("调试已关闭")
            return
        if self._frame is None:
            self.debug_meta.setText("等待处理帧")
            return
        now = time.monotonic()
        if not force and now - self._debug_last < 2:
            return
        self._debug_last = now
        self.debug_meta.setText(f"帧号 {self.current_frame_id} · 来源 {self._frame.get('source', '--')}")
        try:
            gray, mask = self._images()
            result, config = _mapping(self._frame.get("result")), _mapping(self._frame.get("config"))
            region = roi_bounds(config)
            roi_image = None
            if region is not None:
                x1, y1, x2, y2 = (int(v) for v in region)
                source = np.asarray(mask.convert("L"))
                height, width = source.shape
                roi_array = np.zeros((height, width), np.uint8)
                roi_array[max(0, y1):min(height, y2), max(0, x1):min(width, x2)] = (
                    source[max(0, y1):min(height, y2), max(0, x1):min(width, x2)])
                roi_image = Image.fromarray(roi_array).convert("RGB")
            areas = self._annotate(gray, result, config, tracks=False) if isinstance(result.get("detections"), list) else None
            tracked = self._annotate(gray, result, config) if isinstance(result.get("tracks"), list) else None
            steps = ((None, gray), (gray, mask), (mask, roi_image), (roi_image or mask, areas), (areas, tracked))
            for pair, images in zip(self.debug_steps, steps):
                for (picture, note), image in zip(pair, images):
                    picture.set_image(image)
                    if image is None:
                        picture.setText("此步骤未提供画面")
                        note.setText("不可用：原始数据未提供")
                    else:
                        note.setText("同帧实际数据与本机标注")
        except (KeyError, TypeError, ValueError) as exc:
            self.debug_status.setText(f"调试数据不可用：{exc}")

    def closeEvent(self, event: QCloseEvent):
        if self.allow_close:
            event.accept()
            return
        event.ignore()
        if self.closing:
            return
        self.closing = True
        for button in (self.start_button, self.stop_button, self.reset_button, self.update_button,
                       self.pause_button, self.debug_button, self.resource_button):
            button.setEnabled(False)
        try:
            if self.controller.close() is False:
                raise RuntimeError("控制器未能启动关闭流程")
            self.show_message("正在停止任务并关闭窗口…")
        except Exception as exc:
            self.closing = False
            self.show_status(self._status)
            self.show_message(f"关闭失败：{exc}")
