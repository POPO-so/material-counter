"""Keep the active camera/packet/build graph free of the retired conversion route."""
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/gray8"))
sys.path.insert(0, str(ROOT / "scripts/wo0016"))
sys.path.insert(0, str(ROOT / "scripts/wo0015"))


def test_default_runtime_protocol_is_gray8_without_triplicated_payload():
    import board_preflight
    import stream_counter
    import packet_encoder
    from packet_codec import validate_packet
    gray = (np.arange(320 * 240) % 256).astype(np.uint8).reshape(240, 320)
    output = np.empty(76832, dtype=np.uint8)
    packet_encoder.gray320x240_to_gray_packet_into(gray, output, frame_id=23)
    assert board_preflight.TX_BYTES == stream_counter.TX_BYTES == output.nbytes == 76832
    np.testing.assert_array_equal(validate_packet(output, frame_id=23), gray)


def test_retired_encoder_and_pl_modules_are_absent_from_active_graph():
    forbidden = ("gray320x240_to_rgb_packet", "axis_rgb2gray", "axis_dma_rgb_adapter")
    for folder in ("scripts/wo0015", "scripts/wo0016", "scripts/gray8", "fpga/experimental/gray8"):
        for path in (ROOT / folder).glob("*"):
            if path.suffix not in (".py", ".v", ".tcl") or path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8-sig")
            assert not any(token in text for token in forbidden), str(path)
    assert not (ROOT / "fpga/rtl/axis_rgb2gray.v").exists()
    assert not (ROOT / "fpga/experimental/wo0014/axis_dma_rgb_adapter.v").exists()
