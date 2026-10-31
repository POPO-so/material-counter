"""V4L2 exposure control with queried range and verified readback."""
from dataclasses import dataclass
import math
import re
import subprocess


@dataclass(frozen=True)
class ExposureInfo:
    minimum: int
    maximum: int
    step: int
    current: int
    unit_us: int = 100
    manual_supported: bool = True


class V4L2CtlBackend:
    """Small v4l2-ctl adapter for the standard V4L2 exposure controls."""
    _FIELDS = re.compile(r"\b(min|max|step|value)=(-?\d+)")

    def __init__(self, device="/dev/video0", command="v4l2-ctl", timeout=1.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("v4l2-ctl timeout must be finite and positive")
        self.device, self.command = device, command
        self.timeout = timeout
        self._manual_supported = False
        self._auto_name = None
        self._exposure_name = None

    def _run(self, *args):
        return subprocess.run([self.command, "-d", self.device, *args],
                              check=True, capture_output=True, text=True,
                              timeout=self.timeout).stdout

    def _controls(self):
        return self._run("--list-ctrls-menus")

    def _control(self, name):
        lines = self._controls().splitlines()
        for index, line in enumerate(lines):
            if re.search(r"\b" + re.escape(name) + r"\s*\(", line):
                body = line
                cursor = index + 1
                while cursor < len(lines) and lines[cursor].startswith("\t"):
                    body += " " + lines[cursor]
                    cursor += 1
                return body
        raise RuntimeError(f"V4L2 control unavailable: {name}")

    def query(self):
        text = self._controls()
        lines = text.splitlines()
        exp_name = next((name for name in ("exposure_time_absolute", "exposure_absolute")
                         if any(re.search(r"\b" + re.escape(name) + r"\s*\(", line)
                                for line in lines)), None)
        auto_name = next((name for name in ("auto_exposure", "exposure_auto")
                          if any(re.search(r"\b" + re.escape(name) + r"\s*\(", line)
                                 for line in lines)), None)
        exp_line = next((line for line in lines if exp_name and
                         re.search(r"\b" + re.escape(exp_name) + r"\s*\(", line)), None)
        if exp_line is None:
            raise RuntimeError("V4L2 exposure control unavailable")
        vals = dict((k, int(v)) for k, v in self._FIELDS.findall(exp_line))
        if not all(k in vals for k in ("min", "max", "step")) or vals["step"] <= 0:
            raise RuntimeError("invalid V4L2 exposure range")
        auto_line = next((line for line in lines if auto_name and
                          re.search(r"\b" + re.escape(auto_name) + r"\s*\(", line)), "")
        menu_text = auto_line
        index = lines.index(auto_line) + 1 if auto_line else len(lines)
        while index < len(lines) and lines[index].startswith("\t"):
            menu_text += "\n" + lines[index]
            index += 1
        manual_match = re.search(r"\b(\d+)\s*:\s*Manual", menu_text, re.IGNORECASE)
        manual = manual_match is not None
        self._manual_value = int(manual_match.group(1)) if manual_match else None
        self._manual_supported = manual
        self._auto_name, self._exposure_name = auto_name, exp_name
        current = self.get()
        return ExposureInfo(vals["min"], vals["max"], vals["step"], current,
                            manual_supported=manual)

    def get(self):
        if self._exposure_name is None:
            self.query()
        output = self._run(f"--get-ctrl={self._exposure_name}")
        match = re.search(r"\bvalue=(-?\d+)", output)
        if match:
            return int(match.group(1))
        try:
            return int(output.strip().split(":")[-1].strip())
        except ValueError as exc:
            raise RuntimeError("unable to parse V4L2 exposure readback") from exc

    def _get_named_value(self, name):
        output = self._run(f"--get-ctrl={name}")
        match = re.search(r":\s*(-?\d+)", output)
        if not match:
            raise RuntimeError(f"unable to parse V4L2 readback for {name}")
        return int(match.group(1))

    def set_manual(self):
        if not self._manual_supported:
            raise RuntimeError("manual exposure mode unsupported by this device")
        if self._auto_name is None:
            self.query()
        self._run(f"--set-ctrl={self._auto_name}={self._manual_value}")
        actual = self._get_named_value(self._auto_name)
        if actual != self._manual_value:
            raise RuntimeError("V4L2 manual exposure mode readback failed")

    def set(self, value):
        if self._exposure_name is None:
            self.query()
        self._run(f"--set-ctrl={self._exposure_name}={value}")


class ExposureController:
    def __init__(self, backend=None):
        self.backend = backend or V4L2CtlBackend()

    def status(self):
        info = self.backend.query()
        current = self.backend.get()
        return {"minimum_ms": info.minimum * info.unit_us / 1000,
                "maximum_ms": info.maximum * info.unit_us / 1000,
                "current_ms": current * info.unit_us / 1000,
                "step_ms": info.step * info.unit_us / 1000}

    def _adjust(self, direction):
        info = self.backend.query()
        if not info.manual_supported:
            raise RuntimeError("manual exposure mode unsupported by this device")
        self.backend.set_manual()
        current = self.backend.get()
        target = max(info.minimum, min(info.maximum, current + direction * 10))
        # Align to the device's legal step grid, measured from queryctrl minimum.
        offset = target - info.minimum
        target = info.minimum + ((offset + info.step // 2) // info.step) * info.step
        target = max(info.minimum, min(info.maximum, target))
        self.backend.set(target)
        actual = self.backend.get()
        if actual < info.minimum or actual > info.maximum:
            raise RuntimeError("V4L2 returned exposure outside queried range")
        return actual * info.unit_us / 1000

    def exposure_up(self):
        return self._adjust(1)

    def exposure_down(self):
        return self._adjust(-1)
