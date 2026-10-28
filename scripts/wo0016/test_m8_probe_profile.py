import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m8_probe_profile as probe  # noqa: E402
import run_stream_counter as runner  # noqa: E402

BOOT_ID = "9552f3cf-5fb6-4b97-b5c3-5640e708a598"
CAPTURE_PATH = os.path.abspath(os.path.join("m8-probe", "attempt-01"))
SHA = {name: str(index) * 64
       for index, name in enumerate(("manifest", "bit", "hwh", "stage1", "counter",
                                     "runner", "module", "gate"), start=1)}

PINNED = dict(
    max_frames=100,
    expected_count=0,
    material_label="empty_belt_memory_probe",
    stabilization_delay_s=3.0,
    operator_prearm_delay_s=15.0,
    counting_profile="lighting-calibrated",
    area_filter="output-roi",
    reference_area=111.0,
    lighting_threshold=100,
    capture_write_mode="bounded_stream",
)


def _identity(**changes):
    values = dict(
        boot_id=BOOT_ID,
        manifest_sha256=SHA["manifest"],
        bit_sha256=SHA["bit"],
        hwh_sha256=SHA["hwh"],
        stage1_receipt_sha256=SHA["stage1"],
        attempt_id="probe-1",
        counting_profile="lighting-calibrated",
        area_filter="output-roi",
        reference_area=111.0,
        lighting_threshold=100,
        capture_path=CAPTURE_PATH,
        evidence_path=str(Path(CAPTURE_PATH).parent / 'probe-result.json'),
        capture_max_bytes=33554432,
        runtime_sha256={"run_stream_counter.py": SHA["runner"]},
        counter_sha256=SHA["counter"],
        runner_sha256=SHA["runner"],
        probe_module_sha256=SHA["module"],
        gate_launcher_sha256=SHA["gate"],
        execution_contract=runner.probe_execution_contract(runner.canonical_probe_options()),
        diagnostic_runtime_sha256={"m8_probe_profile.py": SHA["module"],
                                   "m8_probe_runtime.py": SHA["module"],
                                   "m8_memory_sampler.py": SHA["module"]},
    )
    values.update(changes)
    return probe.probe_permit_identity(**values)


def _write_permit(path, record, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, sort_keys=True)
    return path


def _args(**changes):
    values = dict(
        mode="board",
        wo0017_profile=None,
        no_consume_guard=False,
        physical_calibration=False,
        capture_dir=CAPTURE_PATH,
        attempt_id="probe-1",
        expected_boot_id=BOOT_ID,
        stage1_receipt="/var/lib/wo0015-s2mm/overlay-only-pass.json",
        stage1_receipt_sha256=SHA["stage1"],
        ground_truth_count=PINNED["expected_count"],
        material_label=PINNED["material_label"],
        stabilization_delay_s=PINNED["stabilization_delay_s"],
        operator_prearm_delay_s=PINNED["operator_prearm_delay_s"],
        counting_profile=PINNED["counting_profile"],
        lighting_area_filter=PINNED["area_filter"],
        reference_area=PINNED["reference_area"],
        lighting_threshold=PINNED["lighting_threshold"],
        capture_write_mode=PINNED["capture_write_mode"],
        max_frames=PINNED["max_frames"],
    )
    values.update(changes)
    return SimpleNamespace(**values)


def _plan(**changes):
    values = dict(boot_id=BOOT_ID)
    values.update(changes)
    return SimpleNamespace(**values)


def test_identity_is_pinned_to_a_non_acceptance_measurement():
    identity = _identity()
    assert identity["work_order"] == "M8-PROBE"
    assert identity["stage"] == "m8-probe100-memory"
    assert identity["max_frames"] == 100
    assert identity["expected_count"] == 0
    assert identity["material_label"] == "empty_belt_memory_probe"
    assert identity["capture_config"]["write_mode"] == "bounded_stream"
    assert identity["capture_config"]["ring_depth"] == 4
    assert "not a counting or acceptance run" in identity["purpose"]
    assert identity["attempt_id"] == "probe-1"
    assert identity["runtime_sha256"] == {"run_stream_counter.py": SHA["runner"]}


@pytest.mark.parametrize("field,value", [
    ("counting_profile", "full-width-mixed"),
    ("area_filter", "full-frame"),
    ("reference_area", 100.0),
    ("lighting_threshold", 90),
])
def test_identity_rejects_measurement_parameter_drift(field, value):
    with pytest.raises(ValueError):
        _identity(**{field: value})


@pytest.mark.parametrize("field,value", [
    ("manifest_sha256", "A" * 64),
    ("bit_sha256", "short"),
    ("runtime_sha256", {}),
    ("runtime_sha256", {"run_stream_counter.py": "x"}),
])
def test_identity_rejects_unpinned_hashes(field, value):
    with pytest.raises(ValueError):
        _identity(**{field: value})


@pytest.mark.parametrize("attempt_id", ["", "has space", "x" * 33, "../escape"])
def test_identity_rejects_invalid_attempt_ids(attempt_id):
    with pytest.raises(ValueError):
        _identity(attempt_id=attempt_id)


@pytest.mark.parametrize("field,value", [
    ("capture_path", "relative/path"),
    ("capture_max_bytes", 0),
])
def test_identity_rejects_unbounded_capture(field, value):
    with pytest.raises(ValueError):
        _identity(**{field: value})


def test_pinned_arguments_accept_only_the_reviewed_contract():
    probe.validate_pinned_arguments(**PINNED)


@pytest.mark.parametrize("field,value", [
    ("max_frames", 600),
    ("expected_count", 1),
    ("material_label", "dense_m8"),
    ("stabilization_delay_s", 1.0),
    ("operator_prearm_delay_s", 0.0),
    ("counting_profile", "full-width-mixed"),
    ("area_filter", "full-frame"),
    ("reference_area", 100.0),
    ("lighting_threshold", 90),
    ("capture_write_mode", "deferred"),
    ("capture_write_mode", "streaming_queue"),
    ("capture_write_mode", "auto"),
])
def test_pinned_arguments_reject_any_deviation(field, value):
    arguments = dict(PINNED)
    arguments[field] = value
    with pytest.raises(ValueError):
        probe.validate_pinned_arguments(**arguments)


def test_guard_paths_are_one_per_boot_and_separate_from_formal_guards():
    first = probe.probe_guard_paths(BOOT_ID)
    second = probe.probe_guard_paths("11111111-2222-3333-4444-555555555555")
    assert first["permit"] != second["permit"]
    assert first["permit"] != first["consumed"]
    assert first["base"] == first["permit"].parent
    assert "m8probe" in first["base"].parts
    assert "wo0016" not in first["base"].parts
    assert "wo0017" not in first["base"].parts
    with pytest.raises(ValueError):
        probe.probe_guard_paths("")


def test_permit_round_trip_accepts_the_exact_identity(tmp_path):
    identity = _identity()
    path = _write_permit(tmp_path / "permits" / "permit.json",
                         {**identity, "permit_status": "AUTHORIZED", "consumed": False})
    expected_uid = os.getuid() if os.name == "posix" else 0
    result = runner.validate_probe_permit(path, identity, expected_uid=expected_uid)
    assert result["permit"] == {**identity, "permit_status": "AUTHORIZED", "consumed": False}
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("mutation", [
    {"permit_status": "PENDING"},
    {"consumed": True},
    {"stage": "m8-batch-1"},
    {"max_frames": 600},
    {"threshold": 90},
    {"attempt_id": "probe-2"},
])
def test_permit_rejects_any_identity_or_status_drift(tmp_path, mutation):
    identity = _identity()
    record = {**identity, "permit_status": "AUTHORIZED", "consumed": False}
    record.update(mutation)
    path = _write_permit(tmp_path / "permits" / "permit.json", record)
    expected_uid = os.getuid() if os.name == "posix" else 0
    with pytest.raises(RuntimeError):
        runner.validate_probe_permit(path, identity, expected_uid=expected_uid)


def test_permit_must_be_a_private_regular_file(tmp_path):
    identity = _identity()
    path = _write_permit(tmp_path / "permits" / "permit.json",
                         {**identity, "permit_status": "AUTHORIZED", "consumed": False})
    link = tmp_path / "permits" / "link.json"
    try:
        link.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    expected_uid = os.getuid() if os.name == "posix" else 0
    with pytest.raises(RuntimeError):
        runner.validate_probe_permit(link, identity, expected_uid=expected_uid)


def test_receipt_must_be_the_pinned_path_and_digest(tmp_path):
    receipt = tmp_path / "overlay-only-pass.json"
    receipt.write_bytes(b'{"status": "OVERLAY_ONLY_PASS"}\n')
    digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    runner.validate_probe_receipt(receipt, digest, pinned=receipt)
    with pytest.raises(ValueError):
        runner.validate_probe_receipt(receipt, "0" * 64, pinned=receipt)
    substitute = tmp_path / "other.json"
    substitute.write_bytes(receipt.read_bytes())
    with pytest.raises(ValueError):
        runner.validate_probe_receipt(substitute, digest, pinned=receipt)


def _patch_options_checks(monkeypatch, tmp_path, calls):
    def record_receipt(path, sha256, **kwargs):
        calls["receipt"] = (str(path), sha256)

    monkeypatch.setattr(runner, "validate_probe_receipt", record_receipt)
    paths = {"base": tmp_path / "m8probe", "permit": tmp_path / "m8probe" / "permit.json",
             "consumed": tmp_path / "m8probe" / "consumed.json"}
    paths["base"].mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(probe, "probe_guard_paths", lambda boot_id: paths)
    return paths


def test_options_accept_only_the_reviewed_probe_request(tmp_path, monkeypatch):
    calls = {}
    paths = _patch_options_checks(monkeypatch, tmp_path, calls)
    assert runner.validate_probe_options(_args(), _plan()) == paths
    assert calls["receipt"] == ("/var/lib/wo0015-s2mm/overlay-only-pass.json",
                                SHA["stage1"])


@pytest.mark.parametrize("changes", [
    {"mode": "mock"},
    {"wo0017_profile": "b1"},
    {"wo0017_profile": "b2"},
    {"no_consume_guard": True},
    {"physical_calibration": True},
    {"capture_dir": None},
    {"attempt_id": None},
    {"expected_boot_id": None},
    {"stage1_receipt": None},
    {"stage1_receipt_sha256": None},
])
def test_options_refuse_formal_flags_and_missing_identity(tmp_path, monkeypatch, changes):
    _patch_options_checks(monkeypatch, tmp_path, {})
    with pytest.raises(ValueError):
        runner.validate_probe_options(_args(**changes), _plan())


def test_options_refuse_a_different_approved_boot(tmp_path, monkeypatch):
    _patch_options_checks(monkeypatch, tmp_path, {})
    with pytest.raises(ValueError):
        runner.validate_probe_options(_args(), _plan(boot_id="another-boot"))


def test_options_refuse_an_already_consumed_probe(tmp_path, monkeypatch):
    paths = _patch_options_checks(monkeypatch, tmp_path, {})
    paths["consumed"].write_text('{"status": "CONSUMED"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        runner.validate_probe_options(_args(), _plan())


def test_diagnostic_probe_is_board_only_and_off_by_default():
    assert runner._parser().parse_args([]).diagnostic_probe is False
    assert runner._parser().parse_args(["--diagnostic-probe"]).diagnostic_probe is True
    assert runner.main(["--diagnostic-probe"]) == 2


def test_probe_module_is_outside_the_formal_runtime_identity():
    names = runner.formal_runtime_sha256()
    assert "m8_probe_profile.py" not in names
    assert "issue_m8_probe_permit.py" not in names
    assert "run_stream_counter.py" in names


def test_probe_identity_binds_outputs_and_all_diagnostic_module_hashes():
    first = _identity()
    changed = _identity(evidence_path=str(Path(CAPTURE_PATH).parent / 'different-result.json'))
    assert first != changed
    assert first['schema'] == 2
    assert first['memory_samples_path'].endswith('.memory-samples.jsonl')
    altered = dict(first['diagnostic_runtime_sha256'])
    altered['m8_memory_sampler.py'] = 'a' * 64
    assert _identity(diagnostic_runtime_sha256=altered) != first
    with pytest.raises(ValueError, match='diagnostic runtime'):
        _identity(diagnostic_runtime_sha256={'m8_probe_profile.py': SHA['module']})


def test_probe_identity_rejects_evidence_inside_capture_directory():
    with pytest.raises(ValueError, match='outside'):
        _identity(evidence_path=str(Path(CAPTURE_PATH) / 'result.json'))
