from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET


SCRIPT = Path(__file__).with_name("joint_prepare.py")
SPEC = importlib.util.spec_from_file_location("joint_prepare_timing_xdc_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
joint_prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(joint_prepare)


def test_timing_override_leaves_clock_gating_to_project_tcl_and_binds_pinned_file():
    resolver = getattr(joint_prepare, "resolve_timing_xdc_override", None)
    assert callable(resolver), "joint_prepare must bind the reviewed HDMI timing XDC"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        baseline = root / "baseline"
        source = baseline / "scripts/standalone/display_timing.xdc"
        source.parent.mkdir(parents=True)
        source.write_text("# pinned timing source\n", encoding="utf-8")
        candidate = root / "display_timing.xdc"
        candidate.write_text(
            "# retain the 5x MMCM clock propagated through SerialClk\n"
            "set_false_path -from [get_ports {btns_4bits_tri_i[*]}] -to [get_pins button_first/D]\n"
            "set_false_path -to [get_ports {hdmi_out_hpd[0]}]\n", encoding="utf-8")
        pinned = joint_prepare.entry(source)

        binding = resolver(candidate, baseline, [pinned])

        assert binding["candidate"]["sha256"] == joint_prepare.sha(candidate)
        assert binding["target_relative_path"] == "scripts/standalone/display_timing.xdc"
        candidate.write_text(candidate.read_text(encoding="utf-8") +
                              "delete_clocks [get_clocks SerialClk]\n", encoding="utf-8")
        try:
            resolver(candidate, baseline, [pinned])
        except ValueError as exc:
            assert "project Tcl" in str(exc)
        else:
            raise AssertionError("an unsupported clock deletion command was accepted")
        candidate.write_text(candidate.read_text(encoding="utf-8").replace(
            "delete_clocks [get_clocks SerialClk]\n", ""), encoding="utf-8")
        candidate.write_text(candidate.read_text(encoding="utf-8") +
                              "create_generated_clock -name duplicate [get_ports clk]\n",
                              encoding="utf-8")
        try:
            resolver(candidate, baseline, [pinned])
        except ValueError as exc:
            assert "generated clock" in str(exc)
        else:
            raise AssertionError("a duplicate generated clock was accepted")


def test_joint_build_disables_only_the_duplicate_rgb2dvi_clock_xdc():
    build = Path(__file__).with_name("joint_build.tcl").read_text(encoding="utf-8")
    assert "*rgb2dvi_clocks.xdc" in build
    assert "set_property USED_IN_SYNTHESIS false $xdc" in build
    assert "set_property USED_IN_IMPLEMENTATION false $xdc" in build
    assert build.count("joint::disable_duplicate_rgb2dvi_clock_xdc}") == 2


def test_staged_timing_xdc_is_registered_in_the_xpr_constraints_fileset():
    register = getattr(joint_prepare, "register_timing_xdc_in_project", None)
    assert callable(register), "staging must register the timing override before live-file verification"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        xpr = root / "display.xpr"
        xpr.write_text(
            '<Project><FileSets><FileSet Name="constrs_1" Type="Constrs">'
            '<Filter Type="Constrs"/><Config/></FileSet></FileSets></Project>',
            encoding="utf-8")
        xdc = root / "display_timing.xdc"
        xdc.write_text("set_false_path -to [get_ports hdmi_out_hpd[0]]\n", encoding="utf-8")
        project = ET.parse(xpr)

        register(project, xdc)
        fileset = project.find(".//FileSet[@Name='constrs_1']")
        assert fileset is not None
        entries = fileset.findall("File")
        assert len(entries) == 1
        assert entries[0].attrib["Path"] == xdc.absolute().as_posix()
        assert [attr.attrib["Val"] for attr in entries[0].findall("FileInfo/Attr")
                if attr.attrib.get("Name") == "UsedIn"] == ["synthesis", "implementation"]
        children = list(fileset)
        assert children.index(entries[0]) < next(i for i, child in enumerate(children)
                                                  if child.tag == "Config")

        register(project, xdc)
        assert len(fileset.findall("File")) == 1, "re-registering must not duplicate the XDC"
