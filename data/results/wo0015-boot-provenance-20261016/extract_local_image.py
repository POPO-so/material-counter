"""Reviewed host-only extractor. Input is one regular IMG file, opened rb.

No mounting, devices, subprocesses, target execution, or writes outside OUT.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import stat

from boot_image_reader import Fat16, parse_mbr, parse_zynq_boot

OUT = Path(__file__).resolve().parent
IMAGE = Path("C:/Users/POP/Desktop/FPGA赛/pynq_z2_v3.1.1/pynq_z2_v3.1.1.img")
RECORDED_IMAGE_SHA256 = "4a6ac1cdbb413d768fcb059b7182e1f53aa7f728b898b7f5061cbb0b7cc93aab"

def dump(path, obj):
    with path.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")

def utc(timestamp): return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).isoformat()

before = IMAGE.stat()
assert stat.S_ISREG(before.st_mode) and before.st_size == 8337309696
extracted = OUT / "extracted"
extracted.mkdir(exist_ok=False)
with IMAGE.open("rb") as image:
    def read_at(offset, length):
        if min(offset, length) < 0 or offset + length > before.st_size: raise ValueError("Source bounds")
        image.seek(offset)
        data = image.read(length)
        if len(data) != length: raise ValueError("Short source read")
        return data
    parts = parse_mbr(read_at(0, 512), before.st_size)
    dump(OUT / "image-partitions.json", {"source": str(IMAGE), "size": before.st_size, "mbr_partitions": parts})
    fat = Fat16(read_at, parts[0])
    dump(OUT / "boot-filesystem.json", {"source": str(IMAGE), "partition": parts[0], "bpb": fat.bpb, "root_short_entries": fat.entries})
    raw, file_ev = fat.extract("BOOT.BIN")
    with (extracted / "BOOT.BIN").open("xb") as f: f.write(raw)
    dump(OUT / "BOOT.BIN-source.json", {"source_image": str(IMAGE), "source_image_sha256": RECORDED_IMAGE_SHA256, "hash_method": "Worker reported one complete read-only SHA-256 pass; no accompanying vendor checksum or download receipt", "source_image_creation_utc": utc(before.st_birthtime), "source_image_mtime_utc": utc(before.st_mtime), "timestamp_role": "Auxiliary host filesystem metadata; not a build or flashing receipt", "fat_entry": file_ev, "extraction": "Bounded MBR/FAT16 parser; no mount or target binary execution"})
    parsed = parse_zynq_boot(raw)
    dump(OUT / "BOOT.BIN-analysis.json", parsed)
    for p in parsed["partitions"]:
        data = raw[p["data_offset"]:p["data_offset"] + p["total_nbytes"]]
        target = extracted / f"partition-{p['index']:02d}.raw.bin"
        with target.open("xb") as f: f.write(data)
        strings = [{"offset": match.start(), "ascii": match.group().decode("ascii")} for match in re.finditer(rb"[\x20-\x7e]{8,}", data) if any(x in match.group().lower() for x in (b"fsbl", b"xilinx", b"u-boot", b"version", b"2024", b"2023", b"ps7"))]
        literals = {f"0x{address:08X}": [m.start() for m in re.finditer(re.escape(address.to_bytes(4, 'little')), data)] for address in (0xF8000240, 0xF8009000, 0xF8009014)}
        dump(extracted / f"partition-{p['index']:02d}.static-hints.json", {"image_name_from_header": p["image_name"], "strings": strings, "address_literal_offsets": literals, "interpretation": "Strings/literals do not prove source code identity, instruction stores, reachability or execution"})
    for name in ("BOOT.SCR", "UENV.TXT"):
        if any(e["short_name"] == name for e in fat.entries):
            data, ev = fat.extract(name, limit=1024 * 1024)
            with (extracted / name).open("xb") as f: f.write(data)
            dump(extracted / (name + ".source.json"), ev)
after = IMAGE.stat()
assert (before.st_size, before.st_mtime_ns, before.st_ino) == (after.st_size, after.st_mtime_ns, after.st_ino)
print(json.dumps({"source": str(IMAGE), "boot_sha256": hashlib.sha256(raw).hexdigest(), "boot_nbytes": len(raw), "partitions": parsed["partitions"], "source_metadata_unchanged": True}, ensure_ascii=False))
