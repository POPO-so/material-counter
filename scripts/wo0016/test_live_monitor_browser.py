"""Real browser interaction against the persistent host-only mock monitor."""
import asyncio
from pathlib import Path
import queue
import sys
import threading

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
                page.locator("#reset-count").click()
                playwright.expect(page.locator("#count")).to_have_text("0", timeout=3000)
                page.locator("#view-mode").select_option("binary")
                playwright.expect(page.locator("#feedback")).to_contain_text("画面")
                assert state.view_mode == "binary"
                playwright.expect(page.locator("#rss")).not_to_have_text("--")
                playwright.expect(page.locator("#cma-free")).to_have_text("--")
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
