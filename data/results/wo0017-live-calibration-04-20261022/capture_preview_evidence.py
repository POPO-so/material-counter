"""Read an already-running preview; never starts camera, DMA or a board attempt."""
import json
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
URL = "http://192.168.2.99:8080"
timestamps = []
with urlopen(URL + "/stream.mjpg", timeout=10) as response:
    while len(timestamps) < 180:
        line = response.readline()
        if not line:
            raise RuntimeError("preview stream closed")
        if line.strip() != b"--frame":
            continue
        headers = {}
        while True:
            line = response.readline()
            if line in (b"\r\n", b"\n"):
                break
            key, _, value = line.decode("ascii").partition(":")
            headers[key.lower()] = value.strip()
        size = int(headers["content-length"])
        jpeg = response.read(size)
        if len(jpeg) != size:
            raise RuntimeError("short preview JPEG")
        timestamps.append(time.perf_counter())
        response.readline()
(ROOT / "attempt-04-screen.jpg").write_bytes(jpeg)
with urlopen(URL + "/state.json", timeout=10) as response:
    state = json.load(response)
(ROOT / "attempt-04-preview-state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
measurement = {"frames": len(timestamps), "seconds": timestamps[-1]-timestamps[0],
               "delivered_fps": (len(timestamps)-1)/(timestamps[-1]-timestamps[0]),
               "source": "Windows client consuming 180 HTTP multipart JPEG frames",
               "pipeline_fps": state.get("live_fps"), "preprocessing_ms": state.get("stage_median_ms")}
(ROOT / "attempt-04-client-measurement.json").write_text(json.dumps(measurement, indent=2) + "\n", encoding="utf-8")
print(json.dumps(measurement, indent=2))
