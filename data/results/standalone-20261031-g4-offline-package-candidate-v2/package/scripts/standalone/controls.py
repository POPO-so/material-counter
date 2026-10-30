"""Debounced board controls; the consumer applies events at a frame boundary."""
from dataclasses import dataclass
import math


class ButtonDecoder:
    def __init__(self, debounce=0.03, clear_hold=2.0):
        self.debounce = debounce
        self.clear_hold = clear_hold
        self._candidate = self._stable = 0
        self._changed = 0.0
        self._last_time = None
        self._clear_start = None
        self._clear_sent = False

    def update(self, bits, now):
        if type(bits) is not int or not 0 <= bits <= 15:
            raise ValueError("GPIO must be four bits")
        if not math.isfinite(now) or (self._last_time is not None and now < self._last_time):
            raise ValueError("button timestamps must be monotonic")
        self._last_time = now
        if bits != self._candidate:
            self._candidate, self._changed = bits, now
        commands = []
        if bits != self._stable and now - self._changed >= self.debounce:
            pressed = bits & ~self._stable
            self._stable = bits
            for mask, command in ((1, "pause"), (4, "background"), (8, "page")):
                if pressed & mask:
                    commands.append(command)
            if pressed & 2:
                self._clear_start, self._clear_sent = now, False
            if not bits & 2:
                self._clear_start, self._clear_sent = None, False
        if (bits & self._stable & 2 and self._clear_start is not None and
                not self._clear_sent and now - self._clear_start >= self.clear_hold):
            commands.append("clear")
            self._clear_sent = True
        return tuple(commands)


@dataclass
class ControlState:
    paused: bool = False
    binary_background: bool = True
    page: int = 0
    clear_requests: int = 0

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
            else:
                raise ValueError(f"unknown board command: {command}")
