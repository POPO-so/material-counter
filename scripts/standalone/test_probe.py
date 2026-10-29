import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.standalone import probe


def _write(root: Path, relative: str, data: str | bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def test_collect_reads_iio_raw_scale_offset_and_computed_value(tmp_path):
    dev = tmp_path / "sys/bus/iio/devices/iio-device0"
    _write(dev, "name", "xadc")
    _write(dev, "in_voltage0_label", "vaux0")
    _write(dev, "in_voltage0_raw", "1200\n")
    _write(dev, "in_voltage0_scale", "0.5\n")
    _write(dev, "in_voltage0_offset", "-10\n")

    report = probe.collect(root=tmp_path, pynq_root=tmp_path / "missing-pynq")
    channels = report["iio"]["channels"]

    channel = next(item for item in channels if item["label"] == "vaux0")
    assert channel["raw"] == 1200
    assert channel["scale"] == 0.5
    assert channel["offset"] == -10.0
    assert channel["value"] == 595.0
    assert channel["unit"] == "mV"
    assert channel["value_si"] == 0.595
    assert isinstance(channel["sampled_monotonic"], (int, float))


def test_iio_xadc_temperature_uses_linux_offset_order_and_millidegree_unit(tmp_path):
    dev = tmp_path / "sys/bus/iio/devices/iio-device0"
    _write(dev, "name", "xadc")
    _write(dev, "in_temp0_raw", "2500\n")
    _write(dev, "in_temp0_scale", "123.04\n")
    _write(dev, "in_temp0_offset", "-2220\n")

    report = probe.collect(root=tmp_path, pynq_root=tmp_path / "missing-pynq")
    channel = report["iio"]["channels"][0]

    assert channel["value"] == pytest.approx(34451.2)
    assert channel["unit"] == "m°C"
    assert channel["value_si"] == pytest.approx(34.4512)


def test_missing_and_unreadable_evidence_is_explicit_unknown(tmp_path, monkeypatch):
    _write(tmp_path, "proc/meminfo", "MemTotal: 100 kB\n")
    _write(tmp_path, "proc/interrupts", "           CPU0\n")
    blocked = _write(tmp_path, "sys/bus/iio/devices/iio-device0/in_temp0_raw", "not-readable")
    original = Path.read_text

    def deny_one(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("fixture permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_one)
    report = probe.collect(root=tmp_path, pynq_root=tmp_path / "missing-pynq")

    assert report["memory"]["status"] == "partial"
    assert report["memory"]["cma"]["status"] == "unknown"
    assert report["system"]["boot_id"]["status"] == "unknown"
    assert any(error["kind"] == "permission" for error in report["errors"])
    assert report["status"] == "evidence_only"


def test_cma_free_without_cma_total_is_not_complete_evidence(tmp_path):
    _write(tmp_path, "proc/meminfo", "MemTotal: 100 kB\nCmaFree: 20 kB\n")

    report = probe.collect(root=tmp_path, pynq_root=tmp_path / "missing-pynq")

    assert report["memory"]["cma"]["status"] in {"partial", "unknown"}
    assert report["memory"]["status"] == "partial"


def test_camera_by_id_keeps_stable_path_and_records_resolved_target(tmp_path, monkeypatch):
    target = _write(tmp_path, "dev/video2", b"")
    stable_dir = tmp_path / "dev/v4l/by-id"
    stable_dir.mkdir(parents=True)
    stable = stable_dir / "usb-Camera_ABC-video-index0"
    stable.write_bytes(b"")
    original_resolve = Path.resolve

    def resolve_fixture(path, *args, **kwargs):
        if path == stable:
            return target
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_fixture)

    report = probe.collect(root=tmp_path, pynq_root=tmp_path / "missing-pynq")

    assert report["camera"]["by_id"][0]["path"] == "/dev/v4l/by-id/usb-Camera_ABC-video-index0"
    assert report["camera"]["by_id"][0]["resolved"] == "/dev/video2"


def test_camera_by_id_dangling_link_cannot_report_camera_status_ok(tmp_path, monkeypatch):
    stable_dir = tmp_path / "dev/v4l/by-id"
    stable_dir.mkdir(parents=True)
    stable = stable_dir / "usb-Camera-video-index0"
    stable.write_bytes(b"")
    original_resolve = Path.resolve

    def resolve_dangling(path, *args, **kwargs):
        if path == stable:
            raise FileNotFoundError("dangling by-id target")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_dangling)
    report = probe.collect(root=tmp_path, pynq_root=tmp_path / "missing-pynq")

    assert report["camera"]["status"] in {"partial", "unknown"}
    assert report["camera"]["by_id"][0]["resolved"] == "unknown"


def test_hashes_sources_installed_artifacts_and_extracts_hwh_video_button_ips(tmp_path):
    pynq_root = tmp_path / "usr/local/lib/python3.11/site-packages/pynq"
    source_files = [
        "lib/video/common.py", "lib/video/dma.py", "lib/video/frontend.py",
        "lib/video/dvi.py", "lib/video/pipeline.py", "lib/video/hierarchies.py", "buffer.py",
    ]
    expected = {}
    for relative in source_files:
        data = ("source:" + relative).encode()
        _write(pynq_root, relative, data)
        expected[relative] = hashlib.sha256(data).hexdigest()

    overlay_dir = pynq_root / "overlays/base"
    artifacts = {"base.bit": b"bitstream", "base.tcl": b"create_bd_design", "base.hwh": b"""<?xml version="1.0"?><ROOT><MODULES>
      <MODULE INSTANCENAME="axi_vdma" FULLNAME="design_1/video/hdmi_out" VLNV="xilinx.com:ip:axi_vdma:6.3"><PARAMETERS><PARAMETER NAME="C_BASEADDR" VALUE="0x43000000"/><PARAMETER NAME="C_NUM_FSTORES" VALUE="4"/></PARAMETERS><MEMRANGE NAME="S_AXI_LITE" BASEVALUE="0x43C00000" HIGHVALUE="0x43C0FFFF"/><PORTS><PORT NAME="vtc_irq"/><PORT NAME="introut"/></PORTS></MODULE>
      <MODULE INSTANCENAME="axi_gpio" FULLNAME="design_1/buttons/gpio" VLNV="xilinx.com:ip:axi_gpio:2.0"><PARAMETERS><PARAMETER NAME="C_BASEADDR" VALUE="0x41200000"/><PARAMETER NAME="C_GPIO_WIDTH" VALUE="4"/></PARAMETERS><PORTS><PORT NAME="ip2intc_irpt"/></PORTS></MODULE>
    </MODULES></ROOT>"""}
    artifact_hashes = {}
    for name, data in artifacts.items():
        _write(overlay_dir, name, data)
        artifact_hashes[name] = hashlib.sha256(data).hexdigest()

    report = probe.collect(root=tmp_path, pynq_root=pynq_root)
    source_hashes = {item["path"].removeprefix(str(pynq_root) + os.sep).replace(os.sep, "/"): item["sha256"] for item in report["pynq"]["sources"]}
    overlay_hashes = {item["name"]: item["sha256"] for item in report["pynq"]["base_artifacts"]}

    assert source_hashes == expected
    assert overlay_hashes == artifact_hashes
    assert report["pynq"]["base_ips"]["video"]
    assert report["pynq"]["base_ips"]["buttons"]
    video_ip = report["pynq"]["base_ips"]["video"][0]
    button_ip = report["pynq"]["base_ips"]["buttons"][0]
    assert video_ip["instance"] == "axi_vdma"
    assert video_ip["full_name"] == "design_1/video/hdmi_out"
    assert video_ip["addresses"]["S_AXI_LITE"] == "0x43C00000"
    assert "vtc_irq" in video_ip["interrupt_ports"]
    assert "ip2intc_irpt" in button_ip["interrupt_ports"]
    assert report["schema"]


def test_collects_nested_droid_font_native_library_clock_driver_and_pl_state(tmp_path, monkeypatch):
    pynq_root = tmp_path / "usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq"
    font_data = b"font-bytes"
    library_data = b"native-video-library"
    extension_data = b"pynq-lib-video-extension"
    clock_data = b"ps-clock-driver"
    state_data = b'{"overlay": "rle", "fclk0_mhz": 31.25}\n'
    _write(tmp_path, "usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", font_data)
    _write(pynq_root, "lib/video/libvideo.so", library_data)
    _write(pynq_root, "lib/_video.cpython-310-arm-linux-gnueabihf.so", extension_data)
    _write(pynq_root, "ps.py", clock_data)
    _write(pynq_root, "pl_server/global_pl_state_.json", state_data)

    imported = []
    original_import = probe.importlib.import_module

    def track_import(name, *args, **kwargs):
        imported.append(name)
        if name == "pynq":
            raise AssertionError("PYNQ package code must not be imported")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(probe.importlib, "import_module", track_import)
    report = probe.collect(root=tmp_path, pynq_root=pynq_root)

    font = next(item for item in report["fonts"]["candidates"] if item["path"].endswith("DroidSansFallbackFull.ttf"))
    assert font["sha256"] == hashlib.sha256(font_data).hexdigest()
    assert report["pynq"]["clock_driver"]["sha256"] == hashlib.sha256(clock_data).hexdigest()
    native_hashes = {Path(item["path"]).name: item["sha256"] for item in report["pynq"]["native_libraries"]}
    assert native_hashes["libvideo.so"] == hashlib.sha256(library_data).hexdigest()
    assert native_hashes["_video.cpython-310-arm-linux-gnueabihf.so"] == hashlib.sha256(extension_data).hexdigest()
    state = report["pynq"]["global_pl_state"][0]
    assert state["content"] == state_data.decode().strip()
    assert state["sha256"] == hashlib.sha256(state_data).hexdigest()
    assert "pynq" not in imported


def test_missing_opencv_distribution_metadata_is_reported_without_stopping(tmp_path, monkeypatch):
    original_import = probe.importlib.import_module
    original_version = probe.importlib.metadata.version

    def import_without_cv2(name, *args, **kwargs):
        if name == "cv2":
            raise ModuleNotFoundError("cv2 unavailable")
        if name == "pynq":
            raise AssertionError("PYNQ must not be imported")
        return original_import(name, *args, **kwargs)

    def metadata_without_opencv(name):
        if name == "opencv-python":
            raise probe.importlib.metadata.PackageNotFoundError(name)
        if name == "opencv-python-headless":
            return "4.5.4"
        return original_version(name)

    monkeypatch.setattr(probe.importlib, "import_module", import_without_cv2)
    monkeypatch.setattr(probe.importlib.metadata, "version", metadata_without_opencv)
    report = probe.collect(root=tmp_path, pynq_root=tmp_path / "pynq")

    assert report["system"]["modules"]["cv2"]["distributions"]["opencv-python-headless"] == "4.5.4"
    assert report["system"]["modules"]["cv2"]["distributions"]["opencv-python"] == "unknown"
    assert any(error["path"] == "distribution:opencv-python" for error in report["errors"])
    assert report["status"] == "evidence_only"


def test_missing_module_version_is_recorded_in_errors(tmp_path, monkeypatch):
    def import_without_numpy(name, *args, **kwargs):
        if name == "numpy":
            raise ModuleNotFoundError("numpy module unavailable")
        return object()

    monkeypatch.setattr(probe.importlib, "import_module", import_without_numpy)
    monkeypatch.setattr(probe.importlib.metadata, "version", lambda _distribution: "1.0")
    errors = []

    versions = probe._module_versions(errors)

    assert versions["numpy"]["module_version"] == "unknown"
    assert versions["numpy"]["distributions"]["numpy"] == "1.0"
    assert any(error["path"] == "module:numpy" and error["kind"] == "import" for error in errors)


def test_cli_writes_json_only_to_a_new_explicit_output(tmp_path):
    output = tmp_path / "probe.json"
    assert probe.main(["--root", str(tmp_path), "--pynq-root", str(tmp_path / "pynq"), "--output", str(output)]) == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["schema"] == probe.SCHEMA

    output.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        probe.main(["--root", str(tmp_path), "--pynq-root", str(tmp_path / "pynq"), "--output", str(output)])
    assert output.read_text(encoding="utf-8") == "keep"

