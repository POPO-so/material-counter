"""Optional Playwright QA against the real server; all inputs are OFFLINE fixtures.

Run with a local Playwright installation and Microsoft Edge. No board access.
Screenshots are written to pytest's temporary directory, never hardware evidence.
"""
import sys
import time
from pathlib import Path

import numpy as np
import pytest

playwright = pytest.importorskip("playwright.sync_api")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dashboard_state import DashboardState
from mjpeg_preview import MjpegPreviewServer, render_preview_jpeg
from stream_counter import MaskCounter


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(channel="msedge", headless=True)
        yield browser
        browser.close()


def open_page(browser, server, width=1440, height=1000):
    page = browser.new_page(viewport={"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"http://127.0.0.1:{server.port}", wait_until="domcontentloaded")
    return page, errors


def test_unconnected_dashboard_responsive_without_fake_numbers(browser, tmp_path):
    with MjpegPreviewServer(host="127.0.0.1", port=0) as server:
        page, errors = open_page(browser, server)
        try:
            playwright.expect(page.locator('#connection')).to_contain_text('计数数据未提供')
            for width in (1440, 1024, 768, 375, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                playwright.expect(page.locator('#count')).to_have_text('--')
                playwright.expect(page.get_by_role('button', name='开始采集', exact=True)).to_be_disabled()
                playwright.expect(page.locator('#dma')).to_contain_text('未连接')
                page.screenshot(path=str(tmp_path / f"offline-{width}.png"), full_page=True)
            assert not errors
        finally:
            page.close()


def test_completed_result_stale_recovery_offline_and_preview_pause(browser):
    now = [10.0]
    telemetry = DashboardState(source="software", clock=lambda: now[0])
    counter = MaskCounter()
    mask = np.zeros((240, 320), np.uint8)
    result = counter.update(0, mask)
    telemetry.on_frame(result)
    fail = [False]
    def provider():
        if fail[0]:
            raise RuntimeError('offline test failure')
        return {"dashboard": telemetry.snapshot()}
    with MjpegPreviewServer(host="127.0.0.1", port=0, state_provider=provider) as server:
        server.hub.publish(render_preview_jpeg(mask, total_count=result.total_count,
                                              line_y=120, pip_label='SIM TH=128'),
                           dimensions=(320, 240))
        page, errors = open_page(browser, server)
        try:
            playwright.expect(page.locator('#count')).to_have_text('0')
            playwright.expect(page.locator('#fpga')).to_contain_text('未启用')
            playwright.expect(page.locator('#source')).to_contain_text('非 FPGA 输出')
            playwright.expect(page.locator('#stream')).to_be_visible()
            page.get_by_role('button', name='暂停预览').click()
            assert page.locator('#stream').get_attribute('src') is None
            playwright.expect(page.locator('#video-title')).to_have_text('预览已暂停')
            page.get_by_role('button', name='恢复预览').click()
            playwright.expect(page.locator('#stream')).to_be_visible()
            now[0] += 6
            playwright.expect(page.locator('#count')).to_have_text('--', timeout=6000)
            playwright.expect(page.locator('#phase')).to_have_text('数据过期')
            telemetry.on_frame(counter.update(1, mask))
            playwright.expect(page.locator('#count')).to_have_text('0', timeout=6000)
            fail[0] = True
            playwright.expect(page.locator('#connection')).to_contain_text('状态未连接', timeout=6000)
            playwright.expect(page.locator('#count')).to_have_text('--')
            playwright.expect(page.locator('#dma')).to_contain_text('未连接')
            fail[0] = False
            telemetry.set_phase('complete', '离线测试完成')
            now[0] += 60
            playwright.expect(page.locator('#count')).to_have_text('0', timeout=6000)
            playwright.expect(page.locator('#count-hint')).to_have_text('本次会话最终结果')
            assert not errors
        finally:
            page.close()


def test_calibration_requires_confirmation_and_real_response(browser):
    changes = []
    values = {"exposure_time_absolute": 50, "brightness": 0}
    fail = [False]
    def update(payload):
        if fail[0]:
            raise PermissionError('calibration is disabled')
        changes.append(payload)
        values.update(payload)
        return dict(values)
    def provider():
        return {"calibration_mode": True, "camera_controls": dict(values)}
    with MjpegPreviewServer(host="127.0.0.1", port=0,
                            state_provider=provider, control_callback=update) as server:
        page, errors = open_page(browser, server)
        try:
            playwright.expect(page.locator('#calibration')).to_be_visible()
            page.locator('#exposure').fill('100')
            page.get_by_role('button', name='应用参数').click()
            page.get_by_role('button', name='取消', exact=True).click()
            assert not changes
            page.get_by_role('button', name='应用参数').click()
            page.get_by_role('button', name='确认操作', exact=True).click()
            playwright.expect(page.locator('#feedback')).to_contain_text('后端已确认')
            assert changes == [{"exposure_time_absolute": 100, "brightness": 0}]
            fail[0] = True
            page.get_by_role('button', name='恢复基准参数').click()
            page.get_by_role('button', name='确认操作', exact=True).click()
            playwright.expect(page.locator('#feedback')).to_contain_text('calibration is disabled')
            assert len(changes) == 1
            assert not errors
        finally:
            page.close()
