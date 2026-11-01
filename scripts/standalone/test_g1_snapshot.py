import pytest

from scripts.standalone.camera import LateDecodeTimeout
from scripts.standalone.g1_run import read_camera_frame, snapshot


class Monitor:
    def __init__(self, final_sequence=11):
        self.sequence_reads = iter((10, 11, final_sequence))
        self.registers = {0x08: 2}
        self.registers.update({offset: value for offset, value in zip(
            range(0x50, 0x9C, 4), range(1, 20))})
        self.registers[0x50] = 3

    def read(self, offset):
        if offset == 0x0C:
            return next(self.sequence_reads)
        return self.registers.get(offset, 0)

    def write(self, offset, value):
        assert (offset, value) == (0x04, 1)


def test_snapshot_reads_diagnostic_flags_and_224_bit_markers_as_little_word_hex():
    monitor = Monitor()

    result = snapshot(monitor)

    assert result["gray_error"] is True
    assert result["marker_pixel_error"] is True
    assert result["pair_mismatch"] is False
    assert result["top_invalid"] is False
    assert result["bottom_invalid"] is False
    top_words = [monitor.registers[offset] for offset in range(0x60, 0x7C, 4)]
    bottom_words = [monitor.registers[offset] for offset in range(0x80, 0x9C, 4)]
    top_value = sum(word << (32 * i) for i, word in enumerate(top_words))
    bottom_value = sum(word << (32 * i) for i, word in enumerate(bottom_words))
    assert result["top_marker"] == f"{top_value:056x}"
    assert result["bottom_marker"] == f"{bottom_value:056x}"


def test_snapshot_rejects_sequence_change_during_diagnostic_read():
    with pytest.raises(RuntimeError, match="snapshot changed during atomic read"):
        snapshot(Monitor(final_sequence=12))


def test_g1_camera_consumes_late_decoded_frame_and_preserves_empty_timeout():
    frame = (4, object(), 1.0)

    class Camera:
        def __init__(self, error):
            self.error = error

        def read(self, *, after, timeout):
            assert after == -1 and timeout == 0.25
            raise self.error

    assert read_camera_frame(Camera(LateDecodeTimeout(frame)), timeout=0.25) is frame
    with pytest.raises(TimeoutError, match="no fresh frame"):
        read_camera_frame(Camera(TimeoutError("no fresh frame")), timeout=0.25)
