from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from birth_semantics_ab import SCENARIOS, run_scenario


# (baseline, band_gate) counts. Rows with a known truth show the trade-off:
# band_gate recovers the in-band late births (late_birth_in_band, two_late_items)
# but counts one extra object for a fragment that appears in the band after a
# real crossing (fragment_after_crossing_in_band).
EXPECTED = {
    "late_birth_in_band": (0, 1),
    "birth_inside_count_zone": (0, 0),
    "duplicate_reentry": (1, 1),
    "in_band_flicker": (0, 0),
    "band_birth_then_forward": (0, 1),
    "two_late_items": (0, 2),
    "fragment_after_crossing_in_band": (1, 2),
}


def test_birth_semantics_ab_is_single_variable_and_matches_the_recorded_matrix():
    assert {scenario["name"] for scenario in SCENARIOS} == set(EXPECTED)
    for scenario in SCENARIOS:
        baseline = run_scenario(scenario["frames"], "baseline")
        candidate = run_scenario(scenario["frames"], "band_gate")
        assert (baseline, candidate) == EXPECTED[scenario["name"]], scenario["name"]