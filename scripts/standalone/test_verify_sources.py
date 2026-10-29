import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

if importlib.util.find_spec("scripts.standalone.verify_sources") is None:
    verify_sources = None
else:
    from scripts.standalone import verify_sources


def _api():
    if verify_sources is None:
        pytest.fail("verify_sources implementation is missing")
    return verify_sources


def _write(path: Path, data: bytes | str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path, files: dict[str, bytes | str]):
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.name", "Source verifier test")
    _git(root, "config", "user.email", "source-verifier@example.invalid")
    _write(root / "README.fixture", "fixture repository\n")
    for relative, data in files.items():
        _write(root / relative, data)
    _git(root, "add", "--all")
    _git(root, "commit", "-q", "-m", "fixture")
    return root, _git(root, "rev-parse", "HEAD")


def _lock(path: Path, commit: str, files: list[dict]):
    path.write_text(json.dumps({"schema": "material-counter.video-sources.v1", "commit": commit, "files": files}), encoding="utf-8")


def _entry(relative: str, content: bytes):
    normalized = content.replace(b"\r\n", b"\n")
    return {"path": relative, "sha256_lf": hashlib.sha256(normalized).hexdigest()}


def test_verify_sources_accepts_matching_commit_and_crlf_normalized_hash(tmp_path):
    content = b"first line\r\nsecond line\r\n"
    root, head = _repo(tmp_path, {"rtl/video.v": content})
    lock_path = tmp_path / "video_sources.lock.json"
    _lock(lock_path, head, [_entry("rtl/video.v", content)])

    result = _api().verify_sources(root, lock_path)

    assert result["status"] == "ok"
    assert result["head"] == head
    assert result["files"][0]["status"] == "ok"
    assert (root / "rtl/video.v").read_bytes() == content


def test_verify_sources_reports_hash_and_commit_mismatches(tmp_path):
    root, head = _repo(tmp_path, {"rtl/video.v": b"actual\n"})
    lock_path = tmp_path / "video_sources.lock.json"
    _lock(lock_path, "0" * 40, [{"path": "rtl/video.v", "sha256_lf": "f" * 64}])

    result = _api().verify_sources(root, lock_path)

    assert result["status"] == "failed"
    assert any(error["kind"] == "commit" for error in result["errors"])
    assert any(item["status"] == "hash_mismatch" for item in result["files"])


@pytest.mark.parametrize("bad_path", ["../outside.v", "/etc/passwd", "C:/outside.v", "rtl\\outside.v"])
def test_verify_sources_rejects_non_relative_or_escaping_paths(tmp_path, bad_path):
    root, head = _repo(tmp_path, {})
    lock_path = tmp_path / "video_sources.lock.json"
    _lock(lock_path, head, [{"path": bad_path, "sha256_lf": "0" * 64}])

    result = _api().verify_sources(root, lock_path)

    assert result["status"] == "failed"
    assert result["files"][0]["status"] == "invalid_path"


def test_verify_sources_rejects_symlink_resolving_outside_root(tmp_path, monkeypatch):
    root, head = _repo(tmp_path, {"rtl/video.v": b"inside\n"})
    outside = _write(tmp_path / "outside.v", b"outside\n")
    candidate = root / "rtl/video.v"
    lock_path = tmp_path / "video_sources.lock.json"
    _lock(lock_path, head, [_entry("rtl/video.v", b"inside\n")])
    original_resolve = Path.resolve

    def resolve_escape(path, *args, **kwargs):
        if path == candidate:
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_escape)
    result = _api().verify_sources(root, lock_path)

    assert result["status"] == "failed"
    assert result["files"][0]["status"] == "invalid_path"


def test_verify_sources_reports_missing_file_and_cli_exits_nonzero(tmp_path, monkeypatch, capsys):
    root, head = _repo(tmp_path, {})
    lock_path = tmp_path / "video_sources.lock.json"
    _lock(lock_path, head, [_entry("missing.v", b"not present")])
    api = _api()
    monkeypatch.setattr(api, "LOCK_PATH", lock_path)

    assert api.main(["--source-root", str(root)]) != 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "failed"
    assert output["files"][0]["status"] == "missing"


def test_cli_brief_summarizes_success_without_file_details(tmp_path, monkeypatch, capsys):
    content = b"video source\n"
    root, head = _repo(tmp_path, {"video.v": content})
    lock_path = tmp_path / "video_sources.lock.json"
    _lock(lock_path, head, [_entry("video.v", content)])
    api = _api()
    monkeypatch.setattr(api, "LOCK_PATH", lock_path)

    assert api.main(["--source-root", str(root), "--brief"]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary == {
        "status": "ok",
        "files": {"total": 1, "ok": 1, "failed": 0},
        "errors": {"count": 0, "by_kind": {}},
    }


def test_cli_brief_summarizes_failure_and_preserves_nonzero_exit(tmp_path, monkeypatch, capsys):
    root, head = _repo(tmp_path, {"video.v": b"actual\n"})
    lock_path = tmp_path / "video_sources.lock.json"
    _lock(lock_path, head, [{"path": "video.v", "sha256_lf": "0" * 64}])
    api = _api()
    monkeypatch.setattr(api, "LOCK_PATH", lock_path)

    assert api.main(["--source-root", str(root), "--brief"]) == 1
    summary = json.loads(capsys.readouterr().out)

    assert summary["status"] == "failed"
    assert summary["files"] == {"total": 1, "ok": 0, "failed": 1}
    assert summary["errors"] == {"count": 1, "by_kind": {"hash": 1}}

