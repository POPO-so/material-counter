from __future__ import annotations
import os,json,subprocess
from pathlib import Path
class PreflightError(RuntimeError):pass
def audit_process_ownership(proc_root: str | Path = "/proc", *, current_pid: int | None = None,
                            require_root: bool = True) -> dict[str, Any]:
    """Reject another process with FPGA device mappings/FDs or known owners."""
    if require_root and (not hasattr(os, "geteuid") or os.geteuid() != 0):
        raise PreflightError("root is required for a complete /proc device ownership scan")
    proc = Path(proc_root)
    if not proc.is_dir():
        raise PreflightError(f"process table is unavailable: {proc}")
    self_pid = os.getpid() if current_pid is None else current_pid
    device_names = ("/dev/mem", "/dev/xdevcfg", "/dev/fpga0", "/dev/fpga", "/dev/uio")
    command_names = ("ipykernel_launcher", "fpgautil", "openocd", "xsdb", "xvcserver",
                     "remote-send-once", "dma_replay_verify", "diagnostic50")
    findings: list[dict[str, Any]] = []
    denied: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == self_pid:
            continue
        pid = int(entry.name)
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
            maps = (entry / "maps").read_text(encoding="utf-8", errors="replace")
            reasons: list[str] = []
            if any(name in command for name in command_names):
                reasons.append("known_fpga_owner_command")
            if any(device in maps for device in device_names):
                reasons.append("fpga_device_mapping")
            fd_dir = entry / "fd"
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(fd)
                except FileNotFoundError:
                    continue
                if (any(target == device or target.startswith(device + " ")
                        for device in device_names) or
                        target == "/dev/uio" or target.startswith("/dev/uio")):
                    reasons.append("fpga_device_fd")
                    break
            if reasons:
                findings.append({"pid": pid, "reasons": reasons})
        except FileNotFoundError:
            # Processes naturally disappear while /proc is being enumerated.
            continue
        except PermissionError:
            denied.append(pid)
    if findings or denied:
        raise PreflightError("FPGA device ownership scan failed: " + json.dumps(
            {"findings": findings, "denied": denied}, sort_keys=True))
    return {"findings": findings, "denied": denied, "scanned_proc": str(proc)}

def read(p):
    try:return Path(p).read_text().strip()
    except OSError as e:return str(e)
try:ownership=audit_process_ownership()
except Exception as e:ownership={'error':str(e)}
mem={k:int(v.split()[0]) for k,v in (l.split(':',1) for l in read('/proc/meminfo').splitlines()) if k.startswith('Cma')}
r={'boot_id':read('/proc/sys/kernel/random/boot_id'),'meminfo':mem,'ownership':ownership,
'manager_state':read('/sys/class/fpga_manager/fpga0/state'),'manager_flags':read('/sys/class/fpga_manager/fpga0/flags'),
'remote_bundle_directory_exists':Path('/home/xilinx/wo0015_first_board_20261015').exists(),
'dmesg':subprocess.run(['dmesg','--color=never'],capture_output=True,text=True).stdout,
'guard_files':{str(p):read(p) for p in Path('/var/lib/wo0015-s2mm').glob('*') if p.is_file()}}
print(json.dumps(r),flush=True)
