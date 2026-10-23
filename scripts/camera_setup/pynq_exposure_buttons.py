#!/usr/bin/env python3
"""Adjust a PYNQ-Z2 USB camera's exposure through the WO-0017 button GPIO."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


EXPOSURE_CONTROL = "exposure_time_absolute"
AUTO_EXPOSURE_CONTROL = "auto_exposure"
AUTO_EXPOSURE_MANUAL = 1
EXPOSURE_STEP_UNITS = 5  # V4L2 absolute exposure units are 0.1 ms each.
EXPOSURE_UNITS_PER_MS = 10
BTN0_MASK = 0x01
BTN1_MASK = 0x02


class ExposureControlError(RuntimeError):
    """The camera does not expose a safe, usable manual exposure control."""


@dataclass(frozen=True)
class IntegerControl:
    minimum: int
    maximum: int
    step: int
    value: int


@dataclass(frozen=True)
class CameraControls:
    exposure: IntegerControl
    auto_exposure: int


def _run_v4l2(device: str, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except FileNotFoundError as exc:
        raise ExposureControlError("v4l2-ctl is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExposureControlError("v4l2-ctl timed out while accessing the camera") from exc

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        detail = output.strip() or f"exit status {result.returncode}"
        raise ExposureControlError(f"v4l2-ctl failed: {detail}")
    return output


def _parse_integer_control(output: str, name: str) -> IntegerControl:
    line = next(
        (candidate.strip() for candidate in output.splitlines()
         if candidate.lstrip().startswith(name + " ")),
        None,
    )
    if line is None:
        raise ExposureControlError(f"camera does not report the {name} control")

    fields = {key: int(value) for key, value in
              re.findall(r"\b(min|max|step|value)=(-?\d+)\b", line)}
    missing = {"min", "max", "step", "value"} - fields.keys()
    if missing:
        raise ExposureControlError(
            f"could not parse {name} control fields: {', '.join(sorted(missing))}"
        )
    control = IntegerControl(
        minimum=fields["min"],
        maximum=fields["max"],
        step=fields["step"],
        value=fields["value"],
    )
    if control.minimum > control.maximum or control.step < 1:
        raise ExposureControlError(f"camera reported an invalid {name} range")
    return control


def _parse_control_value(output: str, name: str) -> int:
    line = next(
        (candidate.strip() for candidate in output.splitlines()
         if candidate.lstrip().startswith(name + " ")),
        None,
    )
    if line is None:
        raise ExposureControlError(f"camera does not report the {name} control")
    match = re.search(r"\bvalue=(-?\d+)\b", line)
    if match is None:
        raise ExposureControlError(f"could not parse the current {name} value")
    return int(match.group(1))


def read_camera_controls(device: str) -> CameraControls:
    output = _run_v4l2(device, "--list-ctrls")
    exposure = _parse_integer_control(output, EXPOSURE_CONTROL)
    auto_exposure = _parse_control_value(output, AUTO_EXPOSURE_CONTROL)
    if not exposure.minimum <= exposure.value <= exposure.maximum:
        raise ExposureControlError("camera exposure readback is outside its reported range")
    if (exposure.value - exposure.minimum) % exposure.step:
        raise ExposureControlError("camera exposure readback is not aligned to its reported step")
    if auto_exposure != AUTO_EXPOSURE_MANUAL:
        raise ExposureControlError(
            "camera auto exposure is not in Manual Mode; no camera settings were changed"
        )
    if EXPOSURE_STEP_UNITS % exposure.step:
        raise ExposureControlError(
            "this camera's exposure step cannot represent an exact 0.5 ms button step"
        )
    return CameraControls(exposure=exposure, auto_exposure=auto_exposure)


def _aligned_target(current: int, direction: int, control: IntegerControl) -> int:
    requested = current + direction * EXPOSURE_STEP_UNITS
    bounded = min(max(requested, control.minimum), control.maximum)
    offset = bounded - control.minimum
    if direction > 0:
        offset = ((offset + control.step - 1) // control.step) * control.step
    else:
        offset = (offset // control.step) * control.step
    largest_valid_offset = ((control.maximum - control.minimum) // control.step) * control.step
    return control.minimum + min(offset, largest_valid_offset)


def _format_exposure(value: int) -> str:
    milliseconds = value / EXPOSURE_UNITS_PER_MS
    return f"{milliseconds:.1f} ms"


def adjust_exposure(device: str, direction: int, label: str) -> None:
    controls = read_camera_controls(device)
    current = controls.exposure.value
    target = _aligned_target(current, direction, controls.exposure)
    if target == current:
        bound = "minimum" if direction < 0 else "maximum"
        print(f"{label}: already at camera {bound} {_format_exposure(current)}", flush=True)
        return

    _run_v4l2(device, f"--set-ctrl={EXPOSURE_CONTROL}={target}")
    updated = read_camera_controls(device).exposure.value
    if updated != target:
        raise ExposureControlError(
            f"camera readback was {_format_exposure(updated)} after requesting "
            f"{_format_exposure(target)}"
        )
    print(f"{label}: {_format_exposure(current)} -> {_format_exposure(updated)}", flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_attached_overlay_buttons(bitfile: Path, hwh: Path, manifest_path: Path):
    bitfile = bitfile.resolve()
    hwh = hwh.resolve()
    manifest_path = manifest_path.resolve()
    if not bitfile.is_file() or not hwh.is_file() or not manifest_path.is_file():
        raise ExposureControlError("WO-0017 bit, HWH, or pair manifest is missing")
    if bitfile.with_suffix(".hwh") != hwh:
        raise ExposureControlError("the WO-0017 bit and HWH must share the same file stem")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExposureControlError(f"could not read the WO-0017 pair manifest: {exc}") from exc
    if manifest.get("build_id") != "wo0017-hw-50mhz-20261021":
        raise ExposureControlError("pair manifest is not the expected WO-0017 build")
    if manifest.get("implementation_status") != "implemented_offline":
        raise ExposureControlError("pair manifest does not identify an implemented WO-0017 artifact")
    if manifest.get("bit", {}).get("sha256") != _sha256(bitfile):
        raise ExposureControlError("WO-0017 bitfile hash does not match its pair manifest")
    if manifest.get("hwh", {}).get("sha256") != _sha256(hwh):
        raise ExposureControlError("WO-0017 HWH hash does not match its pair manifest")

    gpio_manifest = manifest.get("exposure_buttons_gpio", {})
    if (gpio_manifest.get("ip_name") != "exposure_buttons_gpio" or
            gpio_manifest.get("readback_address", {}).get("offset", "").lower() != "0x41200000" or
            gpio_manifest.get("readback_address", {}).get("range", "").lower() != "64k"):
        raise ExposureControlError("WO-0017 manifest does not declare the expected button GPIO")

    try:
        from pynq import Overlay
    except ImportError as exc:
        raise ExposureControlError(
            "PYNQ Overlay support is unavailable in this Python environment"
        ) from exc

    # Parse metadata only. This call must never program or replace the active PL image.
    try:
        overlay = Overlay(str(bitfile), download=False)
        if not overlay.is_loaded():
            raise ExposureControlError(
                "the matching WO-0017 bitstream is not already loaded; this utility will not program PL"
            )
        ip_info = overlay.ip_dict.get("exposure_buttons_gpio")
        try:
            gpio_base = int(str(ip_info.get("phys_addr", -1)), 0) if isinstance(ip_info, dict) else -1
        except ValueError:
            gpio_base = -1
        if gpio_base != 0x41200000:
            raise ExposureControlError("loaded HWH lacks exposure_buttons_gpio at 0x41200000")
        buttons_ip = getattr(overlay, "exposure_buttons_gpio", None)
        if buttons_ip is None or not hasattr(buttons_ip, "channel1"):
            raise ExposureControlError("PYNQ did not expose the WO-0017 AXI GPIO channel")
        buttons = buttons_ip.channel1
        buttons.setdirection("in")
        buttons.setlength(2)
    except ExposureControlError:
        raise
    except Exception as exc:
        raise ExposureControlError(f"could not attach to the already loaded WO-0017 overlay: {exc}") from exc
    return overlay, buttons


class ButtonEdges:
    """Emit debounced, press-only edges for BTN0 and BTN1."""

    def __init__(self, initial_mask: int, debounce_s: float):
        initial = initial_mask & (BTN0_MASK | BTN1_MASK)
        self._stable_mask = initial
        self._candidate_mask = initial
        self._candidate_since = time.monotonic()
        self._debounce_s = debounce_s

    def update(self, raw_mask: int, now: float | None = None) -> tuple[int, ...]:
        sample = raw_mask & (BTN0_MASK | BTN1_MASK)
        timestamp = time.monotonic() if now is None else now
        if sample != self._candidate_mask:
            self._candidate_mask = sample
            self._candidate_since = timestamp
            return ()
        if sample == self._stable_mask or timestamp - self._candidate_since < self._debounce_s:
            return ()

        pressed = sample & ~self._stable_mask
        self._stable_mask = sample
        if sample == (BTN0_MASK | BTN1_MASK):
            return ()
        return tuple(bit for bit in (BTN0_MASK, BTN1_MASK) if pressed & bit)


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adjust a PYNQ-Z2 camera's manual exposure with BTN0 and BTN1."
    )
    parser.add_argument(
        "--bit", required=True, type=Path, help="WO-0017 bit paired with the current active PL image"
    )
    parser.add_argument("--hwh", required=True, type=Path, help="matching WO-0017 HWH file")
    parser.add_argument("--manifest", required=True, type=Path, help="matching WO-0017 pair-manifest.json")
    parser.add_argument("--device", required=True, help="explicit V4L2 camera device, such as /dev/video0")
    parser.add_argument("--poll-ms", type=int, default=20, help="button poll interval (default: 20)")
    parser.add_argument("--debounce-ms", type=int, default=35, help="button debounce time (default: 35)")
    args = parser.parse_args(argv)
    if not 5 <= args.poll_ms <= 200:
        parser.error("--poll-ms must be in 5..200")
    if not 10 <= args.debounce_ms <= 200:
        parser.error("--debounce-ms must be in 10..200")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        controls = read_camera_controls(args.device)
        print(
            f"Camera {args.device}: manual exposure {_format_exposure(controls.exposure.value)}; "
            f"range {_format_exposure(controls.exposure.minimum)}.."
            f"{_format_exposure(controls.exposure.maximum)}.",
            flush=True,
        )
        overlay, buttons = _load_attached_overlay_buttons(args.bit, args.hwh, args.manifest)
        initial_mask = int(buttons.read())
        edges = ButtonEdges(initial_mask, args.debounce_ms / 1000.0)
        print("Ready: BTN0 decreases exposure; BTN1 increases it by 0.5 ms per press.",
              flush=True)
        print("Hold does not repeat. Press Ctrl+C to stop; the active PL image is left loaded.",
              flush=True)

        poll_s = args.poll_ms / 1000.0
        while True:
            pressed = edges.update(int(buttons.read()))
            if len(pressed) == 2:
                print("BTN0 and BTN1 pressed together; ignoring this edge.", flush=True)
            elif pressed == (BTN0_MASK,):
                adjust_exposure(args.device, -1, "BTN0")
            elif pressed == (BTN1_MASK,):
                adjust_exposure(args.device, 1, "BTN1")
            time.sleep(poll_s)
    except KeyboardInterrupt:
        print("\nStopped. The camera stream and active PL image were left running.",
              flush=True)
        return 0
    except ExposureControlError as exc:
        print(f"Exposure control stopped: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
