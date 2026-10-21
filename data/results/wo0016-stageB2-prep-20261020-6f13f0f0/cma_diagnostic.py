"""Read-only CMA/process/descriptor snapshot for the current PYNQ boot."""
from pathlib import Path
import os
import re
import json
import subprocess

targets = ("/dev/video", "/dev/mem", "/dev/uio", "/dev/dma_heap", "/dev/xlnk")
holders = []
for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    pid = entry.name
    try:
        cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        for fd in (entry / "fd").iterdir():
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if any(link.startswith(prefix) for prefix in targets):
                holders.append({"pid": int(pid), "fd": fd.name, "target": link, "cmdline": cmdline})
    except (OSError, PermissionError):
        continue

def read(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError as exc:
        return f"UNAVAILABLE: {type(exc).__name__}: {exc}"

def run(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=10)
    return {"argv": args, "rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}

mem = read("/proc/meminfo")
result = {
    "boot_id": read("/proc/sys/kernel/random/boot_id").strip(),
    "device_holders": holders,
    "python_hud_processes": run(["ps", "-eo", "pid,ppid,etime,rss,stat,args"]),
    "meminfo_selected": {k: v for line in mem.splitlines() if (m := re.match(r"^(MemFree|MemAvailable|Cached|SReclaimable|CmaTotal|CmaFree):", line)) for k, v in [(m.group(1), line)]},
    "vmstat": {k: v for line in read("/proc/vmstat").splitlines() if (m := re.match(r"^(nr_file_pages|nr_shmem|nr_slab_reclaimable|nr_free_cma|nr_free_pages)\s+(\d+)$", line)) for k, v in [(m.group(1), int(m.group(2)))]},
    "buddyinfo": read("/proc/buddyinfo"),
    "pagetypeinfo_cma": "\n".join(line for line in read("/proc/pagetypeinfo").splitlines() if "CMA" in line or "Node" in line),
    "cma_debugfs": {str(path): read(path) for path in Path("/sys/kernel/debug/cma").glob("*")} if Path("/sys/kernel/debug/cma").exists() else "not mounted/available",
    "listeners_8080": run(["ss", "-ltnp", "sport", "=", ":8080"]),
    "fd_scan_access": "root process enumeration; transient process races may omit descriptors"
}
print(json.dumps(result, indent=2, sort_keys=True))
