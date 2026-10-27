"""Prepare a relocatable Gray8-only XSim source package, without hardware access."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = (
    "fpga/rtl/axis_gray2binary.v",
    "fpga/experimental/wo0014/axis_binary_output_checker.v",
    "fpga/experimental/wo0015/axis_s2mm_frame_packer.v",
    "fpga/experimental/gray8/axis_dma_gray_adapter.v",
    "fpga/experimental/gray8/axis_dma_gray_image_pipeline.v",
    "fpga/experimental/gray8/axis_dma_gray_s2mm_pipeline_bd.v",
    "fpga/sim/gray8/tb_axis_dma_gray_adapter.sv",
    "fpga/sim/gray8/tb_axis_dma_gray_wrapper.sv",
    "fpga/sim/wo0015/tb_axis_s2mm_guards.sv",
    "scripts/gray8/run_sim.tcl",
    "scripts/gray8/README.md",
)


def prepare(output):
    output = Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Replay output must be missing or empty")
    if output == ROOT or output in ROOT.parents:
        raise ValueError("Replay output cannot contain the checkout")
    hashes = {}
    for name in SOURCE_FILES:
        path = ROOT / name
        if not path.is_file():
            raise FileNotFoundError("Missing Gray8 source: " + name)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    for name in SOURCE_FILES:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    manifest = dict(protocol="GRAY8_PACKET_V1", scope="OFFLINE_XSIM_SOURCE_PACKAGE",
                    source_sha256=hashes, hardware_actions=0)
    (output / "replay-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.out), indent=2))
