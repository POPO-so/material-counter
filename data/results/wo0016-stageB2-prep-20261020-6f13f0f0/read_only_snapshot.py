"""Read-only board gates; no capture, allocation, MMIO, or guard mutation."""
from pathlib import Path
import datetime
import hashlib
import json
import subprocess

base = Path("/home/xilinx/wo0016-phase110-58e6af6c-6660-4d44-b0c5-e18f4a564202")
def command(argv):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=15)
    return {"argv": argv, "rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
def file_info(path):
    p = Path(path)
    info = {"path": str(p), "exists": p.exists(), "symlink": p.is_symlink()}
    if p.is_symlink():
        info["link_target"] = str(p.readlink())
    if p.is_file():
        content = p.read_bytes()
        info.update(sha256=hashlib.sha256(content).hexdigest(), size=len(content))
        if p.suffix == ".json":
            info["json"] = json.loads(content)
    return info
boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
files = [base / "wo0015/run-manifest.json", base / "wo0015/wo15.bit", base / "wo0015/wo15.hwh",
         Path("/home/xilinx/wo0015/wo15.bit"), Path("/home/xilinx/wo0015/wo15.hwh"),
         Path("/home/xilinx/wo0015/build-pair-manifest.json")]
for root in (Path("/var/lib/wo0016"), Path("/var/lib/wo0015-s2mm")):
    files.extend(sorted(root.glob("*.json")))
files.extend(sorted(Path("/var/lib/wo0015-s2mm").glob("boot-*/overlay-only-pass.json")))
result = {
    "board_time": datetime.datetime.now(datetime.timezone.utc).isoformat(), "boot_id": boot,
    "fpga_state": Path("/sys/class/fpga_manager/fpga0/state").read_text().strip(),
    "fpga_flags": Path("/sys/class/fpga_manager/fpga0/flags").read_text().strip(),
    "meminfo": Path("/proc/meminfo").read_text(), "files": [file_info(p) for p in files],
    "checks": [command(c) for c in [
        ["systemctl", "show", "ov9281-init.service", "-p", "Type", "-p", "Result", "-p", "ExecMainStatus", "-p", "ActiveState"],
        ["journalctl", "-b", "-u", "ov9281-init.service", "--no-pager"],
        ["v4l2-ctl", "-d", "/dev/video0", "--get-fmt-video", "--get-parm", "--list-ctrls"],
        ["ss", "-ltnp"], ["fuser", "-v", "/dev/video0", "/dev/video1"],
        ["systemctl", "show", "wo0016-b2-prep-hud-6f13f0f0.service", "-p", "ActiveState", "-p", "SubState", "-p", "MainPID"],
    ]],
    "stageb2": {"status": "NOT_RUN", "guard_policy": "DO_NOT_CONSUME", "material": "5 M6 nuts; no washers", "target_count": 5, "settle_s": 3, "valid_frames": 165}
}
print(json.dumps(result, indent=2))
