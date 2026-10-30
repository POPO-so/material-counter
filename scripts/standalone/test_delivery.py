import hashlib
import json
import struct

import pytest

from scripts.standalone.delivery import verify_package
from scripts.standalone.bundle import required_addresses


REQUIRED = (
    "export/display-manifest.json",
    "boot-config.json",
    "export/g2-integrated.bit",
    "export/g2-integrated.hwh",
    "native/rle_components_v2.so",
    "fonts/DroidSansFallbackFull.ttf",
    "fonts/DejaVuSans.ttf",
    "runtime/pynq/rle_monitor.py",
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_package(root):
    bit = b"synthetic integrated bitstream"
    ranges = "".join(
        f'<MEMRANGE INSTANCE="{name}" BASEVALUE="{hex(address)}"/>'
        for name, address in required_addresses.items()
    )
    params = "".join(
        f'<PARAMETER NAME="{name}" VALUE="{value}"/>'
        for name, value in {
            "C_NUM_FSTORES": 4,
            "C_INCLUDE_S2MM": 0,
            "C_M_AXIS_MM2S_TDATA_WIDTH": 32,
            "C_M_AXI_MM2S_DATA_WIDTH": 32,
        }.items()
    )
    hwh = f'<ROOT>{ranges}<MODULE INSTANCE="axi_vdma"><PARAMETERS>{params}</PARAMETERS></MODULE></ROOT>'.encode()
    _write(root / "export/g2-integrated.bit", bit)
    _write(root / "export/g2-integrated.hwh", hwh)

    fallback_font = b"synthetic fallback font"
    latin_font = b"synthetic latin font"
    _write(root / "fonts/DroidSansFallbackFull.ttf", fallback_font)
    _write(root / "fonts/DejaVuSans.ttf", latin_font)
    monitor = b"# pinned source\n"
    _write(root / "runtime/pynq/rle_monitor.py", monitor)
    boot_config = {
        "schema": "material-counter.integrated-boot.v1",
        "mode": "continuous",
        "camera": "/dev/v4l/by-id/usb-Camera_ABC-video-index0",
        "duration_s": 3600,
    }
    _write(root / "boot-config.json", json.dumps(boot_config).encode())

    # ELF32, little endian, ARM (EM_ARM = 40).
    elf = bytearray(20)
    elf[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<H", elf, 18, 40)
    _write(root / "native/rle_components_v2.so", bytes(elf))

    display = {
        "schema": "material-counter.display-build.v1",
        "ready_for_board": True,
        "files": {
            "bit": {"path": "g2-integrated.bit", "sha256": _sha(bit)},
            "hwh": {"path": "g2-integrated.hwh", "sha256": _sha(hwh)},
        },
        "rle_monitor": {"abi": 1, "clock_hz": 50000000},
        "runtime": {
            "font_sha256": _sha(fallback_font),
            "latin_font_sha256": _sha(latin_font),
            "sources": [{"path": "rle_monitor.py", "sha256": _sha(monitor)}],
        },
    }
    display_bytes = json.dumps(display).encode()
    _write(root / "export/display-manifest.json", display_bytes)

    entries = {}
    for relative in REQUIRED:
        data = (root / relative).read_bytes()
        entries[relative] = {"sha256": _sha(data), "bytes": len(data)}
    index = {
        "schema": "material-counter.standalone-package.v1",
        "files": entries,
    }
    index_bytes = json.dumps(index).encode()
    _write(root / "deployment-index.json", index_bytes)
    return _sha(index_bytes)


def test_valid_standalone_package_is_verified_offline(tmp_path):
    digest = make_package(tmp_path)

    result = verify_package(tmp_path, digest)

    assert result == {
        "status": "PASS",
        "files_checked": len(REQUIRED) + 1,
        "hardware_actions": 0,
        "scope": "integrity_only_no_gate",
    }


def test_modified_package_file_is_rejected(tmp_path):
    digest = make_package(tmp_path)
    (tmp_path / "native/rle_components_v2.so").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="hash|byte"):
        verify_package(tmp_path, digest)


def test_unlisted_extra_file_is_rejected(tmp_path):
    digest = make_package(tmp_path)
    _write(tmp_path / "runtime/unreviewed.py", b"extra code")

    with pytest.raises(ValueError, match="file set|unexpected|extra"):
        verify_package(tmp_path, digest)


def test_escaping_index_path_is_rejected(tmp_path):
    make_package(tmp_path)
    index_path = tmp_path / "deployment-index.json"
    index = json.loads(index_path.read_text())
    index["files"]["../outside"] = {"sha256": "0" * 64, "bytes": 0}
    index_path.write_text(json.dumps(index))

    with pytest.raises(ValueError, match="path|index hash"):
        verify_package(tmp_path, _sha(index_path.read_bytes()))


def test_duplicate_json_file_path_key_is_rejected(tmp_path):
    make_package(tmp_path)
    index_path = tmp_path / "deployment-index.json"
    index = json.loads(index_path.read_text())
    first_path, first_metadata = next(iter(index["files"].items()))
    serialized_entries = [
        f"{json.dumps(path)}:{json.dumps(metadata)}"
        for path, metadata in index["files"].items()
    ]
    serialized_entries.append(f"{json.dumps(first_path)}:{json.dumps(first_metadata)}")
    raw_index = (
        '{"schema":"material-counter.standalone-package.v1","files":{'
        + ",".join(serialized_entries)
        + "}}"
    )
    index_path.write_text(raw_index)

    with pytest.raises(ValueError, match="duplicate.*key|duplicate.*path"):
        verify_package(tmp_path, _sha(index_path.read_bytes()))


def test_font_manifest_hash_mismatch_is_rejected(tmp_path):
    make_package(tmp_path)
    manifest_path = tmp_path / "export/display-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["runtime"]["font_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    _refresh_package_index(tmp_path, manifest_path)

    index_path = tmp_path / "deployment-index.json"
    with pytest.raises(ValueError, match="font"):
        verify_package(tmp_path, _sha(index_path.read_bytes()))


def test_non_arm_elf_is_rejected(tmp_path):
    digest = make_package(tmp_path)
    path = tmp_path / "native/rle_components_v2.so"
    data = bytearray(path.read_bytes())
    struct.pack_into("<H", data, 18, 62)  # x86-64 machine id
    _write(path, bytes(data))
    index_path = tmp_path / "deployment-index.json"
    index = json.loads(index_path.read_text())
    index["files"]["native/rle_components_v2.so"] = {
        "sha256": _sha(data), "bytes": len(data)
    }
    index_path.write_text(json.dumps(index))

    with pytest.raises(ValueError, match="ELF|ARM|architecture"):
        verify_package(tmp_path, _sha(index_path.read_bytes()))


def test_bad_hwh_semantics_are_rejected_by_bundle_verifier(tmp_path):
    make_package(tmp_path)
    hwh_path = tmp_path / "export/g2-integrated.hwh"
    hwh = hwh_path.read_bytes().replace(b"0x43000000", b"0x44000000")
    _write(hwh_path, hwh)
    manifest_path = tmp_path / "export/display-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["hwh"]["sha256"] = _sha(hwh)
    manifest_path.write_text(json.dumps(manifest))
    _refresh_package_index(tmp_path, hwh_path)
    _refresh_package_index(tmp_path, manifest_path)

    index_path = tmp_path / "deployment-index.json"
    with pytest.raises(ValueError, match="address"):
        verify_package(tmp_path, _sha(index_path.read_bytes()))


def test_unknown_rle_monitor_settings_are_rejected(tmp_path):
    make_package(tmp_path)
    manifest_path = tmp_path / "export/display-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["rle_monitor"]["abi"] = 2
    manifest_path.write_text(json.dumps(manifest))
    _refresh_package_index(tmp_path, manifest_path)

    index_path = tmp_path / "deployment-index.json"
    with pytest.raises(ValueError, match="monitor|ABI|abi"):
        verify_package(tmp_path, _sha(index_path.read_bytes()))


def _refresh_package_index(root, changed_path):
    index_path = root / "deployment-index.json"
    index = json.loads(index_path.read_text())
    relative = changed_path.relative_to(root).as_posix()
    data = changed_path.read_bytes()
    index["files"][relative] = {"sha256": _sha(data), "bytes": len(data)}
    index_path.write_text(json.dumps(index))
