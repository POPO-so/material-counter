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
        "count": 128,
        "state": "RUNNING",
        "display_fps": 19.4,
        "processing_ms": 42.7,
        "cpu_percent": 31.0,
        "cpu0_percent": 28.0,
        "cpu1_percent": 34.0,
        "memory_percent": 46.2,
        "static_lut_percent": 23.0,
        "static_ff_percent": 15.2,
        "static_bram_percent": 3.2,
        "static_dsp_percent": 4.1,
    }
    frame = composer.render(
        gray, binary, uuid.UUID("01234567-89ab-cdef-0123-456789abcdef"), 12345,
        boxes=((112, 127, 208, 223), (428, 92, 542, 208)),
        tracks=(("17", 160, 175), ("18", 485, 160)),
        line_x=320, dashboard=dashboard,
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
<p>合成或回放帧预览；LIVE 数值为示例。接口：/proc/stat、/proc/meminfo；板端资源采样约 3 秒，性能采样约 2 秒；STATIC LUT/FF/BRAM/DSP 来自 Vivado manifest。</p>
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
