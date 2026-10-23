"""Only reviewed host file reads; extract text/kernel candidates as data."""
import hashlib
import json
from pathlib import Path
import re

from boot_image_reader import Fat16, parse_mbr

OUT = Path(__file__).resolve().parent
IMAGE = Path("C:/Users/POP/Desktop/FPGA赛/pynq_z2_v3.1.1/pynq_z2_v3.1.1.img")
if (OUT / "extracted/BOOT.PY").exists() or (OUT / "restricted-raw/REVISION").exists():
    raise FileExistsError("Additional extraction already completed; preserve original evidence")
with IMAGE.open("rb") as source:
    def read_at(o, n):
        source.seek(o)
        return source.read(n)
    fs = Fat16(read_at, parse_mbr(read_at(0, 512), IMAGE.stat().st_size)[0])
    for name in ("REVISION", "BOOT.PY", "IMAGE.UB"):
        raw, ev = fs.extract(name, limit=16 * 1024 * 1024)
        target = OUT / "extracted" / name
        with target.open("xb") as f: f.write(raw)
        ev["kind"] = "LOCAL_IMAGE_CURRENT_FILE_NOT_HISTORICAL_BOOT_PROOF"
        ev["first16_hex"] = raw[:16].hex()
        ev["selected_strings"] = [{"offset": m.start(), "ascii": m.group().decode("ascii")} for m in re.finditer(rb"[\x20-\x7e]{12,}", raw) if any(s in m.group().lower() for s in (b"linux", b"2024", b"pynq"))][:30]
        (OUT / "extracted" / (name + ".source.json")).write_text(json.dumps(ev, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(name, len(raw), hashlib.sha256(raw).hexdigest())
