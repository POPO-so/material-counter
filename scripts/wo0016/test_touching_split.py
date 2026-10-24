"""Independent synthetic labels for optional touching-object segmentation."""
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from split_touching import split_touching_component
from stream_counter import MaskCounter
import run_stream_counter as runner


def touching_pair(height=14, width=14, gap=6):
    mask = np.zeros((2 * height + gap, width), np.uint8)
    mask[:height] = 255
    mask[height + gap:] = 255
    mask[height:height + gap, width // 2 - 1:width // 2 + 1] = 255
    return mask


def frame(component):
    image = np.zeros((240, 320), np.uint8)
    image[80:80 + component.shape[0], 90:90 + component.shape[1]] = component
    return image


@pytest.mark.parametrize('component', [touching_pair(height=8, width=10, gap=5), touching_pair()])
def test_split_is_default_off_and_does_not_enter_helper(monkeypatch, component):
    import stream_counter
    def unexpected(*args, **kwargs):
        pytest.fail('default path must not scan split candidates')
    monkeypatch.setattr(stream_counter, 'split_touching_component', unexpected)
    counter = MaskCounter(roi_mode='full', guarded_merge=True)
    assert len(counter.update(0, frame(component)).detections) == 1


@pytest.mark.parametrize('guarded_merge', [False, True])
@pytest.mark.parametrize('hollow', [False, True])
@pytest.mark.parametrize('transpose', [False, True])
@pytest.mark.parametrize('clustering', [{'component_cluster_gap': 14},
                                      {'component_cluster_dx': 30, 'component_cluster_dy': 60}])
def test_opt_in_splits_large_pair_independently_of_merge_gate(guarded_merge, clustering, hollow, transpose):
    component = touching_pair()
    if hollow:
        component[4:10, 4:10] = 0
        component[24:30, 4:10] = 0
    labeled_component = component.copy()
    if transpose:
        component = component.T
    counter = MaskCounter(roi_mode='full', guarded_merge=guarded_merge,
                          enable_touching_split=True, min_split_area=280,
                          **clustering)
    result = counter.update(0, frame(component))
    assert len(result.detections) == 2
    assert sum(d.area for d in result.detections) == np.count_nonzero(component)
    assert all(d.area >= 140 for d in result.detections)
    pieces = split_touching_component(component, min_split_area=280)
    assert len(pieces) == 2
    assert not np.any(pieces[0] & pieces[1])
    np.testing.assert_array_equal(pieces[0] | pieces[1], component != 0)
    assert all(cv2.connectedComponents(p.astype(np.uint8))[0] == 2 for p in pieces)
    # The labeled object cores exclude the ambiguous connecting neck. Each
    # returned child must retain one entire core and no pixels of the other.
    cores = [labeled_component[:14] != 0, labeled_component[20:] != 0]
    if transpose:
        pieces = tuple(piece.T for piece in pieces)
    assert any(np.array_equal(piece[:14], cores[0]) and not piece[20:].any() for piece in pieces)
    assert any(np.array_equal(piece[20:], cores[1]) and not piece[:14].any() for piece in pieces)


@pytest.mark.parametrize('height,gap,area', [(7, 5, 150), (7, 10, 160),
                                           (8, 5, 170), (10, 5, 210), (12, 5, 250)])
def test_small_single_object_neck_is_not_oversegmented(height, gap, area, monkeypatch):
    component = touching_pair(height=height, width=10, gap=gap)
    assert np.count_nonzero(component) == area
    assert len(split_touching_component(component)) == 1
    import stream_counter
    def unexpected(*args, **kwargs):
        pytest.fail('small components must bypass split scanning')
    monkeypatch.setattr(stream_counter, 'split_touching_component', unexpected)
    counter = MaskCounter(roi_mode='full', enable_touching_split=True)
    assert len(counter.update(0, frame(component)).detections) == 1


def test_cli_switch_and_area_change_counting_identity():
    parser = runner._parser()
    defaults = runner.counting_configuration(parser.parse_args(['--counting-profile', 'full-width-mixed']))
    enabled = runner.counting_configuration(parser.parse_args([
        '--counting-profile', 'full-width-mixed', '--enable-touching-split', '--min-split-area', '320']))
    assert defaults['enable_touching_split'] is False
    assert defaults['min_split_area'] == 280
    assert enabled['enable_touching_split'] is True
    assert enabled['min_split_area'] == 320
    assert defaults != enabled
    assert defaults['active_rows'] == [20, 220]


@pytest.mark.parametrize('kwargs', [{'enable_touching_split': 1}, {'min_split_area': 260},
                                   {'min_split_area': True}, {'min_split_area': 280.5}])
def test_invalid_split_config_is_rejected(kwargs):
    with pytest.raises(ValueError):
        MaskCounter(**kwargs)


def test_cli_rejects_unsafe_area_before_dispatch_and_mock_honors_opt_in(monkeypatch):
    seen = []
    monkeypatch.setattr(runner, 'run_mock_simulation',
                        lambda **kwargs: (seen.append(kwargs) or {'status': 'MOCK_PASS'}))
    assert runner.main(['--mode', 'mock', '--min-split-area', '260']) == 2
    assert seen == []
    assert runner.main(['--mode', 'mock', '--enable-touching-split']) == 0
    assert seen[0]['counter_config']['enable_touching_split'] is True


@pytest.mark.parametrize('flags', [['--enable-touching-split'], ['--min-split-area', '320']])
def test_split_options_are_bound_by_calibration_lock(tmp_path, flags):
    import physical_calibration_lock as lock
    parser = runner._parser()
    identity = {'stage': 'physical-calibration',
                'counting_config': runner.counting_configuration(parser.parse_args([]))}
    changed = {'stage': 'physical-calibration',
               'counting_config': runner.counting_configuration(parser.parse_args(flags))}
    path = tmp_path / 'new-lock' / 'calibration-lock.json'
    lock.write_ready_calibration_lock(path, identity)
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match='identity/status'):
        lock.validate_ready_calibration_lock(path, changed, expected_uid=None)
    assert path.read_bytes() == before


def test_preview_preserves_logical_roi_and_draws_active_rows(monkeypatch):
    import mjpeg_preview as preview
    actual_render = preview.render_preview_jpeg
    encoded_options = []
    def render(mask, **kwargs):
        encoded_options.append(kwargs)
        return actual_render(mask, **kwargs)
    monkeypatch.setattr(preview, 'render_preview_jpeg', render)
    mask = np.zeros((240, 320), np.uint8)
    result = MaskCounter(roi_mode='full', active_rows=[20, 220]).update(0, mask)
    assert (result.roi_top, result.roi_bottom) == (0, 240)
    hub = preview.MjpegFrameHub()
    publisher = preview.PeriodicMjpegPublisher(hub=hub, line_y=120, count_axis='x',
                                               active_rows=[20, 220])
    try:
        with hub.streaming_client():
            publisher(result, mask)
            assert hub.wait_for_next(after_sequence=0, timeout_s=5) is not None
        assert encoded_options[0]['roi_top'] == 20
        assert encoded_options[0]['roi_bottom'] == 220
        image = preview.render_preview_frame(mask, total_count=0, line_y=120,
                                            count_axis='x', line_x=160,
                                            roi_top=20, roi_bottom=220)
        assert tuple(image[110, 160]) == (0, 0, 255)
        assert tuple(image[20, 3]) == (50, 220, 130)
        assert tuple(image[219, 3]) == (50, 220, 130)
    finally:
        publisher.close()
        hub.close()
