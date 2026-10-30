import tempfile
import unittest

from scripts.standalone.telemetry import Telemetry


def write(root, path, text):
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def proc_stat(pid, comm, user, system, rss_pages=4):
    # Fields after the final ')' begin at field 3 (state).
    fields = ["S"] + ["0"] * 10 + [str(user), str(system)] + ["0"] * 4 + [str(rss_pages)]
    return f"{pid} ({comm}) " + " ".join(fields)


class TelemetryTests(unittest.TestCase):
  def setUp(self):
    self.tempdir = tempfile.TemporaryDirectory()
    self.root = __import__("pathlib").Path(self.tempdir.name)

  def tearDown(self):
    self.tempdir.cleanup()

  def test_cpu_delta_excludes_duplicate_guest_and_tracks_each_core(self):
    samples = iter([
        "cpu 100 0 20 80 10 0 0 0 30 4\ncpu0 60 0 10 30 5 0 0 0 20 2\ncpu1 40 0 10 50 5 0 0 0 10 2\n",
        "cpu 150 0 30 130 20 0 0 0 60 8\ncpu0 90 0 15 50 10 0 0 0 40 4\ncpu1 60 0 15 80 10 0 0 0 20 4\n",
    ])
    root = self.root
    write(root, "/proc/stat", next(samples))
    telemetry = Telemetry(root=root, clock=lambda: 1.0)
    telemetry.poll(1.0)
    write(root, "/proc/stat", next(samples))
    telemetry.poll(4.0)
    metrics = telemetry.snapshot(4.0)
    # Delta total is 90 (guest is already included in user); busy is 40.
    self.assertAlmostEqual(metrics["cpu.total_percent"]["value"], 100 * 40 / 90)
    self.assertAlmostEqual(metrics["cpu.core0_percent"]["value"], 100 * 25 / 55)
    self.assertAlmostEqual(metrics["cpu.core1_percent"]["value"], 100 * 15 / 35)


  def test_process_comm_with_spaces_and_parentheses_and_single_core_percent(self):
    root = self.root
    write(root, "/proc/stat", "cpu 0 0 0 0 0 0 0 0 0 0\n")
    write(root, "/proc/321/stat", proc_stat(321, "worker (blue) task", 100, 50))
    write(root, "/proc/321/status", "Name:\tworker\nVmRSS:\t2048 kB\n")
    telemetry = Telemetry(root=root, clk_tck=100, clock=lambda: 0)
    telemetry.set_processes({"worker": 321})
    telemetry.poll(1.0)
    write(root, "/proc/321/stat", proc_stat(321, "worker (blue) task", 130, 65))
    telemetry.poll(4.0)
    metrics = telemetry.snapshot(4.0)
    self.assertEqual(metrics["process.worker.cpu_percent"]["value"], 150)
    self.assertEqual(metrics["process.worker.rss_bytes"]["value"], 2048 * 1024)


  def test_iio_applies_offset_and_scale_every_five_seconds(self):
    root = self.root
    channel = "/sys/bus/iio/devices/iio:device0"
    write(root, channel + "/name", "xadc")
    write(root, channel + "/in_temp0_raw", "100")
    write(root, channel + "/in_temp0_offset", "-10")
    write(root, channel + "/in_temp0_scale", "1000")
    telemetry = Telemetry(root=root, clock=lambda: 0)
    telemetry.poll(1.0)
    self.assertEqual(telemetry.snapshot(1.0)["iio.xadc.in_temp0_c"]["value"], 90.0)
    write(root, channel + "/in_temp0_raw", "200")
    telemetry.poll(5.9)
    self.assertEqual(telemetry.snapshot(5.9)["iio.xadc.in_temp0_c"]["value"], 90.0)
    telemetry.poll(6.0)
    self.assertEqual(telemetry.snapshot(6.0)["iio.xadc.in_temp0_c"]["value"], 190.0)


  def test_failed_read_preserves_value_timestamp_and_eventually_stales(self):
    root = self.root
    write(root, "/proc/meminfo", "MemAvailable: 100 kB\nCmaTotal: 20 kB\nCmaFree: 10 kB\n")
    telemetry = Telemetry(root=root, clock=lambda: 0)
    telemetry.poll(1.0)
    before = telemetry.snapshot(1.0)["memory.available_bytes"]
    (root / "proc/meminfo").unlink()
    telemetry.poll(4.0)
    failed = telemetry.snapshot(4.0)["memory.available_bytes"]
    self.assertEqual(failed["value"], 100 * 1024)
    self.assertEqual(failed["sampled_monotonic"], before["sampled_monotonic"])
    self.assertTrue(failed["error"])
    self.assertFalse(failed["stale"])
    self.assertTrue(telemetry.snapshot(7.1)["memory.available_bytes"]["stale"])


  def test_first_failed_sample_is_unknown_and_sd_available_is_bytes(self):
    root = self.root
    telemetry = Telemetry(root=root, statvfs=lambda path: type("V", (), {"f_bavail": 3, "f_frsize": 4096})(), clock=lambda: 0)
    telemetry.poll(2.0)
    metrics = telemetry.snapshot(2.0)
    self.assertIsNone(metrics["memory.available_bytes"]["value"])
    self.assertTrue(metrics["memory.available_bytes"]["stale"])
    self.assertEqual(metrics["sd.available_bytes"]["value"], 3 * 4096)


if __name__ == "__main__":
  unittest.main()

