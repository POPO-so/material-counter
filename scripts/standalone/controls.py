"""Debounced board controls; commands are applied at a frame boundary."""
from dataclasses import dataclass
import math


class ButtonDecoder:
    """Decode active-high BTN0..BTN3 bits; deadlines avoid polling while idle."""

    def __init__(self, debounce=0.03, long_press=0.8):
        if debounce < 0 or long_press <= 0:
            raise ValueError("debounce must be nonnegative and long_press positive")
        self.debounce = debounce
        self.long_press = long_press
        self._candidate = self._stable = 0
        self._changed = 0.0
        self._last_time = None
        self._btn2_start = None
        self._btn2_long_sent = False
        self.next_deadline = None

    def _check(self, bits, now):
        if type(bits) is not int or not 0 <= bits <= 15:
            raise ValueError("GPIO must be four bits")
        if not math.isfinite(now) or (self._last_time is not None and now < self._last_time):
            raise ValueError("button timestamps must be monotonic")

    def update(self, bits, now):
        self._check(bits, now)
        self._last_time = now
        if bits != self._candidate:
            self._candidate, self._changed = bits, now
        return self._advance(now)

    def advance(self, now):
        """Advance timers without a GPIO read, at the advertised deadline."""
        self._check(self._candidate, now)
        self._last_time = now
        return self._advance(now)

    def _advance(self, now):
        commands = []
        if self._candidate != self._stable and now - self._changed >= self.debounce:
            before = self._stable
            self._stable = self._candidate
            pressed = self._stable & ~before
            released = before & ~self._stable
            if pressed & 1:
                commands.append("clear")
            if pressed & 2:
                commands.append("mode")
            if pressed & 4:
                self._btn2_start, self._btn2_long_sent = now, False
            if released & 4 and self._btn2_start is not None and not self._btn2_long_sent:
                commands.append("exposure_up")
                self._btn2_start = None
        if (self._stable & 4 and self._btn2_start is not None and
                not self._btn2_long_sent and
                now + 1e-9 >= self._btn2_start + self.long_press):
            commands.append("exposure_down")
            self._btn2_long_sent = True
        self.next_deadline = None
        if self._candidate != self._stable:
            self.next_deadline = self._changed + self.debounce
        if self._stable & 4 and self._btn2_start is not None and not self._btn2_long_sent:
            hold_deadline = self._btn2_start + self.long_press
            if self.next_deadline is None or hold_deadline < self.next_deadline:
                self.next_deadline = hold_deadline
        return tuple(commands)


@dataclass
class ControlState:
    paused: bool = False
    binary_background: bool = True
    page: int = 0
    clear_requests: int = 0
    diagnostic: bool = False

    def apply(self, commands):
        for command in commands:
            if command == "pause":
                self.paused = not self.paused
            elif command == "background":
                self.binary_background = not self.binary_background
            elif command == "page":
                self.page = 1 - self.page
            elif command == "clear":
                self.clear_requests += 1
            elif command == "mode":
                self.diagnostic = not self.diagnostic
            elif command in ("exposure_up", "exposure_down"):
                # Hardware actions are consumed by the button event owner.
                continue
            else:
                raise ValueError(f"unknown board command: {command}")
