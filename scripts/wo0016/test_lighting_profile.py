"""Tests for the calibrated lighting profile."""
import sys
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "wo0015"))

import lighting_profile as profile
import packet_encoder


def test_profile_constants_capture_the_calibrated_source_settings():
    assert profile.THRESHOLD == 100
    assert profile.SOURCE_ROI == (20, 380)
    assert profile.SOURCE_MIN_NET_AREA == 60
    assert profile.CAMERA_CONTROLS == {
        "auto_exposure": 1,
        "exposure_time_absolute": 50,
        "brightness": 0,
        "gamma": 100,
    }
    assert profile.OPTIONAL_CAMERA_CONTROLS == {"gain": 0}


@pytest.mark.parametrize(
    ("area", "expected"),
    [
        (59, "unknown"),
        (60, "small"),
        (95, "small"),
        (96, "medium"),
        (130, "medium"),
        (131, "large"),
        (188, "large"),
        (189, "unknown"),
        (None, "unknown"),
    ],
)
def test_area_hints_use_provisional_output_pixel_ranges(area, expected):
    assert profile.classify_area(area) == expected
    assert profile.AREA_HINTS["provisional"] is True
    assert profile.AREA_HINTS["units"] == "output_foreground_pixels"


def test_source_noise_filter_uses_outer_minus_holes_contour_area_and_exact_100_threshold():
    gray = np.full((400, 640), 50, dtype=np.uint8)
    roi_top = profile.SOURCE_ROI[0]

    # Outer contour area 64 minus hole area 8 gives net contour area 56.
    gray[roi_top + 10:roi_top + 19, 10:19] = 101
    gray[roi_top + 14:roi_top + 15, 12:16] = 50

    # Outer contour area 64 minus hole area 4 gives net contour area 60.
    gray[roi_top + 10:roi_top + 19, 40:49] = 101
    gray[roi_top + 14:roi_top + 15, 42:44] = 50

    # Same 56 contour-area shape at exactly 100 is below the >100 threshold.
    gray[roi_top + 25:roi_top + 34, 10:19] = 100
    gray[roi_top + 29:roi_top + 30, 12:16] = 50
    gray[19, 0] = 200
    gray[380, 0] = 200
    original = gray.copy()

    filtered = profile.filter_source_noise(gray)

    assert filtered.shape == (400, 640)
    assert filtered.dtype == np.uint8
    assert not np.shares_memory(filtered, gray)
    assert np.array_equal(gray, original)
    assert np.all(filtered[roi_top + 10:roi_top + 19, 10:19][
        (gray[roi_top + 10:roi_top + 19, 10:19] > 100)
    ] == 0)
    assert np.all(filtered[roi_top + 10:roi_top + 19, 40:49][
        gray[roi_top + 10:roi_top + 19, 40:49] > 100
    ] == 101)
    assert np.all(filtered[roi_top + 14:roi_top + 15, 12:16] == 50)
    assert np.all(filtered[roi_top + 14:roi_top + 15, 42:44] == 50)
    assert np.all(filtered[roi_top + 25:roi_top + 34, 10:19] ==
                  gray[roi_top + 25:roi_top + 34, 10:19])
    assert np.all(filtered[19] == 0)
    assert np.all(filtered[380:] == 0)
    assert filtered[roi_top, 0] == 50
    assert filtered[379, 0] == 50


@pytest.mark.parametrize(
    "gray",
    [np.zeros((400, 640), dtype=np.uint16),
     np.zeros((399, 640), dtype=np.uint8),
     np.zeros((400, 639), dtype=np.uint8)],
)
def test_source_noise_filter_rejects_non_gray8_or_wrong_geometry(gray):
    with pytest.raises(ValueError, match="uint8.*400, 640"):
        profile.filter_source_noise(gray)


def test_operator_confirmed_names_follow_the_three_provisional_size_bands():
    assert profile.classify_material_area(80) == "M6平垫圈"
    assert profile.classify_material_area(111) == "M6螺母"
    assert profile.classify_material_area(150) == "M8螺母"
    assert profile.classify_material_area(500) is None
    assert profile.AREA_HINTS["provisional"] is True


def test_prepare_gray_crops_source_roi_in_letterbox_coordinates_without_mutating_input():
    gray = np.full((240, 320), 73, dtype=np.uint8)
    original = gray.copy()

    prepared = profile.prepare_gray(gray)

    assert prepared.shape == (240, 320)
    assert prepared.dtype == np.uint8
    assert not np.shares_memory(prepared, gray)
    assert np.array_equal(gray, original)
    assert np.all(prepared[:30] == 0)
    assert np.all(prepared[30:210] == 73)
    assert np.all(prepared[210:] == 0)


def test_wire_threshold_compensation_matches_gray_greater_than_100_for_every_intensity():
    gray = np.zeros((240, 320), dtype=np.uint8)
    values = np.arange(256, dtype=np.uint8)
    gray[40, :256] = values
    output = profile.encode(gray, frame_id=91)

    pixels = output[16:16 + packet_encoder.PAYLOAD_BYTES].reshape(240, 320)
    pl_binary = pixels[40, :256] >= 128

    assert np.array_equal(pl_binary, values > profile.THRESHOLD)
    assert np.array_equal(pixels[40, :256], np.minimum(values.astype(np.uint16) + 27, 255))


def test_encode_into_uses_existing_packet_writer_and_preserves_gray8_evidence(monkeypatch):
    gray = np.full((240, 320), 100, dtype=np.uint8)
    gray[50, 10] = 101
    expected_gray = np.minimum(profile.prepare_gray(gray).astype(np.uint16) + 27, 255).astype(np.uint8)
    output = np.empty(packet_encoder.TX_BYTES, dtype=np.uint8)
    calls = []

    def write_packet(encoded_gray, destination, *, frame_id):
        calls.append((encoded_gray.copy(), destination, frame_id))

    monkeypatch.setattr(profile, "gray320x240_to_gray_packet_into", write_packet)
    result = profile.encode_into(gray, output, frame_id=7)

    assert result is None
    assert len(calls) == 1
    assert np.array_equal(calls[0][0], expected_gray)
    assert calls[0][1] is output and calls[0][2] == 7
    assert gray[50, 10] == 101


def test_encode_allocates_expected_tx_buffer_and_delegates_to_encode_into(monkeypatch):
    gray = np.zeros((240, 320), dtype=np.uint8)
    calls = []

    def write_into(image, output, *, frame_id):
        calls.append((image, output, frame_id))
        output.fill(17)

    monkeypatch.setattr(profile, "encode_into", write_into)
    packet = profile.encode(gray, frame_id=123)

    assert packet.dtype == np.uint8
    assert packet.nbytes == packet_encoder.TX_BYTES
    assert np.all(packet == 17)
    assert calls[0][0] is gray and calls[0][1] is packet and calls[0][2] == 123


def test_apply_camera_controls_sets_then_reads_back_exact_controls_as_root(monkeypatch):
    commands = []
    readback = "\n".join(
        f"{name}: {value}" for name, value in profile.CAMERA_CONTROLS.items()
    )

    def fake_run(command, *, timeout, check, capture_output, text):
        commands.append(command)
        assert timeout > 0
        assert check is True
        assert capture_output is True
        assert text is True
        if command[-2:] == ["--get-ctrl", "gain"]:
            return SimpleNamespace(stdout="gain: 0")
        return SimpleNamespace(stdout="" if "--set-ctrl" in command else readback)

    monkeypatch.setattr(profile.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(profile.subprocess, "run", fake_run)

    result = profile.apply_camera_controls("/dev/video0")

    assert commands[0][:2] == ["v4l2-ctl", "--device"]
    controls_argument = ",".join(
        f"{name}={value}" for name, value in profile.CAMERA_CONTROLS.items()
    )
    assert commands[0][2:] == ["/dev/video0", "--set-ctrl", controls_argument]
    assert commands[1] == ["v4l2-ctl", "--device", "/dev/video0", "--set-ctrl", "gain=0"]
    assert commands[2] == ["v4l2-ctl", "--device", "/dev/video0", "--get-ctrl",
                           ",".join(profile.CAMERA_CONTROLS)]
    assert commands[3] == ["v4l2-ctl", "--device", "/dev/video0", "--get-ctrl", "gain"]
    assert result["required_controls_verified"] is True
    assert result["required_controls"] == profile.CAMERA_CONTROLS
    assert result["gain"]["status"] == "verified"
    assert result["gain"]["value"] == 0
    assert result["gain"]["set_response"] == "applied"


def test_apply_camera_controls_uses_noninteractive_sudo_for_non_root(monkeypatch):
    commands = []
    readback = "\n".join(
        f"{name}: {value}" for name, value in profile.CAMERA_CONTROLS.items()
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[-2:] == ["--get-ctrl", "gain"]:
            return SimpleNamespace(stdout="gain: 0")
        return SimpleNamespace(stdout="" if "--set-ctrl" in command else readback)

    monkeypatch.setattr(profile.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(profile.subprocess, "run", fake_run)

    profile.apply_camera_controls("/dev/video2")

    assert commands[0][:4] == ["sudo", "-n", "v4l2-ctl", "--device"]
    assert all(command[:4] == ["sudo", "-n", "v4l2-ctl", "--device"]
               for command in commands)


def test_apply_camera_controls_propagates_v4l2_command_failure(monkeypatch):
    def fail_run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="unsupported control")

    monkeypatch.setattr(profile.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(profile.subprocess, "run", fail_run)

    with pytest.raises(subprocess.CalledProcessError):
        profile.apply_camera_controls("/dev/video0")


def test_apply_camera_controls_rejects_mismatched_readback(monkeypatch):
    readback = "\n".join(
        f"{name}: {999 if name == 'gamma' else value}"
        for name, value in profile.CAMERA_CONTROLS.items()
    )
    outputs = iter(("", "", readback))
    monkeypatch.setattr(profile.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(profile.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout=next(outputs)))

    with pytest.raises(RuntimeError, match="gamma"):
        profile.apply_camera_controls("/dev/video0")


def test_apply_camera_controls_rejects_missing_readback_control(monkeypatch):
    readback = "\n".join(
        f"{name}: {value}" for name, value in profile.CAMERA_CONTROLS.items()
        if name != "gamma"
    )
    outputs = iter(("", "", readback))
    monkeypatch.setattr(profile.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(profile.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout=next(outputs)))

    with pytest.raises(RuntimeError, match="gamma"):
        profile.apply_camera_controls("/dev/video0")


def test_apply_camera_controls_exempts_only_unexposed_gain(monkeypatch):
    required = {
        "auto_exposure": 1,
        "exposure_time_absolute": 50,
        "brightness": 0,
        "gamma": 100,
    }
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[-2:] == ["--set-ctrl", "gain=0"] or command[-2:] == ["--get-ctrl", "gain"]:
            raise subprocess.CalledProcessError(1, command, stderr="unknown control 'gain'")
        if "--get-ctrl" in command:
            return SimpleNamespace(stdout="\n".join(f"{key}: {value}" for key, value in required.items()))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(profile.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(profile.subprocess, "run", fake_run)

    result = profile.apply_camera_controls("/dev/video0")

    assert result["required_controls_verified"] is True
    assert result["required_controls"] == required
    assert {key: value for key, value in result["gain"].items()
            if key not in ("driver_response", "set_response")} == {
        "requested": 0,
        "value": None,
        "status": "not_exposed",
        "verification": "not_verified",
        "message": "UVC 驱动未暴露 gain 控件，未能通过 V4L2 验证",
    }
    assert result["gain"]["driver_response"].startswith("unknown control 'gain'")
    assert result["gain"]["set_response"].startswith("unknown control 'gain'")
    assert any(command[-1] == "gain=0" for command in commands if "--set-ctrl" in command)
    assert any(command[-1] == "gain" for command in commands if "--get-ctrl" in command)


def test_gain_unknown_during_readback_is_explicit_but_other_errors_still_fail(monkeypatch):
    required = {
        "auto_exposure": 1,
        "exposure_time_absolute": 50,
        "brightness": 0,
        "gamma": 100,
    }
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[-2:] == ["--get-ctrl", "gain"]:
            raise subprocess.CalledProcessError(1, command, stderr="unknown control 'gain'")
        return SimpleNamespace(stdout="\n".join(f"{key}: {value}" for key, value in required.items()))

    monkeypatch.setattr(profile.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(profile.subprocess, "run", fake_run)
    report = profile.verify_camera_controls("/dev/video0")
    assert report["gain"]["status"] == "not_exposed"
    assert report["required_controls_verified"] is True

    def fail_other_control(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="unknown control 'gamma'")

    monkeypatch.setattr(profile.subprocess, "run", fail_other_control)
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        profile.verify_camera_controls("/dev/video0")
    assert exc_info.value.stderr == "unknown control 'gamma'"

    def fail_gain_write(command, **kwargs):
        if command[-2:] == ["--set-ctrl", "gain=0"]:
            raise subprocess.CalledProcessError(1, command, stderr="permission denied")
        if command[-2:] == ["--get-ctrl", "gain"]:
            raise subprocess.CalledProcessError(1, command, stderr="unknown control 'gain'")
        return SimpleNamespace(stdout="\n".join(f"{key}: {value}" for key, value in required.items()))

    monkeypatch.setattr(profile.subprocess, "run", fail_gain_write)
    with pytest.raises(subprocess.CalledProcessError) as gain_write_error:
        profile.apply_camera_controls("/dev/video0")
    assert gain_write_error.value.stderr == "permission denied"


def test_exposed_gain_must_read_back_as_zero(monkeypatch):
    required = {
        "auto_exposure": 1,
        "exposure_time_absolute": 50,
        "brightness": 0,
        "gamma": 100,
    }

    def fake_run(command, **kwargs):
        if command[-2:] == ["--get-ctrl", "gain"]:
            return SimpleNamespace(stdout="gain: 2")
        return SimpleNamespace(stdout="\n".join(f"{key}: {value}" for key, value in required.items()))

    monkeypatch.setattr(profile.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(profile.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="gain readback mismatch"):
        profile.verify_camera_controls("/dev/video0")
