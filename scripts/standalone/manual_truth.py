"""Offline, evidence-bound reconciliation of a locked human truth record."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys


TRUTH_SCHEMA = 'material-counter.manual-truth.v1'
REPORT_SCHEMA = 'material-counter.integrated-run.v1'
KEY_RE = re.compile(r'^(0|[1-9][0-9]*):(0|[1-9][0-9]*)$')
HEX_RE = re.compile(r'^[0-9a-fA-F]{64}$')


class InputError(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError('JSON 对象含重复字段: {}'.format(key))
        result[key] = value
    return result


def reject_nonfinite(value):
    raise InputError('JSON 不允许非有限数值: {}'.format(value))


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path, label):
    try:
        with path.open('r', encoding='utf-8') as stream:
            value = json.load(stream, object_pairs_hook=unique_object,
                              parse_constant=reject_nonfinite)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError('{} 无法读取为 JSON: {}'.format(label, exc)) from exc
    if type(value) is not dict:
        raise InputError('{} 顶层必须是 JSON 对象'.format(label))
    return value


def require_hash(value, label):
    if not isinstance(value, str) or not HEX_RE.fullmatch(value):
        raise InputError('{} 必须是 SHA-256 十六进制字符串'.format(label))


def parse_time(value, label):
    if not isinstance(value, str) or not value:
        raise InputError('{} 必须是带时区的 ISO8601 时间'.format(label))
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    except ValueError as exc:
        raise InputError('{} 不是有效 ISO8601 时间'.format(label)) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InputError('{} 必须包含时区'.format(label))
    return parsed


def event_key(frame_id, track_id):
    return '{}:{}'.format(frame_id, track_id)


def validate_report(report):
    if report.get('schema') != REPORT_SCHEMA or report.get('state') != 'STOPPED':
        raise InputError('报告必须是 integrated-run.v1 且 state=STOPPED')
    if report.get('source') != 'camera' or report.get('material_accuracy') != 'NOT_MEASURED':
        raise InputError('报告必须 source=camera 且 material_accuracy=NOT_MEASURED')
    for name in ('bit_sha256', 'hwh_sha256', 'manifest_sha256', 'native_sha256'):
        require_hash(report.get(name), '报告 ' + name)
    for name in ('boot_id', 'session_id'):
        if not isinstance(report.get(name), str) or not report[name]:
            raise InputError('报告缺少 ' + name)
    identity = report.get('source_identity')
    if not isinstance(identity, dict) or identity.get('complete') is not True:
        raise InputError('报告缺少完整 source_identity')
    require_hash(identity.get('digest'), '报告 source_identity.digest')
    trace = report.get('frame_trace')
    if type(trace) is not list:
        raise InputError('报告 frame_trace 必须是数组')
    trace_frames = report.get('trace_frames')
    if type(trace_frames) is not int or trace_frames != len(trace) or not 0 <= trace_frames <= 600:
        raise InputError('trace_frames 必须等于 frame_trace 长度且不超过 600')
    if report.get('trace_annotations_truncated') is not False:
        raise InputError('报告必须明确标注 trace_annotations_truncated=false')
    events_log = report.get('events')
    if type(events_log) is not list:
        raise InputError('报告 events 必须是数组以证明没有控制命令事件')
    if report.get('events_truncated') not in (None, 0, False):
        raise InputError('报告控制命令事件记录存在截断')
    if len(events_log) >= 2048:
        raise InputError('报告控制命令事件记录达到缓冲上限，无法证明未截断')
    if events_log:
        raise InputError('报告包含控制命令事件')
    if type(report.get('processed')) is not int or report['processed'] != trace_frames:
        raise InputError('processed 必须等于 frame_trace 长度以证明逐帧证据完整')

    frame_ids = set()
    systems = {}
    total = 0
    trace_digest = hashlib.sha256()
    for row in trace:
        if type(row) is not dict or type(row.get('frame')) is not int:
            raise InputError('frame_trace 含无效帧记录')
        frame = row['frame']
        if frame != len(frame_ids):
            raise InputError('frame_trace 必须按 0..processed-1 顺序覆盖每帧')
        frame_ids.add(frame)
        if row.get('annotations_truncated') is not False:
            raise InputError('frame_trace 每帧都必须明确未截断标注')
        evs = row.get('events')
        if type(evs) is not list:
            raise InputError('frame_trace 每帧 events 必须是数组')
        trace_digest.update(json.dumps(row, sort_keys=True, separators=(',', ':')).encode('utf-8') + b'\n')
        if type(row.get('count')) is not int or row['count'] < 0:
            raise InputError('frame_trace 每帧 count 必须是非负整数')
        for ev in evs:
            if type(ev) is not dict:
                raise InputError('CountEvent 必须是对象')
            fid, tid = ev.get('frame_id'), ev.get('track_id')
            if type(fid) is not int or fid != frame or type(tid) is not int or tid < 0:
                raise InputError('CountEvent frame_id/track_id 无效或帧号不一致')
            x, y, gap = ev.get('centroid_x'), ev.get('centroid_y'), ev.get('inferred_gap')
            if type(x) not in (int, float) or not math.isfinite(x) or type(y) not in (int, float) or not math.isfinite(y):
                raise InputError('CountEvent centroid_x/centroid_y 必须是有限数值')
            if type(gap) is not bool:
                raise InputError('CountEvent inferred_gap 必须是布尔值')
            key = event_key(fid, tid)
            if key in systems:
                raise InputError('系统事件键不唯一: {}'.format(key))
            systems[key] = {'key': key, 'frame_id': fid, 'track_id': tid,
                            'centroid': [x, y], 'inferred_gap': gap}
            total += 1
        if row['count'] != total:
            raise InputError('frame_trace 逐帧累计计数与事件数不一致')
    require_hash(report.get('trace_sha256'), '报告 trace_sha256')
    if report['trace_sha256'].lower() != trace_digest.hexdigest():
        raise InputError('报告 trace_sha256 与完整逐帧记录不符')
    if type(report.get('total_count')) is not int or report['total_count'] != total:
        raise InputError('total_count 必须等于 frame_trace 中 CountEvent 数量')
    if frame_ids != set(range(trace_frames)):
        raise InputError('frame_trace 必须覆盖完整连续的 0..processed-1 帧号')
    return systems, frame_ids


def validate_truth(truth, truth_path, report_sha, systems, frame_ids):
    allowed = {'schema', 'report_sha256', 'video_path', 'video_sha256', 'alignment',
               'human_sealed_at', 'matched_at', 'human_events'}
    if set(truth) != allowed:
        raise InputError('truth 顶层字段必须且只能包含约定字段')
    if truth.get('schema') != TRUTH_SCHEMA:
        raise InputError('truth schema 必须是 {}'.format(TRUTH_SCHEMA))
    require_hash(truth.get('report_sha256'), 'report_sha256')
    if truth['report_sha256'].lower() != report_sha:
        raise InputError('report_sha256 与报告文件不符')
    alignment = truth.get('alignment')
    if not isinstance(alignment, str) or not alignment.strip():
        raise InputError('alignment 必须是非空说明')
    sealed = parse_time(truth.get('human_sealed_at'), 'human_sealed_at')
    matched = parse_time(truth.get('matched_at'), 'matched_at')
    if matched <= sealed:
        raise InputError('matched_at 必须晚于 human_sealed_at')

    video_value = truth.get('video_path')
    if not isinstance(video_value, str) or not video_value.strip():
        raise InputError('video_path 必须是非空路径')
    video_path = Path(video_value)
    if not video_path.is_absolute():
        video_path = truth_path.parent / video_path
    try:
        if not video_path.is_file():
            raise InputError('录像文件不存在: {}'.format(video_path))
        video_sha = sha256_file(video_path)
    except OSError as exc:
        raise InputError('无法读取录像文件: {}'.format(exc)) from exc
    require_hash(truth.get('video_sha256'), 'video_sha256')
    if truth['video_sha256'].lower() != video_sha:
        raise InputError('video_sha256 与录像文件不符')

    humans = truth.get('human_events')
    if type(humans) is not list or not humans:
        raise InputError('human_events 必须是非空数组')
    ids, assignments = set(), {}
    claimed = set()
    for item in humans:
        allowed_event = {'id', 'material_id', 'material_label', 'direction', 'status', 'enter_frame',
                         'exit_frame', 'matched_system_event', 'related_system_events',
                         'match_basis', 'reason'}
        if type(item) is not dict or set(item) - allowed_event:
            raise InputError('人工事件结构无效或含未知字段')
        ident = item.get('id')
        if not isinstance(ident, str) or not ident or ident in ids:
            raise InputError('人工事件 id 必须非空且唯一')
        ids.add(ident)
        if not isinstance(item.get('material_id'), str) or not item['material_id']:
            raise InputError('material_id 必须是非空字符串')
        if not isinstance(item.get('material_label'), str) or not item['material_label']:
            raise InputError('material_label 必须是非空字符串')
        status = item.get('status')
        if status not in ('confirmed', 'pending', 'boundary'):
            raise InputError('人工事件 status 无效')
        if item.get('direction') not in ('+y', '-y', 'unknown'):
            raise InputError('人工事件 direction 无效')
        if status == 'confirmed':
            if item['direction'] != '+y':
                raise InputError('confirmed 事件必须是 +y 正向穿越')
            if not isinstance(item.get('match_basis'), str) or not item['match_basis'].strip():
                raise InputError('confirmed 事件必须写明配对或漏计依据')
            a, b = item.get('enter_frame'), item.get('exit_frame')
            if type(a) is not int or type(b) is not int or a > b or a not in frame_ids or b not in frame_ids:
                raise InputError('confirmed enter_frame/exit_frame 必须在报告帧范围内且 enter<=exit')
            if 'matched_system_event' not in item:
                raise InputError('confirmed 事件必须显式提供 matched_system_event（null 或键）')
            match = item['matched_system_event']
            if match is None:
                assignments[ident] = None
            elif isinstance(match, str) and KEY_RE.fullmatch(match):
                if match not in systems:
                    raise InputError('truth 配对引用未知系统事件: {}'.format(match))
                if match in claimed:
                    raise InputError('同一系统事件被重复配对: {}'.format(match))
                claimed.add(match)
                assignments[ident] = match
            else:
                raise InputError('matched_system_event 必须是 null 或 frame_id:track_id')
            if 'related_system_events' in item:
                raise InputError('confirmed 事件不能声明 related_system_events')
        else:
            if not isinstance(item.get('reason'), str) or not item['reason'].strip():
                raise InputError('pending/boundary 事件必须写明原因')
            if 'matched_system_event' in item and item['matched_system_event'] is not None:
                raise InputError('pending/boundary 事件不得与系统事件配成 TP')
            related = item.get('related_system_events', [])
            if type(related) is not list:
                raise InputError('related_system_events 必须是数组')
            if related and (not isinstance(item.get('match_basis'), str) or not item['match_basis'].strip()):
                raise InputError('关联待定/边界系统事件必须写明依据')
            for key in related:
                if not isinstance(key, str) or not KEY_RE.fullmatch(key) or key not in systems:
                    raise InputError('related_system_events 引用未知系统事件')
                if key in claimed:
                    raise InputError('系统事件存在重复关联: {}'.format(key))
                claimed.add(key)
            a, b = item.get('enter_frame'), item.get('exit_frame')
            if a is None and b is None:
                raise InputError('pending/boundary 事件至少需要一个帧锚点')
            for bound in (a, b):
                if bound is not None and (type(bound) is not int or bound not in frame_ids):
                    raise InputError('人工事件帧锚点必须处于报告帧范围内')
            if a is not None and b is not None and a > b:
                raise InputError('人工事件 enter_frame 必须不大于 exit_frame')
    return humans, assignments, claimed, video_sha


def reconcile(report_path, truth_path, output_path):
    report_path, truth_path, output_path = map(Path, (report_path, truth_path, output_path))
    report = read_json(report_path, 'integrated-run 报告')
    truth = read_json(truth_path, '人工真值')
    try:
        report_sha = sha256_file(report_path)
    except OSError as exc:
        raise InputError('无法计算报告 SHA-256: {}'.format(exc)) from exc
    systems, frame_ids = validate_report(report)
    humans, assignments, claimed, video_sha = validate_truth(
        truth, truth_path, report_sha, systems, frame_ids)

    paired = {key: human_id for human_id, key in assignments.items() if key is not None}
    tp = sum(1 for key in paired)
    fn = sum(1 for key in assignments.values() if key is None)
    pending_count = sum(item['status'] == 'pending' for item in humans)
    boundary_count = sum(item['status'] == 'boundary' for item in humans)
    fp_keys = sorted(set(systems) - claimed)
    detail = []
    for key, system in sorted(systems.items(), key=lambda pair: (pair[1]['frame_id'], pair[1]['track_id'])):
        if key in paired:
            classification, human_id = 'TP', paired[key]
        elif key in claimed:
            classification, human_id = 'unclassified_pending_or_boundary', next(
                item['id'] for item in humans if key in item.get('related_system_events', []))
        else:
            classification, human_id = 'FP', None
        detail.append(dict(**system, classification=classification, human_event_id=human_id))
    summary = {
        'schema': 'material-counter.manual-truth-summary.v1',
        'source': report['source'], 'report_sha256': report_sha, 'video_sha256': video_sha,
        'truth_sha256': sha256_file(truth_path),
        'scope': 'offline_manual_truth_reconciliation_only', 'G3_release': False,
        'human_events_total': len(humans), 'confirmed': sum(x['status'] == 'confirmed' for x in humans),
        'pending': pending_count, 'boundary': boundary_count,
        'system_events_total': len(systems), 'TP': tp, 'FN': fn, 'FP': len(fp_keys),
        'unmatched_system_event_keys': fp_keys,
        'unclassified_system_events': len(claimed - set(paired)),
        'inferred_gap': sum(x['inferred_gap'] for x in systems.values()),
        'pairing_details': detail,
        'human_match_details': [
            {'id': item['id'], 'status': item['status'], 'direction': item['direction'],
             'matched_system_event': assignments.get(item['id']),
             'related_system_events': item.get('related_system_events', []),
             'match_basis': item.get('match_basis'), 'reason': item.get('reason')}
            for item in humans
        ],
        'identity': {name: report[name] for name in (
            'boot_id', 'session_id', 'bit_sha256', 'hwh_sha256',
            'manifest_sha256', 'native_sha256')},
        'source_identity_digest': report['source_identity']['digest'],
    }
    payload = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    created = False
    try:
        with output_path.open('x', encoding='utf-8', newline='\n') as stream:
            created = True
            stream.write(payload)
    except FileExistsError as exc:
        raise InputError('输出文件已存在，拒绝覆盖: {}'.format(output_path)) from exc
    except OSError as exc:
        if created:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise InputError('无法独占创建输出文件: {}'.format(exc)) from exc
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, help='integrated-run.v1 JSON')
    parser.add_argument('--truth', required=True, help='manual-truth.v1 JSON')
    parser.add_argument('--output', required=True, help='新建 summary JSON 路径')
    args = parser.parse_args(argv)
    try:
        summary = reconcile(args.report, args.truth, args.output)
    except InputError as exc:
        print('manual_truth: 拒绝: {}'.format(exc), file=sys.stderr)
        return 2
    print(json.dumps({'output': args.output, 'system_events': summary['system_events_total'],
                      'TP': summary['TP'], 'FN': summary['FN'], 'FP': summary['FP']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
