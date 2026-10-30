import pytest

from scripts.standalone.g2_readback import READ_OFFSETS, read_pipeline


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
