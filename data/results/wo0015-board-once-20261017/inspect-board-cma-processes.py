import datetime
import json
import pathlib
import subprocess

RESULT = pathlib.Path(__file__).parent / "pre-recovery-board-memory-processes.json"
REMOTE = r'''import json, pathlib, subprocess
mem = pathlib.Path("/proc/meminfo").read_text().splitlines()
wanted = [line for line in mem if line.startswith(("CmaTotal:", "CmaFree:"))]
processes = []
needles = ("python", "mjpeg", "8080", "preview", "camera", "video", "v4l2")
for p in pathlib.Path("/proc").glob("[0-9]*"):
    try:
        pid = int(p.name)
        comm = (p / "comm").read_text().strip()
        raw = (p / "cmdline").read_bytes()
        argv = [x.decode("utf-8", "replace") for x in raw.split(b"\0") if x]
        fds = []
        for fd in (p / "fd").iterdir():
            try:
                target = str(fd.resolve(strict=True))
                if target.startswith("/dev/video") or "/dma_heap/" in target or "/dev/dma" in target:
                    fds.append(target)
            except (OSError, RuntimeError):
                pass
        text = " ".join([comm] + argv).lower()
        if fds or any(n in text for n in needles):
            processes.append({"pid":pid,"comm":comm,"argv":argv,"device_fds":sorted(set(fds))})
    except (OSError, ValueError):
        pass
ss = subprocess.run(["ss", "-ltnp"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
listeners = [line for line in ss.stdout.splitlines() if ":8080" in line]
print(json.dumps({"meminfo_lines":wanted,"processes_of_interest":processes,"port_8080_listeners":listeners,"ss_exit":ss.returncode,"ss_stderr":ss.stderr.strip()},sort_keys=True))
'''
proc = subprocess.run(
    [
        "ssh.exe",
        "-oBatchMode=yes",
        "-oConnectionAttempts=1",
        "-oConnectTimeout=10",
        "xilinx@192.168.2.99",
        "sudo -n python3 -",
    ],
    input=REMOTE.encode(),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
record = {
    "timestamp_local": datetime.datetime.now().astimezone().isoformat(),
    "command": "SSH BatchMode + sudo -n Python read-only /proc and listener inspection",
    "returncode": proc.returncode,
    "stdout": proc.stdout.decode("utf-8", "replace"),
    "stderr": proc.stderr.decode("utf-8", "replace"),
    "password_prompted_or_supplied": False,
}
RESULT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(record["stdout"], end="")
if record["stderr"]:
    print(record["stderr"])
raise SystemExit(proc.returncode)
