"""Run WO-0008 morphology checks through the project's existing Ross MCP client.

This runner owns a fresh Ross state directory and Vivado session for each run.
It preserves the JSON-RPC transcript, XSim logs, lint parser artifacts, reports,
and source hashes beneath one new evidence directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "fpga" / "scripts"))
from run_rgb2gray_mcp import Ross  # Reuse the configured official Ross transport.

MODES = (0, 1, 2)
CHAINS = (0, 1)
READY_MODES = (0, 1)
SEED = 6862
PART = "xc7z020clg400-1"
PROJECT_NAME = "morphology_pkg"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_python(command: list[str]) -> None:
    print("PYTHON: " + subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=REPO, check=True)


def tcl_braced(value: str) -> str:
    """Return a Tcl braced word, protecting braces and line breaks in paths."""
    value = value.replace("\r", "").replace("\n", "")
    value = value.replace("{", r"\{").replace("}", r"\}")
    return "{" + value + "}"


def xsim_arg(value: str) -> str:
    """Quote one XSim command-line token when a Windows path contains spaces."""
    if any(ch.isspace() for ch in value):
        return '"' + value.replace('"', r'\"') + '"'
    return value


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def result_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(result_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(result_text(v) for v in value)
    return str(value)


def prepare_vectors(root: Path, supplied: Path | None) -> Path:
    if supplied is not None:
        vectors = supplied.resolve()
        manifest_path = vectors / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"morphology vector manifest missing: {manifest_path}")
        return vectors

    vectors = root / "vectors"
    run_python([
        sys.executable,
        str(REPO / "python" / "refmodel" / "morphology_vectors.py"),
        "--out", str(vectors),
    ])
    return vectors


def safe_vector_path(vectors: Path, relative: str) -> Path:
    path = (vectors / relative).resolve()
    try:
        path.relative_to(vectors.resolve())
    except ValueError as exc:
        raise ValueError(f"vector path escapes vector directory: {relative!r}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"vector file missing: {path}")
    return path


def load_scenes(vectors: Path, selection: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path = vectors / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("scenes"), list) or not manifest["scenes"]:
        raise ValueError("manifest.scenes must be a non-empty list")

    declared_hashes = manifest.get("files_sha256")
    if not isinstance(declared_hashes, dict) or not declared_hashes:
        raise ValueError("manifest.files_sha256 must map every vector file to its SHA-256")
    for relative, expected_hash in declared_hashes.items():
        path = safe_vector_path(vectors, relative)
        actual_hash = sha256(path)
        if actual_hash.lower() != str(expected_hash).lower():
            raise ValueError(f"vector hash mismatch for {relative}: {actual_hash} != {expected_hash}")

    by_name: dict[str, dict[str, Any]] = {}
    for scene in manifest["scenes"]:
        if not isinstance(scene, dict):
            raise ValueError("each manifest scene must be an object")
        name = scene.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError(f"unsafe or missing scene name: {name!r}")
        if name in by_name:
            raise ValueError(f"duplicate scene name: {name}")
        for field in ("width", "height", "frames", "beats"):
            value = scene.get(field)
            if type(value) is not int or value <= 0:
                raise ValueError(f"scene {name}: {field} must be a positive integer")
        if scene["beats"] != scene["width"] * scene["height"] * scene["frames"]:
            raise ValueError(f"scene {name}: beats does not equal width*height*frames")
        for field in ("rgb", "binary"):
            if not isinstance(scene.get(field), str):
                raise ValueError(f"scene {name}: missing {field} vector path")
            safe_vector_path(vectors, scene[field])
        expected = scene.get("expected")
        if not isinstance(expected, dict) or not all(str(mode) in expected for mode in MODES):
            raise ValueError(f"scene {name}: expected must contain paths for modes 0, 1, and 2")
        for mode in MODES:
            safe_vector_path(vectors, expected[str(mode)])
        by_name[name] = scene

    if selection is None or selection.strip().lower() == "all":
        names = list(by_name)
    else:
        names = [part.strip() for part in selection.split(",") if part.strip()]
        if not names:
            raise ValueError("--scenes must be 'all' or a comma-separated scene list")
        if len(set(names)) != len(names):
            raise ValueError("--scenes contains a duplicate name")
        unknown = [name for name in names if name not in by_name]
        if unknown:
            raise ValueError("unknown scene(s): " + ", ".join(unknown))
    return manifest, [by_name[name] for name in names]


def source_hashes(root: Path) -> None:
    candidates = [
        REPO / "fpga" / "rtl" / "axis_morphology3x3.v",
        REPO / "fpga" / "rtl" / "axis_rgb2gray.v",
        REPO / "fpga" / "rtl" / "axis_gray2binary.v",
        REPO / "fpga" / "sim" / "tb_axis_morphology3x3.sv",
        REPO / "python" / "refmodel" / "morphology_vectors.py",
        REPO / "python" / "refmodel" / "check_morphology_output.py",
        Path(__file__).resolve(),
    ]
    hashes = {
        str(path.relative_to(REPO)).replace("\\", "/"):
            sha256(path) if path.is_file() else "MISSING"
        for path in candidates
    }
    (root / "source-hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")


def copy_xsim_logs(project: Path, destination: Path) -> tuple[list[Path], str]:
    simdir = project / f"{PROJECT_NAME}.sim" / "sim_1" / "behav" / "xsim"
    logs = sorted(simdir.glob("*.log"))
    if not logs:
        raise RuntimeError(f"XSim did not produce any logs under {simdir}")
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    texts: list[str] = []
    for log in logs:
        target = destination / log.name
        shutil.copy2(log, target)
        copied.append(target)
        texts.append(target.read_text(encoding="utf-8", errors="replace"))
    return copied, "\n".join(texts)


def clear_xsim_logs(project: Path) -> None:
    simdir = project / f"{PROJECT_NAME}.sim" / "sim_1" / "behav" / "xsim"
    if simdir.is_dir():
        for log in simdir.glob("*.log"):
            log.unlink()


def validate_lint_csv(path: Path, expected_count: int) -> None:
    rows = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()]
    if len(rows) != expected_count:
        raise RuntimeError(
            f"canonical lint row count {len(rows)} differs from Vivado total {expected_count}"
        )
    for line_number, line in enumerate(rows, 1):
        if len(line.split(",")) != 7:
            raise RuntimeError(f"lint CSV row {line_number} does not have seven canonical fields: {line!r}")
    print(f"CANONICAL_LINT_ROWS={len(rows)}", flush=True)


def write_lint_canonical(root: Path, mode: int, lint_result: dict[str, Any],
                         log_messages: dict[str, Any]) -> int:
    result_path = root / f"lint-mode-{mode}-tool-result.json"
    messages_path = root / f"lint-mode-{mode}-log-messages.json"
    result_path.write_text(json.dumps(lint_result, indent=2), encoding="utf-8")
    messages_path.write_text(json.dumps(log_messages, indent=2), encoding="utf-8")

    authoritative_text = json_text(lint_result) + "\n" + json_text(log_messages)
    count_match = re.search(
        r"Total\s+of\s+(\d+)\s+linter\s+message(?:\(s\)|s)?\s+generated",
        authoritative_text, flags=re.IGNORECASE,
    )
    if not count_match:
        raise RuntimeError(f"Vivado lint mode {mode} did not expose its authoritative message total")
    authoritative_count = int(count_match.group(1))

    parser_script = REPO / ".agents" / "skills" / "vivado-rtl-lint" / "parse_lint_report.py"
    report = root / f"lint-mode-{mode}.rpt"
    raw_csv = root / f"lint-mode-{mode}-parser-raw.csv"
    canonical_csv = root / f"lint-mode-{mode}-parsed.csv"
    run_python([sys.executable, str(parser_script), str(report), str(raw_csv)])
    raw_rows = [line for line in raw_csv.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()]

    if authoritative_count == 0:
        normalized_json = root / f"lint-mode-{mode}-authoritative-zero.json"
        normalized_json.write_text(json.dumps({
            "cols": ["id", "description", "name", "info", "module", "file", "line"],
            "rows": [],
        }, indent=2), encoding="utf-8")
        run_python([sys.executable, str(parser_script), str(normalized_json), str(canonical_csv)])
        canonical_rows = [line for line in canonical_csv.read_text(
            encoding="utf-8", errors="replace").splitlines() if line.strip()]
        if canonical_rows:
            raise RuntimeError(f"official parser did not preserve zero-row result for mode {mode}")
        (root / f"lint-mode-{mode}-normalization.json").write_text(json.dumps({
            "authoritative_vivado_total": 0,
            "raw_parser_nonempty_rows": len(raw_rows),
            "canonical_nonempty_rows": 0,
            "normalization": "empty JSON rows passed through the official parser for authoritative zero",
        }, indent=2), encoding="utf-8")
    else:
        shutil.copy2(raw_csv, canonical_csv)

    validate_lint_csv(canonical_csv, authoritative_count)
    return authoritative_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("red", "verify", "lint", "synth", "all"), default="all")
    parser.add_argument("--out", type=Path,
                        help="new evidence directory (must not already exist)")
    parser.add_argument("--vectors", type=Path,
                        help="existing morphology vectors; otherwise generate them for verify/all")
    parser.add_argument("--scenes", default=None,
                        help="comma-separated scene names or 'all' (default: every manifest scene)")
    args = parser.parse_args()

    if args.out is None:
        root = REPO / "data" / "results" / (
            "morphology-" + args.phase + "-" + time.strftime("%Y%m%d-%H%M%S")
        )
    else:
        root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    print("EVIDENCE_DIR=" + str(root), flush=True)

    vectors: Path | None = None
    manifest: dict[str, Any] | None = None
    scenes: list[dict[str, Any]] = []
    if args.phase in ("verify", "all"):
        vectors = prepare_vectors(root, args.vectors)
        manifest, scenes = load_scenes(vectors, args.scenes)
        print("MORPHOLOGY_SCENES=" + ",".join(scene["name"] for scene in scenes), flush=True)

    source_hashes(root)
    (root / "run-config.json").write_text(json.dumps({
        "phase": args.phase,
        "part": PART,
        "project": PROJECT_NAME,
        "mode_values": list(MODES),
        "chain_values": list(CHAINS),
        "ready_mode_values": list(READY_MODES),
        "seed": SEED,
        "vectors": str(vectors) if vectors else None,
        "scenes": [scene["name"] for scene in scenes],
        "simulation_watchdog": "30ms",
        "mcp_transport": "Ross stdio MCP reused via run_rgb2gray_mcp.Ross",
    }, indent=2), encoding="utf-8")

    client = Ross(root)
    session_id: str | None = None
    project = root / "project"
    project_posix = project.as_posix()
    repo_posix = REPO.as_posix()
    task_ids: list[str] = []

    def execute(command: str, allow_error: bool = False) -> dict[str, Any]:
        result = client.tool("vivado_execute", session_id=session_id,
                             command=command, verbosity="full")
        if result.get("status") == "running" or result.get("error_type") == "STILL_RUNNING_DETACHED":
            # Never resubmit a detached command; poll only the current session.
            for attempt in range(20):
                time.sleep(30)
                status = client.tool("vivado_status", session_id=session_id, action="session")
                if "SKIPPED (duplicate)" in result_text(status):
                    raise RuntimeError("Ross MCP duplicate protection skipped a Vivado command: " + command)
                if status.get("is_command_running") is False:
                    if status.get("last_completed_code") != 0 and not allow_error:
                        raise RuntimeError("Detached Vivado command failed: " + json_text(status))
                    return status
            raise RuntimeError("Vivado detached command did not finish within 10 minutes")
        full_text = result_text(result)
        if "SKIPPED (duplicate)" in full_text:
            raise RuntimeError("Ross MCP duplicate protection skipped a Vivado command: " + command)
        failed = result.get("exit_code", 0) != 0 or result.get("isError")
        if failed and not allow_error:
            raise RuntimeError("Vivado command failed: " + json_text(result))
        return result

    def update_task(task_id: str, status: str, summary: str | None = None) -> None:
        arguments: dict[str, Any] = {"operation": "update", "session_id": session_id,
                                     "id": task_id, "status": status}
        if summary is not None:
            arguments["summary"] = summary
        client.tool("vivado_todos", **arguments)

    def setup_project() -> None:
        execute("version -short")
        execute(f"create_project {PROJECT_NAME} {tcl_braced(project_posix)} -part {PART}")
        execute(f"set_property target_simulator XSim [current_project]; "
                "set_property xsim.simulate.runtime 30ms [get_filesets sim_1]; "
                "puts {MORPHOLOGY_PROJECT_READY}")

    def add_common_sim_sources() -> None:
        morph = f"{repo_posix}/fpga/rtl/axis_morphology3x3.v"
        rgb2gray = f"{repo_posix}/fpga/rtl/axis_rgb2gray.v"
        gray2binary = f"{repo_posix}/fpga/rtl/axis_gray2binary.v"
        testbench = f"{repo_posix}/fpga/sim/tb_axis_morphology3x3.sv"
        execute(
            f"add_files {tcl_braced(morph)}; "
            f"add_files -fileset sim_1 {tcl_braced(rgb2gray)}; "
            f"add_files -fileset sim_1 {tcl_braced(gray2binary)}; "
            f"add_files -fileset sim_1 {tcl_braced(testbench)}; "
            "set_property top tb_axis_morphology3x3 [get_filesets sim_1]; "
            "update_compile_order -fileset sources_1; update_compile_order -fileset sim_1; "
            "puts {MORPHOLOGY_SIM_SOURCES_READY}"
        )

    def run_skeleton_baseline() -> None:
        skeleton = f"{repo_posix}/fpga/rtl/axis_stream_skeleton.v"
        skeleton_tb = f"{repo_posix}/fpga/sim/tb_axis_stream_skeleton.sv"
        execute(
            f"add_files {tcl_braced(skeleton)}; "
            f"add_files -fileset sim_1 {tcl_braced(skeleton_tb)}; "
            "set_property top tb_axis_stream_skeleton [get_filesets sim_1]; "
            "set_property xsim.simulate.runtime 10us [get_filesets sim_1]; "
            "update_compile_order -fileset sources_1; update_compile_order -fileset sim_1"
        )
        clear_xsim_logs(project)
        execute("launch_simulation -simset sim_1 -mode behavioral; puts {MORPHOLOGY_SKELETON_BASELINE}")
        _, text = copy_xsim_logs(project, root / "sim-skeleton")
        if "PASS: in=8 out=8" not in text or "Fatal:" in text or "ERROR:" in text:
            raise RuntimeError("existing AXI stream skeleton regression failed")
        execute("close_sim -force; puts {MORPHOLOGY_SKELETON_CLOSED}")

    def run_red() -> None:
        # Testbench and upstream stages are present; morphology RTL is deliberately omitted.
        testbench = f"{repo_posix}/fpga/sim/tb_axis_morphology3x3.sv"
        rgb2gray = f"{repo_posix}/fpga/rtl/axis_rgb2gray.v"
        gray2binary = f"{repo_posix}/fpga/rtl/axis_gray2binary.v"
        execute(
            f"add_files -fileset sim_1 {tcl_braced(rgb2gray)}; "
            f"add_files -fileset sim_1 {tcl_braced(gray2binary)}; "
            f"add_files -fileset sim_1 {tcl_braced(testbench)}; "
            "set_property top tb_axis_morphology3x3 [get_filesets sim_1]; "
            "update_compile_order -fileset sim_1; puts {MORPHOLOGY_RED_SETUP}"
        )
        result = execute("launch_simulation -simset sim_1 -mode behavioral; puts {MORPHOLOGY_RED}",
                         allow_error=True)
        (root / "red-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        try:
            _, log_text = copy_xsim_logs(project, root / "sim-red")
        except RuntimeError:
            log_text = ""
        text = log_text + "\n" + json_text(result)
        missing_module = "axis_morphology3x3" in text and any(
            signature in text.lower() for signature in (
                "not found", "unresolved", "cannot be resolved", "module is not declared",
            )
        ) and any(signature in text for signature in ("VRFC", "xelab", "module not found", "unresolved"))
        if not missing_module:
            raise RuntimeError("RED run did not capture an identifiable missing axis_morphology3x3 module")
        if "TEST_PASS" in text:
            raise RuntimeError("RED run unexpectedly reached TEST_PASS")
        print("MORPHOLOGY_RED_EXPECTED_MISSING_MODULE", flush=True)

    def run_case(scene: dict[str, Any], mode: int, chain: int, ready: int) -> None:
        assert vectors is not None and manifest is not None
        output_path = root / f"output-{scene['name']}-mode{mode}-chain{chain}-ready{ready}.hex"
        expected_path = safe_vector_path(vectors, scene["expected"][str(mode)])
        rgb_path = safe_vector_path(vectors, scene["rgb"])
        binary_path = safe_vector_path(vectors, scene["binary"])
        plusargs = [
            ("N", str(scene["beats"])),
            ("SCENE", scene["name"]),
            ("RGB", rgb_path.as_posix()),
            ("BINARY", binary_path.as_posix()),
            ("EXPECTED", expected_path.as_posix()),
            ("OUTPUT", output_path.as_posix()),
            ("READY_MODE", str(ready)),
            ("SEED", str(SEED)),
            ("RESET_CASE", "1"),
        ]
        options = [token for key, value in plusargs
                   for token in ("-testplusarg", f"{key}={value}")]
        more_options = " " + " ".join(xsim_arg(option) for option in options)
        generics = (f"FRAME_WIDTH={scene['width']} FRAME_HEIGHT={scene['height']} "
                    f"MODE={mode} CHAIN={chain}")
        simset = "[get_filesets sim_1]"
        execute(
            f"set_property generic {tcl_braced(generics)} {simset}; "
            f"set_property xsim.elaborate.xelab.more_options {{}} {simset}; "
            f"set_property xsim.simulate.xsim.more_options {tcl_braced(more_options)} {simset}; "
            f"set_property xsim.simulate.runtime 30ms {simset}; "
            "update_compile_order -fileset sim_1"
        )
        clear_xsim_logs(project)
        execute(
            f"launch_simulation -simset sim_1 -mode behavioral; "
            f"puts {{MORPHOLOGY_SCENE_{scene['name']}_MODE_{mode}_CHAIN_{chain}_READY_{ready}}}"
        )
        _, text = copy_xsim_logs(
            project, root / f"sim-{scene['name']}-mode{mode}-chain{chain}-ready{ready}"
        )
        marker = (
            f"TEST_PASS scene={scene['name']} mode={mode} chain={chain} "
            f"ready={ready} beats={scene['beats']} seed={SEED}"
        )
        if marker not in text or "Fatal:" in text or "ERROR:" in text:
            raise RuntimeError(
                f"XSim case failed terminal PASS gate: {marker}; "
                f"logs saved under sim-{scene['name']}-mode{mode}-chain{chain}-ready{ready}"
            )
        if not output_path.is_file():
            raise RuntimeError(f"XSim PASS marker was present but output file is missing: {output_path}")
        execute(
            f"close_sim -force; "
            f"puts {{MORPHOLOGY_CASE_CLOSED_{scene['name']}_MODE_{mode}_CHAIN_{chain}_READY_{ready}}}"
        )

    def add_standalone_morphology(label: str) -> None:
        morph = f"{repo_posix}/fpga/rtl/axis_morphology3x3.v"
        skeleton = f"{repo_posix}/fpga/rtl/axis_stream_skeleton.v"
        execute(
            f"set morph_files [get_files -quiet {tcl_braced(morph)}]; "
            "if {[llength $morph_files] == 0} {"
            f"add_files {tcl_braced(morph)}"
            "}; "
            f"set skeleton_files [get_files -quiet {tcl_braced(skeleton)}]; "
            "if {[llength $skeleton_files] != 0} {remove_files $skeleton_files}; "
            "set_property top axis_morphology3x3 [get_filesets sources_1]; "
            f"update_compile_order -fileset sources_1; puts {{MORPHOLOGY_STANDALONE_SETUP_{label}}}"
        )

    def run_lint() -> None:
        add_standalone_morphology("LINT")
        for mode in MODES:
            report_path = (root / f"lint-mode-{mode}.rpt").as_posix()
            execute(
                "if {[current_design -quiet] ne {}} {close_design}; "
                f"puts {{MORPHOLOGY_LINT_PREP_MODE_{mode}}}"
            )
            lint_result = execute(
                f"synth_design -top axis_morphology3x3 -part {PART} "
                f"-generic {{FRAME_WIDTH=320 FRAME_HEIGHT=240 MODE={mode}}} "
                f"-lint -file {tcl_braced(report_path)}"
            )
            messages = client.tool("vivado_log_messages", session_id=session_id,
                                   filter="all", max_messages=500)
            total = write_lint_canonical(root, mode, lint_result, messages)
            print(f"MORPHOLOGY_LINT_MODE_{mode}_TOTAL={total}", flush=True)
            execute(
                "if {[current_design -quiet] ne {}} {close_design}; "
                f"puts {{MORPHOLOGY_LINT_MODE_CLOSED_{mode}}}"
            )

    def run_synth() -> None:
        add_standalone_morphology("SYNTH")
        summaries: dict[str, Any] = {
            "top": "axis_morphology3x3",
            "part": PART,
            "frame_width": 320,
            "frame_height": 240,
            "clock_constraint": "10ns aclk; no I/O delays",
            "modes": {},
        }
        for mode in MODES:
            execute(
                "if {[current_design -quiet] ne {}} {close_design}; "
                f"puts {{MORPHOLOGY_SYNTH_PREP_MODE_{mode}}}"
            )
            execute(
                f"synth_design -top axis_morphology3x3 -part {PART} -mode out_of_context "
                f"-generic {{FRAME_WIDTH=320 FRAME_HEIGHT=240 MODE={mode}}}"
            )
            execute(
                "create_clock -name aclk -period 10.000 [get_ports aclk]; "
                f"puts {{MORPHOLOGY_SYNTH_CLOCK_MODE_{mode}}}"
            )
            util_path = (root / f"utilization-mode-{mode}.rpt").as_posix()
            hierarchical_path = (root / f"utilization-hierarchical-mode-{mode}.rpt").as_posix()
            timing_path = (root / f"timing-summary-mode-{mode}.rpt").as_posix()
            execute(
                f"report_utilization -file {tcl_braced(util_path)}; "
                f"report_utilization -hierarchical -file {tcl_braced(hierarchical_path)}; "
                f"report_timing_summary -file {tcl_braced(timing_path)}"
            )
            messages = client.tool("vivado_log_messages", session_id=session_id,
                                   filter="all", max_messages=500)
            (root / f"synth-mode-{mode}-log-messages.json").write_text(
                json.dumps(messages, indent=2), encoding="utf-8"
            )
            report = root / f"utilization-mode-{mode}.rpt"
            report_text = report.read_text(encoding="utf-8", errors="replace")
            patterns = {
                "lut": r"\|\s*Slice LUTs\*?\s*\|\s*([\d,]+)",
                "ff": r"\|\s*Slice Registers\s*\|\s*([\d,]+)",
                "dsp": r"\|\s*DSPs\s*\|\s*([\d,]+)",
                "bram_tiles": r"\|\s*Block RAM Tile\s*\|\s*([\d,]+)",
            }
            resources: dict[str, int] = {}
            for name, pattern in patterns.items():
                match = re.search(pattern, report_text)
                if not match:
                    raise RuntimeError(f"could not extract {name} for MODE={mode} from {report}")
                resources[name] = int(match.group(1).replace(",", ""))
            summaries["modes"][str(mode)] = resources
            execute(f"close_design; puts {{MORPHOLOGY_SYNTH_MODE_CLOSED_{mode}}}")
        (root / "resource-summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")

    try:
        started = client.tool("vivado_start", working_dir=str(root),
                              session_type="general", gui_mode=False)
        session_id = started.get("session_id")
        if not session_id:
            raise RuntimeError("Ross did not return a Vivado session_id: " + json_text(started))

        tasks = [{"id": "setup", "content": "Create a fresh Vivado project and configure XSim"}]
        if args.phase == "red":
            tasks.append({"id": "red", "content": "Capture the expected missing morphology module error"})
        if args.phase in ("verify", "all"):
            tasks.extend([
                {"id": "verify", "content": "Run morphology XSim matrix and Python output checks"},
            ])
        if args.phase in ("lint", "all"):
            tasks.append({"id": "lint", "content": "Run official Vivado lint for modes 0, 1, and 2"})
        if args.phase in ("synth", "all"):
            tasks.append({"id": "synth", "content": "Synthesize modes 0, 1, and 2 and record resources"})
        task_ids = [task["id"] for task in tasks]
        client.tool("vivado_todos", operation="plan", session_id=session_id,
                    title="WO-0008 streaming 3x3 morphology verification", tasks=tasks)

        update_task("setup", "active")
        setup_project()
        update_task("setup", "done", "Fresh morphology_pkg project created through the Ross MCP")

        if args.phase == "red":
            update_task("red", "active")
            run_red()
            update_task("red", "done", "Missing axis_morphology3x3 module captured in XSim evidence")
            return

        if args.phase in ("verify", "all"):
            update_task("verify", "active")
            run_skeleton_baseline()
            add_common_sim_sources()
            run_count = 0
            for scene in scenes:
                for mode in MODES:
                    for chain in CHAINS:
                        for ready in READY_MODES:
                            run_case(scene, mode, chain, ready)
                            run_count += 1

            assert vectors is not None
            checker = REPO / "python" / "refmodel" / "check_morphology_output.py"
            checker_command = [
                sys.executable, str(checker), "--vectors", str(vectors), "--run", str(root),
            ]
            checker_command.extend(["--scenes", ",".join(scene["name"] for scene in scenes)])
            run_python(checker_command)
            print(f"MORPHOLOGY_XSIM_CASES_PASS={run_count}", flush=True)
            update_task("verify", "done", f"{run_count} XSim matrix cases and the independent Python checker passed")

        if args.phase in ("lint", "all"):
            update_task("lint", "active")
            run_lint()
            update_task("lint", "done", "Vivado lint total and canonical diagnostics recorded for all three modes")

        if args.phase in ("synth", "all"):
            update_task("synth", "active")
            run_synth()
            update_task("synth", "done", "OOC reports and resource summary recorded for all three modes")

        source_hashes(root)
        print("MORPHOLOGY_WORK_PACKAGE_CHECKS_FINISHED", flush=True)
    finally:
        if session_id:
            try:
                client.tool("vivado_stop", session_id=session_id)
            except Exception as exc:  # Preserve the main failure and raw MCP transcript.
                print("VIVADO_STOP_WARNING=" + str(exc), flush=True)
        client.close()


if __name__ == "__main__":
    main()
