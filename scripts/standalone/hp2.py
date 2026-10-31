"""Guarded synchronization of the RLE HP1 and display HP2 width bits.

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
_RLE_DMA_BASE = 0x40400000
_RLE_PIPELINE_BASE = 0x43C20000
_AFI1_BASE = 0xF8009000
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


def _hwh_hp1_width(hwh_path: str | Path) -> int:
    try:
        root = ET.parse(hwh_path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise RuntimeError(f"cannot inspect HP1 width in HWH {hwh_path}: {exc}") from exc
    ps_modules = [node for node in root.iter("MODULE")
                  if node.attrib.get("INSTANCE") == "ps7"]
    if len(ps_modules) != 1:
        raise RuntimeError("HWH must contain exactly one PS7 module for HP1 width check")
    ps7 = ps_modules[0]
    params = [node.attrib.get("VALUE") for node in ps7.iter("PARAMETER")
              if node.attrib.get("NAME") == "PCW_S_AXI_HP1_DATA_WIDTH"]
    buses = [node for node in ps7.iter("BUSINTERFACE")
             if node.attrib.get("NAME") == "S_AXI_HP1"]
    if params != ["32"] or len(buses) != 1 or buses[0].attrib.get("DATAWIDTH") != "32":
        raise RuntimeError(
            "HWH must declare PS7 S_AXI_HP1 as 32-bit in both PS parameters and bus interface"
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


def sync_hp1_readwrite32(hwh_path, mmio_factory=None):
    """Enable AFI1 read/write 32-bit mode after HWH and hardware safety checks."""
    width_bits = _hwh_hp1_width(hwh_path)
    if mmio_factory is None:
        from pynq import MMIO

        mmio_factory = MMIO

    slcr = mmio_factory(_SLCR_BASE, _PAGE_SIZE)
    reset = _read_u32(slcr, _SLCR_FPGA_RST_CTRL, "SLCR_FPGA_RST_CTRL")
    if reset != 0:
        raise RuntimeError(
            "refusing AFI1 access until SLCR_FPGA_RST_CTRL is exactly 0x00000000; "
            f"observed 0x{reset:08x}"
        )

    dma = mmio_factory(_RLE_DMA_BASE, _PAGE_SIZE)
    dma_status = {}
    for name, offset in (("read", 0x04), ("write", 0x34)):
        status = _read_u32(dma, offset, f"RLE DMA {name} status")
        if status & 0x770:
            raise RuntimeError(
                f"refusing AFI1 access with RLE DMA {name} error bits: 0x{status & 0x770:03x}"
            )
        if not status & 0x3:
            raise RuntimeError(f"refusing AFI1 access while RLE DMA {name} is active")
        dma_status[name] = {"offset": offset, "status": status}

    pipeline = mmio_factory(_RLE_PIPELINE_BASE, _PAGE_SIZE)
    signature = _read_u32(pipeline, 0x04, "RLE pipeline signature")
    geometry = _read_u32(pipeline, 0x2C, "RLE pipeline geometry")
    max_runs = _read_u32(pipeline, 0x30, "RLE pipeline max_runs")
    state = _read_u32(pipeline, 0x08, "RLE pipeline state")
    if signature != 0x31454C52:
        raise RuntimeError(f"RLE pipeline signature mismatch: 0x{signature:08x}")
    if geometry != 0x01900280:
        raise RuntimeError(f"RLE pipeline geometry mismatch: 0x{geometry:08x}")
    if max_runs != 16384:
        raise RuntimeError(f"RLE pipeline max_runs mismatch: {max_runs}")
    if state & 0x5:
        raise RuntimeError(f"RLE pipeline is busy or has input_error: 0x{state:08x}")

    afi1 = mmio_factory(_AFI1_BASE, _PAGE_SIZE)
    registers = (("AFI1_RDCHAN_CTRL", 0x00), ("AFI1_WRCHAN_CTRL", 0x14))
    before = {}
    write_value = {}
    after = {}
    for name, offset in registers:
        before[name] = _read_u32(afi1, offset, name)
        requested = before[name] | 1
        if before[name] & 1:
            write_value[name] = None
        else:
            afi1.write(offset, requested)
            write_value[name] = requested
        after[name] = _read_u32(afi1, offset, f"{name} readback")
        if after[name] != requested:
            raise RuntimeError(
                f"{name} readback mismatch: requested 0x{requested:08x}, "
                f"observed 0x{after[name]:08x}"
            )

    return {
        "status": "HP1_READWRITE_32BIT_SYNC_PASS",
        "hwh_path": str(hwh_path),
        "hwh_hp1_data_width_bits": width_bits,
        "slcr_fpga_rst_ctrl": reset,
        "rle_dma": {"base": "0x40400000", "status": dma_status},
        "rle_pipeline": {
            "base": "0x43c20000",
            "signature": signature,
            "geometry": geometry,
            "max_runs": max_runs,
            "state": state,
        },
        "registers": {
            name: {"address": f"0x{_AFI1_BASE + offset:08X}",
                   "before": before[name], "write_value": write_value[name],
                   "after": after[name]}
            for name, offset in registers
        },
    }
