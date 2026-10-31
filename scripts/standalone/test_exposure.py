from dataclasses import dataclass

import pytest

try:
    from scripts.standalone.exposure import ExposureController, V4L2CtlBackend
except ModuleNotFoundError:
    ExposureController = None


@dataclass
class Info:
    minimum: int = 1
    maximum: int = 100
    step: int = 2
    current: int = 10
    unit_us: int = 100
    manual_supported: bool = True


class Backend:
    def __init__(self, info=None):
        self.info = info or Info()
        self.value = self.info.current
        self.manual_calls = 0
        self.fail_set = False

    def query(self):
        return self.info

    def get(self):
        return self.value

    def set_manual(self):
        self.manual_calls += 1
        if not self.info.manual_supported:
            raise RuntimeError("manual exposure unsupported")

    def set(self, value):
        if self.fail_set:
            raise RuntimeError("write failed")
        self.value = value


def test_exposure_uses_real_query_range_step_and_readback_in_milliseconds():
    assert ExposureController is not None, "V4L2 exposure control is not implemented"
    backend = Backend()
    exposure = ExposureController(backend)
    assert exposure.status() == {"minimum_ms": 0.1, "maximum_ms": 10.0,
                                 "current_ms": 1.0, "step_ms": 0.2}
    assert exposure.exposure_up() == 2.1
    assert exposure.exposure_down() == 1.1


def test_exposure_clamps_to_legal_range_and_readback():
    assert ExposureController is not None, "V4L2 exposure control is not implemented"
    backend = Backend(Info(minimum=3, maximum=11, step=4, current=11))
    exposure = ExposureController(backend)
    assert exposure.exposure_up() == 1.1
    assert backend.value == 11
    backend.value = 3
    assert exposure.exposure_down() == .3
    assert backend.value == 3


def test_manual_mode_is_checked_and_write_failure_does_not_fake_success():
    assert ExposureController is not None, "V4L2 exposure control is not implemented"
    backend = Backend(Info(manual_supported=False))
    exposure = ExposureController(backend)
    with pytest.raises(RuntimeError, match="unsupported"):
        exposure.exposure_up()
    assert backend.manual_calls == 0
    backend = Backend()
    backend.fail_set = True
    exposure = ExposureController(backend)
    with pytest.raises(RuntimeError, match="write failed"):
        exposure.exposure_up()


def test_v4l2_backend_prefers_board_control_names_and_reads_back(monkeypatch):
    calls = []
    listing = ("auto_exposure (menu) : min=0 max=3 step=1 value=1\n"
               "\t 0: Auto Mode\n\t 1: Manual Mode\n"
               "exposure_time_absolute (int) : min=50 max=10000 step=1 value=50\n")

    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def run(args, **kwargs):
        calls.append(args)
        if "--list-ctrls-menus" in args:
            return Result(listing)
        if "--get-ctrl=exposure_time_absolute" in args:
            return Result("exposure_time_absolute: 50\n")
        if "--get-ctrl=auto_exposure" in args:
            return Result("auto_exposure: 1\n")
        return Result("")

    monkeypatch.setattr("scripts.standalone.exposure.subprocess.run", run)
    backend = V4L2CtlBackend("/dev/video0")
    assert backend.query().minimum == 50
    assert backend.query().maximum == 10000
    backend.set_manual()
    backend.set(60)
    assert any("--set-ctrl=auto_exposure=1" in c for c in calls)
    assert any("--set-ctrl=exposure_time_absolute=60" in c for c in calls)


def test_v4l2_backend_rejects_device_without_manual_menu(monkeypatch):
    listing = ("auto_exposure (menu) : min=0 max=3 step=1 value=0\n"
               "\t 0: Auto Mode\n\t 2: Aperture Priority\n"
               "exposure_time_absolute (int) : min=50 max=10000 step=1 value=50\n")

    class Result:
        stdout = listing

    monkeypatch.setattr("scripts.standalone.exposure.subprocess.run", lambda *a, **k: Result())
    backend = V4L2CtlBackend()
    backend.query()
    with pytest.raises(RuntimeError, match="unsupported"):
        backend.set_manual()


def test_v4l2_commands_use_a_finite_timeout(monkeypatch):
    captured = {}

    class Result:
        stdout = ""

    def run(args, **kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr("scripts.standalone.exposure.subprocess.run", run)
    V4L2CtlBackend(timeout=.5)._run("--list-ctrls-menus")
    assert captured["timeout"] == .5
