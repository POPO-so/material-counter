import pytest

from scripts.standalone.g2_readback import READ_OFFSETS, read_pipeline, snapshot_counters


EXPECTED_VALUES = {
    0x04: 0x31454C52,
    0x08: 0,
    0x0C: 1234,
    0x10: 7,
    0x14: 2,
    0x18: 0x00000000,
    0x1C: 0x01900280,
    0x20: 127,
    0x24: 0,
    0x28: 1,
    0x2C: (400 << 16) | 640,
    0x30: 16384,
    0x38: 0x42,
}


class FakeMMIO:
    def __init__(self, values):
        self.values = dict(values)
        self.reads = []

    def read(self, offset):
        if offset not in READ_OFFSETS:
            raise AssertionError(f"read from undefined offset {offset:#x}")
        self.reads.append(offset)
        return self.values[offset]

    def write(self, offset, value):
        raise AssertionError(f"MMIO write attempted at {offset:#x}: {value:#x}")


class CounterMMIO:
    def __init__(self, sequence_reads, counter_values):
        self.sequence_reads = iter(sequence_reads)
        self.counter_values = dict(counter_values)
        self.events = []

    def read(self, offset):
        self.events.append(("read", offset))
        if offset == 0x3C:
            return next(self.sequence_reads)
        return self.counter_values[offset]

    def write(self, offset, value):
        self.events.append(("write", offset, value))


def test_reads_only_defined_registers_and_returns_offsets_with_values():
    mmio = FakeMMIO(EXPECTED_VALUES)

    result = read_pipeline(mmio)

    assert mmio.reads == list(READ_OFFSETS)
    assert 0x00 not in mmio.reads
    assert 0x34 not in mmio.reads
    assert result["signature"] == {"offset": 0x04, "value": 0x31454C52}
    assert result["state"] == {
        "offset": 0x08,
        "value": 0,
        "busy": False,
        "done_seen": False,
        "error": False,
    }
    for name, offset in (
        ("actual_bytes", 0x0C),
        ("frame_id", 0x10),
        ("params_version", 0x14),
        ("roi_start", 0x18),
        ("roi_end", 0x1C),
        ("threshold", 0x20),
        ("offset", 0x24),
        ("morph", 0x28),
        ("geometry", 0x2C),
        ("max_runs", 0x30),
        ("packet_code", 0x38),
    ):
        assert result[name] == {"offset": offset, "value": EXPECTED_VALUES[offset]}


@pytest.mark.parametrize(
    "offset,value,error",
    [
        (0x04, 0, "signature"),
        (0x2C, (400 << 16) | 320, "geometry"),
        (0x30, 8192, "max_runs"),
        (0x08, 0x1, "busy"),
        (0x08, 0x4, "error"),
    ],
)
def test_rejects_invalid_identity_geometry_capacity_or_state(offset, value, error):
    values = dict(EXPECTED_VALUES)
    values[offset] = value

    with pytest.raises(RuntimeError, match=error):
        read_pipeline(FakeMMIO(values))


def test_done_seen_is_allowed():
    values = dict(EXPECTED_VALUES)
    values[0x08] = 0x2

    result = read_pipeline(FakeMMIO(values))

    assert result["state"]["done_seen"] is True
    assert result["state"]["busy"] is False
    assert result["state"]["error"] is False


def test_snapshot_counters_latches_once_and_reads_only_counter_page():
    values = {
        0x40: 0x89ABCDEF,
        0x44: 0x01234567,
        0x48: 0x76543210,
        0x4C: 0xFEDCBA98,
        0x50: 3,
        0x54: 0,
        0x58: 5,
        0x5C: 0,
        0x60: 7,
        0x64: 0,
        0x68: 11,
        0x6C: 0,
    }
    mmio = CounterMMIO([0xFFFFFFFF, 0, 0], values)

    result = snapshot_counters(mmio)

    assert result == {
        "sequence": 0,
        "clock_hz": 50_000_000,
        "counters": {
            "total_cycles": 0x0123456789ABCDEF,
            "active_cycles": 0xFEDCBA9876543210,
            "input_beats": 3,
            "output_beats": 5,
            "input_stall_cycles": 7,
            "output_stall_cycles": 11,
        },
    }
    assert mmio.events == [
        ("read", 0x3C),
        ("write", 0x3C, 1),
        ("read", 0x3C),
        *[("read", offset) for offset in range(0x40, 0x70, 4)],
        ("read", 0x3C),
    ]


@pytest.mark.parametrize("sequence_reads", [[4, 4], [8, 10]])
def test_snapshot_counters_rejects_unexpected_sequence(sequence_reads):
    values = {offset: 0 for offset in range(0x40, 0x70, 4)}
    mmio = CounterMMIO(sequence_reads, values)

    with pytest.raises(RuntimeError, match="sequence"):
        snapshot_counters(mmio)

    assert mmio.events == [("read", 0x3C), ("write", 0x3C, 1), ("read", 0x3C)]


def test_snapshot_counters_rejects_sequence_change_while_reading():
    values = {offset: 0 for offset in range(0x40, 0x70, 4)}
    mmio = CounterMMIO([10, 11, 12], values)

    with pytest.raises(RuntimeError, match="sequence"):
        snapshot_counters(mmio)

    assert mmio.events[0:3] == [("read", 0x3C), ("write", 0x3C, 1), ("read", 0x3C)]
    assert mmio.events[-1] == ("read", 0x3C)
    assert [event for event in mmio.events if event[0] == "write"] == [("write", 0x3C, 1)]
