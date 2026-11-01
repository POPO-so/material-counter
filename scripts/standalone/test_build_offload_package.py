import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts.standalone.bundle import required_addresses
from scripts.standalone.delivery import verify_package
from scripts.standalone.test_delivery import make_package
from scripts.standalone.build_offload_package import build_offload_package
import scripts.standalone.build_offload_package as builder


BIT = b"new reviewed offload bitstream"
OFFLOAD_MODULES = (
    "scripts/standalone/button_events.py",
    "scripts/standalone/exposure.py",
    "scripts/standalone/runtime_metrics.py",
    "scripts/standalone/diagnostic_mode.py",
    "scripts/standalone/pl_preview.py",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_hwh():
    ranges = "".join(
        f'<MEMRANGE INSTANCE="{name}" BASEVALUE="{hex(address)}"/>'
        for name, address in required_addresses.items()
    )
    ranges += ('<MEMRANGE INSTANCE="binary_overlay" BASEVALUE="0x43c50000"/>'
               '<MEMRANGE INSTANCE="image_pipeline" BASEVALUE="0x43c20000"/>')
    params = ''.join(
        f'<PARAMETER NAME="{name}" VALUE="{value}"/>'
        for name, value in {"C_NUM_FSTORES": 4, "C_INCLUDE_S2MM": 0,
                            "C_M_AXIS_MM2S_TDATA_WIDTH": 32,
                            "C_M_AXI_MM2S_DATA_WIDTH": 32}.items()
    )
    return (f'<ROOT>{ranges}<MODULE INSTANCE="axi_vdma"><PARAMETERS>{params}</PARAMETERS></MODULE>'
            '<MODULE INSTANCE="btns_gpio"><PARAMETERS><PARAMETER NAME="C_INTERRUPT_PRESENT" VALUE="1"/>'
            '</PARAMETERS><PORT NAME="ip2intc_irpt"><CONNECTIONS><CONNECTION INSTANCE="video_interrupts" '
            'PORT="In3"/></CONNECTIONS></PORT></MODULE><MODULE INSTANCE="video_interrupts">'
            '<PARAMETERS><PARAMETER NAME="NUM_PORTS" VALUE="4"/></PARAMETERS></MODULE></ROOT>').encode()


def make_source_repo(root, initial_modules):
    sources = {
        "scripts/standalone/integrated_run.py": b"from .button_events import Buttons\n",
        "scripts/standalone/button_events.py": b"from .exposure import Exposure\n",
        "scripts/standalone/exposure.py": b"from scripts.rle.transport import Raw\n",
        "scripts/rle/transport.py": b"class Raw: pass\n",
    }
    for relative in initial_modules:
        sources.setdefault(relative, b"# source module\n")
    for relative in OFFLOAD_MODULES:
        sources.setdefault(relative, b"# offload module\n")
    for relative, data in sources.items():
        write(root / relative, data)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "fixture"], cwd=root, check=True)
    subprocess.run(["git", "add", "scripts"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


@pytest.fixture
def package_inputs(tmp_path):
    baseline = tmp_path / "v9-baseline"
    baseline_index = make_package(baseline)

    source = tmp_path / "source"
    source_commit = make_source_repo(source, json.loads(
        (baseline / "package-source.json").read_text())["module_files"])

    hardware = tmp_path / "hardware"
    bit_path = hardware / "offload.bit"
    hwh_path = hardware / "offload.hwh"
    write(bit_path, BIT)
    write(hwh_path, make_hwh())
    manifest = {
        "schema": "material-counter.display-build.v1",
        "ready_for_board": True,
        "rle_monitor": {"abi": 1, "clock_hz": 50_000_000},
        "runtime": json.loads((baseline / "export/display-manifest.json").read_text())["runtime"],
        "files": {"bit": {"path": bit_path.name, "sha256": sha(BIT)},
                  "hwh": {"path": hwh_path.name, "sha256": sha(hwh_path.read_bytes())}},
        "offload": {"abi": 2,
                    "preview": {"signature": "PBO1", "base": 0x43C50000,
                                "source_geometry": [640, 400], "viewport": [384, 146, 512, 320]},
                    "input": {"signature": "RAW1", "mode": "raw"},
                    "buttons": {"interrupt": True, "intc_index": 3,
                                "status_signature": "BTN1"},
                    "features": {"signature": "FRG2"}},
        "rtl_sha256": "a" * 64,
    }
    write(hardware / "display-manifest.json", json.dumps(manifest).encode())
    return baseline, baseline_index, source, source_commit, hardware


def test_build_candidate_binds_git_blobs_index_and_new_manifest(package_inputs, tmp_path):
    baseline, baseline_index, source, commit, hardware = package_inputs
    dirty_bytes = b"uncommitted worktree source must not enter candidate\n"
    write(source / "scripts/standalone/button_events.py", dirty_bytes)
    baseline_before = {p.relative_to(baseline).as_posix(): sha(p.read_bytes())
                       for p in baseline.rglob("*") if p.is_file()}
    output = tmp_path / "candidate"
    result = build_offload_package(
        baseline_root=baseline, baseline_index_sha256=baseline_index,
        source_root=source, source_commit=commit, hardware_export=hardware,
        output=output,
    )
    assert result["verification"]["status"] == "PASS"
    assert verify_package(output, result["index_sha256"])["status"] == "PASS"
    declaration = json.loads((output / "package-source.json").read_text())
    assert declaration["source_commit"] == commit
    assert set(OFFLOAD_MODULES).issubset(declaration["module_files"])
    assert "scripts/rle/transport.py" in declaration["module_files"]
    for relative in declaration["module_files"]:
        source_bytes = subprocess.check_output(
            ["git", "-C", str(source), "cat-file", "blob", f"{commit}:{relative}"])
        assert (output / relative).read_bytes() == source_bytes
    assert (output / "scripts/standalone/button_events.py").read_bytes() != dirty_bytes
    assert (output / "boot-config.json").read_bytes() == (baseline / "boot-config.json").read_bytes()
    after = {p.relative_to(baseline).as_posix(): sha(p.read_bytes())
             for p in baseline.rglob("*") if p.is_file()}
    assert after == baseline_before


@pytest.mark.parametrize("change", ["not-ready", "wrong-capability"])
def test_rejects_unready_or_non_candidate_hardware(package_inputs, tmp_path, change):
    baseline, baseline_index, source, commit, hardware = package_inputs
    manifest_path = hardware / "display-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if change == "not-ready":
        manifest["ready_for_board"] = False
    elif change == "wrong-capability":
        manifest["offload"]["input"]["mode"] = "packet"
    write(manifest_path, json.dumps(manifest).encode())
    with pytest.raises(ValueError):
        build_offload_package(baseline_root=baseline, baseline_index_sha256=baseline_index,
                              source_root=source, source_commit=commit,
                              hardware_export=hardware, output=tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


def test_requires_explicit_matching_baseline_digest_and_never_overwrites_output(package_inputs, tmp_path):
    baseline, baseline_index, source, commit, hardware = package_inputs
    output = tmp_path / "candidate"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("untouched")
    with pytest.raises(ValueError, match="output"):
        build_offload_package(baseline_root=baseline, baseline_index_sha256=baseline_index,
                              source_root=source, source_commit=commit,
                              hardware_export=hardware, output=output)
    assert sentinel.read_text() == "untouched"
    with pytest.raises(ValueError, match="hash"):
        build_offload_package(baseline_root=baseline, baseline_index_sha256="0" * 64,
                              source_root=source, source_commit=commit,
                              hardware_export=hardware, output=tmp_path / "other")


def test_rejects_known_old_g2_bit_digest(package_inputs, monkeypatch):
    _, _, _, _, hardware = package_inputs
    monkeypatch.setattr(builder, "_sha256_bytes", lambda _data: builder.OLD_BIT_SHA256)
    with pytest.raises(ValueError, match="old G2"):
        builder._load_hardware_manifest(hardware)


def test_rejects_baseline_symlink_escape(package_inputs, tmp_path):
    baseline, baseline_index, source, commit, hardware = package_inputs
    outside = tmp_path / "outside.ttf"
    outside.write_bytes(b"outside baseline root")
    link = baseline / "fonts/escape.ttf"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem does not permit creating symlinks")
    with pytest.raises(ValueError, match="symlink|escapes"):
        build_offload_package(baseline_root=baseline, baseline_index_sha256=baseline_index,
                              source_root=source, source_commit=commit,
                              hardware_export=hardware, output=tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()
