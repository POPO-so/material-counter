"""Real browser interaction against the persistent host-only mock monitor."""
import asyncio
import json
from pathlib import Path
import queue
import re
import sys
import threading
import time

import pytest

playwright = pytest.importorskip("playwright.sync_api")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live_monitor as live
from live_monitor_state import LiveMonitorState


def test_mock_browser_reset_view_health_and_stop(tmp_path):
    state = LiveMonitorState(mode="mock", history_limit=16)
    args = live.parser().parse_args(["--mode", "mock", "--no-auth", "--port", "0", "--mock-fps", "30"])
    ready, errors = queue.Queue(), []
    def run():
        try:
            asyncio.run(live.run_monitor(args, state=state, server_ready=ready.put))
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    server = ready.get(timeout=5)
    try:
        with playwright.sync_playwright() as driver:
            browser = driver.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            try:
                page.goto(f"http://127.0.0.1:{server.port}/", wait_until="domcontentloaded")
                playwright.expect(page.locator("#mode")).to_contain_text("合成")
                playwright.expect(page.locator("#stream")).to_be_visible()
                playwright.expect(page.locator("#count")).to_have_text("1", timeout=7000)
                playwright.expect(page.locator("#conveyor-speed")).to_have_text(
                    re.compile(r"\d+\.\d cm/s"))
                playwright.expect(page.locator("#speed-note")).to_contain_text("0.75 mm/px")
                playwright.expect(page.locator("#last-crossing-area")).to_contain_text("px")
                page.locator("#reset-count").click()
                playwright.expect(page.locator("#count")).to_have_text("0", timeout=3000)
                page.locator("#view-mode").select_option("binary")
                playwright.expect(page.locator("#feedback")).to_contain_text("画面")
                assert state.view_mode == "binary"
                playwright.expect(page.locator("#resource-status")).to_have_text("采样已停止")
                playwright.expect(page.locator("#resource-content")).to_be_hidden()
                samples = []
                started = time.monotonic()
                while time.monotonic() - started < 30:
                    samples.append(page.request.get(
                        f"http://127.0.0.1:{server.port}/status.json").json()["telemetry"])
                    assert samples[-1]["camera"] == samples[-1]["fpga"] == samples[-1]["dma"] == "disabled"
                    assert samples[-1]["processing_fps"] > 0
                    page.wait_for_timeout(1000)
                assert len(samples) >= 25
                speeds = [sample for sample in samples if sample["cm_per_second"] is not None]
                assert len(speeds) >= 15
                assert any(sample["px_per_frame"] == pytest.approx(4.0) for sample in speeds)
                for sample in speeds:
                    # Image-edge clipping shifts the observed centroid during entry/exit.
                    assert 0 <= sample["px_per_frame"] <= 4.01
                    assert sample["px_per_second"] == pytest.approx(
                        sample["px_per_frame"] * sample["processing_fps"])
                    assert sample["cm_per_second"] == pytest.approx(sample["px_per_second"] * 0.075)
                assert samples[-1]["frames_processed"] > samples[0]["frames_processed"] + 100
                assert samples[-1]["last_crossing_area_px"] == 178
                (tmp_path / "mock-30s.json").write_text(json.dumps({
                    "duration_seconds": time.monotonic() - started, "samples": samples,
                    "page_errors": page_errors}, ensure_ascii=False, indent=2), encoding="utf-8")
                page.screenshot(path=str(tmp_path / "live-monitor-desktop.png"), full_page=True)
                assert page.locator("#stream").evaluate("image => image.naturalWidth") == 320
                for width in (1440, 768, 375, 320):
                    page.set_viewport_size({"width": width, "height": 1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(tmp_path / "live-monitor-mobile.png"), full_page=True)
                page.locator("#stop-monitor").click()
                worker.join(timeout=5)
                assert not worker.is_alive()
                assert not page_errors
            finally:
                browser.close()
    finally:
        state.stop.set()
        worker.join(timeout=5)
    assert not errors


def test_speed_fallback_stale_and_disconnect_rendering():
    from mjpeg_preview import MjpegPreviewServer
    data = {"source": "software", "mode": "mock", "phase": "running", "stale": False,
            "last_frame_age_seconds": 0, "px_per_frame": 4.0, "mm_per_pixel": 0.5,
            "last_crossing_area_px": 135}
    with MjpegPreviewServer(host="127.0.0.1", port=0,
                           state_provider=lambda: {"dashboard": data}) as server:
        with playwright.sync_playwright() as driver:
            browser = driver.chromium.launch(channel="msedge", headless=True)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.port}/", wait_until="domcontentloaded")
                playwright.expect(page.locator("#conveyor-speed")).to_have_text("4.0 px/帧")
                playwright.expect(page.locator("#speed-note")).to_contain_text("0.5 mm/px")
                data.update(stale=True, last_frame_age_seconds=6, cm_per_second=6.0)
                playwright.expect(page.locator("#conveyor-speed")).to_have_text("--")
                playwright.expect(page.locator("#last-crossing-area")).to_have_text("最近越线面积：--")
                page.route("**/status.json", lambda route: route.abort())
                playwright.expect(page.locator("#speed-note")).to_have_text("等待轨迹速度与标定比例")
            finally:
                browser.close()
