"""Render a hardware-free 1280x720 preview of the standalone HDMI dashboard."""
from __future__ import annotations

import argparse
import base64
from pathlib import Path
import uuid

import cv2
import numpy as np
from PIL import Image

from .compositor import DashboardComposer


def _first_font(candidates, role):
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    raise FileNotFoundError("No usable {} font found. Tried: {}".format(
        role, ", ".join(str(Path(item)) for item in candidates)))


def _fonts():
    root = Path(__file__).resolve().parents[2]
    cjk = _first_font((
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        root / "fonts" / "DroidSansFallbackFull.ttf",
        Path("/home/xilinx/standalone/fonts/DroidSansFallbackFull.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ), "CJK")
    latin = _first_font((
        Path(r"C:\Windows\Fonts\arial.ttf"),
        root / "fonts" / "DejaVuSans.ttf",
        Path("/home/xilinx/standalone/fonts/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ), "Latin")
    return cjk, latin


def _synthetic_frame():
    yy, xx = np.mgrid[0:400, 0:640]
    gray = np.clip(34 + xx * 0.19 + yy * 0.08, 0, 255).astype(np.uint8)
    cv2.rectangle(gray, (25, 35), (615, 365), 58, 2)
    for cx, cy, radius, level in ((160, 175, 48, 204), (286, 220, 36, 168),
                                  (465, 150, 58, 222), (520, 285, 28, 190)):
        cv2.circle(gray, (cx, cy), radius, level, -1, cv2.LINE_AA)
        cv2.circle(gray, (cx, cy), radius, 246, 2, cv2.LINE_AA)
    cv2.line(gray, (320, 0), (320, 399), 130, 1, cv2.LINE_8)
    _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    return gray, binary


def render_preview(out_dir):
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cjk_font, latin_font = _fonts()
    composer = DashboardComposer(cjk_font, latin_font_path=latin_font)
    gray, binary = _synthetic_frame()
    dashboard = {
        "count": 134,
        "state": "RUNNING",
        "camera_fps": 30.1,
        "e2e_ms": 52.6,
        "drop_total": 0,
        "processing_fps": 21.7,
        "display_fps": 19.4,
        "effective_input_fps": 29.8,
        "preview_submitted_fps": 21.4,
        "binary_fps": 21.4,
        "processing_ms": 42.7,
        "regions_ms": {"mean": 7.8},
        "track_ms": {"mean": 4.6},
        "pl": {"aggregate": {"active_cycles": 38227,
                               "total_cycles": 169580}},
        "mode": "NORMAL",
        "exposure_text": "曝光 5.0 ms",
        "exposure_range_text": "范围 0–1000 ms / ±1 ms",
        "button_status": "IRQ READY / SW0 ON",
        "preview_status": "PL READY · 二值预览",
        "exposure_latency_text": "EXP 42 ms",
        "cpu_source_text": "CPU 来源 draw 31%, camera 18%, other 11%",
        "queue_summary_text": "队列 camera 0/8 · draw 1/8",
        "thread_text": "threads python3 31.0tick/s · drawing 14.8tick/s",
        "cpu_percent": 31.0,
        "cpu0_percent": 28.0,
        "cpu1_percent": 34.0,
        "memory_percent": 46.2,
        "static_lut_percent": 23.0,
        "static_ff_percent": 15.2,
        "static_bram_percent": 3.2,
        "static_dsp_percent": 4.1,
        "rle_fresh": True,
        "rle_activity_pct": 3.0,
        "input_mb_s": 1.1,
        "output_mb_s": 0.4,
        "input_stall_pct": 2.3,
        "output_stall_pct": 0.0,
    }
    frame = composer.render(
        gray, binary, uuid.UUID("01234567-89ab-cdef-0123-456789abcdef"), 12345,
        boxes=((112, 127, 208, 223), (428, 92, 542, 208)),
        tracks=(("17", 160, 175), ("18", 485, 160)),
        line_x=320, dashboard=dashboard,
        status_lines=("诊断快照 关闭", "跟踪 2 个目标", "遥测状态 新鲜"),
        binary_background=True,
    )
    png_path = out_dir / "hdmi-resource-dashboard.png"
    html_path = out_dir / "hdmi-resource-dashboard.html"
    Image.fromarray(frame).save(png_path, format="PNG")
    encoded = base64.b64encode(png_path.read_bytes()).decode("ascii")
    html = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HDMI Resource Dashboard Preview</title>
<style>
html,body{margin:0;min-height:100%;background:#10141b;color:#dce5ef;
font:14px/1.5 Arial,"Microsoft YaHei",sans-serif}
body{min-height:100vh;display:flex;flex-direction:column;align-items:center;
justify-content:center;gap:12px;padding:12px;box-sizing:border-box}
img{display:block;width:1280px;height:720px;max-width:calc(100vw - 24px);
max-height:calc(100vh - 74px);object-fit:contain;image-rendering:auto}
p{margin:0;text-align:center;color:#aab7c6}
</style>
</head>
<body>
<img id="preview" src="data:image/png;base64,""" + encoded + """" alt="1280×720 HDMI 圆饼资源仪表盘">
<p>布局合成预览；所有数值均为示例，不代表板端实时数据。STATIC LUT/FF/BRAM/DSP 为合成值。</p>
<script>
(()=>{const image=document.getElementById('preview');function fit(){
const ratio=Math.min((innerWidth-24)/1280,(innerHeight-74)/720);
const scale=ratio>=1?Math.floor(ratio):ratio;
image.style.width=(1280*scale)+'px';image.style.height=(720*scale)+'px';}
addEventListener('resize',fit);fit();})();
</script>
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8")
    return png_path, html_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path,
                        default=Path.cwd() / "dashboard-preview")
    args = parser.parse_args(argv)
    png_path, html_path = render_preview(args.out_dir)
    print("PNG: {}".format(png_path))
    print("HTML: {}".format(html_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
