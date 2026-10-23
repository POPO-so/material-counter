from pathlib import Path
import os
import subprocess

base = Path('/home/xilinx/wo0017/physical-calibration-20261023-06')
log_path = base / "operator-gate.log"
pid_path = base / "operator-gate.pid"
if log_path.exists() or pid_path.exists():
    raise SystemExit("refusing to reuse an existing standby log or pid file")
args = [
    "/usr/bin/python3", str(base / "scripts/wo0016/operator_gate_launcher.py"),
    "--attempt-dir", str(base), "--control-port", "8081", "--host", "127.0.0.1",
    "--handoff-timeout-s", "3600", "--standby-before-preflight", "--",
    "--mode", "board", "--physical-calibration",
    "--calibration-id", 'physical-calibration-20261023-06', "--calibration-lock", '/var/lib/wo0017/calibration/35983d66-bb1d-4a8a-85e5-802170825515/physical-calibration-20261023-06/calibration-lock.json',
    "--expected-boot-id", '35983d66-bb1d-4a8a-85e5-802170825515',
    "--stage1-receipt", "/var/lib/wo0017/calibration/35983d66-bb1d-4a8a-85e5-802170825515/physical-calibration-20261023-06-inputs/stage1-receipt.json",
    "--stage1-receipt-sha256", 'b3dfa4788dac8ac3b1b867846fe7f654cc41ef1e49de29f5facd161994fa7d74',
    "--manifest", str(base / "calibration-run-manifest.json"),
    "--manifest-sha256", 'd02b04e495ad80f0256f35002cda474bcc6af16590b8085a8044f86f52e5f782',
    "--execute", "--authorize-stage", "wo0017-physical-calibration", "--approve-once",
    "--counting-profile", "full-width-mixed", "--max-frames", "300",
    "--stabilization-delay-s", "3.0", "--operator-prearm-delay-s", "0.0",
    "--camera", "/dev/video0", "--line-x", "160", "--line-y", "120", "--count-axis", "x",
    "--host", "127.0.0.1", "--port", "8080",
    "--capture-dir", str(base / "capture"), "--capture-max-bytes", "134217728",
    "--evidence-json", str(base / "evidence.json"),
]
env = os.environ.copy()
env.update({"XILINX_XRT": "/usr", "PYTHONPATH": "/home/xilinx", "PYTHONDONTWRITEBYTECODE": "1"})
os.umask(0o077)
with log_path.open("xb") as log:
    process = subprocess.Popen(args, cwd=base / "scripts/wo0016", env=env,
                               stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True,
                               close_fds=True)
with pid_path.open("x") as pid_file:
    pid_file.write(f"{process.pid}\n")
print(process.pid)
