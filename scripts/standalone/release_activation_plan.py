#!/usr/bin/env python3
"""Write a read-only, non-authorizing G4 activation plan from a measured baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

from .delivery import verify_package


SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
EXPECTED_RESTART = "no"
EXPECTED_KILL_MODE = "process"
EXPECTED_SEND_SIGKILL = "no"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_unit(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read candidate unit: {exc}") from exc
    section = ""
    dependencies: dict[str, str] = {}
    install: dict[str, str] = {}
    environments: list[str] = []
    allowed_service = {"Type", "User", "Group", "WorkingDirectory",
                       "EnvironmentFile", "Environment", "ExecStart", "Restart",
                       "KillMode", "SendSIGKILL", "TimeoutStartSec",
                       "TimeoutStopSec", "StandardOutput", "StandardError"}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if section not in {"Unit", "Service", "Install"}:
                raise ValueError(f"unsupported unit section: {section}")
            continue
        key, separator, value = line.partition("=")
        if not separator or key.strip() != key:
            raise ValueError(f"invalid unit directive at line {number}")
        if section == "Unit" and key not in {"Description", "After"}:
            raise ValueError(f"unsupported unit directive that may alter dependencies: {key}")
        if section == "Service" and key not in allowed_service:
            raise ValueError(f"unsupported Service directive: {key}")
        if section == "Install" and key != "WantedBy":
            raise ValueError(f"unsupported Install directive: {key}")
        if not section:
            raise ValueError("unit directive outside a section")
        if section == "Unit" and key == "After":
            if key in dependencies:
                raise ValueError(f"duplicate unit dependency directive: {key}")
            dependencies[key] = value.strip()
        if section == "Service" and key == "Environment":
            environments.append(value.strip())
        elif section == "Service":
            if key in values:
                raise ValueError(f"duplicate Service directive: {key}")
            values[key] = value.strip()
        if section == "Install":
            if key in install:
                raise ValueError(f"duplicate Install directive: {key}")
            install[key] = value.strip()
    required = {"WorkingDirectory", "EnvironmentFile", "ExecStart", "Restart",
                "KillMode", "SendSIGKILL"}
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError("candidate unit is missing required directives: " + ", ".join(missing))
    for key in ("WorkingDirectory", "EnvironmentFile"):
        if not PurePosixPath(values[key]).is_absolute():
            raise ValueError(f"{key} must be an absolute path")
    if values["WorkingDirectory"] != "/opt/material-counter/current":
        raise ValueError("WorkingDirectory must be the reviewed current link")
    if values["EnvironmentFile"] != "/etc/material-counter/package.env":
        raise ValueError("EnvironmentFile must be the reviewed package environment path")
    expected_values = {"Type": "simple", "User": "root", "Group": "root",
                       "TimeoutStartSec": "infinity", "TimeoutStopSec": "infinity",
                       "StandardOutput": "journal", "StandardError": "journal"}
    for key, expected in expected_values.items():
        if values.get(key) != expected:
            raise ValueError(f"{key} must equal {expected}")
    if sorted(environments) != ["PYTHONDONTWRITEBYTECODE=1", "XILINX_XRT=/usr"]:
        raise ValueError("unit Environment directives differ from the reviewed pair")
    if values["Restart"] != EXPECTED_RESTART:
        raise ValueError("Restart must be no")
    if values["KillMode"] != EXPECTED_KILL_MODE:
        raise ValueError("KillMode must be process")
    if values["SendSIGKILL"] != EXPECTED_SEND_SIGKILL:
        raise ValueError("SendSIGKILL must be no")
    command = values["ExecStart"].split()
    expected_tail = ["-m", "scripts.standalone.boot_integrated", "--root",
                     "${PACKAGE_ROOT}", "--index-sha256", "${PACKAGE_INDEX_SHA256}"]
    if command != ["/usr/local/share/pynq-venv/bin/python3", "-B", *expected_tail]:
        raise ValueError("ExecStart must match the approved boot_integrated invocation")
    if dependencies != {"After": "local-fs.target"}:
        raise ValueError("unit dependencies must be limited to After=local-fs.target")
    if install != {"WantedBy": "multi-user.target"}:
        raise ValueError("unit Install directives differ from the reviewed target")
    return values


def _parse_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read candidate environment file: {exc}") from exc
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key.strip() != key:
            raise ValueError(f"invalid environment assignment at line {number}")
        if key in result:
            raise ValueError(f"duplicate environment key: {key}")
        result[key] = value.strip()
    for key in ("PACKAGE_ROOT", "PACKAGE_INDEX_SHA256"):
        if key not in result:
            raise ValueError(f"environment file is missing {key}")
    if set(result) != {"PACKAGE_ROOT", "PACKAGE_INDEX_SHA256"}:
        raise ValueError("environment file contains unreviewed variables")
    if not PurePosixPath(result["PACKAGE_ROOT"]).is_absolute():
        raise ValueError("PACKAGE_ROOT must be an absolute path")
    if not SHA256_RE.fullmatch(result["PACKAGE_INDEX_SHA256"]):
        raise ValueError("PACKAGE_INDEX_SHA256 must be a 64-character SHA-256")
    return result


def _baseline_record(baseline: dict, group: str, name: str) -> tuple[dict, list[str]]:
    blockers: list[str] = []
    fixed = baseline.get(group)
    if not isinstance(fixed, dict) or not isinstance(fixed.get(name), dict):
        return {}, [f"baseline missing {group}.{name}"]
    record = fixed[name]
    if not isinstance(record.get("path"), str) or not record["path"]:
        blockers.append(f"baseline {group}.{name} has no observed path")
    if record.get("state") not in {"present", "missing"}:
        blockers.append(f"baseline {group}.{name} state is unknown")
    if record.get("state") == "present":
        kind = record.get("type")
        if kind == "file" and not isinstance(record.get("sha256"), str):
            blockers.append(f"baseline {group}.{name} file hash is missing or unknown")
        if kind == "symlink" and not isinstance(record.get("symlink_target"), str):
            blockers.append(f"baseline {group}.{name} symlink target is missing or unknown")
        if kind not in {"file", "symlink"}:
            blockers.append(f"baseline {group}.{name} has unsupported observed type")
    return record, blockers


def _baseline_target(record: dict) -> dict:
    keys = ("path", "state", "exists", "type", "sha256", "symlink_target",
            "resolved_target", "resolved_target_state")
    return {key: record[key] for key in keys if key in record}


def build_plan(release_root: Path, pinned_sha: str, unit_path: Path, env_path: Path,
               baseline_path: Path) -> dict:
    if not SHA256_RE.fullmatch(pinned_sha):
        raise ValueError("caller-pinned index digest must be a 64-character SHA-256")
    release = release_root.resolve(strict=True)
    verify_package(release, pinned_sha)
    actual_pinned_sha = _sha256(release / "deployment-index.json")
    if actual_pinned_sha.lower() != pinned_sha.lower():
        raise ValueError("verified deployment index digest differs from caller pin")

    unit = _parse_unit(unit_path)
    env = _parse_env(env_path)
    if env["PACKAGE_ROOT"] != str(release):
        raise ValueError("PACKAGE_ROOT must equal the absolute staged release directory")
    if env["PACKAGE_INDEX_SHA256"].lower() != pinned_sha.lower():
        raise ValueError("PACKAGE_INDEX_SHA256 differs from caller-pinned digest")
    current_destination = PurePosixPath(unit["WorkingDirectory"])
    if str(current_destination) != unit["WorkingDirectory"] or ".." in current_destination.parts:
        raise ValueError("WorkingDirectory must be normalized absolute path")

    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read deployment baseline JSON: {exc}") from exc
    if not isinstance(baseline, dict) or baseline.get("schema") != "material-counter-g4-release-baseline-v1":
        raise ValueError("unsupported deployment baseline schema")

    records = {}
    blockers: list[str] = []
    for name in ("unit_file", "environment_file", "current"):
        records[name], found = _baseline_record(baseline, "fixed_paths", name)
        blockers.extend(found)
    if records["unit_file"].get("path") != "/etc/systemd/system/material-counter-integrated.service":
        blockers.append("baseline unit path does not match the reviewed service destination")
    if records["current"].get("path") != str(current_destination):
        blockers.append("baseline current path does not match unit WorkingDirectory")
    if records["environment_file"].get("path") != unit["EnvironmentFile"]:
        blockers.append("baseline environment path does not match unit EnvironmentFile")
    systemd = baseline.get("systemd")
    if not isinstance(systemd, dict):
        blockers.append("baseline missing systemd state")
    else:
        for field in ("load_state", "active_state", "sub_state", "enabled_state"):
            if not isinstance(systemd.get(field), str) or systemd[field] in {"", "unknown"}:
                blockers.append(f"baseline systemd {field} is missing or unknown")

    targets = {
        "unit_file": {"path": records["unit_file"].get("path"), "sha256": _sha256(unit_path)},
        "environment_file": {"path": records["environment_file"].get("path"), "sha256": _sha256(env_path)},
        "current": {"path": str(current_destination), "symlink_target": str(release),
                    "sha256": actual_pinned_sha},
    }
    operations = []
    for name, source in (("unit_file", unit_path), ("environment_file", env_path)):
        operations.append({
            "item": name,
            "source": {"path": str(source.resolve()), "sha256": _sha256(source)},
            "target": targets[name],
            "rollback_target": _baseline_target(records[name]),
        })
    operations.append({
        "item": "current",
        "source": {"path": str(release), "sha256": actual_pinned_sha,
                   "meaning": "verified staged release; index digest identifies package index"},
        "target": targets["current"],
        "rollback_target": _baseline_target(records["current"]),
    })

    return {
        "schema": "material-counter-g4-release-activation-plan-v1",
        "purpose": "read-only activation planning; not an executable authorization",
        "activation_allowed": False,
        "hardware_safe_to_switch": False,
        "G4_release": False,
        "package": {"release_root": str(release), "deployment_index_sha256": actual_pinned_sha,
                    "verification": "verify_package passed"},
        "candidate_configuration": {
            "unit": {"path": str(unit_path.resolve()), "sha256": _sha256(unit_path), "values": unit},
            "environment": {"path": str(env_path.resolve()), "sha256": _sha256(env_path),
                            "values": env},
        },
        "baseline": {"path": str(baseline_path.resolve()), "sha256": _sha256(baseline_path),
                     "captured_at_utc": baseline.get("captured_at_utc"),
                     "systemd": baseline.get("systemd"), "fixed_paths": baseline.get("fixed_paths")},
        "blockers": sorted(set(blockers)),
        "activation_steps": operations,
        "hardware_gate": {
            "state": "unproven",
            "required_same_round_checks": ["DMA idle and error state", "VDMA halted and error state",
                                            "unique owner and child-process/resource release"],
            "note": "This planner does not read MMIO or establish hardware/resource safety.",
        },
        "network_dependency": "forbidden; candidate unit checked for known network service dependencies",
        "operations_performed": ["read staged package and configuration", "verify_package", "read baseline JSON"],
        "operations_not_performed": ["write unit/env/current", "systemctl", "symlink changes", "MMIO access"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", required=True, type=Path,
                        help="already staged release directory")
    parser.add_argument("--index-sha256", required=True,
                        help="caller-pinned deployment index SHA-256")
    parser.add_argument("--unit-file", required=True, type=Path,
                        help="candidate systemd unit file")
    parser.add_argument("--env-file", required=True, type=Path,
                        help="environment file proposed for writing")
    parser.add_argument("--baseline", required=True, type=Path,
                        help="read-only pre-deployment release_baseline JSON")
    parser.add_argument("--output", required=True, type=Path,
                        help="new plan JSON path; existing files are never overwritten")
    args = parser.parse_args(argv)
    created = False
    try:
        plan = build_plan(args.release_root, args.index_sha256, args.unit_file,
                          args.env_file, args.baseline)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            json.dump(plan, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError:
        print(f"refusing to overwrite existing output: {args.output}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        if created:
            try:
                args.output.unlink(missing_ok=True)
            except OSError:
                pass
        print(f"cannot create activation plan: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
