import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_stageb_package import prepare


def test_stageb_package_bundles_and_hash_pins_camera_packet_dependency(tmp_path):
    output = tmp_path / "board-runtime-final"

    result = prepare(output)

    package_manifest = json.loads((output / "package-manifest.json").read_text())
    stageb_manifest = json.loads((output / "stageb-runtime-manifest.json").read_text())
    dependency = output / "wo0015" / "prepare_vectors.py"
    digest = hashlib.sha256(dependency.read_bytes()).hexdigest()
    wrapper = output / "phase16_board_entry.sh"
    wrapper_digest = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    camera_preview = output / "camera_preview_server.py"
    camera_preview_digest = hashlib.sha256(camera_preview.read_bytes()).hexdigest()

    assert result["status"] == "STAGEB_BOARD_PACKAGE_READY"
    assert dependency.is_file()
    assert package_manifest["files"]["wo0015/prepare_vectors.py"] == digest
    assert stageb_manifest["executor_sha256"]["prepare_vectors.py"] == digest
    assert stageb_manifest["executor_sha256"]["phase16_board_entry.sh"] == wrapper_digest
    assert camera_preview.is_file()
    assert package_manifest["files"]["camera_preview_server.py"] == camera_preview_digest
    assert stageb_manifest["executor_sha256"]["camera_preview_server.py"] == camera_preview_digest
    with zipfile.ZipFile(result["archive"]) as archive:
        assert hashlib.sha256(archive.read("wo0015/prepare_vectors.py")).hexdigest() == digest
