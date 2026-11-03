from pathlib import Path
import sys
import numpy as np
import pytest

from scripts.rle.components import build_native
from scripts.rle.protocol import encode_mask, decode_packet
from scripts.rle.counter import RleCounter


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    return build_native(tmp_path_factory.mktemp("rle-counter") / ("rle.dll" if sys.platform=="win32" else "rle.so"))


def frame(mask, number=0, **kwargs):
    return decode_packet(encode_mask(mask, frame_id=number, **kwargs))


def _large_touching_ring_cluster() -> np.ndarray:
    """Four visible rings joined into one RLE component above the object cap."""
    outer_size = 50
    pitch = 51
    rows, columns = 1, 4
    mask = np.zeros((rows * pitch + 2, columns * pitch + 2), dtype=bool)
    for row in range(rows):
        for column in range(columns):
            top = 1 + row * pitch
            left = 1 + column * pitch
            mask[top:top + outer_size, left:left + outer_size] = True
            mask[top + 13:top + 37, left + 13:left + 37] = False
            if column + 1 < columns:
                mask[top + 24:top + 27,
                     left + outer_size:left + pitch] = True
    return mask


@pytest.mark.parametrize("provide_features", [False, True])
def test_large_rle_cluster_is_split_before_component_limit_rejection(library, provide_features):
    from scripts.rle.components import Component

    mask = np.zeros((400, 640), dtype=np.uint8)
    cluster = _large_touching_ring_cluster()
    mask[150:150 + cluster.shape[0], 180:180 + cluster.shape[1]] = cluster * 255
    assert int(mask.sum() // 255) > 3600

    counter = RleCounter(
        library=library, guarded_merge=True, single_intact_area=380,
        max_split_area=8192, min_split_child_area=80,
        max_area=3600, max_bbox_width=170, max_bbox_height=170,
    )
    features = None
    if provide_features:
        ys, xs = np.nonzero(mask)
        features = (Component(int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()),
                              len(xs), float(xs.mean()), float(ys.mean())),)
    result = counter.update_rle(frame(mask), region_features=features)

    assert len(result.raw_detections) == 4
    assert len(result.detections) == 4
    assert not counter.rejected_detections
    assert sum(d.area for d in result.raw_detections) == int(mask.sum() // 255)
    assert counter.last_backend == "native"


def test_rle_split_handles_eight_child_region_and_overwide_parent(library):
    outer_size = 23
    pitch = 24
    columns = 8
    cluster = np.zeros((pitch + 2, columns * pitch + 2), dtype=bool)
    for column in range(columns):
        top, left = 1, 1 + column * pitch
        cluster[top:top + outer_size, left:left + outer_size] = True
        cluster[top + 5:top + 18, left + 5:left + 18] = False
        if column + 1 < columns:
            cluster[top + 10:top + 13,
                    left + outer_size:left + pitch] = True
    assert 1200 < np.count_nonzero(cluster) <= 3600
    assert cluster.shape[1] - 2 > 170

    mask = np.zeros((400, 640), dtype=np.uint8)
    mask[150:150 + cluster.shape[0], 180:180 + cluster.shape[1]] = cluster * 255
    counter = RleCounter(
        library=library, guarded_merge=True, single_intact_area=380,
        max_split_area=8192, min_split_child_area=80,
        max_area=3600, max_bbox_width=170, max_bbox_height=170,
    )
    result = counter.update_rle(frame(mask))

    assert len(result.raw_detections) == columns
    assert len(result.detections) == columns
    assert not counter.rejected_detections


def test_rle_splits_fourteen_seed_clusters_from_conveyor_snapshots(library):
    fixture = Path(__file__).parent / "fixtures" / "conveyor-clusters-20261103.npz"
    with np.load(fixture, allow_pickle=False) as masks:
        for frame_id in (5298, 5307):
            crop = masks[f"frame_{frame_id}"]
            mask = np.zeros((400, 640), dtype=np.uint8)
            mask[100:100 + crop.shape[0], 180:180 + crop.shape[1]] = crop * 255
            counter = RleCounter(
                library=library, guarded_merge=True, single_intact_area=380,
                max_split_area=8192, min_split_child_area=80,
                max_area=3600, max_bbox_width=170, max_bbox_height=170,
            )
            result = counter.update_rle(frame(mask, frame_id))

            assert len(result.raw_detections) == 14
            assert len(result.detections) == 14
            assert not counter.rejected_detections
            assert sum(d.area for d in result.raw_detections) == int(crop.sum())


def test_rle_recovers_children_after_a_multihole_parent_crosses_the_line(library, monkeypatch):
    import cv2
    import scripts.rle.counter as module
    from scripts.standalone.count_controls import make_runtime_counter

    original_split = module.split_touching_component

    def preserve_large_parent(mask, **kwargs):
        if int(np.count_nonzero(mask)) > 700:
            return (np.asarray(mask) != 0,)
        return original_split(mask, **kwargs)

    monkeypatch.setattr(module, "split_touching_component", preserve_large_parent)
    counter = make_runtime_counter(library, profile="field-v1")

    def ring_cluster(offset, centers):
        mask = np.zeros((400, 640), dtype=np.uint8)
        for center_x in centers:
            cv2.circle(mask, (center_x + offset, 120), 13, 255, -1)
            cv2.circle(mask, (center_x + offset, 120), 6, 0, -1)
        return mask

    results = []
    for frame_id, offset in enumerate((250, 260, 270, 280, 290, 300)):
        result = counter.update_rle(
            frame(ring_cluster(offset, (14, 37)), frame_id),
            capture_monotonic_ns=(frame_id+1)*100_000_000,
        )
        results.append(result)
        assert not result.detections
        assert not result.raw_detections
        assert len(counter.rejected_detections) == 1
        assert not result.events

    first_split = counter.update_rle(
        frame(ring_cluster(0, (330, 360)), 6), capture_monotonic_ns=700_000_000
    )
    stable_split = counter.update_rle(
        frame(ring_cluster(0, (340, 370)), 7), capture_monotonic_ns=800_000_000
    )

    assert len(first_split.raw_detections) == 2
    assert not first_split.events
    assert len(stable_split.events) == 2
    assert all(event.inferred_gap for event in stable_split.events)
    assert stable_split.total_count == counter.total_count == 2
    assert not counter.rejected_detections


def test_regions_match_pixel_counter_without_full_mask(library, monkeypatch):
    from scripts.wo0016.stream_counter import MaskCounter
    mask=np.zeros((400,640),np.uint8)
    mask[20:40,30:50]=255;mask[90:110,80:100]=255;mask[95:105,85:95]=0
    settings=dict(width=640,height=400,line_y=200,line_x=320,roi_mode="full",min_area=12,max_area=3600,
                  max_bbox_width=170,max_bbox_height=170,component_cluster_dx=0)
    expected=MaskCounter(**settings).update(0,mask)
    def forbidden(*args,**kwargs):raise AssertionError("ordinary path reconstructed a mask")
    monkeypatch.setattr("scripts.rle.counter.local_mask",forbidden)
    result=RleCounter(library=library,**settings).update_rle(frame(mask))
    assert result.detections==expected.detections
    assert result.row_projection==expected.row_projection
    assert result.column_projection==expected.column_projection


def test_tracking_crosses_line_once(library):
    counter=RleCounter(library=library,max_step=50,max_area=3600)
    for i,y in enumerate((160,175,190,205,220,235)):
        mask=np.zeros((400,640),np.uint8);mask[y:y+10,100:110]=255
        result=counter.update_rle(frame(mask,i))
    assert result.total_count==1


def test_overflow_does_not_update_tracker_and_duplicate_rejected(library):
    mask=np.zeros((400,640),np.uint8);mask[:,::2]=255
    counter=RleCounter(library=library)
    bad=frame(mask,3,max_runs=2)
    assert counter.update_rle(bad) is None
    assert counter.total_count==0 and counter.last_frame_id is None
    with pytest.raises(ValueError,match="increas"):
        counter.update_rle(bad)


def test_mismatched_roi_rejected(library):
    counter=RleCounter(library=library,roi_top=50,roi_bottom=300)
    with pytest.raises(ValueError,match="ROI"):
        counter.update_rle(frame(np.zeros((400,640),np.uint8)))


def test_complex_candidate_uses_only_local_mask(library,monkeypatch):
    import scripts.rle.counter as module
    mask=np.zeros((400,640),np.uint8);mask[100:110,200:220]=255
    shapes=[]
    original=module.local_mask
    def local(*args,**kwargs):
        out=original(*args,**kwargs);shapes.append(out.shape);return out
    monkeypatch.setattr(module,"local_mask",local)
    monkeypatch.setattr(module,"split_touching_component",lambda cropped,**kwargs: [cropped])
    counter=RleCounter(library=library,guarded_merge=True,single_intact_area=100,max_split_area=500,
                       max_area=1000,max_merge_area=500)
    result=counter.update_rle(frame(mask))
    assert shapes==[(10,20)] and len(result.detections)==1


def test_verified_features_skip_native_when_no_guarded_merge_candidate(library,monkeypatch):
    from scripts.rle.components import Component
    import scripts.rle.counter as module
    native_analyze=module.analyze_runs
    mask=np.zeros((400,640),np.uint8);mask[10:20,10:20]=255;mask[100:110,200:220]=255
    expected=RleCounter(library=library,max_area=3600).update_rle(frame(mask))
    # Construct independently measured feature components from exact mask pixels.
    regions=[]
    for ys,xs in ((np.arange(10,20),np.arange(10,20)),(np.arange(100,110),np.arange(200,220))):
        yy,xx=np.nonzero(mask[ys.min():ys.max()+1,xs.min():xs.max()+1])
        regions.append(Component(int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max()),len(xx),
                                 float(xs.min()+xx.mean()),float(ys.min()+yy.mean())))
    regions=tuple(regions)
    monkeypatch.setattr(module,"analyze_runs",lambda *a,**k: (_ for _ in ()).throw(AssertionError("native used")))
    counter=RleCounter(library=library,max_area=3600)
    result=counter.update_rle(frame(mask),region_features=regions)
    assert result.detections==expected.detections
    assert result.total_count==expected.total_count
    assert counter.last_backend=="pl_features"
    monkeypatch.setattr(module,"analyze_runs",native_analyze)
    native=RleCounter(library=library,max_area=3600)
    accelerated=RleCounter(library=library,max_area=3600)
    for number,y in enumerate((180,190,200,210,220)):
        moving=np.zeros((400,640),np.uint8);moving[y:y+10,300:310]=255
        ys,xs=np.nonzero(moving)
        component=Component(int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max()),len(xs),
                            float(xs.mean()),float(ys.mean()))
        plain=native.update_rle(frame(moving,number))
        featured=accelerated.update_rle(frame(moving,number),region_features=(component,))
        assert featured.detections==plain.detections
        assert featured.events==plain.events
        assert featured.total_count==plain.total_count


@pytest.mark.parametrize("area,should_split",[(100,False),(150,True)])
def test_guarded_merge_candidate_keeps_whole_frame_native(library,monkeypatch,area,should_split):
    from scripts.rle.components import Component
    import scripts.rle.counter as module
    mask=np.zeros((400,640),np.uint8);mask[100:110,200:200+area//10]=255
    features=(Component(200,100,199+area//10,109,area,199.5+area/20,104.5),)
    calls=[]; real=module.analyze_runs
    def spy(*args,**kwargs):calls.append(True);return real(*args,**kwargs)
    monkeypatch.setattr(module,"analyze_runs",spy)
    counter=RleCounter(library=library,guarded_merge=True,single_intact_area=100,max_split_area=500,
                       max_area=1000,max_merge_area=500)
    counter.update_rle(frame(mask),region_features=features)
    assert bool(calls) is should_split
    assert counter.last_backend==("native" if should_split else "pl_features")
