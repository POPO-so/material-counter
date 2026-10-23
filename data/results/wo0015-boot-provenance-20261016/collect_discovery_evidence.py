"""Bounded local-host filename search; never enumerate physical drives."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

OUT = Path(__file__).resolve().parent
dest = OUT / "discovery"
dest.mkdir(exist_ok=True)
if any(dest.iterdir()): raise FileExistsError("Preserve existing discovery evidence")
roots = [Path(p) for p in ("C:/fpga", "D:/Downloads", "D:/BaiduNetdiskDownload", "D:/cxdownload", "C:/Users/POP/Downloads", "C:/Users/POP/Desktop", "C:/Users/POP/Documents", "F:/FPGA")]
records, candidates = [], []
for root in roots:
    if not root.is_dir():
        records.append({"root": str(root), "status": "NOT_PRESENT"}); continue
    argv = ["rg", "--files", "--hidden", "-g", "!.git", "-g", "!node_modules", "-g", "!.venv", "-g", "!__pycache__", "-g", "!AppData", "-g", "!01_AMD_2026.1_Offline_Installer", str(root)]
    try:
        run = subprocess.run(argv, capture_output=True, timeout=60)
        files = run.stdout.decode("utf-8", "replace").splitlines()
        matches = []
        for raw in files:
            p = Path(raw)
            low = p.name.lower()
            if (low == "boot.bin" or ("fsbl" in low and p.suffix.lower() == ".elf") or p.suffix.lower() == ".bif" or
                (p.suffix.lower() in (".img", ".wic", ".xz", ".zip", ".7z") and re.search(r"pynq|zynq|sd.?card", str(p), re.I))):
                matches.append(raw)
                if p.is_file():
                    s = p.stat()
                    candidates.append({"path": str(p.resolve()), "nbytes": s.st_size, "creation_utc": datetime.datetime.fromtimestamp(s.st_birthtime, datetime.timezone.utc).isoformat(), "mtime_utc": datetime.datetime.fromtimestamp(s.st_mtime, datetime.timezone.utc).isoformat(), "content_read": False, "timestamp_role": "Auxiliary only"})
        records.append({"root": str(root), "exit_code": run.returncode, "files_listed": len(files), "matches": matches, "errors": run.stderr.decode("utf-8", "replace")[:3000]})
    except subprocess.TimeoutExpired:
        records.append({"root": str(root), "status": "TIME_LIMIT_INCOMPLETE"})

archive = Path("F:/FPGA/backups/pynq_counter_lab.xpr.zip")
archive_info = {"path": str(archive), "role": "Project archive, not established boot image"}
if archive.is_file():
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        selected = [n for n in names if re.search(r"(?i)(boot[.]bin|fsbl.*[.]elf|[.]bif$|ps7_init[.](c|tcl|html)$)", n)]
        archive_info.update({"members": len(names), "selected_names": selected, "contents_extracted": False})
    with archive.open("rb") as source:
        archive_info["sha256"] = hashlib.file_digest(source, "sha256").hexdigest()

image = Path("C:/Users/POP/Desktop/FPGA赛/pynq_z2_v3.1.1/pynq_z2_v3.1.1.img")
inventory = {"candidates": candidates, "image_hash": {"path": str(image), "sha256": "4a6ac1cdbb413d768fcb059b7182e1f53aa7f728b898b7f5061cbb0b7cc93aab", "method": "Worker complete read-only SHA-256 pass, once; no vendor checksum/flash receipt available"}, "archive": archive_info}
(dest / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(dest / "search-scope.json").write_text(json.dumps({"host_file_roots_only": records, "exclusions": ["physical/removable devices", "mounted images", "AMD installer payload", "unrelated file contents", "full filesystem search"], "negative_claim_boundary": "No match within listed, completed filename searches does not prove global absence"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"roots": len(records), "candidates": len(candidates), "archive_members": archive_info.get("members")}))
