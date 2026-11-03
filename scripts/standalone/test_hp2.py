import xml.etree.ElementTree as ET

import pytest

from scripts.standalone.hp2 import sync_hp2_read32


def write_hwh(path, *, param_width="32", bus_width="32", ps7_count=1):
    root = ET.Element("SYSTEM")
    for index in range(ps7_count):
        module = ET.SubElement(root, "MODULE", INSTANCE="ps7")
        if index == 0:
            ET.SubElement(module, "PARAMETERS")
            params = module[-1]
            ET.SubElement(params, "PARAMETER", NAME="PCW_S_AXI_HP2_DATA_WIDTH", VALUE=param_width)
            ET.SubElement(module, "BUSINTERFACES")
            businterfaces = module[-1]
            ET.SubElement(businterfaces, "BUSINTERFACE", NAME="S_AXI_HP2", DATAWIDTH=bus_width)
    ET.ElementTree(root).write(path)
    return path


class FakeMMIO:
    def __init__(self, base, size, regs, writes):
        self.base = base
        self.size = size
        self.regs = regs
        self.writes = writes

    def read(self, offset):
        return self.regs.get((self.base, offset), 0)

    def write(self, offset, value):
        self.writes.append((self.base, offset, value))
        self.regs[(self.base, offset)] = value


def make_factory(regs, writes):
    def factory(base, size):
        return FakeMMIO(base, size, regs, writes)
    return factory


@pytest.mark.parametrize("param,bus", [("64", "32"), ("32", "64"), ("32", None)])
def test_rejects_hwh_width_unless_parameter_and_bus_are_both_32(tmp_path, param, bus):
    path = write_hwh(tmp_path / "design.hwh", param_width=param,
                     bus_width="missing" if bus is None else bus)
    writes = []

    with pytest.raises(RuntimeError, match="32-bit"):
        sync_hp2_read32(path, make_factory({}, writes))

    assert writes == []


def test_rejects_non_unique_ps7_before_any_mmio(tmp_path):
    path = write_hwh(tmp_path / "design.hwh", ps7_count=2)
    calls = []

    def factory(*args):
        calls.append(args)
        return FakeMMIO(*args, {}, [])

    with pytest.raises(RuntimeError, match="exactly one PS7"):
        sync_hp2_read32(path, factory)

    assert calls == []


@pytest.mark.parametrize("unsafe", ["reset", "control", "status", "fault"])
def test_unsafe_reset_or_vdma_state_causes_no_afi_write(tmp_path, unsafe):
    path = write_hwh(tmp_path / "design.hwh")
    regs = {(0xF8000000, 0x240): 1 if unsafe == "reset" else 0,
            (0x43000000, 0): 1 if unsafe == "control" else 0,
            (0x43000000, 4): (0 if unsafe == "status" else 1) |
                              (0x10 if unsafe == "fault" else 0),
            (0xF800A000, 0): 0}
    writes = []

    with pytest.raises(RuntimeError):
        sync_hp2_read32(path, make_factory(regs, writes))

    assert not any(base == 0xF800A000 for base, _, _ in writes)


@pytest.mark.parametrize("fault_bit,refused", [(4, True), (5, True), (6, True),
                                               (7, True), (8, False), (10, False)])
def test_vdma_fault_mask_covers_sof_early_err_but_not_axi_dma_sg_bits(
        tmp_path, fault_bit, refused):
    path = write_hwh(tmp_path / "design.hwh")
    regs = {(0xF8000000, 0x240): 0, (0x43000000, 0): 0,
            (0x43000000, 4): 1 | (1 << fault_bit), (0xF800A000, 0): 0}
    writes = []

    if refused:
        with pytest.raises(RuntimeError, match="fault"):
            sync_hp2_read32(path, make_factory(regs, writes))
        assert not any(base == 0xF800A000 for base, _, _ in writes)
    else:
        sync_hp2_read32(path, make_factory(regs, writes))
        assert writes == [(0xF800A000, 0, 1)]


def test_enables_only_hp2_read_bit_and_preserves_other_bits(tmp_path):
    path = write_hwh(tmp_path / "design.hwh")
    before = 0xA5A50020
    regs = {(0xF8000000, 0x240): 0, (0x43000000, 0): 0,
            (0x43000000, 4): 1, (0xF800A000, 0): before}
    writes = []

    result = sync_hp2_read32(path, make_factory(regs, writes))

    assert writes == [(0xF800A000, 0, before | 1)]
    assert result["before"] == before
    assert result["after"] == before | 1
    assert result["width_bit0"] == 1


def test_readback_mismatch_is_reported(tmp_path):
    path = write_hwh(tmp_path / "design.hwh")
    regs = {(0xF8000000, 0x240): 0, (0x43000000, 0): 0,
            (0x43000000, 4): 1, (0xF800A000, 0): 0}
    writes = []

    class BadReadback(FakeMMIO):
        def write(self, offset, value):
            writes.append((self.base, offset, value))

    def factory(base, size):
        if base == 0xF800A000:
            return BadReadback(base, size, regs, writes)
        return FakeMMIO(base, size, regs, writes)

    with pytest.raises(RuntimeError, match="readback"):
        sync_hp2_read32(path, factory)


def test_already_enabled_is_idempotent_and_does_not_write(tmp_path):
    path = write_hwh(tmp_path / "design.hwh")
    before = 0xA5A50021
    regs = {(0xF8000000, 0x240): 0, (0x43000000, 0): 0,
            (0x43000000, 4): 1, (0xF800A000, 0): before}
    writes = []

    result = sync_hp2_read32(path, make_factory(regs, writes))

    assert writes == []
    assert result["before"] == result["after"] == before
    assert result["write_value"] is None
