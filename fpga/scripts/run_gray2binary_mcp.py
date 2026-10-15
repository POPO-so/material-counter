"""Run the Gray8-to-Binary8 regression through the installed Ross MCP server.

The existing RGB-to-Gray runner owns the project's MCP stdio transport. This
runner imports that Ross client, uses a fresh Vivado state directory, and keeps
the full JSON-RPC transcript and per-run XSim logs in a new evidence folder.
"""
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
from run_rgb2gray_mcp import Ross  # Reuse the existing, tested MCP stdio client.

THRESHOLDS = (0, 128, 255)
READY_MODES = (0, 1)
CHAINS = (0, 1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_python(command: list[str]) -> None:
    print("PYTHON: " + subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=REPO, check=True)


def prepare_vectors(root: Path, supplied: Path | None,
                    supplied_rgb: Path | None) -> Path:
    if supplied is not None:
        vectors = supplied.resolve()
        if not (vectors / "manifest.json").is_file():
            raise FileNotFoundError(f"binary vector manifest missing: {vectors}")
        return vectors

    rgb_vectors = supplied_rgb.resolve() if supplied_rgb is not None else root / "rgb2gray-vectors"
    if supplied_rgb is None:
        run_python([
            sys.executable,
            str(REPO / "python" / "refmodel" / "rgb2gray_vectors.py"),
            "--out", str(rgb_vectors),
        ])
    vectors = root / "vectors"
    run_python([
        sys.executable,
        str(REPO / "python" / "refmodel" / "gray2binary_vectors.py"),
        "--rgb-vectors", str(rgb_vectors),
        "--out", str(vectors),
    ])
    return vectors


def copy_xsim_logs(project_root: Path, destination: Path) -> tuple[list[Path], str]:
    simdir = project_root / "gray2binary_pkg.sim" / "sim_1" / "behav" / "xsim"
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
        raise RuntimeError(f"canonical lint row count {len(rows)} differs from Vivado total {expected_count}")
    for line_number, line in enumerate(rows, 1):
        if len(line.split(",")) != 7:
            raise RuntimeError(f"lint CSV row {line_number} does not have seven canonical fields: {line!r}")
    print(f"CANONICAL_LINT_ROWS={len(rows)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("red", "verify", "lint", "synth", "all"), default="all")
    parser.add_argument("--vectors", type=Path,
                        help="existing Gray-to-Binary vector directory; otherwise generate it")
    parser.add_argument("--rgb-vectors", type=Path,
                        help="existing WO-0006 vectors; otherwise generate with the existing script")
    parser.add_argument("--out", type=Path,
                        help="new evidence directory (must not already exist)")
    args = parser.parse_args()

    if args.out is None:
        root = REPO / "data" / "results" / ("gray2binary-" + time.strftime("%Y%m%d-%H%M%S"))
    else:
        root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    print("EVIDENCE_DIR=" + str(root), flush=True)

    vectors = None
    if args.phase in ("verify", "all"):
        vectors = prepare_vectors(root, args.vectors, args.rgb_vectors)
        manifest = json.loads((vectors / "manifest.json").read_text(encoding="utf-8"))
    else:
        manifest = None

    client = Ross(root)
    sid = None
    project = root / "project"
    project_posix = project.as_posix()
    repo_posix = REPO.as_posix()

    def execute(command: str, allow_error: bool = False) -> dict:
        result = client.tool("vivado_execute", session_id=sid,
                             command=command, verbosity="full")
        if result.get("status") == "running" or result.get("error_type") == "STILL_RUNNING_DETACHED":
            for _ in range(20):
                time.sleep(30)
                status = client.tool("vivado_status", session_id=sid, action="session")
                if status.get("is_command_running") is False:
                    if status.get("last_completed_code") != 0 and not allow_error:
                        raise RuntimeError("Detached Vivado command failed: " + str(status))
                    return status
            raise RuntimeError("Vivado handoff did not complete within 10 minutes")
        failed = result.get("exit_code", 0) != 0 or result.get("isError")
        if "SKIPPED (duplicate)" in result.get("output", ""):
            raise RuntimeError("MCP duplicate protection skipped command: " + command)
        if failed and not allow_error:
            raise RuntimeError("Vivado command failed: " + str(result))
        return result

    def setup_project() -> None:
        execute("version -short")
        execute(f"create_project gray2binary_pkg {{{project_posix}}} -part xc7z020clg400-1")
        execute(
            f"set_property target_simulator XSim [current_project]; "
            f"add_files {{{repo_posix}/fpga/rtl/axis_stream_skeleton.v}}; "
            f"add_files -fileset sim_1 {{{repo_posix}/fpga/sim/tb_axis_stream_skeleton.sv}}; "
            "set_property top tb_axis_stream_skeleton [get_filesets sim_1]; "
            "set_property xsim.simulate.runtime 10us [get_filesets sim_1]; "
            "update_compile_order -fileset sources_1; update_compile_order -fileset sim_1"
        )

    def update_task(task_id: str, status: str, summary: str | None = None) -> None:
        arguments = {"operation": "update", "session_id": sid,
                     "id": task_id, "status": status}
        if summary is not None:
            arguments["summary"] = summary
        client.tool("vivado_todos", **arguments)

    def isolate_binary_synthesis_design() -> None:
        gray_binary_path = f"{repo_posix}/fpga/rtl/axis_gray2binary.v"
        skeleton_path = f"{repo_posix}/fpga/rtl/axis_stream_skeleton.v"
        execute(
            f"set binary_files [get_files -quiet {{{gray_binary_path}}}]; "
            f"if {{[llength $binary_files] == 0}} {{"
            f"add_files {{{gray_binary_path}}}"
            "}; "
            f"set skeleton_files [get_files -quiet {{{skeleton_path}}}]; "
            f"if {{[llength $skeleton_files] != 0}} {{remove_files $skeleton_files}}; "
            "set_property top axis_gray2binary [get_filesets sources_1]; "
            "update_compile_order -fileset sources_1; puts {GRAY2BINARY_ISOLATED_SYNTH_SOURCE}"
        )

    def run_skeleton_baseline() -> None:
        execute("launch_simulation -simset sim_1 -mode behavioral; puts {SKELETON_BASELINE}")
        _, baseline_text = copy_xsim_logs(project, root / "sim-skeleton")
        if "PASS: in=8 out=8" not in baseline_text or "Fatal:" in baseline_text or "ERROR:" in baseline_text:
            raise RuntimeError("Existing stream skeleton regression failed")
        execute("close_sim -force; puts {CLOSED_SKELETON}")

    def add_binary_testbench() -> None:
        execute(
            f"add_files {{{repo_posix}/fpga/rtl/axis_gray2binary.v}}; "
            f"add_files -fileset sim_1 {{{repo_posix}/fpga/rtl/axis_rgb2gray.v}}; "
            f"add_files -fileset sim_1 {{{repo_posix}/fpga/sim/tb_axis_gray2binary.sv}}; "
            "set_property top tb_axis_gray2binary [get_filesets sim_1]; "
            "update_compile_order -fileset sim_1; puts {GRAY2BINARY_TB_SETUP}"
        )

    def run_simulation(threshold: int, chain: int, mode: int,
                       vector_dir: Path, expected_path: Path) -> None:
        assert manifest is not None
        output_path = root / f"output-th{threshold}-chain{chain}-mode{mode}.hex"
        vector_posix = vector_dir.as_posix()
        output_posix = output_path.as_posix()
        simset = "[get_filesets sim_1]"
        execute(
            f"set_property generic {{THRESHOLD={threshold} CHAIN={chain}}} {simset}; "
            f"set_property xsim.elaborate.xelab.more_options {{}} {simset}; "
            f"set_property xsim.simulate.xsim.more_options "
            f"{{ -testplusarg N={manifest['beats']} -testplusarg READY_MODE={mode} "
            f"-testplusarg RGB={vector_posix}/{manifest['rgb_input']['path']} "
            f"-testplusarg GRAY={vector_posix}/{manifest['gray_input']['path']} "
            f"-testplusarg EXPECTED={expected_path.as_posix()} "
            f"-testplusarg OUTPUT={output_posix}}} {simset}; "
            f"set_property xsim.simulate.runtime 30ms {simset}"
        )
        execute(
            f"launch_simulation -simset sim_1 -mode behavioral; "
            f"puts {{GRAY2BINARY_T{threshold}_C{chain}_M{mode}}}"
        )
        _, text = copy_xsim_logs(project, root / f"sim-th{threshold}-chain{chain}-mode{mode}")
        marker = f"TEST_PASS threshold={threshold} chain={chain} mode={mode}"
        if marker not in text or "Fatal:" in text or "ERROR:" in text:
            raise RuntimeError(f"Simulation failed PASS gate for threshold={threshold}, chain={chain}, mode={mode}")
        execute(f"close_sim -force; puts {{CLOSED_T{threshold}_C{chain}_M{mode}}}")

    def run_invalid_threshold(threshold: int, vector_dir: Path, expected_path: Path) -> None:
        assert manifest is not None
        output_path = root / f"invalid-threshold-{threshold}.hex"
        vector_posix = vector_dir.as_posix()
        simset = "[get_filesets sim_1]"
        execute(
            f"set_property generic {{THRESHOLD={threshold} CHAIN=0}} {simset}; "
            f"set_property xsim.elaborate.xelab.more_options {{}} {simset}; "
            f"set_property xsim.simulate.xsim.more_options "
            f"{{ -testplusarg N={manifest['beats']} -testplusarg READY_MODE=0 "
            f"-testplusarg RGB={vector_posix}/{manifest['rgb_input']['path']} "
            f"-testplusarg GRAY={vector_posix}/{manifest['gray_input']['path']} "
            f"-testplusarg EXPECTED={expected_path.as_posix()} "
            f"-testplusarg OUTPUT={output_path.as_posix()}}} {simset}; "
            f"set_property xsim.simulate.runtime 1us {simset}"
        )
        result = execute(
            f"launch_simulation -simset sim_1 -mode behavioral; puts {{INVALID_THRESHOLD_{threshold}}}",
            allow_error=True,
        )
        (root / f"invalid-threshold-{threshold}-result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        try:
            _, text = copy_xsim_logs(project, root / f"sim-invalid-threshold-{threshold}")
        except RuntimeError:
            text = json.dumps(result)
        if "THRESHOLD must be in [0,255]" not in text:
            raise RuntimeError(f"RTL did not reject invalid THRESHOLD={threshold} with its contract assertion")
        if "TEST_PASS" in text:
            raise RuntimeError(f"invalid THRESHOLD={threshold} unexpectedly reached TEST_PASS")
        execute(f"close_sim -force; puts {{CLOSED_INVALID_THRESHOLD_{threshold}}}", allow_error=True)

    def run_red_evidence() -> None:
        # Deliberately omit axis_gray2binary.v from this fresh project's source set.
        execute(
            f"add_files -fileset sim_1 {{{repo_posix}/fpga/sim/tb_axis_gray2binary.sv}}; "
            "set_property top tb_axis_gray2binary [get_filesets sim_1]; "
            "update_compile_order -fileset sim_1"
        )
        result = execute(
            "launch_simulation -simset sim_1 -mode behavioral; puts {GRAY2BINARY_RED}",
            allow_error=True,
        )
        (root / "red-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        try:
            _, text = copy_xsim_logs(project, root / "sim-red")
        except RuntimeError:
            text = json.dumps(result)
        if "axis_gray2binary" not in text or not any(
                signature in text for signature in ("VRFC 10-2063", "module not found", "design unit")):
            raise RuntimeError("RED run did not produce an identifiable missing-axis_gray2binary error")
        print("GRAY2BINARY_RED_EXPECTED_MISSING_MODULE", flush=True)

    try:
        started = client.tool("vivado_start", working_dir=str(root),
                              session_type="general", gui_mode=False)
        sid = started.get("session_id")
        if not sid:
            raise RuntimeError("Ross did not return a Vivado session_id: " + str(started))
        todo_tasks = [{"id": "setup", "content": "Create a fresh project and check the existing stream skeleton"}]
        if args.phase == "red":
            todo_tasks.append({"id": "red", "content": "Capture expected missing-module RED evidence"})
        else:
            if args.phase in ("verify", "all"):
                todo_tasks.append({"id": "verify", "content": "Run XSim matrix and independent Python checks"})
            if args.phase in ("lint", "all"):
                todo_tasks.append({"id": "lint", "content": "Run Vivado RTL lint and canonicalize its report"})
            if args.phase in ("synth", "all"):
                todo_tasks.append({"id": "synth", "content": "Synthesize the standalone threshold stage and record resources"})
        client.tool("vivado_todos", operation="plan", session_id=sid,
                    title="WO-0007 Gray8 to Binary8 verification", tasks=todo_tasks)
        update_task("setup", "active")
        setup_project()
        update_task("setup", "done", "Fresh Vivado project created and existing stream skeleton added")

        if args.phase == "red":
            update_task("red", "active")
            run_red_evidence()
            update_task("red", "done", "Missing axis_gray2binary module produced the expected RED elaboration failure")
            return

        if args.phase in ("verify", "all"):
            update_task("verify", "active")
            run_skeleton_baseline()
            add_binary_testbench()
            for threshold in THRESHOLDS:
                expected_path = vectors / f"threshold-{threshold}" / "expected_binary.hex"
                for chain in CHAINS:
                    for mode in READY_MODES:
                        run_simulation(threshold, chain, mode, vectors, expected_path)
                if threshold == 0:
                    run_invalid_threshold(-1, vectors, expected_path)
                if threshold == 255:
                    run_invalid_threshold(256, vectors, expected_path)

            run_python([
                sys.executable,
                str(REPO / "python" / "refmodel" / "check_gray2binary_output.py"),
                "--vectors", str(vectors), "--run", str(root),
            ])
            update_task("verify", "done", "12 XSim matrix runs and independent Python pixel/sideband checks completed")

        if args.phase in ("lint", "synth", "all"):
            isolate_binary_synthesis_design()

        if args.phase in ("lint", "all"):
            update_task("lint", "active")
            execute(
                "set_property top axis_gray2binary [get_filesets sources_1]; "
                "update_compile_order -fileset sources_1; puts {GRAY2BINARY_LINT_SETUP}"
            )
            lint_result = execute(
                f"synth_design -top axis_gray2binary -part xc7z020clg400-1 -lint -file {{{root.as_posix()}/lint.rpt}}"
            )
            (root / "lint-tool-result.json").write_text(
                json.dumps(lint_result, indent=2), encoding="utf-8"
            )
            lint_text = json.dumps(lint_result, ensure_ascii=False)
            count_match = re.search(
                r"Total\s+of\s+(\d+)\s+linter\s+message(?:\(s\)|s)?\s+generated",
                lint_text, flags=re.IGNORECASE,
            )
            if not count_match:
                raise RuntimeError("Vivado lint response did not expose its authoritative total message count")
            authoritative_lint_count = int(count_match.group(1))
            parser_script = REPO / ".agents" / "skills" / "vivado-rtl-lint" / "parse_lint_report.py"
            run_python([
                sys.executable, str(parser_script),
                str(root / "lint.rpt"), str(root / "lint-parser-raw.csv"),
            ])
            raw_rows = [line for line in (root / "lint-parser-raw.csv").read_text(
                encoding="utf-8", errors="replace").splitlines() if line.strip()]
            if authoritative_lint_count == 0:
                normalized_json = root / "lint-authoritative-zero.json"
                normalized_json.write_text(json.dumps({
                    "cols": ["id", "description", "name", "info", "module", "file", "line"],
                    "rows": [],
                }, indent=2), encoding="utf-8")
                run_python([
                    sys.executable, str(parser_script),
                    str(normalized_json), str(root / "lint-parsed.csv"),
                ])
                canonical_rows = [line for line in (root / "lint-parsed.csv").read_text(
                    encoding="utf-8", errors="replace").splitlines() if line.strip()]
                if canonical_rows:
                    raise RuntimeError("official parser did not preserve authoritative zero-row lint result")
                (root / "lint-normalization.json").write_text(json.dumps({
                    "authoritative_vivado_total": authoritative_lint_count,
                    "raw_parser_nonempty_rows": len(raw_rows),
                    "canonical_nonempty_rows": len(canonical_rows),
                    "normalization": "empty JSON rows passed through official parser because the ASCII empty-table parser consumed trailing prose",
                }, indent=2), encoding="utf-8")
            else:
                shutil.copy2(root / "lint-parser-raw.csv", root / "lint-parsed.csv")
                if len(raw_rows) != authoritative_lint_count:
                    raise RuntimeError(
                        f"canonical lint row count {len(raw_rows)} differs from Vivado total {authoritative_lint_count}"
                    )
            update_task("lint", "done", f"Vivado total {authoritative_lint_count}; canonical lint CSV preserved")
            validate_lint_csv(root / "lint-parsed.csv", authoritative_lint_count)
            if args.phase == "all":
                update_task("synth", "active")

        if args.phase in ("synth", "all"):
            # Synthesize only the independent Gray8->Binary8 design under test.
            if args.phase == "synth":
                update_task("synth", "active")
            execute(
                "set_property top axis_gray2binary [get_filesets sources_1]; "
                "update_compile_order -fileset sources_1; puts {GRAY2BINARY_SYNTH_SETUP}"
            )
            execute("synth_design -top axis_gray2binary -part xc7z020clg400-1 -mode out_of_context")
            execute("create_clock -name aclk -period 10 [get_ports aclk]")
            execute(
                f"report_utilization -file {{{root.as_posix()}/utilization.rpt}}; "
                f"report_timing_summary -file {{{root.as_posix()}/timing-summary.rpt}}"
            )
            client.tool("vivado_log_messages", session_id=sid, filter="all", max_messages=500)
            utilization = (root / "utilization.rpt").read_text(encoding="utf-8", errors="replace")
            resource_patterns = {
                "lut": r"\|\s*Slice LUTs\*?\s*\|\s*([\d,]+)",
                "ff": r"\|\s*Slice Registers\s*\|\s*([\d,]+)",
                "dsp": r"\|\s*DSPs\s*\|\s*([\d,]+)",
                "bram_tiles": r"\|\s*Block RAM Tile\s*\|\s*([\d,]+)",
            }
            resources = {}
            for name, pattern in resource_patterns.items():
                match = re.search(pattern, utilization)
                if not match:
                    raise RuntimeError(f"could not extract {name} from utilization.rpt")
                resources[name] = int(match.group(1).replace(",", ""))
            (root / "resource-summary.json").write_text(
                json.dumps({"top": "axis_gray2binary", "threshold_parameter": 128,
                            "part": "xc7z020clg400-1", "resources": resources,
                            "timing_constraint": "10ns clock only; no I/O delays"}, indent=2),
                encoding="utf-8",
            )
            update_task("synth", "done", "Independent OOC synthesis and LUT/FF/DSP/BRAM resource summary completed")

        sources = [
            REPO / "fpga" / "rtl" / "axis_gray2binary.v",
            REPO / "fpga" / "rtl" / "axis_rgb2gray.v",
            REPO / "fpga" / "sim" / "tb_axis_gray2binary.sv",
            REPO / "python" / "refmodel" / "gray2binary_vectors.py",
            REPO / "python" / "refmodel" / "check_gray2binary_output.py",
            Path(__file__).resolve(),
        ]
        hashes = {str(path.relative_to(REPO)): sha256(path) for path in sources if path.is_file()}
        (root / "source-hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
        print("GRAY2BINARY_WORK_PACKAGE_CHECKS_FINISHED", flush=True)
    finally:
        if sid:
            try:
                client.tool("vivado_stop", session_id=sid)
            except Exception as exc:  # Preserve the primary failure and transcript.
                print("VIVADO_STOP_WARNING=" + str(exc), flush=True)
        client.close()


if __name__ == "__main__":
    main()
