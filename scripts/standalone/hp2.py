"""Guarded synchronization of the display-only HP2 read width bit.

UG585: Register-axi_hp-AFI_RDCHAN_CTRL and Register-AFI_RDCHAN_CTRL-Details
at https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/ .
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


_SLCR_BASE = 0xF8000000
_SLCR_FPGA_RST_CTRL = 0x240
_VDMA_BASE = 0x43000000
_AFI2_BASE = 0xF800A000
_PAGE_SIZE = 0x1000


def _hwh_hp2_width(hwh_path: str | Path) -> int:
    try:
        root = ET.parse(hwh_path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise RuntimeError(f"cannot inspect HP2 width in HWH {hwh_path}: {exc}") from exc
    ps_modules = [node for node in root.iter("MODULE")
                  if node.attrib.get("INSTANCE") == "ps7"]
    if len(ps_modules) != 1:
        raise RuntimeError("HWH must contain exactly one PS7 module for HP2 width check")
    ps7 = ps_modules[0]
    params = [node.attrib.get("VALUE") for node in ps7.iter("PARAMETER")
              if node.attrib.get("NAME") == "PCW_S_AXI_HP2_DATA_WIDTH"]
    buses = [node for node in ps7.iter("BUSINTERFACE")
             if node.attrib.get("NAME") == "S_AXI_HP2"]
    if params != ["32"] or len(buses) != 1 or buses[0].attrib.get("DATAWIDTH") != "32":
        raise RuntimeError(
            "HWH must declare PS7 S_AXI_HP2 as 32-bit in both PS parameters and bus interface"
        )
    return 32


def _read_u32(mmio, offset: int, name: str) -> int:
    value = mmio.read(offset)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise RuntimeError(f"{name} read did not return an unsigned 32-bit integer")
    return value


def sync_hp2_read32(hwh_path, mmio_factory=None):
    """Enable AFI2 read 32-bit mode after HWH, reset, and VDMA safety checks."""
    width_bits = _hwh_hp2_width(hwh_path)
    if mmio_factory is None:
        from pynq import MMIO

        mmio_factory = MMIO

    slcr = mmio_factory(_SLCR_BASE, _PAGE_SIZE)
    reset = _read_u32(slcr, _SLCR_FPGA_RST_CTRL, "SLCR_FPGA_RST_CTRL")
    if reset != 0:
        raise RuntimeError(
            "refusing AFI2 access until SLCR_FPGA_RST_CTRL is exactly 0x00000000; "
            f"observed 0x{reset:08x}"
        )

    vdma = mmio_factory(_VDMA_BASE, _PAGE_SIZE)
    control = _read_u32(vdma, 0, "VDMA control")
    status = _read_u32(vdma, 4, "VDMA status")
    if control & 1:
        raise RuntimeError("refusing AFI2 access while VDMA control bit 0 is set")
    if not status & 1:
        raise RuntimeError("refusing AFI2 access until VDMA status bit 0 indicates halted")
    if status & 0x770:
        raise RuntimeError(f"refusing AFI2 access with VDMA fault bits set: 0x{status & 0x770:03x}")

    afi2 = mmio_factory(_AFI2_BASE, _PAGE_SIZE)
    before = _read_u32(afi2, 0, "AFI2_RDCHAN_CTRL")
    requested = before | 1
    if before & 1:
        after = before
        write_value = None
    else:
        afi2.write(0, requested)
        after = _read_u32(afi2, 0, "AFI2_RDCHAN_CTRL readback")
        write_value = requested
        if after != requested:
            raise RuntimeError(
                "AFI2_RDCHAN_CTRL readback mismatch: "
                f"requested 0x{requested:08x}, observed 0x{after:08x}"
            )

    return {
        "status": "HP2_READ_32BIT_SYNC_PASS",
        "hwh_path": str(hwh_path),
        "hwh_hp2_data_width_bits": width_bits,
        "slcr_fpga_rst_ctrl": reset,
        "vdma": {"base": "0x43000000", "control": control, "status": status},
        "register": "AFI2_RDCHAN_CTRL",
        "address": "0xF800A000",
        "before": before,
        "write_value": write_value,
        "after": after,
        "width_bit0": after & 1,
    }
