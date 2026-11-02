# C candidate — overlay verification report

Date: 2026-11-02
Worktree: `C:/fpga/wt/claude-jpeg-20261102`, branch `codex/claude-jpeg-20261102`
Scope: XSim only. No routing, board access, deployment, push, or merge.

## 1. Status

| Item | Result |
|---|---|
| old/new delay alignment (640x400) | PASS, mismatches = 0 |
| functional overlay (640x400) | PASS, white = 414, cmp_mismatch = 0 |
| small boundary (64x64, full view) | PASS, all four box edges + line + interior + window edge |
| viewport sub-rectangle (64x64, view 32x32) | PASS, uncovered passthrough exact |

The previous claim that "the RTL is verified correct and the TB is the only blocker"
is withdrawn. What is now proven is narrower and reproducible: the current
candidate output equals the frozen baseline delayed by exactly one `pixel_clk`,
and both reproduce the configured geometry exactly under an independent
coordinate model. The earlier small-TB failure was traced to the TB's coordinate
label, not to the RTL, and that label has been replaced.

## 2. Localisation of the small-TB failure

The earlier gate sampled the output with a coordinate tracker anchored on the
output sync edges (`rx`,`ry`) and reported black at `rx=10` / `rx=30`. A
white-pixel dump using the TB-driven source scan changed the picture:

```
W d0=(32,0) d1=(31,0) d2=(30,0) ...      <- first row, startup skew
W d0=(31,1) d1=(30,1) d2=(29,1) ...      <- count line, every row
W d0=(11,10) d1=(10,10) ...              <- box left edge
W d0=(21,10) d1=(20,10) ...              <- box right edge
W d0=(15,10) ...                         <- box top/bottom edge row
```

`any_white = 104` = 64 line pixels + 40 box-perimeter pixels for one displayed
frame. Every rendered pixel matches the published config (box 10..20, line 30)
at tap `d1`; tap `d0` is one pixel early and the `rx/ry` tracker was one pixel
early plus a first-row skew. The label, not the sample, was wrong.

Sampling edges are now explicit:

* stimulus: `xpos/ypos` driven by the TB frame scan;
* label: `d1` = one `pixel_clk` behind the driven coordinate;
* check: `old_active && old_dv` in the same `posedge pixel_clk` block;
* comparison: `new_data` vs `old_data` delayed one `pixel_clk`.

## 3. Independent reference model

Coordinates come only from the TB's own stimulus scan (`xpos/ypos`) and are
calibrated by the requirement that rendered pixels reproduce the published cfg
coordinates exactly. No DUT internal signal is read as a reference. The small TB
asserts:

* box left `(10,15)`, right `(20,15)`, top `(15,10)`, bottom `(15,20)` white;
* count line `(30,15)` and `(30,40)` white;
* interior `(15,15)` black;
* window edges `(0,30)` and `(63,30)` black (in view);
* old/new identical (`cmp_mm = 0`).

## 4. Uncovered-region encoding

The viewport TB uses `VIEW_X=VIEW_Y=8`, `VIEW_WIDTH=VIEW_HEIGHT=32`, an all-zero
mask, no boxes, and a disabled count line. Result:

```
VIEWPORT: in_black=1024 out_gray=3072 edge_in=2 edge_out=4 bad=0 cmp_mm=0
```

`1024 = 32x32` and `3072 = 64x64 - 1024`: every sampled pixel is exactly black
inside the view or exactly the base RGB `0x404040` outside it. The unique base
encoding is used only in uncovered regions, as required.

## 5. Provenance

| Role | File | sha256 | Notes |
|---|---|---|---|
| Old reference / frozen baseline | `C:/w/jj2026j/snapshot/.../rtl/video_binary_overlay.v` | `01d5563c7be95e92...` | **already contains** the x-direction 5:4 precomputation `g_source_x_5_4` (`local_x + (local_x>>2)`) |
| Frozen BD (FRG1) | `.../rtl/video_binary_overlay_bd.v` | `dfc487227d9ad9ea...` | returns FRG1 `0x46524731` |
| Current candidate | `fpga/experimental/standalone/video_binary_overlay.v` | `811db98140f55686...` | commit `72f77cce`; adds the y-direction `g_row_base_5_4` row-base precompute and one output pipeline stage |
| Frozen test copy | `fpga/experimental/standalone/video_binary_overlay_frozen.v` | `a7a01fa794c3d90e...` | body of `01d5563c`, module renamed, duplicate `binary_mask_ram` removed |
| Retired | `.../video_binary_overlay_old_renamed.v` | `b96ee30915220dd8...` | an ad-hoc copy lacking the CFG scale state machine; **not** the frozen baseline; removed in `ae7cbe4f` |

The frozen baseline already includes the x-direction precomputation; the
candidate does not re-introduce it. The candidate's delta is the row-base
precompute plus the hit-vector pipeline and `q2` alignment.

## 6. Reproduce

```sh
V=F:/AMDDesignTools/2026.1/Vivado/bin
cd C:/fpga/wt/claude-jpeg-20261102
$V/xvlog.bat --sv fpga/experimental/jpeg_integration/tb_video_binary_overlay_cmp.sv \
  fpga/experimental/standalone/video_binary_overlay.v \
  fpga/experimental/standalone/video_binary_overlay_frozen.v
$V/xelab.bat -debug typical tb_video_binary_overlay_cmp -s cmp_snap && $V/xsim.bat cmp_snap -R
# repeat with tb_video_binary_overlay_func / _small / _viewport
```

Machine-readable results: `tb_video_binary_overlay_results.json`.