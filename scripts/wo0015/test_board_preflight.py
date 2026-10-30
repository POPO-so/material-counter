from __future__ import annotations

import hashlib
import json
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import board_preflight as preflight


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes())


def _module(root: ET.Element, name: str, params: dict[str, str] | None = None,
            interfaces: dict[str, str] | None = None, *, modtype: str = "") -> None:
    attributes = {"INSTANCE": name}
    if modtype:
        attributes["MODTYPE"] = modtype
    module = ET.SubElement(root, "MODULE", attributes)
    if params:
        parameters = ET.SubElement(module, "PARAMETERS")
        for key, value in params.items():
            ET.SubElement(parameters, "PARAMETER", {"NAME": key, "VALUE": value})
    if interfaces:
        for interface, bus in interfaces.items():
            ET.SubElement(module, "BUSINTERFACE", {
                "NAME": interface,
                "BUSNAME": bus,
                "TYPE": "MASTER" if interface.startswith(("M_", "m_")) else "SLAVE",
            })


def _add_bus_params(module: ET.Element, interface: str, **params: str) -> None:
    bus = next(node for node in module.iter()
               if node.tag == "BUSINTERFACE" and node.attrib.get("NAME") == interface)
    for name, value in params.items():
        ET.SubElement(bus, "PARAMETER", {"NAME": name, "VALUE": value})


def _hwh_bytes(*, include_s2mm: str = "1",
               pipeline_modtype: str = "axis_dma_gray_s2mm_pipeline_bd") -> bytes:
    root = ET.Element("SYSTEM")
    dma = {
        "C_INCLUDE_MM2S": "1",
        "C_INCLUDE_S2MM": include_s2mm,
        "C_INCLUDE_SG": "0",
        "C_MICRO_DMA": "0",
        "C_SG_LENGTH_WIDTH": "18",
        "C_M_AXI_MM2S_ADDR_WIDTH": "32",
        "C_M_AXI_S2MM_ADDR_WIDTH": "32",
        "C_M_AXI_MM2S_DATA_WIDTH": "64",
        "C_M_AXI_S2MM_DATA_WIDTH": "32",
        "C_M_AXIS_MM2S_TDATA_WIDTH": "32",
        "C_S_AXIS_S2MM_TDATA_WIDTH": "32",
        "C_INCLUDE_MM2S_DRE": "0",
        "C_INCLUDE_S2MM_DRE": "0",
    }
    _module(root, "axi_dma_0", dma, {
        "M_AXI_MM2S": "dma_mm2s",
        "M_AXI_S2MM": "dma_s2mm",
        "S_AXIS_S2MM": "stream_s2mm",
    })
    _module(root, "hp0_protocol", interfaces={"S_AXI": "dma_mm2s", "M_AXI": "hp0"})
    _module(root, "hp1_fabric", {"NUM_SI": "1", "NUM_MI": "1"},
            {"S00_AXI": "dma_s2mm", "M00_AXI": "hp1"})
    _module(root, "image_pipeline", interfaces={"m_axis": "stream_s2mm"},
            modtype=pipeline_modtype)
    _module(root, "ps7", {
        "PCW_FPGA0_PERIPHERAL_FREQMHZ": "50",
        "PCW_USE_S_AXI_HP0": "1",
        "PCW_S_AXI_HP0_DATA_WIDTH": "64",
        "PCW_USE_S_AXI_HP1": "1",
        "PCW_S_AXI_HP1_DATA_WIDTH": "32",
    }, {"S_AXI_HP0": "hp0", "S_AXI_HP1": "hp1"})
    for module_name, widths in {
        "hp1_fabric": {"S00_AXI": "32", "M00_AXI": "32"},
        "ps7": {"S_AXI_HP1": "32"},
    }.items():
        module = next(node for node in root.iter()
                      if node.tag == "MODULE" and node.attrib.get("INSTANCE") == module_name)
        for bus in module.iter():
            if bus.tag == "BUSINTERFACE" and bus.attrib.get("NAME") in widths:
                bus.attrib["DATAWIDTH"] = widths[bus.attrib["NAME"]]
    for instance, base, high, interface, block in (
        ("axi_dma_0", "0x40400000", "0x4040FFFF", "S_AXI_LITE", "Reg"),
        ("image_pipeline", "0x43C20000", "0x43C2FFFF", "s_axi", "reg0"),
        ("ps7", "0x00000000", "0x1FFFFFFF", "S_AXI_HP0", "HP0_DDR_LOWOCM"),
        ("ps7", "0x00000000", "0x1FFFFFFF", "S_AXI_HP1", "HP1_DDR_LOWOCM"),
    ):
        ET.SubElement(root, "MEMRANGE", {
            "INSTANCE": instance, "BASEVALUE": base, "HIGHVALUE": high,
            "SLAVEBUSINTERFACE": interface, "ADDRESSBLOCK": block,
            "MEMTYPE": "REGISTER" if instance != "ps7" else "MEMORY",
        })
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _packet(frame_id: int, *, checkerboard: bool = True) -> tuple[bytes, bytes]:
    pixels = preflight.PIXEL_COUNT
    payload = bytearray()
    binary = bytearray()
    for index in range(pixels):
        white = checkerboard and ((index + frame_id) & 1) == 0
        value = 255 if white else 0
        payload.append(value)
        binary.append(value)
    header = struct.pack("<IIII", preflight.PACKET_MAGIC,
                         (preflight.HEIGHT << 16) | preflight.WIDTH,
                         len(payload), frame_id)
    trailer = struct.pack("<IIII", preflight.TRAILER_MAGIC, frame_id,
                          sum(payload) & 0xFFFFFFFF, preflight.TRAILER_END)
    return header + payload + trailer, bytes(binary)


def make_manifest(tmp_path: Path, *, frame_count: int = 3) -> tuple[Path, dict]:
    bit_path = tmp_path / "wo0015.bit"
    hwh_path = tmp_path / "wo0015.hwh"
    bit_path.write_bytes(b"mock bitstream bytes")
    hwh_path.write_bytes(_hwh_bytes())

    frames = []
    for frame_id in range(1, frame_count + 1):
        packet, binary = _packet(frame_id)
        packet_path = tmp_path / f"frame-{frame_id}.packet"
        binary_path = tmp_path / f"frame-{frame_id}.bin"
        packet_path.write_bytes(packet)
        binary_path.write_bytes(binary)
        frames.append({
            "frame_id": frame_id,
            "packet_path": packet_path.name,
            "packet_sha256": file_sha256(packet_path),
            "binary_path": binary_path.name,
            "binary_sha256": file_sha256(binary_path),
        })

    manifest = {
        "schema": 1,
        "work_order": "WO-0015",
        "stage": "S2MM_BINARY8_FRAME_RETURN",
        "build_id": "wo0015-fixture-build",
        "boot_id": preflight.AUTHORIZED_BOOT_ID,
        "part": preflight.EXPECTED_PART,
        "clock_mhz": 50,
        "bit_path": bit_path.name,
        "bit_sha256": file_sha256(bit_path),
        "hwh_path": hwh_path.name,
        "hwh_sha256": file_sha256(hwh_path),
        "dma_base": preflight.DMA_BASE,
        "checker_base": preflight.CHECKER_BASE,
        "contract": {
            "dma_mode": "simple",
            "include_mm2s": True,
            "include_s2mm": True,
            "include_sg": False,
            "micro_dma": False,
            "dma_length_width_bits": 18,
            "dma_addr_width_bits": 32,
            "mm2s_memory_width_bits": 64,
            "mm2s_stream_width_bits": 32,
        "s2mm_memory_width_bits": 32,
            "s2mm_stream_width_bits": 32,
            "mm2s_dre": False,
            "s2mm_dre": False,
            "hp0_enabled": True,
            "hp1_enabled": True,
            "hp0_data_width_bits": 64,
            "hp1_data_width_bits": 32,
            "hp0_range": [0, 0x20000000],
            "hp1_range": [0, 0x20000000],
            "checker_signature": preflight.PACKET_MAGIC,
            "packer_signature": preflight.PACKER_SIGNATURE,
        },
        "executor_sha256": {
            "board_once.py": file_sha256(HERE / "board_once.py"),
            "board_preflight.py": file_sha256(HERE / "board_preflight.py"),
        },
        "ready_for_board": True,
        "frames": frames,
    }
    manifest_path = tmp_path / "wo0015-manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                             encoding="utf-8")
    return manifest_path, manifest


def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def test_valid_manifest_checks_bit_hwh_packet_golden_and_1_or_3_frame_contract(tmp_path):
    manifest_path, _ = make_manifest(tmp_path)

    plan = preflight.validate_manifest(
        manifest_path,
        expected_manifest_sha256=file_sha256(manifest_path),
    )

    assert plan.boot_id == preflight.AUTHORIZED_BOOT_ID
    assert plan.dma_base == 0x40400000
    assert plan.checker_base == 0x43C20000
    assert [frame.frame_id for frame in plan.frames] == [1, 2, 3]
    assert all(len(frame.packet) == preflight.TX_BYTES == 76832 for frame in plan.frames)
    assert all(len(frame.binary) == 76800 for frame in plan.frames)


def test_manifest_digest_mismatch_is_rejected(tmp_path):
    manifest_path, _ = make_manifest(tmp_path)

    with pytest.raises(preflight.PreflightError, match="manifest SHA-256"):
        preflight.validate_manifest(manifest_path, expected_manifest_sha256="0" * 64)


def test_new_boot_requires_explicit_approved_boot_and_pinned_manifest(tmp_path):
    path, manifest = make_manifest(tmp_path)
    boot = "35983d66-bb1d-4a8a-85e5-802170825515"
    manifest["boot_id"] = boot
    _write_manifest(path, manifest)
    with pytest.raises(preflight.PreflightError, match="approved boot"):
        preflight.validate_manifest(path)
    with pytest.raises(preflight.PreflightError, match="SHA-256"):
        preflight.validate_manifest(path, approved_boot_id=boot)
    plan = preflight.validate_manifest(path, file_sha256(path), approved_boot_id=boot)
    assert plan.boot_id == boot
    with pytest.raises(preflight.PreflightError, match="approved boot"):
        preflight.validate_manifest(path, file_sha256(path),
                                    approved_boot_id="00000000-0000-0000-0000-000000000000")


def test_s2mm_disabled_in_hwh_is_rejected_even_when_hwh_hash_matches(tmp_path):
    manifest_path, manifest = make_manifest(tmp_path, frame_count=1)
    hwh_path = tmp_path / manifest["hwh_path"]
    hwh_path.write_bytes(_hwh_bytes(include_s2mm="0"))
    manifest["hwh_sha256"] = file_sha256(hwh_path)
    _write_manifest(manifest_path, manifest)

    with pytest.raises(preflight.PreflightError, match="C_INCLUDE_S2MM"):
        preflight.validate_manifest(manifest_path)


def test_binary_golden_must_match_independent_gray8_threshold(tmp_path):
    manifest_path, manifest = make_manifest(tmp_path, frame_count=1)
    binary_path = tmp_path / manifest["frames"][0]["binary_path"]
    corrupted = bytearray(binary_path.read_bytes())
    corrupted[17] ^= 0xFF
    binary_path.write_bytes(corrupted)
    manifest["frames"][0]["binary_sha256"] = file_sha256(binary_path)
    _write_manifest(manifest_path, manifest)

    with pytest.raises(preflight.PreflightError, match="independent Binary8 golden"):
        preflight.validate_manifest(manifest_path)


def test_legacy_rgb_wrapper_hwh_is_rejected(tmp_path):
    manifest_path, manifest = make_manifest(tmp_path, frame_count=1)
    hwh_path = tmp_path / manifest["hwh_path"]
    hwh_path.write_bytes(_hwh_bytes(pipeline_modtype="axis_dma_s2mm_pipeline_bd"))
    manifest["hwh_sha256"] = file_sha256(hwh_path)
    _write_manifest(manifest_path, manifest)
    with pytest.raises(preflight.PreflightError, match="not the Gray8 wrapper"):
        preflight.validate_manifest(manifest_path)


def test_legacy_rgb_packet_is_rejected_by_gray8_admission():
    legacy = struct.pack("<IIII", 0x574F3134, (240 << 16) | 320,
                         320 * 240 * 3, 1) + bytes(320 * 240 * 3) + struct.pack(
                             "<IIII", 0x454E4434, 1, 0, 0xC0DEC014)
    with pytest.raises(preflight.PreflightError, match="exactly 76832 bytes"):
        preflight._validate_packet(legacy, 1)


def test_only_one_or_three_strictly_increasing_frames_are_accepted(tmp_path):
    manifest_path, manifest = make_manifest(tmp_path, frame_count=2)
    with pytest.raises(preflight.PreflightError, match="one or three frames"):
        preflight.validate_manifest(manifest_path)

    manifest_path, manifest = make_manifest(tmp_path, frame_count=3)
    manifest["frames"][1]["frame_id"] = manifest["frames"][0]["frame_id"]
    _write_manifest(manifest_path, manifest)
    with pytest.raises(preflight.PreflightError, match="strictly increasing"):
        preflight.validate_manifest(manifest_path)


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"execute": False}, "--execute"),
        ({"authorize_stage": None}, "matching --authorize-stage"),
        ({"authorize_stage": "dma-three"}, "matching --authorize-stage"),
        ({"approve_once": False}, "--approve-once"),
        ({"manifest_sha256": "1" * 64}, "manifest SHA-256"),
        ({"boot_id": "other-boot"}, "boot_id"),
    ],
)
def test_board_execution_gate_requires_execute_stage_once_manifest_and_fixed_boot(
    tmp_path, kwargs, match
):
    manifest_path, _ = make_manifest(tmp_path, frame_count=1)
    plan = preflight.validate_manifest(manifest_path)
    args = {
        "execute": True,
        "stage": "dma-single",
        "authorize_stage": "dma-single",
        "approve_once": True,
        "manifest_sha256": plan.manifest_sha256,
        "expected_manifest_sha256": plan.manifest_sha256,
        "boot_id": preflight.AUTHORIZED_BOOT_ID,
    }
    args.update(kwargs)

    with pytest.raises(preflight.PreflightError, match=match):
        preflight.require_board_authorization(**args)


def test_matching_board_authorization_is_accepted(tmp_path):
    manifest_path, _ = make_manifest(tmp_path, frame_count=1)
    plan = preflight.validate_manifest(manifest_path)

    preflight.require_board_authorization(
        execute=True,
        stage="dma-single",
        authorize_stage="dma-single",
        approve_once=True,
        manifest_sha256=plan.manifest_sha256,
        expected_manifest_sha256=plan.manifest_sha256,
        boot_id=preflight.AUTHORIZED_BOOT_ID,
    )


def test_hwh_requires_32_bit_s2mm_memory_and_hp1_path(tmp_path):
    path = tmp_path / "valid.hwh"
    path.write_bytes(_hwh_bytes())
    preflight.validate_hwh(path)

    root = ET.fromstring(_hwh_bytes())
    dma = next(node for node in root.iter()
               if node.tag == "MODULE" and node.attrib["INSTANCE"] == "axi_dma_0")
    dma_width = next(node for node in dma.iter()
                     if node.tag == "PARAMETER" and node.attrib["NAME"] == "C_M_AXI_S2MM_DATA_WIDTH")
    dma_width.attrib["VALUE"] = "64"
    path.write_bytes(ET.tostring(root, encoding="utf-8"))
    with pytest.raises(preflight.PreflightError, match="C_M_AXI_S2MM_DATA_WIDTH"):
        preflight.validate_hwh(path)


def test_hwh_ignores_bus_interface_parameters_when_reading_ip_configuration(tmp_path):
    root = ET.fromstring(_hwh_bytes())
    dma = next(node for node in root.iter()
               if node.tag == "MODULE" and node.attrib["INSTANCE"] == "axi_dma_0")
    _add_bus_params(dma, "M_AXI_MM2S", ADDR_WIDTH="32", DATA_WIDTH="64", PROTOCOL="AXI4")
    _add_bus_params(dma, "M_AXI_S2MM", ADDR_WIDTH="32", DATA_WIDTH="32", PROTOCOL="AXI4")
    path = tmp_path / "real-shape.hwh"
    path.write_bytes(ET.tostring(root, encoding="utf-8"))

    preflight.validate_hwh(path)


def test_device_process_audit_rejects_live_fpga_mapping_and_fds(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    owner = proc / "123"
    (owner / "fd").mkdir(parents=True)
    (owner / "cmdline").write_bytes(b"python\x00worker.py\x00")
    (owner / "maps").write_text("7f000000-7f001000 rw-s 00000000 00:05 1 /dev/mem\n")
    with pytest.raises(preflight.PreflightError, match="fpga_device_mapping"):
        preflight.audit_process_ownership(proc_root=proc, current_pid=999, require_root=False)

    (owner / "maps").write_text("7f000000-7f001000 rw-p 00000000 00:00 0 [heap]\n")
    (owner / "fd" / "9").write_text("")
    monkeypatch.setattr(preflight.os, "readlink", lambda _fd: "/dev/xdevcfg")
    with pytest.raises(preflight.PreflightError, match="fpga_device_fd"):
        preflight.audit_process_ownership(proc_root=proc, current_pid=999, require_root=False)


def test_fpga_manager_requires_operating_and_zero_flags(tmp_path):
    state = tmp_path / "state"
    flags = tmp_path / "flags"
    state.write_text("operating\n")
    flags.write_text("0\n")
    assert preflight.read_fpga_manager(state, flags) == {"state": "operating", "flags": "0"}
    flags.write_text("1\n")
    with pytest.raises(preflight.PreflightError, match="flags=0"):
        preflight.read_fpga_manager(state, flags)


def test_pynq_global_state_requires_audited_shutdown_list_and_loaded_bit_identity(tmp_path):
    module_file = tmp_path / "pynq" / "__init__.py"
    module_file.parent.mkdir()
    module_file.write_text("# test module")
    state_path = module_file.parent / "pl_server" / "global_pl_state_.json"
    state_path.parent.mkdir()
    # Missing state is an explicit cold-boot case before the first Overlay.
    assert preflight.inspect_pynq_global_state(module_file, require_empty_shutdown=True)[
        "pl_state_status"] == "COLD_BOOT_NO_GLOBAL_STATE"
    bit = tmp_path / "wo15.bit"
    bit.write_bytes(b"new reviewed bit")
    state_path.write_text(json.dumps({"shutdown_ips": {}, "ip_dict": {}}))
    before = preflight.inspect_pynq_global_state(module_file, require_empty_shutdown=True)
    assert before["shutdown_ips_empty"]
    state_path.write_text(json.dumps({"shutdown_ips": {"old": "shutdown"}, "ip_dict": {}}))
    with pytest.raises(preflight.PreflightError, match="shutdown_ips"):
        preflight.inspect_pynq_global_state(module_file, require_empty_shutdown=True)
    state_path.write_text(json.dumps({
        "shutdown_ips": {}, "ip_dict": {}, "bitfile_name": str(bit),
        "bitfile_hash": hashlib.sha1(bit.read_bytes()).hexdigest(),
    }))
    loaded = preflight.inspect_pynq_global_state(
        module_file, require_empty_shutdown=True, require_loaded_bit=True,
        expected_bit_path=bit, expected_bit_sha256=file_sha256(bit))
    assert loaded["loaded_bit_sha256"] == file_sha256(bit)


def test_pynq_global_state_accepts_identical_bit_at_an_alternate_path_only_when_opted_in(tmp_path):
    module_file = tmp_path / "pynq" / "__init__.py"
    module_file.parent.mkdir()
    module_file.write_text("# test module")
    state_path = module_file.parent / "pl_server" / "global_pl_state_.json"
    state_path.parent.mkdir()
    active_bit = tmp_path / "active" / "g2-integrated.bit"
    expected_bit = tmp_path / "adapter" / "rle.bit"
    active_bit.parent.mkdir()
    expected_bit.parent.mkdir()
    content = b"the same reviewed G2 bitstream"
    active_bit.write_bytes(content)
    expected_bit.write_bytes(content)
    state_path.write_text(json.dumps({
        "shutdown_ips": {}, "ip_dict": {}, "bitfile_name": str(active_bit),
        "bitfile_hash": hashlib.sha1(content).hexdigest(),
    }))
    kwargs = dict(
        require_empty_shutdown=True,
        require_loaded_bit=True,
        expected_bit_path=expected_bit,
        expected_bit_sha256=file_sha256(expected_bit),
    )

    with pytest.raises(preflight.PreflightError, match="loaded BIT path differs"):
        preflight.inspect_pynq_global_state(module_file, **kwargs)

    loaded = preflight.inspect_pynq_global_state(
        module_file, allow_equivalent_bit_path=True, **kwargs)
    assert loaded["loaded_bit_path"] == str(active_bit.resolve())
    assert loaded["loaded_bit_path_matches_expected"] is False
    assert loaded["loaded_bit_sha256"] == file_sha256(expected_bit)

    active_bit.write_bytes(b"a different bitstream")
    state_path.write_text(json.dumps({
        "shutdown_ips": {}, "ip_dict": {}, "bitfile_name": str(active_bit),
        "bitfile_hash": hashlib.sha1(active_bit.read_bytes()).hexdigest(),
    }))
    with pytest.raises(preflight.PreflightError, match="SHA-256 differs"):
        preflight.inspect_pynq_global_state(
            module_file, allow_equivalent_bit_path=True, **kwargs)


def test_kernel_log_delta_requires_continuity_and_rejects_new_health_errors():
    before = [f"line {index}" for index in range(12)]
    assert preflight.kernel_log_delta(before, before + ["new healthy line"]) == ["new healthy line"]
    with pytest.raises(preflight.PreflightError, match="new kernel health warnings"):
        preflight.kernel_log_delta(before, before + ["new DMA error"])
    with pytest.raises(preflight.PreflightError, match="continuity lost"):
        preflight.kernel_log_delta(before, ["different"] * 20)


def test_historical_rgb_hwh_is_rejected_offline_when_present():
    path = (HERE.parents[1] / "data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh")
    if not path.is_file():
        pytest.skip("real generated HWH is not included in this checkout")
    with pytest.raises(preflight.PreflightError, match="not the Gray8 wrapper"):
        preflight.validate_hwh(path)


def test_current_gray8_hwh_passes_offline_when_present():
    path = HERE.parents[1] / "data/results/gray8-dma-20261027/export/gray8.hwh"
    if not path.is_file():
        pytest.skip("Gray8 build HWH is not included in this checkout")
    preflight.validate_hwh(path)


def test_board_preflight_cli_is_offline_and_does_not_import_pynq(tmp_path, monkeypatch):
    manifest_path, _ = make_manifest(tmp_path, frame_count=1)
    real_import = __import__

    def no_pynq_import(name, *args, **kwargs):
        if name == "pynq" or name.startswith("pynq."):
            raise AssertionError("offline preflight tried to import PYNQ")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", no_pynq_import)

    assert preflight.main(["--manifest", str(manifest_path)]) == 0
