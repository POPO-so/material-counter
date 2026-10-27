#!/usr/bin/env python3
"""Standalone lighting aid. Importing this module never opens hardware.

Run --demo for offline UI review. Real acquisition and V4L2 writes require
separate explicit command-line opt-ins. See live_lighting_tuner.md.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
import uuid

import cv2
import numpy as np

_WO16_DIR = Path(__file__).resolve().parent / 'wo0016'
if str(_WO16_DIR) not in sys.path:
    sys.path.insert(0, str(_WO16_DIR))
from live_calibration import (monochrome_view, read_first_monochrome_frame,
                              request_native_monochrome)

DEFAULT_PARAMS = dict(view_mode='assist', binary_thresh=100, overexp_thresh=255,
                      roi_top=20, roi_bottom=380, min_area=60,
                      exposure=50, gamma=100, gain=0)
HARDWARE_KEYS = {'exposure', 'gamma', 'gain'}


def analyze_frame(frame, params):
    """Return metrics and BGR view; percentages always use the full ROI.

    Holes are threshold topology hints, not a diagnosis of shadow or part type.
    Unavailable populations stay null rather than borrowing another zone's mean.
    """
    if frame.ndim == 2:
        gray = frame
    elif frame.ndim == 3 and frame.shape[2] in (1, 3, 4):
        gray = frame[:, :, 0]
    else:
        raise ValueError('Expected a uint8 grayscale or monochrome-channel frame')
    if gray.dtype != np.uint8 or gray.ndim != 2 or min(gray.shape) < 3:
        raise ValueError('Expected a uint8 grayscale or BGR frame at least 3x3')
    h, w = gray.shape
    top, bottom = params['roi_top'], params['roi_bottom']
    if not 0 <= top < bottom <= h:
        raise ValueError('ROI is outside the actual frame')
    roi = gray[top:bottom]
    fg = (roi > params['binary_thresh']).astype(np.uint8) * 255
    bg = cv2.erode(255 - fg, np.ones((5, 5), np.uint8)) > 0
    fg_pixels, bg_pixels = roi[fg > 0], roi[bg]

    def mean(pixels):
        return float(np.mean(pixels)) if pixels.size else None

    fg_mean, bg_mean = mean(fg_pixels), mean(bg_pixels)
    zones = [mean(roi[:, indices][bg[:, indices]]) for indices in np.array_split(np.arange(w), 3)]
    uniformity = max(zones) - min(zones) if all(x is not None for x in zones) else None
    overexp = float(np.count_nonzero(roi >= params['overexp_thresh']) / roi.size * 100)
    contours, hierarchy = cv2.findContours(fg, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if params['view_mode'] == 'binary':
        vis[top:bottom] = cv2.cvtColor(fg, cv2.COLOR_GRAY2BGR)
    parts = []
    if hierarchy is not None:
        for index, contour in enumerate(contours):
            if hierarchy[0][index][3] != -1:
                continue
            area = cv2.contourArea(contour)
            child, hole_area = int(hierarchy[0][index][2]), 0.0
            mask = np.zeros_like(fg)
            cv2.drawContours(mask, contours, index, 255, -1)
            while child != -1:
                child_area = cv2.contourArea(contours[child])
                hole_area += child_area
                cv2.drawContours(mask, contours, child, 0, -1)
                child = int(hierarchy[0][child][0])
            net_area = max(0.0, area - hole_area)
            if net_area < params['min_area']:
                continue
            x, y, bw, bh = cv2.boundingRect(contour)
            part_pixels = roi[(mask > 0) & (fg > 0)]
            has_hole = hole_area >= 12
            part = dict(x=x, y=y + top, width=bw, height=bh, area=net_area,
                        hole=has_hole, hole_area=hole_area, fg=mean(part_pixels),
                        touches_roi_edge=x == 0 or y == 0 or x + bw == w or y + bh == bottom - top)
            parts.append(part)
            if params['view_mode'] in ('assist', 'grid'):
                color = (0, 230, 118) if has_hole else (0, 165, 255)
                cv2.rectangle(vis, (x, y + top), (x + bw - 1, y + top + bh - 1), color, 1)
                label = 'A:%.0f FG:%.0f %s' % (net_area, part['fg'] or 0, 'Hole' if has_hole else 'NoHole')
                cv2.putText(vis, label, (max(0, x - 10), max(12, y + top - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, .35, color, 1, cv2.LINE_AA)
    if params['view_mode'] == 'grid':
        for edge in (w // 3, 2 * w // 3):
            cv2.line(vis, (edge, top), (edge, bottom - 1), (255, 200, 0), 1)
    if params['view_mode'] != 'raw':
        cv2.line(vis, (0, top), (w - 1, top), (100, 100, 255), 1)
        cv2.line(vis, (0, bottom - 1), (w - 1, bottom - 1), (100, 100, 255), 1)
    if params['view_mode'] == 'assist':
        # Highlight the whole image, while numeric metrics retain the selected ROI.
        vis[gray >= params['overexp_thresh']] = (0, 0, 255)

    def band(value, low, high):
        if value is None:
            return 'UNAVAILABLE'
        return 'PASS' if low <= value <= high else ('LOW' if value < low else 'HIGH')

    statuses = dict(status_bg=band(bg_mean, 20, 45),
                    status_fg=band(fg_mean, 160, 235) if parts else 'NO_PART',
                    status_overexp='PASS' if overexp <= .5 else 'OVEREXP',
                    status_uniform='UNAVAILABLE' if uniformity is None else ('PASS' if uniformity <= 10 else 'UNEVEN'))
    metrics = dict(bg_mean=bg_mean, bg_std=float(np.std(bg_pixels)) if bg_pixels.size else None,
                   fg_mean=fg_mean, fg_max=int(fg_pixels.max()) if fg_pixels.size else None,
                   contrast=fg_mean - bg_mean if fg_mean is not None and bg_mean is not None else None,
                   overexp_pct=overexp, overexp_denominator='roi_pixels', roi_pixels=int(roi.size),
                   foreground_pixels=int(fg_pixels.size), background_pixels=int(bg_pixels.size),
                   left_bg=zones[0], center_bg=zones[1], right_bg=zones[2],
                   uniformity_diff=uniformity, part_count=len(parts), parts=parts,
                   all_pass=all(value == 'PASS' for value in statuses.values()), **statuses)
    return metrics, vis


class V4L2Controls:
    """Bounded commands with return-code checks, exact readback and restoration."""
    names = dict(exposure='exposure_time_absolute', gamma='gamma', gain='gain')

    def __init__(self, device):
        self.device = device
        self.saved = {}

    def run(self, option):
        result = subprocess.run(['v4l2-ctl', '-d', self.device, option],
                                capture_output=True, text=True, timeout=3, check=True)
        return result.stdout

    def read(self, name):
        return int(self.run('--get-ctrl=' + name).strip().split(':', 1)[1].split()[0])

    def write(self, name, value):
        if name not in self.saved:
            self.saved[name] = self.read(name)
        self.run('--set-ctrl=%s=%d' % (name, value))
        if self.read(name) != value:
            raise RuntimeError('V4L2 readback mismatch: ' + name)

    def apply(self, updates):
        if 'exposure' in updates:
            self.write('auto_exposure', 1)
        for key, value in updates.items():
            self.write(self.names[key], value)
        return {key: self.read(self.names[key]) for key in updates}

    def restore(self):
        errors = []
        # Restore exposure while still in manual mode; restore auto mode last.
        for name in sorted(self.saved, key=lambda name: name == 'auto_exposure'):
            try:
                self.run('--set-ctrl=%s=%d' % (name, self.saved[name]))
                if self.read(name) != self.saved[name]:
                    raise RuntimeError('readback mismatch')
            except Exception as error:
                errors.append('%s: %s' % (name, error))
        return errors


class LightingTunerState:
    def __init__(self, device='/dev/video0', width=640, height=400, fps=60,
                 demo=False, allow_camera_controls=False, observe_camera_controls=False,
                 exposure_buttons=False):
        self.device, self.width, self.height, self.fps = device, width, height, fps
        self.demo = demo
        self.lock = threading.RLock()
        self.io_lock = threading.RLock()
        self.stop = threading.Event()
        self.params = dict(DEFAULT_PARAMS, roi_top=min(20, height - 1), roi_bottom=min(380, height))
        self.controls = V4L2Controls(device) if allow_camera_controls else None
        self.hardware_applied = None
        self.hardware_error = None
        self.latest_jpeg = None
        self.record = None
        self.sequence = 0
        self.source = 'DEMO' if demo else 'WAITING'
        self.error = None
        self.last_frame_time = None
        self.observe_camera_controls = observe_camera_controls and not demo
        self.exposure_buttons = exposure_buttons and not demo
        self.camera_readback = None
        self.camera_readback_at = None
        self.camera_readback_error = None

    def refresh_camera_readback(self):
        """Read controls separately from frame processing; never write camera settings."""
        if not self.observe_camera_controls:
            return
        try:
            names = ('auto_exposure', 'exposure_time_absolute', 'gain', 'gamma')
            result = subprocess.run(['v4l2-ctl', '-d', self.device, '--get-ctrl=' + ','.join(names)],
                                    capture_output=True, text=True, timeout=2, check=True)
            values = {}
            for name in names:
                match = re.search(r'^' + name + r':\s*(-?\d+)\b', result.stdout, re.MULTILINE)
                if match is None:
                    raise ValueError('Missing camera readback: ' + name)
                values[name] = int(match.group(1))
            values['exposure_ms'] = values['exposure_time_absolute'] / 10
            with self.lock:
                self.camera_readback = values
                self.camera_readback_at = time.monotonic()
                self.camera_readback_error = None
        except Exception as error:
            with self.lock:
                self.camera_readback = None
                self.camera_readback_at = None
                self.camera_readback_error = str(error)

    def update_controls(self, updates):
        if not isinstance(updates, dict) or not updates or set(updates) - set(DEFAULT_PARAMS):
            raise ValueError('Expected a nonempty object containing known controls')
        limits = dict(binary_thresh=(0, 255), overexp_thresh=(1, 255), roi_top=(0, self.height - 1),
                      roi_bottom=(1, self.height), min_area=(1, self.width * self.height),
                      exposure=(1, 10000), gamma=(1, 500), gain=(0, 255))
        with self.io_lock, self.lock:
            candidate = dict(self.params, **updates)
            if candidate['view_mode'] not in ('assist', 'grid', 'raw', 'binary'):
                raise ValueError('Invalid view mode')
            for key, (low, high) in limits.items():
                value = candidate[key]
                if type(value) is not int or not low <= value <= high:
                    raise ValueError('%s requires an integer in [%d, %d]' % (key, low, high))
            if candidate['roi_top'] >= candidate['roi_bottom']:
                raise ValueError('ROI top must be below ROI bottom')
            hardware = {key: updates[key] for key in HARDWARE_KEYS if key in updates}
            if hardware:
                if not self.controls or self.demo or self.stop.is_set() or self.error:
                    raise ValueError('Hardware writes are disabled')
                try:
                    applied = self.controls.apply(hardware)
                except Exception as error:
                    # A multi-control write may partially succeed. Do not show old data as valid.
                    self.hardware_applied = None
                    self.hardware_error = str(error)
                    self.fail('Hardware update failed: ' + str(error))
                    self.stop.set()
                    raise RuntimeError(self.error) from error
                self.hardware_applied = dict(self.hardware_applied or {}, **applied)
                self.hardware_error = None
            self.params = candidate
            return self.params.copy()

    def publish(self, frame):
        with self.io_lock, self.lock:
            if frame.shape[:2] != (self.height, self.width):
                raise ValueError('Camera dimensions differ from requested dimensions')
            params = self.params.copy()
            metrics, vis = analyze_frame(frame, params)
            now = time.monotonic()
            metrics['fps'] = 1 / (now - self.last_frame_time) if self.last_frame_time else 0.0
            if self.demo:
                metrics['all_pass'] = False
                cv2.putText(vis, 'DEMO - NOT CAMERA EVIDENCE', (4, self.height - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, .4, (0, 180, 255), 1)
            ok, encoded = cv2.imencode('.jpg', vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                raise RuntimeError('JPEG encoding failed')
            self.source = 'DEMO' if self.demo else 'CAMERA'
            self.error = None
            self.sequence += 1
            self.last_frame_time = now
            self.latest_jpeg = encoded.tobytes()
            self.record = dict(timestamp=datetime.now(timezone.utc).isoformat(), frame_id=self.sequence,
                               source=self.source, params=params, metrics=metrics,
                               hardware_applied=deepcopy(self.hardware_applied),
                               physical_calibration_status='NOT_MEASURED')
            self.raw = frame.copy()

    def fail(self, error):
        with self.lock:
            self.source, self.error = 'CAMERA_ERROR', error
            self.latest_jpeg = None
            self.record = None

    def status(self):
        with self.lock:
            stale = self.last_frame_time is None or time.monotonic() - self.last_frame_time > 3
            record = deepcopy(self.record) if self.record else {}
            metrics = record.get('metrics', dict(all_pass=False))
            if stale:
                metrics['all_pass'] = False
            return dict(source=self.source, error=self.error, stale=stale, params=self.params.copy(),
                        frame_params=record.get('params'), frame_id=record.get('frame_id'),
                        width=self.width, height=self.height, metrics=metrics,
                        hardware_enabled=self.controls is not None and not self.demo and not self.stop.is_set() and not self.error,
                        hardware_applied=deepcopy(self.hardware_applied), hardware_error=self.hardware_error,
                        camera_readback=deepcopy(self.camera_readback),
                        camera_readback_stale=self.camera_readback_at is None or time.monotonic() - self.camera_readback_at > 3,
                        camera_readback_error=self.camera_readback_error,
                        exposure_buttons=self.exposure_buttons,
                        physical_calibration_status='NOT_MEASURED')

    def snapshot(self, directory):
        with self.lock:
            if not self.record or self.status()['stale']:
                raise ValueError('No fresh frame to save')
            report, raw = deepcopy(self.record), self.raw.copy()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stem = 'lighting_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_') + uuid.uuid4().hex
        image, metadata = directory / (stem + '.png'), directory / (stem + '.json')
        try:
            if not cv2.imwrite(str(image), raw):
                raise OSError('PNG write failed')
            metadata.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        except Exception:
            image.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
            raise
        return dict(ok=True, image=str(image.resolve()), report=str(metadata.resolve()))


def observe_camera_loop(state):
    while not state.stop.is_set():
        state.refresh_camera_readback()
        state.stop.wait(1)


def capture_loop(state):
    cap = None
    try:
        monochrome_validated = False
        native_conversion_requested = False
        if not state.demo:
            source = int(state.device) if state.device.isdigit() else state.device
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                raise RuntimeError('Cannot open camera')
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, state.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, state.height)
            cap.set(cv2.CAP_PROP_FPS, state.fps)
            native_conversion_requested = request_native_monochrome(cap, cv2)
            if state.controls:
                state.update_controls({key: state.params[key] for key in HARDWARE_KEYS})
        while not state.stop.is_set():
            with state.io_lock:
                if state.demo:
                    frame = np.full((state.height, state.width), 32, np.uint8)
                    center = state.width // 2, state.height // 2
                    radius = max(2, min(state.height, state.width) // 8)
                    cv2.circle(frame, center, radius, 200, -1)
                    cv2.circle(frame, center, max(1, radius // 2), 32, -1)
                else:
                    if not monochrome_validated:
                        frame, pixel_format, fallback = read_first_monochrome_frame(
                            cap, cv2,
                            native_conversion_requested=native_conversion_requested,
                            expected_shape=(state.height, state.width),
                        )
                        monochrome_validated = True
                        print('CAMERA_PIXEL_PATH format=%s convert_rgb_request=%s fallback=%s' % (
                            pixel_format, native_conversion_requested, fallback), flush=True)
                    else:
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            raise RuntimeError('Camera frame read failed; capture stopped')
                        frame = monochrome_view(
                            frame, expected_shape=(state.height, state.width))
                state.publish(frame)
            state.stop.wait(1 / state.fps if state.demo else .001)
    except Exception as error:
        state.fail(str(error))
    finally:
        if cap is not None:
            cap.release()
        with state.io_lock:
            state.stop.set()
            if state.controls:
                errors = state.controls.restore()
                state.hardware_applied = None
                if errors:
                    state.hardware_error = 'Restoration failed: ' + '; '.join(errors)
                    state.fail(state.hardware_error)
                    print(state.hardware_error, flush=True)


HTML_PAGE = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>实时调光辅助台</title><style>
*{box-sizing:border-box}body{margin:0;padding:20px;background:#0f172a;color:#e2e8f0;font:14px system-ui;overflow-wrap:anywhere}
header,section{padding:18px;background:#1e293b;border:1px solid #475569;border-radius:12px;margin-bottom:16px}
h1{font-size:23px;margin:0 0 8px}p{line-height:1.6;color:#cbd5e1}main{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:16px}main>div{min-width:0}
img{width:100%;background:#020617;min-height:160px}button,select{background:#334155;color:white;border:1px solid #64748b;padding:9px;border-radius:6px;margin:4px}
label{display:flex;align-items:center;gap:8px;padding:8px 0}label span{width:130px}input{flex:1;min-width:50px}output{width:50px;text-align:right}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px}.kpi{background:#0f172a;padding:12px;border-left:4px solid #f59e0b}.PASS{border-color:#10b981}.value{font-size:25px;margin:6px 0}
#banner,#feedback{white-space:pre-wrap;padding:10px;color:#fbbf24}pre{white-space:pre-wrap;max-height:200px;overflow:auto;font-size:12px}
@media(max-width:850px){main{grid-template-columns:1fr}body{padding:10px}}
</style><header><h1>实时调光辅助台</h1><p>红色：灰度达到告警阈值（数值统计仅限 ROI）。绿框：检测到内孔；橙框：未检测到内孔（可能为实心件、阈值或阴影影响）。这些提示不能单独证明光学标定完成或计数准确率。</p><div id="banner">等待视频源…</div></header>
<main><div><section><select id="mode"><option value="assist">调光辅助</option><option value="grid">三区均匀度</option><option value="binary">二值化</option><option value="raw">原始灰度</option></select><button id="save">保存原图与 JSON 快照</button><img id="video" src="/stream.mjpg" alt="实时预览"><div id="feedback" role="status"></div><div id="details"></div></section><section><h3>零件拓扑提示</h3><pre id="parts">等待帧…</pre></section></div>
<div><section><h3>相机实际参数</h3><div class="metrics"><div class="kpi"><div>曝光时间</div><div class="value" id="actual-exposure">—</div><small id="actual-mode">等待回读</small></div><div class="kpi"><div>增益</div><div class="value" id="actual-gain">—</div><small id="actual-gamma">等待回读</small></div></div><p id="camera-readback-status">等待相机参数…</p><p id="exposure-buttons" hidden>板载按键：BTN0 减少 / BTN1 增加曝光，每次 0.1 毫秒。按下后松开；长按不连续调节。</p></section><section><div class="metrics" id="metrics"></div></section><section><h3>图像参数</h3><div id="software"></div><h3>网页相机控制</h3><p id="hw">读取硬件控制状态…</p><div id="hardware"></div></section></div></main>
<script>
const $=id=>document.getElementById(id);
const controls=[['binary_thresh','分割阈值',0,255],['overexp_thresh','过曝告警阈值',1,255],['roi_top','ROI 起始行',0,399],['roi_bottom','ROI 结束行（不含）',1,400],['min_area','最小净面积',1,3000],['exposure','曝光（设备单位）',1,1000],['gamma','Gamma',1,500],['gain','增益',0,255]];
for(const [key,name,min,max] of controls){const label=document.createElement('label');label.innerHTML=`<span>${name}</span><input type="range" id="${key}" min="${min}" max="${max}" step="1"><output id="${key}_value"></output>`;$( ['exposure','gamma','gain'].includes(key)?'hardware':'software').append(label);$(key).oninput=()=>$(key+'_value').textContent=$(key).value;$(key).onchange=()=>queueControl({[key]:Number($(key).value)});}
let pending=Promise.resolve();
function queueControl(update){pending=pending.then(()=>post('/api/control',update)).then(()=>{$('feedback').textContent='参数已接受，等待下一帧';}).catch(e=>{$('feedback').textContent=e.message;});}
async function post(path,data){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Error(result.error||response.statusText);return result;}
$('mode').onchange=()=>queueControl({view_mode:$('mode').value});
$('save').onclick=async()=>{try{const r=await post('/api/snapshot',{});$('feedback').textContent='已保存到服务端：\n'+r.image+'\n'+r.report;}catch(e){$('feedback').textContent=e.message;}};
const fmt=v=>v===null||v===undefined?'—':Number(v).toFixed(2);
async function poll(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('状态读取失败');const s=await r.json(),m=s.metrics;
$('banner').textContent=s.error|| (s.stale?'视频帧已过期，指标无效':s.source==='DEMO'?'DEMO 模拟画面：不可作为相机或标定证据':m.all_pass?'当前帧四项参考指标达标，请人工复核并保存快照':'正在调光：当前帧参考指标未全部达标');
$('video').style.opacity=s.stale||s.error?'.25':'1';
const kpis=[['背景灰度',m.bg_mean,m.status_bg,'参考 20–45'],['金属前景灰度',m.fg_mean,m.status_fg,'参考 160–235'],['过曝比例 %',m.overexp_pct,m.status_overexp,'全 ROI 分母，≤0.5%'],['三区背景极差',m.uniformity_diff,m.status_uniform,'参考 ≤10']];
$('metrics').replaceChildren();for(const [title,value,status,hint] of kpis){const d=document.createElement('div');d.className='kpi '+(s.stale?'WAIT':status);d.innerHTML=`<div>${title}</div><div class="value">${s.stale?'—':fmt(value)}</div><small>${hint} · ${s.stale?'STALE':status||'WAIT'}</small>`;$('metrics').append(d);}
for(const [key] of controls){if(key==='roi_top')$(key).max=s.height-1;if(key==='roi_bottom')$(key).max=s.height;if(document.activeElement!==$(key)){ $(key).value=s.params[key];$(key+'_value').textContent=s.params[key];}if(['exposure','gamma','gain'].includes(key))$(key).disabled=!s.hardware_enabled;}
$('mode').value=s.params.view_mode;$('hw').textContent=s.hardware_enabled?'已启用 V4L2 写入和回读；退出时尝试恢复原设置':'硬件写入关闭；滑块数值不是相机实际读数';
$('hardware').hidden=!s.hardware_enabled;
const camera=s.camera_readback,actualValid=camera&&!s.camera_readback_stale;
$('actual-exposure').textContent=actualValid?camera.exposure_ms.toFixed(1)+' ms':'—';
$('actual-gain').textContent=actualValid?camera.gain:'—';
$('actual-mode').textContent=actualValid?(camera.auto_exposure===1?'手动曝光':'自动曝光 · 显示曝光控件值'):'等待有效回读';
$('actual-gamma').textContent=actualValid?'Gamma '+camera.gamma:'—';
$('camera-readback-status').textContent=actualValid?'相机实际回读 · 约每秒更新一次':s.camera_readback_error?'参数回读失败：'+s.camera_readback_error:'暂无有效相机参数';
$('exposure-buttons').hidden=!s.exposure_buttons;
$('details').textContent=`源 ${s.source} · 帧 ${s.frame_id||'—'} · FPS ${fmt(m.fps)} · 左/中/右背景 ${fmt(m.left_bg)} / ${fmt(m.center_bg)} / ${fmt(m.right_bg)}`;
$('parts').textContent=JSON.stringify(m.parts||[],null,2);
}catch(e){$('banner').textContent='连接中断：'+e.message;$('video').style.opacity='.25';$('metrics').textContent='指标不可用';$('actual-exposure').textContent='—';$('actual-gain').textContent='—';$('actual-mode').textContent='连接中断';$('actual-gamma').textContent='—';$('camera-readback-status').textContent='参数不可用';}finally{setTimeout(poll,500);}}poll();
</script></html>'''


def make_server(state, host, port, snapshot_directory):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_bytes(self, code, data, content_type):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, code, data):
            self.send_bytes(code, json.dumps(data, allow_nan=False).encode('utf-8'), 'application/json; charset=utf-8')

        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/':
                self.send_bytes(200, HTML_PAGE.encode('utf-8'), 'text/html; charset=utf-8')
            elif path == '/api/status':
                self.send_json(200, state.status())
            elif path == '/stream.mjpg':
                self.connection.settimeout(3)
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                previous = -1
                try:
                    while not state.stop.is_set():
                        with state.lock:
                            jpg, sequence = state.latest_jpeg, state.sequence
                            stale = state.status()['stale']
                        if jpg is not None and sequence != previous and not stale:
                            header = b'--FRAME\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % len(jpg)
                            self.wfile.write(header + jpg + b'\r\n')
                            previous = sequence
                        state.stop.wait(.03)
                except (OSError, TimeoutError):
                    pass
            else:
                self.send_json(404, dict(error='Unknown endpoint'))

        def do_POST(self):
            path = urlparse(self.path).path
            # Consume a normal bounded body before any early response. On
            # Windows, closing with unread request bytes can reset the socket
            # and discard the 403/404/415 response. Never read an oversized
            # body here; the endpoint validation below still rejects it.
            length_header = self.headers.get('Content-Length', '0')
            try:
                bounded_length = int(length_header)
            except (TypeError, ValueError):
                bounded_length = None
            body = None
            body_read_error = None
            if bounded_length is not None and 0 < bounded_length <= 4096:
                try:
                    self.connection.settimeout(3)
                    body = self.rfile.read(bounded_length)
                except OSError as error:
                    body_read_error = error

            if path not in ('/api/control', '/api/snapshot'):
                self.send_json(404, dict(error='Unknown endpoint'))
                return
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                self.send_json(403, dict(error='Cross-origin control is forbidden'))
                return
            if self.headers.get_content_type() != 'application/json':
                self.send_json(415, dict(error='Content-Type must be application/json'))
                return
            try:
                if body_read_error is not None:
                    raise body_read_error
                length = int(length_header)
                if not 0 < length <= 4096:
                    raise ValueError('JSON body must be 1..4096 bytes')
                if body is None or len(body) != length:
                    raise ValueError('Incomplete JSON body')
                data = json.loads(body.decode('utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON object')
                if path == '/api/control':
                    result = dict(ok=True, params=state.update_controls(data))
                else:
                    result = state.snapshot(snapshot_directory)
                self.send_json(200, result)
            except (ValueError, UnicodeError) as error:
                self.send_json(400, dict(error=str(error)))
            except (OSError, RuntimeError) as error:
                self.send_json(503, dict(error=str(error)))

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='/dev/video0')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=400)
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--demo', action='store_true', help='Explicit synthetic source; never opens a camera')
    parser.add_argument('--allow-camera', action='store_true', help='Explicitly allow real acquisition')
    parser.add_argument('--allow-camera-controls', action='store_true', help='Allow V4L2 writes/readback/restoration')
    parser.add_argument('--observe-camera-controls', action='store_true', help='Read actual camera controls every second without writing')
    parser.add_argument('--exposure-buttons', action='store_true', help='Show the separately configured BTN0/BTN1 exposure adjustment hint')
    parser.add_argument('--snapshot-dir', type=Path, default=Path('calibration_snapshots'))
    args = parser.parse_args()
    if not args.demo and not args.allow_camera:
        parser.error('Choose --demo or explicitly authorize acquisition with --allow-camera')
    if args.demo and (args.allow_camera or args.allow_camera_controls):
        parser.error('--demo cannot be combined with camera opt-ins')
    if args.allow_camera_controls and not args.device.startswith('/dev/video'):
        parser.error('V4L2 controls require a /dev/video device')
    if not (3 <= args.width <= 4096 and 3 <= args.height <= 4096 and 1 <= args.fps <= 240 and 0 <= args.port <= 65535):
        parser.error('Invalid dimensions, FPS or port')
    state = LightingTunerState(args.device, args.width, args.height, args.fps, args.demo, args.allow_camera_controls,
                               args.observe_camera_controls, args.exposure_buttons)
    # Bind before opening hardware: a port conflict must not touch the camera.
    server = make_server(state, args.host, args.port, args.snapshot_dir)
    thread = threading.Thread(target=capture_loop, args=(state,), daemon=True)
    thread.start()
    observer = None
    if state.observe_camera_controls:
        observer = threading.Thread(target=observe_camera_loop, args=(state,), daemon=True)
        observer.start()
    print('Lighting tuner: http://%s:%d/ (%s)' % (args.host, server.server_port, 'DEMO' if args.demo else 'CAMERA'), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop.set()
        server.server_close()
        thread.join(timeout=20)
        if observer is not None:
            observer.join(timeout=3)
        if thread.is_alive():
            print('Capture is still blocked in the camera backend; restoration is not confirmed.', flush=True)


if __name__ == '__main__':
    main()
