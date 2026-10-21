from pathlib import Path
import json, csv, hashlib, statistics
repo = Path(__file__).resolve().parents[3]
source_dir = repo / 'data/results/wo0016-stageB2-attempt3-20261020-6f13f0f0'
frames_path = source_dir / 'var/lib/wo0016/attempts/6f13f0f0-4433-445f-a023-fa403685aeee/attempt-3/frame-results.json'
result_path = source_dir / 'home/xilinx/board-runtime-currentboot-v2/stageb2-attempt-3-result.json'
frames = json.loads(frames_path.read_text(encoding='utf-8'))
result = json.loads(result_path.read_text(encoding='utf-8'))
ranges = [(24,30),(114,135)]
records=[]
summary=[]
for lo,hi in ranges:
    selected=[f for f in frames if lo <= f['frame_id'] <= hi]
    for f in selected:
        comps=[]
        for i,d in enumerate(f.get('detections',[]),1):
            c={
                'component_index':i,
                'centroid_x':d['centroid_x'], 'centroid_y':d['centroid_y'], 'area':d['area'],
                'bbox_inclusive':{'x_min':d['x_min'],'y_min':d['y_min'],'x_max':d['x_max'],'y_max':d['y_max']},
            }
            comps.append(c)
            records.append({'range':f'{lo}-{hi}','frame_id':f['frame_id'],'roi_foreground_pixels':f['roi_foreground_pixels'],'ccl_executed':f['ccl_executed'],'component_index':i,'centroid_x':d['centroid_x'],'centroid_y':d['centroid_y'],'area':d['area'],'x_min':d['x_min'],'y_min':d['y_min'],'x_max':d['x_max'],'y_max':d['y_max']})
        if not comps:
            records.append({'range':f'{lo}-{hi}','frame_id':f['frame_id'],'roi_foreground_pixels':f['roi_foreground_pixels'],'ccl_executed':f['ccl_executed'],'component_index':'','centroid_x':'','centroid_y':'','area':'','x_min':'','y_min':'','x_max':'','y_max':''})
        summary.append({'frame_id':f['frame_id'],'range':f'{lo}-{hi}','roi_foreground_pixels':f['roi_foreground_pixels'],'ccl_executed':f['ccl_executed'],'component_count':len(comps),'components':comps})
out=Path(__file__).resolve().parent
(out/'raw-ccl-components.json').write_text(json.dumps({'source_files':{'frame_results_sha256':hashlib.sha256(frames_path.read_bytes()).hexdigest(),'result_sha256':hashlib.sha256(result_path.read_bytes()).hexdigest()},'note':'All components persisted in FrameResults for requested frames; runner had min_area=12, so sub-threshold connected components were not persisted. bbox endpoints are inclusive.','frames':summary},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
with (out/'raw-ccl-components.csv').open('w',newline='',encoding='utf-8-sig') as fp:
    w=csv.DictWriter(fp,fieldnames=['range','frame_id','roi_foreground_pixels','ccl_executed','component_index','centroid_x','centroid_y','area','x_min','y_min','x_max','y_max'])
    w.writeheader();w.writerows(records)
lines=['# Attempt #3 原始 CCL 逐帧摘录','',f"原始侧录 SHA-256：`{hashlib.sha256(frames_path.read_bytes()).hexdigest()}`；结果 JSON SHA-256：`{hashlib.sha256(result_path.read_bytes()).hexdigest()}`。坐标框为包含端点 `[x_min,y_min,x_max,y_max]`。面积单位为二值前景像素。",'', '注意：这些是 `frame-results.json` 中实际持久化的合格连通域。该执行器的 `min_area=12`，因此面积小于 12 的连通域在侧录生成前已过滤，不能从此文件恢复。']
for lo,hi in ranges:
    sel=[s for s in summary if lo<=s['frame_id']<=hi]
    allareas=[c['area'] for s in sel for c in s['components']]
    peak=max(sel,key=lambda x:x['roi_foreground_pixels'])
    maxc=max(sel,key=lambda x:x['component_count'])
    lines += ['',f'## 帧 {lo}–{hi}', '',f"帧数 {len(sel)}；前景像素峰值 {peak['roi_foreground_pixels']}（帧 {peak['frame_id']}）；连通域峰值 {maxc['component_count']}（帧 {maxc['frame_id']}）；已记录分量 {len(allareas)}；面积范围 {min(allareas) if allareas else '无'}–{max(allareas) if allareas else '无'} px.",'','| 帧 | ROI 前景 px | CCL 执行 | 分量数 | 全部分量：`(cx, cy, area, bbox inclusive)` |','|---:|---:|:---:|---:|---|']
    for s in sel:
        comps='; '.join(f"({c['centroid_x']:.3f}, {c['centroid_y']:.3f}, {c['area']}, [{c['bbox_inclusive']['x_min']}, {c['bbox_inclusive']['y_min']}, {c['bbox_inclusive']['x_max']}, {c['bbox_inclusive']['y_max']}])" for c in s['components']) or '—'
        lines.append(f"| {s['frame_id']} | {s['roi_foreground_pixels']} | {'是' if s['ccl_executed'] else '否'} | {s['component_count']} | {comps} |")
(out/'raw-ccl-frames-24-30-and-114-135.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
# concise machine-readable distribution for manager
summary_stats=[]
for lo,hi in ranges:
    sel=[s for s in summary if lo<=s['frame_id']<=hi]
    comps=[c for s in sel for c in s['components']]
    summary_stats.append({'frames':[lo,hi],'roi_foreground_pixels':{'min':min(s['roi_foreground_pixels'] for s in sel),'max':max(s['roi_foreground_pixels'] for s in sel),'max_frame':max(sel,key=lambda s:s['roi_foreground_pixels'])['frame_id']},'components_per_frame':{'min':min(s['component_count'] for s in sel),'max':max(s['component_count'] for s in sel),'max_frame':max(sel,key=lambda s:s['component_count'])['frame_id']},'component_observations':len(comps),'areas':{'min':min(c['area'] for c in comps),'median':statistics.median(c['area'] for c in comps),'max':max(c['area'] for c in comps)},'x_range':[min(c['centroid_x'] for c in comps),max(c['centroid_x'] for c in comps)],'y_range':[min(c['centroid_y'] for c in comps),max(c['centroid_y'] for c in comps)]})
(out/'raw-ccl-statistics.json').write_text(json.dumps({'source_result_summary':{k:result.get(k) for k in ('total_count','frames_processed','throughput_fps','elapsed_seconds')},'ranges':summary_stats},indent=2)+'\n',encoding='utf-8')
print(json.dumps({'out':str(out),'sidecar_sha256':hashlib.sha256(frames_path.read_bytes()).hexdigest(),'result_sha256':hashlib.sha256(result_path.read_bytes()).hexdigest(),'frames_exported':len(summary),'component_observations':sum(s['component_count'] for s in summary),'stats':summary_stats},ensure_ascii=False,indent=2))