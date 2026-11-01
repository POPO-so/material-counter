"""Debounced board controls; commands are applied at a frame boundary."""
from dataclasses import dataclass
import math


_UNSET = object()


class ButtonDecoder:
    """Decode active-high BTN0..BTN3 bits; BTN3 is currently unassigned."""

    def __init__(self, debounce=0.03):
        if debounce < 0:
            raise ValueError("debounce must be nonnegative")
        self.debounce = debounce
        self._candidate = self._stable = 0
        self._changed = 0.0
        self._last_time = None
        self._sw0_state = None
        self.next_deadline = None

    def _check(self, bits, now):
        if type(bits) is not int or not 0 <= bits <= 15:
            raise ValueError("GPIO must be four bits")
        if not math.isfinite(now) or (self._last_time is not None and now < self._last_time):
            raise ValueError("button timestamps must be monotonic")

    @staticmethod
    def _validate_sw0_state(sw0_state):
        if sw0_state is not None and type(sw0_state) is not bool:
            raise ValueError("SW0 state must be bool or None")

    def update(self, bits, now, sw0_state=None):
        self._check(bits, now)
        self._validate_sw0_state(sw0_state)
        self._last_time = now
        self._sw0_state = sw0_state
        if bits != self._candidate:
            self._candidate, self._changed = bits, now
        return self._advance(now)

    def advance(self, now, sw0_state=_UNSET):
        """Advance timers without a GPIO read, at the advertised deadline."""
        self._check(self._candidate, now)
        if sw0_state is not _UNSET:
            self._validate_sw0_state(sw0_state)
            self._sw0_state = sw0_state
        self._last_time = now
        return self._advance(now)

    def _advance(self, now):
        commands = []
        if self._candidate != self._stable and now - self._changed >= self.debounce:
            before = self._stable
            self._stable = self._candidate
            pressed = self._stable & ~before
            if pressed & 1:
                commands.append("clear")
            if pressed & 2:
                commands.append("mode")
            if pressed & 4:
                # Do not guess the exposure direction when SW0 is unreadable.
                if self._sw0_state is True:
                    commands.append("exposure_up")
                elif self._sw0_state is False:
                    commands.append("exposure_down")
        self.next_deadline = None
        if self._candidate != self._stable:
            self.next_deadline = self._changed + self.debounce
        return tuple(commands)


@dataclass
class ControlState:
    paused: bool = False
    binary_background: bool = True
    page: int = 0  # Compatibility field; BTN3 no longer changes pages.
    clear_requests: int = 0
    diagnostic: bool = False

    def apply(self, commands):
        for command in commands:
            if command == "pause":
                self.paused = not self.paused
            elif command == "background":
                self.binary_background = not self.binary_background
            elif command == "page":
                # Ignore legacy replay commands; the display is a single page.
                continue
            elif command == "clear":
                self.clear_requests += 1
            elif command == "mode":
                self.diagnostic = not self.diagnostic
            elif command in ("exposure_up", "exposure_down"):
                # Hardware actions are consumed by the button event owner.
                continue
            else:
                raise ValueError(f"unknown board command: {command}")
