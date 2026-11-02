from __future__ import annotations

import os

from scripts.experiments import run_jpeg_counter as runner


def test_native_library_prefers_explicit_path_without_building(tmp_path, monkeypatch):
    library = tmp_path / "selected.so"
    library.write_bytes(b"selected")

    def unexpected_build(_path):
        raise AssertionError("an explicit native library must not be rebuilt")

    monkeypatch.setattr(runner, "build_native", unexpected_build, raising=False)

    result = runner.resolve_native_library(library, tmp_path)

    assert result == library.resolve()


def test_native_library_uses_existing_environment_path(tmp_path, monkeypatch):
    library = tmp_path / "environment.so"
    library.write_bytes(b"environment")
    monkeypatch.setenv("RLE_NATIVE_LIB", str(library))

    result = runner.resolve_native_library(None, tmp_path)

    assert result == library.resolve()


def test_native_library_builds_source_bound_candidate_before_hardware(tmp_path, monkeypatch):
    built = []

    def fake_build(path):
        path.write_bytes(b"compiled-for-current-source")
        built.append(path)
        return path

    monkeypatch.delenv("RLE_NATIVE_LIB", raising=False)
    monkeypatch.setattr(runner, "build_native", fake_build, raising=False)
    source = runner.Path(runner.__file__).resolve().parents[1] / "rle" / "native" / "rle_components.cpp"
    source_digest = runner._sha256(source)
    suffix = ".dll" if os.name == "nt" else ".so"
    expected = (tmp_path / f"rle_components_v2_{source_digest[:12]}{suffix}").resolve()

    result = runner.resolve_native_library(None, tmp_path)

    assert result == expected
    assert built == [expected]
    assert expected.read_bytes() == b"compiled-for-current-source"
