from scripts.standalone.vdma_diag import (
    MAX_READS_PER_SAMPLE, SAFE_REGISTERS, SCHEMA, capture_vdma_snapshot,
)


REGISTERS = {
    0x00: 0x00011001,      # RS | FrmCnt_IrqEn | IRQFrameCount=1
    0x04: 0x00000000,      # no halt, no error, FrmCnt_Irq not pending
    0x28: (3 << 16) | 2,   # RdFrmStore=3, RdFrmPtrRef=2
    0x2C: 0x62000000,
    0x50: 720,
    0x54: 1280,
    0x58: 1280,
    0x5C: 0x1E000000,
    0x60: 0x1E200000,
    0x64: 0x1E400000,
    0x68: 0x1E600000,
}


class Reader:
    def __init__(self, values=None, *, fail_offsets=(), second_values=None):
        self.values = REGISTERS if values is None else values
        self.second_values = second_values
        self.fail_offsets = set(fail_offsets)
        self.calls = []

    def __call__(self, offset):
        self.calls.append(offset)
        if self.second_values is not None and len(self.calls) > MAX_READS_PER_SAMPLE:
            values = self.second_values
        else:
            values = self.values
        if offset in self.fail_offsets:
            raise OSError("mmio unavailable")
        return values[offset]


def test_snapshot_reads_only_safe_registers_and_decodes_the_map():
    reader = Reader()
    snapshot = capture_vdma_snapshot(reader, gap_s=0)
    assert snapshot["schema"] == SCHEMA
    assert sorted(set(reader.calls)) == sorted(offset for offset, _ in SAFE_REGISTERS)
    assert snapshot["reads"] == 2 * MAX_READS_PER_SAMPLE
    assert len(snapshot["samples"]) == 2
    decoded = snapshot["decoded"]
    assert decoded["running"] is True
    assert decoded["circular"] is False
    assert decoded["frmcnt_irq_en"] is True
    assert decoded["irq_frame_count"] == 1
    assert decoded["halted"] is False
    assert decoded["error_bits"] == []
    assert decoded["frmcnt_irq"] is False
    assert decoded["irq_frame_cnt_sts"] == 0
    assert decoded["rd_frm_ptr_ref"] == 2
    assert decoded["rd_frm_store"] == 3
    assert decoded["frame_stores"] == [0x1E000000, 0x1E200000,
                                       0x1E400000, 0x1E600000]
    assert (decoded["vsize"], decoded["hsize"], decoded["stride"]) == (720, 1280, 1280)
    assert decoded["rd_frm_store_moved"] is False
    assert decoded["frmcnt_irq_observed"] is False
    assert snapshot["read_errors"] == []


def test_frame_store_movement_between_samples_is_reported():
    second = dict(REGISTERS)
    second[0x28] = (2 << 16) | 2
    second[0x04] = 0x1000
    snapshot = capture_vdma_snapshot(
        Reader(second_values=second), gap_s=0)
    assert snapshot["decoded"]["rd_frm_store_moved"] is True
    assert snapshot["decoded"]["frmcnt_irq_observed"] is True


def test_error_bits_are_named_without_writing_status():
    values = dict(REGISTERS)
    values[0x04] = 0x1 | (1 << 4) | (1 << 7) | 0x1000
    reader = Reader(values)
    snapshot = capture_vdma_snapshot(reader, gap_s=0)
    decoded = snapshot["decoded"]
    assert decoded["halted"] is True
    assert decoded["error_bits"] == ["vdma_int_err", "sof_early_err"]
    assert decoded["frmcnt_irq"] is True
    # Only reads: a write-capable helper would have hit 0x04 with a value.
    assert all(offset in dict(SAFE_REGISTERS) for offset in reader.calls)


def test_failed_read_is_recorded_and_does_not_stop_the_capture():
    reader = Reader(fail_offsets={0x28})
    snapshot = capture_vdma_snapshot(reader, gap_s=0)
    assert len(snapshot["read_errors"]) == 2
    assert snapshot["read_errors"][0]["offset"] == "0x28"
    assert snapshot["decoded"]["rd_frm_store"] is None
    assert snapshot["decoded"]["rd_frm_store_moved"] is None
    assert snapshot["decoded"]["halted"] is False
    assert "snapshot_error" not in snapshot


def test_capture_never_raises_when_every_read_fails():
    def broken(offset):
        raise RuntimeError("bus hung")

    snapshot = capture_vdma_snapshot(broken, gap_s=0)
    assert snapshot["reads"] == 2 * MAX_READS_PER_SAMPLE
    assert len(snapshot["read_errors"]) == 2 * MAX_READS_PER_SAMPLE
    assert snapshot["decoded"]["running"] is None
    assert snapshot["decoded"]["halted"] is None
    assert snapshot["decoded"]["frame_stores"] == [None, None, None, None]


def test_single_sample_is_bounded_and_has_no_movement_claim():
    reader = Reader()
    snapshot = capture_vdma_snapshot(reader, samples=1, gap_s=0)
    assert snapshot["reads"] == MAX_READS_PER_SAMPLE
    assert len(snapshot["samples"]) == 1
    assert "rd_frm_store_moved" not in snapshot["decoded"]