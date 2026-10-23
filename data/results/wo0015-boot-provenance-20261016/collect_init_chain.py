"""Static host-source and saved-log audit; no imports of target code."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import struct
import uuid
import zlib

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
DEST = OUT / "init-chain"
DEST.mkdir(exist_ok=True)
def sha(p):
    with p.open("rb") as f: return hashlib.file_digest(f, "sha256").hexdigest()
def info(p):
    s = p.stat()
    return {"path": str(p), "nbytes": s.st_size, "sha256": sha(p), "creation_utc": datetime.datetime.fromtimestamp(s.st_birthtime, datetime.timezone.utc).isoformat(), "mtime_utc": datetime.datetime.fromtimestamp(s.st_mtime, datetime.timezone.utc).isoformat(), "timestamp_role": "Auxiliary host metadata, not source generation or target execution proof"}
def dump(name, value):
    with (DEST / name).open("x", encoding="utf-8") as f: json.dump(value, f, ensure_ascii=False, indent=2); f.write("\n")

template = Path("F:/AMDDesignTools/2026.1/Vitis/data/embeddedsw/lib/sw_apps/zynq_fsbl/src")
spans = {"main.c": [(243, 264), (482, 503), (560, 586), (721, 772)], "image_mover.c": [(166, 175), (326, 340), (424, 441), (542, 559)], "image_mover.h": [(65, 79)], "fsbl_hooks.c": [(49, 66), (78, 96), (108, 126)], "pcap.c": [(402, 435)], "fsbl.h": [(403, 415)]}
sources = []
for name, ranges in spans.items():
    p = template / name
    lines = p.read_text(encoding="utf-8").splitlines()
    sources.append({**info(p), "role": "Installed 2026.1 SDK reference template, NOT established source of image FSBL", "excerpts": [{"first": a, "last": b, "text": "\n".join(f"{i}: {lines[i-1]}" for i in range(a, min(b, len(lines)) + 1))} for a, b in ranges]})

log = ROOT / "data/results/wo0015-first-board-retry3-20261015/postflight-dmesg.txt"
lines = log.read_text(encoding="utf-8").splitlines()
matches = [{"line": i + 1, "text": s} for i, s in enumerate(lines) if any(term in s for term in ("writing wo15.bin", "writing base.bin", "444d4c49-7c99-ec8f-b75a-33d7ed50b28d", "63b8b04d", "mmcblk0: p1 p2"))]
saved = json.loads((ROOT / "data/results/wo0015-runonly-resume-retry2-20261015/initial-snapshot.json").read_bytes())
dump("evidence.json", {"source_metadata": sources, "historical_log": {**info(log), "selected_lines": matches}, "historical_os_identity": {"boot_id": saved["boot_id"], "uname": saved["uname"]}, "actual_fsbl_source": "UNKNOWN", "post_config_execution": "UNKNOWN", "real_hardware_state": "UNKNOWN; user reports board disconnected"})

image = Path("C:/Users/POP/Desktop/FPGA赛/pynq_z2_v3.1.1/pynq_z2_v3.1.1.img")
with image.open("rb") as f:
    f.seek(268288 * 512 + 1024)
    sb = f.read(1024)
assert sb[56:58] == b"\x53\xef"
with (DEST / "ext4-superblock.raw.bin").open("xb") as f: f.write(sb)
root_uuid = str(uuid.UUID(bytes=sb[104:120]))
dump("rootfs-uuid-comparison.json", {"local_image": str(image), "superblock_offset": 268288 * 512 + 1024, "raw_sha256": hashlib.sha256(sb).hexdigest(), "uuid_offset_in_superblock": "0x68", "local_uuid": root_uuid, "historical_root_uuid": "63b8b04d-af08-419a-a9dc-16f374e283dc", "equal": root_uuid == "63b8b04d-af08-419a-a9dc-16f374e283dc", "classification": "INFERRED common filesystem lineage; matching clone UUID is not unique device, complete image, BOOT.BIN or execution identity", "format_source": "https://www.kernel.org/doc/html/latest/filesystems/ext4/super.html"})

fsbl = (OUT / "extracted/partition-00.raw.bin").read_bytes()
table_words = [0x22, 0xF8000008, 0xDF0D, 0x33, 0xF8000900, 0xF, 0xF, 0x33, 0xF8000240, 0xFFFFFFFF, 0, 0x22, 0xF8000004, 0x767B, 0]
pattern = struct.pack("<15I", *table_words)
hits = [m.start() for m in re.finditer(re.escape(pattern), fsbl)]
dump("fsbl-table-patterns.json", {"raw_fsbl_sha256": hashlib.sha256(fsbl).hexdigest(), "pattern_sha256": hashlib.sha256(pattern).hexdigest(), "pattern_offsets": hits, "words_hex": [f"0x{v:08X}" for v in table_words], "interpretation": {"VERIFIED": "Three exact raw byte patterns with PS7 instruction grammar; no F8009000/F8009014 absolute little-endian words in this payload", "INFERRED": "These resemble default PS7 post-config tables that enable level shifters/reset without setting AFI1 bit0", "UNKNOWN": "Symbol names, silicon assignment, call sites, reachability, indirect register writes and actual execution"}, "macro_reference": "Saved WO0015 generated ps7_init.h:38-51; opcode WRITE=2 with 2 args, MASKWRITE=3 with 3 args, EXIT=0"})

script = (OUT / "extracted/BOOT.SCR").read_bytes()
assert struct.unpack_from(">I", script)[0] == 0x27051956
header = bytearray(script[:64]); expected_hcrc = struct.unpack_from(">I", header, 4)[0]; header[4:8] = b"\x00" * 4
size, dcrc = struct.unpack_from(">I", script, 12)[0], struct.unpack_from(">I", script, 24)[0]
data = script[64:64 + size]
assert zlib.crc32(header) & 0xFFFFFFFF == expected_hcrc and zlib.crc32(data) & 0xFFFFFFFF == dcrc
strings = [m.group().decode("ascii") for m in re.finditer(rb"[\x20-\x7e\t\r\n]{8,}", data)]
with (DEST / "boot-script.static.txt").open("x", encoding="utf-8") as f: f.write("STATIC DATA ONLY; NOT EXECUTED\n" + "\n".join(strings) + "\n")
dump("boot-script-audit.json", {"source_sha256": hashlib.sha256(script).hexdigest(), "header_crc_valid": True, "data_crc_valid": True, "declared_payload_bytes": size, "script_type_byte": script[30], "executed": False, "note": "Source recipe only; scripts may select external boot files and alternate boot modes. No claim of historical execution."})
print(json.dumps({"source_files_hashed": len(sources), "root_uuid_matches_history": root_uuid == "63b8b04d-af08-419a-a9dc-16f374e283dc", "postconfig_shaped_patterns": len(hits), "actual_fsbl_source": "UNKNOWN"}))
