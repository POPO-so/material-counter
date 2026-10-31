"""Runtime policy for optional full per-frame diagnostics."""


class DiagnosticPolicy:
    MODES = ("normal", "diagnostic")

    def __init__(self, mode="normal"):
        if mode not in self.MODES:
            raise ValueError("mode must be 'normal' or 'diagnostic'")
        self.mode = mode
        self.transitions = []

    @property
    def trace_enabled(self):
        return self.mode == "diagnostic"

    def set_mode(self, mode, *, frame_id=None, timestamp_ns=None):
        if mode not in self.MODES:
            raise ValueError("mode must be 'normal' or 'diagnostic'")
        if mode == self.mode:
            return None
        entry = {"from": self.mode, "to": mode, "frame_id": frame_id,
                 "timestamp_ns": timestamp_ns}
        self.transitions.append(entry)
        self.mode = mode
        return dict(entry)
