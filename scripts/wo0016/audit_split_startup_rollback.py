"""Host-only fault injection of exported allocation functions; no CMA or DMA."""
import argparse
import ast
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


def function(path, name, namespace):
    tree = ast.parse(Path(path).read_bytes())
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
    code = ast.Module(body=[node], type_ignores=[])
    exec(compile(code, str(path), "exec"), namespace)
    return namespace[name]


def audit(package):
    runtime = Path(package) / "scripts/wo0016"

    class Buffer:
        freed = 0

        def freebuffer(self):
            self.freed += 1

    first = Buffer()
    calls = []

    def allocate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise MemoryError("injected second allocation failure")
        return first

    helper = function(runtime / "stream_counter.py", "allocate_ping_pong_buffers",
                      {"Callable": Callable, "Any": Any, "np": SimpleNamespace(uint8=int),
                       "TX_BYTES": 230432, "RX_BYTES": 76800})
    try:
        helper(allocate)
    except MemoryError:
        pass
    else:
        raise AssertionError("fault injection did not reach the allocation failure")

    second = Buffer()
    reads = iter((40000, 10000))
    api = SimpleNamespace(cma_free_kib=lambda: next(reads), allocate=lambda size: second,
                          free=lambda buffer: buffer.freebuffer())
    callback = function(runtime / "run_stream_counter.py", "allocate_with_watermark",
                        {"Any": Any, "api": api, "CMA_WATERMARK_KIB": 32768,
                         "np": SimpleNamespace(prod=math.prod),
                         "preflight": SimpleNamespace(PreflightError=RuntimeError)})
    try:
        callback(shape=(230432,), dtype=int)
    except RuntimeError as exc:
        assert "after" in str(exc)
    else:
        raise AssertionError("post-allocation watermark failure was not reached")
    return {"status": "FAIL" if first.freed != 1 or second.freed != 1 else "OFFLINE_PASS",
            "scope": "host fake buffers; source cleanup contract, not actual CMA telemetry",
            "hardware_actions": 0,
            "partial_allocation": {"allocator_calls": len(calls), "allocated_buffers": 1,
                                   "explicit_freebuffer_calls": first.freed},
            "post_allocation_watermark": {"before_kib_injected": 40000, "after_kib_injected": 10000,
                                          "new_buffer_explicit_free_calls": second.freed},
            "board_leak_proven": False,
            "required_gate": "Repair and review startup rollback before hardware execution; do not lower the watermark or retry"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    args = parser.parse_args()
    result = audit(args.package)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "OFFLINE_PASS" else 1)
