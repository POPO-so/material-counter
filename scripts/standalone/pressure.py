"""Offline pressure-frame generation and G2 transfer-counter validation."""

from __future__ import annotations

from collections.abc import Iterable
import numbers

import numpy as np


WIDTH = 640
HEIGHT = 400
# RAW1 sends only Gray8 pixels. The 16-byte header and 16-byte trailer belong
# to packet mode and never enter the PL input-beat counter.
RAW1_INPUT_BYTES = WIDTH * HEIGHT
INPUT_BEATS_PER_FRAME = RAW1_INPUT_BYTES // 4
_U32_MAX = (1 << 32) - 1
_U64_MASK = (1 << 64) - 1
_COUNTER_NAMES = (
    "total_cycles",
    "active_cycles",
    "input_beats",
    "output_beats",
    "input_stall_cycles",
    "output_stall_cycles",
)


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"{name} must be an integer")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be in {minimum}..{maximum}")
    return result


def make_input(pattern: str, index: int) -> np.ndarray:
    """Return one deterministic 640x400 Gray8 synthetic pressure frame."""
    index = _integer(index, "index", 0, (1 << 63) - 1)
    if pattern not in ("moving", "high_runs"):
        raise ValueError("pattern must be 'moving' or 'high_runs'")

    image = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    if pattern == "moving":
        y = 140 + (index % 12) * 12
        image[y:y + 16, 200:220] = 200
    else:
        image[:, ::16] = 255
    return image


def _snapshot_parts(snapshot: object, name: str) -> tuple[int, dict[str, int]]:
    if not isinstance(snapshot, dict):
        raise TypeError(f"{name} must be a snapshot_counters result mapping")
    sequence = _integer(snapshot.get("sequence"), f"{name}.sequence", 0, _U32_MAX)
    counters = snapshot.get("counters")
    if not isinstance(counters, dict) or set(counters) != set(_COUNTER_NAMES):
        raise ValueError(f"{name}.counters must contain exactly the six G2 counters")
    validated = {
        counter: _integer(counters[counter], f"{name}.{counter}", 0, _U64_MASK)
        for counter in _COUNTER_NAMES
    }
    return sequence, validated


def counter_delta(before: dict, after: dict) -> dict:
    """Compute modular 64-bit deltas for a pair of atomic counter snapshots."""
    sequence_before, counters_before = _snapshot_parts(before, "before")
    sequence_after, counters_after = _snapshot_parts(after, "after")
    return {
        "sequence_before": sequence_before,
        "sequence_after": sequence_after,
        "counters": {
            name: (counters_after[name] - counters_before[name]) & _U64_MASK
            for name in _COUNTER_NAMES
        },
    }


def validate_transfers(delta: dict, frames: int, rx_bytes: Iterable[int]) -> dict:
    """Validate input/output beat totals and return an auditable summary."""
    frames = _integer(frames, "frames", 1, (1 << 63) - 1)
    if not isinstance(delta, dict):
        raise TypeError("delta must be the result of counter_delta")
    sequence_before = _integer(delta.get("sequence_before"), "sequence_before", 0, _U32_MAX)
    sequence_after = _integer(delta.get("sequence_after"), "sequence_after", 0, _U32_MAX)
    counters = delta.get("counters")
    if not isinstance(counters, dict) or set(counters) != set(_COUNTER_NAMES):
        raise ValueError("delta.counters must contain exactly the six G2 counter deltas")
    checked = {
        name: _integer(counters[name], f"delta.{name}", 0, _U64_MASK)
        for name in _COUNTER_NAMES
    }

    if isinstance(rx_bytes, (str, bytes, bytearray)):
        raise TypeError("rx_bytes must be an iterable of per-frame integer byte lengths")
    try:
        lengths = list(rx_bytes)
    except TypeError as exc:
        raise TypeError("rx_bytes must be an iterable of per-frame integer byte lengths") from exc
    if len(lengths) != frames:
        raise ValueError("rx_bytes must contain exactly one length per frame")
    lengths = [
        _integer(length, f"rx_bytes[{index}]", 0, (1 << 63) - 1)
        for index, length in enumerate(lengths)
    ]
    if any(length % 4 for length in lengths):
        raise ValueError("each rx_bytes length must be 4-byte aligned")

    expected_input_beats = frames * INPUT_BEATS_PER_FRAME
    expected_output_beats = sum(lengths) // 4
    if checked["input_beats"] != expected_input_beats:
        raise ValueError(
            f"input_beats mismatch: expected {expected_input_beats}, "
            f"observed {checked['input_beats']}"
        )
    if checked["output_beats"] != expected_output_beats:
        raise ValueError(
            f"output_beats mismatch: expected {expected_output_beats}, "
            f"observed {checked['output_beats']}"
        )

    return {
        "valid": True,
        "frames": frames,
        "rx_bytes": lengths,
        "sequence_before": sequence_before,
        "sequence_after": sequence_after,
        "expected_input_beats": expected_input_beats,
        "observed_input_beats": checked["input_beats"],
        "expected_output_beats": expected_output_beats,
        "observed_output_beats": checked["output_beats"],
        "input_stall_cycles": checked["input_stall_cycles"],
        "output_stall_cycles": checked["output_stall_cycles"],
        "counter_deltas": checked,
    }


def validate_transfer_totals(delta: dict,frames: int,rx_total_bytes: int) -> dict:
    """Validate a continuous session using constant-size cumulative totals."""
    frames=_integer(frames,'frames',0,(1<<63)-1)
    total=_integer(rx_total_bytes,'rx_total_bytes',0,(1<<64)-1)
    if total%4:raise ValueError('rx_total_bytes must be 4-byte aligned')
    if not isinstance(delta,dict) or not isinstance(delta.get('counters'),dict):
        raise ValueError('delta must contain counters')
    counters=delta['counters']
    if set(counters)!=set(_COUNTER_NAMES):raise ValueError('expected six counter deltas')
    checked={key:_integer(counters[key],key,0,_U64_MASK) for key in _COUNTER_NAMES}
    expected_input=frames*INPUT_BEATS_PER_FRAME
    expected_output=total//4
    if checked['input_beats']!=expected_input:raise ValueError('input_beats total mismatch')
    if checked['output_beats']!=expected_output:raise ValueError('output_beats total mismatch')
    if not frames and total:raise ValueError('received bytes without processed frames')
    return dict(valid=True,frames=frames,rx_total_bytes=total,
        expected_input_beats=expected_input,observed_input_beats=checked['input_beats'],
        expected_output_beats=expected_output,observed_output_beats=checked['output_beats'],
        counter_deltas=checked)
