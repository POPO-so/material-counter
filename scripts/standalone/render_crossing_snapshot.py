"""Render compact crossing evidence without modifying the captured source."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path

import cv2
import numpy as np


def _read_artifact(event_path: Path, record: dict, name: str) -> bytes:
    summary=record.get('artifacts',{}).get(name)
    if not isinstance(summary,dict):
        raise ValueError(f'missing artifact summary: {name}')
    local=event_path.parent/name
    if local.is_file():
        data=local.read_bytes()
    else:
        rel=record.get('shared_frames',{}).get(name)
        if not rel:
            raise ValueError(f'missing artifact and shared reference: {name}')
        root=event_path.parent.parent.resolve()
        source=(root/rel).resolve()
        if root not in source.parents or not source.is_file():
            raise ValueError(f'invalid or incomplete shared artifact: {rel}')
        data=source.read_bytes()
    if len(data)!=summary.get('bytes') or hashlib.sha256(data).hexdigest()!=summary.get('sha256'):
        raise ValueError(f'artifact digest mismatch: {name}')
    return data


def render_event(event_json, output_dir):
    event_path=Path(event_json).resolve()
    record=json.loads(event_path.read_text(encoding='utf-8'))
    if record.get('schema') not in (
            'material-counter.crossing-snapshot.v1',
            'material-counter.crossing-snapshot.v2') or record.get('storage_mode')!='compact':
        raise ValueError('renderer requires a complete compact event.json')
    if record.get('context_complete') is None or not isinstance(record.get('frames'),list):
        raise ValueError('incomplete event metadata')
    out=Path(output_dir)
    out.mkdir(parents=True,exist_ok=False)
    for meta in record['frames']:
        prefix='frame-{:010d}'.format(meta['frame_id'])
        gray_blob=_read_artifact(event_path,record,prefix+'.gray.png')
        rle_blob=_read_artifact(event_path,record,prefix+'.rle.npz')
        gray=cv2.imdecode(np.frombuffer(gray_blob,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise ValueError(f'invalid gray PNG for frame {meta["frame_id"]}')
        with np.load(io.BytesIO(rle_blob),allow_pickle=False) as data:
            runs=data['runs']; rows=data['row_projection']; columns=data['column_projection']
        packet=meta['packet']; height=int(packet['height']); width=int(packet['width'])
        if gray.shape!=(height,width): raise ValueError('gray dimensions do not match metadata')
        binary=np.zeros((height,width),dtype=np.uint8)
        for word in runs:
            value=int(word); y=value>>20; x0=(value>>10)&1023; x1=value&1023
            if y>=height or x0>x1 or x1>=width: raise ValueError('invalid packed RLE run')
            binary[y,x0:x1+1]=255
        if not np.array_equal(binary.sum(axis=1)//255,rows) or not np.array_equal(binary.sum(axis=0)//255,columns):
            raise ValueError('RLE projections do not match reconstructed binary')
        annotated=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
        line=meta['count_line']; pos=line['position']
        points=((pos,0),(pos,height-1)) if line['axis']=='x' else ((0,pos),(width-1,pos))
        cv2.line(annotated,*points,(0,255,255),1)
        for box in meta['rejected_detections']:
            cv2.rectangle(annotated,(box['x_min'],box['y_min']),(box['x_max'],box['y_max']),(0,0,255),1)
        for box in meta['detections']:
            cv2.rectangle(annotated,(box['x_min'],box['y_min']),(box['x_max'],box['y_max']),(0,255,0),1)
        for track in meta['tracks']:
            box=track['detection']
            label='{}:{} C{} A{} K{} M{}'.format(meta['trajectory_epoch'],track['track_id'],int(track['confirmed']),int(track['armed']),int(track['counted']),track['missed'])
            cv2.putText(annotated,label,(box['x_min'],max(10,box['y_min']-4)),cv2.FONT_HERSHEY_SIMPLEX,.32,(255,160,0),1)
        cv2.putText(annotated,'frame={} count={} paused={}'.format(meta['frame_id'],meta['total_count'],meta['paused']),(5,16),cv2.FONT_HERSHEY_SIMPLEX,.45,(0,255,255),1)
        for suffix,image in (('gray',gray),('binary',binary),('annotated',annotated)):
            ok,encoded=cv2.imencode('.png',image,[cv2.IMWRITE_PNG_COMPRESSION,1])
            if not ok: raise OSError('render PNG encoding failed')
            (out/(prefix+'.'+suffix+'.png')).write_bytes(encoded.tobytes())
    return {'frames':len(record['frames']),'output_dir':str(out)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('event_json',type=Path)
    parser.add_argument('output_dir',type=Path)
    args=parser.parse_args()
    print(json.dumps(render_event(args.event_json,args.output_dir),indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
