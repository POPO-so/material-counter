"""Offline regressions for conservative CMA metadata admission and allocation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import board_preflight as preflight
import board_once as runner
from test_board_preflight import make_manifest


SYSTEM_RAM_512M = "00000000-1fffffff : System RAM\n"
MEMINFO_128M = {"CmaTotal": 131072, "CmaFree": 32768}


def _resolve(**overrides):
    inputs = {
        "dt_ranges": (),
        "dmesg_text": "",
        "debugfs_ranges": (),
        "meminfo": dict(MEMINFO_128M),
        "cmdline": "",
        "bootargs": "",
        "iomem_text": SYSTEM_RAM_512M,
    }
    inputs.update(overrides)
    return preflight.resolve_cma_sources(**inputs)


@pytest.mark.parametrize("cma_free_kib", [32768, 73944])
def test_meminfo_and_exact_system_ram_admit_reserved_low_candidate(cma_free_kib):
    result = _resolve(meminfo={"CmaTotal": 131072, "CmaFree": cma_free_kib})

    assert result.source == "MEMINFO_IOMEM_VERIFIED"
    assert result.exact is False
    assert result.ranges == ((0x00100000, 0x20000000),)
    evidence = result.as_evidence()
    assert evidence["physical_cma_range_known"] is False
    assert "not a measured CMA interval" in evidence["limitation"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"meminfo": {"CmaTotal": 131071, "CmaFree": 32768}},
        {"meminfo": {"CmaTotal": 132000, "CmaFree": 32768}},
        {"meminfo": {"CmaTotal": 131072, "CmaFree": 32767}},
        {"iomem_text": "00000000-0fffffff : System RAM\n"},
        {"cmdline": "console=ttyPS0 cma=64M"},
    ],
)
def test_meminfo_iomem_admission_rejects_inconsistent_inputs(overrides):
    with pytest.raises(preflight.PreflightError):
        _resolve(**overrides)


@pytest.mark.parametrize("side", ["tx_address", "rx_address"])
def test_post_alloc_gate_rejects_addresses_below_one_mib_even_if_cma_range_includes_them(side):
    args = {
        "tx_address": 0x18000000,
        "tx_size": preflight.TX_BYTES,
        "rx_address": 0x18040000,
        "rx_size": preflight.RX_BYTES,
        "cma_ranges": ((0, 0x20000000),),
        "hp0_range": preflight.EXPECTED_DDR_RANGE,
        "hp1_range": preflight.EXPECTED_DDR_RANGE,
    }
    args[side] = 0x00080000

    with pytest.raises(preflight.PreflightError):
        preflight.validate_dma_buffer_pair(**args)


def test_post_alloc_gate_accepts_one_mib_boundary_with_exact_transfer_lengths():
    result = preflight.validate_dma_buffer_pair(
        tx_address=0x00100000,
        tx_size=preflight.TX_BYTES,
        rx_address=0x18040000,
        rx_size=preflight.RX_BYTES,
        cma_ranges=((0x00100000, 0x20000000),),
        hp0_range=preflight.EXPECTED_DDR_RANGE,
        hp1_range=preflight.EXPECTED_DDR_RANGE,
    )

    assert result["status"] == "POST_ALLOC_PHYSICAL_GATE_PASS"
    assert result["tx_actual_bytes"] == preflight.TX_BYTES
    assert result["rx_actual_bytes"] == preflight.RX_BYTES


@pytest.mark.parametrize("frame_count", [1, 3])
def test_mock_run_reports_cma_use_during_both_allocations_and_exact_gate(tmp_path, frame_count):
    manifest_path, _ = make_manifest(tmp_path, frame_count=frame_count)
    plan = preflight.validate_manifest(manifest_path)
    api = runner.MockBoardAPI(plan)

    result = runner.run_frames(plan, api, timeout_s=2.0)

    samples = result["cma_allocation_samples"]
    by_stage = {sample["stage"]: sample["cma_free_kib"] for sample in samples}
    assert [sample["stage"] for sample in samples] == [
        "before", "after_mm2s", "after_s2mm", "after_release"
    ]
    assert result["cma_free_before_kib"] == by_stage["before"]
    assert by_stage["after_mm2s"] < by_stage["before"]
    assert by_stage["after_s2mm"] < by_stage["after_mm2s"]
    assert result["cma_free_during_kib"] == by_stage["after_s2mm"]
    assert result["cma_free_after_kib"] == by_stage["after_release"]
    assert by_stage["after_release"] == by_stage["before"]
    assert api.cma_free_kib() == by_stage["before"]

    gate = result["post_alloc_physical_gate"]
    assert gate["tx_actual_bytes"] == preflight.TX_BYTES
    assert gate["rx_actual_bytes"] == preflight.RX_BYTES
    assert gate["status"] == "POST_ALLOC_PHYSICAL_GATE_PASS"
