"""Capture a read-only snapshot of an installed board runtime environment.

This inventory is observational only. It does not establish offline rebuildability,
run application code, install dependencies, access hardware, or use the network.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .delivery import verify_package

SNAPSHOT_SCHEMA = "material-counter.offline-runtime-snapshot.v1"
DPKG_TIMEOUT_SECONDS = 20
DPKG_QUERY = [
    "dpkg-query",
    "--show",
    "--showformat=${binary:Package}\\t${Version}\\t${Architecture}\\t${db:Status-Status}\\n",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_text(path: Path) -> dict:
    try:
        return {"status": "available", "value": path.read_text(encoding="utf-8").strip()}
    except (OSError, UnicodeError) as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


def _distribution_inventory() -> dict:
    records = []
    try:
        for dist in importlib.metadata.distributions():
            try:
                name = dist.metadata.get("Name")
                version = dist.version
                requires = dist.requires or []
                records.append({
                    "name": name,
                    "version": version,
                    "requires": sorted(requires, key=lambda value: (value.casefold(), value)),
                })
            except Exception as exc:  # Preserve a discoverable but unreadable entry.
                records.append({
                    "name": None,
                    "version": None,
                    "requires": None,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        records.sort(key=lambda item: (
            (item["name"] or "").casefold(), item["name"] or "",
            item["version"] or "",
        ))
        status = "partial" if any(item.get("error") or not item["name"] or not item["version"] for item in records) else "complete"
        return {"status": status, "distributions": records}
    except Exception as exc:
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "distributions": records,
        }


def _dpkg_inventory() -> dict:
    try:
        result = subprocess.run(
            DPKG_QUERY,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DPKG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}", "packages": []}
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "timeout_seconds": DPKG_TIMEOUT_SECONDS,
            "error": f"{type(exc).__name__}: {exc}",
            "packages": [],
        }
    except OSError as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "packages": []}

    packages = []
    malformed_lines = []
    for line_number, line in enumerate(result.stdout.splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) != 4:
            malformed_lines.append({"line": line_number, "value": line})
            continue
        name, version, architecture, status = fields
        if status == "installed":
            packages.append({"name": name, "version": version, "architecture": architecture})
    packages.sort(key=lambda item: (item["name"].casefold(), item["name"], item["architecture"]))
    response = {
        "status": "complete" if result.returncode == 0 and not malformed_lines else "error",
        "returncode": result.returncode,
        "packages": packages,
    }
    if malformed_lines:
        response["malformed_lines"] = malformed_lines
    if result.returncode != 0:
        response["stderr"] = result.stderr.strip()
    return response


def collect_snapshot(package_root: Path, index_sha256: str) -> dict:
    verification = verify_package(package_root, index_sha256)
    package_root = package_root.resolve(strict=True)
    runtime_file = package_root / "board-runtime.json"
    return {
        "schema": SNAPSHOT_SCHEMA,
        "version": 1,
        "collected_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "complete_inventory": False,
        "offline_rebuild_proven": False,
        "scope": {
            "business_code_run": False,
            "dependencies_installed": False,
            "network_accessed": False,
            "hardware_accessed": False,
        },
        "candidate_package": {
            "index_sha256": index_sha256.lower(),
            "verification": verification,
            "board_runtime_sha256": _sha256(runtime_file),
        },
        "python": {
            "executable": sys.executable,
            "version": sys.version,
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "libc": {
                "name": platform.libc_ver()[0] or None,
                "version": platform.libc_ver()[1] or None,
            },
        },
        "os_release": _read_text(Path("/etc/os-release")),
        "boot_id": _read_text(Path("/proc/sys/kernel/random/boot_id")),
        "python_distributions": _distribution_inventory(),
        "dpkg": _dpkg_inventory(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture a read-only board runtime dependency snapshot; this does not prove offline rebuildability."
    )
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--index-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    snapshot = collect_snapshot(args.package_root, args.index_sha256)
    created = False
    try:
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            json.dump(snapshot, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError:
        parser.error(f"output already exists; refusing to overwrite: {args.output}")
    except BaseException:
        if created:
            try:
                args.output.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
