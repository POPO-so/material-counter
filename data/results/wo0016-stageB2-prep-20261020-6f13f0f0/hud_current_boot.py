"""Restore the existing camera-only HUD on the explicitly verified new boot."""
from pathlib import Path
import hashlib
import runpy
import sys

BOOT = "6f13f0f0-4433-445f-a023-fa403685aeee"
BASE = Path("/home/xilinx/wo0016-phase110-58e6af6c-6660-4d44-b0c5-e18f4a564202")
SOURCE = BASE / "stageb1_calibration_preview_hud.py"
EXPECTED_SHA256 = "66206cf7460818064639443b787ee4e2b20e9ab19f54e48e88186d1bc3d087ce"
if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != BOOT:
    raise SystemExit("STOP: boot changed")
if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != EXPECTED_SHA256:
    raise SystemExit("STOP: HUD source changed")
sys.path.insert(0, str(BASE))
namespace = runpy.run_path(str(SOURCE), run_name="camera_only_hud")
# The original file is preserved. Only the camera-only main's boot binding is
# updated; no sampler, Overlay, DMA, manifest, receipt, or guard is imported.
namespace["main"].__globals__["BOOT"] = BOOT
namespace["main"]()
