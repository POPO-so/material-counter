"""The conveyor profile must preserve physical order and count each nut once."""
import pytest

from scripts.wo0016.stream_counter import Detection, MaskCounter
from scripts.standalone.crossing_replay import load_fixture, replay_window


def box(x, y=100):
    return Detection(x-5, x+5, y-5, y+5, 80, float(x), float(y))


def tracker(**overrides):
    settings = dict(width=640, height=400, count_axis='x', line_x=320,
        roi_mode='full', max_area=3600, max_step=200, max_lateral_step=12,
        strict_lateral_gate=True, motion_timebase_s=.1, predictive_association=True,
        max_association_interval_s=.5, discard_finished_tracks=True)
    settings.update(overrides)
    return MaskCounter(**settings)


def test_same_row_tracks_keep_order_when_rear_reaches_front_previous_position():
    counter = tracker()
    ids = []
    for i, xs in enumerate(((180,230),(210,260),(280,330),(350,400),(420,470))):
        result = counter.update_detections(i, [box(x) for x in xs],
                                          capture_monotonic_ns=(i+1)*100_000_000)
        ids.append([t['track_id'] for t in sorted(result.tracks, key=lambda t:t['detection']['centroid_x']) if not t['missed']])
    assert all(pair == [1,2] for pair in ids)
    assert result.total_count == 2


def test_different_lanes_can_overtake_without_id_swap():
    counter = tracker()
    for i, ((x1,x2),ms) in enumerate(zip(((200,220),(230,250),(300,280),(370,310),(440,340)),(100,200,300,400,500))):
        result=counter.update_detections(i,[box(x1,100),box(x2,130)],capture_monotonic_ns=ms*1_000_000)
        live={t['track_id']:t['detection']['centroid_y'] for t in result.tracks if not t['missed']}
        assert live == {1:100.,2:130.}
    assert result.total_count == 2


def test_stale_counted_id_does_not_attach_to_next_nut_after_long_stall():
    counter=tracker()
    for i,x in enumerate((260,295,335)):
        result=counter.update_detections(i,[box(x)],capture_monotonic_ns=(i+1)*100_000_000)
    assert result.total_count == 1
    result=counter.update_detections(3,[box(350)],capture_monotonic_ns=2_000_000_000)
    assert not result.events
    assert result.tracks[-1]['track_id'] == 2
    assert not result.tracks[-1]['armed']


def test_backward_motion_and_late_birth_do_not_create_count_events():
    counter=tracker()
    for i,x in enumerate((360,350,340,330,320)):
        result=counter.update_detections(i,[box(x)],capture_monotonic_ns=(i+1)*100_000_000)
    assert result.total_count == 0


def test_timestamp_profile_repairs_saved_single_and_same_row_windows():
    overrides=dict(max_step=200, max_lateral_step=12, strict_lateral_gate=True,
                   motion_timebase_s=.1, predictive_association=True,
                   max_association_interval_s=.5)
    expected={'2310-2317':1,'2325-2331':1,'2380-2387':1,'2645-2649':2,'2485-2489':1}
    for window in load_fixture()['windows']:
        # A stale initial track has no proven timestamp: use the latest observed
        # tracks only, never guess a historical time/velocity for missed tracks.
        import copy
        window=copy.deepcopy(window)
        initial=window['initial']
        initial['tracks']=[t for t in initial['tracks'] if t['last_frame']==initial['frame_id']]
        result=replay_window(window, overrides)
        assert result['added_count']==expected[window['name']],window['name']


def test_predictive_mode_requires_timebase():
    with pytest.raises(ValueError,match='timebase'):
        MaskCounter(predictive_association=True)


def test_counted_front_disappearing_does_not_steal_rear_identity():
    counter=tracker()
    for i,xs in enumerate(((180,230),(210,260),(280,330),(350,))):
        result=counter.update_detections(i,[box(x) for x in xs],capture_monotonic_ns=(i+1)*100_000_000)
    assert result.total_count==2
    live=[t for t in result.tracks if not t['missed']]
    assert len(live)==1 and live[0]['track_id']==1


@pytest.mark.parametrize('axis,direction',[('x',1),('x',-1),('y',1),('y',-1)])
def test_direction_and_axis_count_each_track_only_once(axis,direction):
    counter=tracker(count_axis=axis,line_y=200,direction=direction)
    line=320 if axis=='x' else 200
    for i,offset in enumerate((-60,-35,-10,15,40,65)):
        position=line+direction*offset
        items=[box(position,100)] if axis=='x' else [box(100,position)]
        result=counter.update_detections(i,items,capture_monotonic_ns=(i+1)*100_000_000)
    assert result.total_count==1


def test_ordered_matching_three_same_lane_targets_with_variable_frame_intervals():
    counter=tracker()
    elapsed=100_000_000
    offset=0
    for i,(dt,dx) in enumerate(((0,0),(60,45),(100,75),(120,90),(80,60),(100,75))):
        elapsed+=dt*1_000_000;offset+=dx
        xs=[100+offset,170+offset,240+offset]
        items=[box(x) for x in xs if x<630]
        result=counter.update_detections(i,items,capture_monotonic_ns=elapsed)
        visible=sorted((t for t in result.tracks if not t['missed']),key=lambda t:t['detection']['centroid_x'])
        assert [t['track_id'] for t in visible]==list(range(1,len(items)+1))
    assert result.total_count==3
