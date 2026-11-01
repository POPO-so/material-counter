"""Failure-triggered, read-only MM2S VDMA state capture.

The HDMI output VDMA in this design is MM2S-only (HWH parameters
C_INCLUDE_MM2S=1, C_INCLUDE_S2MM=0), so S2MM registers are not touched.

Register semantics are taken from the HWH shipped with the display build
(xilinx.com:ip:axi_vdma:6.3):

- 0x00 MM2S_VDMACR       control; bit 0 RS, bit 1 circular park,
                         bit 12 FrmCnt_IrqEn, bits 16-23 IRQFrameCount.
- 0x04 MM2S_VDMASR       status; bit 0 Halted, bits 4-7 error flags,
                         bit 12 FrmCnt_Irq, bits 16-23 IRQFrameCntSts.
- 0x28 PARK_PTR_REG      bits 0-4 RdFrmPtrRef (target), bits 16-20
                         RdFrmStore (frame store the MM2S engine is on).
- 0x2C VDMA_VERSION      read-only identity.
- 0x50/0x54/0x58         MM2S VSIZE / HSIZE / FRMDLY_STRIDE.
- 0x5C..0x68             MM2S_SA1..SA4 frame store addresses (4 stores).

Reading these odd addresses has no side effect. Writing 0x04 clears the
interrupt/status bits, so this module never writes. PYNQ's ``activeframe``
helper is not used because it writes 0x04 = 0x4090 before reading.
"""
from __future__ import annotations

import time

SCHEMA = "material-counter.vdma-mm2s-snapshot.v1"

SAFE_REGISTERS = (
    (0x00, "mm2s_vdmaccr"),
    (0x04, "mm2s_vdmasr"),
    (0x28, "park_ptr"),
    (0x2C, "version"),
    (0x50, "mm2s_vsize"),
    (0x54, "mm2s_hsize"),
    (0x58, "mm2s_frmdly_stride"),
    (0x5C, "mm2s_sa1"),
    (0x60, "mm2s_sa2"),
    (0x64, "mm2s_sa3"),
    (0x68, "mm2s_sa4"),
)

MAX_READS_PER_SAMPLE = len(SAFE_REGISTERS)
MAX_SAMPLES = 2
DEFAULT_GAP_S = 0.05

_ERROR_FIELDS = ((4, "vdma_int_err"), (5, "vdma_slv_err"),
                 (6, "vdma_dec_err"), (7, "sof_early_err"))

# MM2S_VDMASR error bits: VDMAIntErr(4), VDMASlvErr(5), VDMADecErr(6),
# SOFEarlyErr(7). Bits 8-10 belong to the AXI DMA register layout and are
# not implemented here, so the AXI DMA mask 0x770 must not be reused.
MM2S_ERROR_MASK = sum(1 << bit for bit, _ in _ERROR_FIELDS)


def _read_sample(read32):
    values = {}
    errors = []
    for offset, name in SAFE_REGISTERS:
        try:
            values[name] = int(read32(offset)) & 0xFFFFFFFF
        except BaseException as exc:  # a failed read must not stop the capture
            errors.append({"offset": hex(offset), "name": name, "error": repr(exc)})
    return values, errors


def _decode(values):
    control = values.get("mm2s_vdmaccr")
    status = values.get("mm2s_vdmasr")
    park = values.get("park_ptr")
    decoded = {
        "running": None if control is None else bool(control & 0x1),
        "circular": None if control is None else bool(control & 0x2),
        "frmcnt_irq_en": None if control is None else bool(control & 0x1000),
        "irq_frame_count": None if control is None else (control >> 16) & 0xFF,
        "halted": None if status is None else bool(status & 0x1),
        "error_bits": (None if status is None else
                       [name for bit, name in _ERROR_FIELDS if status & (1 << bit)]),
        "frmcnt_irq": None if status is None else bool(status & 0x1000),
        "irq_frame_cnt_sts": None if status is None else (status >> 16) & 0xFF,
        "rd_frm_ptr_ref": None if park is None else park & 0x1F,
        "rd_frm_store": None if park is None else (park >> 16) & 0x1F,
        "frame_stores": [values.get(name) for name in
                         ("mm2s_sa1", "mm2s_sa2", "mm2s_sa3", "mm2s_sa4")],
        "vsize": values.get("mm2s_vsize"),
        "hsize": values.get("mm2s_hsize"),
        "stride": (None if values.get("mm2s_frmdly_stride") is None
                   else values["mm2s_frmdly_stride"] & 0xFFFF),
    }
    return decoded


def capture_vdma_snapshot(read32, *, samples=MAX_SAMPLES, gap_s=DEFAULT_GAP_S):
    """Read the MM2S register set once or twice and return a bounded dict.

    ``read32(offset)`` must be side-effect free. A failing read is recorded
    and skipped; the function never raises and never writes a register.
    ``rd_frm_store_moved`` is the only field that can show whether hardware
    frame-store progress continues between the two samples.
    """
    started = time.monotonic()
    snapshot = {
        "schema": SCHEMA,
        "monotonic": started,
        "samples": [],
        "read_errors": [],
        "reads": 0,
        "decoded": {},
    }
    sample_count = 1 if samples is None or samples < 2 else MAX_SAMPLES
    try:
        for index in range(sample_count):
            if index:
                time.sleep(max(0.0, float(gap_s)))
            values, errors = _read_sample(read32)
            snapshot["reads"] += MAX_READS_PER_SAMPLE
            snapshot["samples"].append(values)
            snapshot["read_errors"].extend(errors)
        snapshot["decoded"] = _decode(snapshot["samples"][0])
        if sample_count > 1:
            first = snapshot["samples"][0].get("park_ptr")
            second = snapshot["samples"][1].get("park_ptr")
            if first is not None and second is not None:
                snapshot["decoded"]["rd_frm_store_moved"] = (
                    (first >> 16) & 0x1F) != ((second >> 16) & 0x1F)
            else:
                snapshot["decoded"]["rd_frm_store_moved"] = None
            first_irq = snapshot["samples"][0].get("mm2s_vdmasr")
            second_irq = snapshot["samples"][1].get("mm2s_vdmasr")
            if first_irq is not None and second_irq is not None:
                snapshot["decoded"]["frmcnt_irq_observed"] = bool(
                    (first_irq | second_irq) & 0x1000)
            else:
                snapshot["decoded"]["frmcnt_irq_observed"] = None
    except BaseException as exc:  # evidence collection must never propagate
        snapshot["snapshot_error"] = repr(exc)
    return snapshot