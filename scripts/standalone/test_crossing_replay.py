import json
from pathlib import Path
import pytest
from scripts.standalone.crossing_replay import replay_window, load_fixture

FIXTURE = Path(__file__).parent / 'fixtures' / 'crossing-regressions-20261031.json'
EXPECTED = {'2310-2317': (0, 1), '2325-2331': (0, 1), '2380-2387': (0, 1), '2645-2649': (1, 2), '2485-2489': (1, 1)}

def test_fixture_windows_replay_to_recorded_events_and_counts():
    fixture = load_fixture(FIXTURE)
    for window in fixture['windows']:
        result = replay_window(window)
        baseline_events = [event for frame in [window['initial'], *window['frames']] for event in frame['events']]
        assert result['events'] == baseline_events
        assert result['total_count'] == window['frames'][-1]['total_count']
        assert result['added_count'] == EXPECTED[window['name']][0]
        assert result['frames']==[{key:frame[key] for key in ('frame_id','total_count','events')}
                                  for frame in window['frames']]

def test_max_step_120_experiment_matches_three_requested_outcomes():
    fixture = load_fixture(FIXTURE)
    expected = {name:counts[1] for name,counts in EXPECTED.items()}
    for window in fixture['windows']:
        if window['name'] in expected:
            result = replay_window(window, overrides={'max_step': 120})
            assert result['added_count'] == expected[window['name']]
