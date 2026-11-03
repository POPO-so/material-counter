"""CPU replay reports bind compact inputs and preserve deterministic semantics."""
import json
import hashlib
import sys
from types import SimpleNamespace

import pytest

from scripts.rle.components import build_native
from scripts.standalone.crossing_snapshots import CrossingSnapshots
from scripts.standalone.test_crossing_snapshots import counter, observe
from scripts.standalone import performance_replay
from scripts.standalone.performance_replay import main, replay_events
from scripts.wo0016.stream_counter import CountEvent


def _event(tmp_path):
    root = tmp_path / 'captures'
    capture = CrossingSnapshots(root, identity={}, storage_mode='compact',
                                context_frames=1, min_free_bytes=0)
    tracker = counter()
    observe(capture, tracker, 0, 315)
    observe(capture, tracker, 1, 325)
    assert capture.close(timeout=10)
    return next(root.glob('event-*/event.json'))


def test_replay_repeats_are_deterministic_and_keep_rle_geometry(tmp_path):
    event = _event(tmp_path)
    library = build_native(tmp_path / ('rle.dll' if sys.platform == 'win32' else 'rle.so'))
    output = tmp_path / 'report.json'
    assert main(['--event', str(event), '--event', str(event), '--native-lib', str(library), '--output',
                 str(output), '--repeats', '2']) == 0
    report = json.loads(output.read_text(encoding='utf-8'))

    group = report['inputs'][0]
    first, second = group['repeats']
    assert report['execution']['mode'] == 'cpu-only'
    assert report['execution']['window_is_complete_video'] is False
    assert report['execution']['claims_material_accuracy'] is False
    assert 'does not prove matching the historical initial track state' in report['execution']['determinism_scope']
    assert len(report['inputs']) == 2
    assert group['frame_count'] == 2
    assert group['deterministic'] is True
    assert group['truth']['status'] == 'NOT_MEASURED'
    assert first['semantic_sha256'] == second['semantic_sha256']
    assert first['semantic_summary'] == second['semantic_summary']
    assert first['frames'][0]['detection_count'] == 1
    assert first['frames'][0]['runs'] == 12
    assert first['frames'][0]['wall_ms'] >= 0
    assert first['frames'][0]['region_ms'] >= 0
    assert first['frames'][0]['track_ms'] >= 0
    assert all(key in first['frames'][0] for key in (
        'split_ms', 'analyze_ms', 'region_ms', 'track_ms', 'wall_ms',
        'capture_monotonic_ns', 'capture_interval_ms', 'split_stats',
        'split_diagnostics', 'cluster_parent_diagnostics'))
    assert first['frames'][0]['capture_interval_ms'] is None
    assert first['frames'][1]['capture_interval_ms'] >= 0
    assert first['summary']['capture_interval_ms']['count'] == 1
    assert first['summary']['wall_ms']['count'] == 2
    semantics = first['semantic_summary'][0]
    assert semantics['detections'] == [dict(x_min=315, x_max=326, y_min=100, y_max=111,
                                             area=144, centroid_x=320.5, centroid_y=105.5)]
    assert semantics['events'] == [] and semantics['total_count'] == 0
    assert [frame['frame_id'] for frame in first['frames']] == [0, 1]
    assert len(report['application_source']['digest']) == 64
    assert 'scripts/wo0016/motion_association.py' in report['application_source']['sha256_by_path']
    assert 'scripts/wo0016/binary_stats.py' in report['application_source']['sha256_by_path']
    assert 'scripts/wo0016/packet_encoder.py' in report['application_source']['sha256_by_path']
    assert report['application_source']['optional_sources']['scripts/rle/native/rle_components.cpp']['included'] is True
    assert 'scripts/rle/split_work.py' in report['application_source']['sha256_by_path']
    assert report['native_library']['sha256'] == hashlib.sha256(library.read_bytes()).hexdigest()
    assert len(group['event_sha256']) == 64
    assert group['artifacts']['frame-0000000000.rle.npz']['sha256']


def test_replay_accepts_v2_parent_cluster_evidence_and_reports_truth(tmp_path):
    event = _event(tmp_path)
    record = json.loads(event.read_text(encoding='utf-8'))
    assert record['schema'] == 'material-counter.crossing-snapshot.v2'
    record['frames'][0]['cluster_parent_diagnostics'] = [
        {'state': 'observed', 'parent_id': 3}]
    event.write_text(json.dumps(record), encoding='utf-8')
    truth = tmp_path / 'truth.json'
    truth.write_text(json.dumps({
        'schema': 'material-counter.truth.v1', 'matching_axis': 'x',
        'objects': [{'object_id': 'a', 'frame_id_min': 0, 'frame_id_max': 1,
                     'centroid_y': 105.5, 'lateral_tolerance_px': 2}],
    }), encoding='utf-8')
    library = build_native(tmp_path / ('rle.dll' if sys.platform == 'win32' else 'rle.so'))
    report = replay_events([event], native_lib=library, repeats=1, truth_path=truth)

    frame = report['inputs'][0]['repeats'][0]['frames'][0]
    assert frame['snapshot_cluster_parent_diagnostics'] == [
        {'state': 'observed', 'parent_id': 3}]
    result = report['inputs'][0]['truth']
    assert result['status'] == 'MEASURED'
    assert result['matching_axis'] == 'x'
    assert result['truth_sha256'] == hashlib.sha256(truth.read_bytes()).hexdigest()
    assert 'per event window' in result['scope']
    assert report['execution']['claims_material_accuracy'] is False


def test_replay_still_accepts_v1_compact_events(tmp_path):
    event = _event(tmp_path)
    record = json.loads(event.read_text(encoding='utf-8'))
    record['schema'] = 'material-counter.crossing-snapshot.v1'
    event.write_text(json.dumps(record), encoding='utf-8')
    library = build_native(tmp_path / ('rle.dll' if sys.platform == 'win32' else 'rle.so'))

    group = replay_events([event], native_lib=library, repeats=1)['inputs'][0]
    assert group['frame_count'] == 2
    assert group['truth']['status'] == 'NOT_MEASURED'


def test_truth_matching_reports_all_event_and_object_outcomes(tmp_path, monkeypatch):
    event = _event(tmp_path)
    truth = tmp_path / 'truth.json'
    truth.write_text(json.dumps({
        'schema': 'material-counter.truth.v1', 'matching_axis': 'x',
        'objects': [
            {'object_id': 'dup', 'frame_id_min': 0, 'frame_id_max': 1,
             'centroid_y': 100, 'lateral_tolerance_px': 2},
            {'object_id': 'ok', 'frame_id_min': 0, 'frame_id_max': 1,
             'centroid_y': 200, 'lateral_tolerance_px': 2},
            {'object_id': 'miss', 'frame_id_min': 0, 'frame_id_max': 1,
             'centroid_y': 400, 'lateral_tolerance_px': 2},
            {'object_id': 'amb-a', 'frame_id_min': 0, 'frame_id_max': 1,
             'centroid_y': 300, 'lateral_tolerance_px': 5},
            {'object_id': 'amb-b', 'frame_id_min': 0, 'frame_id_max': 1,
             'centroid_y': 308, 'lateral_tolerance_px': 5},
        ],
    }), encoding='utf-8')

    class FakeCounter:
        count_axis = 'x'
        last_split_stats = {'attempted': 0}
        last_split_diagnostics = [{'state': 'attempted', 'component': 1}]
        last_cluster_parent_diagnostics = [{'state': 'observed', 'parent_id': 7}]

        def update_rle(self, packet, *, capture_monotonic_ns=None):
            events = {
                0: (),
                1: (CountEvent(1, 1, 320, 100), CountEvent(2, 1, 320, 100),
                    CountEvent(3, 1, 320, 200), CountEvent(4, 1, 320, 304),
                    CountEvent(5, 1, 320, 500)),
            }[packet.frame_id]
            return SimpleNamespace(
                detections=(), events=events, suppressed_events=(), tracks=(),
                total_count=len(events), roi_subtimings_ms={
                    'split_ms': 1, 'analyze_ms': 2, 'rle_regions_ms': 3,
                    'track_ms': 4},
            )

    monkeypatch.setattr(performance_replay, 'make_runtime_counter',
                        lambda *args, **kwargs: FakeCounter())
    replay = replay_events([event], repeats=1, truth_path=truth)['inputs'][0]
    result = replay['truth']
    frame = replay['repeats'][0]['frames'][0]
    assert frame['split_stats'] == {'attempted': 0}
    assert frame['split_diagnostics'] == [{'state': 'attempted', 'component': 1}]
    assert frame['cluster_parent_diagnostics'] == [{'state': 'observed', 'parent_id': 7}]
    assert result['duplicate_events'] == [{'object_id': 'dup', 'events': 2,
                                           'event_refs': [{'frame_id': 1, 'event_index': 0},
                                                          {'frame_id': 1, 'event_index': 1}]}]
    assert result['missed_objects'] == ['miss']
    assert result['unassigned_events'] == [{'frame_id': 1, 'event_index': 4}]
    assert result['ambiguous_events'] == [{'frame_id': 1, 'event_index': 3,
                                           'candidate_object_ids': ['amb-a', 'amb-b']}]
    assert result['unique_matches'] == [{'object_id': 'ok', 'frame_id': 1,
                                         'event_index': 2}]
    assert result['claims_material_accuracy'] is False


@pytest.mark.parametrize('objects', [
    [{'object_id': 'a', 'frame_id_min': 0, 'frame_id_max': -1,
      'centroid_y': 1, 'lateral_tolerance_px': 1}],
    [{'object_id': 'a', 'frame_id_min': 0, 'frame_id_max': 1,
      'centroid_y': float('nan'), 'lateral_tolerance_px': 1}],
    [{'object_id': 'a', 'frame_id_min': 0, 'frame_id_max': 1,
      'centroid_y': 1, 'lateral_tolerance_px': -1}],
    [{'object_id': 'a', 'frame_id_min': 0, 'frame_id_max': 1,
      'centroid_y': 1, 'lateral_tolerance_px': 1},
     {'object_id': 'a', 'frame_id_min': 0, 'frame_id_max': 1,
      'centroid_y': 2, 'lateral_tolerance_px': 1}],
])
def test_truth_validation_rejects_invalid_values_and_duplicate_ids(tmp_path, objects):
    event = _event(tmp_path)
    truth = tmp_path / 'truth.json'
    truth.write_text(json.dumps({'schema': 'material-counter.truth.v1',
                                 'matching_axis': 'x', 'objects': objects,
                                 'allow_nan': True}), encoding='utf-8')
    with pytest.raises(ValueError, match='truth'):
        replay_events([event], repeats=1, truth_path=truth)


def test_truth_with_ambiguous_extra_event_does_not_claim_unique_object():
    objects = [dict(object_id='a', frame_id_min=0, frame_id_max=2,
                    centroid_y=100, lateral_tolerance_px=10),
               dict(object_id='b', frame_id_min=0, frame_id_max=2,
                    centroid_y=116, lateral_tolerance_px=10)]
    result = performance_replay._match_truth(objects, [(1, 0, 100), (2, 0, 108)])
    assert not result['unique_matches']
    assert result['ambiguous_objects'] == ['a', 'b']


def test_replay_rejects_compact_rle_artifact_digest_mismatch(tmp_path):
    event = _event(tmp_path)
    record = json.loads(event.read_text(encoding='utf-8'))
    rle_name = next(name for name in record['artifacts'] if name.endswith('.rle.npz'))
    (event.parent / rle_name).write_bytes(b'corrupted')

    with pytest.raises(ValueError, match='artifact digest mismatch'):
        replay_events([event], repeats=1)


def test_package_without_native_cpp_still_reports_python_sources(tmp_path, monkeypatch):
    package_root = tmp_path / 'package'
    module_file = package_root / 'scripts' / 'standalone' / 'performance_replay.py'
    for relative in performance_replay._SOURCE_FILES:
        if relative == 'scripts/rle/native/rle_components.cpp':
            continue
        path = package_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'packaged python source')
    module_file.write_bytes(b'packaged replay source')
    native_library = tmp_path / 'native-rle.so'
    native_library.write_bytes(b'native binary')
    event = tmp_path / 'event.json'
    event.write_text(json.dumps(dict(schema='material-counter.crossing-snapshot.v1',
                                     storage_mode='compact')), encoding='utf-8')
    monkeypatch.setattr(performance_replay, '__file__', str(module_file))
    monkeypatch.setattr(performance_replay, '_replay_event',
                        lambda path, record, digest, library, repeats, truth_objects=None:
                        dict(event_path=str(path), event_sha256=digest, artifacts={},
                             frame_count=0, deterministic=True, repeats=[],
                             truth=dict(status='NOT_MEASURED')))

    report = replay_events([event], native_lib=native_library)
    source = report['application_source']
    assert 'scripts/wo0016/motion_association.py' in source['sha256_by_path']
    assert source['optional_sources']['scripts/rle/native/rle_components.cpp'] == {
        'included': False, 'sha256': None}
    assert report['native_library']['sha256'] == hashlib.sha256(b'native binary').hexdigest()
