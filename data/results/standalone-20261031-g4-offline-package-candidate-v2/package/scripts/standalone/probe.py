"""Read-only collection of evidence for the standalone-display G0 gate."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import stat
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


SCHEMA = "material-counter.g0-probe.v1"
_PYNQ_SOURCES = (
    "lib/video/common.py",
    "lib/video/dma.py",
    "lib/video/frontend.py",
    "lib/video/dvi.py",
    "lib/video/pipeline.py",
    "lib/video/hierarchies.py",
    "buffer.py",
)
_MODULE_DISTRIBUTIONS = {
    "numpy": ("numpy",),
    "cv2": ("opencv-python", "opencv-python-headless"),
    "PIL": ("Pillow",),
    "pynq": ("pynq",),
}
_FONT_SUFFIXES = {".ttf", ".ttc", ".otf", ".otc"}


def _relative(path: Path, root: Path) -> str:
    try:
        return "/" + path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _read_text(path: Path, errors: list[dict], *, required: bool = True) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except FileNotFoundError:
        if required:
            errors.append({"path": str(path), "kind": "missing", "message": "not found"})
    except PermissionError as exc:
        errors.append({"path": str(path), "kind": "permission", "message": str(exc)})
    except OSError as exc:
        errors.append({"path": str(path), "kind": "read", "message": str(exc)})
    return None


def _hash_file(path: Path, errors: list[dict]) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except FileNotFoundError:
        errors.append({"path": str(path), "kind": "missing", "message": "not found"})
    except PermissionError as exc:
        errors.append({"path": str(path), "kind": "permission", "message": str(exc)})
    except OSError as exc:
        errors.append({"path": str(path), "kind": "read", "message": str(exc)})
    return None


def _evidence_status(value) -> str:
    return "ok" if value is not None else "unknown"


def _parse_number(text: str | None):
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _module_versions(errors: list[dict]) -> dict:
    result = {}
    for module_name, distributions in _MODULE_DISTRIBUTIONS.items():
        module_version = None
        module_error = None
        if module_name == "pynq":
            module_error = "not imported by read-only collector"
        else:
            try:
                module = importlib.import_module(module_name)
                module_version = getattr(module, "__version__", None)
            except Exception as exc:  # Optional modules must not stop collection.
                module_error = f"{type(exc).__name__}: {exc}"
                errors.append({
                    "path": f"module:{module_name}",
                    "kind": "import",
                    "message": module_error,
                })

        distribution_versions = {}
        for distribution in distributions:
            try:
                distribution_versions[distribution] = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError as exc:
                distribution_versions[distribution] = "unknown"
                errors.append({
                    "path": f"distribution:{distribution}",
                    "kind": "missing",
                    "message": str(exc) or "distribution metadata not found",
                })
            except Exception as exc:
                distribution_versions[distribution] = "unknown"
                errors.append({
                    "path": f"distribution:{distribution}",
                    "kind": "metadata",
                    "message": f"{type(exc).__name__}: {exc}",
                })
        result[module_name] = {
            "module_version": module_version if module_version is not None else "unknown",
            "module_error": module_error,
            "distributions": distribution_versions,
        }
    return result


def _parse_meminfo(text: str | None) -> dict:
    values = {}
    if text is not None:
        for line in text.splitlines():
            match = re.match(r"^([^:]+):\s*(.*?)\s*$", line)
            if match:
                values[match.group(1)] = match.group(2)
    cma_values = {key: value for key, value in values.items() if key.lower().startswith("cma")}
    cma_keys = {key.lower() for key in cma_values}
    cma_complete = {"cmatotal", "cmafree"}.issubset(cma_keys)
    cma_status = "ok" if cma_complete else ("partial" if cma_values else "unknown")
    return {
        "status": "ok" if cma_complete and values else ("partial" if values else "unknown"),
        "values": values,
        "cma": {"status": cma_status, "values": cma_values},
    }


def _collect_iio(root: Path, errors: list[dict]) -> dict:
    base = root / "sys/bus/iio/devices"
    devices = []
    channels = []
    try:
        entries = sorted(p for p in base.iterdir() if p.is_dir() and (p.name.startswith("iio:") or p.name.startswith("iio-")))
    except FileNotFoundError:
        errors.append({"path": str(base), "kind": "missing", "message": "IIO sysfs directory not found"})
        entries = []
    except PermissionError as exc:
        errors.append({"path": str(base), "kind": "permission", "message": str(exc)})
        entries = []
    except OSError as exc:
        errors.append({"path": str(base), "kind": "read", "message": str(exc)})
        entries = []

    for device in entries:
        name = _read_text(device / "name", errors, required=False)
        devices.append({"name": name or "unknown", "path": str(device)})
        try:
            attrs = sorted(device.glob("in_*_raw"))
        except OSError as exc:
            errors.append({"path": str(device), "kind": "read", "message": str(exc)})
            attrs = []
        for raw_path in attrs:
            stem = raw_path.name[:-4]
            prefix = stem.removesuffix("_raw")
            raw_text = _read_text(raw_path, errors)
            scale_text = _read_text(device / f"{prefix}_scale", errors, required=False)
            offset_text = _read_text(device / f"{prefix}_offset", errors, required=False)
            label = _read_text(device / f"{prefix}_label", errors, required=False)
            try:
                raw = int(raw_text) if raw_text is not None else None
            except ValueError:
                raw = None
                errors.append({"path": str(raw_path), "kind": "parse", "message": "raw value is not an integer"})
            scale = _parse_number(scale_text)
            offset = _parse_number(offset_text) if offset_text is not None else 0.0
            if scale_text is not None and scale is None:
                errors.append({"path": str(device / f"{prefix}_scale"), "kind": "parse", "message": "scale is not numeric"})
            if offset_text is not None and offset is None:
                errors.append({"path": str(device / f"{prefix}_offset"), "kind": "parse", "message": "offset is not numeric"})
            value = (raw + offset) * scale if raw is not None and scale is not None and offset is not None else None
            if prefix.startswith("in_temp"):
                unit = "m°C"
                value_si = value / 1000.0 if value is not None else None
            elif prefix.startswith("in_voltage"):
                unit = "mV"
                value_si = value / 1000.0 if value is not None else None
            else:
                unit = "unknown"
                value_si = None
            channels.append({
                "device": name or "unknown",
                "channel": prefix,
                "label": label or "unknown",
                "raw": raw if raw is not None else "unknown",
                "scale": scale if scale is not None else "unknown",
                "offset": offset if offset is not None else "unknown",
                "value": value if value is not None else "unknown",
                "unit": unit,
                "value_si": value_si if value_si is not None else "unknown",
                "sampled_monotonic": time.monotonic(),
            })
    return {"status": "ok" if channels else "unknown", "devices": devices, "channels": channels}


def _permissions(path: Path, errors: list[dict]) -> dict:
    try:
        info = path.stat()
    except FileNotFoundError:
        return {"status": "unknown", "exists": False, "mode": "unknown", "readable": "unknown", "writable": "unknown"}
    except PermissionError as exc:
        errors.append({"path": str(path), "kind": "permission", "message": str(exc)})
        return {"status": "unknown", "exists": "unknown", "mode": "unknown", "readable": "unknown", "writable": "unknown"}
    except OSError as exc:
        errors.append({"path": str(path), "kind": "read", "message": str(exc)})
        return {"status": "unknown", "exists": "unknown", "mode": "unknown", "readable": "unknown", "writable": "unknown"}
    return {
        "status": "ok",
        "exists": True,
        "mode": format(stat.S_IMODE(info.st_mode), "04o"),
        "readable": os.access(path, os.R_OK),
        "writable": os.access(path, os.W_OK),
    }


def _collect_devices(root: Path, errors: list[dict]) -> dict:
    dev = root / "dev"
    uio = []
    try:
        paths = sorted(dev.glob("uio*"))
    except OSError as exc:
        errors.append({"path": str(dev), "kind": "read", "message": str(exc)})
        paths = []
    for path in paths:
        if not path.name.startswith("uio"):
            continue
        number = path.name[3:]
        driver_link = root / f"sys/class/uio/uio{number}/device/driver"
        try:
            driver = driver_link.resolve(strict=True).name
        except FileNotFoundError:
            driver = "unknown"
            errors.append({"path": str(driver_link), "kind": "missing", "message": "UIO driver binding not found"})
        except PermissionError as exc:
            driver = "unknown"
            errors.append({"path": str(driver_link), "kind": "permission", "message": str(exc)})
        except OSError as exc:
            driver = "unknown"
            errors.append({"path": str(driver_link), "kind": "read", "message": str(exc)})
        uio.append({"path": _relative(path, root), "permissions": _permissions(path, errors), "driver": driver})
    if not uio:
        errors.append({"path": str(dev / "uio*"), "kind": "missing", "message": "no UIO devices found"})
    mem_path = dev / "mem"
    mem_permissions = _permissions(mem_path, errors)
    if not mem_path.exists():
        errors.append({"path": str(mem_path), "kind": "missing", "message": "device not found"})
    return {"uio_status": "ok" if uio else "unknown", "uio": uio, "dev_mem": {"path": "/dev/mem", "permissions": mem_permissions}}


def _collect_camera(root: Path, errors: list[dict]) -> dict:
    base = root / "dev/v4l/by-id"
    cameras = []
    try:
        entries = sorted(base.iterdir())
    except FileNotFoundError:
        errors.append({"path": str(base), "kind": "missing", "message": "stable camera by-id directory not found"})
        entries = []
    except PermissionError as exc:
        errors.append({"path": str(base), "kind": "permission", "message": str(exc)})
        entries = []
    except OSError as exc:
        errors.append({"path": str(base), "kind": "read", "message": str(exc)})
        entries = []
    for path in entries:
        try:
            resolved = "/" + path.resolve(strict=True).relative_to(root).as_posix()
        except FileNotFoundError:
            resolved = "unknown"
            errors.append({"path": str(path), "kind": "missing-target", "message": "by-id target is missing"})
        except PermissionError as exc:
            resolved = "unknown"
            errors.append({"path": str(path), "kind": "permission", "message": str(exc)})
        except (OSError, ValueError) as exc:
            resolved = "unknown"
            errors.append({"path": str(path), "kind": "resolve", "message": str(exc)})
        cameras.append({"path": _relative(path, root), "resolved": resolved})
    if not cameras:
        status = "unknown"
    elif any(camera["resolved"] == "unknown" for camera in cameras):
        status = "partial"
    else:
        status = "ok"
    return {"status": status, "by_id": cameras}


def _find_pynq_root() -> Path | None:
    try:
        spec = importlib.util.find_spec("pynq")
    except (ImportError, ValueError, AttributeError):
        return None
    if spec is None:
        return None
    if spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations)))
    if spec.origin:
        return Path(spec.origin).parent
    return None


def _parse_hwh(path: Path, errors: list[dict]) -> dict:
    try:
        tree = ET.parse(path)
    except FileNotFoundError:
        return {"status": "unknown", "video": [], "buttons": []}
    except PermissionError as exc:
        errors.append({"path": str(path), "kind": "permission", "message": str(exc)})
        return {"status": "unknown", "video": [], "buttons": []}
    except (OSError, ET.ParseError) as exc:
        errors.append({"path": str(path), "kind": "parse", "message": str(exc)})
        return {"status": "unknown", "video": [], "buttons": []}
    result = {"status": "ok", "video": [], "buttons": []}
    for module in tree.iter():
        if module.tag.split("}")[-1] not in {"MODULE", "IP"}:
            continue
        instance = module.get("INSTANCENAME") or module.get("INSTANCE") or module.get("NAME") or "unknown"
        full_name = module.get("FULLNAME") or module.get("FULL_NAME") or instance
        vlnv = module.get("VLNV") or module.get("VLNV_NAME") or "unknown"
        marker = f"{instance} {full_name} {vlnv}".lower()
        group = "video" if any(word in marker for word in ("video", "hdmi", "vdma", "vtc")) else (
            "buttons" if any(word in marker for word in ("button", "gpio")) else None
        )
        if group is None:
            continue
        parameters = {}
        interrupts = []
        for item in module.iter():
            tag = item.tag.split("}")[-1]
            if tag in {"PARAMETER", "PARAM"}:
                name = item.get("NAME") or item.get("name")
                value = item.get("VALUE") or item.get("value") or (item.text or "").strip() or "unknown"
                if name:
                    parameters[name] = value
            elif tag in {"PORT", "PIN"}:
                name = item.get("NAME") or item.get("name") or ""
                lowered = name.lower()
                if any(token in lowered for token in ("interrupt", "intr", "irq", "irpt")):
                    interrupts.append(name)
        addresses = {name: value for name, value in parameters.items() if "ADDR" in name.upper()}
        for item in module.iter():
            if item.tag.split("}")[-1] == "MEMRANGE":
                name = item.get("NAME") or item.get("name") or "unknown"
                base_value = item.get("BASEVALUE") or item.get("basevalue") or "unknown"
                addresses[name] = base_value
        result[group].append({
            "instance": instance,
            "full_name": full_name,
            "vlnv": vlnv,
            "addresses": addresses,
            "parameters": parameters,
            "interrupt_ports": sorted(set(interrupts)),
        })
    return result


def _collect_pynq(root: Path, pynq_root: Path | None, errors: list[dict]) -> dict:
    if pynq_root is None:
        pynq_root = _find_pynq_root()
    sources = []
    base_artifacts = []
    native_libraries = []
    clock_driver = {"path": "unknown", "sha256": "unknown"}
    global_pl_state = []
    base_ips = {"status": "unknown", "video": [], "buttons": []}
    if pynq_root is None:
        errors.append({"path": "pynq", "kind": "missing", "message": "PYNQ package path not found by find_spec"})
        return {
            "root": "unknown", "sources": sources, "native_libraries": native_libraries,
            "clock_driver": clock_driver, "base_artifacts": base_artifacts,
            "base_ips": base_ips, "global_pl_state": global_pl_state,
        }

    pynq_root = Path(pynq_root)
    for relative in _PYNQ_SOURCES:
        path = pynq_root / relative
        digest = _hash_file(path, errors)
        sources.append({"path": str(path), "sha256": digest if digest else "unknown"})

    ps_path = pynq_root / "ps.py"
    ps_digest = _hash_file(ps_path, errors)
    clock_driver = {"path": str(ps_path), "sha256": ps_digest or "unknown"}
    lib_root = pynq_root / "lib"
    try:
        libraries = sorted({
            path
            for pattern in ("_video*.so", "libvideo.so")
            for path in lib_root.rglob(pattern)
            if path.is_file()
        })
    except OSError as exc:
        errors.append({"path": str(lib_root), "kind": "read", "message": str(exc)})
        libraries = []
    for path in libraries:
        digest = _hash_file(path, errors)
        native_libraries.append({"path": str(path), "sha256": digest or "unknown"})
    if not native_libraries:
        errors.append({"path": str(lib_root / "**/libvideo.so"), "kind": "missing", "message": "PYNQ video native library not found under pynq/lib"})

    overlay_dirs = (
        pynq_root / "overlays/base",
        root / "home/xilinx/pynq/overlays/base",
        root / "home/xilinx/pynq/boards/Pynq-Z2/base",
    )
    for name in ("base.bit", "base.hwh", "base.tcl"):
        path = next((directory / name for directory in overlay_dirs if (directory / name).is_file()), overlay_dirs[0] / name)
        digest = _hash_file(path, errors)
        base_artifacts.append({"name": name, "path": str(path), "sha256": digest if digest else "unknown"})
        if name == "base.hwh" and digest:
            parsed = _parse_hwh(path, errors)
            if parsed["status"] == "ok":
                base_ips = parsed

    state_dirs = (
        pynq_root / "pl_server",
        pynq_root,
        pynq_root.parent,
        root / "run",
        root / "var/run",
        root / "var/tmp",
        root / "tmp",
        root / "home/xilinx",
        root / "home/xilinx/.cache/pynq",
        root / "var/lib/pynq",
    )
    seen_state = set()
    for directory in state_dirs:
        try:
            matches = sorted(directory.glob("global_pl_state_*.json"))
        except PermissionError as exc:
            errors.append({"path": str(directory), "kind": "permission", "message": str(exc)})
            continue
        except OSError as exc:
            errors.append({"path": str(directory), "kind": "read", "message": str(exc)})
            continue
        for path in matches:
            if path in seen_state:
                continue
            seen_state.add(path)
            content = _read_text(path, errors)
            digest = _hash_file(path, errors)
            global_pl_state.append({
                "path": str(path),
                "content": content if content is not None else "unknown",
                "sha256": digest or "unknown",
            })
    if not global_pl_state:
        errors.append({"path": "global_pl_state_*.json", "kind": "missing", "message": "PYNQ global PL state file not found in known state directories"})
    return {
        "root": str(pynq_root),
        "sources": sources,
        "native_libraries": native_libraries,
        "clock_driver": clock_driver,
        "base_artifacts": base_artifacts,
        "base_ips": base_ips,
        "global_pl_state": global_pl_state,
    }


def _collect_fonts(root: Path, errors: list[dict]) -> dict:
    directory = root / "usr/share/fonts"
    candidates = []
    try:
        paths = sorted(path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in _FONT_SUFFIXES)
    except PermissionError as exc:
        errors.append({"path": str(directory), "kind": "permission", "message": str(exc)})
        paths = []
    except OSError as exc:
        errors.append({"path": str(directory), "kind": "read", "message": str(exc)})
        paths = []
    if not paths:
        errors.append({"path": str(directory), "kind": "missing", "message": "no font files found under font directory"})
    for path in paths:
        digest = _hash_file(path, errors)
        candidates.append({"path": _relative(path, root), "exists": digest is not None, "sha256": digest or "unknown"})
    return {"root": "/usr/share/fonts", "candidates": candidates}


def collect(root: Path = Path("/"), pynq_root: Path | None = None) -> dict:
    """Collect read-only system evidence. Missing evidence remains explicitly unknown."""
    root = Path(root)
    errors: list[dict] = []
    monotonic = time.monotonic()
    uname = platform.uname()
    boot_id = _read_text(root / "proc/sys/kernel/random/boot_id", errors)
    meminfo = _read_text(root / "proc/meminfo", errors)
    interrupts = _read_text(root / "proc/interrupts", errors)

    memory = _parse_meminfo(meminfo)
    if meminfo is None:
        memory["status"] = "unknown"

    return {
        "schema": SCHEMA,
        "status": "evidence_only",
        "collected_monotonic": monotonic,
        "system": {
            "uname": {
                "system": uname.system,
                "node": uname.node,
                "release": uname.release,
                "version": uname.version,
                "machine": uname.machine,
                "processor": uname.processor,
            },
            "boot_id": {"status": _evidence_status(boot_id), "value": boot_id or "unknown"},
            "monotonic": {"status": "ok", "seconds": monotonic},
            "python": sys.version,
            "modules": _module_versions(errors),
        },
        "memory": memory,
        "interrupts": {"status": _evidence_status(interrupts), "raw": interrupts or "unknown"},
        "camera": _collect_camera(root, errors),
        "iio": _collect_iio(root, errors),
        "devices": _collect_devices(root, errors),
        "fonts": _collect_fonts(root, errors),
        "pynq": _collect_pynq(root, pynq_root, errors),
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Collect read-only standalone display G0 evidence")
    parser.add_argument("--output", required=True, type=Path, help="new JSON file; existing files are never replaced")
    parser.add_argument("--root", type=Path, default=Path("/"), help="filesystem root (primarily for fixture trees)")
    parser.add_argument("--pynq-root", type=Path, help="PYNQ package directory; otherwise located with find_spec")
    args = parser.parse_args(argv)
    payload = json.dumps(collect(args.root, args.pynq_root), indent=2, ensure_ascii=False) + "\n"
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
