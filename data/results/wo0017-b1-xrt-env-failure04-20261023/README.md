# B1-04 XRT environment failure: zero frames, attempt consumed

The operator's B1-04 click passed the corrected gate-session validation. The runner consumed `b1-empty-04`, began its runtime initialization, imported PYNQ and then failed with `RuntimeError: No Devices Found`. The durable result reports `BOARD_STREAM_FAILED`, `frames_processed=0`, `xilinx_xrt=null`, `buffer_layout=null`, `overlay_ready_monotonic=null` and `camera_open_monotonic=null`. No capture directory exists. The attempt's `consumed.json` exists and must be retained.

## Verified cause

The launch process did not receive `XILINX_XRT`. The board's installed PYNQ `pl_server/__init__.py`, lines 17-20, registers `EmbeddedDevice` only when that environment variable is present. Its `Device.active_device` raises `No Devices Found` if the device registry is empty. The installed `/etc/profile.d/xrt_setup.sh` exports `XILINX_XRT=/usr`; the successful Stage 1 root-02 script also explicitly requires that exact value. These installed files were read without importing PYNQ or probing a device again.

The B1 launch helper used a fresh passwordless-sudo child environment without explicitly carrying over that Stage 1 setting. This is a launch-environment preparation omission. It is distinct from the previously corrected `__main__` identity defect. High CMA alone did not validate this environment dependency.

At failure, PYNQ import had occurred and the API marked `board_accessed=true` on entering MMIO initialization; this does not establish a successful MMIO mapping or DMA transaction. There is no completed Overlay construction, DMA buffer layout, camera-open milestone or frame sample. The runner's outer `BOARD_STREAM_REJECTED` terminal wrapper prints fixed `pynq_imported=false`/`board_accessed=false` flags even after the inner runtime path fails; those outer flags conflict with `b1-result.json` and must not be used to deny the actual import/initialization attempt. The full inner evidence is preserved.

## Offline prevention added

`require_formal_xrt_environment` now requires `XILINX_XRT=/usr` for WO-0017. Both preflight-standby admission and actual execution call it after opt-in validation and before reading the runtime manifest or claiming a sample Permit. Thus a missing environment is rejected before the webpage can become READY and is checked again before Permit consumption. The function only reads the process environment; it neither imports PYNQ nor changes global settings. Legacy WO-0016 calls without a WO-0017 profile retain their prior behavior.

Two new regressions first failed and then passed: missing environment before standby admission, and missing environment on actual execution admission. Both verify that the manifest is not read and no consumed marker/capture directory is created. Fresh focused regression: **145 passed, 3 skipped**, in `pytest-gates.xml`; the skips concern POSIX UID/mode tests unavailable on Windows.

Any separately authorized future deployment must explicitly set `XILINX_XRT=/usr` in both the preparation process and the standby/runner child process before PYNQ's first import, for example through a per-command `sudo -n env XILINX_XRT=/usr ...` invocation or an explicit child environment dictionary. Setting an environment variable alone does not import PYNQ or open a hardware context. This operation did not apply that change to the board or re-test device enumeration. Old deployment scripts and bound runtime hashes were not modified in place.

## Read-only closeout

The follow-up snapshot records the unchanged Boot `35983d66-bb1d-4a8a-85e5-802170825515`, CmaFree **100544 KiB**, both DMASR registers `0x00000001`, no camera/FPGA device owner, no remaining standby process and no capture directory. No retry, Permit reset, cache recovery, Overlay download, camera operation or new DMA action was performed during diagnosis. The `-04` deployment and all earlier records remain untouched. The old server exited; its passcode is no longer usable.

Evidence includes the original `b1-result.json`, `consumed.json`, exit/milestone/operator records, installed PYNQ registration source and XRT profile text, a private-passcode-redacted terminal log and the follow-up state snapshot. This is a failed software/runtime admission with zero sampled frames, not a DMA hardware test or a completed B1 acceptance run.
