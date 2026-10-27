import json
import sys
from pathlib import Path

import pytest
import asyncio
import time
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_stream_counter as runner


def test_export_config_is_local_complete_and_exclusive(tmp_path, monkeypatch):
    target = tmp_path / 'config.json'
    def forbidden(*args, **kwargs):
        pytest.fail('configuration export must not access hardware')
    monkeypatch.setattr(runner, 'execute_board_stream', forbidden)
    assert runner.main(['--counting-profile', 'lighting-calibrated',
                        '--export-effective-config', str(target)]) == 0
    data = json.loads(target.read_text(encoding='utf-8'))
    assert data['board_accessed'] is False
    assert data['counter_config']['min_area'] == 15
    assert data['counter_config']['min_split_child_area'] == 48
    assert data['counter_config']['motion_timebase_s'] is None
    assert data['counter_config']['width'] == 320
    assert data['counter_config']['roi_bottom'] == 210
    assert data['split_conditions']['neck_peak_ratio_max'] == .65
    assert runner.main(['--export-effective-config', str(target)]) == 2


def test_deferred_write_mode_and_observability_are_explicit():
    args = runner._parser().parse_args(['--capture-write-mode', 'deferred',
                                      '--count-observability'])
    assert runner.defer_capture_writes_until_close(args)
    assert runner.counting_configuration(args)['observability'] is True


def test_all_stages_have_percentiles_without_removing_average_fields():
    summary = runner.summarize_stage_timings_ms({'t_split': [1., 2., 3., 100.]})
    assert summary['p50_t_split_ms'] == 2.5
    assert summary['p95_t_split_ms'] == pytest.approx(85.45)
    assert summary['max_t_split_ms'] == 100
    assert summary['t_split_timing_samples'] == 4
    assert summary['avg_dma_ms'] is None


def test_capture_capacity_for_600_frames_is_explicit():
    from capture_dataset import estimated_capture_bytes
    assert estimated_capture_bytes(600) < 256 * 1024**2
    assert 600 * 2 * 320 * 240 == 92_160_000


def test_birth_on_right_of_geometric_line_is_marked_even_within_hysteresis():
    from stream_counter import Detection, MaskCounter
    counter = MaskCounter(count_axis='x', line_x=160, observability=True)
    result = counter.update_detections(0, [Detection(157, 165, 100, 108, 81, 161., 104.)])
    assert result.tracks[0]['born_past_line'] is True
    assert result.tracks[0]['born_in_hysteresis_band'] is True
    assert result.tracks[0]['counted'] is False
    assert result.tracks[0]['born_counted_without_event'] is False
    assert result.events == ()


def test_runner_dashboard_exposes_its_own_completed_frame_identity():
    from dashboard_state import DashboardState
    from stream_counter import FrameResult
    state = DashboardState(source='software')
    state.on_frame(FrameResult(17, 3, (), (), 0, 240, (), ()))
    assert state.snapshot()['frame_id'] == 17


def test_partial_roi_crop_that_was_not_rejected_is_not_candidate_d():
    from capture_dataset import CapturedFrame
    from diagnose_count_discrepancy import _detect_records
    image = np.zeros((240, 320), np.uint8)
    meta = {'diagnostics': {'rejected_components': [
        {'detection': {'area': 14, 'centroid_x': 100, 'centroid_y': 30},
         'reason': 'partially_outside_roi', 'rejected': False, 'partial_crop_only': True}]}}
    rows, _, _ = _detect_records((CapturedFrame(0, image, image, meta),),
        {'roi_top': 30, 'roi_bottom': 210, 'min_area': 15},
        min_area_margin=5, large_area_px=210, large_area_ratio=1.5,
        late_birth_x=150, area_jump_ratio=.5)
    assert not any(row['category'] == 'D' for row in rows)


def test_stream_frame_timings_survive_capture_serialization(tmp_path):
    from capture_dataset import CaptureWriter, load_dataset
    from stream_counter import DoubleBufferStream, MaskCounter, FrameInput, TX_BYTES, allocate_ping_pong_buffers
    from test_stream_counter import MockDma, MockAllocator
    from test_capture_dataset import _frame_metadata
    masks = [np.zeros((240, 320), np.uint8) for _ in range(6)]
    for i, mask in enumerate(masks):
        mask[100:110, 130+i*8:140+i*8] = 255
    dma = MockDma(masks)
    dma.release_frame1.set()
    tx, rx = allocate_ping_pong_buffers(MockAllocator())
    config = {'count_axis': 'x', 'roi_mode': 'full', 'observability': True}
    writer = CaptureWriter(tmp_path/'capture', expected_frames=6,
                           config={'counter_config': config, 'frame_geometry': {'mapping': 'test'}},
                           defer_writes_until_close=True)
    counter = MaskCounter(**config)
    async def source():
        for i in range(6):
            yield FrameInput(masks[i], i, 1000+i, 3000+i, 2.5)
    def save(result, slot):
        started = time.perf_counter_ns()
        metadata = _frame_metadata(result.frame_id)
        metadata.update(algorithm_config=config, dma_complete_monotonic_ns=slot.dma_complete_monotonic_ns,
                        diagnostics=result.diagnostics, stage_timings_ms=dict(result.stage_timings_ms))
        writer.submit(result.frame_id, slot.capture_gray, slot.capture_mask, metadata,
                      submission_started_ns=started)
    timings = {}
    stream = DoubleBufferStream(recv_channel=dma.recvchannel, send_channel=dma.sendchannel,
                                tx_buffers=tx, rx_buffers=rx, counter=counter,
                                packet_encoder=lambda gray, *, frame_id: bytes([frame_id])*TX_BYTES,
                                capture_callback=save, stage_timings_ms=timings)
    asyncio.run(stream.run(source()))
    assert writer.close()['status'] == 'COMPLETE'
    _, frames = load_dataset(tmp_path/'capture')
    for frame in frames:
        stages = frame.metadata['stage_timings_ms']
        assert stages['t_acquire_preprocess'] == 2.5
        assert all(stages[key] >= 0 for key in ('t_ccl', 't_split', 't_track', 't_preview_sink'))
        assert len(timings['t_preview_sink']) == 6


def test_600_frame_deferred_capture_has_no_sampling_queue_or_disk_writes(tmp_path, monkeypatch):
    from capture_dataset import CaptureWriter, load_dataset
    from test_capture_dataset import _frame_metadata
    writer = CaptureWriter(tmp_path/'capture', expected_frames=600, config={'line_y': 120, 'min_area': 12},
                           max_bytes=256*1024**2, queue_depth=1,
                           defer_writes_until_close=True)
    def forbidden(*args, **kwargs):
        pytest.fail('sampling must not enqueue disk work')
    monkeypatch.setattr(writer._queue, 'put_nowait', forbidden)
    gray = np.zeros((240, 320), np.uint8)
    with monkeypatch.context() as sampling:
        sampling.setattr(writer, '_write_frame', forbidden)
        for i in range(600):
            writer.submit(i, gray, gray, _frame_metadata(i), submission_started_ns=time.perf_counter_ns())
    manifest = writer.close()
    assert manifest['status'] == 'COMPLETE'
    assert manifest['captured_frames'] == 600
    assert manifest['dropped_frames'] == manifest['missing_frames'] == 0
    assert len(load_dataset(tmp_path/'capture')[1]) == 600
