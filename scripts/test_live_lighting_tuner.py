"""Offline tests: no real camera or board access."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import cv2
import numpy as np

PATH = Path(__file__).with_name('live_lighting_tuner.py')
spec = importlib.util.spec_from_file_location('lighting_tuner', PATH)
tuner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tuner)


def ring():
    frame = np.full((120, 180), 32, np.uint8)
    cv2.circle(frame, (90, 60), 24, 200, -1)
    cv2.circle(frame, (90, 60), 10, 32, -1)
    return frame


class AnalysisTests(unittest.TestCase):
    def analyze(self, frame, **updates):
        params = dict(tuner.DEFAULT_PARAMS, roi_top=0, roi_bottom=frame.shape[0])
        params.update(updates)
        return tuner.analyze_frame(frame, params)

    def test_ring_metrics_and_hole(self):
        metrics, _ = self.analyze(ring())
        self.assertEqual(metrics['part_count'], 1)
        self.assertTrue(metrics['parts'][0]['hole'])
        self.assertAlmostEqual(metrics['bg_mean'], 32)
        self.assertAlmostEqual(metrics['fg_mean'], 200)
        self.assertTrue(metrics['all_pass'])

    def test_solid_is_not_a_shadow_diagnosis(self):
        frame = ring()
        cv2.circle(frame, (90, 60), 10, 200, -1)
        metrics, _ = self.analyze(frame)
        self.assertFalse(metrics['parts'][0]['hole'])

    def test_empty_and_missing_zone_never_pass(self):
        metrics, _ = self.analyze(np.full((120, 180), 32, np.uint8))
        self.assertIsNone(metrics['fg_mean'])
        self.assertFalse(metrics['all_pass'])
        frame = ring()
        frame[:, :60] = 200
        metrics, _ = self.analyze(frame)
        self.assertIsNone(metrics['left_bg'])
        self.assertEqual(metrics['status_uniform'], 'UNAVAILABLE')
        self.assertFalse(metrics['all_pass'])

    def test_threshold_inclusive_and_roi_denominator(self):
        frame = ring()
        frame[50:55, 80:85] = 255
        metrics, vis = self.analyze(frame)
        self.assertAlmostEqual(metrics['overexp_pct'], 25 / frame.size * 100)
        np.testing.assert_array_equal(vis[52, 82], [0, 0, 255])

    def test_raw_view_has_no_overlay(self):
        frame = ring()
        _, vis = self.analyze(frame, view_mode='raw')
        np.testing.assert_array_equal(vis, cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))

    def test_highlight_outside_roi_does_not_affect_roi_metrics(self):
        frame = ring()
        frame[2, 2] = 255
        metrics, vis = self.analyze(frame, roi_top=10)
        self.assertEqual(metrics['overexp_pct'], 0)
        np.testing.assert_array_equal(vis[2, 2], [0, 0, 255])


class StateTests(unittest.TestCase):
    def test_invalid_controls_are_atomic(self):
        state = tuner.LightingTunerState(width=180, height=120)
        original = state.params.copy()
        for update in ({'binary_thresh': -1}, {'roi_top': 120}, {'gain': True},
                       {'view_mode': 'invalid'}, {'binary_thresh': 95.5}, {'unknown': 1}):
            with self.assertRaises(ValueError):
                state.update_controls(update)
            self.assertEqual(state.params, original)

    def test_hardware_disabled_does_not_claim_applied(self):
        state = tuner.LightingTunerState(width=180, height=120)
        with self.assertRaises(ValueError):
            state.update_controls({'exposure': 20})
        self.assertIsNone(state.status()['hardware_applied'])

    def test_failed_read_clears_frame_and_acceptance(self):
        state = tuner.LightingTunerState(width=180, height=120)
        state.publish(ring())
        state.fail('camera read failed')
        self.assertFalse(state.status()['metrics']['all_pass'])
        self.assertIsNone(state.latest_jpeg)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                state.snapshot(Path(directory))

    def test_demo_cannot_pass_and_snapshot_matches_frame_params(self):
        state = tuner.LightingTunerState(width=180, height=120, demo=True)
        state.publish(ring())
        self.assertFalse(state.status()['metrics']['all_pass'])
        state.update_controls({'binary_thresh': 120})
        with tempfile.TemporaryDirectory() as directory:
            result = state.snapshot(Path(directory))
            report = json.loads(Path(result['report']).read_text(encoding='utf-8'))
            self.assertEqual(report['params']['binary_thresh'], 100)
            self.assertEqual(report['source'], 'DEMO')
            self.assertFalse(report['metrics']['all_pass'])
            self.assertTrue(Path(result['image']).exists())

    def test_open_failure_never_falls_back_to_synthetic(self):
        state = tuner.LightingTunerState(width=180, height=120)
        with patch.object(tuner.cv2, 'VideoCapture') as camera:
            camera.return_value.isOpened.return_value = False
            tuner.capture_loop(state)
        self.assertEqual(state.status()['source'], 'CAMERA_ERROR')
        self.assertIsNone(state.latest_jpeg)
        camera.return_value.release.assert_called_once()

    def test_stale_snapshot_is_rejected(self):
        state = tuner.LightingTunerState(width=180, height=120)
        state.publish(ring())
        state.last_frame_time -= 4
        self.assertFalse(state.status()['metrics']['all_pass'])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                state.snapshot(Path(directory))

    def test_hardware_cannot_be_written_after_capture_stops(self):
        state = tuner.LightingTunerState(width=180, height=120, allow_camera_controls=True)
        state.stop.set()
        with patch.object(state.controls, 'apply') as apply:
            with self.assertRaises(ValueError):
                state.update_controls({'exposure': 40})
            apply.assert_not_called()

    def test_failed_hardware_write_invalidates_capture(self):
        state = tuner.LightingTunerState(width=180, height=120, allow_camera_controls=True)
        state.publish(ring())
        with patch.object(state.controls, 'apply', side_effect=RuntimeError('readback mismatch')):
            with self.assertRaises(RuntimeError):
                state.update_controls({'gamma': 120})
        self.assertFalse(state.status()['metrics']['all_pass'])
        self.assertIsNone(state.status()['hardware_applied'])
        self.assertEqual(state.params['gamma'], 100)
        self.assertTrue(state.stop.is_set())


class HardwareTests(unittest.TestCase):
    def test_menu_readback_with_description(self):
        hardware = tuner.V4L2Controls('/dev/video0')
        with patch.object(hardware, 'run', return_value='auto_exposure: 1 (Manual Mode)\n'):
            self.assertEqual(hardware.read('auto_exposure'), 1)

    def test_write_readback_mismatch_is_not_success(self):
        hardware = tuner.V4L2Controls('/dev/video0')
        with patch.object(hardware, 'read', side_effect=[100, 99]), patch.object(hardware, 'run'):
            with self.assertRaises(RuntimeError):
                hardware.write('gamma', 120)
        self.assertEqual(hardware.saved['gamma'], 100)

    def test_restore_checks_and_restores_auto_mode_last(self):
        hardware = tuner.V4L2Controls('/dev/video0')
        hardware.saved = dict(auto_exposure=3, gamma=100, exposure_time_absolute=50)
        writes = []
        with patch.object(hardware, 'run', side_effect=lambda option: writes.append(option)), \
             patch.object(hardware, 'read', side_effect=lambda name: hardware.saved[name]):
            self.assertEqual(hardware.restore(), [])
        self.assertEqual(writes[-1], '--set-ctrl=auto_exposure=3')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.state = tuner.LightingTunerState(width=180, height=120, demo=True)
        self.state.publish(ring())
        self.directory = tempfile.TemporaryDirectory()
        self.server = tuner.make_server(self.state, '127.0.0.1', 0, Path(self.directory.name))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = 'http://127.0.0.1:%s' % self.server.server_port

    def tearDown(self):
        self.state.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def post(self, path, body):
        return urlopen(Request(self.base + path, data=body, headers={'Content-Type': 'application/json'}), timeout=3)

    def test_routes_and_malformed_requests(self):
        with urlopen(self.base + '/api/status', timeout=3) as response:
            self.assertEqual(json.load(response)['source'], 'DEMO')
        with urlopen(self.base, timeout=3) as response:
            self.assertIn('text/html', response.headers['Content-Type'])
        for path, body, code in [('/api/control', b'{', 400), ('/api/control', b'[]', 400),
                                 ('/api/control', b'{"roi_bottom":0}', 400), ('/missing', b'{}', 404)]:
            with self.assertRaises(HTTPError) as error:
                self.post(path, body)
            self.assertEqual(error.exception.code, code)
        with self.post('/api/control', b'{"binary_thresh":110}') as response:
            self.assertTrue(json.load(response)['ok'])
        with self.post('/api/snapshot', b'{}') as response:
            self.assertTrue(json.load(response)['ok'])

    def test_mjpeg_has_actual_jpeg(self):
        with urlopen(self.base + '/stream.mjpg', timeout=3) as response:
            self.assertIn('multipart/x-mixed-replace', response.headers['Content-Type'])
            self.assertEqual(response.readline(), b'--FRAME\r\n')
            self.assertEqual(response.readline(), b'Content-Type: image/jpeg\r\n')
            size = int(response.readline().decode().split(':')[1])
            self.assertEqual(response.readline(), b'\r\n')
            jpeg = response.read(size)
            self.assertIsNotNone(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE))

    def test_cross_origin_control_is_rejected(self):
        request = Request(self.base + '/api/control', data=b'{"gamma":120}',
                          headers={'Content-Type': 'application/json', 'Origin': 'http://example.org'})
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 403)

    def test_repeated_early_rejections_return_status_without_control_changes(self):
        original = dict(self.state.status()['params'])
        body = b'{"gamma":120}' + b' ' * 4000
        requests = [
            ('/missing', body, {'Content-Type': 'application/json'}, 404),
            ('/api/control', body, {'Content-Type': 'application/json',
                                    'Origin': 'http://example.org'}, 403),
            ('/api/control', body, {'Content-Type': 'text/plain'}, 415),
        ]
        for _ in range(3):
            for path, payload, headers, expected in requests:
                request = Request(self.base + path, data=payload, headers=headers)
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=3)
                self.assertEqual(error.exception.code, expected)
                error.exception.close()
        self.assertEqual(self.state.status()['params'], original)


if __name__ == '__main__':
    unittest.main()
