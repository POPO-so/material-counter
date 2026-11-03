"""Offline tests for the three-state pre-load policy and stage persistence."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RUNNER_PATH = Path(__file__).with_name("run_jpeg_counter.py")
SPEC = importlib.util.spec_from_file_location("jpeg_preload_policy_tests", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

CANDIDATE_SHA = "1" * 64
BASE_SHA256 = runner.KNOWN_BASE_BIT_SHA256
BASE_SHA1 = runner.KNOWN_BASE_BIT_SHA1
BASE_BIT_PATH = ("/usr/local/share/pynq-venv/lib/python3.10/site-packages/"
                 "pynq/overlays/base/base.bit")
BASE_PYNQ = {"pl_state_status": "GLOBAL_STATE_PRESENT", "shutdown_ips": {},
             "loaded_bit_path": BASE_BIT_PATH,
             "loaded_bit_sha1": BASE_SHA1, "loaded_bit_sha256": BASE_SHA256}
COLD_PYNQ = {"pl_state_status": "COLD_BOOT_NO_GLOBAL_STATE", "shutdown_ips": {}}
COLD_BOOT = {"boot_id": "boot", "fpga_manager": {"state": "operating", "flags": "0"},
             "boot_bitstream": "base.bin"}
EMPTY_OWNERS = {"findings": [], "denied": [], "scanned_proc": "/proc"}


def base_hashes(_path):
    return {"sha1": BASE_SHA1, "sha256": BASE_SHA256}


def base_evidence(base):
    return {"hwh_path": base["hwh_path"],
            "hwh_sha256": runner.KNOWN_BASE_HWH_SHA256,
            "bus_masters": [dict(item) for item in base["bus_masters"]]}


class FakeMmio:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def read(self, offset):
        return self.values.get(offset, 0)

    def write(self, offset, value):
        self.values[offset] = value


class FakeMmioFactory:
    def __init__(self, events, *, control_signature=0x31454C52,
                 mm2s_status=0x3, s2mm_status=0x3,
                 vdma_mm2s_control=0x0, vdma_mm2s_status=0x1,
                 vdma_s2mm_control=0x0, vdma_s2mm_status=0x1,
                 trace_pi_control=0x0, trace_pi_status=0x1,
                 trace_pmodb_control=0x0, trace_pmodb_status=0x1):
        self.events = events
        self.control_signature = control_signature
        self.mm2s_status = mm2s_status
        self.s2mm_status = s2mm_status
        self.vdma_mm2s_control = vdma_mm2s_control
        self.vdma_mm2s_status = vdma_mm2s_status
        self.vdma_s2mm_control = vdma_s2mm_control
        self.vdma_s2mm_status = vdma_s2mm_status
        self.trace_pi_control = trace_pi_control
        self.trace_pi_status = trace_pi_status
        self.trace_pmodb_control = trace_pmodb_control
        self.trace_pmodb_status = trace_pmodb_status
        self.calls = []

    def __call__(self, base, size):
        self.calls.append((base, size))
        self.events.append(("mmio", base))
        if base == runner.RLE_BASE:
            return FakeMmio({0x04: self.control_signature})
        if base == runner.DMA_BASE:
            return FakeMmio({0x04: self.mm2s_status, 0x34: self.s2mm_status})
        if base == runner.BASE_VDMA_BASE:
            return FakeMmio({0x00: self.vdma_mm2s_control,
                             0x04: self.vdma_mm2s_status,
                             0x30: self.vdma_s2mm_control,
                             0x34: self.vdma_s2mm_status})
        if base == 0x80430000:
            return FakeMmio({0x30: self.trace_pi_control,
                             0x34: self.trace_pi_status})
        if base == 0x80420000:
            return FakeMmio({0x30: self.trace_pmodb_control,
                             0x34: self.trace_pmodb_status})
        return FakeMmio()


class FakeDisplay:
    def __init__(self):
        self.started = False
        self.closed = False

    def start(self):
        self.started = True
        return self

    def stop(self):
        self.closed = True

    def dma_uncertain(self):
        return False

    def state(self):
        return {"started": self.started, "closed": self.closed}


def classification(kind="COLD_BASE", *, pynq_state=None, boot_state=None,
                   owners=None, known=None, hash_reader=base_hashes):
    return runner.classify_preload_state(
        pynq_state=pynq_state if pynq_state is not None else COLD_PYNQ,
        boot_state=boot_state if boot_state is not None else COLD_BOOT,
        owners=owners if owners is not None else EMPTY_OWNERS,
        known=known if known is not None else runner.known_overlays(CANDIDATE_SHA),
        hash_reader=hash_reader)


def base_classification(*, pynq_state=None, boot_state=None, owners=None,
                        hash_reader=base_hashes):
    return classification(
        pynq_state=pynq_state if pynq_state is not None else BASE_PYNQ,
        boot_state=boot_state, owners=owners, hash_reader=hash_reader)


def run_sequence(cls, events, *, overlay_factory, identity_failure=False,
                 factory=None, mm2s_status=0x3, s2mm_status=0x3,
                 vdma_mm2s_control=0x0, vdma_mm2s_status=0x1,
                 vdma_s2mm_control=0x0, vdma_s2mm_status=0x1,
                 trace_pi_control=0x0, trace_pi_status=0x1,
                 trace_pmodb_control=0x0, trace_pmodb_status=0x1,
                 base_evidence_reader=base_evidence,
                 stop_base_dmas=False, stop_kwargs=None):
    factory = factory or FakeMmioFactory(
        events, mm2s_status=mm2s_status, s2mm_status=s2mm_status,
        vdma_mm2s_control=vdma_mm2s_control,
        vdma_mm2s_status=vdma_mm2s_status,
        vdma_s2mm_control=vdma_s2mm_control,
        vdma_s2mm_status=vdma_s2mm_status,
        trace_pi_control=trace_pi_control, trace_pi_status=trace_pi_status,
        trace_pmodb_control=trace_pmodb_control,
        trace_pmodb_status=trace_pmodb_status)

    def post_load_identity(overlay):
        events.append(("identity",))
        if identity_failure:
            raise RuntimeError("loaded BIT identity mismatch")
        return {"pl_state_status": "GLOBAL_STATE_PRESENT"}

    def prepare(overlay):
        events.append(("prepare",))
        return FakeDisplay()

    return runner.run_candidate_sequence(
        classification=cls,
        mmio_factory=factory,
        overlay_factory=overlay_factory,
        post_load_identity=post_load_identity,
        candidate_dma_check=lambda: runner.check_candidate_dma_idle(
            mmio_factory=factory),
        hp1_sync=lambda: events.append(("hp1_sync",)) or {
            "status": "test-only HP1 sync"},
        preview_factory=lambda: events.append(("preview",)) or "preview",
        prepare_display_fn=prepare,
        allocate_tx=lambda: events.append(("tx",)) or "tx",
        allocate_rx=lambda: events.append(("rx",)) or "rx",
        make_transport=lambda tx, rx, overlay, preview: (
            events.append(("transport",)) or "transport"),
        base_evidence_reader=base_evidence_reader,
        stop_base_dmas=stop_base_dmas, stop_kwargs=stop_kwargs,
        stage=lambda name, **fields: events.append(("stage", name)),
    )


def test_cold_base_never_touches_candidate_mmio_before_overlay():
    events = []
    cls = classification()
    assert cls["kind"] == "COLD_BASE"
    result = run_sequence(
        cls, events,
        overlay_factory=lambda: events.append(("overlay",)) or "overlay")
    first_mmio = next(index for index, event in enumerate(events)
                      if event[0] == "mmio")
    overlay_index = events.index(("overlay",))
    assert first_mmio > overlay_index
    assert events.index(("stage", "overlay_download")) < overlay_index
    assert result["probe"] is None
    assert result["candidate_dma"] == {"mm2s": 0x3, "s2mm": 0x3}
    assert result["display"].started is True


def test_unknown_state_stops_before_overlay_download():
    unknown = classification(
        pynq_state={"pl_state_status": "COLD_BOOT_NO_GLOBAL_STATE",
                    "shutdown_ips": {}},
        boot_state={"fpga_manager": {"state": "operating", "flags": "0"},
                    "boot_bitstream": None})
    assert unknown["kind"] == "UNKNOWN"
    events = []
    with pytest.raises(RuntimeError):
        run_sequence(unknown, events,
                     overlay_factory=lambda: events.append(("overlay",)) or "overlay")
    assert events == []


def test_unknown_loaded_overlay_identity_is_refused():
    unknown = classification(
        pynq_state={"pl_state_status": "GLOBAL_STATE_PRESENT",
                    "loaded_bit_sha256": "2" * 64, "shutdown_ips": {}})
    assert unknown["kind"] == "UNKNOWN"
    assert "known table" in unknown["reason"]


def test_known_base_exact_content_is_accepted():
    cls = base_classification()
    assert cls["kind"] == "KNOWN_BASE"
    assert cls["candidate_mmio_before_load"] is False
    assert Path(cls["base"]["bit_path"]) == Path(BASE_BIT_PATH)
    assert Path(cls["base"]["hwh_path"]) == Path(BASE_BIT_PATH).with_suffix(".hwh")


def test_known_base_same_path_different_hash_is_refused():
    state = {**BASE_PYNQ, "loaded_bit_sha256": "2" * 64}
    cls = classification(pynq_state=state, hash_reader=base_hashes)
    assert cls["kind"] == "UNKNOWN"
    assert "known table" in cls["reason"]


def test_known_base_actual_content_mismatch_is_refused():
    cls = classification(
        pynq_state=BASE_PYNQ,
        hash_reader=lambda _path: {"sha1": BASE_SHA1, "sha256": "3" * 64})
    assert cls["kind"] == "UNKNOWN"
    assert "SHA-256" in cls["reason"]


def test_known_base_state_sha1_inconsistency_is_refused():
    cls = classification(
        pynq_state={**BASE_PYNQ, "loaded_bit_sha1": "4" * 40},
        hash_reader=base_hashes)
    assert cls["kind"] == "UNKNOWN"
    assert "SHA-1" in cls["reason"]


def test_known_base_missing_bit_file_is_refused():
    def missing(_path):
        raise FileNotFoundError("base.bit")

    cls = classification(pynq_state=BASE_PYNQ, hash_reader=missing)
    assert cls["kind"] == "UNKNOWN"
    assert "cannot be hashed" in cls["reason"]


def test_known_base_requires_boot_base_and_empty_shutdown():
    wrong_boot = base_classification(
        boot_state={"fpga_manager": {"state": "operating", "flags": "0"},
                    "boot_bitstream": "other.bin"})
    assert wrong_boot["kind"] == "UNKNOWN"
    shutdown = base_classification(
        pynq_state={**BASE_PYNQ, "shutdown_ips": {"axi_dma": {}}})
    assert shutdown["kind"] == "UNKNOWN"


def test_known_base_owner_presence_is_unknown():
    cls = base_classification(
        owners={"findings": [{"pid": 1, "reasons": ["fpga"]}], "denied": []})
    assert cls["kind"] == "UNKNOWN"


def test_known_base_probes_only_confirmed_vdma_before_overlay():
    cls = base_classification()
    events = []
    result = run_sequence(
        cls, events,
        overlay_factory=lambda: events.append(("overlay",)) or "overlay")
    overlay_index = events.index(("overlay",))
    pre_mmio = [event[1] for event in events[:overlay_index] if event[0] == "mmio"]
    assert pre_mmio == [runner.BASE_VDMA_BASE, 0x80430000, 0x80420000]
    for candidate_base in (runner.DMA_BASE, runner.RLE_BASE,
                           runner.OVERLAY_BASE, runner.JPEG_BASE):
        assert candidate_base not in pre_mmio
    assert result["probe"]["bus_masters"]["video_axi_vdma"]["mm2s"] == {
        "type": "axi_vdma",
        "control": 0x0, "status": 0x1, "rs": False, "halted": True,
        "reserved_bit1": 0, "error_mask": 0x00F0,
        "error_bits": [], "reserved_bits": 0}
    assert result["probe"]["bus_masters"]["video_axi_vdma"]["s2mm"] == {
        "type": "axi_vdma",
        "control": 0x0, "status": 0x1, "rs": False, "halted": True,
        "reserved_bit1": 0, "error_mask": 0x89F0,
        "error_bits": [], "reserved_bits": 0}
    for trace in ("trace_analyzer_pi_axi_dma_0",
                  "trace_analyzer_pmodb_axi_dma_0"):
        assert result["probe"]["bus_masters"][trace]["s2mm"] == {
            "type": "axi_dma",
            "control": 0x0, "status": 0x1, "rs": False, "halted": True,
            "reserved_bit1": 0, "error_mask": 0x0770,
            "error_bits": [], "reserved_bits": 0}


def test_known_base_active_vdma_is_refused_before_overlay():
    cls = base_classification()
    events = []
    factory = FakeMmioFactory(events, vdma_mm2s_status=0x0)
    with pytest.raises(runner.PreloadUnsafe):
        run_sequence(cls, events,
                     overlay_factory=lambda: events.append(("overlay",)) or "overlay",
                     factory=factory)
    assert ("overlay",) not in events
    assert all(event[1] == runner.BASE_VDMA_BASE
               for event in events if event[0] == "mmio")


def test_known_base_unverified_hwh_stops_before_mmio_and_overlay():
    def unsafe(_base):
        raise runner.PreloadUnsafe("base HWH bus-master map differs")

    cls = base_classification()
    events = []
    with pytest.raises(runner.PreloadUnsafe):
        run_sequence(
            cls, events,
            overlay_factory=lambda: events.append(("overlay",)) or "overlay",
            base_evidence_reader=unsafe)
    assert not any(event[0] == "mmio" for event in events)
    assert ("overlay",) not in events


BASE_HWH_TEMPLATE = """<?xml version="1.0"?>
<EDKSYSTEM>
  <MODULE INSTANCENAME="video_axi_vdma" VLNV="xilinx.com:ip:axi_vdma:6.3">
    <PARAMETERS>
      <PARAMETER NAME="C_BASEADDR" VALUE="0x43000000"/>
      <PARAMETER NAME="C_HIGHADDR" VALUE="0x4300FFFF"/>
      <PARAMETER NAME="C_INCLUDE_MM2S" VALUE="1"/>
      <PARAMETER NAME="C_INCLUDE_S2MM" VALUE="1"/>
    </PARAMETERS>
  </MODULE>
  <MODULE INSTANCENAME="trace_analyzer_pi_axi_dma_0" VLNV="xilinx.com:ip:axi_dma:7.1">
    <PARAMETERS>
      <PARAMETER NAME="C_BASEADDR" VALUE="0x80430000"/>
      <PARAMETER NAME="C_HIGHADDR" VALUE="0x8043FFFF"/>
      <PARAMETER NAME="C_INCLUDE_MM2S" VALUE="0"/>
      <PARAMETER NAME="C_INCLUDE_S2MM" VALUE="1"/>
    </PARAMETERS>
  </MODULE>
  <MODULE INSTANCENAME="trace_analyzer_pmodb_axi_dma_0" VLNV="xilinx.com:ip:axi_dma:7.1">
    <PARAMETERS>
      <PARAMETER NAME="C_BASEADDR" VALUE="0x80420000"/>
      <PARAMETER NAME="C_HIGHADDR" VALUE="0x8042FFFF"/>
      <PARAMETER NAME="C_INCLUDE_MM2S" VALUE="0"/>
      <PARAMETER NAME="C_INCLUDE_S2MM" VALUE="1"/>
    </PARAMETERS>
  </MODULE>
  {extra}
</EDKSYSTEM>
"""

EXTRA_DMA = """<MODULE INSTANCENAME="axi_dma_0" VLNV="xilinx.com:ip:axi_dma:7.1">
    <PARAMETERS><PARAMETER NAME="C_BASEADDR" VALUE="0x40400000"/></PARAMETERS>
  </MODULE>"""


def test_base_hwh_matches_the_pinned_bus_master_map(tmp_path, monkeypatch):
    hwh = tmp_path / "base.hwh"
    hwh.write_text(BASE_HWH_TEMPLATE.format(extra=""), encoding="utf-8")
    monkeypatch.setattr(runner, "_sha256",
                        lambda _path: runner.KNOWN_BASE_HWH_SHA256)
    base = {**runner.known_bases()[0], "hwh_path": str(hwh)}
    evidence = runner.read_base_hwh_evidence(base)
    actual = {(item["instance"], item["vlnv"], item["type"],
               item["base"], item["channels"])
              for item in evidence["bus_masters"]}
    assert actual == {
        ("video_axi_vdma", "xilinx.com:ip:axi_vdma:6.3", "axi_vdma",
         0x43000000, ("mm2s", "s2mm")),
        ("trace_analyzer_pi_axi_dma_0", "xilinx.com:ip:axi_dma:7.1",
         "axi_dma", 0x80430000, ("s2mm",)),
        ("trace_analyzer_pmodb_axi_dma_0", "xilinx.com:ip:axi_dma:7.1",
         "axi_dma", 0x80420000, ("s2mm",)),
    }


def test_base_hwh_additional_dma_master_is_refused(tmp_path, monkeypatch):
    hwh = tmp_path / "base.hwh"
    hwh.write_text(BASE_HWH_TEMPLATE.format(extra=EXTRA_DMA), encoding="utf-8")
    monkeypatch.setattr(runner, "_sha256",
                        lambda _path: runner.KNOWN_BASE_HWH_SHA256)
    base = {**runner.known_bases()[0], "hwh_path": str(hwh)}
    with pytest.raises(runner.PreloadUnsafe):
        runner.read_base_hwh_evidence(base)


def test_base_hwh_hash_mismatch_is_refused(tmp_path):
    hwh = tmp_path / "base.hwh"
    hwh.write_text(BASE_HWH_TEMPLATE.format(extra=""), encoding="utf-8")
    base = {**runner.known_bases()[0], "hwh_path": str(hwh)}
    with pytest.raises(runner.PreloadUnsafe, match="SHA-256"):
        runner.read_base_hwh_evidence(base)


def test_base_hwh_missing_channel_flags_is_refused(tmp_path, monkeypatch):
    hwh = tmp_path / "base.hwh"
    hwh.write_text(
        BASE_HWH_TEMPLATE.format(extra="").replace(
            '      <PARAMETER NAME="C_INCLUDE_MM2S" VALUE="1"/>\n'
            '      <PARAMETER NAME="C_INCLUDE_S2MM" VALUE="1"/>\n', ""),
        encoding="utf-8")
    monkeypatch.setattr(runner, "_sha256",
                        lambda _path: runner.KNOWN_BASE_HWH_SHA256)
    base = {**runner.known_bases()[0], "hwh_path": str(hwh)}
    with pytest.raises(runner.PreloadUnsafe, match="C_INCLUDE"):
        runner.read_base_hwh_evidence(base)


VDMA_FAILURES = [
    ("mm2s", {"vdma_mm2s_status": 0x1 | (1 << 7)}, "sof_early_err"),
    ("s2mm", {"vdma_s2mm_status": 0x1 | (1 << 15)}, "eol_late_err"),
    ("mm2s", {"vdma_mm2s_control": 0x1}, "not safely halted"),
    ("mm2s", {"vdma_mm2s_status": 0x1 | (1 << 9)}, "reserved"),
    ("s2mm", {"trace_pi_status": 0x1 | (1 << 4)}, "dma_int_err"),
    ("s2mm", {"trace_pmodb_status": 0x1 | (1 << 8)}, "sg_int_err"),
    ("s2mm", {"trace_pi_control": 0x1}, "not safely halted"),
    ("s2mm", {"trace_pi_status": 0x1 | (1 << 2)}, "reserved"),
]

CONFIRMED_BASE_MMIO = {runner.BASE_VDMA_BASE, 0x80420000, 0x80430000}
CANDIDATE_BASE_MMIO = {runner.DMA_BASE, runner.RLE_BASE,
                       runner.OVERLAY_BASE, runner.JPEG_BASE}


@pytest.mark.parametrize("channel,kwargs,reason", VDMA_FAILURES)
def test_known_base_unsafe_bus_master_is_refused_before_overlay(
        channel, kwargs, reason):
    events = []
    factory = FakeMmioFactory(events, **kwargs)
    with pytest.raises(runner.PreloadUnsafe, match=reason):
        run_sequence(
            base_classification(), events,
            overlay_factory=lambda: events.append(("overlay",)) or "overlay",
            factory=factory)
    assert ("overlay",) not in events
    touched = {event[1] for event in events if event[0] == "mmio"}
    assert touched <= CONFIRMED_BASE_MMIO
    assert not touched & CANDIDATE_BASE_MMIO


def test_axi_dma_masks_match_pg021_and_do_not_gate_idle():
    assert runner.AXI_DMA_CHANNELS["s2mm"]["error_mask"] == 0x0770
    assert runner.AXI_DMA_CHANNELS["s2mm"]["reserved_mask"] == 0x8884
    assert runner.AXI_DMA_CHANNELS["s2mm"]["reserved_mask"] & (1 << 1) == 0
    assert runner.AXI_DMA_CHANNELS["s2mm"]["reserved_mask"] & (1 << 3) == 0


def test_vdma_masks_are_channel_specific_not_the_axi_dma_mask():
    assert runner.VDMA_CHANNELS["mm2s"]["error_mask"] == 0x00F0
    assert runner.VDMA_CHANNELS["s2mm"]["error_mask"] == 0x89F0
    assert runner.VDMA_CHANNELS["mm2s"]["error_mask"] != 0x770
    assert runner.VDMA_CHANNELS["s2mm"]["error_mask"] != 0x770
    assert runner.VDMA_CHANNELS["mm2s"]["reserved_mask"] & (1 << 1)
    assert runner.VDMA_CHANNELS["s2mm"]["reserved_mask"] & (1 << 1)


def test_known_base_old_paths_still_work():
    old = runner.known_overlays(CANDIDATE_SHA)[1]
    known = classification(
        pynq_state={"pl_state_status": "GLOBAL_STATE_PRESENT",
                    "loaded_bit_sha256": old["sha256"], "shutdown_ips": {}})
    assert known["kind"] == "KNOWN_OLD"
    assert classification()["kind"] == "COLD_BASE"


class FakeStopDevice:
    def __init__(self, *, control=0x00011003, status=0x00000000,
                 stop_works=True, error_after=False, rearm=False):
        self.regs = {0x30: control, 0x34: status}
        self.writes = []
        self.stop_works = stop_works
        self.error_after = error_after
        self.rearm = rearm
        self.post_write_control_reads = 0

    def read(self, offset):
        if offset == 0x30 and self.writes:
            self.post_write_control_reads += 1
            if self.rearm and self.post_write_control_reads >= 2:
                return self.regs[0x30] | 0x1
        return self.regs.get(offset, 0)

    def write(self, offset, value):
        self.writes.append((offset, value))
        self.regs[offset] = value
        if offset == 0x30 and not (value & 0x1) and self.stop_works:
            self.regs[0x34] |= 0x1
            if self.error_after:
                self.regs[0x34] |= 0x10


def stop_clock():
    stop_clock.value += 1
    return stop_clock.value


stop_clock.value = 0


def stop_devices(*, pi=None, pmodb=None):
    return {0x80430000: pi if pi is not None else FakeStopDevice(),
            0x80420000: pmodb if pmodb is not None else FakeStopDevice()}


def stop_factory(devices):
    return lambda base, size: devices[base]


def call_stop(devices, **kwargs):
    stop_clock.value = 0
    kwargs.setdefault("timeout_s", 1.0)
    kwargs.setdefault("stable_s", 0.0)
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("clock", stop_clock)
    return runner.stop_base_trace_dmas(
        base_classification(), mmio_factory=stop_factory(devices), **kwargs)


def test_stop_base_trace_dmas_writes_only_rs_on_the_two_channels():
    devices = stop_devices()
    result = call_stop(devices)
    for device in devices.values():
        assert device.writes == [(0x30, 0x00011002)]
    assert result["trace_analyzer_pi_axi_dma_0"]["after"]["halted"] is True
    assert result["trace_analyzer_pmodb_axi_dma_0"]["after"]["halted"] is True


def test_stop_base_trace_dmas_is_idempotent_when_already_halted():
    devices = stop_devices(
        pi=FakeStopDevice(control=0x00011002, status=0x00000001),
        pmodb=FakeStopDevice(control=0x00011002, status=0x00000001))
    result = call_stop(devices)
    for device in devices.values():
        assert device.writes == []
    assert result["trace_analyzer_pi_axi_dma_0"]["wrote_rs_clear"] is False


def test_stop_base_trace_dmas_timeout_does_not_reset_or_clear():
    pi = FakeStopDevice(stop_works=False)
    devices = stop_devices(pi=pi)
    with pytest.raises(runner.DmaStopTimeout):
        call_stop(devices)
    assert pi.writes == [(0x30, 0x00011002)]
    assert pi.writes[0][1] & 0x4 == 0
    assert all(offset != 0x34 for offset, _ in pi.writes)


def test_stop_base_trace_dmas_reports_error_without_clearing_it():
    pi = FakeStopDevice(error_after=True)
    devices = stop_devices(pi=pi)
    with pytest.raises(runner.PreloadUnsafe, match="error/reserved"):
        call_stop(devices)
    assert pi.writes == [(0x30, 0x00011002)]
    assert pi.regs[0x34] & 0x10


def test_stop_base_trace_dmas_detects_rearm():
    pi = FakeStopDevice(rearm=True)
    devices = stop_devices(pi=pi)
    with pytest.raises(runner.PreloadUnsafe, match="re-armed"):
        call_stop(devices)
    assert pi.writes == [(0x30, 0x00011002)]


def test_stop_base_trace_dmas_rejects_unexpected_target_set():
    cls = base_classification()
    cls["base"]["bus_masters"] = tuple(
        m for m in cls["base"]["bus_masters"]
        if m["instance"] != "trace_analyzer_pmodb_axi_dma_0")
    called = []
    with pytest.raises(runner.PreloadUnsafe, match="unexpected base axi_dma"):
        runner.stop_base_trace_dmas(
            cls, mmio_factory=lambda base, size: called.append(base))
    assert called == []


def test_run_sequence_stops_then_rechecks_before_overlay():
    events = []
    devices = stop_devices()
    devices[0x43000000] = FakeMmio({
        0x00: 0x00010082, 0x04: 0x00010001,
        0x30: 0x00010002, 0x34: 0x00010001})
    devices[runner.DMA_BASE] = FakeMmio({0x04: 0x3, 0x34: 0x3})
    result = run_sequence(
        base_classification(), events,
        overlay_factory=lambda: events.append(("overlay",)) or "overlay",
        factory=stop_factory(devices), stop_base_dmas=True,
        stop_kwargs={"timeout_s": 1.0, "stable_s": 0.0,
                     "sleep": lambda _seconds: None, "clock": stop_clock})
    for base in (0x80430000, 0x80420000):
        assert [write for write in devices[base].writes
                if write[0] != 0x30] == []
    assert result["base_dma_stop"]["trace_analyzer_pi_axi_dma_0"][
        "after"]["halted"] is True
    names = [event[1] for event in events if event[0] == "stage"]
    assert names.index("known_base_dma_stop") < names.index(
        "known_base_dma_stop_done") < names.index("known_base_probe_done")
    assert names.index("known_base_probe_done") < names.index("overlay_download")


def test_run_sequence_without_stop_flag_never_writes():
    events = []
    devices = stop_devices()
    devices[0x43000000] = FakeMmio({
        0x00: 0x00010082, 0x04: 0x00010001,
        0x30: 0x00010002, 0x34: 0x00010001})
    devices[runner.DMA_BASE] = FakeMmio({0x04: 0x3, 0x34: 0x3})
    with pytest.raises(runner.PreloadUnsafe):
        run_sequence(
            base_classification(), events,
            overlay_factory=lambda: events.append(("overlay",)) or "overlay",
            factory=stop_factory(devices), stop_base_dmas=False)
    for device in devices.values():
        assert getattr(device, "writes", []) == []


def test_cma_budget_covers_explicit_buffers_and_reserve():
    budget = runner.cma_budget()
    assert budget["explicit_bytes"] == runner.JPEG_MAX_BYTES + runner.RX_CAPACITY
    assert budget["required_kib"] == (
        (budget["explicit_bytes"] + 1023) // 1024 + runner.CMA_RESERVE_KIB)
    assert budget["required_kib"] > budget["reserve_kib"]


def test_known_old_overlay_only_reads_its_confirmed_addresses():
    old = runner.known_overlays(CANDIDATE_SHA)[1]
    known = classification(
        pynq_state={"pl_state_status": "GLOBAL_STATE_PRESENT",
                    "loaded_bit_sha256": old["sha256"], "shutdown_ips": {}})
    assert known["kind"] == "KNOWN_OLD"
    events = []
    result = run_sequence(
        known, events,
        overlay_factory=lambda: events.append(("overlay",)) or "overlay")
    overlay_index = events.index(("overlay",))
    pre_mmio = [event[1] for event in events[:overlay_index] if event[0] == "mmio"]
    assert set(pre_mmio) == {runner.RLE_BASE, runner.DMA_BASE}
    assert runner.OVERLAY_BASE not in pre_mmio
    assert runner.JPEG_BASE not in pre_mmio
    assert result["probe"]["control_signature"] == 0x31454C52
    assert result["probe"]["dma"] == {"mm2s": 0x3, "s2mm": 0x3}


def test_known_old_active_dma_is_refused_before_overlay():
    old = runner.known_overlays(CANDIDATE_SHA)[1]
    known = classification(
        pynq_state={"pl_state_status": "GLOBAL_STATE_PRESENT",
                    "loaded_bit_sha256": old["sha256"], "shutdown_ips": {}})
    events = []
    factory = FakeMmioFactory(events, mm2s_status=0x0)
    with pytest.raises(runner.DmaOwnershipUncertain):
        run_sequence(known, events,
                     overlay_factory=lambda: events.append(("overlay",)) or "overlay",
                     factory=factory)
    assert ("overlay",) not in events


def test_overlay_load_failure_skips_all_post_load_steps():
    events = []
    cls = classification()

    def overlay():
        events.append(("overlay",))
        raise RuntimeError("download failed")

    with pytest.raises(RuntimeError):
        run_sequence(cls, events, overlay_factory=overlay)
    assert ("identity",) not in events
    assert ("prepare",) not in events
    assert ("tx",) not in events
    assert ("rx",) not in events
    assert ("transport",) not in events


def test_post_load_identity_failure_skips_dma_display_and_allocation():
    events = []
    cls = classification()
    with pytest.raises(RuntimeError):
        run_sequence(
            cls, events,
            overlay_factory=lambda: events.append(("overlay",)) or "overlay",
            identity_failure=True)
    assert ("prepare",) not in events
    assert ("tx",) not in events
    assert ("rx",) not in events
    assert ("transport",) not in events
    assert not any(event == ("stage", "candidate_dma") for event in events)


def test_normal_path_runs_post_load_dma_and_display_checks():
    events = []
    cls = classification()
    result = run_sequence(
        cls, events,
        overlay_factory=lambda: events.append(("overlay",)) or "overlay")
    order = [event[0] if event[0] != "stage" else f"stage:{event[1]}"
             for event in events]
    assert order.index("overlay") < order.index("identity")
    assert order.index("identity") < order.index("stage:candidate_dma")
    assert order.index("stage:candidate_dma") < order.index("prepare")
    assert order.index("prepare") < order.index("tx")
    assert order.index("tx") < order.index("rx")
    assert order.index("rx") < order.index("transport")
    assert result["candidate_dma"] == {"mm2s": 0x3, "s2mm": 0x3}


def test_cold_base_requires_boot_base_evidence_not_just_cold_state():
    cls = classification(boot_state={"fpga_manager": {"state": "operating", "flags": "0"},
                                     "boot_bitstream": "unknown.bin"})
    assert cls["kind"] == "UNKNOWN"
    assert "base.bin" in cls["reason"]


def test_owner_presence_is_unknown():
    cls = classification(owners={"findings": [{"pid": 1, "reasons": ["fpga"]}],
                                 "denied": []})
    assert cls["kind"] == "UNKNOWN"


def test_write_stage_persists_boot_record_and_flushes(tmp_path):
    output = tmp_path / "run.json"
    report = {"status": "STARTING", "boot_id_expected": "boot-1",
              "pynq_state_before": COLD_PYNQ, "preload_classification": classification()}
    runner.write_stage(output, report, "preflight", extra="value")
    stage_path = tmp_path / "run.json.stage.json"
    assert stage_path.is_file()
    payload = json.loads(stage_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "material-counter.jpeg-smoke-stage.v1"
    assert payload["stage"] == "preflight"
    assert payload["boot_id"] == "boot-1"
    assert payload["extra"] == "value"
    assert payload["report"]["preload_classification"]["kind"] == "COLD_BASE"