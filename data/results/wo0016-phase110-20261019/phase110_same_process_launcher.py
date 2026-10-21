"""Phase 1.10 launcher: time native PYNQ setup without a probe subprocess."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import run_stream_counter as runner


_OriginalAPI = runner.board_once.PynqBoardAPI
TIMING: dict[str, float] = {}
FRAME_DMASR: list[dict[str, int | str]] = []


class TimedPynqBoardAPI(_OriginalAPI):
    def __init__(self, *args, **kwargs):
        started = time.perf_counter()
        super().__init__(*args, **kwargs)
        TIMING["pynq_api_init_seconds"] = time.perf_counter() - started
        self._phase110_overlay_wrapped = False

    def audit_environment(self, *args, **kwargs):
        started = time.perf_counter()
        result = super().audit_environment(*args, **kwargs)
        TIMING["pynq_environment_init_seconds"] = time.perf_counter() - started
        if not self._phase110_overlay_wrapped:
            original_overlay = self._pynq.Overlay

            def timed_overlay(*overlay_args, **overlay_kwargs):
                overlay_started = time.perf_counter()
                overlay = original_overlay(*overlay_args, **overlay_kwargs)
                TIMING["overlay_download_false_init_seconds"] = (
                    time.perf_counter() - overlay_started
                )
                return overlay

            self._pynq.Overlay = timed_overlay
            self._phase110_overlay_wrapped = True
        return result

def _argument_value(arguments: list[str], *names: str) -> str | None:
    for index, value in enumerate(arguments):
        for name in names:
            if value == name and index + 1 < len(arguments):
                return arguments[index + 1]
            prefix = f"{name}="
            if value.startswith(prefix):
                return value[len(prefix):]
    return None


_OriginalStream = runner.DoubleBufferStream


class ObservedDoubleBufferStream(_OriginalStream):
    def __init__(self, *args, **kwargs):
        original_after_transfer = kwargs.get("after_transfer")
        if original_after_transfer is None:
            raise RuntimeError("Phase 1.10 requires a per-frame verification callback")
        import pynq

        dma_mmio = pynq.MMIO(0x40400000, 0x10000)

        def verify_and_read_dmasr(frame_id, slot):
            original_after_transfer(frame_id, slot)
            mm2s = int(dma_mmio.read(0x04))
            s2mm = int(dma_mmio.read(0x34))
            FRAME_DMASR.append({
                "frame_id": int(frame_id),
                "mm2s": f"0x{mm2s:08x}",
                "s2mm": f"0x{s2mm:08x}",
            })
            if mm2s != 0x1002 or s2mm != 0x1002:
                raise RuntimeError(
                    f"frame {frame_id} DMA status mismatch: "
                    f"MM2S=0x{mm2s:08x}, S2MM=0x{s2mm:08x}"
                )

        kwargs["after_transfer"] = verify_and_read_dmasr
        super().__init__(*args, **kwargs)


def main() -> int:
    runner.board_once.PynqBoardAPI = TimedPynqBoardAPI
    runner.DoubleBufferStream = ObservedDoubleBufferStream
    started = time.perf_counter()
    result = runner.main(sys.argv[1:])
    TIMING["runner_wall_seconds"] = time.perf_counter() - started
    evidence_name = _argument_value(sys.argv[1:], "--evidence-json", "--output-json")
    if evidence_name:
        evidence_path = Path(evidence_name)
        if evidence_path.is_file():
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["phase110_same_process_timing"] = {
                key: round(value, 6) for key, value in TIMING.items()
            }
            evidence["phase110_frame_dmasr"] = FRAME_DMASR
            temporary = evidence_path.with_suffix(evidence_path.suffix + ".timing.tmp")
            temporary.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(evidence_path)
    print(json.dumps({"phase110_same_process_timing": TIMING}, sort_keys=True))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
