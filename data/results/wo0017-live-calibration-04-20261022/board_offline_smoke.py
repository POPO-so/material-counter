"""Board Python/OpenCV compatibility check. Does not open camera or access MMIO."""
import sys
import json

sys.path.insert(0, "/home/xilinx/wo0017/app")
import numpy as np
import cv2
from live_calibration import MonochromePreprocessor, optical_metrics
from stream_counter import MaskCounter
from mjpeg_preview import render_preview_jpeg

frame = np.full((400,640,3),30,np.uint8)
frame[170:220,100:200] = 200
processor = MonochromePreprocessor()
random_frame = np.random.RandomState(17).randint(0,256,(400,640,3),dtype=np.uint8)
sampled, _ = processor(random_frame)
expected = cv2.resize(random_frame[:,:,0], (320,240), interpolation=cv2.INTER_NEAREST)
assert np.array_equal(sampled,expected)
gray, mask = processor(frame)
counter = MaskCounter(count_axis="x", line_x=160, discard_finished_tracks=True)
result = counter.update(0, mask)
metrics = optical_metrics(gray, result, counter, fps=1)
assert metrics["glare_area_px"] > 900
assert not result.tracks
assert metrics["bg_mean"] == 30
jpeg = render_preview_jpeg(mask, total_count=0, line_y=120, calibration=metrics)
assert jpeg[:2] == b"\xff\xd8"
print(json.dumps({"status":"PASS", "kind":"board offline synthetic, no camera/MMIO/DMA",
                  "opencv":cv2.__version__, "glare_area_px":metrics["glare_area_px"],
                  "jpeg_bytes":len(jpeg)}))
