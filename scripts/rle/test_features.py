import pytest

from scripts.rle.features import PlRegionFeatures


class Mmio:
    def __init__(self, registers, records=()):
        self.registers = dict(registers)
        self.records = list(records)
        self.record_reads = 0

    def read(self, offset):
        if offset >= 0x1000:
            word = self.records[(offset - 0x1000) // 4]
            self.record_reads += 1
            return word
        return self.registers[offset]


def mk(records=(), *, status=1, frame_id=7, count=None, total=20):
    regs = {0x200: 0x46524731, 0x204: status, 0x208: frame_id,
            0x20C: len(records) // 8 if count is None else count,
            0x210: total, 0x214: 0, 0x218: 512}
    return Mmio(regs, records)


def test_valid_record_and_consistent_frame_are_returned():
    mmio = mk([20, 290, 210, 0x0014000A, 0x000B000A, 7, 0, 0], total=20)
    result = PlRegionFeatures(mmio).read(type("Frame", (), {"valid": True, "frame_id": 7, "total_fg": 20,
                                                               "width": 640, "height": 400})())
    assert len(result) == 1
    assert (result[0].x_min, result[0].y_min, result[0].x_max, result[0].y_max) == (10, 10, 20, 11)


@pytest.mark.parametrize("status,frame_id,total,records", [
    (0, 7, 20, [20, 190, 290, 0x000A000A, 0x000B000A, 7, 0, 0]),
    (3, 7, 20, [20, 190, 290, 0x000A000A, 0x000B000A, 7, 0, 0]),
    (1, 8, 20, [20, 190, 290, 0x000A000A, 0x000B000A, 8, 0, 0]),
    (1, 7, 21, [20, 190, 290, 0x000A000A, 0x000B000A, 7, 0, 0]),
    (1, 7, 20, [20, 190, 290, 0x000A000A, 0x000B000A, 7, 1, 0]),
    (1, 7, 20, [20, 1, 290, 0x000A000A, 0x000B000A, 7, 0, 0]),
])
def test_unready_overflow_frame_mismatch_bad_stats_or_reserved_are_rejected(status, frame_id, total, records):
    mmio = mk(records, status=status, frame_id=frame_id, total=total)
    frame = type("Frame", (), {"valid": True, "frame_id": 7, "total_fg": 20,
                                "width": 640, "height": 400})()
    assert PlRegionFeatures(mmio).read(frame) is None


def test_constructor_checks_identity_and_capacity():
    mmio = mk()
    mmio.registers[0x218] = 10
    with pytest.raises(ValueError):
        PlRegionFeatures(mmio)


def test_reason_counts_are_bounded_and_capacity_is_enforced():
    mmio = mk(status=1, frame_id=7, total=0)
    mmio.registers[0x20C] = 513
    reader = PlRegionFeatures(mmio)
    frame = type("Frame", (), {"valid": True, "frame_id": 7, "total_fg": 0,
                                "width": 640, "height": 400})()
    assert reader.read(frame) is None
    assert reader.reason_counts["bad_count"] == 1
    assert set(reader.reason_counts) == {
        "invalid_frame", "mmio_error", "not_done", "overflow", "busy",
        "frame_mismatch", "bad_count", "foreground_mismatch",
        "snapshot_changed", "bad_record",
    }


def test_snapshot_change_during_record_read_is_rejected():
    class SwappingMmio(Mmio):
        status_reads = 0

        def read(self, offset):
            if offset == 0x204:
                self.status_reads += 1
                if self.status_reads == 2:
                    return 5  # done + busy after the feature words were sampled
            return super().read(offset)

    mmio = SwappingMmio({0x200: 0x46524731, 0x204: 1, 0x208: 7, 0x20C: 1,
                         0x210: 20, 0x214: 0, 0x218: 512},
                        [20, 290, 210, 0x0014000A, 0x000B000A, 7, 0, 0])
    reader = PlRegionFeatures(mmio)
    frame = type("Frame", (), {"valid": True, "frame_id": 7, "total_fg": 20,
                                "width": 640, "height": 400})()
    assert reader.read(frame) is None
    assert reader.reason_counts["snapshot_changed"] == 1


def test_historical_dropped_count_does_not_reject_following_valid_frame():
    mmio = mk([20, 290, 210, 0x0014000A, 0x000B000A, 7, 0, 0], status=3, total=20)
    mmio.registers[0x214] = 4
    reader = PlRegionFeatures(mmio)
    frame = type("Frame", (), {"valid": True, "frame_id": 7, "total_fg": 20,
                                "width": 640, "height": 400})()
    assert reader.read(frame) is None
    mmio.registers[0x204] = 1
    assert reader.read(frame) is not None
    assert reader.last_dropped == 4
