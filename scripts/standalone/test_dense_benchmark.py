"""Synthetic truth is checked per object, not by aggregate count alone."""
from scripts.rle.test_counter import library
from scripts.standalone.dense_benchmark import run_benchmark


def test_budgeted_dense_sequence_counts_each_of_one_hundred_objects_once(library):
    report = run_benchmark(library, repeats=1)
    result = report['synthetic']
    assert result['passed']
    assert result['actual_events'] == result['expected_objects'] == 100
    assert all(len(events) == 1 for events in result['per_object'].values())
    assert max(row['split_stats']['attempted'] for row in result['frames']) <= 2
    assert any(row['split_stats']['deferred'] for row in result['frames'])
    assert report['cases'][-1]['children'] == [1]
    assert len(report['native_sha256']) == 64


def test_benchmark_cli_fails_when_per_object_truth_fails(tmp_path, monkeypatch):
    from scripts.standalone import dense_benchmark
    monkeypatch.setattr(dense_benchmark, 'run_benchmark', lambda *args: dict(cases=[],
        synthetic=dict(summary={}, uniquely_correct_objects=99, actual_events=100, passed=False)))
    assert dense_benchmark.main(['--native-lib', 'unused', '--output', str(tmp_path/'failed.json')]) == 1
