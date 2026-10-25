"""Offline diagnostic; no board imports or hardware actions."""
import sys,json,hashlib,time,warnings
from pathlib import Path
import cv2,numpy as np
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/"scripts/wo0016"),str(ROOT/"scripts/wo0015")]
import live_calibration as calibration
import run_live_monitor as live
from stream_counter import MaskCounter
files=sorted((ROOT/"data/calibration/lighting-20261025").glob("lighting_*"))
report={"kind":"offline; no physical accuracy claim","files":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},"recordings":[]}
raw=cv2.imread(str(next(p for p in files if p.suffix==".png")),0)
_,binary=calibration.MonochromePreprocessor(mapping="lighting-calibrated")(raw)
config=live.finite.counting_configuration(live.parser().parse_args([]))
result=MaskCounter(**config).update(0,binary)
report["counting_config"]=config
report["preprocessing_config"]=calibration.frame_geometry("lighting-calibrated")
report["snapshot_output"]= [{"area":d.area,"centroid":[d.centroid_x,d.centroid_y],
                                "size_hint":live.finite.lighting_profile.classify_area(d.area),
                                "material_hint":live.finite.lighting_profile.classify_material_area(d.area)}
                               for d in result.detections]
report["operator_confirmed_single_part_names"]=[
 {"pixels":80,"size_hint":"small","material":"M6平垫圈","area_band_provisional":True},
 {"pixels":111,"size_hint":"medium","material":"M6螺母","area_band_provisional":True},
 {"pixels":150,"size_hint":"large","material":"M8螺母","area_band_provisional":True},
]
roi=(raw[20:380]>100).astype(np.uint8)
contours,hierarchy=cv2.findContours(roi,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
parts=[]
for i,c in enumerate(contours):
 if hierarchy[0,i,3]!=-1: continue
 outer=cv2.contourArea(c);hole=0;child=int(hierarchy[0,i,2])
 while child!=-1:
  hole+=cv2.contourArea(contours[child]);child=int(hierarchy[0,child,0])
 if outer-hole>=60:parts.append({"outer":outer,"hole":hole,"net":outer-hole})
report["source_contours"]=sorted(parts,key=lambda d:d["net"])
touching_path=ROOT/"data/calibration/lighting-20261025/lighting_20250509T174438_72fe589d453c4f0c9bdf3e3f265b485d.png"
touching_raw=cv2.imread(str(touching_path),0)
_,touching_binary=calibration.MonochromePreprocessor(mapping="lighting-calibrated")(touching_raw)
touching_counter=MaskCounter(**config)
touching_result=touching_counter.update(0,touching_binary)
assert len(touching_result.detections)==15
assert sum(d.area for d in touching_result.detections)==np.count_nonzero(touching_binary)
groups=[]
for top,bottom in ((45,85),(90,125),(130,180)):
 row=[d for d in touching_result.detections if top<=d.centroid_y<bottom]
 groups.append({"output_y_range":[top,bottom],"left_count":sum(d.centroid_x<160 for d in row),"right_count":sum(d.centroid_x>160 for d in row)})
 assert groups[-1]["left_count"]==3 and groups[-1]["right_count"]==2
raw_count,_,raw_stats,_=cv2.connectedComponentsWithStats(touching_binary,8)
touching_json=json.loads(touching_path.with_suffix(".json").read_text(encoding="utf-8"))
report["touching_snapshot"]={"path":str(touching_path.relative_to(ROOT)),"source_contour_components":touching_json["metrics"]["part_count"],"output_raw_component_areas":sorted(int(raw_stats[i,cv2.CC_STAT_AREA]) for i in range(1,raw_count)),"visible_annotation_count":15,"split_count":len(touching_result.detections),"groups":groups,"output_detections":[{"area":d.area,"centroid":[d.centroid_x,d.centroid_y]} for d in touching_result.detections],"foreground_pixels":int(np.count_nonzero(touching_binary)),"partition_area":sum(d.area for d in touching_result.detections),"overexp_pct":touching_json["metrics"]["overexp_pct"],"status_overexp":touching_json["metrics"]["status_overexp"],"status_uniform":touching_json["metrics"]["status_uniform"],"hardware_applied":touching_json["hardware_applied"],"kind":"static image segmentation only; no physical counting accuracy claim"}
expected_legacy={"wo_touching_pair_motion_20261024_03":2,"wo_touching_three_motion_20261024_02":3,"wo_touching_three_motion_20261024_04":3}
for path in sorted((ROOT/"data/recorded/wo0017-touching-20261024").glob("*/camera_gray_only.npz")):
 arrays=np.load(path)
 key=next(k for k in arrays.files if arrays[k].ndim==3)
 frames=arrays[key]
 counter=MaskCounter(**config);warns=[];start=time.perf_counter()
 with warnings.catch_warnings(record=True) as captured:
  warnings.simplefilter("always")
  for i,gray in enumerate(frames):
   mask=np.where(gray>100,255,0).astype(np.uint8);mask[:30]=0;mask[210:]=0
   result=counter.update(i,mask)
 legacy=MaskCounter(**live.finite.counting_configuration(live.parser().parse_args(["--counting-profile","full-width-mixed"])))
 for i,gray in enumerate(frames):
  legacy_result=legacy.update(i,np.where(gray>128,255,0).astype(np.uint8))
 assert legacy_result.total_count==expected_legacy[path.parent.name]
 report["recordings"].append({"legacy_count":legacy_result.total_count,"legacy_expected":expected_legacy[path.parent.name],"path":str(path.relative_to(ROOT)),"frames":len(frames),"shape":list(frames.shape),"count":result.total_count,"warnings":[str(w.message) for w in captured],"seconds":time.perf_counter()-start,"limit":"Historical camera setup/optics; stored 320x240 Gray8 only, source net-area filtering cannot be reconstructed. Diagnostic only."})
output=Path(__file__).with_name("verification.json")
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
