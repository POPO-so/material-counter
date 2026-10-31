"""CPU replay reports bind compact inputs and preserve deterministic semantics."""
import json
import hashlib
import sys

import pytest

from scripts.rle.components import build_native
from scripts.standalone.crossing_snapshots import CrossingSnapshots
from scripts.standalone.test_crossing_snapshots import counter, observe
from scripts.standalone import performance_replay
from scripts.standalone.performance_replay import main, replay_events


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
    assert first['semantic_sha256'] == second['semantic_sha256']
    assert first['semantic_summary'] == second['semantic_summary']
    assert first['frames'][0]['detection_count'] == 1
    assert first['frames'][0]['runs'] == 12
    assert first['frames'][0]['wall_ms'] >= 0
    assert first['frames'][0]['region_ms'] >= 0
    assert first['frames'][0]['track_ms'] >= 0
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
    assert report['native_library']['sha256'] == hashlib.sha256(library.read_bytes()).hexdigest()
    assert len(group['event_sha256']) == 64
    assert group['artifacts']['frame-0000000000.rle.npz']['sha256']


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
                        lambda path, record, digest, library, repeats:
                        dict(event_path=str(path), event_sha256=digest, artifacts={},
                             frame_count=0, deterministic=True, repeats=[]))

    report = replay_events([event], native_lib=native_library)
    source = report['application_source']
    assert 'scripts/wo0016/motion_association.py' in source['sha256_by_path']
    assert source['optional_sources']['scripts/rle/native/rle_components.cpp'] == {
        'included': False, 'sha256': None}
    assert report['native_library']['sha256'] == hashlib.sha256(b'native binary').hexdigest()
