"""Fail-closed compatibility check for the observed offline board runtime."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
from importlib import metadata
import json
import platform
from pathlib import Path
import re
import stat
import subprocess


OBSERVATION_SCHEMA = "material-counter.board-runtime-observation.v2"
NATIVE_VIDEO_FILE = "lib/_video.cpython-310-arm-linux-gnueabihf.so"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_PACKAGES = {
    "pynq": "pynq",
    "numpy": "numpy",
    "opencv": None,
    "pillow": "Pillow",
    "cffi": "cffi",
}
REQUIRED_OS_PACKAGES = (
    "python3-opencv:armhf",
    "libopencv-core4.5d:armhf",
    "libopencv-imgproc4.5d:armhf",
    "libopencv-videoio4.5d:armhf",
    "python3-cffi",
    "python3-numpy",
    "python3-pil:armhf",
    "python3-scipy",
)
_PLATFORM_PATTERN = re.compile(
    r"^(?P<system>\S+) (?P<release>\S+) (?P<machine>\S+) "
    r"(?P<libc_name>\S+) (?P<libc_version>\S+)$"
)


def current_runtime():
    """Read compatibility fields without importing PYNQ or accessing hardware."""
    try:
        pynq_spec = importlib.util.find_spec("pynq")
    except Exception as error:
        raise RuntimeError("cannot locate installed PYNQ package") from error
    pynq_origin = getattr(pynq_spec, "origin", None)
    if not isinstance(pynq_origin, str):
        raise RuntimeError("cannot locate installed PYNQ package")
    try:
        pynq_root = Path(pynq_origin).resolve(strict=True).parent
        native_path = pynq_root / NATIVE_VIDEO_FILE
        resolved_native_path = native_path.resolve(strict=True)
        resolved_native_path.relative_to(pynq_root)
        native_stat = resolved_native_path.stat()
        if not stat.S_ISREG(native_stat.st_mode):
            raise RuntimeError(
                "PYNQ native extension is not a regular file: {}".format(
                    NATIVE_VIDEO_FILE
                )
            )
        digest = hashlib.sha256()
        with resolved_native_path.open("rb") as native_file:
            for chunk in iter(lambda: native_file.read(1024 * 1024), b""):
                digest.update(chunk)
        native_files = {NATIVE_VIDEO_FILE: digest.hexdigest()}
    except (OSError, ValueError) as error:
        raise RuntimeError(
            "cannot safely read PYNQ native extension: {}".format(NATIVE_VIDEO_FILE)
        ) from error

    packages = {}
    for name, distribution in REQUIRED_PACKAGES.items():
        if distribution is not None:
            try:
                packages[name] = metadata.version(distribution)
            except metadata.PackageNotFoundError as error:
                raise RuntimeError(
                    "runtime distribution is missing: {}".format(distribution)
                ) from error

    try:
        cv2 = importlib.import_module("cv2")
    except (ImportError, OSError) as error:
        raise RuntimeError("runtime module is missing: cv2") from error
    cv2_version = getattr(cv2, "__version__", None)
    if not isinstance(cv2_version, str) or not cv2_version:
        raise RuntimeError("runtime module cv2 has no version")
    packages["opencv"] = cv2_version

    os_packages = {}
    for package in REQUIRED_OS_PACKAGES:
        try:
            result = subprocess.run(
                ["dpkg-query", "-W", "-f=${db:Status-Abbrev}\\t${Version}", package],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "dpkg-query timed out for package: {}".format(package)
            ) from error
        except OSError as error:
            raise RuntimeError("cannot run dpkg-query: {}".format(error)) from error
        if result.returncode != 0:
            raise RuntimeError(
                "dpkg package is missing or cannot be queried: {}".format(package)
            )
        status_and_version = result.stdout.rstrip("\r\n")
        if "\t" not in status_and_version:
            raise RuntimeError(
                "dpkg-query returned an invalid status/version for: {}".format(package)
            )
        status, version = status_and_version.split("\t", 1)
        if status.strip() != "ii":
            raise RuntimeError(
                "dpkg package is not installed (status={!r}): {}".format(
                    status, package
                )
            )
        version = version.strip()
        if not version:
            raise RuntimeError("dpkg-query returned no version for: {}".format(package))
        os_packages[package] = version

    libc_name, libc_version = platform.libc_ver()
    return {
        "python": platform.python_version(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "libc": {"name": libc_name, "version": libc_version},
        "packages": packages,
        "os_packages": os_packages,
        "native_files": native_files,
    }


def _expected_runtime(observation):
    if not isinstance(observation, dict):
        raise ValueError("board runtime observation must be an object")
    if observation.get("schema") != OBSERVATION_SCHEMA:
        raise ValueError("unknown board runtime observation schema")

    python_version = observation.get("python")
    if not isinstance(python_version, str) or not python_version:
        raise ValueError("board runtime observation lacks python version")
    platform_value = observation.get("platform")
    if not isinstance(platform_value, str):
        raise ValueError("board runtime observation lacks platform")
    match = _PLATFORM_PATTERN.fullmatch(platform_value)
    if match is None:
        raise ValueError("board runtime observation has invalid platform")

    observed_packages = observation.get("packages")
    if not isinstance(observed_packages, dict):
        raise ValueError("board runtime observation lacks packages")
    packages = {}
    for name in REQUIRED_PACKAGES:
        version = observed_packages.get(name)
        if not isinstance(version, str) or not version:
            raise ValueError(
                "board runtime observation lacks package version: {}".format(name)
            )
        packages[name] = version

    observed_os_packages = observation.get("os_packages")
    if not isinstance(observed_os_packages, dict):
        raise ValueError("board runtime observation lacks os_packages")
    if set(observed_os_packages) != set(REQUIRED_OS_PACKAGES):
        missing = sorted(set(REQUIRED_OS_PACKAGES) - set(observed_os_packages))
        unknown = sorted(set(observed_os_packages) - set(REQUIRED_OS_PACKAGES))
        raise ValueError(
            "board runtime observation os_packages must contain exactly the required packages"
            " (missing={!r}, unknown={!r})".format(missing, unknown)
        )
    os_packages = {}
    for name in REQUIRED_OS_PACKAGES:
        version = observed_os_packages[name]
        if not isinstance(version, str) or not version:
            raise ValueError(
                "board runtime observation lacks os package version: {}".format(name)
            )
        os_packages[name] = version

    observed_native_files = observation.get("native_files")
    if not isinstance(observed_native_files, dict):
        raise ValueError("board runtime observation lacks native_files")
    if set(observed_native_files) != {NATIVE_VIDEO_FILE}:
        missing = sorted({NATIVE_VIDEO_FILE} - set(observed_native_files))
        unknown = sorted(set(observed_native_files) - {NATIVE_VIDEO_FILE})
        raise ValueError(
            "board runtime observation native_files must contain exactly the required file"
            " (missing={!r}, unknown={!r})".format(missing, unknown)
        )
    native_digest = observed_native_files[NATIVE_VIDEO_FILE]
    if (
        not isinstance(native_digest, str)
        or _SHA256_PATTERN.fullmatch(native_digest) is None
    ):
        raise ValueError(
            "board runtime observation has invalid SHA-256 for: {}".format(
                NATIVE_VIDEO_FILE
            )
        )

    return {
        "python": python_version,
        "system": match.group("system"),
        "release": match.group("release"),
        "machine": match.group("machine"),
        "libc": {
            "name": match.group("libc_name"),
            "version": match.group("libc_version"),
        },
        "packages": packages,
        "os_packages": os_packages,
        "native_files": {NATIVE_VIDEO_FILE: native_digest},
    }


def verify_runtime_observation(observation, actual):
    """Compare current compatibility fields with a read-only board observation."""
    expected = _expected_runtime(observation)
    if not isinstance(actual, dict):
        raise RuntimeError("current runtime probe did not return an object")

    mismatches = []
    for key in ("python", "system", "release", "machine"):
        if actual.get(key) != expected[key]:
            mismatches.append(
                "{}: expected {!r}, got {!r}".format(key, expected[key], actual.get(key))
            )
    actual_libc = actual.get("libc")
    if not isinstance(actual_libc, dict):
        actual_libc = {}
    for key, expected_value in expected["libc"].items():
        if actual_libc.get(key) != expected_value:
            mismatches.append(
                "libc.{}: expected {!r}, got {!r}".format(
                    key, expected_value, actual_libc.get(key)
                )
            )

    actual_packages = actual.get("packages")
    if not isinstance(actual_packages, dict):
        actual_packages = {}
    for name, expected_version in expected["packages"].items():
        if actual_packages.get(name) != expected_version:
            mismatches.append(
                "packages.{}: expected {!r}, got {!r}".format(
                    name, expected_version, actual_packages.get(name)
                )
            )
    actual_os_packages = actual.get("os_packages")
    if not isinstance(actual_os_packages, dict):
        actual_os_packages = {}
    for name, expected_version in expected["os_packages"].items():
        if actual_os_packages.get(name) != expected_version:
            mismatches.append(
                "os_packages.{}: expected {!r}, got {!r}".format(
                    name, expected_version, actual_os_packages.get(name)
                )
            )
    actual_native_files = actual.get("native_files")
    if not isinstance(actual_native_files, dict):
        actual_native_files = {}
    if actual_native_files != expected["native_files"]:
        mismatches.append(
            "native_files.{}: expected {!r}, got {!r}".format(
                NATIVE_VIDEO_FILE,
                expected["native_files"][NATIVE_VIDEO_FILE],
                actual_native_files.get(NATIVE_VIDEO_FILE),
            )
        )
    if mismatches:
        raise RuntimeError(
            "runtime compatibility mismatch; refusing hardware initialization: "
            + "; ".join(mismatches)
        )

    return {
        "status": "PASS",
        "scope": "observed_runtime_compatibility_only",
        "expected": expected,
        "actual": actual,
    }


def verify_runtime_environment(observation_path):
    """Read the package's pinned observation and check it before hardware setup."""
    path = Path(observation_path)
    try:
        observation = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(
            "cannot read board runtime observation: {}".format(path)
        ) from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid board runtime observation: {}".format(path)) from error
    return verify_runtime_observation(observation, current_runtime())
