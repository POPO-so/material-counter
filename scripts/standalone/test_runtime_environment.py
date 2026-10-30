import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts.standalone import runtime_environment
from scripts.standalone.runtime_environment import (
    verify_runtime_observation,
)


OS_PACKAGE_VERSIONS = {
    "python3-opencv:armhf": "4.5.4+dfsg-9ubuntu4",
    "libopencv-core4.5d:armhf": "4.5.4+dfsg-9ubuntu4",
    "libopencv-imgproc4.5d:armhf": "4.5.4+dfsg-9ubuntu4",
    "libopencv-videoio4.5d:armhf": "4.5.4+dfsg-9ubuntu4",
    "python3-cffi": "1.15.0-1build2",
    "python3-numpy": "1:1.21.5-1build2",
    "python3-pil:armhf": "9.0.1-1build1",
    "python3-scipy": "1.8.0-1exp2ubuntu1",
}


def observation():
    return {
        "schema": "material-counter.board-runtime-observation.v1",
        "python": "3.10.4",
        "platform": "Linux 6.6.10-xilinx-v2024.1 armv7l glibc 2.35",
        "packages": {
            "pynq": "3.1.1",
            "numpy": "1.21.5",
            "opencv": "4.5.4",
            "pillow": "9.0.1",
            "cffi": "1.15.0",
        },
        "os_packages": dict(OS_PACKAGE_VERSIONS),
    }


def current_runtime():
    return {
        "python": "3.10.4",
        "system": "Linux",
        "release": "6.6.10-xilinx-v2024.1",
        "machine": "armv7l",
        "libc": {"name": "glibc", "version": "2.35"},
        "packages": {
            "pynq": "3.1.1",
            "numpy": "1.21.5",
            "opencv": "4.5.4",
            "pillow": "9.0.1",
            "cffi": "1.15.0",
        },
        "os_packages": dict(OS_PACKAGE_VERSIONS),
    }


def _patch_runtime_probes(monkeypatch, dpkg_query):
    package_versions = {
        "pynq": "3.1.1",
        "numpy": "1.21.5",
        "Pillow": "9.0.1",
        "cffi": "1.15.0",
    }
    monkeypatch.setattr(runtime_environment.platform, "python_version", lambda: "3.10.4")
    monkeypatch.setattr(runtime_environment.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        runtime_environment.platform, "release", lambda: "6.6.10-xilinx-v2024.1"
    )
    monkeypatch.setattr(runtime_environment.platform, "machine", lambda: "armv7l")
    monkeypatch.setattr(
        runtime_environment.platform, "libc_ver", lambda: ("glibc", "2.35")
    )
    monkeypatch.setattr(
        runtime_environment.metadata,
        "version",
        lambda name: package_versions[name],
    )
    monkeypatch.setattr(
        runtime_environment.importlib,
        "import_module",
        lambda name: SimpleNamespace(__version__="4.5.4"),
    )
    monkeypatch.setattr(runtime_environment.subprocess, "run", dpkg_query)


def test_current_runtime_collects_the_boot_environment(monkeypatch):
    def dpkg_query(command, **kwargs):
        assert command[:3] == [
            "dpkg-query",
            "-W",
            "-f=${db:Status-Abbrev}\\t${Version}",
        ]
        assert kwargs["timeout"] == 5
        return SimpleNamespace(
            returncode=0,
            stdout="ii \t{}".format(OS_PACKAGE_VERSIONS[command[-1]]),
        )

    _patch_runtime_probes(monkeypatch, dpkg_query)

    actual = runtime_environment.current_runtime()

    assert actual == current_runtime()


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (SimpleNamespace(returncode=1, stdout=""), "missing or cannot be queried"),
        (SimpleNamespace(returncode=0, stdout="rc \t9.0.1-1build1"), "not installed"),
    ],
)
def test_current_runtime_rejects_missing_or_residual_dpkg_packages(
    monkeypatch, response, message
):
    _patch_runtime_probes(monkeypatch, lambda command, **kwargs: response)

    with pytest.raises(RuntimeError, match=message):
        runtime_environment.current_runtime()


def test_current_runtime_rejects_dpkg_query_timeout(monkeypatch):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    _patch_runtime_probes(monkeypatch, timeout)

    with pytest.raises(RuntimeError, match="timed out"):
        runtime_environment.current_runtime()


def test_runtime_observation_rejects_dpkg_version_mismatch(monkeypatch):
    def dpkg_query(command, **kwargs):
        package = command[-1]
        version = OS_PACKAGE_VERSIONS[package]
        if package == "python3-numpy":
            version = "1:9.9-1"
        return SimpleNamespace(returncode=0, stdout="ii \t{}".format(version))

    _patch_runtime_probes(monkeypatch, dpkg_query)
    actual = runtime_environment.current_runtime()

    with pytest.raises(RuntimeError, match="python3-numpy"):
        verify_runtime_observation(observation(), actual)


def test_runtime_observation_accepts_the_matching_target_profile():
    expected = observation()
    actual = current_runtime()

    result = verify_runtime_observation(expected, actual)

    assert result["status"] == "PASS"
    assert result["actual"] == actual
    assert result["scope"] == "observed_runtime_compatibility_only"


def test_runtime_environment_reads_the_packaged_observation(tmp_path, monkeypatch):
    path = tmp_path / "board-runtime.json"
    path.write_text(json.dumps(observation()), encoding="utf-8")
    monkeypatch.setattr(runtime_environment, "current_runtime", current_runtime)

    result = runtime_environment.verify_runtime_environment(path)

    assert result["status"] == "PASS"
    assert result["actual"] == current_runtime()


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("top", "python", "3.11.0"),
        ("top", "release", "6.6.10-other"),
        ("packages", "opencv", "4.8.0"),
        ("packages", "pillow", "9.1.0"),
    ],
)
def test_runtime_observation_rejects_version_or_platform_mismatches(
    section, key, value
):
    expected = observation()
    actual = current_runtime()
    target = actual if section == "top" else actual["packages"]
    target[key] = value

    with pytest.raises(RuntimeError, match=key):
        verify_runtime_observation(expected, actual)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(schema="unknown.runtime.schema"),
        lambda value: value.pop("python"),
        lambda value: value.update(platform="Linux armv7l"),
        lambda value: value["packages"].pop("opencv"),
        lambda value: value["packages"].update(cffi=None),
        lambda value: value.pop("os_packages"),
    ],
)
def test_invalid_runtime_observation_is_rejected(mutate):
    expected = observation()
    mutate(expected)

    with pytest.raises(ValueError):
        verify_runtime_observation(expected, current_runtime())


def test_runtime_observation_does_not_require_unused_observed_packages():
    expected = observation()
    expected["packages"]["scipy"] = "1.8.0"

    result = verify_runtime_observation(expected, current_runtime())

    assert result["status"] == "PASS"
    assert "scipy" not in result["expected"]["packages"]
