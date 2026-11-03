"""Apply local commands at a counting frame boundary without resetting tracks."""
from dataclasses import replace


FIELD_V1_SETTINGS = dict(
    predictive_association=True, motion_timebase_s=.1,
    max_step=200, max_lateral_step=12, strict_lateral_gate=True,
    max_association_interval_s=.5, guarded_merge=True,
    single_intact_area=380, max_split_area=8192, min_split_child_area=80,
    cluster_parent_recovery=True,
    # Admission limits, not a preemptive deadline for a single OpenCV call.
    split_budget_ms=8.0, split_max_candidates=2, split_max_pixels=65536,
    split_retry_interval_s=.15,
)


def make_runtime_counter(library, *, profile='legacy'):
    """Use streaming track retirement; never retain completed trajectory history."""
    from scripts.rle.counter import RleCounter
    if profile not in ('legacy', 'field-v1'):
        raise ValueError('unknown counting profile')
    return RleCounter(library=library,discard_finished_tracks=True,
                      count_axis="x",line_x=320,direction=1,
                      **(FIELD_V1_SETTINGS if profile=='field-v1' else {}))


def advance_count(counter, controls, packet, commands=(), *, capture_monotonic_ns=None,
                  region_features=None):
    previous_clear = controls.clear_requests
    controls.apply(commands)
    if controls.clear_requests != previous_clear:
        counter.total_count = 0
        clear_parents = getattr(counter, 'clear_pending_cluster_parents', None)
        if callable(clear_parents):
            clear_parents()
    before = counter.total_count
    result = counter.update_rle(packet, capture_monotonic_ns=capture_monotonic_ns,
                                region_features=region_features)
    if controls.paused:
        # Association and per-track counted flags still advance. Only accounting
        # is suppressed, so a crossing during pause cannot be replayed later.
        counter.total_count = before
        if result is not None:
            result = replace(result, total_count=before, events=(),
                             suppressed_events=result.suppressed_events + result.events)
    return result
