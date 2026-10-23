"""Plot the twelve sealed -10 crossing frames with exact pixel rulers locally."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    root = args.bundle.resolve(strict=True)
    output = args.output or root / "calibration-ruler-preview.png"
    archive = root / "capture/frames_gray8_binary8.npz"
    manifest = json.loads((root / "capture/manifest.json").read_text())
    assert sha(archive) == manifest["raw_frame_archive"]["sha256"]
    results_path = next(root.glob("standby-reopen-*/frame-results.json"))
    results = json.loads(results_path.read_text())
    with np.load(archive, allow_pickle=False) as data:
        gray = data["raw_gray8"]
    assert gray.shape == (300, 240, 320) and gray.dtype == np.uint8
    assert len(results) == 300
    events = [(frame, event) for frame in results for event in frame["events"]]
    assert len(events) == 12
    fonts = [Path("C:/Windows/Fonts/segoeui.ttf"),
             Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    font_path = next((path for path in fonts if path.is_file()), None)
    font = lambda size: (ImageFont.truetype(str(font_path), size)
                         if font_path else ImageFont.load_default())
    title_font, label_font, small_font = font(27), font(16), font(14)
    canvas = Image.new("RGB", (2000, 1290), "#f4f6f8")
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 15), "-10 calibration ruler preview | 12 crossing events / 300 captured frames",
              fill="#152337", font=title_font)
    draw.text((24, 53), "Original mapped Gray8 intensities. Green: counted track bbox. Cyan: X=160 counting line.",
              fill="#243b53", font=label_font)
    draw.text((24, 79), "Left M: mapped Y pixels. Right S*: equivalent source Y = 2 x (M - 20), not a reconstructed source frame.",
              fill="#243b53", font=label_font)
    draw.text((24, 104), "Orange: [20,220) active image. No 180 mm belt scale is assumed. Physical calibration: NOT_MEASURED.",
              fill="#9b4317", font=label_font)
    records = []
    for index, (frame, event) in enumerate(events):
        frame_id, track_id = frame["frame_id"], event["track_id"]
        detection = next(track["detection"] for track in frame["tracks"]
                         if track["track_id"] == track_id)
        xmin, xmax, ymin, ymax = (detection[key] for key in
                                ("x_min", "x_max", "y_min", "y_max"))
        assert 0 <= xmin <= xmax < 320 and 20 <= ymin <= ymax < 220
        tile_x, tile_y = (index % 4) * 500, 150 + (index // 4) * 340
        origin_x, origin_y = tile_x + 63, tile_y + 52
        draw.text((tile_x + 15, tile_y + 3),
                  f"Track {track_id}  |  frame {frame_id}  |  area {detection['area']} px",
                  fill="#152337", font=label_font)
        draw.text((tile_x + 15, tile_y + 26),
                  f"bbox {xmax-xmin+1} x {ymax-ymin+1} px; Y {ymin}..{ymax}",
                  fill="#243b53", font=small_font)
        canvas.paste(Image.fromarray(gray[frame_id]).convert("RGB"), (origin_x, origin_y))
        draw.rectangle((origin_x - 1, origin_y - 1, origin_x + 320, origin_y + 240),
                       outline="#6b7c93", width=1)
        for y in range(0, 240, 8):
            draw.line((origin_x+160, origin_y+y, origin_x+160, origin_y+min(y+3,239)),
                      fill="#40cce5", width=1)
        for y in (20, 220):
            draw.line((origin_x, origin_y+y, origin_x+319, origin_y+y),
                      fill="#ffad42", width=1)
        draw.rectangle((origin_x+xmin, origin_y+ymin, origin_x+xmax, origin_y+ymax),
                       outline="#43ff85", width=2)
        for y in (20, 60, 100, 140, 180, 220):
            yy = origin_y + y
            draw.line((origin_x-6, yy, origin_x-1, yy), fill="#243b53")
            draw.text((origin_x-43, yy-10), str(y), fill="#243b53", font=small_font)
            draw.line((origin_x+320, yy, origin_x+326, yy), fill="#243b53")
            draw.text((origin_x+331, yy-10), str(2*(y-20)), fill="#243b53", font=small_font)
        draw.text((origin_x-59, origin_y+10), "M", fill="#243b53", font=small_font)
        draw.text((origin_x+366, origin_y+10), "S*", fill="#243b53", font=small_font)
        border_touch = ymax == 219 or ymin == 20 or xmin == 0 or xmax == 319
        if border_touch:
            draw.text((tile_x+63, tile_y+299), "Touches active image border: outline may be clipped",
                      fill="#a12a2a", font=small_font)
        records.append({"frame_id": frame_id, "track_id": track_id,
                        "bbox_xyxy_inclusive": [xmin,ymin,xmax,ymax],
                        "area_px": detection["area"], "touches_active_border": border_touch})
    draw.text((24, 1182), "S*=0..400 denotes source-coordinate extent; sampled source row centers end at 398 (mapped row 219).",
              fill="#243b53", font=label_font)
    draw.text((24, 1210), "Only 18 source-resolution frames were archived. These 12 panels show actual mapped event frames, not invented source images.",
              fill="#243b53", font=label_font)
    draw.text((24, 1238), "Track IDs describe algorithm trajectories; matching the operator total 12 does not establish per-item physical identity.",
              fill="#243b53", font=label_font)
    encoded = io.BytesIO()
    canvas.save(encoded, format="PNG")
    payload = encoded.getvalue()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if output.read_bytes() != payload:
            raise FileExistsError(f"refusing to replace a different existing preview: {output}")
    else:
        with output.open("xb") as stream:
            stream.write(payload)
    receipt = {"scope": "LOCAL_DERIVED_VISUALIZATION", "panels": records,
               "preview_path": str(output.resolve()),
               "preview_sha256": hashlib.sha256(payload).hexdigest(),
               "raw_npz_sha256": sha(archive), "frame_results_sha256": sha(results_path),
               "script_sha256": sha(__file__), "image_size": list(canvas.size),
               "physical_calibration_status": "NOT_MEASURED", "pixels_per_mm_y": None}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"preview": str(output), "panels": len(records), "sha256": receipt["preview_sha256"]}))


if __name__ == "__main__":
    main()
