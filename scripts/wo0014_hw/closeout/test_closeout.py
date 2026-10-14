"""Offline closeout worker regressions; these tests never import PYNQ."""
import hashlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import closeout_worker as closeout


def plan(frame_id=1, *, input_checksum=0x1234, binary_checksum=255 * 17):
    return SimpleNamespace(
        frame_id=frame_id,
        input_byte_checksum=input_checksum,
        expected_binary_checksum=binary_checksum,
        packet=b"x" * closeout.base.PACKET_BYTES,
        packet_sha256="a" * 64,
    )


def checker_state(**overrides):
    values = {
        "drain_enable": 1,
        "magic": closeout.base.PACKET_MAGIC,
        "status": 9,
        "error_code": 0,
        "frame_id": 1,
        "packet_done_count": 2,
        "packet_error_count": 0,
        "input_bytes": 2 * closeout.base.PACKET_BYTES,
        "beats": 2 * closeout.base.PACKET_BEATS,
        "tlast_count": 2,
        "first_word": closeout.base.PACKET_MAGIC,
        "last_word": closeout.base.TRAILER_END,
        "input_byte_checksum": 0x1234 + 0x1234,
        "observed_pixels": closeout.base.PIXEL_COUNT,
        "sofs": 1,
        "eols": closeout.base.HEIGHT,
        "binary_checksum": 255 * 17,
        "accepted_output_total": 2 * closeout.base.PIXEL_COUNT,
        "stalled_cycles": 10,
    }
    values.update(overrides)
    return values


def previous_state():
    return checker_state(
        frame_id=0,
        packet_done_count=1,
        input_bytes=closeout.base.PACKET_BYTES,
        beats=closeout.base.PACKET_BEATS,
        tlast_count=1,
        input_byte_checksum=0x1234,
        accepted_output_total=closeout.base.PIXEL_COUNT,
    )


class FakeBuffer(bytearray):
    def __init__(self, size, address):
        super().__init__(size)
        self.device_address = address
        self.flags = SimpleNamespace(c_contiguous=True)
        self.freed = False

    def sync_to_device(self):
        pass

    def freebuffer(self):
        self.freed = True


class FakeMmio:
    def __init__(self, api, regs, *, dma=False):
        self.api, self.regs, self.dma = api, regs, dma
        self.reads, self.writes = [], []

    def read(self, offset):
        self.reads.append(offset)
        return self.regs.get(offset, 0)

    def write(self, offset, value):
        self.writes.append((offset, value))
        self.regs[offset] = value
        if self.dma and offset == 0x28:
            if self.api.complete_frame:
                self.api.advance_checker()
            self.regs[0x04] = 0x1002


class FakeApi:
    class _Numpy:
        uint8 = "uint8"

        @staticmethod
        def frombuffer(packet, dtype):
            return packet

    def __init__(self, checker_regs, *, complete_frame=True, wrong_frame_id=False,
                 wrong_binary_checksum=False):
        self.np = self._Numpy()
        self.checker_regs = checker_regs
        self.complete_frame = complete_frame
        self.wrong_frame_id = wrong_frame_id
        self.wrong_binary_checksum = wrong_binary_checksum
        self.checker = FakeMmio(self, checker_regs)
        self.dma = FakeMmio(self, {0x00: 1, 0x04: 0x1002, 0x18: 0x200000,
                                   0x28: closeout.base.PACKET_BYTES}, dma=True)
        self.buffers = []
        self.next_address = 0x400000
        self.worker = None

    def MMIO(self, address, length):
        if address == 0x40400000:
            return self.dma
        if address == 0x43C20000:
            return self.checker
        raise AssertionError("unexpected MMIO base")

    def allocate(self, **kwargs):
        buffer = FakeBuffer(kwargs["shape"][0], self.next_address)
        self.next_address += 0x100000
        self.buffers.append(buffer)
        return buffer

    def advance_checker(self):
        plan = self.worker.current_frame.plan
        previous = {name: self.checker_regs[offset]
                    for name, offset in closeout.base.CHECKER_REGS.items()}
        values = dict(previous)
        values.update({
            "status": 9,
            "frame_id": 0xDEAD if self.wrong_frame_id else plan.frame_id,
            "packet_done_count": previous["packet_done_count"] + 1,
            "packet_error_count": previous["packet_error_count"],
            "input_bytes": (previous["input_bytes"] + closeout.base.PACKET_BYTES) & 0xFFFFFFFF,
            "beats": (previous["beats"] + closeout.base.PACKET_BEATS) & 0xFFFFFFFF,
            "tlast_count": previous["tlast_count"] + 1,
            "first_word": previous["first_word"] or closeout.base.PACKET_MAGIC,
            "last_word": closeout.base.TRAILER_END,
            "input_byte_checksum": (previous["input_byte_checksum"] +
                                     plan.input_byte_checksum) & 0xFFFFFFFF,
            "observed_pixels": closeout.base.PIXEL_COUNT,
            "sofs": 1,
            "eols": closeout.base.HEIGHT,
            "binary_checksum": plan.expected_binary_checksum +
                               (1 if self.wrong_binary_checksum else 0),
            "accepted_output_total": (previous["accepted_output_total"] +
                                      closeout.base.PIXEL_COUNT) & 0xFFFFFFFF,
            "stalled_cycles": previous["stalled_cycles"] + 1,
        })
        self.checker_regs.clear()
        self.checker_regs.update({closeout.base.CHECKER_REGS[name]: value
                                  for name, value in values.items()})


def make_validated_plan(frame_id, seed, foreground):
    packet = bytes([seed]) * closeout.base.PACKET_BYTES
    return SimpleNamespace(
        frame_id=frame_id,
        packet=packet,
        packet_sha256=hashlib.sha256(packet).hexdigest(),
        input_byte_checksum=(0x1000 + seed) & 0xFFFFFFFF,
        expected_binary_checksum=foreground * 255,
        dma_base=0x40400000,
        checker_base=0x43C20000,
    )


def make_rig(tmp_path, *, complete_frame=True, wrong_frame_id=False,
             wrong_binary_checksum=False, fresh_mode_auth=True):
    base = closeout.base
    initial_plan = make_validated_plan(0, 0, 16)
    frames = tuple(SimpleNamespace(plan=make_validated_plan(n, n, 20 + n),
                                   frame_id=n, foreground_pixels=20 + n)
                   for n in (1, 2, 3))
    profile50 = SimpleNamespace(baseline=initial_plan, bit_sha256=closeout.diagnostic_core.BIT_SHA,
                                hwh_sha256=closeout.diagnostic_core.HWH_SHA, clock_mhz=50)
    profile100 = SimpleNamespace(baseline=initial_plan, bit_sha256="c" * 64,
                                 hwh_sha256="d" * 64, clock_mhz=100)
    config = SimpleNamespace(
        profiles={"50": profile50, "100": profile100},
        frames={"multi50": frames, "multi100": frames, "single100": (), "overlay100": ()},
        manifest_sha256="b" * 64,
        parent_manifest_sha256="a" * 64,
    )
    first = initial_plan
    checker = {
        "drain_enable": 1, "magic": base.PACKET_MAGIC, "status": 9,
        "error_code": 0, "frame_id": 0, "packet_done_count": 1,
        "packet_error_count": 0, "input_bytes": base.PACKET_BYTES,
        "beats": base.PACKET_BEATS, "tlast_count": 1,
        "first_word": base.PACKET_MAGIC, "last_word": base.TRAILER_END,
        "input_byte_checksum": first.input_byte_checksum,
        "observed_pixels": base.PIXEL_COUNT, "sofs": 1, "eols": base.HEIGHT,
        "binary_checksum": first.expected_binary_checksum,
        "accepted_output_total": base.PIXEL_COUNT, "stalled_cycles": 0,
    }
    checker_regs = {base.CHECKER_REGS[name]: value for name, value in checker.items()}
    api = FakeApi(checker_regs, complete_frame=complete_frame,
                  wrong_frame_id=wrong_frame_id,
                  wrong_binary_checksum=wrong_binary_checksum)
    journal = closeout.Journal(tmp_path / "closeout-guard")
    parent = closeout.Journal(tmp_path / "parent-guard")
    mode = "multi50"
    journal.claim("supervision-" + mode + "-consumed", {
        "mode": mode, "manifest_sha256": config.manifest_sha256,
    })
    if not fresh_mode_auth:
        journal.claim("supervision-" + mode + "-pass", {
            "mode": mode, "manifest_sha256": config.manifest_sha256,
        })
    _seed_parent_guard(parent, config.parent_manifest_sha256)
    environment_calls = []

    def environment():
        environment_calls.append(True)
        return {"exclusive": True, "boot_id": closeout.BOOT_ID,
                "loaded_bit_sha256": closeout.diagnostic_core.BIT_SHA}

    worker = closeout.CloseoutWorker(config, mode, api, journal, parent, environment)
    api.worker = worker
    return worker, api, journal, parent, environment_calls


def _seed_parent_guard(parent, manifest_sha):
    boot = closeout.BOOT_ID
    bit = closeout.diagnostic_core.BIT_SHA
    parent.claim("overlay-attempt", {"count": 1, "clock_mhz": 50})
    parent.claim("overlay-receipt", {"schema": 1, "mode": "overlay-only",
                                      "boot_id": boot, "bit_sha256": bit})
    parent.claim("mmio-read-only-consumed", {"mode": "mmio-read-only", "boot_id": boot,
                                             "bit_sha256": bit})
    parent.claim("mmio-receipt", {"schema": 1, "mode": "mmio-read-only",
                                   "boot_id": boot, "bit_sha256": bit})
    parent.claim("dma-once-consumed", {"mode": "dma-once", "boot_id": boot,
                                        "bit_sha256": bit})
    parent.claim("send-attempt", {"count": 1, "bytes": closeout.base.PACKET_BYTES,
                                   "address": 0x400000, "boot_id": boot})
    for mode in ("overlay-only", "mmio-read-only", "dma-once"):
        row = {"mode": mode, "manifest_sha256": manifest_sha}
        parent.claim("supervision-" + mode + "-consumed", row)
        parent.claim("supervision-" + mode + "-pass", row)


class CloseoutWorkerTests(unittest.TestCase):
  def test_multiframe_checker_requires_new_packet_count_not_sticky_idle_status(self):
    before = previous_state()
    stale = checker_state(packet_done_count=before["packet_done_count"])

    with self.assertRaisesRegex(closeout.base.PreflightError, "packet_done_count"):
        closeout.validate_checker_increment(before, stale, plan())


  def test_multiframe_checker_checks_cumulative_inputs_and_per_sof_output_window(self):
    before = previous_state()
    current = checker_state()

    self.assertIsNone(closeout.validate_checker_increment(before, current, plan()))

    bad_output = dict(current, observed_pixels=closeout.base.PIXEL_COUNT + 1)
    with self.assertRaisesRegex(closeout.base.PreflightError, "observed_pixels"):
        closeout.validate_checker_increment(before, bad_output, plan())


  def test_mode_stage_order_has_health_between_each_frame(self):
    stages = closeout.MODE_STAGES["multi50"]
    self.assertEqual(stages[:4], ("ENV", "HEALTH", "MIN_READ", "INIT"))
    self.assertEqual(stages[4:], (
        "SEND", "VERIFY", "RELEASE", "HEALTH",
        "SEND", "VERIFY", "RELEASE", "HEALTH",
        "SEND", "VERIFY", "RELEASE", "POST",
    ))

  def test_three_frame_worker_uses_only_sa_length_and_releases_after_each_golden_match(self):
    with unittest.TestCase().subTest("full fake MMIO lifecycle"):
      import tempfile
      with tempfile.TemporaryDirectory() as temp:
        from pathlib import Path
        worker, api, journal, _parent, _env_calls = make_rig(Path(temp))
        report = {"ping": True, "ssh": True, "linux": True,
                  "boot_id": closeout.BOOT_ID, "new_kernel_warnings": []}
        for stage in closeout.MODE_STAGES["multi50"]:
          result = worker.command(stage, report if stage in ("HEALTH", "POST") else None)
          self.assertTrue(result["ok"], (stage, result))
        self.assertEqual(len(worker.frame_metrics), 3)
        self.assertTrue(all(buffer.freed for buffer in api.buffers))
        self.assertEqual([offset for offset, _ in api.dma.writes], [0x18, 0x28] * 3)
        self.assertEqual(worker.send_attempts, 3)
        self.assertEqual(worker.state, "PASS")
        self.assertIn("before_length.dmasr", worker.raw)
        self.assertIn("before_length.dmacr", worker.raw)
        for frame_id in (1, 2, 3):
          self.assertTrue(journal.exists(f"send-multi50-frame-{frame_id}-consumed"))
          self.assertTrue(journal.exists(f"frame-multi50-{frame_id}-pass"))

  def test_idle_with_sticky_status9_and_no_new_packet_times_out_without_release(self):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp:
      worker, api, _journal, _parent, _env_calls = make_rig(Path(temp), complete_frame=False)
      report = {"ping": True, "ssh": True, "linux": True,
                "boot_id": closeout.BOOT_ID, "new_kernel_warnings": []}
      for stage in ("ENV", "HEALTH", "MIN_READ", "INIT"):
        self.assertTrue(worker.command(stage, report if stage == "HEALTH" else None)["ok"])
      failed = worker.command("SEND", deadline_monotonic_s=time.monotonic() + 0.04)
      self.assertFalse(failed["ok"])
      self.assertEqual(worker.state, "FAIL")
      self.assertTrue(api.buffers[0].freed is False)
      self.assertTrue(_journal_has_terminal_failure(worker.journal))

  def test_second_frame_nonidle_stops_before_second_length_write(self):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp:
      worker, api, _journal, _parent, _env_calls = make_rig(Path(temp))
      report = {"ping": True, "ssh": True, "linux": True,
                "boot_id": closeout.BOOT_ID, "new_kernel_warnings": []}
      for stage in ("ENV", "HEALTH", "MIN_READ", "INIT", "SEND", "VERIFY", "RELEASE", "HEALTH"):
        result = worker.command(stage, report if stage == "HEALTH" else None)
        self.assertTrue(result["ok"], (stage, result))
      api.dma.regs[0x04] = 0
      before = len([1 for offset, _ in api.dma.writes if offset == 0x28])
      result = worker.command("SEND")
      after = len([1 for offset, _ in api.dma.writes if offset == 0x28])
      self.assertFalse(result["ok"])
      self.assertEqual(after, before)
      self.assertEqual(worker.state, "FAIL")

  def test_single100_send_requires_fresh_zero_length_register(self):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp:
      worker, api, journal, _parent, _env_calls = make_rig(Path(temp))
      target_plan = make_validated_plan(0, 0, 16)
      worker.mode = "single100"
      worker.stages = ("SEND",)
      worker.config.frames["single100"] = (
          SimpleNamespace(plan=target_plan, frame_id=0, foreground_pixels=16),)
      worker.environment = lambda: {
          "exclusive": True, "boot_id": closeout.BOOT_ID,
          "loaded_bit_sha256": worker.config.profiles["100"].bit_sha256,
      }
      worker.env = {"boot_id": closeout.BOOT_ID}
      worker.dma, worker.checker = api.dma, api.checker
      worker.index = 0
      result = worker.command("SEND")
      self.assertFalse(result["ok"])
      self.assertIn("LENGTH register differs", result["error"])
      self.assertEqual(api.dma.writes, [])
      self.assertEqual(api.buffers, [])
      self.assertTrue(journal.exists("send-single100-frame-0-consumed"))

  def test_single100_cold_frame_passes_without_requiring_initial_idle(self):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp:
      worker, api, _journal, _parent, _env_calls = make_rig(Path(temp))
      base = closeout.base
      target_plan = make_validated_plan(0, 0, 16)
      worker.mode = "single100"
      worker.stages = ("SEND", "VERIFY", "RELEASE")
      worker.config.frames["single100"] = (
          SimpleNamespace(plan=target_plan, frame_id=0, foreground_pixels=16),)
      worker.environment = lambda: {
          "exclusive": True, "boot_id": closeout.BOOT_ID,
          "loaded_bit_sha256": worker.config.profiles["100"].bit_sha256,
      }
      worker.env = {"boot_id": closeout.BOOT_ID}
      worker.dma, worker.checker = api.dma, api.checker
      api.dma.regs.update({0x00: 0, 0x04: 0, 0x18: 0x200000, 0x28: 0})
      fresh_checker = {name: 0 for name in base.CHECKER_REGS}
      fresh_checker.update({"drain_enable": 1, "magic": base.PACKET_MAGIC})
      api.checker_regs.clear()
      api.checker_regs.update({base.CHECKER_REGS[name]: value
                               for name, value in fresh_checker.items()})
      worker.previous_checker = fresh_checker

      for stage in worker.stages:
        result = worker.command(stage)
        self.assertTrue(result["ok"], (stage, result))
      self.assertEqual(worker.send_attempts, 1)
      self.assertEqual([offset for offset, _ in api.dma.writes], [0, 0x18, 0x28])
      self.assertTrue(api.buffers[0].freed)

  def test_dma_idle_without_ioc_fails_final_verify_and_retains_buffer(self):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp:
      worker, api, _journal, _parent, _env_calls = make_rig(Path(temp))
      report = {"ping": True, "ssh": True, "linux": True,
                "boot_id": closeout.BOOT_ID, "new_kernel_warnings": []}
      for stage in ("ENV", "HEALTH", "MIN_READ", "INIT", "SEND"):
        result = worker.command(stage, report if stage == "HEALTH" else None)
        self.assertTrue(result["ok"], (stage, result))
      api.dma.regs[0x04] = closeout.base.DMA_SR_IDLE
      result = worker.command("VERIFY")
      self.assertFalse(result["ok"])
      self.assertFalse(api.buffers[0].freed)

  def test_parent_receipt_validation_accepts_complete_baseline_and_rejects_missing_dma_pass(self):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp:
      parent = closeout.Journal(Path(temp) / "parent")
      _seed_parent_guard(parent, "a" * 64)
      self.assertIsNone(closeout.validate_parent_baseline(
          parent, boot_id=closeout.BOOT_ID,
          executor_manifest_sha256="a" * 64,
          bit_sha256=closeout.diagnostic_core.BIT_SHA))
      parent_path = parent.root / "supervision-dma-once-pass.json"
      parent_path.unlink()
      with self.assertRaisesRegex(closeout.base.PreflightError, "dma-once"):
        closeout.validate_parent_baseline(
            parent, boot_id=closeout.BOOT_ID,
            executor_manifest_sha256="a" * 64,
            bit_sha256=closeout.diagnostic_core.BIT_SHA)

  def test_wrong_frame_identity_or_golden_keeps_buffer_and_blocks_later_mmio(self):
    import tempfile
    from pathlib import Path
    for wrong_id, wrong_checksum in ((True, False), (False, True)):
      with self.subTest(wrong_id=wrong_id, wrong_checksum=wrong_checksum):
        with tempfile.TemporaryDirectory() as temp:
          worker, api, _journal, parent, _env_calls = make_rig(
              Path(temp), wrong_frame_id=wrong_id, wrong_binary_checksum=wrong_checksum)
          report = {"ping": True, "ssh": True, "linux": True,
                    "boot_id": closeout.BOOT_ID, "new_kernel_warnings": []}
          for stage in ("ENV", "HEALTH", "MIN_READ", "INIT"):
            self.assertTrue(worker.command(stage, report if stage == "HEALTH" else None)["ok"])
          result = worker.command("SEND")
          self.assertFalse(result["ok"])
          self.assertFalse(api.buffers[0].freed)
          reads_at_failure = len(api.dma.reads) + len(api.checker.reads)
          self.assertFalse(worker.command("VERIFY")["ok"])
          self.assertEqual(reads_at_failure, len(api.dma.reads) + len(api.checker.reads))
          self.assertTrue(parent.exists("terminal-fail"))

  def test_parent_terminal_failure_blocks_before_environment_or_mmio(self):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp:
      worker, api, journal, parent, env_calls = make_rig(Path(temp))
      parent.claim("terminal-fail", {"mode": "old", "stage": "prior"})
      result = worker.command("ENV")
      self.assertFalse(result["ok"])
      self.assertEqual(env_calls, [])
      self.assertEqual(api.dma.reads, [])
      self.assertTrue(journal.exists("terminal-fail"))

  def test_reused_mode_one_shot_blocks_before_environment_or_mmio(self):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp:
      worker, api, journal, _parent, env_calls = make_rig(Path(temp), fresh_mode_auth=False)
      result = worker.command("ENV")
      self.assertFalse(result["ok"])
      self.assertIn("one-shot", result["error"])
      self.assertEqual(env_calls, [])
      self.assertEqual(api.dma.reads, [])
      self.assertTrue(journal.exists("terminal-fail"))


def _journal_has_terminal_failure(journal):
    return journal.exists("terminal-fail")


if __name__ == "__main__":
    unittest.main()
