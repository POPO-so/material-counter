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
| real FRG1 parameters + checkerboard mask | PASS, 130048 in-view pixels, 0 mismatches |
| config switch / near-VSYNC / mid-frame | PASS, 7 scenarios + old/new identical |

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

## 5b. Real FRG1 parameter regression

Instantiation matches `video_binary_overlay_bd.v:87`: `WIDTH=640 HEIGHT=400
VIEW_X=384 VIEW_Y=146 VIEW_WIDTH=512 VIEW_HEIGHT=320`. Both generate conditions
hold, and the TB asserts them before driving: `HEIGHT*4=1600 == VIEW_HEIGHT*5`
so `g_row_base_5_4` is the elaborated branch, and `WIDTH*4=2560 ==
VIEW_WIDTH*5` for `g_source_x_5_4`.

The mask is a checkerboard `bit(x,y)=(x+y)&1` written in raster order, so any
row-base step, phase wrap, or first/last-row error changes the parity. With no
boxes and no count line the output inside the view must be white iff the source
bit at `(lx+(lx>>2), ly+(ly>>2))` is 1. Three frames are driven; the mask
handshake completes at the second boundary, so frames 2 and 3 are both active.

```
FRG1: in_checked=130048 out_checked=380672 mismatch_in=0 bad_out=0 cmp_mm=0
FRG1 coverage: row_first=512 row_last=512 row_phase3=512 row_wrap=512 f1=0 f2=65024 f3=65024
C_FRG1_PASS
```

`130048 = 2 x 256 x 254` in-view pixels, zero mismatches; `row_phase3`
(local_y=3, phase 3) and `row_wrap` (local_y=4, base += 2*WIDTH) are both
covered in both active frames, and the second active frame proves the
next-frame row-base reset.

Reachability note: `VIEW_X+VIEW_WIDTH=896 > WIDTH=640`, so the physically
reachable window is x in [384,639] (256 columns), y in [146,399]. This is a
property of the frozen baseline, not a candidate change; it is recorded here so
the window geometry is not misread.

## 5c. Configuration switch regression

64x64, full viewport, all-zero mask; probes at the d1 tap.

| Frame | Config | Expectation | Result |
|---|---|---|---|
| 2 | A: box 10..20, line 30 | box edge + line white | PASS |
| 3 | B committed 5 pixel_clk before VSYNC: box 30..40, line 50 | B drawn, A's box and A's line gone | PASS |
| 4 | C: `cfg_frame_id=2`, displayed id=1 | box suppressed, line drawn | PASS |
| 5 | D: `count=0` | no box, line drawn | PASS |
| 6 | E: illegal box `x0>x1` | box invalidated, line drawn | PASS |
| 7 | F: `enable=0` | everything suppressed -> base gray | PASS |
| 8 | G committed mid-frame 7 | applies only at frame 8 boundary | PASS |

`cmp_mm=0` in every frame. Note that `cfg_ready` only returns after the
pixel-domain ack at a frame boundary, so a config commit cannot be issued
twice between boundaries; the TB synchronises on the publish toggle.

## 5d. Candidate vs frozen baseline (01d5563c) change list

Config domain (`cfg_clk`):

1. Added a six-state scaling FSM (`CFG_IDLE`, `CFG_LINE`, `CFG_BOX_READ`,
   `CFG_BOX_SCALE`, `CFG_BOX_WRITE`, `CFG_PUBLISH`).
2. `cfg_ready` additionally requires `cfg_state==CFG_IDLE`.
3. Box coordinates are scaled once at load (`cfg_coord_scaled`), replacing the
   per-pixel `(bx*VIEW_WIDTH)/WIDTH` and `(by*VIEW_HEIGHT)/HEIGHT` products.
4. The count line is scaled once at load (`cfg_work_line`), replacing the
   per-pixel `(line_p*VIEW_WIDTH)/WIDTH` product.
5. Per-box validity computed at load: `x0<=x1`, `y0<=y1`, `x1<WIDTH`,
   `y1<HEIGHT` -> `cfg_mb_box_valid`.
6. Line validity computed at load: `cfg_line_x<WIDTH` -> `cfg_mb_line_valid`.
7. Mailbox extended with `cfg_mb_box_valid` / `cfg_mb_line_valid`.
8. The pixel-domain boundary latch copies `box_valid_p` / `line_valid_p`.

Pixel domain:

9. Scalar `line_or_box` replaced by `box_hit[MAX_BOXES]` + `line_hit`.
10. Box comparisons use the pre-scaled local coordinates directly.
11. Added `row_base_addr` + `row_scale_phase`; `pixel_addr = row_base_addr +
    source_x` under `g_row_base_5_4`; the generic path keeps
    `source_y*WIDTH+source_x`.
12. Row base advances by `WIDTH` normally and by `2*WIDTH` on phase 3
    (1,1,1,2), reset at the frame boundary and at `y==VIEW_Y-1`.
13. Added one output pipeline stage: `hit_q`/`line_hit_q` -> `overlay_q`;
    `mask_q2`/`use_q2`/`base_q2`; `active_q2`/`vsync_q2`/`hsync_q2`. Output
    latency increases by one `pixel_clk`.
14. Output expression keeps the same form: white when
    `use_q2 && (mask_q2 || overlay_q)`, black when `use_q2` and neither, base
    otherwise.

Unchanged: mask banks and their CDC, the mask write/publish/ack handshake,
capture counters, the two-FF config mailbox CDC, and the x-direction
`g_source_x_5_4` precompute (already present in the frozen baseline).

## 5e. Pending scenarios (not executed)

- Independent reset of one domain while another is in use (requires coordinated reset).
- 16 simultaneous boxes and overlapping boxes.
- Mask re-publish while the current frame is displaying (bank swap mid-stream).
- Config commit exactly on the VSYNC edge (the executed test used 5 pixel_clk margin).
- Commit while the scaling FSM is busy (`cfg_ready=0` backpressure).
- Reset asserted during config scaling.

## 6. Reproduce

```sh
V=F:/AMDDesignTools/2026.1/Vivado/bin
cd C:/fpga/wt/claude-jpeg-20261102
$V/xvlog.bat --sv fpga/experimental/jpeg_integration/tb_video_binary_overlay_cmp.sv \
  fpga/experimental/standalone/video_binary_overlay.v \
  fpga/experimental/standalone/video_binary_overlay_frozen.v
$V/xelab.bat -debug typical tb_video_binary_overlay_cmp -s cmp_snap && $V/xsim.bat cmp_snap -R
# repeat with tb_video_binary_overlay_func / _small / _viewport / _frg1 / _cfgswitch
```

Machine-readable results: `tb_video_binary_overlay_results.json`.