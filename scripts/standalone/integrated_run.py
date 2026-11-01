"""G3 candidate: one RLE/HDMI owner, independent drawing, bounded shared frames."""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import signal
import stat
import sys
import threading
import time
import uuid

import cv2
import numpy as np

from .bundle import verify_bundle, verify_runtime, sha256
from .camera import LatestCamera
from .compositor import DashboardComposer
from .controls import ButtonDecoder, ControlState
from .count_controls import advance_count, make_runtime_counter
from .crossing_snapshots import CrossingSnapshots
from .async_evidence import EvidenceExporter
from .driver_owner import DriverOwner
from .display_health import DisplayHealthMonitor
from .diag_snapshot import DiagnosticSnapshotWriter,compact_diagnostic_snapshot
from .evidence import write_evidence,fail_hold,TraceBuffer
from .vdma_diag import MM2S_ERROR_MASK
from .g1_run import setup_hardware, snapshot, _memory, _parse_proc_stat, _cpu_usage_percent
from .hp2 import sync_hp1_readwrite32
from .g2_readback import read_pipeline, snapshot_counters
from .pressure import make_input, counter_delta, validate_transfer_totals
from .preview_inputs import PreviewInputs
from .video_pump import VideoPump, DISPLAY_FAIL_HOLD
from .telemetry_cache import TelemetryCache,parse_process_stat
from .probe import _collect_iio
from .shared_frames import SharedFrames, WRITING, READING
from scripts.rle.protocol import TX_BYTES, RX_CAPACITY
from scripts.rle.transport import RleDmaTransport
from scripts.wo0015.board_preflight import audit_process_ownership, inspect_pynq_global_state


def rasterize_into(packet, output):
    if not packet.valid:
        raise ValueError('display requires a valid RLE packet')
    if output.shape != (packet.height,packet.width) or output.dtype != np.uint8:
        raise ValueError('binary workspace must match packet geometry and uint8')
    output.fill(0)
    runs=packet.runs
    if len(runs)<64:
        for word in runs:
            value=int(word)
            output[value >> 20, (value >> 10) & 1023:(value & 1023)+1]=255
        return output
    runs=np.asarray(runs)
    if runs.dtype!=np.uint32:
        runs=runs.astype(np.uint32)
    # Bounded chunking: at most 512 runs x 16 px x 3 int32 temporaries (~96 KiB).
    # The one-shot np.repeat form grows with total foreground and is not used.
    for start in range(0,len(runs),512):
        chunk=runs[start:start+512]
        rows=(chunk >> 20).astype(np.int32)
        starts=((chunk >> 10) & 1023).astype(np.int32)
        ends=(chunk & 1023).astype(np.int32)
        lengths=(ends-starts+1).astype(np.int32)
        total=int(lengths.sum())
        if total==0:
            continue
        offsets=np.empty_like(lengths)
        offsets[0]=0
        np.cumsum(lengths[:-1],out=offsets[1:])
        row_rep=np.repeat(rows,lengths)
        columns=(np.arange(total,dtype=np.int32)
                 -np.repeat(offsets,lengths)
                 +np.repeat(starts,lengths))
        output[row_rep,columns]=255
    return output


def result_metadata(result, *, paused, binary_background, page,trajectory_epoch=0,
                    count_axis='x',line_position=320,direction=1):
    return dict(count=result.total_count, paused=paused,
        binary_background=binary_background,page=page,
        count_line=dict(axis=count_axis,position=line_position,direction=direction),
        annotations_truncated=len(result.detections)>128 or len(result.tracks)>128,
        boxes=[[d.x_min,d.y_min,d.x_max,d.y_max] for d in result.detections[:128]],
        tracks=[['{}:{}'.format(trajectory_epoch,t['track_id']),int(t['detection']['centroid_x']),
                 int(t['detection']['centroid_y'])] for t in result.tracks[:128]])


def dashboard_values(count, paused, page, telemetry):
    """Use measured telemetry only; unavailable or stale readings stay blank."""
    performance=telemetry.get('performance',{})
    resources=telemetry.get('resources',{})
    perf=performance.get('value',{}) if not performance.get('stale',True) else {}
    resource=resources.get('value',{}) if not resources.get('stale',True) else {}
    memory=resource.get('memory',{})
    memory_percent=None
    try:
        total=int(memory['MemTotal'].split()[0])
        available=int(memory['MemAvailable'].split()[0])
        if total>0 and 0<=available<=total:
            memory_percent=100*(total-available)/total
    except (KeyError,ValueError,TypeError,AttributeError):
        pass
    return dict(count=count,state='PAUSED' if paused else 'RUNNING',detail_page=bool(page),
        display_fps=perf.get('display_fps'),processing_ms=perf.get('processing_ms'),
        cpu_percent=resource.get('cpu',{}).get('system'),memory_percent=memory_percent)


def build_status_lines(count, paused, page, annotations_truncated, captures_enabled,
                       telemetry, performance, page_static):
    """Build the preview status lines exactly as the original per-frame code did."""
    lines=['本次会话累计 {}'.format(count),
        '暂停计数' if paused else '运行中',
        '重启清零 / '+('过线诊断留图' if captures_enabled else '诊断留图关闭')]
    if annotations_truncated:lines.append('标注仅显示前128项 / 计数未截断')
    if performance:
        lines.append('处理 {:.1f} FPS{}'.format(performance['value']['processing_fps'],
                                                ' / 过期' if performance['stale'] else ''))
    stale=[name for name,sample in telemetry.items() if sample['stale']]
    if stale:lines.append('过期: '+','.join(stale))
    if page:
        lines+=page_static
        resource=telemetry.get('resources',{}).get('value',{})
        lines.append('CMA '+str(resource.get('memory',{}).get('CmaFree','未知')))
        cpu=resource.get('cpu',{})
        lines.append('CPU0 {:.0f}% / CPU1 {:.0f}% / 系统 {:.0f}%'.format(
            cpu.get('cpu0',0),cpu.get('cpu1',0),cpu.get('system',0)))
        process_info=resource.get('processes',{})
        text=lambda key:('{:.0f}%'.format(process_info[key]['cpu_percent'])
                         if process_info.get(key,{}).get('cpu_percent') is not None else '未知')
        lines.append('进程 计数 {} / 绘图 {}'.format(text('count_capture_video'),text('drawing')))
        available=int(resource.get('memory',{}).get('MemAvailable','0 kB').split()[0])/1024
        lines.append('内存 {:.0f} MB / SD {:.1f} GB'.format(
            available,resource.get('sd_free_bytes',0)/(1024**3)))
        channels=telemetry.get('xadc',{}).get('value',{}).get('channels',[])
        temperatures=[c['value_si'] for c in channels
                      if c['channel'].startswith('in_temp') and isinstance(c['value_si'],(int,float))]
        if temperatures:lines.append('温度 {:.1f} °C'.format(temperatures[0]))
        rails={c['label']:c['value_si'] for c in channels
               if c['label'] in ('vccint','vccaux','vccbram') and isinstance(c['value_si'],(int,float))}
        rail_text=lambda key:('{:.3f}'.format(rails[key]) if key in rails else '未知')
        if rails:lines.append('INT {} / AUX {} / BRAM {} V'.format(
            rail_text('vccint'),rail_text('vccaux'),rail_text('vccbram')))
        if performance:lines.append('RLE {} runs / DMA {} B'.format(
            performance['value']['runs'],performance['value']['rx_bytes']))
    return lines


def timing_report_flag(enabled):
    """Report-level flag exists only when --transfer-timing is enabled."""
    return {'transfer_timing_enabled': True} if enabled else {}


def timing_sample_fields(enabled, last=None):
    """Per-sample timing field exists only when --transfer-timing is enabled."""
    return {'transfer_timing': last} if enabled else {}


def summarize_transfer_timing(parts):
    """Percentile summary for the opt-in transfer timing marks."""
    def pcts(values):
        return {f'p{p}':float(np.percentile(values,p)) if values else None for p in (50,95,99)}
    stats={key:pcts([float(value.get(key,0.0)) for value in parts])
           for key in ('poll_count','poll_max_gap_ms','poll_total_ms')}
    order=['enter','encoded','configured','started','waited','synced','decoded']
    for first,second in zip(order,order[1:]):
        durations=[(value[second]-value[first])*1000.0 for value in parts
                   if first in value and second in value]
        if durations:
            stats[f'{first}_to_{second}']=pcts(durations)
    # Additive sub-stage names; emitted only for samples that carry the finer
    # marks, so payloads from the older instrumentation keep their exact schema.
    split=[value for value in parts if 'preflighted' in value and 'dma_done' in value]
    if split:
        for name,first,second in (
                ('preflight_ms','enter','preflighted'),
                ('encode_ms','preflighted','packed'),
                ('sync_to_device_ms','packed','encoded'),
                ('configure_ms','encoded','configured'),
                ('dma_start_ms','configured','started'),
                ('dma_wait_ms','started','waited'),
                ('dma_reap_ms','waited','dma_done'),
                ('sync_from_device_ms','dma_done','synced'),
                ('decode_ms','synced','decoded')):
            durations=[(value[second]-value[first])*1000.0 for value in split
                       if first in value and second in value]
            if durations:
                stats[name]=pcts(durations)
    return stats


def resolve_display_held(pre_stop_state, join_ok, post_join_state):
    """Classify why display resources must be retained during cleanup.

    The pre-existing failure is checked first: a pump that was already in
    DISPLAY_FAIL_HOLD before the stop is the root cause, not the join timeout
    that merely observed it.
    """
    if pre_stop_state == DISPLAY_FAIL_HOLD:
        return True, 'pre_existing_display_fail_hold'
    if not join_ok:
        return True, 'video_pump_join_timeout'
    if post_join_state == DISPLAY_FAIL_HOLD:
        return True, 'video_pump_display_fail_hold'
    return False, None


def start_drawing_child(
    ctx, inputs, outputs, errors, counters, font, latin_font, failure_mode=None
):
    """Start the drawing worker and retain a child that acquired a PID on error."""
    if failure_mode not in (None, "create", "start", "after-start"):
        raise ValueError("unknown drawing process failure mode")
    stop = None
    child = None
    try:
        stop = ctx.Event()
        if failure_mode == "create":
            raise OSError("injected drawing Process creation failure")
        child = ctx.Process(
            target=drawing_process,
            args=(inputs, outputs, stop, errors, counters, font, latin_font),
        )
        if failure_mode == "start":
            raise OSError("injected drawing Process.start failure")
        child.start()
        if failure_mode == "after-start":
            raise OSError("injected error after drawing Process.start returned")
        return child, stop, None
    except Exception as error:
        # A failed Process.start() may still leave a live child if the platform
        # raised after launching it. Keep that handle so the owner can join it.
        if child is not None and child.pid is not None:
            return child, stop, error
        if child is not None:
            try:
                child.close()
            except (ValueError, AssertionError):
                pass
        return None, None, error


def source_identity(project_root):
    root=Path(project_root).resolve()
    scoped_roots=tuple((root/relative).resolve() for relative in
        ('scripts/standalone','scripts/rle','scripts/wo0015','scripts/wo0016'))
    sources={}
    errors=[]

    def is_test_source(path):
        parts=tuple(part.lower() for part in path.parts)
        name=path.name.lower()
        return 'tests' in parts or name.startswith('test_') or name.endswith('_test.py')

    candidates={Path(__file__).resolve()}
    for module in list(sys.modules.values()):
        module_path=getattr(module,'__file__',None)
        if not module_path:
            continue
        try:
            path=Path(module_path).resolve()
            relative=path.relative_to(root)
        except (OSError,ValueError):
            continue
        if path.suffix.lower()!='.py' or is_test_source(relative):
            continue
        if any(path==directory or directory in path.parents for directory in scoped_roots):
            candidates.add(path)

    for path in sorted(candidates,key=lambda item:item.as_posix()):
        try:
            relative=path.relative_to(root).as_posix()
            sources[relative]=sha256(path)
        except Exception as error:
            try:
                relative=path.relative_to(root).as_posix()
            except ValueError:
                relative=path.as_posix()
            errors.append(dict(path=relative,error=repr(error)))
    canonical=json.dumps(sources,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return dict(schema='material-counter.project-source-identity.v1',sha256_by_path=sources,
        digest=hashlib.sha256(canonical).hexdigest(),file_count=len(sources),
        scope='Project-local Python sources under scripts/standalone, scripts/rle, scripts/wo0015, and scripts/wo0016 that are loaded in sys.modules, plus this integrated_run.py; tests and paths outside the project root are excluded. This does not identify dependencies, ABI, or bitstreams.',
        complete=not errors,errors=errors)


def drawing_process(inputs, outputs, stop, errors, counters, font, latin_font):
    """Own ordinary RAM only; a drawing failure cannot free CMA or reload PL."""
    cv2.setNumThreads(2)
    composer=DashboardComposer(font,latin_font_path=latin_font,gray_interpolation=cv2.INTER_AREA)
    try:
        while not stop.is_set():
            lease=inputs.acquire_latest()
            if lease is None:
                stop.wait(.001);continue
            slot=None
            try:
                claimed=outputs.claim_write()
                if claimed is None:
                    counters[1]+=1;continue
                slot,pixels=claimed
                meta=lease.metadata
                lines=meta.get('status_lines',())
                count_line=meta.get('count_line',dict(axis='x',position=320,direction=1))
                composer.render(lease.gray,lease.binary,lease.session_uuid,lease.frame_id,
                    boxes=meta['boxes'],tracks=meta['tracks'],status_lines=lines,
                    binary_background=meta['binary_background'],output=pixels,
                    line_x=count_line['position'] if count_line['axis']=='x' else 320,
                    line_y=count_line['position'] if count_line['axis']=='y' else None,
                    dashboard=meta.get('dashboard'),slot_key=slot)
                timings=composer.last_timings_ms
                outputs.publish(slot,lease.session_uuid,lease.frame_id,
                    scale_ns=int(timings['scale_ms']*1e6),draw_ns=int(timings['draw_ms']*1e6))
                slot=None;counters[0]+=1
            except BaseException:
                if slot is not None:composer.invalidate_slot(slot)
                raise
            finally:
                if slot is not None:outputs.abort_write(slot)
                inputs.release(lease)
    except BaseException as error:
        try:errors.put_nowait(repr(error))
        except queue.Full:pass
        raise
    finally:
        inputs.close();outputs.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--boot-id',required=True)
    parser.add_argument('--native-lib',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--startup-record',type=Path,help='one initialization record; no periodic counter persistence')
    parser.add_argument('--crossing-snapshots',choices=('on','off'),default='on',
        help='save bounded asynchronous crossing diagnostics beside the session report')
    parser.add_argument('--snapshot-max-events',type=int,default=1000)
    parser.add_argument('--snapshot-max-mib',type=int,default=512)
    parser.add_argument('--snapshot-storage',choices=('compact','full'),default='compact',
        help='compact writes shared gray/RLE only; render diagnostic overlays offline')
    parser.add_argument('--counting-profile',choices=('legacy','field-v1'),default='field-v1',
        help='field-v1 enables time-based ordered association and calibrated local splitting')
    parser.add_argument('--source',choices=('replay','camera'),default='replay')
    parser.add_argument('--camera',default='/dev/v4l/by-id/usb-HSK_USB_Camera_20210616103-video-index0')
    parser.add_argument('--display',choices=('on','off'),default='on')
    parser.add_argument('--duration',type=float,default=90)
    parser.add_argument('--frames',type=int,default=1200)
    parser.add_argument('--continuous',action='store_true',help='camera service; bounded RAM diagnostics until explicit stop')
    parser.add_argument('--commands',type=Path,help='JSON map frame number -> ordered control commands')
    parser.add_argument('--inject-drawing-exit',type=int,help='cooperative drawing exit after this processed frame')
    parser.add_argument('--inject-drawing-start-failure',choices=('create','start','after-start'),
        help='inject one drawing process creation/start error on its first launch attempt')
    parser.add_argument('--freeze-telemetry-at-frame',type=int,help='test staleness without stopping safety checks')
    parser.add_argument('--transfer-timing',action='store_true',
        help='record per-stage transfer marks and poll gaps (default off; no behavior change)')
    parser.add_argument('--overlay-control',action='store_true',
        help='run the read-only B1a overlay preflight (default off)')
    parser.add_argument('--overlay-first-write',action='store_true',
        help='after preflight, perform the single CTRL=0 write (default off)')
    parser.add_argument('--display-health-stop',action='store_true',
        help='request a controlled stop on a detected display failure (default: record only)')
    parser.add_argument('--diag-snapshot',action='store_true',
        help='write a bounded periodic diagnostic snapshot (default off)')
    parser.add_argument('--diag-snapshot-interval',type=float,default=30.0,
        help='seconds between diagnostic snapshots')
    parser.add_argument('--vdma-diag-mmio',action='store_true',
        help='capture a bounded read-only MM2S VDMA snapshot on a writeframe failure (default off)')
    parser.add_argument('--font',default='/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf')
    parser.add_argument('--latin-font',default='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    parser.add_argument('--inherited-lock-fds',nargs=2,type=int,metavar=('DESKTOP_FD','STANDALONE_FD'),
        help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.snapshot_max_events < 1 or args.snapshot_max_mib < 1:
        parser.error('snapshot storage limits must be positive')
    if args.diag_snapshot_interval <= 0:
        parser.error('diag snapshot interval must be positive')
    if not args.execute or not 1<=args.duration<=7200 or not 1<=args.frames<=200000 or (args.continuous and args.source!='camera'):
        parser.error('requires execute, duration 1..7200 and frames 1..200000')
    if args.inject_drawing_start_failure and args.display!='on':
        parser.error('drawing process failure injection requires display on')
    if args.output.exists() or not args.native_lib.is_file():parser.error('new output and prebuilt library required')
    if args.startup_record and args.startup_record.exists():parser.error('startup record already exists')
    files=verify_bundle(args.manifest,args.manifest_sha256)
    manifest=json.loads(args.manifest.read_text())
    if manifest.get('rle_monitor')!={'abi':1,'clock_hz':50_000_000}:raise ValueError('requires reviewed monitor ABI 1')
    verify_runtime(manifest['runtime'],args.font,args.latin_font)
    commands=json.loads(args.commands.read_text()) if args.commands else {}
    if not isinstance(commands,dict):raise ValueError('commands must be a frame map')
    # Validate all injected commands before hardware access.
    for key,value in commands.items():
        if not key.isdecimal() or not isinstance(value,list):raise ValueError('invalid command frame')
        ControlState().apply(value)
    if os.geteuid()!=0 or os.environ.get('XILINX_XRT')!='/usr':raise RuntimeError('root/XRT required')
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if boot!=args.boot_id:raise RuntimeError('boot changed')
    import fcntl
    locks=[]
    if args.inherited_lock_fds is None:
        for name in ('/tmp/material-counter-desktop.lock','/run/material-standalone.lock'):
            stream=open(name,'a');fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(stream)
    else:
        inherited_fds=args.inherited_lock_fds
        try:
            if any(fd<=0 for fd in inherited_fds) or inherited_fds[0]==inherited_fds[1]:
                raise ValueError('inherited lock fds must be distinct positive integers')
            expected_paths=('/tmp/material-counter-desktop.lock','/run/material-standalone.lock')
            for fd,name in zip(inherited_fds,expected_paths):
                descriptor=os.fstat(fd)
                if not stat.S_ISREG(descriptor.st_mode):
                    raise ValueError('inherited lock fd is not a regular file')
                actual=os.path.realpath('/proc/self/fd/{}'.format(fd))
                if actual!=os.path.realpath(name):
                    raise ValueError('inherited lock fd does not refer to the expected lock file')
                current=os.stat(name)
                if (descriptor.st_dev,descriptor.st_ino)!=(current.st_dev,current.st_ino):
                    raise ValueError('inherited lock file was replaced before runner admission')
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                stream=os.fdopen(fd,'a')
                locks.append(stream)
                os.set_inheritable(fd,False)
        except BaseException:
            owned={stream.fileno() for stream in locks if not stream.closed}
            for stream in locks:
                try:stream.close()
                except OSError:pass
            for fd in inherited_fds:
                if fd not in owned:
                    try:os.close(fd)
                    except OSError:pass
            raise
    import pynq
    from pynq import MMIO,allocate
    files['digest']=sha256(files['bit'])
    identity=inspect_pynq_global_state(pynq.__file__,require_empty_shutdown=True,
        expected_bit_path=files['bit'],expected_bit_sha256=files['digest'],
        require_loaded_bit=True,allow_equivalent_bit_path=True)
    report=dict(schema='material-counter.integrated-run.v1',boot_id=boot,loaded=identity,
        bit_sha256=files['digest'],hwh_sha256=sha256(files['hwh']),
        manifest_sha256=args.manifest_sha256,native_sha256=sha256(args.native_lib),
        commands_sha256=sha256(args.commands) if args.commands else None,
        source=args.source,display=args.display,main_pid=os.getpid(),continuous=args.continuous,
        diagnostic_limits={'samples':1800,'events':2048,'recoveries':2048,'frame_trace':600,'annotations_per_frame':128,
            'button_raw_transitions':2048},
        session_id=str(uuid.uuid4()),ownership=audit_process_ownership(),state='STARTING',
        material_accuracy='NOT_MEASURED',samples=deque(maxlen=1800),events=deque(maxlen=2048),
        recoveries=deque(maxlen=2048),button_raw_sample_count=0,
        button_raw_transitions=deque(maxlen=2048),button_raw_transition_total=0,button_raw_transition_truncated=0,
        before_memory=_memory(),scope='G3 candidate; G4 physical disconnection/cold-start not asserted')
    if args.transfer_timing:report.update(timing_report_flag(True))
    captures=exporter=None
    button_raw_lock=threading.Lock()
    def report_snapshot():
        with button_raw_lock:
            result=dict(report)
            transitions=list(report['button_raw_transitions'])
            transition_total=report['button_raw_transition_total']
            result['button_raw_sample_count']=report['button_raw_sample_count']
            result['button_raw_transitions']=transitions
            result['button_raw_transition_total']=transition_total
            result['button_raw_transition_truncated']=transition_total-len(transitions)
        result['crossing_snapshots']=captures.status() if captures is not None else {'enabled':False}
        result['report_exports']=exporter.status() if exporter is not None else {'enabled':False}
        result['vdma_diag_mmio']=bool(args.vdma_diag_mmio)
        if video_pump is not None:
            result['video_pump_diagnostics']=video_pump.diagnostics()
            result['video_owner_diagnostics']=video_owner.diagnostics()
        return result
    report['cpu_scope']='system/core normalized 0..100%; process 100% equals one core'
    report['camera_configuration']=dict(by_id=args.camera,requested_format='MJPG',
        validated_decoded_shape=[400,640],decode='IMREAD_GRAYSCALE',negotiated_fps='NOT_QUERIED')
    controls=ControlState(binary_background=False);decoder=ButtonDecoder();events=queue.Queue(maxsize=64)
    previous_raw_button_bits=None
    hw=transport=camera=process=inputs=outputs=display_frame=video_pump=None
    display_held=False
    stop_drawing=None;running=True;export_requested=False;exports=0
    def stop(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    def request_export(*_):
        nonlocal export_requested
        export_requested=True
    if hasattr(signal,'SIGUSR1'):signal.signal(signal.SIGUSR1,request_export)
    def poll():
        nonlocal previous_raw_button_bits
        if hw is not None:
            bits=int(hw.buttons.read())&15
            sampled_at=time.monotonic()
            with button_raw_lock:
                report['button_raw_sample_count']+=1
                if previous_raw_button_bits is None or bits!=previous_raw_button_bits:
                    report['button_raw_transition_total']+=1
                    report['button_raw_transitions'].append(dict(monotonic=sampled_at,bits=bits))
                    report['button_raw_transition_truncated']=report['button_raw_transition_total']-len(report['button_raw_transitions'])
                    previous_raw_button_bits=bits
            for command in decoder.update(bits,sampled_at):
                events.put_nowait(command)
    # One manager controls the overlay lifetime. Separate driver domains prevent
    # a blocked display call from taking the RLE counting domain with it.
    video_owner=DriverOwner()
    owner=DriverOwner(poll=poll)
    ctx=mp.get_context('spawn')
    drawing_errors=ctx.Queue(maxsize=1);drawing_counts=ctx.RawArray('Q',2)
    session=uuid.UUID(report['session_id'])
    previous_cpu=_parse_proc_stat(Path('/proc/stat').read_text())
    stages={key:deque(maxlen=20000) for key in ('transfer_ms','count_ms','snapshot_ms','publish_ms','scale_ms','draw_ms','copy_submit_ms','transfer_parts')}
    cleanup_stage='run'
    diag_writer=None
    display_health=None
    try:
        dma=owner.retain(MMIO(0x40400000,0x1000))
        for offset in (4,0x34):
            state=owner.call(dma.read,offset)
            if state&0x770 or not state&3:raise RuntimeError('RLE DMA not safely stopped')
        report['hp1_readwrite32']=owner.call(sync_hp1_readwrite32, files['hwh'], MMIO)
        hw=video_owner.call(setup_hardware,files,video_owner,timeout=60)
        report['clocks_mhz']=hw.clocks
        if args.overlay_control or args.overlay_first_write:
            from .overlay_control import OverlayControl
            overlay_control=OverlayControl(hw.overlay,video_owner)
            report['overlay_control']=(overlay_control.first_write()
                if args.overlay_first_write else overlay_control.preflight())
        control=owner.retain(MMIO(0x43c20000,0x1000))
        pipeline=owner.call(read_pipeline,control)
        tx=owner.retain(allocate(shape=(TX_BYTES,),dtype='u1'))
        rx=owner.retain(allocate(shape=(RX_CAPACITY,),dtype='u1'))
        transport=RleDmaTransport(hw.overlay.axi_dma_0.sendchannel,hw.overlay.axi_dma_0.recvchannel,dma,control,tx,rx)
        if pipeline['state']['done_seen']:transport._completed_frames=1
        counter=make_runtime_counter(args.native_lib,profile=args.counting_profile)
        exporter=EvidenceExporter()
        report['counting_profile']=args.counting_profile
        report['counting_configuration']=dict(axis=counter.count_axis,line_x=counter.line_x,
            line_y=counter.line_y,direction=counter.direction,motion='left_to_right')
        report['source_identity']=source_identity(Path(__file__).resolve().parents[2])
        if args.crossing_snapshots=='on':
            captures=CrossingSnapshots(args.output.with_suffix('.crossings'),
                identity={name:report[name] for name in ('boot_id','session_id','bit_sha256',
                    'hwh_sha256','manifest_sha256','native_sha256','source_identity','camera_configuration')},
                max_events=args.snapshot_max_events,max_bytes=args.snapshot_max_mib*1024**2,
                storage_mode=args.snapshot_storage)
        trace=TraceBuffer();report['frame_trace']=trace.frames
        if args.startup_record:
            try:
                write_evidence(args.startup_record,dict(schema='material-counter.session-start.v1',
                    boot_id=boot,session_id=report['session_id'],main_pid=os.getpid(),
                    manifest_sha256=args.manifest_sha256,bit_sha256=files['digest'],
                    total_count=counter.total_count,state='COUNT_INITIALIZED_WAITING_FIRST_FRAME',
                    monotonic=time.monotonic()))
            except OSError as error:
                report['recoveries'].append(dict(kind='startup_record',error=repr(error),monotonic=time.monotonic()))
        binary=np.empty((400,640),np.uint8)
        next_drawing_retry=0
        drawing_start_injection_done=False
        if args.display=='on':
            inputs=PreviewInputs.create(ctx);outputs=SharedFrames.create(ctx)
            def start_drawing(frame):
                nonlocal process,stop_drawing,next_drawing_retry,drawing_start_injection_done
                failure_mode=None
                if args.inject_drawing_start_failure and not drawing_start_injection_done:
                    failure_mode=args.inject_drawing_start_failure
                    drawing_start_injection_done=True
                process,stop_drawing,error=start_drawing_child(ctx,inputs,outputs,
                    drawing_errors,drawing_counts,args.font,args.latin_font,
                    failure_mode=failure_mode)
                report['drawing_pid']=process.pid if process is not None else None
                if error is not None:
                    failed_at=time.monotonic()
                    report['recoveries'].append(dict(kind='drawing_start_failed',frame=frame,
                        error=repr(error),injected=failure_mode is not None,
                        failure_mode=failure_mode,monotonic=failed_at))
                    next_drawing_retry=failed_at+5
            video_pump=VideoPump(outputs,hw,video_owner,session,
                vdma_read32=hw.vdma.read if args.vdma_diag_mmio else None)
        else:video_owner.call(hw.hdmi.close,timeout=3)
        if args.source=='camera':camera=LatestCamera(args.camera)
        report['counters_before']=owner.call(snapshot_counters,control)
        started=last_sample=time.monotonic();index=submitted=published=dropped=0
        camera_index=-1;next_camera_retry=0;injection_done=False;next_drawing_retry=0
        cache=TelemetryCache();last_resource=last_xadc=last_performance=0
        page_static=['640x400 / RLE 50MHz','显示 1280x720 60Hz',
            'LUT {} / FF {}'.format(manifest.get('resources',{}).get('lut','未知'),manifest.get('resources',{}).get('ff','未知')),
            'BRAM {} / DSP {}'.format(manifest.get('resources',{}).get('bram_tiles','未知'),manifest.get('resources',{}).get('dsp','未知'))]
        status_cache={'key':None,'lines':()}
        display_health=DisplayHealthMonitor() if args.display=='on' else None
        diag_writer=(DiagnosticSnapshotWriter(args.output.with_suffix('.diag.json'))
                     if args.diag_snapshot else None)
        last_diag=0.0
        report['display_health_events']=deque(maxlen=32)
        last_performance_index=last_performance_submitted=0;last_gray=last_metadata=None;last_valid_id=0;rx_total_bytes=0
        trajectory_epoch=0;camera_in_error=False
        previous_process={}
        last_rle_stats={'runs':0,'rx_bytes':0}
        tick_hz=os.sysconf('SC_CLK_TCK');page_size=os.sysconf('SC_PAGE_SIZE')
        while running and (args.continuous or (time.monotonic()-started<args.duration and index<args.frames)):
            now=time.monotonic()
            if export_requested:
                report.update(state='RUNNING',processed=index,submitted=video_pump.submitted if video_pump else 0,
                    duration_s=now-started,total_count=counter.total_count,invalid_frames=counter.invalid_frames,
                    export_monotonic=now,trace_sha256=trace.digest(),trace_frames=trace.frames_seen,
                    trace_annotations_truncated=trace.annotations_truncated)
                exported=args.output.with_name(args.output.stem+'.snapshot-'+str(exports)+'.json')
                # Freeze while the raw-button producer is excluded; serialization
                # and SD writes happen in a bounded background thread.
                with button_raw_lock:
                    immutable=dict(report)
                    immutable['crossing_snapshots']=captures.status() if captures else {'enabled':False}
                    immutable['report_exports']=exporter.status()
                    accepted=exporter.submit(exported,immutable)
                if not accepted:
                    report['recoveries'].append(dict(kind='evidence_export_dropped',monotonic=now))
                exports+=1;export_requested=False
            frozen=args.freeze_telemetry_at_frame is not None and index>=args.freeze_telemetry_at_frame
            if not frozen:
                if now-last_resource>=3:
                    cpu=_parse_proc_stat(Path('/proc/stat').read_text())
                    disk=os.statvfs('/home/xilinx')
                    usage=_cpu_usage_percent(previous_cpu,cpu)
                    usage['system']=sum(usage.values())/len(usage) if usage else 0
                    processes={}
                    for name,pid in (('count_capture_video',os.getpid()),('drawing',process.pid if process else None)):
                        if pid is None:continue
                        try:measured=parse_process_stat(Path('/proc/{}/stat'.format(pid)).read_text(),page_size=page_size)
                        except (OSError,ValueError):
                            processes[name]={'pid':pid,'state':'UNAVAILABLE'};continue
                        current_time=time.monotonic();old=previous_process.get(name)
                        percent=None
                        if old and old[0]==pid and current_time>old[2] and measured['cpu_ticks']>=old[1]:
                            percent=100*(measured['cpu_ticks']-old[1])/(tick_hz*(current_time-old[2]))
                        previous_process[name]=(pid,measured['cpu_ticks'],current_time)
                        processes[name]=dict(pid=pid,cpu_percent=percent,rss_kib=measured['rss_kib'])
                    cache.record('resources',dict(memory=_memory(),cpu=usage,processes=processes,
                        sd_free_bytes=disk.f_bavail*disk.f_frsize),3,time.monotonic())
                    previous_cpu=cpu;last_resource=now
                if now-last_xadc>=5:
                    sensor_errors=[];iio=_collect_iio(Path('/'),sensor_errors)
                    cache.record('xadc',dict(channels=iio['channels'],errors=sensor_errors),5,time.monotonic())
                    last_xadc=now
                if now-last_performance>=2:
                    fps=(index-last_performance_index)/(now-last_performance) if last_performance else 0
                    current_submitted=video_pump.submitted if video_pump else 0
                    display_fps=((current_submitted-last_performance_submitted)/(now-last_performance)
                                 if last_performance and video_pump else None)
                    processing_ms=(float(np.mean(list(stages['transfer_ms'])[-30:])+
                                         np.mean(list(stages['count_ms'])[-30:])+
                                         np.mean(list(stages['snapshot_ms'])[-30:]))
                                   if stages['transfer_ms'] and stages['count_ms'] else None)
                    cache.record('performance',dict(processing_fps=fps,display_fps=display_fps,
                        processing_ms=processing_ms,processed=index,**last_rle_stats),2,time.monotonic())
                    last_performance=now;last_performance_index=index;last_performance_submitted=current_submitted
            if args.display=='on' and (process is None or not process.is_alive()) and now>=next_drawing_retry:
                if process is not None:
                    process.join(timeout=0)
                    # Only a confirmed dead child held these leases. CMA remains
                    # with the owner and HDMI keeps scanning throughout recovery.
                    for slot,state in enumerate(inputs.statuses()):
                        if state==READING:inputs._frames.reclaim_reader(slot,dead_consumer=True)
                    for slot,state in enumerate(outputs.statuses()):
                        if state==WRITING:outputs.reclaim_writer(slot,dead_producer=True)
                    report['recoveries'].append(dict(kind='drawing',frame=index,exitcode=process.exitcode,monotonic=now))
                    process.close()
                    process=None
                    try:drawing_errors.get_nowait()
                    except queue.Empty:pass
                start_drawing(index)
                next_drawing_retry=time.monotonic()+5
            if args.source=='camera':
                if camera is None:
                    if now<next_camera_retry:time.sleep(.01);continue
                    camera=LatestCamera(args.camera);camera_index=-1
                try:
                    # V4L2 camera startup and its two discarded warm-up frames
                    # can exceed the steady-state fresh-frame timeout.
                    camera_timeout=15.0 if camera_index<0 else .25
                    camera_index,gray,captured=camera.read(after=camera_index,timeout=camera_timeout)
                except (RuntimeError,TimeoutError) as error:
                    camera.close();camera=None;next_camera_retry=time.monotonic()+5
                    report['recoveries'].append(dict(kind='camera',frame=index,error=repr(error),monotonic=now))
                    if inputs is not None and last_gray is not None:
                        fault=dict(last_metadata);fault['status_lines']=['相机断连 / 停止新增计数','每5秒重试 / 恢复新轨迹','累计 {}'.format(counter.total_count)]
                        fault['dashboard']=dict(fault.get('dashboard',{}),state='CAMERA LOST')
                        inputs.publish(last_gray,binary,session,last_valid_id,fault)
                    camera_in_error=True
                    continue
                if camera_in_error:
                    old_total=counter.total_count;counter=make_runtime_counter(args.native_lib,profile=args.counting_profile);counter.total_count=old_total
                    trajectory_epoch+=1;camera_in_error=False
                    report['recoveries'].append(dict(kind='camera_resumed',frame=index,
                        trajectory_epoch=trajectory_epoch,monotonic=time.monotonic()))
                capture_ns=int(captured*1e9)
            else:
                gray=make_input('moving',index);capture_ns=(index+1)*33_333_333
            frame_start=time.monotonic()
            boundary=list(commands.get(str(index),()))
            while True:
                try:boundary.append(events.get_nowait())
                except queue.Empty:break
            if args.transfer_timing:
                transfer_timing={}
                packet=owner.call(transport.transfer_timed,gray,index,transfer_timing,timeout=6)
                report['transfer_timing_last']=transfer_timing
                stages['transfer_parts'].append(transfer_timing)
            else:
                packet=owner.call(transport.transfer,gray,index,timeout=6)
            rx_total_bytes+=packet.actual_bytes
            last_rle_stats={'runs':packet.run_count,'rx_bytes':packet.actual_bytes}
            transfer_end=time.monotonic()
            result=advance_count(counter,controls,packet,boundary,capture_monotonic_ns=capture_ns)
            count_end=time.monotonic()
            if captures is not None:
                captures.observe(gray,packet,result,counter,trajectory_epoch=trajectory_epoch,
                    capture_monotonic_ns=capture_ns,camera_sequence=camera_index if camera else index,
                    paused=controls.paused,commands=boundary,
                    timings={'transfer_ms':(transfer_end-frame_start)*1000,'count_ms':(count_end-transfer_end)*1000})
            snapshot_end=time.monotonic()
            stages['transfer_ms'].append((transfer_end-frame_start)*1000)
            stages['count_ms'].append((count_end-transfer_end)*1000)
            stages['snapshot_ms'].append((snapshot_end-count_end)*1000)
            if boundary:report['events'].append(dict(frame=index,commands=boundary,count=counter.total_count))
            if result is not None:
                trace.add(index,result,trajectory_epoch)
                if args.display=='on':
                    rasterize_into(packet,binary)
                    metadata=result_metadata(result,paused=controls.paused,binary_background=controls.binary_background,
                        page=controls.page,trajectory_epoch=trajectory_epoch,count_axis=counter.count_axis,
                        line_position=counter.line_x if counter.count_axis=='x' else counter.line_y,
                        direction=counter.direction)
                    telemetry=cache.snapshot(time.monotonic())
                    metadata['dashboard']=dashboard_values(counter.total_count,controls.paused,controls.page,telemetry)
                    performance=telemetry.get('performance')
                    status_key=(counter.total_count,controls.paused,controls.page,
                        metadata['annotations_truncated'],
                        tuple((name,sample['sampled_monotonic'],sample['stale'])
                              for name,sample in sorted(telemetry.items())))
                    if status_key!=status_cache['key']:
                        status_cache['key']=status_key
                        status_cache['lines']=tuple(build_status_lines(
                            counter.total_count,controls.paused,controls.page,
                            metadata['annotations_truncated'],captures is not None,
                            telemetry,performance,page_static))
                    metadata['status_lines']=list(status_cache['lines'])
                    last_gray=gray;last_metadata=metadata;last_valid_id=index
                    if process is not None and process.is_alive():
                        if inputs.publish(gray,binary,session,index,metadata,
                            include_binary=bool(metadata.get('binary_background'))):published+=1
                        else:dropped+=1
                    else:dropped+=1
                    stages['publish_ms'].append((time.monotonic()-snapshot_end)*1000)
            elif inputs is not None and last_gray is not None:
                fault=dict(last_metadata);fault['status_lines']=['RLE帧无效 / 本帧拒计','累计 {}'.format(counter.total_count)]
                fault['dashboard']=dict(fault.get('dashboard',{}),state='INVALID FRAME')
                if process is not None and process.is_alive():
                    inputs.publish(last_gray,binary,session,last_valid_id,fault)
            index+=1
            if args.display=='on':
                submitted=video_pump.submitted
                if process is not None and args.inject_drawing_exit is not None and index>=args.inject_drawing_exit and not injection_done:
                    stop_drawing.set();injection_done=True
            if time.monotonic()-last_sample>=2:
                sample_time=time.monotonic();cpu=_parse_proc_stat(Path('/proc/stat').read_text())
                monitor=owner.call(snapshot,hw.monitor)
                vdma_status=owner.call(hw.vdma.read,4)
                if vdma_status&MM2S_ERROR_MASK:
                    display_held=True
                    if video_pump is not None:video_pump.request_stop()
                sample_telemetry=cache.snapshot(time.monotonic())
                report['samples'].append(dict(monotonic=sample_time,processed=index,submitted=submitted,
                    monitor=monitor,count=counter.total_count,invalid=counter.invalid_frames,memory=_memory(),
                    cpu=_cpu_usage_percent(previous_cpu,cpu),vdma_status=vdma_status,
                    video_state=video_pump.state if video_pump else 'OFF',
                    video_pump_status=video_pump.status() if video_pump else {'state':'OFF'},
                    telemetry=sample_telemetry,
                    crossing_snapshots=captures.status() if captures is not None else {'enabled':False},
                    **timing_sample_fields(args.transfer_timing,report.get('transfer_timing_last')),
                    counters=owner.call(snapshot_counters,control)))
                if display_health is not None and video_pump is not None:
                    performance_sample=(sample_telemetry.get('performance') or {})
                    perf_value=performance_sample.get('value') or {}
                    stale=performance_sample.get('stale',True)
                    display_fps=None if stale else perf_value.get('display_fps')
                    fps_sample_time=None if stale else performance_sample.get('sampled_monotonic')
                    event=display_health.observe(sample_time,index,controls.paused,
                        args.display=='on',video_pump.status(),display_fps=display_fps,
                        fps_sample_time=fps_sample_time)
                    if event is not None:
                        event['action']='stop_requested' if args.display_health_stop else 'record_only'
                        report['display_health_events'].append(event)
                        print(json.dumps(event,separators=(',',':')),flush=True)
                        if args.display_health_stop:running=False
                if diag_writer is not None and sample_time-last_diag>=args.diag_snapshot_interval:
                    diag_writer.submit(compact_diagnostic_snapshot(report_snapshot()))
                    last_diag=sample_time
                previous_cpu=cpu;last_sample=sample_time
        report['duration_s']=time.monotonic()-started
        report.update(trace_sha256=trace.digest(),trace_frames=trace.frames_seen,
                      trace_annotations_truncated=trace.annotations_truncated)
        cleanup_stage='camera_close'
        pre_stop_video_state=None
        display_held_reason='vdma_error_during_loop' if display_held else None
        hdmi_close_attempted=False
        if camera is not None:camera.close();camera=None
        cleanup_stage='drawing_join'
        if process is not None:
            stop_drawing.set();process.join(timeout=15)
            if process.is_alive():raise TimeoutError('drawing child failed to stop')
            for slot,state in enumerate(inputs.statuses()):
                if state==READING:inputs._frames.reclaim_reader(slot,dead_consumer=True)
            for slot,state in enumerate(outputs.statuses()):
                if state==WRITING:outputs.reclaim_writer(slot,dead_producer=True)
        if args.display=='on' and video_pump is not None:
            pre_stop_video_state=video_pump.state
            cleanup_stage='video_pump_stop';video_pump.request_stop()
            cleanup_stage='video_pump_join';join_ok=video_pump.join(3)
            if not display_held:
                display_held,display_held_reason=resolve_display_held(
                    pre_stop_video_state,join_ok,video_pump.state)
            submitted=video_pump.submitted
            inputs.close()
            if not video_pump.is_alive:outputs.close()
        report['monitor_after']=owner.call(snapshot,hw.monitor)
        report['counters_after']=owner.call(snapshot_counters,control)
        report['counter_delta']=counter_delta(report['counters_before'],report['counters_after'])
        report['received_bytes_total']=rx_total_bytes
        report['transfer_consistency']=validate_transfer_totals(report['counter_delta'],index,rx_total_bytes)
        report['dma_after']={hex(i):owner.call(dma.read,i) for i in (0,4,0x30,0x34)}
        if any(report['dma_after'][hex(i)]&0x770 or not report['dma_after'][hex(i)]&2 for i in (4,0x34)):
            raise RuntimeError('RLE DMA idle not proven')
        owner.call(transport.close)
        cleanup_stage='hdmi_close'
        if args.display=='on' and not display_held:
            hdmi_close_attempted=True
            video_owner.call(hw.hdmi.close,timeout=3)
        cleanup_stage='owner_close'
        report['vdma_after']={hex(i):owner.call(hw.vdma.read,i) for i in (0,4)}
        if not report['vdma_after']['0x4']&1 and not display_held:raise RuntimeError('VDMA halt not proven')
        report['pipeline_after']=owner.call(read_pipeline,control)
        owner.close()
        if not display_held:video_owner.close()
        cleanup_stage='evidence_write'
        if captures is not None:report['snapshot_writer_drained']=captures.close()
        if exporter is not None:report['report_writer_drained']=exporter.close()
        report.update(state='COUNT_STOPPED_DISPLAY_FAIL_HOLD' if display_held else 'STOPPED',processed=index,submitted=submitted,published=published,
            dropped_preview=dropped,total_count=counter.total_count,invalid_frames=counter.invalid_frames,
            camera_released=True,buffers_released=not display_held,rle_buffers_released=True,after_memory=_memory(),
            drawing_published=int(drawing_counts[0]),drawing_dropped=int(drawing_counts[1]),
            video_pump_state=video_pump.state if video_pump else None,
            video_pump_error=video_pump.error if video_pump else None,
            video_pump_failed_at=video_pump.failed_at if video_pump else None,
            video_pump_failed_stage=video_pump.failed_stage if video_pump else None,
            video_pump_last_frame_id=video_pump.last_frame_id if video_pump else None,
            pre_stop_video_state=pre_stop_video_state,
            display_held=display_held,display_held_reason=display_held_reason,
            hdmi_close_attempted=hdmi_close_attempted,cleanup_stage=cleanup_stage)
        report['timings_ms']={name:{f'p{p}':float(np.percentile(values,p)) if values else None for p in (50,95,99)}
                             for name,values in stages.items() if name!='transfer_parts'}
        if args.transfer_timing and stages['transfer_parts']:
            report['transfer_timing_percentiles_ms']=summarize_transfer_timing(list(stages['transfer_parts']))
        if video_pump is not None and not video_pump.is_alive:report['timings_ms'].update(video_pump.timing_percentiles_ms())
    except BaseException as error:
        if captures is not None:report['snapshot_writer_drained']=captures.close()
        if exporter is not None:report['report_writer_drained']=exporter.close()
        if stop_drawing is not None:stop_drawing.set()
        if video_pump is not None:video_pump.request_stop()
        if process is not None:process.join(timeout=15)
        if display_frame is not None:owner.retain(display_frame)
        report.update(state='FAIL_HOLD',error=repr(error),diagnostics=owner.diagnostics(),
            video_pump_state=video_pump.state if video_pump else None,
            video_pump_error=video_pump.error if video_pump else None,
            video_pump_failed_at=video_pump.failed_at if video_pump else None,
            video_pump_failed_stage=video_pump.failed_stage if video_pump else None,
            cleanup_stage=cleanup_stage)
        if diag_writer is not None:
            report['diag_snapshot']=diag_writer.close()
        fail_hold(lambda:write_evidence(args.output,report_snapshot()),'INTEGRATED_FAIL_HOLD '+repr(error))
    if diag_writer is not None:
        diag_writer.submit(compact_diagnostic_snapshot(report_snapshot()))
        report['diag_snapshot']=diag_writer.close()
    write_evidence(args.output,report_snapshot())
    print(json.dumps({k:report[k] for k in ('state','processed','submitted','duration_s','total_count')}),flush=True)
    if display_held:
        # Counting and its buffers stopped safely, but uncertain display CMA is
        # retained until an operator independently establishes safe recovery.
        fail_hold(lambda:None,'DISPLAY_RESOURCES_RETAINED')


if __name__=='__main__':main()
