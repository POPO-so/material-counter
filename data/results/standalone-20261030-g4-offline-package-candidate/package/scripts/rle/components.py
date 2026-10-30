"""Native run-length connected components for RLE_PACKET_V1 records."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import numpy as np


_ROOT = Path(__file__).resolve().parent
_SOURCE = _ROOT / "native" / "rle_components.cpp"
_DLL_DIRECTORY_HANDLES = []
_LIBRARIES = {}


class _NativeComponent(ctypes.Structure):
    _fields_ = [
        ("x_min", ctypes.c_uint32),
        ("y_min", ctypes.c_uint32),
        ("x_max", ctypes.c_uint32),
        ("y_max", ctypes.c_uint32),
        ("area", ctypes.c_uint32),
        ("sum_x", ctypes.c_uint64),
        ("sum_y", ctypes.c_uint64),
    ]


@dataclass(frozen=True)
class Component:
    x_min: int
    y_min: int
    x_max: int
    y_max: int
    area: int
    centroid_x: float
    centroid_y: float


@dataclass(frozen=True)
class RleAnalysis:
    components: tuple[Component, ...]
    labels: np.ndarray


def build_native(output: str | os.PathLike[str], compiler: str = "g++") -> Path:
    """Build the optional native library at an explicit output path."""
    target = Path(output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [compiler, "-std=c++17", "-O3", "-shared", str(_SOURCE), "-o", str(target)]
    if os.name != "nt":
        command.insert(4, "-fPIC")
    environment = os.environ.copy()
    # GCC itself creates temporary intermediates; keep them beside the explicitly
    # requested build output so callers can use a sandboxed temporary directory.
    environment.update(TMP=str(target.parent), TEMP=str(target.parent), TMPDIR=str(target.parent))
    completed = subprocess.run(command, check=False, capture_output=True, text=True,
                               env=environment)
    if completed.returncode:
        raise RuntimeError("native RLE build failed: " + completed.stderr.strip())
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError("native RLE compiler produced no library")
    return target


def _load_library(library: Any = None):
    if library is None:
        library = os.environ.get("RLE_NATIVE_LIB")
    if library is None:
        raise RuntimeError("native library required; pass library= or set RLE_NATIVE_LIB")
    if isinstance(library, (str, os.PathLike)):
        # Avoid repeated filesystem/symlink walks in the per-frame hot path.
        # A library is fixed for this process; builds use separate output paths.
        key = os.path.abspath(os.fspath(library))
        if key in _LIBRARIES:
            return _LIBRARIES[key]
        compiler = shutil.which("g++")
        if os.name == "nt" and compiler:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(Path(compiler).resolve().parent)))
        library = ctypes.CDLL(key)
    else:
        key = None
    function = library.rle_components_filtered
    function.argtypes = [
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(_NativeComponent), ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    function.restype = ctypes.c_int
    if key is not None:
        _LIBRARIES[key] = function
    return function


def _runs_array(runs: Any) -> np.ndarray:
    array = np.asarray(runs)
    if array.dtype != np.uint32 or array.ndim != 1:
        raise ValueError("runs must be a one-dimensional uint32 array")
    if not array.flags.c_contiguous:
        array = np.ascontiguousarray(array)
    if len(array) > 0xFFFFFFFF:
        raise ValueError("too many runs")
    return array


def analyze_runs(runs: Any, *, library: Any = None, min_area: int = 1) -> RleAnalysis:
    """Join 8-connected runs and return per-component geometry and run labels."""
    array = _runs_array(runs)
    if (isinstance(min_area,bool) or not isinstance(min_area,(int,np.integer))
            or not 1<=min_area<=0xffffffff):
        raise ValueError("min_area must be a positive integer")
    function = _load_library(library)
    count = len(array)
    labels = np.empty(count, dtype=np.uint32)
    output = (_NativeComponent * count)()
    run_pointer = array.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32))
    label_pointer = labels.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32))
    output_pointer = ctypes.cast(output, ctypes.POINTER(_NativeComponent))
    result = function(run_pointer, count, label_pointer, output_pointer, count, min_area)
    if result < 0:
        messages = {
            -1: "invalid native C ABI arguments",
            -2: "runs are malformed, unsorted, non-maximal, or outside encoding bounds",
            -3: "native component output capacity is insufficient",
            -4: "native connected-component allocation failed",
        }
        raise ValueError(messages.get(result, f"native connected-component error {result}"))
    components = tuple(
        Component(int(output[i].x_min), int(output[i].y_min), int(output[i].x_max),
                  int(output[i].y_max), int(output[i].area),
                  float(output[i].sum_x / output[i].area),
                  float(output[i].sum_y / output[i].area))
        for i in range(result)
    )
    labels.flags.writeable = False
    return RleAnalysis(components, labels)


def local_mask(runs: Any, labels: Any, component_index: int) -> np.ndarray:
    """Rebuild a single component's bbox as a Binary8 mask for split handling."""
    run_array = _runs_array(runs)
    label_array = np.asarray(labels)
    if label_array.dtype != np.uint32 or label_array.ndim != 1 or len(label_array) != len(run_array):
        raise ValueError("labels must be a uint32 vector matching runs")
    if (isinstance(component_index, bool) or not isinstance(component_index, (int, np.integer))
            or int(component_index) < 0):
        raise ValueError("component_index must be a nonnegative integer")
    selected = np.flatnonzero(label_array == int(component_index))
    if not len(selected):
        raise ValueError("component_index has no runs")
    x_min = min(int((int(run_array[i]) >> 10) & 0x3FF) for i in selected)
    x_max = max(int(run_array[i]) & 0x3FF for i in selected)
    y_min = min(int(run_array[i]) >> 20 for i in selected)
    y_max = max(int(run_array[i]) >> 20 for i in selected)
    mask = np.zeros((y_max - y_min + 1, x_max - x_min + 1), dtype=np.uint8)
    for index in selected:
        word = int(run_array[index])
        row = (word >> 20) - y_min
        begin = ((word >> 10) & 0x3FF) - x_min
        end = (word & 0x3FF) - x_min
        mask[row, begin:end + 1] = 255
    return mask
