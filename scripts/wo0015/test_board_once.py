from __future__ import annotations

import sys
from pathlib import Path

import json
import types
import pytest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import board_preflight as preflight
import board_once as runner
from test_board_preflight import make_manifest, sha256


def _plan(tmp_path: Path, frame_count: int = 3):
    manifest_path, _ = make_manifest(tmp_path, frame_count=frame_count)
    return preflight.validate_manifest(manifest_path), manifest_path


def test_afi1_sync_sets_32bit_bits_and_preserves_other_register_bits(tmp_path):
    plan, _ = _plan(tmp_path)
    api = runner.MockBoardAPI(plan)
    api.physical_registers[runner.SLCR_FPGA_RST_CTRL] = 0
    api.physical_registers[runner.AFI1_RDCHAN_CTRL] = 0x00000000
    api.physical_registers[runner.AFI1_WRCHAN_CTRL] = 0x00000F00

    result = runner.sync_afi1_hp1_32bit(api, plan, stage="unit-test")

    assert result["hwh_hp1_data_width_bits"] == 32
    assert result["fpga_rst_ctrl"] == 0
    assert api.physical_registers[runner.AFI1_RDCHAN_CTRL] == 0x00000001
    assert api.physical_registers[runner.AFI1_WRCHAN_CTRL] == 0x00000F01
    assert api.physical_write_trace == [
        (runner.AFI1_RDCHAN_CTRL, 0x00000001),
        (runner.AFI1_WRCHAN_CTRL, 0x00000F01),
    ]
    assert [item["readback"] for item in result["registers"].values()] == [1, 0xF01]


def test_afi1_sync_refuses_write_until_fpga_reset_is_fully_released(tmp_path):
    plan, _ = _plan(tmp_path)
    api = runner.MockBoardAPI(plan)
    api.physical_registers[runner.SLCR_FPGA_RST_CTRL] = 0x00000001

    with pytest.raises(preflight.PreflightError, match="FPGA_RST_CTRL.*0x00000000"):
        runner.sync_afi1_hp1_32bit(api, plan, stage="unit-test")

    assert api.physical_write_trace == []


def test_afi1_sync_fails_when_bit0_readback_does_not_latch(tmp_path):
    plan, _ = _plan(tmp_path)
    api = runner.MockBoardAPI(plan)
    api.physical_registers[runner.SLCR_FPGA_RST_CTRL] = 0
    api.fail_afi1_readback = True

    with pytest.raises(preflight.PreflightError, match="AFI1_RDCHAN_CTRL.*readback.*bit 0"):
        runner.sync_afi1_hp1_32bit(api, plan, stage="unit-test")

    assert api.physical_write_trace == [(runner.AFI1_RDCHAN_CTRL, 1)]


def test_afi1_sync_requires_hwh_hp1_width_to_be_32(tmp_path):
    plan, _ = _plan(tmp_path)
    text = plan.hwh_path.read_text(encoding="utf-8")
    text = text.replace('NAME="PCW_S_AXI_HP1_DATA_WIDTH" VALUE="32"',
                        'NAME="PCW_S_AXI_HP1_DATA_WIDTH" VALUE="64"')
    plan.hwh_path.write_text(text, encoding="utf-8")
    api = runner.MockBoardAPI(plan)
    api.physical_registers[runner.SLCR_FPGA_RST_CTRL] = 0

    with pytest.raises(preflight.PreflightError, match="HWH.*HP1.*32-bit"):
        runner.sync_afi1_hp1_32bit(api, plan, stage="unit-test")

    assert api.physical_write_trace == []


def test_afi1_physical_writer_uses_writable_mapping_only_for_allowlisted_words():
    class Mapping(bytearray):
        def __init__(self, size):
            super().__init__(size)
            self.flushed = False
            self.closed = False

        def flush(self):
            self.flushed = True
            raise OSError(22, "Invalid argument")

        def close(self):
            self.closed = True

    events = []
    mapped = Mapping(4096)

    runner.write_afi1_phys32(
        runner.AFI1_WRCHAN_CTRL, 0x00000F01, page_size=4096,
        open_fn=lambda path, flags: events.append(("open", path, flags)) or 7,
        mmap_fn=lambda fd, size, **kwargs: events.append(("mmap", fd, size, kwargs)) or mapped,
        close_fn=lambda fd: events.append(("close", fd)),
    )

    assert int.from_bytes(mapped[0x14:0x18], "little") == 0x00000F01
    assert not mapped.flushed and mapped.closed
    assert events[0][0:2] == ("open", "/dev/mem")
    assert events[1] == ("mmap", 7, 4096, {"access": runner.mmap.ACCESS_WRITE, "offset": 0xF8009000})
    assert events[-1] == ("close", 7)

    with pytest.raises(ValueError, match="AFI1 control allowlist"):
        runner.write_afi1_phys32(runner.SLCR_FPGA_RST_CTRL, 0)


@pytest.mark.parametrize("timeout_s", [float("nan"), float("inf"), float("-inf")])
def test_run_frames_rejects_nonfinite_timeout_before_board_api_use(tmp_path, timeout_s):
    plan, _ = _plan(tmp_path, 1)

    with pytest.raises(ValueError, match="finite and positive"):
        runner.run_frames(plan, object(), timeout_s=timeout_s)


@pytest.mark.parametrize("timeout_s", [float("nan"), float("inf"), float("-inf")])
def test_dma_stage_rejects_nonfinite_timeout_before_pynq_or_guard(tmp_path, monkeypatch, timeout_s):
    plan, _ = _plan(tmp_path, 3)
    guard = tmp_path / "single-consumed.json"

    monkeypatch.setattr(runner, "_assert_no_terminal_failure",
                        lambda: pytest.fail("terminal gate reached before timeout validation"))
    monkeypatch.setattr(runner, "PynqBoardAPI",
                        lambda *_args, **_kwargs: pytest.fail("PYNQ API constructed before timeout validation"))
    monkeypatch.setattr(runner, "_guard_path", lambda _stage: guard)

    with pytest.raises(ValueError, match="finite and positive"):
        runner._dma_stage(plan, plan.boot_id, "dma-single", None, timeout_s)

    assert not guard.exists()


@pytest.mark.parametrize("frame_count", [1, 3])
def test_mock_dma_runs_fixed_frames_with_two_reused_buffers_and_s2mm_first(tmp_path, frame_count):
    plan, _ = _plan(tmp_path, frame_count)
    api = runner.MockBoardAPI(plan, initial_idle=False)

    result = runner.run_frames(plan, api, timeout_s=2.0)

    assert result["status"] == "DMA_ONCE_PASS"
    assert result["frame_count"] == frame_count
    assert [frame["frame_id"] for frame in result["frames"]] == [
        frame.frame_id for frame in plan.frames
    ]
    assert api.allocations == [preflight.TX_BYTES, preflight.RX_BYTES]
    assert api.free_count == 2
    assert api.cma_free_kib() == result["cma_free_before_kib"]
    assert result["dma_start_attempted"] is True
    assert result["transfer_started"] is True
    assert result["dma_may_be_active"] is False
    assert len(api.sync_to_device_trace) == frame_count * 2
    for index in range(frame_count):
        tx_sync, rx_sync = api.sync_to_device_trace[index * 2:index * 2 + 2]
        assert tx_sync[0] is api._mm2s_buffer
        assert tx_sync[1] == plan.frames[index].packet
        assert rx_sync[0] is api._s2mm_buffer
        assert rx_sync[1] == b"\xA5" * preflight.RX_BYTES

    starts = [item for item in api.write_trace
              if item[0] == preflight.DMA_BASE and item[1] in (0x00, 0x18, 0x28, 0x30, 0x48, 0x58)]
    per_frame = [
        (0x30, 1), (0x48, api.last_s2mm_address), (0x58, preflight.RX_BYTES),
        (0x00, 1), (0x18, api.last_mm2s_address), (0x28, preflight.TX_BYTES),
    ]
    # Addresses are fixed for the reusable buffers, so each start sequence is
    # compared after projecting away values that are the same for every frame.
    projected = [(offset, value if offset not in (0x18, 0x48) else offset)
                 for _, offset, value in starts]
    expected = [(offset, value if offset not in (0x18, 0x48) else offset)
                for _ in range(frame_count) for offset, value in per_frame]
    assert projected == expected
    arm_writes = [item for item in api.write_trace
                  if item[0] == preflight.CHECKER_BASE and item[1] == 0x5C]
    assert len(arm_writes) == frame_count
    assert all(value == 1 for _, _, value in arm_writes)
    assert all(not (value & 0x4) for _, offset, value in api.write_trace
               if offset in (0x00, 0x30))
    assert [frame["checker"]["pixels"] for frame in result["frames"]] == [76800] * frame_count
    assert [frame["checker"]["sofs"] for frame in result["frames"]] == [1] * frame_count
    assert [frame["checker"]["eols"] for frame in result["frames"]] == [240] * frame_count
    assert [frame["checker"]["foreground_pixels"] for frame in result["frames"]] == [38400] * frame_count
    assert all(frame["byte_mismatches"] == 0 for frame in result["frames"])


def test_first_pre_length_idle_zero_is_allowed_but_later_nonidle_stops_without_retry(tmp_path):
    plan, _ = _plan(tmp_path, 3)
    api = runner.MockBoardAPI(plan, initial_idle=False, force_nonidle_frame=2)

    with pytest.raises(runner.ExecutionFailure, match="frame 2.*not Idle") as caught:
        runner.run_frames(plan, api, timeout_s=1.0)

    assert caught.value.transfer_started is True  # Frame 1 transferred before frame 2 stopped.
    assert caught.value.dma_start_attempted is True
    assert caught.value.dma_may_be_active is False
    assert api.free_count == 2
    assert len([item for item in api.write_trace
                if item[0] == preflight.DMA_BASE and item[1] == 0x58]) == 1
    assert len([item for item in api.write_trace
                if item[0] == preflight.CHECKER_BASE and item[1] == 0x5C]) == 1


def test_dma_error_after_length_fail_holds_buffers_and_never_retries_or_frees(tmp_path):
    plan, _ = _plan(tmp_path, 1)
    api = runner.MockBoardAPI(plan, s2mm_error_frame=1)

    with pytest.raises(runner.ExecutionFailure, match="S2MM.*error") as caught:
        runner.run_frames(plan, api, timeout_s=1.0)

    assert caught.value.transfer_started is True
    assert api.free_count == 0
    assert len(api.allocations) == 2
    assert len([item for item in api.write_trace
                if item[0] == preflight.DMA_BASE and item[1] == 0x58]) == 1
    assert len([item for item in api.write_trace
                if item[0] == preflight.DMA_BASE and item[1] == 0x28]) == 1
    assert runner.QUARANTINED_BUFFERS
    assert caught.value.dma_start_attempted is True
    assert caught.value.dma_may_be_active is True


def test_tx_rx_overlapping_or_non_cma_buffers_fail_before_dma_start(tmp_path):
    plan, _ = _plan(tmp_path, 1)
    overlapping = runner.MockBoardAPI(plan, s2mm_address_override=0x10000000)
    with pytest.raises(runner.ExecutionFailure, match="overlap") as caught:
        runner.run_frames(plan, overlapping)
    assert caught.value.transfer_started is False
    assert caught.value.dma_may_be_active is False
    assert overlapping.free_count == 2
    assert not [item for item in overlapping.write_trace
                if item[0] == preflight.DMA_BASE and item[1] in (0x58, 0x28)]

    outside = runner.MockBoardAPI(plan)
    outside.cma_ranges = ((0x18000000, 0x20000000),)
    with pytest.raises(runner.ExecutionFailure, match="outside the live CMA"):
        runner.run_frames(plan, outside)
    assert outside.free_count == 2


def test_completed_pixel_mismatch_is_board_transfer_failure_without_dma_hold(tmp_path):
    plan, _ = _plan(tmp_path, 1)
    api = runner.MockBoardAPI(plan, corrupt_s2mm_frame=1)

    with pytest.raises(runner.ExecutionFailure, match="differs from golden") as caught:
        runner.run_frames(plan, api)

    assert caught.value.dma_start_attempted is True
    assert caught.value.transfer_started is True
    assert caught.value.dma_may_be_active is False
    assert caught.value.evidence["board_accessed"] is False  # Mock only
    assert caught.value.evidence["byte_mismatches"] == 1
    assert api.free_count == 2


def test_pynq_sync_methods_fail_closed_when_no_supported_cache_operation_exists(tmp_path):
    plan, _ = _plan(tmp_path, 1)
    api = runner.PynqBoardAPI(plan, stage="dma-single")
    buffer_without_sync = bytearray(preflight.RX_BYTES)
    with pytest.raises(RuntimeError, match="sync_to_device|flush"):
        api.sync_to_device(buffer_without_sync)
    with pytest.raises(RuntimeError, match="sync_from_device|invalidate"):
        api.sync_from_device(buffer_without_sync)


def test_overlay_stage_opens_only_checker_mmio_while_dma_stages_open_both(
    tmp_path, monkeypatch
):
    plan, _ = _plan(tmp_path, 3)
    opened = []

    class FakeMMIO:
        def __init__(self, base, length):
            opened.append((base, length))

    fake_pynq = types.SimpleNamespace(MMIO=FakeMMIO, allocate=lambda **_kwargs: None)
    monkeypatch.setitem(sys.modules, "pynq", fake_pynq)
    overlay_api = runner.PynqBoardAPI(plan, stage="overlay-only")
    overlay_api.open_mmio()
    assert opened == [(preflight.CHECKER_BASE, 0x10000)]
    assert set(overlay_api._mmio) == {preflight.CHECKER_BASE}

    opened.clear()
    dma_api = runner.PynqBoardAPI(plan, stage="dma-single")
    dma_api.open_mmio()
    assert set(opened) == {
        (preflight.CHECKER_BASE, 0x10000),
        (preflight.DMA_BASE, 0x10000),
    }
    assert set(dma_api._mmio) == {preflight.CHECKER_BASE, preflight.DMA_BASE}


def test_single_then_three_stage_uses_same_mocked_boot_manifest_without_overlay_reload(tmp_path):
    plan, _ = _plan(tmp_path, 3)
    api = runner.MockBoardAPI(plan)

    single = runner.run_frames(plan, api, timeout_s=2.0, frame_limit=1)
    three = runner.run_frames(plan, api, timeout_s=2.0, frame_limit=3,
                              expected_prior_frames=1)

    assert single["frame_count"] == 1
    assert three["frame_count"] == 3
    assert single["manifest_sha256"] == three["manifest_sha256"] == plan.manifest_sha256
    assert len(api.allocations) == 4  # two static buffers per one-shot process/stage
    assert api.free_count == 4
    assert [item["frame_id"] for item in three["frames"]] == [1, 2, 3]
    assert [item["checker"]["output_total_delta"] for item in three["frames"]] == [76800] * 3


def test_dma_single_receipt_is_required_as_matching_predecessor_for_three_frames(tmp_path):
    plan, _ = _plan(tmp_path, 3)
    receipt_path = tmp_path / "dma-single-pass.json"
    receipt = runner.write_dma_single_pass_receipt(receipt_path, plan)
    assert receipt["status"] == "DMA_SINGLE_PASS"
    runner.require_dma_single_pass_receipt(receipt_path, plan)

    receipt["manifest_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(preflight.PreflightError, match="manifest_sha256"):
        runner.require_dma_single_pass_receipt(receipt_path, plan)


def test_completed_live_pixel_mismatch_is_not_reported_as_unstarted_or_dma_hold(
    tmp_path, monkeypatch
):
    plan, _ = _plan(tmp_path, 3)
    overlay_receipt = tmp_path / "overlay-pass.json"
    runner.write_overlay_pass_receipt(overlay_receipt, plan)
    guard = tmp_path / "single-consumed.json"
    terminal = tmp_path / "terminal-failure.json"
    api = runner.MockBoardAPI(plan, corrupt_s2mm_frame=1)
    api._overlay_loaded = True
    api.board_accessed = True  # Model the live adapter after MMIO was opened.
    monkeypatch.setattr(runner, "OVERLAY_PASS_RECEIPT", overlay_receipt)
    monkeypatch.setattr(runner, "_guard_path", lambda _stage: guard)
    monkeypatch.setattr(runner, "TERMINAL_FAILURE", terminal)
    monkeypatch.setattr(runner, "PynqBoardAPI",
                        lambda _plan, stage, cma_ranges=None: api)

    with pytest.raises(runner.ExecutionFailure, match="differs from golden") as caught:
        runner._dma_stage(plan, plan.boot_id, "dma-single", None, 1.0)

    assert caught.value.board_accessed is True
    assert caught.value.evidence["board_accessed"] is True
    assert caught.value.evidence["transfer_started"] is True
    assert caught.value.evidence["dma_start_attempted"] is True
    assert caught.value.evidence["dma_may_be_active"] is False
    assert caught.value.evidence["status"] == "DMA_SINGLE_FAIL"
    assert terminal.exists() and guard.exists()
    assert api.free_count == 2


def test_uncertain_dma_always_holds_even_if_terminal_marker_write_fails(tmp_path, monkeypatch):
    plan, _ = _plan(tmp_path, 3)
    overlay_receipt = tmp_path / "overlay-pass.json"
    runner.write_overlay_pass_receipt(overlay_receipt, plan)
    api = runner.MockBoardAPI(plan, s2mm_error_frame=1)
    api._overlay_loaded = True
    api.board_accessed = True
    hold_calls = []

    class HoldReached(BaseException):
        pass

    installed_signals = []

    def fake_hold(_path, payload):
        hold_calls.append(payload)

    def fail_marker(*_args):
        assert {runner.signal.SIGINT, runner.signal.SIGTERM} <= set(installed_signals)
        raise OSError("marker disk full")

    monkeypatch.setattr(runner, "OVERLAY_PASS_RECEIPT", overlay_receipt)
    monkeypatch.setattr(runner, "_guard_path", lambda _stage: tmp_path / "single-consumed.json")
    monkeypatch.setattr(runner, "PynqBoardAPI",
                        lambda _plan, stage, cma_ranges=None: api)
    monkeypatch.setattr(runner.signal, "signal",
                        lambda signum, _handler: installed_signals.append(signum))
    monkeypatch.setattr(runner, "_record_terminal_failure", fail_marker)
    monkeypatch.setattr(runner, "_hold_forever", lambda: (_ for _ in ()).throw(HoldReached()))
    api.fail_hold = fake_hold

    with pytest.raises(HoldReached):
        runner._dma_stage(plan, plan.boot_id, "dma-single", None, 1.0)

    assert len(hold_calls) == 1
    assert hold_calls[0]["dma_may_be_active"] is True
    assert "terminal_marker_write_error" in hold_calls[0]
    assert api.free_count == 0
    assert runner.QUARANTINED_BUFFERS


def test_fail_hold_does_not_exit_when_evidence_json_write_fails(tmp_path, monkeypatch):
    plan, _ = _plan(tmp_path, 1)
    api = runner.PynqBoardAPI(plan, stage="dma-single")

    class HoldReached(BaseException):
        pass

    event_order = []

    def fake_signal(signum, _handler):
        event_order.append(("signal", signum))

    def fail_emit(*_args):
        installed = {signum for kind, signum in event_order if kind == "signal"}
        assert {runner.signal.SIGINT, runner.signal.SIGTERM} <= installed
        event_order.append(("emit", None))
        raise OSError("evidence disk full")

    monkeypatch.setattr(runner.signal, "signal", fake_signal)
    monkeypatch.setattr(runner, "_emit", fail_emit)
    monkeypatch.setattr(runner, "_hold_forever", lambda: (_ for _ in ()).throw(HoldReached()))

    with pytest.raises(HoldReached):
        api.fail_hold(str(tmp_path / "evidence.json"), {"dma_may_be_active": True})
    assert event_order[-1] == ("emit", None)
    assert all(kind == "signal" for kind, _ in event_order[:-1])


def test_dma_three_rejects_missing_single_pass_before_claiming_its_guard(tmp_path, monkeypatch):
    plan, _ = _plan(tmp_path, 3)
    overlay_receipt = tmp_path / "overlay-pass.json"
    runner.write_overlay_pass_receipt(overlay_receipt, plan)
    guard = tmp_path / "dma-three.guard"
    monkeypatch.setattr(runner, "OVERLAY_PASS_RECEIPT", overlay_receipt)
    monkeypatch.setattr(runner, "DMA_SINGLE_PASS_RECEIPT", tmp_path / "missing-single-pass.json")
    monkeypatch.setattr(runner, "_guard_path", lambda _stage: guard)

    with pytest.raises(preflight.PreflightError, match="dma-single PASS receipt"):
        runner._dma_stage(plan, plan.boot_id, "dma-three", None, 1.0)

    assert not guard.exists()


def test_overlay_only_loads_once_and_writes_same_boot_manifest_hash_receipt(tmp_path, monkeypatch, capsys):
    plan, _ = _plan(tmp_path, 3)
    guard = tmp_path / "overlay.guard"
    receipt_path = tmp_path / "overlay-pass.json"
    api = runner.MockBoardAPI(plan)
    monkeypatch.setattr(runner, "OVERLAY_GUARD", guard)
    monkeypatch.setattr(runner, "OVERLAY_PASS_RECEIPT", receipt_path)
    monkeypatch.setattr(runner, "PynqBoardAPI", lambda _plan, stage: api)

    result = runner._overlay_stage(plan, plan.boot_id, None)
    capsys.readouterr()
    receipt = runner.require_overlay_pass_receipt(receipt_path, plan)

    assert result["status"] == "OVERLAY_LOAD_PASS"
    assert api.overlay_load_count == 1
    assert [snapshot["stage"] for snapshot in result["afi1_snapshots"]] == [
        "before_overlay_load", "after_overlay_load"]
    before = result["afi1_snapshots"][0]["registers"]
    after = result["afi1_snapshots"][1]["registers"]
    assert before["AFI1_RDCHAN_CTRL"]["width_bit0"] == 0
    assert before["AFI1_WRCHAN_CTRL"]["raw_hex"] == "0x00000f00"
    assert after["AFI1_RDCHAN_CTRL"]["raw_hex"] == "0x00000001"
    assert after["AFI1_WRCHAN_CTRL"]["raw_hex"] == "0x00000f01"
    assert result["afi1_sync"]["hwh_hp1_data_width_bits"] == 32
    assert api.physical_read_trace == [
        runner.SLCR_FPGA0_CLK_CTRL, runner.SLCR_FPGA_RST_CTRL,
        runner.AFI1_RDCHAN_CTRL, runner.AFI1_WRCHAN_CTRL,
        runner.SLCR_FPGA_RST_CTRL, runner.AFI1_RDCHAN_CTRL,
        runner.AFI1_RDCHAN_CTRL, runner.AFI1_WRCHAN_CTRL,
        runner.AFI1_WRCHAN_CTRL,
        runner.SLCR_FPGA0_CLK_CTRL, runner.SLCR_FPGA_RST_CTRL,
        runner.AFI1_RDCHAN_CTRL, runner.AFI1_WRCHAN_CTRL,
    ]
    assert api.physical_write_trace == [
        (runner.AFI1_RDCHAN_CTRL, 1), (runner.AFI1_WRCHAN_CTRL, 0xF01),
    ]
    assert receipt["boot_id"] == plan.boot_id
    assert receipt["manifest_sha256"] == plan.manifest_sha256
    assert guard.exists()


def test_staged_mock_consumes_distinct_guards_and_single_pass_gates_three(tmp_path, monkeypatch, capsys):
    plan, _ = _plan(tmp_path, 3)
    overlay_receipt = tmp_path / "overlay-pass.json"
    runner.write_overlay_pass_receipt(overlay_receipt, plan)
    single_receipt = tmp_path / "single-pass.json"
    api = runner.MockBoardAPI(plan)
    api._overlay_loaded = True
    guards = {stage: tmp_path / f"{stage}.guard"
              for stage in ("dma-single", "dma-three")}
    monkeypatch.setattr(runner, "OVERLAY_PASS_RECEIPT", overlay_receipt)
    monkeypatch.setattr(runner, "DMA_SINGLE_PASS_RECEIPT", single_receipt)
    monkeypatch.setattr(runner, "_guard_path", lambda stage: guards[stage])
    monkeypatch.setattr(runner, "PynqBoardAPI",
                        lambda _plan, stage, cma_ranges=None: api)

    single = runner._dma_stage(plan, plan.boot_id, "dma-single", None, 2.0)
    capsys.readouterr()
    three = runner._dma_stage(plan, plan.boot_id, "dma-three", None, 2.0)
    capsys.readouterr()

    assert single["status"] == "DMA_SINGLE_PASS"
    assert three["status"] == "DMA_THREE_PASS"
    assert [snapshot["stage"] for snapshot in single["afi1_snapshots"]] == [
        "pre_dma_afi1_sync", "post_overlay_afi1_sync", "post_dma"]
    assert [snapshot["stage"] for snapshot in three["afi1_snapshots"]] == [
        "pre_dma_afi1_sync", "post_overlay_afi1_sync", "post_dma"]
    for result in (single, three):
        post_overlay = result["afi1_snapshots"][1]["registers"]
        post_dma = result["afi1_snapshots"][2]["registers"]
        assert post_overlay["AFI1_RDCHAN_CTRL"]["raw_hex"] == "0x00000001"
        assert post_overlay["AFI1_WRCHAN_CTRL"]["raw_hex"] == "0x00000f01"
        assert post_dma["AFI1_RDCHAN_CTRL"]["width_bit0"] == 1
        assert post_dma["AFI1_WRCHAN_CTRL"]["width_bit0"] == 1
    assert guards["dma-single"].exists() and guards["dma-three"].exists()
    assert len(api.allocations) == 4 and api.free_count == 4
    assert api.overlay_load_count == 0


def test_runtime_preflight_binds_live_cma_and_audits_fpga_runtime(tmp_path, monkeypatch):
    plan, _ = _plan(tmp_path, 3)
    cma = ((0, 0x20000000),)
    audit = {"boot_id": plan.boot_id, "kernel_log": ["baseline"] * 8}
    seen = {}

    class AuditAPI:
        def __init__(self, _plan, *, stage, cma_ranges):
            seen["stage"] = stage
            seen["cma_ranges"] = cma_ranges

        def audit_environment(self, **kwargs):
            seen["audit_kwargs"] = kwargs
            return audit

    monkeypatch.setattr(runner.preflight, "read_boot_id", lambda _path: plan.boot_id)
    monkeypatch.setattr(runner.preflight, "read_cma_meminfo",
                        lambda _path: {"CmaTotal": 131072, "CmaFree": 131000})
    admission = preflight.CmaAdmission(cma, "device_tree", True, {})
    monkeypatch.setattr(runner.preflight, "read_cma_admission", lambda _path, **_kwargs: admission)
    monkeypatch.setattr(runner.preflight, "validate_runtime_cma", lambda *_args: None)
    monkeypatch.setattr(runner, "PynqBoardAPI", AuditAPI)

    result = runner._runtime_preflight(
        plan, boot_id_path="boot", meminfo_path="meminfo", reserved_memory_path="reserved",
        stage="overlay-only")

    assert result == (plan.boot_id, {"CmaTotal": 131072, "CmaFree": 131000}, cma,
                      {**audit, "cma_admission": admission.as_evidence()})
    assert seen["stage"] == "overlay-only"
    assert seen["cma_ranges"] == cma
    assert seen["audit_kwargs"] == {"require_empty_shutdown": True, "require_loaded_bit": False}


def test_terminal_failure_marker_blocks_later_stage_without_receipt_or_guard(tmp_path, monkeypatch):
    plan, _ = _plan(tmp_path, 3)
    marker = tmp_path / "terminal-failure.json"
    marker.write_text(json.dumps({"status": "TERMINAL_FAILURE"}), encoding="utf-8")
    guard = tmp_path / "dma-single-consumed.json"
    monkeypatch.setattr(runner, "TERMINAL_FAILURE", marker)
    monkeypatch.setattr(runner, "_guard_path", lambda _stage: guard)
    with pytest.raises(preflight.PreflightError, match="terminal-failure marker"):
        runner._dma_stage(plan, plan.boot_id, "dma-single", None, 1.0)
    assert not guard.exists()


def test_offline_cli_never_imports_pynq_and_mock_reports_simulation_only(tmp_path, monkeypatch, capsys):
    plan, manifest_path = _plan(tmp_path, 1)
    real_import = __import__

    def no_pynq(name, *args, **kwargs):
        if name == "pynq" or name.startswith("pynq."):
            raise AssertionError("offline/mock mode imported PYNQ")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", no_pynq)
    digest = preflight.sha256_file(manifest_path)
    assert runner.main(["--mode", "offline", "--manifest", str(manifest_path),
                        "--manifest-sha256", digest]) == 0
    offline = capsys.readouterr().out
    assert '"board_accessed": false' in offline
    assert '"pynq_imported": false' in offline

    assert runner.main(["--mode", "mock", "--manifest", str(manifest_path),
                        "--manifest-sha256", digest]) == 0
    mock = capsys.readouterr().out
    assert '"status": "MOCK_DMA_ONCE_PASS"' in mock
    assert '"board_accessed": false' in mock
    assert '"register_protocol_exercised": true' in mock


@pytest.mark.parametrize("timeout_text", ["nan", "inf", "-inf"])
def test_cli_rejects_nonfinite_timeout_before_manifest_or_runtime_preflight(
    tmp_path, monkeypatch, capsys, timeout_text
):
    def reject_if_called(*_args, **_kwargs):
        pytest.fail("manifest/runtime preflight ran before CLI timeout validation")

    monkeypatch.setattr(preflight, "validate_manifest", reject_if_called)
    monkeypatch.setattr(runner, "_runtime_preflight", reject_if_called)

    result = runner.main(["--mode", "dma-single", "--manifest", str(tmp_path / "unused.json"),
                          f"--timeout-s={timeout_text}"])

    assert result == 2
    output = capsys.readouterr().out
    assert '"status": "DMA_SINGLE_REJECTED"' in output
    assert "timeout_s must be finite and positive" in output


def test_actual_stage_without_both_explicit_gates_fails_before_pynq_or_runtime_io(
    tmp_path, monkeypatch, capsys
):
    _, manifest_path = _plan(tmp_path, 3)
    real_import = __import__

    def no_pynq(name, *args, **kwargs):
        if name == "pynq" or name.startswith("pynq."):
            raise AssertionError("rejected stage imported PYNQ")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", no_pynq)
    assert runner.main(["--mode", "overlay-only", "--manifest", str(manifest_path),
                        "--execute"]) == 2
    result = capsys.readouterr().out
    assert '"status": "OVERLAY_ONLY_REJECTED"' in result
    assert "--authorize-stage" in result


def test_overlay_pass_receipt_is_bound_to_same_boot_manifest_build_and_bit(tmp_path):
    plan, _ = _plan(tmp_path, 1)
    receipt_path = tmp_path / "overlay-only-pass.json"
    receipt = runner.write_overlay_pass_receipt(receipt_path, plan)
    assert receipt["status"] == "OVERLAY_LOAD_PASS"

    runner.require_overlay_pass_receipt(receipt_path, plan)

    receipt["boot_id"] = "different-boot"
    receipt_path.write_text(__import__("json").dumps(receipt), encoding="utf-8")
    with pytest.raises(preflight.PreflightError, match="boot_id"):
        runner.require_overlay_pass_receipt(receipt_path, plan)


def test_stage_guard_is_distinct_and_single_use(tmp_path):
    plan, _ = _plan(tmp_path, 1)
    overlay_guard = tmp_path / "overlay-consumed.json"
    single_guard = tmp_path / "single-consumed.json"
    three_guard = tmp_path / "three-consumed.json"
    preflight.claim_once_guard(overlay_guard, boot_id=plan.boot_id,
                               manifest_sha256=plan.manifest_sha256, stage="overlay-only")
    preflight.claim_once_guard(single_guard, boot_id=plan.boot_id,
                               manifest_sha256=plan.manifest_sha256, stage="dma-single")
    preflight.claim_once_guard(three_guard, boot_id=plan.boot_id,
                               manifest_sha256=plan.manifest_sha256, stage="dma-three")
    with pytest.raises(preflight.PreflightError, match="already consumed"):
        preflight.claim_once_guard(overlay_guard, boot_id=plan.boot_id,
                                   manifest_sha256=plan.manifest_sha256, stage="overlay-only")
    assert overlay_guard.read_text(encoding="utf-8") != single_guard.read_text(encoding="utf-8")
    assert single_guard.read_text(encoding="utf-8") != three_guard.read_text(encoding="utf-8")
