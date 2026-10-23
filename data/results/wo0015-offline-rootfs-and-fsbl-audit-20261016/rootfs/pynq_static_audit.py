from __future__ import annotations
import base64
import csv
import hashlib
import json
import re
import stat
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULT_DIR = HERE.parent
EXTRACTED = HERE / "extracted"
MANIFEST_PATH = HERE / "source-manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
RECORDS = {item["extracted_path"]: item for item in MANIFEST["files"]}

def find_lines(path: Path, pattern: str) -> list[int]:
    rx = re.compile(pattern)
    return [n for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1) if rx.search(line)]

def evidence(extracted_path: str, lines: list[int], claim: str) -> dict[str, object]:
    entry = RECORDS[extracted_path]
    return {
        "claim": claim,
        "image_path": entry["image_path"],
        "extracted_path": extracted_path,
        "sha256": entry["sha256"],
        "line_numbers": lines,
    }

metadata_path = EXTRACTED / "system/pynq-3.1.1.dist-info/METADATA"
metadata_text = metadata_path.read_text(encoding="utf-8", errors="replace")
metadata = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in metadata_text.splitlines() if ":" in line and line.split(":", 1)[0] in {"Name", "Version", "Summary", "Home-page"}}
record_path = EXTRACTED / "system/pynq-3.1.1.dist-info/RECORD"
record_rows = list(csv.reader(record_path.read_text(encoding="utf-8", errors="replace").splitlines()))
verified = 0
mismatches: list[str] = []
missing: list[str] = []
for row in record_rows:
    if len(row) < 2 or not row[0].startswith("pynq/") or not row[0].endswith(".py") or not row[1].startswith("sha256="):
        continue
    source = EXTRACTED / "pynq" / row[0][len("pynq/"):]
    if not source.is_file():
        missing.append(row[0])
        continue
    actual = base64.urlsafe_b64encode(hashlib.sha256(source.read_bytes()).digest()).decode("ascii").rstrip("=")
    expected = row[1].split("=", 1)[1].rstrip("=")
    verified += 1
    if actual != expected:
        mismatches.append(row[0])

hwh_path = EXTRACTED / "pynq/overlays/base/base.hwh"
hwh_text = hwh_path.read_text(encoding="utf-8", errors="replace")
hwh_tree = ET.parse(hwh_path)
hwh_params: dict[str, list[str]] = {}
for element in hwh_tree.iter("PARAMETER"):
    name = element.attrib.get("NAME", "")
    if re.fullmatch(r"PCW_S_AXI_HP[0-3]_DATA_WIDTH", name):
        hwh_params.setdefault(name, []).append(element.attrib.get("VALUE", ""))
tcl_path = EXTRACTED / "pynq/overlays/base/base.tcl"
tcl_text = tcl_path.read_text(encoding="utf-8", errors="replace")
ports = {}
for n in range(4):
    param = f"PCW_S_AXI_HP{n}_DATA_WIDTH"
    use = f"PCW_USE_S_AXI_HP{n}"
    tcl_use = re.findall(rf"CONFIG\.{use}\s+\{{([^}}]+)\}}", tcl_text)
    tcl_width = re.findall(rf"CONFIG\.{param}\s+\{{([^}}]+)\}}", tcl_text)
    hwh_width = hwh_params.get(param, [])
    ports[f"HP{n}"] = {
        "hwh_data_width_bits": hwh_width[0] if len(hwh_width) == 1 else None,
        "hwh_parameter_occurrences": len(hwh_width),
        "tcl_data_width_values": tcl_width,
        "tcl_enable_values": tcl_use,
        "hwh_line_numbers": find_lines(hwh_path, rf'NAME="{param}"'),
        "tcl_width_line_numbers": find_lines(tcl_path, rf"CONFIG\.{param}\s"),
        "tcl_enable_line_numbers": find_lines(tcl_path, rf"CONFIG\.{use}\s"),
    }

pynq_py_files = sorted((EXTRACTED / "pynq").rglob("*.py"))
searched = {"f8009000": [], "f8009014": [], "f8000240": [], "FPGA_RST_CTRL": [], "AFI1_RDCHAN_CTRL": [], "AFI1_WRCHAN_CTRL": []}
needles = {key: re.compile(re.escape(key), re.IGNORECASE) for key in searched}
for source in pynq_py_files:
    for line_no, line in enumerate(source.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        for key, pattern in needles.items():
            if pattern.search(line):
                searched[key].append({"path": source.relative_to(EXTRACTED).as_posix(), "line": line_no, "text": line.strip()})

image_id = json.loads((HERE / "image-identity.json").read_text(encoding="utf-8"))
boot = EXTRACTED / "boot/BOOT.PY"
boot_lines = boot.read_text(encoding="utf-8", errors="replace").splitlines()
boot_evidence = evidence("extracted/boot/BOOT.PY", [27, 29], "BOOT.PY imports BaseOverlay and constructs BaseOverlay('base.bit'); runtime execution is not proven by static contents")

# Read only named local kernel build/source metadata from the ext4 image.
sys.path.insert(0, str(HERE))
from ext4_ro import Ext4Image
image = Path(image_id["path"])
with Ext4Image(image, expected_uuid=MANIFEST["source_image_uuid"]) as fs:
    fpga_entries = [e.name for e in fs.listdir("/usr/src/kernel/drivers/fpga") if e.name not in {".", ".."}]
    dts_files = [path for path, inode in fs.walk("/usr/src/kernel/arch/arm/boot/dts", max_directories=30_000) if path.endswith((".dts", ".dtsi", ".dtb"))]
    ext4_boot_entries = [e.name for e in fs.listdir("/boot") if e.name not in {".", "..", "REVISION"}]

config_path = EXTRACTED / "system/kernel/.config"
config_lines = config_path.read_text(encoding="utf-8", errors="replace").splitlines()
config_re = re.compile(r"^(CONFIG_(?:ARCH_ZYNQ|CMA|CMA_SIZE_MBYTES|FPGA|FPGA_MGR_ZYNQ_FPGA|FPGA_MGR_ZYNQ_AFI_FPGA|FPGA_BRIDGE|FPGA_REGION|OF_FPGA_REGION))=")
config_evidence = [{"line": n, "text": line} for n, line in enumerate(config_lines, 1) if config_re.search(line)]
builtin_path = EXTRACTED / "system/kernel/modules.builtin"
builtin_lines = builtin_path.read_text(encoding="utf-8", errors="replace").splitlines()
builtin_fpga = [line for line in builtin_lines if re.search(r"fpga|zynq|xlnx", line, re.IGNORECASE)]

def record_entry(path: str) -> dict[str, object]:
    item = RECORDS[path]
    return {"image_path": item["image_path"], "extracted_path": path, "size_bytes": item["size_bytes"], "sha256": item["sha256"], "kind": item["kind"]}

result = {
    "classification": "STATIC_OFFLINE_ROOTFS_AUDIT_NOT_RUNTIME_BOARD_EVIDENCE",
    "scope": {
        "mount": False,
        "journal_replay": False,
        "network_board_access": False,
        "image_code_executed": False,
        "restricted_raw_read": False,
        "REVISION_contents_read": False,
        "ext4_interpretation": "on-disk static view only",
    },
    "image_identity": image_id,
    "ext4": MANIFEST["filesystem"],
    "pynq_distribution": {
        "metadata": metadata,
        "version_source": "image-local dist-info/METADATA",
        "wheel_record_audit": {
            "scope": "all hashed .py rows under pynq/ in the image-local RECORD",
            "verified_rows": verified,
            "mismatches": mismatches,
            "missing_extracted_rows": missing,
            "result": "PASS" if verified > 0 and not mismatches and not missing else "FAIL",
            "interpretation": "Extracted package .py bytes match this image's local RECORD. This does not authenticate the upstream wheel or exclude downstream changes already captured in METADATA/RECORD.",
        },
        "source_directory": str((EXTRACTED / "pynq").resolve()),
        "source_module_note": "Installed module is pynq.pl_server.xrt_device; no pynq/pl_server/xrt.py file was present in extracted Python sources.",
    },
    "extracted_artifacts": {
        key: record_entry(path) for key, path in {
            "boot_py": "extracted/boot/BOOT.PY",
            "overlay_py": "extracted/pynq/overlay.py",
            "bitstream_py": "extracted/pynq/bitstream.py",
            "buffer_py": "extracted/pynq/buffer.py",
            "registers_py": "extracted/pynq/registers.py",
            "mmio_py": "extracted/pynq/mmio.py",
            "device_py": "extracted/pynq/pl_server/device.py",
            "embedded_device_py": "extracted/pynq/pl_server/embedded_device.py",
            "xrt_device_py": "extracted/pynq/pl_server/xrt_device.py",
            "base_bit": "extracted/pynq/overlays/base/base.bit",
            "base_hwh": "extracted/pynq/overlays/base/base.hwh",
            "base_tcl": "extracted/pynq/overlays/base/base.tcl",
            "pyxrt_extension": "extracted/system/usr/lib/python3.10/pyxrt.so",
            "libcma_header": "extracted/system/usr/include/libxlnk_cma.h",
            "libcma_binary": "extracted/system/usr/lib/libcma.so",
            "xrt_version_header": "extracted/system/usr/include/xrt/version.h",
            "xrt_core_binary": "extracted/system/usr/lib/libxrt_core.so.2.17.0",
            "xrt_coreutil_binary": "extracted/system/usr/lib/libxrt_coreutil.so.2.17.0",
            "xrt_cpp_binary": "extracted/system/usr/lib/libxrt++.so.2.17.0",
            "kernel_config": "extracted/system/kernel/.config",
            "kernel_modules_builtin": "extracted/system/kernel/modules.builtin",
            "kernel_fpga_makefile": "extracted/system/kernel/drivers/fpga/Makefile",
            "kernel_fpga_kconfig": "extracted/system/kernel/drivers/fpga/Kconfig",
        }.items()
    },
    "base_overlay": {
        "boot_py_evidence": boot_evidence,
        "bit_size_bytes": RECORDS["extracted/pynq/overlays/base/base.bit"]["size_bytes"],
        "bit_sha256": RECORDS["extracted/pynq/overlays/base/base.bit"]["sha256"],
        "hwh_size_bytes": RECORDS["extracted/pynq/overlays/base/base.hwh"]["size_bytes"],
        "hwh_sha256": RECORDS["extracted/pynq/overlays/base/base.hwh"]["sha256"],
        "ps_hp_ports": ports,
        "interpretation": "base.hwh reports 64-bit HP0-HP3 data-width parameters; base.tcl marks HP0 and HP2 enabled and HP1/HP3 disabled. HP1's 64-bit parameter is therefore metadata/config width, not proof HP1 is enabled or was used by WO-0015.",
    },
    "static_call_paths": {
        "boot_overlay": [
            {"path": "extracted/boot/BOOT.PY", "lines": [27, 29], "finding": "imports BaseOverlay and constructs it with base.bit"},
            {"path": "extracted/pynq/overlays/base/base.py", "lines": [56, 57], "finding": "BaseOverlay forwards arguments to Overlay"},
            {"path": "extracted/pynq/overlay.py", "lines": [289, 318, 353, 354, 402, 432], "finding": "Overlay default download=True; full download sets clocks then calls Bitstream/Device download"},
            {"path": "extracted/pynq/bitstream.py", "lines": [99, 118, 151, 168], "finding": "resolves the named bitstream and delegates download to its device"},
            {"path": "extracted/pynq/pl_server/embedded_device.py", "lines": [538, 539, 665, 690], "finding": "EmbeddedDevice writes fpga_manager flags/firmware then calls AXI-width setup and XRT download/post-download"},
        ],
        "ps_axi_width_and_afi": [
            {"path": "extracted/pynq/pl_server/embedded_device.py", "lines": [604, 637], "finding": "set_axi_port_width only runs its register mapping when parser.family_ps == zynq_ultra_ps_e; it does not implement Zynq-7000 PS7 AFI settings"},
            {"path": "extracted/pynq/registers.py", "lines": [94, 128, 166], "finding": "Register maps a physical address through MMIO and supports read-modify-write bit fields"},
            {"path": "extracted/pynq/mmio.py", "lines": [42, 85], "finding": "MMIO maps through Device.mmap for MEMORY_MAPPED devices"},
        ],
        "reset_boundary": [
            {"path": "extracted/pynq/pl_server/device.py", "lines": [147, 158, 383, 423], "finding": "Device.reset resets software metadata dictionaries; shutdown only accesses configured AXI shutdown-manager IPs; neither establishes absence of kernel/FPGA-manager reset side effects"},
            {"path": "extracted/pynq/pl_server/embedded_device.py", "lines": [682, 690], "finding": "writes sysfs fpga_manager firmware node, so kernel driver behavior remains in the load chain"},
        ],
        "buffer_xrt": [
            {"path": "extracted/pynq/buffer.py", "lines": [85, 128, 138, 158], "finding": "PynqBuffer.cacheable is !coherent; sync_to_device/from_device call flush/invalidate; allocate delegates to active Device"},
            {"path": "extracted/pynq/pl_server/xrt_device.py", "lines": [59, 88, 322, 346], "finding": "_xrt_allocate defaults cacheable=0 and sets coherent=False; allocate_bo then uses pyxrt normal flags unless cacheable is truthy; flush/invalidate call pyxrt BO sync to/from device"},
            {"path": "extracted/pynq/pl_server/xrt_device.py", "lines": [19, 23, 251], "finding": "ON_TARGET imports pyxrt; its Python extension is /usr/lib/python3.10/pyxrt.so in this image"},
        ],
    },
    "semantic_cache_note": "For the inspected XRT PynqBuffer path, PynqBuffer.cacheable=True is a derived property from coherent=False; the allocation argument cacheable defaults to 0, selecting pyxrt.bo.flags.normal. These are distinct meanings. sync_to_device/from_device route to XRT bo.sync TO_DEVICE/FROM_DEVICE. libcma is installed, but no libcma call appears in this inspected Python allocation/sync path; this is not a claim about all binaries or other code paths.",
    "native_source_availability": {
        "pynq_package_c_cpp_files_matching_xrt_or_cma": [],
        "usr_src_top_level": ["googletest", "gtest", "kernel"],
        "kernel_fpga_driver_source_files": [],
        "libcma_implementation_source_found": False,
        "pyxrt_extension_source_found": False,
        "available_artifacts": ["libxlnk_cma.h API declarations", "libcma.so opaque binary", "pyxrt.so opaque extension binary", "XRT 2.17.0 headers and runtime binaries", "partial kernel source/build metadata"],
        "bounded_inventory_note": "Absence means not present in inspected PYNQ package, /usr/src top-level source inventory, and kernel drivers/fpga path; binaries were not reverse engineered.",
    },    "register_literal_scan": {
        "scope": f"{len(pynq_py_files)} extracted PYNQ .py files",
        "searched_literals": list(searched),
        "hits": searched,
        "interpretation": "No direct matching PS7 AFI / FPGA_RST_CTRL address or register-name literal was found in extracted PYNQ Python sources. This does not inspect opaque ELF/kernel execution paths.",
    },
    "kernel_local_evidence": {
        "release_directory": "6.6.10-xilinx-v2024.1-g3c0eca68c652",
        "config_evidence": config_evidence,
        "builtin_fpga_zynq_lines": builtin_fpga,
        "fpga_source_directory_entries": fpga_entries,
        "fpga_driver_c_sources_present": [name for name in fpga_entries if name.endswith((".c", ".h"))],
        "arch_arm_boot_dts_source_file_count": len(dts_files),
        "ext4_boot_entries_except_excluded_name": ext4_boot_entries,
        "conclusion": "Local build config and modules.builtin indicate FPGA manager, Zynq FPGA manager, Zynq AFI FPGA, FPGA bridge/region and CMA support are built in (CMA size 128 MiB). The image's kernel source tree lacks the FPGA driver's C implementations and has no .dts/.dtsi/.dtb under arch/arm/boot/dts; there is no local source/DTB evidence to bind fpga_manager writes to a specific PS7 reset/AFI side effect.",
    },
    "unknowns_and_risks": [
        "The image is a clean-flag, no-needs_recovery static ext4 view; no journal replay or mount occurred, so only bytes currently on disk are asserted.",
        "No board runtime was observed. BOOT.PY content does not prove it ran on any boot, and no PS7 AFI/reset register value was read on hardware.",
        "The PYNQ Python sources contain no direct searched PS7 AFI / FPGA_RST_CTRL literals, but fpga_manager sysfs programming enters the kernel. The actual Zynq FPGA-manager driver C source and device tree are absent from this image view; kernel-triggered reset/AFI effects stay UNKNOWN.",
        "The local PYNQ metadata says version 3.1.1 and extracted .py files match its local RECORD. This validates internal image consistency only, not the official upstream tag or whether vendor/downstream changes were included before RECORD creation.",
        "libcma.so and pyxrt.so are binary extensions. They were hashed/extracted as opaque files and not loaded or executed; libcma implementation behavior is not established here.",
        "This parser is deliberately scoped to this MBR/ext4 image: supports only known incompat bits, refuses unknown bits, does not replay journals, and caps extent depth at 5 / file size at 512 MiB. Unsupported metadata fails closed.",
    ],
    "tests": {
        "command": "C:\\fpga\\material-counter\\.venv\\Scripts\\python.exe -B -m pytest -p no:cacheprovider -q test_ext4_ro.py",
        "result": "4 passed",
        "log": "tests-final.log",
        "edge_case": "ee_len=0x8000 is initialized 32768 blocks; only values >0x8000 mark unwritten extents",
        "red_green_logs": ["tests-red.log", "tests-ee_len-red.log", "tests-green.log"],
    },
    "extraction_manifest": "source-manifest.json",
}

out_root = RESULT_DIR / "pynq-afi-and-base-hwh-audit.json"
out_local = HERE / "pynq-static-audit.json"
serialized = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
out_root.write_text(serialized, encoding="utf-8")
out_local.write_text(serialized, encoding="utf-8")
print(json.dumps({
    "top_level_json": str(out_root),
    "rootfs_json": str(out_local),
    "pynq_version": metadata.get("Version"),
    "record_py_rows_verified": verified,
    "record_mismatches": len(mismatches),
    "record_missing": len(missing),
    "hp1": ports["HP1"],
    "kernel_fpga_c_files_present": [name for name in fpga_entries if name.endswith(".c")],
    "dtb_source_file_count": len(dts_files),
}, indent=2))