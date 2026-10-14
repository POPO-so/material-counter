"""Offline mock checks for the bounded WO-0014 PYNQ replay helper."""
from __future__ import annotations

import builtins
import io
import hashlib
import importlib.util
import json
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("pynq_image_once.py")
SPEC = importlib.util.spec_from_file_location("pynq_image_once", SCRIPT_PATH)
assert SPEC and SPEC.loader
wo14 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wo14
SPEC.loader.exec_module(wo14)


def make_packet() -> tuple[bytes, int]:
    width, height, frame_id = 320, 240, 0x1234ABCD
    payload = bytes((index * 29 + (index // 11) * 7) & 0xFF
                   for index in range(width * height * 3))
    header = struct.pack("<IIII", 0x574F3134, (height << 16) | width,
                         len(payload), frame_id)
    trailer = struct.pack("<IIII", 0x454E4434, frame_id,
                          sum(payload) & 0xFFFFFFFF, 0xC0DEC014)
    total = 0
    for offset in range(0, len(payload), 3):
        red, green, blue = payload[offset:offset + 3]
        gray = (77 * red + 150 * green + 29 * blue) // 256
        if gray >= 128:
            total = (total + 0xFF) & 0xFFFFFFFF
    return header + payload + trailer, total


def write_hwh(path: Path, length_width: int = 18,
              checker_base: int = 0x43C20000) -> None:
    params = {
        "C_INCLUDE_MM2S": 1,
        "C_INCLUDE_S2MM": 0,
        "C_INCLUDE_SG": 0,
        "C_MICRO_DMA": 0,
        "C_SG_LENGTH_WIDTH": length_width,
        "C_M_AXI_MM2S_ADDR_WIDTH": 32,
        "C_M_AXI_MM2S_DATA_WIDTH": 64,
        "C_M_AXIS_MM2S_TDATA_WIDTH": 32,
        "C_INCLUDE_MM2S_DRE": 0,
    }
    dma_params = "".join(f'<PARAMETER NAME="{key}" VALUE="{value}"/>'
                         for key, value in params.items())
    path.write_text(
        '<SYSTEM><MODULE INSTANCE="axi_dma_0" MODTYPE="axi_dma">'
        f'{dma_params}</MODULE>'
        '<MODULE INSTANCE="image_pipeline" MODTYPE="axis_dma_image_pipeline">'
        '<PARAMETER NAME="WIDTH" VALUE="320"/>'
        '<PARAMETER NAME="HEIGHT" VALUE="240"/>'
        '<PARAMETER NAME="THRESHOLD" VALUE="128"/>'
        '</MODULE>'
        '<MODULE INSTANCE="ps7" MODTYPE="processing_system7">'
        '<PARAMETER NAME="PCW_FPGA0_PERIPHERAL_FREQMHZ" VALUE="50"/>'
        '<PARAMETER NAME="PCW_USE_S_AXI_HP0" VALUE="1"/>'
        '<PARAMETER NAME="PCW_S_AXI_HP0_DATA_WIDTH" VALUE="64"/>'
        '</MODULE>'
        '<MEMRANGE INSTANCE="axi_dma_0" BASEVALUE="0x40400000" '
        'HIGHVALUE="0x4040FFFF" MEMTYPE="REGISTER"/>'
        f'<MEMRANGE INSTANCE="image_pipeline" BASEVALUE="0x{checker_base:08X}" '
        'HIGHVALUE="0x43C2FFFF" MEMTYPE="REGISTER"/>'
        '<MEMRANGE INSTANCE="ps7" ADDRESSBLOCK="HP0_DDR_LOWOCM" '
        'BASEVALUE="0x00000000" HIGHVALUE="0x1FFFFFFF" '
        'SLAVEBUSINTERFACE="S_AXI_HP0" MEMTYPE="MEMORY"/>'
        '</SYSTEM>', encoding="utf-8")


def make_artifacts(root: Path, packet: bytes | None = None) -> tuple[Path, Path, Path, Path]:
    bit_path = root / "wo0014_image.bit"
    hwh_path = root / "wo0014_image.hwh"
    packet_path = root / "image_320x240.rgbpkt"
    manifest_path = root / "pair-manifest.json"
    bit_path.write_bytes(b"mock-bitstream-for-offline-tests")
    write_hwh(hwh_path)
    packet_path.write_bytes(packet if packet is not None else make_packet()[0])
    manifest = {
        "schema": 1,
        "build_id": "wo0014-mock-50mhz-test",
        "clock_mhz": 50,
        "part": "xc7z020clg400-1",
        "bit_path": str(bit_path.resolve()),
        "hwh_path": str(hwh_path.resolve()),
        "bit_sha256": hashlib.sha256(bit_path.read_bytes()).hexdigest(),
        "hwh_sha256": hashlib.sha256(hwh_path.read_bytes()).hexdigest(),
        "dma_base": "0x40400000",
        "checker_base": "0x43C20000",
        "dma_length_width": 18,
        "dma_mode": "simple",
        "dma_mm2s": True,
        "dma_s2mm": False,
        "dma_stream_width_bits": 32,
        "dma_addr_width_bits": 32,
        "dma_mm2s_memory_width_bits": 64,
        "dma_mm2s_dre": False,
        "dma_include_sg": False,
        "dma_micro_mode": False,
        "hp0_range_start": "0x00000000",
        "hp0_range_end_exclusive": "0x20000000",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return bit_path, hwh_path, packet_path, manifest_path


class FakeBuffer:
    def __init__(self, size: int, address: int) -> None:
        self.data = bytearray(size)
        self.device_address = address
        self.flags = SimpleNamespace(c_contiguous=True)
        self.sync_count = 0
        self.free_count = 0

    def __len__(self) -> int:
        return len(self.data)

    def __setitem__(self, item, value) -> None:
        self.data[item] = value

    def __getitem__(self, item):
        return self.data[item]

    def sync_to_device(self) -> None:
        self.sync_count += 1

    def freebuffer(self) -> None:
        self.free_count += 1


class FakeNumpy:
    uint8 = "uint8"

    @staticmethod
    def frombuffer(data: bytes, dtype: str):
        assert dtype == "uint8"
        return data


class FakeOverlay:
    def __init__(self, bit_path: str, download: bool) -> None:
        self.bit_path = bit_path
        self.download = download
        self.ip_dict = {
            "axi_dma_0": {"phys_addr": 0x40400000},
            "image_pipeline/s_axi": {"phys_addr": 0x43C20000},
        }


class FakeMMIO:
    def __init__(self, base: int, mode: str, initial_dma_status: int,
                 run_dma_status: int) -> None:
        self.base = base
        self.mode = mode
        self.initial_dma_status = initial_dma_status
        self.run_dma_status = run_dma_status
        self.writes: list[tuple[int, int]] = []
        self.regs: dict[int, int] = {}
        if base == 0x40400000:
            self.regs = {0x04: initial_dma_status, 0x00: 0x0}
        else:
            self.regs = {offset: 0 for offset in range(0, 0x4C, 4)}
            self.regs[0x00] = 1
            self.regs[0x04] = 0x574F3134

    def read(self, offset: int) -> int:
        return self.regs.get(offset, 0)

    def write(self, offset: int, value: int) -> None:
        self.writes.append((offset, value))
        if self.base != 0x40400000:
            self.regs[offset] = value
            return
        self.regs[offset] = value
        if offset == 0x00 and value & 1:
            self.regs[0x04] = self.run_dma_status
        elif offset == 0x28:
            if self.mode == "success":
                self.regs[0x04] = 0x2
                checker = self.owner.checker
                values = {
                    0x08: 0x9,
                    0x0C: 0,
                    0x10: 0x1234ABCD,
                    0x14: 1,
                    0x18: 0,
                    0x1C: 230432,
                    0x20: 57608,
                    0x24: 1,
                    0x28: 0x574F3134,
                    0x2C: 0xC0DEC014,
                    0x30: self.owner.input_checksum,
                    0x34: 76800,
                    0x38: 1,
                    0x3C: 240,
                    0x40: self.owner.binary_checksum,
                    0x44: 76800,
                    0x48: 12,
                }
                checker.regs.update(values)
            else:
                self.regs[0x04] = 0


class FakeApi:
    def __init__(self, packet: bytes, address: int = 0x10000000,
                 mode: str = "success", initial_dma_status: int = 0x2,
                 run_dma_status: int = 0x2) -> None:
        self.packet = packet
        self.address = address
        self.mode = mode
        self.initial_dma_status = initial_dma_status
        self.run_dma_status = run_dma_status
        self.overlay_calls: list[tuple[str, bool]] = []
        self.mmios: dict[int, FakeMMIO] = {}
        self.buffers: list[FakeBuffer] = []
        _packet, self.binary_checksum = make_packet()
        self.input_checksum = sum(packet) & 0xFFFFFFFF
        self.np = FakeNumpy()

    @property
    def checker(self):
        return self.mmios[0x43C20000]

    def Overlay(self, bit_path: str, download: bool):
        self.overlay_calls.append((bit_path, download))
        overlay = FakeOverlay(bit_path, download)
        return overlay

    def MMIO(self, base: int, length: int):
        item = FakeMMIO(base, self.mode, self.initial_dma_status, self.run_dma_status)
        self.mmios[base] = item
        if base == 0x40400000:
            item.owner = self
        return item

    def allocate(self, shape: tuple[int, ...], dtype: str):
        buffer = FakeBuffer(shape[0], self.address)
        self.buffers.append(buffer)
        return buffer


class PynqImageOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        wo14._QUARANTINED_BUFFERS.clear()
        wo14._QUARANTINED_CHANNELS.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = make_artifacts(self.root)
        self.plan = wo14.validate_inputs(*self.paths)

    def tearDown(self) -> None:
        wo14._QUARANTINED_BUFFERS.clear()
        wo14._QUARANTINED_CHANNELS.clear()
        self.temp.cleanup()

    def test_packet_and_pair_manifest_validate_from_independent_files(self) -> None:
        self.assertEqual(self.plan.packet_bytes, 230432)
        self.assertEqual(self.plan.packet_beats, 57608)
        self.assertEqual(self.plan.frame_id, 0x1234ABCD)
        self.assertEqual(self.plan.expected_binary_checksum, make_packet()[1])

    def test_hwh_length_width_and_address_map_must_match_manifest(self) -> None:
        bit, hwh, packet, manifest_path = self.paths
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        write_hwh(hwh, length_width=17)
        manifest["hwh_sha256"] = hashlib.sha256(hwh.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(wo14.PreflightError):
            wo14.validate_inputs(bit, hwh, packet, manifest_path)

        write_hwh(hwh, checker_base=0x43C30000)
        manifest["hwh_sha256"] = hashlib.sha256(hwh.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(wo14.PreflightError):
            wo14.validate_inputs(bit, hwh, packet, manifest_path)

    def test_packet_header_checksum_and_trailer_mutations_are_rejected(self) -> None:
        original = self.paths[2].read_bytes()
        for name, offset, mask in (
                ("header", 0, 0x01),
                ("payload-checksum", 230416 + 8, 0x01),
                ("trailer-end", 230416 + 12, 0x01)):
            with self.subTest(name=name):
                mutated = bytearray(original)
                mutated[offset] ^= mask
                self.paths[2].write_bytes(mutated)
                with self.assertRaises(wo14.PreflightError):
                    wo14.validate_inputs(*self.paths)
        self.paths[2].write_bytes(original)

    def test_unsafe_buffer_address_never_writes_dma_length(self) -> None:
        api = FakeApi(self.plan.packet, address=0x1FFF0000)
        result = wo14.run_hardware_once(self.plan, api=api, timeout_s=0.02,
                                        poll_interval_s=0.001)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["send_attempt_count"], 0)
        self.assertEqual([value for offset, value in api.mmios[0x40400000].writes
                          if offset == 0x28], [])

    def test_fresh_reset_halt_transitions_to_idle_before_single_transfer(self) -> None:
        api = FakeApi(self.plan.packet, initial_dma_status=wo14.DMA_SR_HALTED,
                      run_dma_status=wo14.DMA_SR_IDLE)
        result = wo14.run_hardware_once(self.plan, api=api, timeout_s=0.05,
                                        poll_interval_s=0.001)
        dma = api.mmios[0x40400000]
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["pre_send_dma_status"], 0x1)
        self.assertEqual(result["post_runstop_dma_status"], 0x2)
        self.assertEqual(result["pre_length_dma_status"], 0x2)
        self.assertEqual(result["send_attempt_count"], 1)
        self.assertEqual(len([1 for offset, _ in dma.writes if offset == 0x28]), 1)
        self.assertEqual([value for offset, value in dma.writes if offset == 0x00], [1])
        self.assertEqual(api.buffers[0].free_count, 1)

    def test_unknown_active_prestate_never_enables_or_sends_dma(self) -> None:
        api = FakeApi(self.plan.packet, initial_dma_status=0x0)
        result = wo14.run_hardware_once(self.plan, api=api, timeout_s=0.02,
                                        poll_interval_s=0.001)
        dma = api.mmios[0x40400000]
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["send_attempt_count"], 0)
        self.assertFalse(any(offset == 0x00 and value & wo14.DMA_CR_RUNSTOP
                             for offset, value in dma.writes))
        self.assertFalse(any(offset == 0x28 for offset, _ in dma.writes))
        self.assertEqual(api.buffers, [])

    def test_length_is_blocked_until_runstop_reaches_idle(self) -> None:
        api = FakeApi(self.plan.packet, run_dma_status=0x0)
        result = wo14.run_hardware_once(self.plan, api=api, timeout_s=0.02,
                                        poll_interval_s=0.001)
        dma = api.mmios[0x40400000]
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["send_attempt_count"], 0)
        self.assertFalse(any(offset == 0x28 for offset, _ in dma.writes))

    def test_timeout_does_not_retry_or_reset_and_quarantines_active_buffer(self) -> None:
        api = FakeApi(self.plan.packet, mode="timeout")
        result = wo14.run_hardware_once(self.plan, api=api, timeout_s=0.02,
                                        poll_interval_s=0.001)
        dma = api.mmios[0x40400000]
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["send_attempt_count"], 1)
        self.assertEqual(len([1 for offset, _ in dma.writes if offset == 0x28]), 1)
        self.assertFalse(any(offset == 0x00 and value & 4 for offset, value in dma.writes))
        self.assertEqual(api.buffers[0].free_count, 0)
        self.assertIn(api.buffers[0], wo14._QUARANTINED_BUFFERS)
        self.assertIn("raw_registers", result)

    def test_one_successful_send_requires_complete_output_statistics(self) -> None:
        api = FakeApi(self.plan.packet)
        result = wo14.run_hardware_once(self.plan, api=api, timeout_s=0.05,
                                        poll_interval_s=0.001)
        dma = api.mmios[0x40400000]
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["send_attempt_count"], 1)
        self.assertEqual(len([1 for offset, _ in dma.writes if offset == 0x28]), 1)
        self.assertEqual(api.overlay_calls, [(str(self.plan.bit_path), True)])
        self.assertEqual(api.buffers[0].sync_count, 1)
        self.assertEqual(api.buffers[0].free_count, 1)

    def test_dry_run_never_imports_pynq(self) -> None:
        args = ["--bit", str(self.paths[0]), "--hwh", str(self.paths[1]),
                "--packet", str(self.paths[2]), "--manifest", str(self.paths[3])]
        original_import = builtins.__import__

        def no_pynq(name, *call_args, **call_kwargs):
            if name == "pynq" or name.startswith("pynq."):
                raise AssertionError("dry run attempted to import PYNQ")
            return original_import(name, *call_args, **call_kwargs)

        with mock.patch("builtins.__import__", side_effect=no_pynq):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(wo14.main(args), 0)

    def test_execute_flag_without_human_approval_cannot_load_overlay(self) -> None:
        args = ["--bit", str(self.paths[0]), "--hwh", str(self.paths[1]),
                "--packet", str(self.paths[2]), "--manifest", str(self.paths[3]),
                "--execute"]
        original_import = builtins.__import__

        def no_pynq(name, *call_args, **call_kwargs):
            if name == "pynq" or name.startswith("pynq."):
                raise AssertionError("approval gate imported PYNQ")
            return original_import(name, *call_args, **call_kwargs)

        with mock.patch("builtins.__import__", side_effect=no_pynq):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(wo14.main(args), 2)


if __name__ == "__main__":
    unittest.main()
