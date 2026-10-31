"""Apply local commands at a counting frame boundary without resetting tracks."""
from dataclasses import replace


def make_runtime_counter(library):
    """Use streaming track retirement; never retain completed trajectory history."""
    from scripts.rle.counter import RleCounter
    return RleCounter(library=library,discard_finished_tracks=True,
                      count_axis="x",line_x=320,direction=1)


def advance_count(counter, controls, packet, commands=(), *, capture_monotonic_ns=None):
    previous_clear = controls.clear_requests
    controls.apply(commands)
    if controls.clear_requests != previous_clear:
        counter.total_count = 0
    before = counter.total_count
    result = counter.update_rle(packet, capture_monotonic_ns=capture_monotonic_ns)
    if controls.paused:
        # Association and per-track counted flags still advance. Only accounting
        # is suppressed, so a crossing during pause cannot be replayed later.
        counter.total_count = before
        if result is not None:
            result = replace(result, total_count=before, events=(),
                             suppressed_events=result.suppressed_events + result.events)
    return result
