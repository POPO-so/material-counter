"""Artifact hashes and HWH admission for the independent Gray8 overlay."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts/wo0015"))
import board_preflight

CONTRACT = dict(protocol="GRAY8_PACKET_V1", checker_signature=0x47525938,
                packer_signature=0x574F3135, width=320, height=240,
                tx_bytes=76832, rx_bytes=76800, threshold=128,
                clock_mhz=50, part="xc7z020clg400-1",
                dma_base=0x40400000, checker_base=0x43C20000)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_gray_hwh(path):
    try:
        board_preflight.validate_hwh(path)
    except board_preflight.PreflightError as exc:
        raise ValueError(str(exc)) from exc
    root = ET.parse(path).getroot()
    nodes = [n for n in root.iter("MODULE") if n.get("INSTANCE") == "image_pipeline"]
    if len(nodes) != 1 or nodes[0].get("MODTYPE") != "axis_dma_gray_s2mm_pipeline_bd":
        raise ValueError("HWH must contain the Gray8-only pipeline module")
    parameters = {p.get("NAME"): p.get("VALUE") for p in nodes[0].findall("./PARAMETERS/PARAMETER")}
    for name, expected in (("WIDTH", 320), ("HEIGHT", 240), ("THRESHOLD", 128)):
        if int(str(parameters.get(name, "-1")), 0) != expected:
            raise ValueError("Gray8 pipeline parameter mismatch: " + name)


def build_manifest(output, repo=REPO):
    output, repo = Path(output), Path(repo)
    export = output / "export"
    validate_gray_hwh(export / "gray8.hwh")
    bit, hwh = export / "gray8.bit", export / "gray8.hwh"
    if not bit.is_file() or not bit.stat().st_size:
        raise ValueError("Missing bitstream")
    snapshots = {str(p.relative_to(output)).replace("\\", "/"): sha256(p)
                 for p in sorted((output / "rtl").glob("*.v"))}
    return dict(schema=1, **CONTRACT, bit_path="gray8.bit", hwh_path="gray8.hwh",
                bit_sha256=sha256(bit), hwh_sha256=sha256(hwh),
                rtl_snapshot_sha256=snapshots, build_evidence="ROUTED_NOT_BOARD_VERIFIED")


def load_manifest(path, expected_sha256=None):
    path = Path(path).resolve()
    if expected_sha256 is not None and sha256(path) != expected_sha256:
        raise ValueError("Manifest SHA-256 differs from requested digest")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or any(manifest.get(k) != v for k, v in CONTRACT.items()):
        raise ValueError("Gray8 manifest contract mismatch")
    for kind in ("bit", "hwh"):
        name = manifest.get(kind + "_path")
        if name != "gray8." + kind:
            raise ValueError("Artifact filename must match the Gray8 pair")
        artifact = path.parent / name
        if sha256(artifact) != manifest.get(kind + "_sha256"):
            raise ValueError(kind + " SHA-256 mismatch")
    validate_gray_hwh(path.parent / manifest["hwh_path"])
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=REPO)
    args = parser.parse_args()
    manifest = build_manifest(args.out, args.repo)
    path = args.out / "export/manifest.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
    print("GRAY8_ARTIFACTS_VALID manifest=%s sha256=%s" % (path, sha256(path)))


if __name__ == "__main__":
    main()
