"""Pure policy helpers for one-time standalone boot initialization."""

import json
import math
from pathlib import Path, PurePosixPath
import re
import uuid


_MARKER_INITIALIZING = "INITIALIZING"
_MARKER_READY = "READY"
_MARKER_FAILED = "FAILED"
_CONFIG_SCHEMA = "material-counter.integrated-boot.v1"
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


def _normalize_boot_id(boot_id):
    try:
        return str(uuid.UUID(str(boot_id)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("boot_id must be a valid UUID") from exc


def _write_marker(path, boot_id, phase, *, exclusive=False):
    mode = "x" if exclusive else "w"
    with open(path, mode, encoding="utf-8") as marker_file:
        json.dump({"boot_id": boot_id, "phase": phase}, marker_file, separators=(",", ":"))
        marker_file.write("\n")


def _read_marker(path):
    try:
        with open(path, "r", encoding="utf-8") as marker_file:
            marker = json.load(marker_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("boot marker cannot be read") from exc
    if not isinstance(marker, dict):
        raise RuntimeError("boot marker is invalid")
    return marker


def _result(configured, boot_id, phase, mode):
    return {"configured": configured, "boot_id": boot_id, "phase": phase, "mode": mode}


def initialize_once(backend, marker_path, boot_id, uptime_s, *, reuse_loaded=False):
    """Audit, then configure at most once for this boot and persist marker state."""
    backend.audit()
    normalized_boot_id = _normalize_boot_id(boot_id)
    marker_path = Path(marker_path)

    if marker_path.exists():
        marker = _read_marker(marker_path)
        if marker.get("boot_id") != normalized_boot_id:
            raise RuntimeError("boot marker belongs to a different boot")
        if marker.get("phase") != _MARKER_READY:
            raise RuntimeError("boot marker is not READY; automatic retry is forbidden")
        backend.verify_loaded()
        return _result(False, normalized_boot_id, _MARKER_READY, "already_ready")

    if not reuse_loaded:
        if isinstance(uptime_s, bool) or not isinstance(uptime_s, (int, float)):
            raise ValueError("uptime_s must be a finite number between 0 and 300")
        if not math.isfinite(uptime_s) or not 0 <= uptime_s <= 300:
            raise ValueError("uptime_s must be a finite number between 0 and 300")

    try:
        _write_marker(marker_path, normalized_boot_id, _MARKER_INITIALIZING, exclusive=True)
    except FileExistsError as exc:
        raise RuntimeError("boot marker was created concurrently") from exc

    if reuse_loaded:
        try:
            backend.verify_loaded()
        except Exception:
            _write_marker(marker_path, normalized_boot_id, _MARKER_FAILED)
            raise
        _write_marker(marker_path, normalized_boot_id, _MARKER_READY)
        return _result(False, normalized_boot_id, _MARKER_READY, "reuse_loaded")

    try:
        backend.configure()
        backend.verify_loaded()
    except Exception:
        _write_marker(marker_path, normalized_boot_id, _MARKER_FAILED)
        raise

    _write_marker(marker_path, normalized_boot_id, _MARKER_READY)
    return _result(True, normalized_boot_id, _MARKER_READY, "configured")


def _path_string(path):
    return str(Path(path))


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate boot config key: {}".format(key))
        value[key] = item
    return value


def load_boot_config(path):
    """Read boot-config.json, rejecting duplicate keys before any validation."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("boot config cannot be read") from exc
    try:
        config = json.loads(text, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise ValueError("boot config is not valid JSON") from exc
    if not isinstance(config, dict):
        raise ValueError("boot config must be a JSON object")
    return config


def build_argv(root, config, boot_id, output, manifest_sha256, python_executable):
    """Validate integrated-boot configuration and return its argv vector."""
    if not isinstance(config, dict):
        raise TypeError("config must be a dict")
    required_keys = {"schema", "mode", "camera", "duration_s"}
    allowed_keys = required_keys | {"transfer_timing", "diag_snapshot"}
    unknown = set(config) - allowed_keys
    missing = required_keys - set(config)
    if unknown:
        raise ValueError("unsupported boot config fields: {}".format(sorted(unknown)))
    if missing:
        raise ValueError("missing boot config fields: {}".format(sorted(missing)))
    if "transfer_timing" in config and type(config["transfer_timing"]) is not bool:
        raise TypeError("transfer_timing must be a boolean")
    if "diag_snapshot" in config and type(config["diag_snapshot"]) is not bool:
        raise TypeError("diag_snapshot must be a boolean")
    if config["schema"] != _CONFIG_SCHEMA:
        raise ValueError("unsupported config schema")
    if config["mode"] not in ("continuous", "acceptance"):
        raise ValueError("mode must be continuous or acceptance")

    duration_s = config["duration_s"]
    if isinstance(duration_s, bool) or not isinstance(duration_s, int) or not 1 <= duration_s <= 7200:
        raise ValueError("duration_s must be an integer between 1 and 7200")

    camera = config["camera"]
    if not isinstance(camera, str):
        raise TypeError("camera must be a path string")
    camera_path = PurePosixPath(camera)
    if (
        str(camera_path) != camera
        or camera_path.parent != PurePosixPath("/dev/v4l/by-id")
        or len(camera_path.parts) != 5
        or camera_path.name in ("", ".", "..")
    ):
        raise ValueError("camera must be a direct child of /dev/v4l/by-id")

    if not isinstance(manifest_sha256, str) or not _SHA256_RE.fullmatch(manifest_sha256):
        raise ValueError("manifest_sha256 must contain exactly 64 hexadecimal characters")
    normalized_boot_id = _normalize_boot_id(boot_id)

    root = Path(root)
    argv = [
        str(python_executable),
        "-B",
        "-m",
        "scripts.standalone.integrated_run",
        "--execute",
        "--manifest",
        str(root / "export" / "display-manifest.json"),
        "--manifest-sha256",
        manifest_sha256,
        "--boot-id",
        normalized_boot_id,
        "--native-lib",
        str(root / "native" / "rle_components_v2.so"),
        "--output",
        _path_string(output),
        "--source",
        "camera",
        "--display",
        "on",
        "--duration",
        str(duration_s),
        "--frames",
        "200000",
        "--camera",
        camera,
        "--font",
        str(root / "fonts" / "DroidSansFallbackFull.ttf"),
        "--latin-font",
        str(root / "fonts" / "DejaVuSans.ttf"),
    ]
    if config["mode"] == "continuous":
        argv.append("--continuous")
    if config.get("transfer_timing", False):
        argv.append("--transfer-timing")
    if config.get("diag_snapshot", False):
        argv.append("--diag-snapshot")
    return argv
