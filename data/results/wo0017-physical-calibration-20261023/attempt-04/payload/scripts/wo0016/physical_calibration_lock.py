"""Independent one-shot lock for non-formal WO-0017 physical calibration."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping


CALIBRATION_ROOT = Path("/var/lib/wo0017/calibration")
LOCK_NAME = "calibration-lock.json"
CONSUMED_NAME = "consumed.json"
_BOOT_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CALIBRATION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", re.ASCII)


def calibration_lock_path(boot_id: str, calibration_id: str, *,
                          root: str | Path = CALIBRATION_ROOT) -> Path:
    if not isinstance(boot_id, str) or _BOOT_ID_RE.fullmatch(boot_id) is None:
        raise ValueError("boot id must be a lowercase UUID")
    if (not isinstance(calibration_id, str)
            or _CALIBRATION_ID_RE.fullmatch(calibration_id) is None):
        raise ValueError("calibration id must contain 1-64 ASCII letters, digits, '_' or '-'")
    return Path(root) / boot_id / calibration_id / LOCK_NAME


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _check_parent(path: Path, *, expected_uid: int | None) -> None:
    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise RuntimeError(f"calibration lock directory is unavailable: {exc}") from exc
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise RuntimeError("calibration lock directory must be a real directory")
    if os.name == "posix" and (
            (expected_uid is not None and parent.st_uid != expected_uid)
            or stat.S_IMODE(parent.st_mode) != 0o700):
        raise RuntimeError("calibration lock directory must be owner-controlled mode 0700")


def _write_exclusive(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(str(path), flags, mode)
    try:
        if os.name == "posix":
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        # Keep an O_EXCL marker after any partial write; never reopen/retry it.
        raise


def write_ready_calibration_lock(path: str | Path,
                                 identity: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(identity, Mapping) or not identity:
        raise ValueError("calibration lock identity must be a non-empty mapping")
    if identity.get("stage") != "physical-calibration":
        raise ValueError("calibration lock stage must be physical-calibration")
    target = Path(path)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=False)
    _check_parent(target, expected_uid=0 if os.name == "posix" else None)
    ready = {**dict(identity), "lock_status": "READY", "consumed": False}
    raw = _json_bytes(ready)
    _write_exclusive(target, raw)
    return {"lock": ready, "sha256": hashlib.sha256(raw).hexdigest()}


def _read_private_json(path: Path, *, label: str,
                       expected_uid: int | None) -> tuple[dict[str, Any], bytes]:
    _check_parent(path, expected_uid=expected_uid)
    try:
        before = path.lstat()
    except OSError as exc:
        raise RuntimeError(f"{label} is unavailable: {exc}") from exc
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode)
            or (os.name == "posix" and (
                (expected_uid is not None and before.st_uid != expected_uid)
                or stat.S_IMODE(before.st_mode) != 0o600))):
        raise RuntimeError(f"{label} must be a private owner-controlled regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or not stat.S_ISREG(opened.st_mode)):
                raise RuntimeError(f"{label} changed while being opened")
            raw = stream.read(16385)
    except OSError as exc:
        raise RuntimeError(f"{label} could not be read safely: {exc}") from exc
    if len(raw) > 16384:
        raise RuntimeError(f"{label} exceeds its size limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must contain a JSON object")
    return value, raw


def validate_ready_calibration_lock(path: str | Path,
                                    identity: Mapping[str, Any], *,
                                    expected_uid: int | None = 0) -> dict[str, Any]:
    if not isinstance(identity, Mapping) or not identity:
        raise ValueError("calibration lock identity must be a non-empty mapping")
    target = Path(path)
    ready, raw = _read_private_json(target, label="calibration lock",
                                    expected_uid=expected_uid)
    expected = {**dict(identity), "lock_status": "READY", "consumed": False}
    if ready != expected:
        raise RuntimeError("calibration lock identity/status does not match this run")
    return {"lock": ready, "sha256": hashlib.sha256(raw).hexdigest()}


def claim_calibration_lock_once(path: str | Path,
                                identity: Mapping[str, Any], *,
                                expected_uid: int | None = 0) -> dict[str, Any]:
    target = Path(path)
    ready = validate_ready_calibration_lock(target, identity,
                                            expected_uid=expected_uid)
    receipt = {
        "schema": 1,
        "work_order": "WO-0017",
        "stage": "physical-calibration",
        "calibration_id": identity["calibration_id"],
        "boot_id": identity["boot_id"],
        "ready_lock_sha256": ready["sha256"],
        "consumed": True,
    }
    _write_exclusive(target.parent / CONSUMED_NAME, _json_bytes(receipt))
    return receipt


def validate_consumed_calibration_lock(path: str | Path,
                                       identity: Mapping[str, Any], *,
                                       expected_uid: int | None = 0) -> dict[str, Any]:
    target = Path(path)
    ready = validate_ready_calibration_lock(target, identity,
                                            expected_uid=expected_uid)
    consumed, raw = _read_private_json(target.parent / CONSUMED_NAME,
                                       label="calibration consumed marker",
                                       expected_uid=expected_uid)
    expected = {
        "schema": 1,
        "work_order": "WO-0017",
        "stage": "physical-calibration",
        "calibration_id": identity["calibration_id"],
        "boot_id": identity["boot_id"],
        "ready_lock_sha256": ready["sha256"],
        "consumed": True,
    }
    if consumed != expected:
        raise RuntimeError("calibration consumed marker identity does not match this run")
    return {"receipt": consumed, "sha256": hashlib.sha256(raw).hexdigest()}
