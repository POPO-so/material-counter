# WO-0013 Phase A PYNQ MM2S replay

This is an isolated PS DDR → AXI DMA MM2S → `axis_dma_rx_monitor` experiment.
It does not use S2MM, return stream bytes to DDR, or connect the project's
production video pipeline. A successful run is board evidence only when the
script is actually run against the PYNQ-Z2 and the matching bitstream.

## Fixture contract

The default 76,816-byte packet exactly recreates
`fpga/sim/wo0013/tb_axis_dma_rx_monitor.sv`:

- 320 × 240 row-major Binary8 bytes, each `00` or `FF`.
- No header and no padding.
- A 16-byte test trailer containing TDATA words `10203040`, `50607080`,
  foreground-pixel count, and `12340000`, stored little-endian in DDR.
- 19,204 32-bit beats, all four bytes valid, final beat carries TLAST.
- Monitor checksum is the modulo-2³² sum of 32-bit TDATA words. This is an
  experiment check, not a collision-resistant CRC.

An optional `--payload` file is accepted only if it matches this exact fixture.

## Run from PYNQ Jupyter

Copy the script and matching `.bit`/`.hwh` files to the board. The default
expected HWH map is DMA `0x40400000`, monitor `0x43C10000`; the script resolves
`axi_dma_0` and `rx_monitor` from `Overlay.ip_dict` and fails if the HWH address
differs. The generated HWH names the module-ref instance `rx_monitor`; the
resolver also accepts interface-qualified path components if a PYNQ release
exposes the entry as `rx_monitor/s_axi`.
The checked HWH declares PS `FCLK_CLK0` and the connected IP clocks at 50 MHz,
DMA `S_AXI_LITE` width 32, and monitor `s_axi` width 32.
The board design uses a 64-bit PS HP0/memory-mapped DMA path through the
protocol converter; AXI-Stream `TDATA` remains 32 bits. The script requires
the allocated source address to be 8-byte aligned, and the 76,816-byte frame
length is divisible by eight. This assumes the official PYNQ-Z2 image's HP
port boot configuration, which enables HP data width 64; if using a custom
boot image or a PS configuration with HP0 width 32, verify the configured
interface widths against the overlay before running the test. See the
[PYNQ DMA hardware tutorial](https://discuss.pynq.io/t/tutorial-pynq-dma-part-1-hardware-design/3133).
If the reviewed build uses other addresses or cell names, pass the updated
expected values explicitly after checking its HWH:

```python
from dma_replay_verify import run_replay

result = run_replay(
    bit_path="/home/xilinx/jupyter_notebooks/wo0013_phase_a.bit",
    timeout_s=5.0,
    reset_timeout_s=2.0,
)
```

For this generated HWH, the monitor instance is `rx_monitor`, wrapping the
`axis_dma_rx_monitor_bd` module reference.

For notebook execution, keep the kernel running if recovery reports that the
MM2S reset could not be confirmed. The module retains the DMA-owned source
buffer in `_QUARANTINED_BUFFERS`; reset or power-cycle the board before ending
that kernel. Do not reuse that buffer or retry the transfer while ownership is
unknown.

## What the script checks

1. Downloads the bitstream through `Overlay`, which parses the adjacent HWH.
2. Confirms the requested DMA/monitor names and base addresses against HWH.
3. Allocates a contiguous PYNQ buffer, checks its physical address is 8-byte
   aligned and within the configured 32-bit DMA address range, fills it, then
   calls `sync_to_device()` (or `flush()` on older PYNQ versions).
4. Arms the receiver, starts direct-register MM2S, and bounds every wait by
   monotonic deadlines.
5. Requires both DMA idle and monitor done, then checks expected/received
   bytes, beats, first and last TDATA, one TLAST, checksum, status, and error.
6. On failure after MM2S starts, requests the AXI DMA soft reset and confirms
   reset plus halted status before releasing the source buffer. It then
   re-arms the receiver. If reset cannot be confirmed, it retains the buffer
   for the lifetime of the Jupyter kernel and asks for board reset.

The simple/direct-register MM2S channel has no separate actual-byte-count
register. The monitor's byte/beat counters provide the received length, while
DMA idle confirms completion of the programmed transfer. There is no PL data
readback path here; the first/last words and checksum are monitor observations.

## References

- PYNQ [Overlay](https://pynq.readthedocs.io/en/latest/pynq_libraries/overlay.html)
  documents `.bit` plus adjacent `.hwh`, download, and `ip_dict` metadata.
- PYNQ [Allocate](https://pynq.readthedocs.io/en/latest/pynq_libraries/allocate.html)
  documents contiguous buffers, `device_address`, `coherent`, `sync_to_device`,
  and `flush`.
- PYNQ [MMIO](https://pynq.readthedocs.io/en/latest/pynq_libraries/mmio.html)
  documents memory-mapped IP register access.
- AMD [PG021 direct-register mode](https://docs.amd.com/r/en-US/pg021_axi_dma/Direct-Register-Mode-Simple-DMA),
  [MM2S control](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_DMACR-MM2S-DMA-Control-Register-Offset-00h),
  and [MM2S status](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_DMASR-MM2S-DMA-Status-Register-Offset-04h)
  define the register start, reset, length, idle, and halted semantics.

No PYNQ-Z2 board transfer has been performed by preparing this script.
