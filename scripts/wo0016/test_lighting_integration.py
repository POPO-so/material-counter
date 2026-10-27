import sys
from pathlib import Path
import cv2
import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).parent))
import run_live_monitor as live
import run_stream_counter as finite
from stream_counter import MaskCounter
import live_calibration as calibration

SNAPSHOT = Path(__file__).parents[2] / "data/calibration/lighting-20261025/lighting_20250509T172731_2856b9c8ba9d4089af9c5bfdac1cb04e.png"

def test_live_uses_calibrated_profile():
    args = live.parser().parse_args([])
    assert args.counting_profile == "lighting-calibrated"
    config = finite.counting_configuration(args)
    assert config["min_area"] == 15
    assert config["min_split_child_area"] == 48
    assert (config["roi_top"], config["roi_bottom"]) == (30, 210)
    assert config["single_intact_area"] == 64
    assert config["split_min_hole_area"] == 16
    assert finite.preprocessing_configuration(args)["binary_thresh"] == 100


def test_output_roi_area_filter_mode_is_explicit_and_keeps_calibrated_count_roi():
    args = live.parser().parse_args(["--lighting-area-filter", "output-roi"])
    geometry = finite.preprocessing_configuration(args)
    assert geometry["mapping"] == "lighting-calibrated-output-roi"
    assert geometry["active_output_roi_xyxy"] == [0, 30, 320, 210]
    assert geometry["area_filter_mode"] == "output_roi_connected_components"
    assert geometry["source_min_net_contour_area"] is None
    assert geometry["source_min_net_contour_area_configured"] == 60
    assert geometry["runtime_min_foreground_pixels"] == 15


def test_output_roi_area_filter_skips_source_ccomp_and_keeps_15_pixel_gate():
    source = np.zeros((400, 640), np.uint8)
    source[100:108, 200:208] = 200  # source contour area 49; after 2x sampling: 16 px
    full_filter = np.empty((240, 320), np.uint8)
    output_roi_filter = np.empty_like(full_filter)
    calibration.resize_monochrome_into(
        source, full_filter, mapping="lighting-calibrated")
    calibration.resize_monochrome_into(
        source, output_roi_filter, mapping="lighting-calibrated-output-roi")
    assert np.count_nonzero(full_filter[30:210]) == 0
    assert np.count_nonzero(output_roi_filter[30:210]) == 16

    binary = np.where(output_roi_filter > 100, 255, 0).astype(np.uint8)
    counter = MaskCounter(roi_mode="full", roi_top=30, roi_bottom=210, min_area=15,
                          count_axis="x", line_x=160, max_step=48)
    result = counter.update(0, binary)
    assert [detection.area for detection in result.detections] == [16]

def test_roi_offsets_and_crossing_stay_in_output_frame():
    counter = MaskCounter(roi_mode="full", roi_top=30, roi_bottom=210, min_area=60,
                          count_axis="x", line_x=160, max_step=48)
    for frame, x in enumerate((130, 145, 165)):
        mask = np.zeros((240,320), np.uint8)
        mask[50:60,x:x+8] = 255
        mask[20:29,100:110] = 255
        mask[210:220,100:110] = 255
        result = counter.update(frame,mask)
        assert len(result.detections) == 1
        assert result.detections[0].centroid_y == 54.5
    assert result.total_count == 1
    assert result.events[0].centroid_y == 54.5

def test_snapshot_three_materials_remain_three_and_count_once_each():
    raw = cv2.imread(str(SNAPSHOT), cv2.IMREAD_GRAYSCALE)
    assert raw is not None
    gray, binary = calibration.MonochromePreprocessor(mapping="lighting-calibrated")(raw)
    counter = MaskCounter(**finite.counting_configuration(live.parser().parse_args([])))
    result = counter.update(0,binary)
    assert sorted(d.area for d in result.detections) == [80,111,150]
    assert sorted(d.y_min for d in result.detections) == [91,117,144]
    moving = MaskCounter(**finite.counting_configuration(live.parser().parse_args([])))
    for frame, shift in enumerate((-55,-35,-15,5,25)):
        mask = cv2.warpAffine(binary,np.float32([[1,0,shift],[0,1,0]]),(320,240),flags=cv2.INTER_NEAREST)
        result = moving.update(frame,mask)
        assert len(result.detections) == 3
    assert result.total_count == 3

def test_invalid_roi_is_rejected():
    for top,bottom in ((30,30),(-1,210),(30,241)):
        with pytest.raises(ValueError):
            MaskCounter(roi_top=top,roi_bottom=bottom)


def test_area_hints_are_provisional_and_reach_live_events():
    from live_monitor_state import LiveMonitorState
    from types import SimpleNamespace
    from dataclasses import make_dataclass
    state = LiveMonitorState(mode="mock")
    state.counting_profile = "lighting-calibrated"
    event = SimpleNamespace(track_id=1,frame_id=1,inferred_gap=False)
    result = make_dataclass("Result", ["total_count","detections","events","tracks"])(
        1,(),(event,),({"track_id":1,"detection":{"area":111}},))
    state.complete_frame(result)
    data = state.snapshot()["dashboard"]
    assert data["events"][-1]["area_size_hint"] == "medium"
    assert data["events"][-1]["material_hint"] == "M6螺母"
    assert data["area_hints_provisional"] is True


@pytest.mark.parametrize("mapping", [
    "lighting-calibrated",
    "lighting-calibrated-output-roi",
])
def test_capture_applies_and_rechecks_controls_before_delivering_frames(monkeypatch, mapping):
    import asyncio
    from types import SimpleNamespace
    calls=[]
    class Capture:
        def isOpened(self): return True
        def set(self,*args): calls.append("mode"); return True
        def get(self,prop):
            return {cv2.CAP_PROP_FOURCC:cv2.VideoWriter_fourcc(*"MJPG"),
                    cv2.CAP_PROP_FRAME_WIDTH:640,cv2.CAP_PROP_FRAME_HEIGHT:400,
                    cv2.CAP_PROP_FPS:60}.get(prop,1)
        def read(self):
            calls.append("read")
            ok, encoded = cv2.imencode(".jpg", np.full((400,640),101,np.uint8))
            assert ok
            return True, encoded.reshape(-1)
        def release(self): calls.append("release")
    fake=SimpleNamespace(**{name:getattr(cv2,name) for name in (
        "CAP_V4L2","CAP_PROP_FOURCC","CAP_PROP_FRAME_WIDTH","CAP_PROP_FRAME_HEIGHT",
        "CAP_PROP_FPS","CAP_PROP_BUFFERSIZE","CAP_PROP_CONVERT_RGB","IMREAD_GRAYSCALE",
        "INTER_NEAREST","VideoWriter_fourcc","resize")})
    fake.VideoCapture=lambda *args:Capture()
    fake.imdecode=cv2.imdecode
    required={"auto_exposure":1,"exposure_time_absolute":50,"brightness":0,"gamma":100}
    applied={"required_controls_verified":True,"required_controls":required,
             "gain":{"requested":0,"value":None,"status":"not_exposed",
                     "verification":"not_verified","message":"UVC 驱动未暴露 gain 控件，未能通过 V4L2 验证",
                     "driver_response":"unknown control 'gain'","set_response":"unknown control 'gain'"}}
    verified={"required_controls_verified":True,"required_controls":required,
              "gain":{"requested":0,"value":None,"status":"not_exposed",
                      "verification":"not_verified","message":"UVC 驱动未暴露 gain 控件，未能通过 V4L2 验证",
                      "driver_response":"unknown control 'gain'","set_response":"not_attempted"}}
    monkeypatch.setattr(finite.lighting_profile,"apply_camera_controls",lambda d:calls.append("apply") or applied)
    monkeypatch.setattr(finite.lighting_profile,"verify_camera_controls",lambda d:calls.append("verify") or dict(verified, gain=dict(verified["gain"])))
    capture_metadata={}
    callbacks=[]
    async def collect():
        return [frame async for frame in finite._camera_gray_frames_impl(
            "/dev/fake",max_frames=1,cv2_module=fake,frame_mapping=mapping,
            capture_metadata=capture_metadata,camera_controls_callback=callbacks.append)]
    frames=asyncio.run(collect())
    assert len(frames)==1
    assert calls.index("mode") < calls.index("apply") < calls.index("read") < calls.index("verify")
    assert not frames[0][:30].any() and not frames[0][210:].any()
    assert capture_metadata["hardware_applied"] == required
    assert capture_metadata["camera_control_status"]["gain"]["message"] == (
        "UVC 驱动未暴露 gain 控件，未能通过 V4L2 验证")
    assert capture_metadata["camera_control_status"]["gain"]["set_response"] == "unknown control 'gain'"
    assert callbacks == [capture_metadata["camera_control_status"]]
    assert calls[-1]=="release"


def test_source_net_area_60_survives_scaling_but_smaller_component_is_removed():
    raw=np.full((400,640),27,np.uint8)
    raw[80:87,80:91]=210   # contour 10*6=60; 24 sampled foreground pixels
    raw[100:107,80:90]=210  # contour 9*6=54
    _,binary=calibration.MonochromePreprocessor(mapping="lighting-calibrated")(raw)
    counter=MaskCounter(**finite.counting_configuration(live.parser().parse_args([])))
    result=counter.update(0,binary)
    assert len(result.detections)==1
    assert result.detections[0].area==24


def test_small_touching_pair_partitions_and_prediction_recovers_single():
    raw=cv2.imread(str(SNAPSHOT),cv2.IMREAD_GRAYSCALE)
    _,binary=calibration.MonochromePreprocessor(mapping="lighting-calibrated")(raw)
    small=binary[91:103,157:168]
    mask=np.zeros_like(binary)
    mask[91:103,130:141] |= small
    mask[91:103,140:151] |= small
    counter=MaskCounter(**finite.counting_configuration(live.parser().parse_args([])))
    result=counter.update(0,mask)
    assert len(result.detections)==2
    assert sum(d.area for d in result.detections)==np.count_nonzero(mask)
    moving=MaskCounter(**finite.counting_configuration(live.parser().parse_args([])))
    for frame,x in enumerate((130,140,None,170)):
        mask=np.zeros_like(binary)
        if x is not None: mask[91:103,x:x+11]=small
        result=moving.update(frame,mask)
    assert result.total_count==1
    assert result.events[0].inferred_gap is True


def test_finite_mock_cli_reports_selected_calibration(capsys):
    import json
    assert finite.main(["--counting-profile","lighting-calibrated"])==0
    data=json.loads(capsys.readouterr().out)
    assert data["counting_profile"]=="lighting-calibrated"
    assert data["preprocessing_config"]["binary_thresh"]==100
    assert data["total_count"]==5
    assert data["min_area"]==15


def test_calibrated_resize_rejects_output_aliasing_original_camera_evidence():
    raw=np.full((400,640),200,np.uint8)
    output=raw.ravel()[:240*320].reshape(240,320)
    with pytest.raises(ValueError,match="independent"):
        calibration.resize_monochrome_into(raw,output,mapping="lighting-calibrated")


TOUCHING_SNAPSHOT = SNAPSHOT.with_name("lighting_20250509T174438_72fe589d453c4f0c9bdf3e3f265b485d.png")

def test_real_touching_snapshot_preserves_fifteen_visible_parts():
    # Visual annotation: left groups contain three parts each, right groups two.
    # This is static-image segmentation evidence, not a moving production count.
    raw=cv2.imread(str(TOUCHING_SNAPSHOT),cv2.IMREAD_GRAYSCALE)
    assert raw is not None
    _,binary=calibration.MonochromePreprocessor(mapping="lighting-calibrated")(raw)
    counter=MaskCounter(**finite.counting_configuration(live.parser().parse_args([])))
    result=counter.update(0,binary)
    assert len(result.detections)==15
    assert sum(d.area for d in result.detections)==np.count_nonzero(binary)
    for top,bottom in ((45,85),(90,125),(130,180)):
        row=[d for d in result.detections if top <= d.centroid_y < bottom]
        assert len([d for d in row if d.centroid_x<160])==3
        assert len([d for d in row if d.centroid_x>160])==2


def test_calibrated_hole_options_reject_invalid_values():
    for options in ({"split_min_hole_area":0},{"split_min_hole_area":True},
                    {"split_min_hole_area":1.5}):
        with pytest.raises(ValueError):
            MaskCounter(**options)
