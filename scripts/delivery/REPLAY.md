# WO-0014 offline replay package

`prepare_replay.py` builds a fresh, self-contained input set for the existing WO-0014 simulation chain. It runs the current checkout's fixed `h3_matrix.json` generator into the requested output directory, then passes those 487 Gray8 frames to the checkout's `image_vectors.generate`. It also writes relocated copies of `run_full.tcl`, `run_directed.tcl`, and `run_negative.tcl`; the tracked historical Tcl files are unchanged.

The destination must be missing or empty. No partial-frame mode is offered: the main testbench and Tcl retain their 487-frame acceptance checks. All output is contained in the destination:

```text
<out>/h3-source/data/synthetic/h3_full/   regenerated H3 frames/config/ground truth
<out>/replay/vectors/                    input/golden vectors and vector manifest
<out>/replay/run_full.tcl                 complete offline XSim chain
<out>/replay/run_directed.tcl             sourced child replay
<out>/replay/run_negative.tcl             sourced child replay
<out>/replay/source_sha256.json            checkout sources and generated-file hashes
```

Prepare from the repository root. If an earlier 487-frame dataset is available, provide it to record an exact frame-key SHA comparison; this is read-only.

```powershell
python scripts/delivery/prepare_replay.py --out C:\fpga\delivery\wo0014-replay
# Optional evidence cross-check:
python scripts/delivery/prepare_replay.py --out C:\fpga\delivery\wo0014-replay-reference --reference-h3-root C:\fpga\material-counter\data\synthetic\h3_full
```

Use one command with one fresh output directory. `source_sha256.json` records the checkout commit when available, hashes of the RTL/testbench/generator/spec/original Tcl/comparator/preparation files, all generated dataset/vector/Tcl files, and each rebuilt PNG keyed relative to `h3_full`. When `--reference-h3-root` is supplied it also records both hashes and the match result for each relative condition/frame key. A SHA mismatch returns exit code 2 and is not a passing reference comparison.

Run the full chain with the same checkout used for preparation. Set `MATERIAL_COUNTER_ROOT` to that checkout; the generated Tcl uses it for all RTL/testbench source paths and writes simulation artifacts only below `<out>\replay`.

```powershell
$env:MATERIAL_COUNTER_ROOT = (Resolve-Path .).Path
$out = (Resolve-Path 'C:\fpga\delivery\wo0014-replay').Path
& "$env:VIVADO_BIN\vivado.bat" -mode batch -source "$out\replay\run_full.tcl"
```

`VIVADO_BIN` must name the installed Vivado `bin` directory. The Tcl runs the full 487-frame H3 vector replay, the reviewed directed/reset case, all seven negative/recovery cases, partial-keep cases, and threshold checks. It retains the historical `RESULT PASS` and fatal-error guards; this preparation step does not weaken or itself rerun those XSim checks.

After Vivado completes, independently compare the full output against the generated Python golden stream and source frames. `compare_output.py` imports its H3 root from `image_vectors`; inject the packaged root before executing it so verification remains self-contained even when the checkout does not contain PNGs:

```powershell
$repo = (Resolve-Path $env:MATERIAL_COUNTER_ROOT).Path
$out = (Resolve-Path 'C:\fpga\delivery\wo0014-replay').Path
$compare = Join-Path $repo 'python\refmodel\experiments\wo0014\compare_output.py'
$h3 = Join-Path $out 'h3-source\data\synthetic\h3_full'
@"
import runpy, sys
from pathlib import Path
sys.path.insert(0, r"$repo\python\refmodel\experiments\wo0014")
import image_vectors
image_vectors.ROOT = Path(r"$out\h3-source")
image_vectors.H3_ROOT = Path(r"$h3")
script = r"$compare"
sys.argv = [script, "--actual", r"$out\replay\h3-final-actual.bin", "--expected", r"$out\replay\vectors\h3_expected_th128.bin", "--manifest", r"$out\replay\vectors\manifest.json", "--kind", "h3", "--report", r"$out\replay\h3-compare.json"]
runpy.run_path(script, run_name="__main__")
"@ | python -
```

The full comparison requires 487 frames, exact output length, zero word mismatches, valid SOF/EOL and Binary8 values, matching generated frame hashes, and recomputation of every expected frame. This is offline RTL simulation evidence only; it does not establish bitstream timing, board communication, or material-counting accuracy on real objects. No PYNQ, overlay, MMIO, DMA, or motor operation is part of this package.
