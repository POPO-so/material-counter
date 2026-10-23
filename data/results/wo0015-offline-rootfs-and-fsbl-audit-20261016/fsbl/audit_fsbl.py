"""Offline helpers for the WO-0015 FSBL image audit (host-side parsing only)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct


_OP_INFO = {
    1: ("WRITE_ZERO", 1),
    2: ("WRITE", 2),
    3: ("MASKWRITE", 3),
    4: ("MASKPOLL", 2),
    5: ("DELAY", 2),
}


def decode_table_records(blob: bytes, offset: int = 0) -> list[dict]:
    """Decode a PS7-style command table until its zero EXIT word.

    The instruction word stores the opcode in its high nibble and argument
    count in its low nibble. This is the encoding used by the audited parser
    at raw-image address 0x2b10 (base address zero).
    """
    if offset < 0 or offset % 4:
        raise ValueError("table offset must be a non-negative word boundary")
    records = []
    cursor = offset
    while True:
        if cursor + 4 > len(blob):
            raise ValueError("table is missing EXIT terminator")
        command = struct.unpack_from("<I", blob, cursor)[0]
        if command == 0:
            return records
        opcode = command >> 4
        argc = command & 0xF
        info = _OP_INFO.get(opcode)
        if info is None:
            raise ValueError(f"unknown opcode {opcode} at offset 0x{cursor:x}")
        kind, expected_argc = info
        if argc != expected_argc:
            raise ValueError(
                f"opcode {opcode} at offset 0x{cursor:x} has argc {argc}; "
                f"expected {expected_argc}"
            )
        end = cursor + 4 * (1 + argc)
        if end > len(blob):
            raise ValueError(f"truncated arguments for opcode {opcode} at offset 0x{cursor:x}")
        args = list(struct.unpack_from("<" + "I" * argc, blob, cursor + 4))
        record = {
            "command_offset": cursor,
            "command_word": command,
            "opcode": opcode,
            "argc": argc,
            "kind": kind,
            "args": args,
        }
        if opcode == 1:
            record.update(address=args[0], value=0)
        elif opcode == 2:
            record.update(address=args[0], value=args[1])
        elif opcode == 3:
            record.update(address=args[0], mask=args[1], value=args[2])
        elif opcode == 4:
            record.update(address=args[0], mask=args[1])
        elif opcode == 5:
            # The parser treats the first argument as a reference to a
            # timer/counter register and the second as a scaled delay count.
            record.update(timer_address=args[0], delay_count=args[1])
        records.append(record)
        cursor = end


def decode_table(blob: bytes, offset: int = 0) -> list[dict]:
    """Return compact operation records convenient for decoder unit tests."""
    compact = []
    for record in decode_table_records(blob, offset):
        item = {"kind": record["kind"], "opcode": record["opcode"]}
        for key in ("address", "mask", "value", "timer_address", "delay_count"):
            if key in record:
                item[key] = record[key]
        compact.append(item)
    return compact


ROOT = Path(__file__).resolve().parents[4]
RESULTS = ROOT / "data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016"
FSBL_OUT = RESULTS / "fsbl"
PROVENANCE = ROOT / "data/results/wo0015-boot-provenance-20261016"
EXTRACTED = PROVENANCE / "extracted"

POST_TABLES = [0x10778, 0x107B4, 0x107F0]
POST_SELECTOR = {
    "selection_register": 0xF8007080,
    "revision_field": "bits[31:28]",
    "branches": {"0": 0x10778, "1": 0x107B4, "other": 0x107F0},
}
PS7_TABLES = {
    "silicon_revision_0": [0x11190, 0x108A4, 0x10A24, 0x10B10, 0x1101C],
    "silicon_revision_1": [0x11F18, 0x1160C, 0x1178C, 0x11878, 0x11DA4],
    "silicon_revision_2_or_later": [0x12C90, 0x12394, 0x12514, 0x12600, 0x12B1C],
}
REVISION_ADJACENT_TABLES = [0x1082C, 0x10854, 0x1087C]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hex32(value: int) -> str:
    return f"0x{value:08X}"


def decode_catalog(blob: bytes) -> dict:
    """Decode every pointer table selected by the audited dispatchers."""
    groups = []

    def add_group(name: str, offsets: list[int], selection: str) -> None:
        tables = []
        for address in offsets:
            records = decode_table_records(blob, address)
            end = records[-1]["command_offset"] + 4 * (1 + records[-1]["argc"]) if records else address
            # The zero EXIT word immediately follows the final operation.
            end += 4
            if struct.unpack_from("<I", blob, end - 4)[0] != 0:
                raise ValueError(f"table at 0x{address:x} lost its EXIT word")
            tables.append({
                "start_address": hex32(address),
                "byte_length_including_exit": end - address,
                "operation_count": len(records),
                "exit_address": hex32(end - 4),
                "operations": records,
            })
        groups.append({"name": name, "selection": selection, "tables": tables})

    add_group("ps7_post_config_candidates", POST_TABLES,
              "0x2c48 reads F8007080[31:28]: 0 selects first, 1 second, other third")
    for revision, offsets in PS7_TABLES.items():
        add_group("ps7_initialization_" + revision, offsets,
                  "0x2cc8 selects five parser tables by F8007080[31:28]")
    add_group("revision_adjacent_tables", REVISION_ADJACENT_TABLES,
              "0x2c88 selects one table by F8007080[31:28]; exact semantic label UNKNOWN")

    direct_ops = [
        operation
        for group in groups
        for table in group["tables"]
        for operation in table["operations"]
        if operation["opcode"] in (1, 2, 3)
    ]
    writes_to = {}
    for op in direct_ops:
        writes_to.setdefault(hex32(op["address"]), 0)
        writes_to[hex32(op["address"])] += 1
    target_address_audit = {}
    for target in (0xF8009000, 0xF8009014, 0xF8000240):
        hits = []
        for group in groups:
            for table in group["tables"]:
                for operation in table["operations"]:
                    if operation.get("address") != target:
                        continue
                    hits.append({
                        "table_group": group["name"],
                        "table_start_address": table["start_address"],
                        "command_address": hex32(operation["command_offset"]),
                        "opcode": operation["opcode"],
                        "kind": operation["kind"],
                        "mask": hex32(operation["mask"]) if "mask" in operation else None,
                        "value": hex32(operation["value"]) if "value" in operation else None,
                    })
        target_address_audit[hex32(target)] = {"hit_count": len(hits), "hits": hits}
    return {
        "encoding": {
            "command_word": "little-endian uint32",
            "opcode": "command_word >> 4 (high nibble)",
            "argument_count": "command_word & 0xF (low nibble)",
            "terminator": "0x00000000 EXIT",
            "parser_entry_address": "0x00002B04",
            "parser_instruction_evidence": [
                {"address": "0x00002B10", "word": "0xE5953000", "fact": "load command word from current table pointer"},
                {"address": "0x00002B14", "word": "0xE213600F", "fact": "argument count = command & 0xF"},
                {"address": "0x00002B18", "word": "0xE1A04223", "fact": "opcode = command >> 4"},
                {"address": "0x00002C18", "word": "0xE5832000", "fact": "opcode 2 writes the second argument to address argument 0"},
                {"address": "0x00002C08", "word": "0xE5823000", "fact": "opcode 3 writes calculated masked value to address argument 0"},
            ],
            "opcode_semantics": {
                "1": "WRITE_ZERO(address); store zero through table-supplied pointer",
                "2": "WRITE(address,value); store value through table-supplied pointer",
                "3": "MASKWRITE(address,mask,value); (old & ~mask) | (value & mask), then store through table-supplied pointer",
                "4": "MASKPOLL(address,mask); reads/polls, no store in handler",
                "5": "DELAY(timer_address,delay_count); timer/counter polling handler",
            },
        },
        "post_config_selector": POST_SELECTOR,
        "groups": groups,
        "decoded_operation_count": sum(len(t["operations"]) for g in groups for t in g["tables"]),
        "decoded_direct_write_count": len(direct_ops),
        "direct_write_target_frequency": writes_to,
        "target_search": {
            "0xF8009000": sum(1 for x in direct_ops if x["address"] == 0xF8009000),
            "0xF8009014": sum(1 for x in direct_ops if x["address"] == 0xF8009014),
            "scope": "decoded direct address arguments only; does not resolve non-table runtime pointers or unrelated code paths",
        },
        "target_address_audit": target_address_audit,
        "indirect_write_binding": {
            "parser": "0x2B04",
            "command_read_and_opcode_arg_split": "0x2B10 loads table word; 0x2B14 extracts low-nibble argc; 0x2B18 extracts high-nibble opcode",
            "opcode_1_handler": "0x2C28 uses arg[0] as destination and stores zero",
            "opcode_2_handler": "0x2C18 uses arg[0] as destination and arg[1] as stored value",
            "opcode_3_handler": "0x2BEC uses arg[0] as destination, arg[1] as mask, arg[2] as new value; EOR/AND/EOR at 0x2BF8-0x2C08 then STR through arg[0]",
            "table_targets_resolved": "All target address arguments are enumerated per candidate table in groups[].tables[].operations[].address; these records describe candidate commands and do not imply execution.",
            "fpga_rst_ctrl_bit21": "The opcode-3 table command at 0xF8000240 has mask 0xFFFFFFFF and value 0; bit 21 is not an HP1 status observation.",
        },
    }


def _read_c_string(blob: bytes, offset: int) -> tuple[str, int]:
    end = blob.find(b"\0", offset)
    if end < 0:
        raise ValueError("unterminated FDT string")
    return blob[offset:end].decode("utf-8", errors="strict"), end + 1


def parse_fdt(blob: bytes) -> tuple[dict, list[tuple[int, int]], dict]:
    """Parse the FDT structure block into an ordered tree and reservations."""
    if len(blob) < 40:
        raise ValueError("FDT header is truncated")
    header = struct.unpack_from(">10I", blob, 0)
    (magic, total, off_struct, off_strings, off_reserve, version, last_comp,
     boot_cpuid, size_strings, size_struct) = header
    if magic != 0xD00DFEED or total > len(blob) or total < 40:
        raise ValueError("invalid FDT magic or total size")
    if off_struct + size_struct > total or off_strings + size_strings > total:
        raise ValueError("FDT block range exceeds total size")
    strings = blob[off_strings:off_strings + size_strings]
    structure = blob[off_struct:off_struct + size_struct]
    reservations = []
    reserve_cursor = off_reserve
    while reserve_cursor + 16 <= total:
        address, size = struct.unpack_from(">QQ", blob, reserve_cursor)
        reserve_cursor += 16
        if address == 0 and size == 0:
            break
        reservations.append((address, size))

    root = {"name": "", "properties": [], "children": []}
    stack = []
    cursor = 0
    ended = False
    while cursor + 4 <= len(structure):
        token = struct.unpack_from(">I", structure, cursor)[0]
        cursor += 4
        if token == 1:  # FDT_BEGIN_NODE
            name, cursor = _read_c_string(structure, cursor)
            cursor = (cursor + 3) & ~3
            node = {"name": name, "properties": [], "children": []}
            if stack:
                stack[-1]["children"].append(node)
            elif root["children"] or root["properties"]:
                raise ValueError("multiple FDT root nodes")
            else:
                root = node
            stack.append(node)
        elif token == 2:  # FDT_END_NODE
            if not stack:
                raise ValueError("unbalanced FDT_END_NODE")
            stack.pop()
        elif token == 3:  # FDT_PROP
            if not stack or cursor + 8 > len(structure):
                raise ValueError("orphan or truncated FDT_PROP")
            length, nameoff = struct.unpack_from(">II", structure, cursor)
            cursor += 8
            if nameoff >= len(strings) or cursor + length > len(structure):
                raise ValueError("FDT property range/name offset invalid")
            name, _ = _read_c_string(strings, nameoff)
            value = bytes(structure[cursor:cursor + length])
            cursor = (cursor + length + 3) & ~3
            stack[-1]["properties"].append({"name": name, "raw": value})
        elif token == 4:  # FDT_NOP
            continue
        elif token == 9:  # FDT_END
            ended = True
            break
        else:
            raise ValueError(f"unknown FDT token 0x{token:08x}")
    if stack or not ended:
        raise ValueError("FDT structure is incomplete")
    metadata = {
        "total_size": total,
        "version": version,
        "last_compatible_version": last_comp,
        "boot_cpu_id": boot_cpuid,
        "structure_size": size_struct,
        "strings_size": size_strings,
    }
    return root, reservations, metadata


def _dt_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


_DT_SECRET_PROPERTIES = {"serial-number", "mac-address", "local-mac-address", "ethaddr", "serial"}


def _format_property(name: str, raw: bytes) -> str:
    if not raw:
        return f"{name};"
    if name in _DT_SECRET_PROPERTIES:
        return f"{name} = <REDACTED>;"
    if raw.endswith(b"\0"):
        try:
            strings = raw[:-1].decode("utf-8", errors="strict").split("\0")
            no_empty_segments = len(raw) == 1 or all(strings)
            if no_empty_segments and all(all(ch.isprintable() for ch in s) for s in strings):
                return f"{name} = " + ", ".join(_dt_string(s) for s in strings) + ";"
        except UnicodeDecodeError:
            pass
    if len(raw) % 4 == 0:
        cells = struct.unpack(">" + "I" * (len(raw) // 4), raw)
        return f"{name} = <" + " ".join(f"0x{x:08x}" for x in cells) + ">;"
    return f"{name} = [" + " ".join(f"{x:02x}" for x in raw) + "];"


def render_dts(root: dict, reservations: list[tuple[int, int]]) -> str:
    lines = ["/dts-v1/;", ""]
    for address, size in reservations:
        lines.append(f"/memreserve/ 0x{address:x} 0x{size:x};")
    if reservations:
        lines.append("")

    def emit(node: dict, depth: int, is_root: bool = False) -> None:
        indent = "\t" * depth
        name = "/" if is_root else node["name"]
        lines.append(f"{indent}{name} {{")
        for prop in node["properties"]:
            lines.append(f"{indent}\t" + _format_property(prop["name"], prop["raw"]))
        if node["properties"] and node["children"]:
            lines.append("")
        for child in node["children"]:
            emit(child, depth + 1)
        lines.append(f"{indent}}};")
        if depth == 0:
            lines.append("")

    emit(root, 0, True)
    return "\n".join(lines)


_ENV_EXACT_ALLOWLIST = {
    "bootcmd", "bootdelay", "baudrate", "loadaddr", "boot_targets",
    "distro_bootcmd", "bootcmd_mmc0", "bootcmd_mmc1", "bootcmd_qspi",
    "bootcmd_pxe", "bootcmd_dhcp", "boot_scripts", "boot_prefixes",
    "scriptaddr", "fdt_addr", "fdt_addr_r", "kernel_addr_r", "ramdisk_addr_r",
    "fitimage_name", "kernel_image", "fdtfile", "fpga", "fpga_image",
    "fpga_load", "afi", "afi0", "afi1",
}


def audit_uboot_env(blob: bytes) -> list[dict]:
    """Extract only explicitly allowlisted boot/FPGA/AFI keys from NUL strings."""
    entries = []
    cursor = 0
    seen = set()
    sensitive = ("password", "passwd", "credential", "token", "secret", "revision")
    for item in blob.split(b"\0"):
        start = cursor
        cursor += len(item) + 1
        if b"=" not in item or len(item) > 4096:
            continue
        key_b, value_b = item.split(b"=", 1)
        try:
            key = key_b.decode("ascii")
            value = value_b.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        if key not in _ENV_EXACT_ALLOWLIST or key in seen:
            continue
        if any(term in key.lower() or term in value.lower() for term in sensitive):
            continue
        if not key or not value.isprintable():
            continue
        seen.add(key)
        entries.append({"offset": f"0x{start:x}", "key": key, "value": value})
    return entries


def _node_counts(node: dict) -> tuple[int, int]:
    nodes = 1
    props = len(node["properties"])
    for child in node["children"]:
        child_nodes, child_props = _node_counts(child)
        nodes += child_nodes
        props += child_props
    return nodes, props


def _fdt_prop_value(name: str, raw: bytes):
    if not raw:
        return True
    if raw.endswith(b"\0"):
        try:
            parts = raw[:-1].decode("utf-8", errors="strict").split("\0")
            if (len(raw) == 1 or all(parts)) and all(all(ch.isprintable() for ch in x) for x in parts):
                return parts
        except UnicodeDecodeError:
            pass
    if len(raw) % 4 == 0:
        return [hex32(x) for x in struct.unpack(">" + "I" * (len(raw) // 4), raw)]
    return raw.hex()


def relevant_dtb_nodes(root: dict) -> dict:
    nodes = []
    phandles = {}
    afi_name_hits = []

    def visit(node: dict, parent: str) -> None:
        path = (parent.rstrip("/") + "/" + node["name"]) if parent != "/" else "/" + node["name"]
        if not node["name"]:
            path = "/"
        props = {p["name"]: p["raw"] for p in node["properties"]}
        phandle = props.get("phandle", props.get("linux,phandle"))
        if phandle and len(phandle) == 4:
            phandles[struct.unpack(">I", phandle)[0]] = path
        comp = _fdt_prop_value("compatible", props.get("compatible", b""))
        comp_list = comp if isinstance(comp, list) else []
        selected = (
            "fpga" in node["name"].lower()
            or "slcr" in node["name"].lower()
            or "devcfg" in node["name"].lower()
            or any(token in s.lower() for s in comp_list for token in ("fpga", "slcr", "devcfg"))
            or "fpga-mgr" in props
        )
        if selected:
            fields = ("compatible", "reg", "status", "phandle", "linux,phandle", "fpga-mgr", "#address-cells", "#size-cells", "ranges")
            record = {"path": path, "properties": {k: _fdt_prop_value(k, props[k]) for k in fields if k in props}}
            if "fpga-mgr" in props and len(props["fpga-mgr"]) == 4:
                target = struct.unpack(">I", props["fpga-mgr"])[0]
                record["fpga_mgr_resolves_to"] = phandles.get(target, "UNRESOLVED_AT_NODE_SCAN")
                record["fpga_mgr_phandle"] = hex32(target)
            nodes.append(record)
        for prop in node["properties"]:
            marker = (path + " " + prop["name"] + " " + prop["raw"].decode("latin1", errors="ignore")).lower()
            if "afi" in marker or "xlnx,afi-fpga" in marker:
                afi_name_hits.append({"path": path, "property": prop["name"], "value": _fdt_prop_value(prop["name"], prop["raw"])})
        for child in node["children"]:
            visit(child, path)

    visit(root, "")
    # Resolve phandles now that the complete tree has been visited.
    by_handle = {}
    def collect_handles(node: dict, parent: str) -> None:
        path = (parent.rstrip("/") + "/" + node["name"]) if parent != "/" else "/" + node["name"]
        if not node["name"]:
            path = "/"
        props = {p["name"]: p["raw"] for p in node["properties"]}
        phandle = props.get("phandle", props.get("linux,phandle"))
        if phandle and len(phandle) == 4:
            by_handle[struct.unpack(">I", phandle)[0]] = path
        for child in node["children"]:
            collect_handles(child, path)
    collect_handles(root, "")
    for record in nodes:
        if "fpga_mgr_phandle" in record:
            handle = int(record["fpga_mgr_phandle"], 16)
            record["fpga_mgr_resolves_to"] = by_handle.get(handle, "UNRESOLVED")
    return {
        "nodes": nodes,
        "afi_name_property_hits": afi_name_hits,
        "search_interpretation": "FDT names, property names and printable property values only; an absent AFI label does not prove absence of runtime register writes",
    }


def write_audit_outputs() -> dict:
    fsbl_path = EXTRACTED / "partition-00.raw.bin"
    uboot_path = EXTRACTED / "partition-01.raw.bin"
    dtb_path = EXTRACTED / "partition-02.raw.bin"
    boot_info_path = PROVENANCE / "BOOT.BIN-analysis.json"
    fsbl = fsbl_path.read_bytes()
    uboot = uboot_path.read_bytes()
    dtb = dtb_path.read_bytes()
    boot_info = json.loads(boot_info_path.read_text(encoding="utf-8"))

    table_data = decode_catalog(fsbl)
    tables_path = FSBL_OUT / "decoded-tables.json"
    tables_path.write_text(json.dumps(table_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    root, reservations, fdt_metadata = parse_fdt(dtb)
    dts_path = FSBL_OUT / "system.dts"
    dts_path.write_text(render_dts(root, reservations), encoding="utf-8")
    node_count, property_count = _node_counts(root)
    relevant_nodes = relevant_dtb_nodes(root)

    env_entries = audit_uboot_env(uboot)
    (FSBL_OUT / "uboot-boot-env-allowlist.json").write_text(
        json.dumps({
            "source_partition_sha256": sha256(uboot),
            "source_partition_bytes": len(uboot),
            "method": "NUL-delimited printable strings; exact boot/FPGA/AFI key allowlist only; first occurrence per key",
            "entries": env_entries,
            "limitations": [
                "Static strings do not establish which environment was selected or whether it was modified at runtime.",
                "Only the allowlist is reported; this is not a complete U-Boot environment dump.",
            ],
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    partitions = []
    for item in boot_info.get("partitions", []):
        partitions.append({
            "index": item["index"],
            "header_image_name": item.get("image_name"),
            "data_nbytes": item.get("data_nbytes"),
            "load_address": item.get("load_address"),
            "execution_address": item.get("execution_address"),
            "attributes_hex": item.get("attributes_hex"),
            "sha256": item.get("sha256_total_partition"),
            "checksum_valid": item.get("checksum_valid"),
        })

    audit = {
        "audit_kind": "offline static ARM/FDT/U-Boot default-environment audit",
        "execution_performed": False,
        "disassembly_method": {
            "tool": "GNU arm-none-eabi-objdump 2.32.0.20190204",
            "arguments": "-D -b binary -marm --adjust-vma=0",
            "address_model": "raw file offset equals virtual address because base VMA is zero",
            "file": "data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016/fsbl/partition-00.arm-base0.disasm.txt",
        },
        "original_artifact_filename_search": {
            "roots": ["C:/fpga", "C:/Users/POP/Desktop/FPGA赛"],
            "method": "filename-only rg --files filters for FSBL ELF, BIF, MAP and serial/UART/boot logs; restricted-raw, REVISION and credential-named paths excluded",
            "result": "no original FSBL ELF, BIF or serial/UART/boot log filename found; six matching MAP files were unrelated Vivado incremental-synthesis maps",
            "content_read": False,
        },
        "input_hashes": {
            "partition_00_raw_fsbl_payload": {"bytes": len(fsbl), "sha256": sha256(fsbl), "header_name": "zynq_fsbl.elf", "elf_magic_present": fsbl[:4] == b"\x7fELF"},
            "partition_01_uboot_payload": {"bytes": len(uboot), "sha256": sha256(uboot)},
            "partition_02_dtb": {"bytes": len(dtb), "sha256": sha256(dtb)},
        },
        "boot_header": {
            "load_address": boot_info.get("fsbl_load_address"),
            "entry_address": boot_info.get("fsbl_entry_address"),
            "checksum_valid": boot_info.get("boot_header_checksum_valid"),
            "authenticity_statement": boot_info.get("authenticity"),
            "execution_evidence": boot_info.get("execution_evidence"),
            "partitions": partitions,
            "bootrom_register_initialization_pairs": boot_info.get("bootrom_register_initialization_pairs"),
            "static_no_pl_partition": all((int(x["attributes_hex"], 16) & 0x20) == 0 for x in partitions),
            "pl_mask_source": "0x20 is corroborated by installed SDK template; source identity is not established",
        },
        "artifacts": {
            "full_arm_base0_disassembly": "data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016/fsbl/partition-00.arm-base0.disasm.txt",
            "decoded_tables": "data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016/fsbl/decoded-tables.json",
            "readable_dts": "data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016/fsbl/system.dts",
            "uboot_allowlisted_env": "data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016/fsbl/uboot-boot-env-allowlist.json",
            "detailed_findings": "data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016/fsbl/findings.md",
            "archive_search": "data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016/fsbl/archive-search.json",
        },
        "table_summary": {
            "table_groups": len(table_data["groups"]),
            "table_count": sum(len(g["tables"]) for g in table_data["groups"]),
            "operation_count": table_data["decoded_operation_count"],
            "direct_write_count": table_data["decoded_direct_write_count"],
            "post_config_candidates": [hex32(x) for x in POST_TABLES],
        },
        "control_flow": {
            "bitstream_flag_static_mapping": {
                "numeric_address": "0x000193ED",
                "symbol_name": "BitstreamFlag (INFERRED from installed SDK template; not an ELF symbol)",
                "direct_instruction_xrefs": ["0x00000AC0 STRB zero", "0x00000BAC STRB one", "0x000012DC LDRB"],
            },
            "startup_bss_clear": {
                "startup_path": "0x00000000 branches to 0x0000012C; 0x00000398 branches to 0x00004438; 0x00004438 calls 0x000071AC then clears BSS",
                "range_start": "0x00019040",
                "range_end_exclusive": "0x0001B400",
                "flag_address_inside_range": True,
                "effect": "word-wise zero stores cover word 0x193EC containing byte 0x193ED before later code",
            },
            "partition_attribute_branch": {
                "routine_address": "0x00000AA8",
                "caller_address": "0x000102A4",
                "flag_reset_instruction": "0x00000AC0: STRB r3,[r4,#0x391], r4=0x1905C, r3=0 => [0x193ED]=0",
                "partition_attribute_load": "0x00000B64 loads [r9,#0x58] into fp",
                "pl_branch": "0x00000B80 TST fp,#0x20; nonzero falls through 0xB98; 0xB94 BEQ 0xBB4 takes non-PL path",
                "pl_flag_set_instruction": "0x00000BAC: STRB r2,[r4,#0x391] after r2=1 on PL path",
                "ps_path": "0x00000BB4 tests bit 0x10; stores other context bytes at 0x193EB/0x193EE/0x193EC, with no store to 0x193ED",
                "actual_image_partition_attributes": [x["attributes_hex"] for x in partitions],
                "static_image_path_conclusion": "all three BOOT.BIN partition attributes have bit 0x20 clear; on the normal parser path none takes the PL flag-set path, so flag remains 0 after reset and partition scan (INFERRED static control-flow result; not target execution)",
            },
            "handoff_branch": {
                "caller": "0x000102A8 calls 0x000012CC after 0x000102A4 calls 0x00000AA8",
                "flag_read": "0x000012CC/0x000012D4 construct address 0x193ED; 0x000012DC LDRB; 0x000012E0 CMP r3,#0",
                "zero_branch": "0x000012E4 BNE 0x00001350 is not taken when flag is 0; flow skips post-config call and continues at 0x000012E8",
                "post_config_branch": "0x00001350 BL 0x00002C48 is reached only via the nonzero branch at 0x12E4",
                "handoff_transfer": "on the nonzero handoff-address path, 0x00001334 BL 0x000103E0; helper 0x103E0 copies r0 to LR and transfers with BX LR at 0x10400. The binary checks r4 at 0x1310/0x1320 and takes a separate zero-address path; BOOT header gives U-Boot execution address 0x04000000 but does not prove LoadBootImage returned it at runtime.",
                "addressed_machine_code_evidence": [
                    {"address": "0x00000AC0", "word": "0xE5C43391", "fact": "STRB r3,[r4,#0x391]; r4=0x1905C and r3=0 at this point, so store byte zero at 0x193ED"},
                    {"address": "0x00000B80", "word": "0xE31B0020", "fact": "TST fp,#0x20 on partition-header attributes"},
                    {"address": "0x00000B94", "word": "0x0A000006", "fact": "BEQ 0xBB4 routes bit-0x20-clear partitions away from PL flag-set block"},
                    {"address": "0x00000BAC", "word": "0xE5C42391", "fact": "STRB r2,[r4,#0x391]; on PL path r2 was set to 1"},
                    {"address": "0x00000BB4", "word": "0xE31B0010", "fact": "PS path tests bit 0x10; its stores do not target 0x193ED"},
                    {"address": "0x00004438", "word": "0xEB000B5B", "fact": "calls early CPU/MMU setup before BSS clear loop"},
                    {"address": "0x00004450", "word": "0xE4810004", "fact": "zero-word store and post-increment in first BSS-clear range"},
                    {"address": "0x00004468", "word": "0xE4810004", "fact": "zero-word store and post-increment in second BSS-clear range"},
                    {"address": "0x000012DC", "word": "0xE5D33000", "fact": "LDRB r3,[0x193ED]"},
                    {"address": "0x000012E4", "word": "0x1A000019", "fact": "BNE 0x1350 gates handoff-time post-config on nonzero flag"},
                    {"address": "0x00001334", "word": "0xEB003C29", "fact": "BL 0x103E0 uses nonzero handoff address after zero-flag path"}
                ],
            },
            "jtag_branch": {
                "bootmode_dispatch": "0x000101C4 loads BOOT_MODE, masks low 3 bits; selector table at 0x101E0 maps value 0 to 0x102E8",
                "machine_test": "0x000102F0 loads 0xF800700C; 0x000102F4 TST bit 0x4; 0x000102F8 BNE 0x103A4 calls 0x2C48 there and rejoins at 0x102FC; clear bit skips that call",
                "interpretation": "SDK template labels selector 0 JTAG and bit 0x4 PCFG_DONE; template association only, and runtime boot mode/PCFG_DONE are UNKNOWN",
            },
            "evidence_classes": {
                "MACHINE_CODE_FACT": "instruction words and control-flow targets decoded from the raw payload at base 0",
                "IMAGE_HEADER_FACT": "partition attributes and execution addresses parsed from local BOOT.BIN analysis",
                "INFERRED": "BitstreamFlag/FsblHandoff/LoadBootImage names, partition attribute semantics, and normal-path outcome based on static inputs plus SDK template cross-reference",
                "UNKNOWN": "whether this BOOT image ever executed, actual runtime flag/MMIO values, alternate boot image or mutated partition headers, aliases through unrelated runtime pointers, and selected U-Boot environment",
            },
        },
        "dtb": {
            "partition_bytes": len(dtb),
            "metadata": fdt_metadata,
            "trailing_partition_padding_bytes": len(dtb) - fdt_metadata["total_size"],
            "node_count": node_count,
            "property_count": property_count,
            "reservation_count": len(reservations),
            "secret_identity_properties_redacted": sorted(_DT_SECRET_PROPERTIES),
            "rendering": "readable DTS-like text; scalar arrays use big-endian 32-bit cells; unclassified non-cell data uses bytes",
            "relevant_fpga_register_nodes": relevant_nodes,
        },
        "uboot_env": {
            "entries_reported": len(env_entries),
            "report_scope": "exact boot/FPGA/AFI allowlist; other environment variables deliberately omitted",
            "possible_register_initialization": "UNKNOWN: BootGen analysis contains no BootROM init pairs; decoded FSBL table addresses are enumerated separately; U-Boot default strings do not prove runtime MMIO behavior, and this audit does not establish absence of all code-driven or indirect U-Boot writes.",
        },
        "limitations": [
            "Header image_name says zynq_fsbl.elf, but raw extracted payload begins with ARM branch instructions and lacks ELF magic; do not treat the image name as ELF/source identity.",
            "SDK source excerpts are installed 2026.1 reference templates and not proven to be the source of this raw image.",
            "Static analysis only: no target execution, board, SSH, MMIO, mount, or hardware-state claim.",
            "Full indirect write behavior outside decoded table commands is not proven absent.",
        ],
    }
    out_path = RESULTS / "fsbl-disassembly-and-dtb-audit.json"
    out_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return audit


if __name__ == "__main__":
    report = write_audit_outputs()
    print(json.dumps({
        "output": "fsbl-disassembly-and-dtb-audit.json",
        "table_count": report["table_summary"]["table_count"],
        "operation_count": report["table_summary"]["operation_count"],
        "dtb_nodes": report["dtb"]["node_count"],
        "dtb_properties": report["dtb"]["property_count"],
        "uboot_env_allowlisted": report["uboot_env"]["entries_reported"],
    }, ensure_ascii=False))
