"""Tests for the version-agnostic split meter used by arm_split_ab."""
from __future__ import annotations

import types

import pytest

from scripts.rle.experiments import arm_split_ab


class _Partition:
    def __init__(self, pieces):
        self.pieces = pieces


def _legacy_module(pieces):
    calls = []

    def legacy(*args, **kwargs):
        calls.append((args, kwargs))
        return pieces

    return types.SimpleNamespace(split_touching_component=legacy), calls


def _verified_module(pieces):
    calls = []

    def verified(*args, **kwargs):
        calls.append((args, kwargs))
        return _Partition(pieces)

    return types.SimpleNamespace(split_touching_component_verified=verified), calls


def test_legacy_entry_is_wrapped_once():
    module, calls = _legacy_module(("a", "b"))
    meter = arm_split_ab.Meter()
    entry = arm_split_ab.install_split_meter(module, meter)
    assert entry == "legacy"
    result = module.split_touching_component("mask")
    assert result == ("a", "b")
    assert len(calls) == 1
    assert meter.calls == 1 and meter.pieces[2] == 1


def test_verified_entry_is_wrapped_once():
    module, calls = _verified_module(("a", "b", "c"))
    meter = arm_split_ab.Meter()
    entry = arm_split_ab.install_split_meter(module, meter)
    assert entry == "verified"
    partition = module.split_touching_component_verified("mask")
    assert partition.pieces == ("a", "b", "c")
    assert len(calls) == 1
    assert meter.calls == 1 and meter.pieces[3] == 1


def test_only_one_entry_is_wrapped_when_both_exist():
    legacy_calls = []

    def legacy(*args, **kwargs):
        legacy_calls.append(1)
        return ("a",)

    def verified(*args, **kwargs):
        return _Partition(("a", "b"))

    module = types.SimpleNamespace(split_touching_component=legacy,
                                   split_touching_component_verified=verified)
    meter = arm_split_ab.Meter()
    assert arm_split_ab.install_split_meter(module, meter) == "verified"
    assert module.split_touching_component is legacy
    module.split_touching_component_verified("mask")
    assert meter.calls == 1 and meter.pieces[2] == 1
    module.split_touching_component("mask")
    assert legacy_calls == [1] and meter.calls == 1


def test_single_piece_is_counted_as_no_split():
    module, _ = _legacy_module(("a",))
    meter = arm_split_ab.Meter()
    arm_split_ab.install_split_meter(module, meter)
    module.split_touching_component("mask")
    assert meter.calls == 1 and meter.pieces[1] == 1


def test_more_than_four_pieces_are_capped():
    module, _ = _legacy_module(("a", "b", "c", "d", "e"))
    meter = arm_split_ab.Meter()
    arm_split_ab.install_split_meter(module, meter)
    module.split_touching_component("mask")
    assert meter.pieces[4] == 1
    assert sum(meter.pieces.values()) == meter.calls == 1


def test_arguments_are_forwarded():
    module, calls = _legacy_module(("a", "b"))
    meter = arm_split_ab.Meter()
    arm_split_ab.install_split_meter(module, meter)
    module.split_touching_component("mask", min_child_area=80, assume_connected=True)
    assert calls == [(("mask",), {"min_child_area": 80, "assume_connected": True})]


def test_exception_propagates_and_counts_the_call():
    def broken(*args, **kwargs):
        raise ValueError("boom")

    module = types.SimpleNamespace(split_touching_component=broken)
    meter = arm_split_ab.Meter()
    arm_split_ab.install_split_meter(module, meter)
    with pytest.raises(ValueError, match="boom"):
        module.split_touching_component("mask")
    assert meter.calls == 1
    assert sum(meter.pieces.values()) == 0


def test_missing_entry_point_is_rejected():
    with pytest.raises(RuntimeError, match="split entry point"):
        arm_split_ab.install_split_meter(types.SimpleNamespace(), arm_split_ab.Meter())