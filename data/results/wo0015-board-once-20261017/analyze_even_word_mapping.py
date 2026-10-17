from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def compare_words(left: tuple[int, ...], right: tuple[int, ...]) -> dict[str, int]:
    if len(left) != len(right):
        raise ValueError("word sequences have different lengths")
    byte_mismatches = sum(
        a != b
        for left_word, right_word in zip(left, right)
        for a, b in zip(left_word.to_bytes(4, "little"), right_word.to_bytes(4, "little"))
    )
    mismatched_words = sum(a != b for a, b in zip(left, right))
    return {
        "word_count": len(left),
        "word_matches": len(left) - mismatched_words,
        "word_mismatches": mismatched_words,
        "matching_bytes": len(left) * 4 - byte_mismatches,
        "byte_mismatches": byte_mismatches,
    }


def main() -> None:
    rx = (ROOT / "rx_raw.bin").read_bytes()
    golden = (ROOT / "expected_golden.bin").read_bytes()
    if len(rx) != 76800 or len(golden) != 76800 or len(rx) % 4:
        raise ValueError("expected two 76800-byte, word-aligned captures")
    rx_words = struct.unpack(f"<{len(rx) // 4}I", rx)
    golden_words = struct.unpack(f"<{len(golden) // 4}I", golden)
    even_rx = rx_words[0::2]
    result = {
        "source_files": {
            "rx_raw.bin": {"bytes": len(rx), "sha256": hashlib.sha256(rx).hexdigest()},
            "expected_golden.bin": {
                "bytes": len(golden), "sha256": hashlib.sha256(golden).hexdigest()
            },
        },
        "rx_word_count": len(rx_words),
        "golden_word_count": len(golden_words),
        "even_rx_word_count": len(even_rx),
        "even_rx_vs_golden_even_indices": compare_words(even_rx, golden_words[0::2]),
        "even_rx_vs_golden_first_9600_words": compare_words(even_rx, golden_words[:len(even_rx)]),
    }
    (ROOT / "offline-even-word-mapping.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
