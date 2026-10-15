"""Run the bounded AXI4-Stream true-crop package through the Ross MCP server."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "fpga" / "scripts"))
from run_rgb2gray_mcp import Ross


# Each row is an intentionally bounded compile-time geometry. The two small
# frame sizes include both non-power-of-two and power-of-two exclusive bounds.
CASE_TABLE = [
    {"name": "small-full", "profile": "coordinate", "frame": (6, 4),
     "roi": (0, 0, 6, 4), "runs": ((0, 0), (1, 1))},
    {"name": "small-center", "profile": "coordinate", "frame": (6, 4),
     "roi": (2, 1, 5, 3), "runs": ((0, 1), (1, 0), (1, 1))},
    {"name": "small-bottom-right-pixel", "profile": "coordinate", "frame": (6, 4),
     "roi": (5, 3, 6, 4), "runs": ((0, 0), (1, 1))},
    {"name": "small-empty-width", "profile": "coordinate", "frame": (6, 4),
     "roi": (3, 1, 3, 3), "runs": ((0, 0), (1, 1))},
    {"name": "small-empty-height", "profile": "coordinate", "frame": (6, 4),
     "roi": (2, 2, 4, 2), "runs": ((0, 1),)},
    {"name": "one-by-one-full", "profile": "coordinate", "frame": (1, 1),
     "roi": (0, 0, 1, 1), "runs": ((0, 0),)},
    {"name": "power-two-full", "profile": "coordinate", "frame": (2, 2),
     "roi": (0, 0, 2, 2), "runs": ((0, 0),)},
    {"name": "h3-full", "profile": "h3", "frame": (320, 240),
     "roi": (0, 0, 320, 240), "runs": ((0, 0), (0, 1), (1, 0), (1, 1))},
    {"name": "h3-center", "profile": "h3", "frame": (320, 240),
     "roi": (80, 60, 240, 180), "runs": ((0, 0), (0, 1), (1, 0), (1, 1))},
    {"name": "h3-left-border-column", "profile": "h3", "frame": (320, 240),
     "roi": (0, 0, 1, 240), "runs": ((0, 0), (0, 1), (1, 0), (1, 1))},
]
INVALID_PARAMETER_CASES = [
    ("zero-frame-width", {"FRAME_WIDTH": 0, "FRAME_HEIGHT": 4,
                           "ROI_X0": 0, "ROI_Y0": 0, "ROI_X1": 0, "ROI_Y1": 4}),
    ("zero-frame-height", {"FRAME_WIDTH": 6, "FRAME_HEIGHT": 0,
                            "ROI_X0": 0, "ROI_Y0": 0, "ROI_X1": 6, "ROI_Y1": 0}),
    ("negative-x0", {"FRAME_WIDTH": 6, "FRAME_HEIGHT": 4,
                      "ROI_X0": -1, "ROI_Y0": 0, "ROI_X1": 2, "ROI_Y1": 2}),
    ("negative-y0", {"FRAME_WIDTH": 6, "FRAME_HEIGHT": 4,
                      "ROI_X0": 0, "ROI_Y0": -1, "ROI_X1": 2, "ROI_Y1": 2}),
    ("reverse-x", {"FRAME_WIDTH": 6, "FRAME_HEIGHT": 4,
                    "ROI_X0": 3, "ROI_Y0": 0, "ROI_X1": 2, "ROI_Y1": 2}),
    ("reverse-y", {"FRAME_WIDTH": 6, "FRAME_HEIGHT": 4,
                    "ROI_X0": 0, "ROI_Y0": 3, "ROI_X1": 2, "ROI_Y1": 2}),
    ("x1-past-frame", {"FRAME_WIDTH": 6, "FRAME_HEIGHT": 4,
                        "ROI_X0": 0, "ROI_Y0": 0, "ROI_X1": 7, "ROI_Y1": 2}),
    ("y1-past-frame", {"FRAME_WIDTH": 6, "FRAME_HEIGHT": 4,
                        "ROI_X0": 0, "ROI_Y0": 0, "ROI_X1": 2, "ROI_Y1": 5}),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_python(command: list[str]) -> None:
    print("PYTHON: " + subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=REPO, check=True)


def copy_xsim_logs(project: Path, destination: Path) -> tuple[list[Path], str]:
    simdir = project / "roi_crop_pkg.sim" / "sim_1" / "behav" / "xsim"
    logs = sorted(simdir.glob("*.log"))
    if not logs:
        raise RuntimeError(f"XSim did not produce any logs under {simdir}")
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    texts = []
    for log in logs:
        target = destination / log.name
        shutil.copy2(log, target)
        copied.append(target)
        texts.append(target.read_text(encoding="utf-8", errors="replace"))
    return copied, "\n".join(texts)


def validate_lint_csv(path: Path, expected_count: int) -> None:
    rows = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()]
    if len(rows) != expected_count:
        raise RuntimeError(
            f"canonical lint row count {len(rows)} differs from Vivado total {expected_count}"
        )
    for line_number, line in enumerate(rows, 1):
        if len(line.split(",")) != 7:
            raise RuntimeError(f"lint CSV row {line_number} does not have seven canonical fields")
    print(f"CANONICAL_LINT_ROWS={len(rows)}", flush=True)


def selected_cases(names: list[str] | None) -> list[dict]:
    if not names:
        return CASE_TABLE
    wanted = set(names)
    available = {case["name"] for case in CASE_TABLE}
    unknown = wanted - available
    if unknown:
        raise ValueError("unknown case name(s): " + ", ".join(sorted(unknown)))
    return [case for case in CASE_TABLE if case["name"] in wanted]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("red", "verify", "lint", "synth", "all"), default="all")
    parser.add_argument("--out", type=Path,
                        help="new evidence directory; defaults to data/results/roi-crop-TIMESTAMP")
    parser.add_argument("--case", action="append", dest="cases",
                        help="run one case from the built-in matrix; may be repeated")
    parser.add_argument("--skip-invalid-parameters", action="store_true",
                        help="omit the eight expected-failure parameter guard simulations")
    parser.add_argument("--no-random-color-frame", action="store_true",
                        help="omit the optional seeded RGB color frame after the three H3 frames")
    args = parser.parse_args()

    if args.out is None:
        root = REPO / "data" / "results" / ("roi-crop-" + time.strftime("%Y%m%d-%H%M%S"))
    else:
        root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    print("EVIDENCE_DIR=" + str(root), flush=True)

    client = Ross(root)
    session_id = None
    project = root / "project"
    project_posix = project.as_posix()
    repo_posix = REPO.as_posix()

    def execute(command: str, allow_error: bool = False) -> dict:
        result = client.tool("vivado_execute", session_id=session_id,
                             command=command, verbosity="full")
        if result.get("status") == "running" or result.get("error_type") == "STILL_RUNNING_DETACHED":
            for _ in range(20):
                time.sleep(30)
                status = client.tool("vivado_status", session_id=session_id, action="session")
                if status.get("is_command_running") is False:
                    if status.get("last_completed_code") != 0 and not allow_error:
                        raise RuntimeError("detached Vivado command failed: " + str(status))
                    return status
            raise RuntimeError("Vivado handoff did not complete within 10 minutes")
        failed = result.get("exit_code", 0) != 0 or result.get("isError")
        if "SKIPPED (duplicate)" in result.get("output", ""):
            raise RuntimeError("MCP duplicate protection skipped command: " + command)
        if failed and not allow_error:
            raise RuntimeError("Vivado command failed: " + str(result))
        return result

    def update_task(task_id: str, status: str, summary: str | None = None) -> None:
        values = {"operation": "update", "session_id": session_id,
                  "id": task_id, "status": status}
        if summary is not None:
            values["summary"] = summary
        client.tool("vivado_todos", **values)

    def setup_project(include_roi: bool = True) -> None:
        execute("version -short")
        execute(f"create_project roi_crop_pkg {{{project_posix}}} -part xc7z020clg400-1")
        roi_add = f"add_files {{{repo_posix}/fpga/rtl/axis_roi_crop.v}}; " if include_roi else ""
        execute(
            "set_property target_simulator XSim [current_project]; "
            f"{roi_add}"
            f"add_files {{{repo_posix}/fpga/rtl/axis_rgb2gray.v}}; "
            f"add_files {{{repo_posix}/fpga/rtl/axis_gray2binary.v}}; "
            f"add_files -fileset sim_1 {{{repo_posix}/fpga/sim/tb_axis_roi_crop.sv}}; "
            "set_property top tb_axis_roi_crop [get_filesets sim_1]; "
            "set_property xsim.simulate.runtime 100ms [get_filesets sim_1]; "
            "update_compile_order -fileset sources_1; update_compile_order -fileset sim_1"
        )

    def run_red_evidence() -> None:
        # Keep the ROI RTL out of this fresh project and expect the TB reference
        # to be the compiler's missing design unit.
        result = execute(
            "launch_simulation -simset sim_1 -mode behavioral; puts {ROI_CROP_RED}",
            allow_error=True,
        )
        (root / "red-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        try:
            _, log_text = copy_xsim_logs(project, root / "sim-red")
        except RuntimeError:
            log_text = json.dumps(result)
        if "axis_roi_crop" not in log_text or not any(
                marker in log_text for marker in ("VRFC 10-2063", "module not found", "design unit")):
            raise RuntimeError("RED run did not show the expected missing axis_roi_crop design unit")
        print("ROI_CROP_RED_EXPECTED_MISSING_MODULE", flush=True)

    def prepare_case_vectors(case: dict, vector_root: Path, random_seed: int) -> Path:
        vector_dir = vector_root / case["name"]
        width, height = case["frame"]
        x0, y0, x1, y1 = case["roi"]
        command = [
            sys.executable, str(REPO / "python" / "refmodel" / "roi_crop_vectors.py"),
            "--out", str(vector_dir), "--profile", case["profile"],
            "--frame-width", str(width), "--frame-height", str(height),
            "--roi-x0", str(x0), "--roi-y0", str(y0),
            "--roi-x1", str(x1), "--roi-y1", str(y1),
            "--random-seed", str(random_seed),
        ]
        if args.no_random_color_frame:
            command.append("--no-random-color-frame")
        run_python(command)
        return vector_dir

    def run_simulation(case: dict, vector_dir: Path,
                       chain: int, mode: int, seed_value: int) -> None:
        width, height = case["frame"]
        x0, y0, x1, y1 = case["roi"]
        manifest = json.loads((vector_dir / "manifest.json").read_text(encoding="utf-8"))
        run_dir = root / "runs" / case["name"] / f"chain{chain}-mode{mode}"
        run_dir.mkdir(parents=True, exist_ok=False)
        expected_path = vector_dir / f"expected_chain{chain}.hex"
        output_path = run_dir / "output_roi.hex"
        options = (
            f" -testplusarg N={manifest['input_beats']}"
            f" -testplusarg EXPECTED_N={manifest['output_beats']}"
            f" -testplusarg READY_MODE={mode} -testplusarg SEED={seed_value}"
            f" -testplusarg RGB={vector_dir.as_posix()}/vectors_rgb.hex"
            f" -testplusarg GRAY={vector_dir.as_posix()}/vectors_gray.hex"
            f" -testplusarg INPUT_TAGS={vector_dir.as_posix()}/input_tags.hex"
            f" -testplusarg EXPECTED={expected_path.as_posix()}"
            f" -testplusarg OUTPUT={output_path.as_posix()}"
        )
        execute(
            f"set_property generic {{FRAME_WIDTH={width} FRAME_HEIGHT={height} "
            f"ROI_X0={x0} ROI_Y0={y0} ROI_X1={x1} ROI_Y1={y1} "
            f"CHAIN={chain} THRESHOLD=128}} [get_filesets sim_1]; "
            f"set_property xsim.elaborate.xelab.more_options {{}} [get_filesets sim_1]; "
            f"set_property xsim.simulate.xsim.more_options {{{options}}} [get_filesets sim_1]; "
            "set_property xsim.simulate.runtime 100ms [get_filesets sim_1]"
        )
        marker = (
            f"TEST_PASS roi={x0},{y0},{x1},{y1} frame={width}x{height} "
            f"chain={chain} mode={mode}"
        )
        execute(
            f"launch_simulation -simset sim_1 -mode behavioral; "
            f"puts {{ROI_CROP_{case['name']}_C{chain}_M{mode}}}"
        )
        logs, text = copy_xsim_logs(project, run_dir / "xsim-logs")
        if marker not in text or "Fatal:" in text or "ERROR:" in text:
            raise RuntimeError(
                f"ROI simulation failed PASS gate for {case['name']} chain={chain} mode={mode}; "
                f"logs={logs}"
            )
        execute(f"close_sim -force; puts {{CLOSED_{case['name']}_C{chain}_M{mode}}}")
        run_python([
            sys.executable,
            str(REPO / "python" / "refmodel" / "check_roi_crop_output.py"),
            "--vectors", str(vector_dir), "--run", str(run_dir),
            "--chain", str(chain), "--mode", str(mode),
        ])
        (run_dir / "simulation-result.json").write_text(json.dumps({
            "case": case["name"], "frame": [width, height], "roi": [x0, y0, x1, y1],
            "chain": chain, "mode": mode, "seed": seed_value,
            "input_beats": manifest["input_beats"],
            "expected_output_beats": manifest["output_beats"],
            "pass_marker": marker, "xsim_logs": [str(path.relative_to(run_dir)) for path in logs],
        }, indent=2), encoding="utf-8")

    def run_parameter_guard(case_name: str, generics: dict[str, int]) -> None:
        generic_text = " ".join(f"{key}={value}" for key, value in generics.items())
        result = execute(
            f"set_property top axis_roi_crop [get_filesets sim_1]; "
            f"set_property generic {{{generic_text}}} [get_filesets sim_1]; "
            "update_compile_order -fileset sim_1; "
            "set_property xsim.simulate.runtime 1us [get_filesets sim_1]"
        )
        result = execute(
            f"launch_simulation -simset sim_1 -mode behavioral; puts {{ROI_PARAM_{case_name}}}",
            allow_error=True,
        )
        (root / f"parameter-{case_name}-result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        try:
            logs, log_text = copy_xsim_logs(project, root / f"parameter-{case_name}-logs")
        except RuntimeError:
            logs, log_text = [], json.dumps(result)
        if "ROI parameter contract invalid:" not in log_text:
            raise RuntimeError(
                f"DUT did not reject {case_name} with its parameter contract fatal; "
                f"logs={logs}"
            )
        if "ROI_PARAM_" + case_name in log_text and "ROI parameter contract invalid:" not in log_text:
            raise RuntimeError(f"invalid parameter case {case_name} unexpectedly completed")
        execute(f"close_sim -force; puts {{CLOSED_ROI_PARAM_{case_name}}}", allow_error=True)

    def run_invalid_parameter_matrix() -> None:
        for case_name, generics in INVALID_PARAMETER_CASES:
            run_parameter_guard(case_name, generics)
        execute(
            "set_property top tb_axis_roi_crop [get_filesets sim_1]; "
            "update_compile_order -fileset sim_1; puts {RESTORED_ROI_TB_TOP}"
        )

    def isolate_roi_synthesis_top(phase_tag: str) -> None:
        roi_path = f"{repo_posix}/fpga/rtl/axis_roi_crop.v"
        remove = (
            f"set other_files [get_files -quiet {{{repo_posix}/fpga/rtl/axis_rgb2gray.v "
            f"{repo_posix}/fpga/rtl/axis_gray2binary.v}}]; "
            "if {[llength $other_files] != 0} {remove_files $other_files}; "
        )
        execute(
            f"set roi_files [get_files -quiet {{{roi_path}}}]; "
            "if {[llength $roi_files] == 0} {"
            f"add_files {{{roi_path}}}"
            "}; " + remove +
            "set_property top axis_roi_crop [get_filesets sources_1]; "
            "update_compile_order -fileset sources_1; "
            f"puts {{ROI_CROP_ISOLATED_SYNTH_SOURCE_{phase_tag.upper()}}}"
        )

    def lint_roi() -> None:
        isolate_roi_synthesis_top("lint")
        result = execute(
            f"synth_design -top axis_roi_crop -part xc7z020clg400-1 -lint "
            f"-file {{{root.as_posix()}/lint.rpt}}"
        )
        (root / "lint-tool-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        count_match = re.search(
            r"Total\s+of\s+(\d+)\s+linter\s+message(?:\(s\)|s)?\s+generated",
            json.dumps(result), flags=re.IGNORECASE,
        )
        if not count_match:
            raise RuntimeError("Vivado lint response did not expose its authoritative total message count")
        authoritative_count = int(count_match.group(1))
        parser_script = REPO / ".agents" / "skills" / "vivado-rtl-lint" / "parse_lint_report.py"
        run_python([sys.executable, str(parser_script), str(root / "lint.rpt"),
                    str(root / "lint-parser-raw.csv")])
        raw_rows = [line for line in (root / "lint-parser-raw.csv").read_text(
            encoding="utf-8", errors="replace").splitlines() if line.strip()]
        if authoritative_count == 0:
            normalized_json = root / "lint-authoritative-zero.json"
            normalized_json.write_text(json.dumps({
                "cols": ["id", "description", "name", "info", "module", "file", "line"],
                "rows": [],
            }, indent=2), encoding="utf-8")
            run_python([sys.executable, str(parser_script), str(normalized_json),
                        str(root / "lint-parsed.csv")])
            canonical_rows = [line for line in (root / "lint-parsed.csv").read_text(
                encoding="utf-8", errors="replace").splitlines() if line.strip()]
            if canonical_rows:
                raise RuntimeError("official parser did not preserve authoritative zero-row lint result")
            (root / "lint-normalization.json").write_text(json.dumps({
                "authoritative_vivado_total": 0,
                "raw_parser_nonempty_rows": len(raw_rows),
                "canonical_nonempty_rows": 0,
                "normalization": "empty JSON rows passed through the official parser because the ASCII empty-table parser consumed trailing prose",
            }, indent=2), encoding="utf-8")
        else:
            shutil.copy2(root / "lint-parser-raw.csv", root / "lint-parsed.csv")
            if len(raw_rows) != authoritative_count:
                raise RuntimeError(
                    f"canonical lint row count {len(raw_rows)} differs from Vivado total {authoritative_count}"
                )
        validate_lint_csv(root / "lint-parsed.csv", authoritative_count)
        update_task("lint", "done", f"Vivado reported {authoritative_count} lint messages")

    def synthesize_roi_center() -> None:
        isolate_roi_synthesis_top("synth")
        execute(
            "synth_design -top axis_roi_crop -part xc7z020clg400-1 "
            "-mode out_of_context -generic {FRAME_WIDTH=320 FRAME_HEIGHT=240 "
            "ROI_X0=80 ROI_Y0=60 ROI_X1=240 ROI_Y1=180}"
        )
        execute("create_clock -name aclk -period 10 [get_ports aclk]")
        execute(
            f"report_utilization -file {{{root.as_posix()}/utilization.rpt}}; "
            f"report_timing_summary -file {{{root.as_posix()}/timing-summary.rpt}}"
        )
        client.tool("vivado_log_messages", session_id=session_id, filter="all", max_messages=500)
        utilization = (root / "utilization.rpt").read_text(encoding="utf-8", errors="replace")
        patterns = {
            "lut": r"\|\s*Slice LUTs\*?\s*\|\s*([\d,]+)",
            "ff": r"\|\s*Slice Registers\s*\|\s*([\d,]+)",
            "dsp": r"\|\s*DSPs\s*\|\s*([\d,]+)",
            "bram_tiles": r"\|\s*Block RAM Tile\s*\|\s*([\d,]+)",
        }
        resources = {}
        for name, pattern in patterns.items():
            match = re.search(pattern, utilization)
            if not match:
                raise RuntimeError(f"could not extract {name} from utilization.rpt")
            resources[name] = int(match.group(1).replace(",", ""))
        (root / "resource-summary.json").write_text(json.dumps({
            "top": "axis_roi_crop", "parameters": {
                "FRAME_WIDTH": 320, "FRAME_HEIGHT": 240,
                "ROI_X0": 80, "ROI_Y0": 60, "ROI_X1": 240, "ROI_Y1": 180,
            },
            "part": "xc7z020clg400-1", "resources": resources,
            "timing_constraint": "10ns clock only; no I/O delays",
        }, indent=2), encoding="utf-8")
        update_task("synth", "done", "Standalone non-full-frame center ROI OOC synthesis completed")

    try:
        started = client.tool("vivado_start", working_dir=str(root),
                              session_type="general", gui_mode=False)
        session_id = started.get("session_id")
        if not session_id:
            raise RuntimeError("Ross did not return a Vivado session_id: " + str(started))

        todo_tasks = [{"id": "setup", "content": "Create an isolated Vivado project for the ROI crop work package"}]
        if args.phase == "red":
            todo_tasks.append({"id": "red", "content": "Capture the expected missing-module RED evidence"})
        else:
            if args.phase in ("verify", "all"):
                todo_tasks.append({"id": "verify", "content": "Generate coordinate/H3 vectors and run the ROI crop matrix"})
            if args.phase in ("lint", "all"):
                todo_tasks.append({"id": "lint", "content": "Run Vivado lint and canonicalize its report"})
            if args.phase in ("synth", "all"):
                todo_tasks.append({"id": "synth", "content": "Synthesize the standalone center ROI and record resources"})
        client.tool("vivado_todos", operation="plan", session_id=session_id,
                    title="WO-0008 true ROI crop verification", tasks=todo_tasks)
        update_task("setup", "active")
        setup_project(include_roi=(args.phase != "red"))
        update_task("setup", "done", "Fresh project and ROI verification sources configured")

        if args.phase == "red":
            update_task("red", "active")
            run_red_evidence()
            update_task("red", "done", "Missing axis_roi_crop design unit produced the expected RED failure")
            return

        if args.phase in ("verify", "all"):
            update_task("verify", "active")
            case_list = selected_cases(args.cases)
            matrix_runs = sum(len(case["runs"]) for case in case_list)
            (root / "case-matrix.json").write_text(json.dumps({
                "valid_simulation_runs": matrix_runs,
                "configured_cases": case_list,
                "invalid_parameter_cases": [] if args.skip_invalid_parameters else
                    [name for name, _ in INVALID_PARAMETER_CASES],
            }, indent=2), encoding="utf-8")
            vector_root = root / "vectors"
            vector_root.mkdir()
            run_index = 0
            for case in case_list:
                vector_dir = prepare_case_vectors(case, vector_root, 20261012 + run_index * 101)
                for chain, mode in case["runs"]:
                    run_simulation(case, vector_dir, chain, mode,
                                   20261012 + run_index * 101 + chain * 17 + mode)
                    run_index += 1
            if not args.skip_invalid_parameters:
                run_invalid_parameter_matrix()
            update_task("verify", "done", f"{matrix_runs} valid XSim cases and parameter guard checks completed")

        if args.phase in ("lint", "all"):
            update_task("lint", "active")
            lint_roi()

        if args.phase in ("synth", "all"):
            update_task("synth", "active")
            synthesize_roi_center()

        source_paths = [
            REPO / "fpga" / "rtl" / "axis_roi_crop.v",
            REPO / "fpga" / "rtl" / "axis_rgb2gray.v",
            REPO / "fpga" / "rtl" / "axis_gray2binary.v",
            REPO / "fpga" / "sim" / "tb_axis_roi_crop.sv",
            REPO / "python" / "refmodel" / "roi_crop_vectors.py",
            REPO / "python" / "refmodel" / "check_roi_crop_output.py",
            Path(__file__).resolve(),
        ]
        hashes = {str(path.relative_to(REPO)): sha256(path)
                  for path in source_paths if path.is_file()}
        (root / "source-hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
        print("ROI_CROP_WORK_PACKAGE_CHECKS_FINISHED", flush=True)
    finally:
        if session_id:
            try:
                client.tool("vivado_stop", session_id=session_id)
            except Exception as exc:
                print("VIVADO_STOP_WARNING=" + str(exc), flush=True)
        client.close()


if __name__ == "__main__":
    main()
