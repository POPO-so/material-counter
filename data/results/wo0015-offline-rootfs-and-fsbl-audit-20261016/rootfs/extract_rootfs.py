from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ext4_ro import Ext4Image

IMAGE = Path(r"C:\Users\POP\Desktop\FPGA赛\pynq_z2_v3.1.1\pynq_z2_v3.1.1.img")
EXPECTED_UUID = "63b8b04d-af08-419a-a9dc-16f374e283dc"
PACKAGE = "/home/xilinx/pynq"
CANONICAL_PACKAGE = "/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq"
OUT = HERE / "extracted"
MANIFEST = HERE / "source-manifest.json"
BOOT_PY_PRIOR = HERE.parent.parent / "wo0015-boot-provenance-20261016" / "extracted" / "BOOT.PY"


def write_payload(relative: str, data: bytes) -> str:
    target = OUT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target.relative_to(HERE).as_posix()


records: list[dict[str, object]] = []
with Ext4Image(IMAGE, expected_uuid=EXPECTED_UUID) as fs:
    package_entries = list(fs.walk(PACKAGE, max_directories=100_000))
    for image_path, inode in package_entries:
        rel = image_path[len(PACKAGE) + 1 :]
        if "__pycache__" in rel.split("/") or rel.endswith((".pyc", ".pyo")):
            continue
        suffix = Path(rel).suffix.lower()
        include = suffix == ".py"
        kind = "pynq_python_source"
        if rel in {
            "overlays/base/base.bit",
            "overlays/base/base.hwh",
            "overlays/base/base.py",
            "overlays/base/__init__.py",
            "overlays/base/base.tcl",
        }:
            include = True
            kind = "base_overlay_artifact" if suffix in {".bit", ".hwh"} else "base_overlay_source"
        if not include:
            continue
        data = fs.read_file(image_path)
        extracted = write_payload(f"pynq/{rel}", data)
        records.append(
            {
                "image_path": image_path,
                "canonical_image_path": f"{CANONICAL_PACKAGE}/{rel}",
                "inode": inode.number,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "kind": kind,
                "extracted_path": extracted,
            }
        )

    # Only named XRT/CMA support files are read; no broad system-tree scan.
    support_files = [
        ("/usr/include/libxlnk_cma.h", "system/usr/include/libxlnk_cma.h", "cma_header"),
        ("/usr/lib/libcma.so", "system/usr/lib/libcma.so", "cma_shared_library"),
        ("/usr/lib/libxrt_core.so.2.17.0", "system/usr/lib/libxrt_core.so.2.17.0", "xrt_shared_library"),
        ("/usr/lib/libxrt_coreutil.so.2.17.0", "system/usr/lib/libxrt_coreutil.so.2.17.0", "xrt_shared_library"),
        ("/usr/lib/libxrt++.so.2.17.0", "system/usr/lib/libxrt++.so.2.17.0", "xrt_shared_library"),
        ("/usr/lib/python3.10/pyxrt.so", "system/usr/lib/python3.10/pyxrt.so", "pyxrt_python_extension_binary"),        ("/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq-3.1.1.dist-info/METADATA", "system/pynq-3.1.1.dist-info/METADATA", "pynq_distribution_metadata"),
        ("/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq-3.1.1.dist-info/RECORD", "system/pynq-3.1.1.dist-info/RECORD", "pynq_distribution_record"),
        ("/usr/src/kernel/.config", "system/kernel/.config", "kernel_build_config"),
        ("/usr/src/kernel/drivers/fpga/Kconfig", "system/kernel/drivers/fpga/Kconfig", "kernel_fpga_kconfig"),
        ("/usr/src/kernel/drivers/fpga/Makefile", "system/kernel/drivers/fpga/Makefile", "kernel_fpga_makefile"),
        ("/usr/lib/modules/6.6.10-xilinx-v2024.1-g3c0eca68c652/modules.builtin", "system/kernel/modules.builtin", "kernel_builtin_module_index"),
    ]
    for image_path, inode in fs.walk("/usr/include/xrt", max_directories=2_000):
        suffix = Path(image_path).suffix.lower()
        if suffix not in {".h", ".hpp", ".hxx"}:
            continue
        rel = image_path[len("/usr/include/") :]
        data = fs.read_file(image_path)
        extracted = write_payload(f"system/usr/include/{rel}", data)
        records.append({
            "image_path": image_path,
            "inode": inode.number,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "kind": "xrt_header",
            "extracted_path": extracted,
        })

    for image_path, rel, kind in support_files:
        inode = fs.resolve(image_path)
        data = fs.read_file(image_path)
        extracted = write_payload(rel, data)
        records.append({
            "image_path": image_path,
            "inode": inode.number,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "kind": kind,
            "extracted_path": extracted,
        })

    fs_metadata = {
        "uuid": fs.uuid,
        "partition_index": fs.partition.index,
        "partition_start_lba": fs.partition.start_lba,
        "partition_offset_bytes": fs.partition.offset,
        "partition_size_bytes": fs.partition.size,
        "block_size_bytes": fs.block_size,
        "blocks_count": fs.blocks_count,
        "inodes_count": fs.inodes_count,
        "inode_size_bytes": fs.inode_size,
        "group_descriptor_size_bytes": fs.desc_size,
        "group_count": fs.group_count,
        "feature_incompat_hex": f"0x{fs.feature_incompat:x}",
        "superblock_state_raw": fs.state,
        "superblock_state_clean_bit": bool(fs.state & 0x1),
        "superblock_state_error_bit": bool(fs.state & 0x2),
        "needs_recovery_feature_bit": fs.needs_recovery,
        "journal_replayed": False,
        "filesystem_view": "on-disk static metadata/data view; no journal replay and no mount",
    }

# BOOT.PY was previously extracted from the FAT16 boot partition by the read-only
# historical image reader. Only this explicitly requested non-credential file is
# read here; no REVISION member or restricted-raw path is addressed.
boot_bytes = BOOT_PY_PRIOR.read_bytes()
boot_sha = hashlib.sha256(boot_bytes).hexdigest()
expected_boot_sha = "b2653ef809c3271231c78772342c2be728d39686a877d3c6fe8cdabfd4ac1d42"
if boot_sha != expected_boot_sha:
    raise RuntimeError(f"BOOT.PY source hash mismatch: {boot_sha}")
boot_extracted = write_payload("boot/BOOT.PY", boot_bytes)
records.append({
    "image_path": "/BOOT.PY",
    "partition": "MBR partition 1, FAT16 boot partition",
    "size_bytes": len(boot_bytes),
    "sha256": boot_sha,
    "kind": "boot_script_from_prior_read_only_extraction",
    "extracted_path": boot_extracted,
})

manifest = {
    "classification": "VERIFIED_STATIC_EXTRACTION_FROM_LOCAL_IMAGE",
    "source_image": str(IMAGE),
    "source_image_uuid": EXPECTED_UUID,
    "filesystem": fs_metadata,
    "pynq_package_path": PACKAGE,
    "pynq_canonical_package_path": CANONICAL_PACKAGE,
    "pynq_package_version": "3.1.1 (METADATA Version field)",
    "extraction_policy": "PYNQ .py sources, the requested base overlay bit/HWH, named libcma/XRT headers and runtime libraries, pyxrt.so extension binary, distribution METADATA/RECORD, selected local kernel config/index/build files, plus the explicitly requested BOOT.PY; no image code executed",
    "excluded_content": ["REVISION members", "restricted-raw directories", "unlisted boot-partition files"],
    "files": sorted(records, key=lambda item: str(item["image_path"]).casefold()),
}
MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "filesystem": fs_metadata,
    "file_count": len(records),
    "extracted_root": str(OUT),
    "manifest": str(MANIFEST),
    "base_bit": next(record for record in records if str(record["image_path"]).endswith("/base.bit")),
    "base_hwh": next(record for record in records if str(record["image_path"]).endswith("/base.hwh")),
    "boot_py": next(record for record in records if record["kind"] == "boot_script_from_prior_read_only_extraction"),
}, indent=2))