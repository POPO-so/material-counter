"""Compare PS region paths on identical synthetic masks; not a camera FPS test."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import platform
import sys
import time
import numpy as np
if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.rle.counter import RleCounter,MaskCounter
from scripts.rle.protocol import encode_mask,decode_packet
from scripts.rle.components import build_native,analyze_runs


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--native-lib',type=Path,required=True)
    parser.add_argument('--iterations',type=int,default=100)
    args=parser.parse_args(argv)
    if args.output.exists():raise FileExistsError(args.output)
    if args.iterations<1:parser.error('iterations must be positive')
    if not args.native_lib.exists():build_native(args.native_lib)
    report=dict(architecture=platform.machine(),python=platform.python_version(),
        scope='Synthetic masks, same full-frame ROI, new empty tracker each sample; PS only, excludes camera, input encoding and DMA',cases=[])
    for name in ('empty','six_rings','dense_fragments'):
        mask=np.zeros((400,640),np.uint8)
        if name=='six_rings':
            for y in (100,220):
                for x in (100,300,500):
                    mask[y:y+24,x:x+24]=255;mask[y+8:y+16,x+8:x+16]=0
        if name=='dense_fragments':
            for y in range(10,390,20):
                for x in range(10,630,20):mask[y:y+5,x:x+5]=255
        options=dict(min_area=1,max_area=None,max_bbox_width=640,max_bbox_height=400,
            width=640,height=400,line_y=200,line_x=320,roi_mode='full',component_cluster_dx=0)
        timings={key:[] for key in ('pixel_counter_ms','rle_decode_ms','rle_native_ms','rle_counter_ms')}
        for i in range(args.iterations+3):
            pixels=MaskCounter(**options)
            rle=RleCounter(library=args.native_lib,**options)
            packet=encode_mask(mask,frame_id=i)
            t=time.perf_counter();old=pixels.update(i,mask);t1=time.perf_counter()
            frame=decode_packet(packet);t2=time.perf_counter()
            analysis=analyze_runs(frame.runs,library=args.native_lib);t3=time.perf_counter()
            result=rle.update_rle(frame);t4=time.perf_counter()
            if old.detections!=result.detections:raise RuntimeError('region paths differ')
            if i>=3:
                for key,value in zip(timings,((t1-t)*1000,(t2-t1)*1000,(t3-t2)*1000,(t4-t3)*1000)):
                    timings[key].append(value)
        report['cases'].append(dict(name=name,runs=frame.run_count,rx_bytes=len(packet),
            foreground=frame.total_fg,components=len(analysis.components),
            timings={key:dict(mean_ms=float(np.mean(value)),p95_ms=float(np.percentile(value,95)))
                     for key,value in timings.items()}))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(report))
    return 0


if __name__=='__main__':raise SystemExit(main())
