"""Verify the curated delivery and historical source pins, entirely offline."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "data/results/submission-audit-20261015"


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check(path, expected, expected_bytes=None):
    file = ROOT / path.replace("\\", "/")
    require(file.is_file(), "Missing: " + str(path))
    raw = file.read_bytes()
    if expected_bytes is not None:
        require(len(raw) == expected_bytes,
                f"Byte count mismatch: {path} expected={expected_bytes} actual={len(raw)}")
    require(hashlib.sha256(raw).hexdigest() == expected,
            "SHA-256 mismatch: " + str(path))


def main():
    # Keep the recovered original WO-0014 source manifest bound into offline delivery checks.
    check(
        "scripts/wo0015/fixtures/wo0014-h3-source-manifest.json",
        "8d123469853ad2425e03d91590aa7473de4ca72a04fffaa6bc2d8964b377317d",
        192452,
    )
    batches = sorted(RESULT.glob("batch-*.json"))
    require(len(batches) == 5, "Expected all five delivery inventories")
    count = 0
    for manifest in batches:
        for record in json.loads(manifest.read_text(encoding="utf-8"))["files"]:
            check(record["path"], record["sha256"], record.get("bytes"))
            count += 1
    for name in ("morphology-lint-final-20261013", "morphology-synth-final-20261013"):
        for path, digest in load("data/results/" + name + "/source-hashes.json").items():
            if name.startswith("morphology-lint") and path.endswith(".sv"):
                continue  # Lint's older TB snapshot is not a lint input; synthesis manifest pins final TB.
            check(path, digest)
    ps = load("data/results/wo0010-reference-final-20261013/manager-verification.json")
    for path, digest in ps["source_hashes"].items():
        check(path, digest)
    timing = load("data/results/wo0014-timing-20261014/input-hashes.json")
    for path, digest in timing["sources"].items():
        check(path, digest)
    protected = load("data/results/wo0014-timing-20261014/delivery-summary.json")
    for path, digest in protected["protected_sha256"].items():
        if path != "README.md":  # Current status now links the delivery audit.
            check(path, digest)
    wo13 = load("data/results/wo0013-resetfix-20261014/export/manifest.json")
    for name, record in wo13["artifacts"].items():
        check("data/results/wo0013-resetfix-20261014/export/" + name, record["sha256"])
    pairs = {}
    for clock in (50, 100):
        directory = f"data/results/wo0014-hw-{clock}mhz-20261014/export/"
        pair = load(directory + "pair-manifest.json")
        for ext in ("bit", "hwh"):
            check(directory + "wo0014_image." + ext, pair[ext + "_sha256"])
        for name, digest in pair["rtl_sha256"].items():
            prefix = "fpga/rtl/" if name in ("axis_rgb2gray.v", "axis_gray2binary.v") else "fpga/experimental/wo0014/"
            check(prefix + name, digest)
        require(pair["dma_length_width"] == 18 and not pair["dma_s2mm"], "Unexpected DMA contract")
        t = pair["implemented_timing"]
        require(t["wns"] >= 0 and t["whs"] >= 0 and t["tns"] == t["ths"] == 0,
                "Historical implemented timing failed")
        pairs[str(clock)] = {"bit_sha256": pair["bit_sha256"], "timing": t}
    diag = load("scripts/wo0014_hw/diagnostic50/executor-manifest.json")
    for name, digest in diag["files"].items():
        check("scripts/wo0014_hw/diagnostic50/" + name, digest)
    closeout = load("data/results/wo0014-closeout-20261015/closeout-manifest.json")
    for name, digest in closeout["source_sha256"].items():
        prefix = "scripts/wo0014_hw/" if name.startswith("diagnostic50/") else "scripts/wo0014_hw/closeout/"
        check(prefix + name, digest)
    final = load("data/results/wo0014-closeout-20261015/final-summary.json")
    require(final["status"] == "FAIL_STOPPED" and not final["wo0014_board_closeout_verified"],
            "Historical CMA stop must remain visible")
    print(json.dumps({"status": "PASS", "inventory_files": count, "batches": 5,
                      "hardware_actions": 0, "historical_pairs": pairs,
                      "board_closeout": final["status"],
                      "scope": "Integrity and source correspondence; no new synthesis or board proof"}, indent=2))


if __name__ == "__main__":
    main()
