"""G3 candidate: one RLE/HDMI owner, independent drawing, bounded shared frames."""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
import hashlib
import json
import math
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

from .bundle import verify_bundle, verify_runtime, verify_offload_capabilities, sha256
from .camera import LatestCamera
from .compositor import DashboardComposer
from .controls import ButtonDecoder, ControlState
from .count_controls import advance_count, make_runtime_counter
from scripts.rle.features import PlRegionFeatures
from .crossing_snapshots import CrossingSnapshots
from .async_evidence import EvidenceExporter
from .driver_owner import DriverOwner
from .evidence import write_evidence,fail_hold,TraceBuffer
from .g1_run import setup_hardware, start_display_output, snapshot, _memory, _parse_proc_stat, _cpu_usage_percent
from .hp2 import sync_hp1_readwrite32
from .g2_readback import read_pipeline, snapshot_counters
from .pressure import make_input, counter_delta, validate_transfer_totals
from .preview_inputs import PreviewInputs
from .video_pump import VideoPump, DISPLAY_FAIL_HOLD
from .telemetry_cache import TelemetryCache,parse_process_stat
from .probe import _collect_iio
from .shared_frames import SharedFrames, WRITING, READING
from .button_events import ButtonEventRunner, interrupt_available
from .diagnostic_mode import DiagnosticPolicy
from .exposure import ExposureController, V4L2CtlBackend
from .runtime_metrics import RuntimeSampler, counter_rates
from .pl_preview import PlPreview
from scripts.rle.protocol import TX_BYTES, RX_CAPACITY
from scripts.rle.transport import RAW_TX_BYTES, RleDmaTransport
from scripts.wo0015.board_preflight import audit_process_ownership, inspect_pynq_global_state


def rasterize_into(packet, output):
    if not packet.valid:
        raise ValueError('display requires a valid RLE packet')
    if output.shape != (packet.height,packet.width) or output.dtype != np.uint8:
        raise ValueError('binary workspace must match packet geometry and uint8')
    output.fill(0)
    for word in packet.runs:
        value=int(word)
        output[value >> 20, (value >> 10) & 1023:(value & 1023)+1]=255
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


def dashboard_values(count, paused, page, telemetry, manifest=None, *, mode='normal',
                     exposure=None, exposure_error=None, pl_rates=None, runtime=None,
                     runtime_diagnostics=None, button_status='IRQ READY'):
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
    cpu=resource.get('cpu',{})
    constants={'lut':53200,'ff':106400,'bram_tiles':140,'dsp':220}
    resource_manifest=(manifest.get('resources',{})
                       if isinstance(manifest,dict) else {})
    if not isinstance(resource_manifest,dict):
        resource_manifest={}
    static_percent={}
    for key,total in constants.items():
        used=resource_manifest.get(key)
        try:
            if (isinstance(used,bool) or not isinstance(used,(int,float))
                    or not math.isfinite(used) or used<0):
                static_percent[key]=None
            else:
                static_percent[key]=round(100*used/total,1)
        except (TypeError,ValueError,OverflowError):
            static_percent[key]=None
    exposure=exposure if isinstance(exposure,dict) else {}
    if exposure_error:
        exposure_text='\u66dd\u5149\u5931\u8d25: '+str(exposure_error)[:8]
        if all(key in exposure for key in ('minimum_ms','maximum_ms')):
            exposure_range_text='\u8303\u56f4(\u4e0a\u6b21) {:.0f}\u2013{:.0f} ms / \u00b11 ms'.format(
                exposure['minimum_ms'],exposure['maximum_ms'])
        else:
            exposure_range_text='\u8303\u56f4 --'
    elif all(key in exposure for key in ('minimum_ms','maximum_ms','current_ms')):
        exposure_text='\u66dd\u5149 {:.1f} ms'.format(exposure['current_ms'])
        exposure_range_text='\u8303\u56f4 {:.0f}\u2013{:.0f} ms / \u00b11 ms'.format(
            exposure['minimum_ms'],exposure['maximum_ms'])
    else:
        exposure_text='\u66dd\u5149 unavailable'
        exposure_range_text='\u8303\u56f4 --'
    rates=pl_rates if isinstance(pl_rates,dict) and pl_rates.get('fresh') else None
    pl_text=('RLE active (incl wait) {:.0f}% / IN {:.1f} / OUT {:.1f} MB/s'.format(
        rates['rle_activity_pct'],rates['input_mb_s'],rates['output_mb_s'])
        if rates and rates.get('rle_activity_pct') is not None else
        'RLE activity / throughput unavailable')
    threads=(runtime or {}).get('threads',{}) if isinstance(runtime,dict) else {}
    thread_entries=[]
    for tid,entry in threads.items():
        if not isinstance(entry,dict):continue
        ticks=sum(value for value in (entry.get('utime_ticks_delta'),
                    entry.get('stime_ticks_delta')) if isinstance(value,int))
        thread_entries.append((ticks,str(entry.get('name') or tid)))
    interval=(runtime or {}).get('interval_s',3.0) if isinstance(runtime,dict) else 3.0
    thread_text=('threads '+', '.join('{} {:.1f}tick/s'.format(name[:7],ticks/interval)
                                   for ticks,name in sorted(thread_entries,reverse=True)[:2])
                 if thread_entries else 'threads unavailable')
    queue_text='preview pools unavailable'
    if isinstance(runtime_diagnostics,dict):
        diagnostic_threads=runtime_diagnostics.get('threads',[])
        if diagnostic_threads:
            thread_text=' | '.join('{} CPU{:.0f}ms run{:.0f}ms wait{:.0f}ms'.format(
                str(entry.get('name') or entry.get('tid','?'))[:4],
                entry['cpu_time_ms'],entry['run_time_ms'],entry['wait_time_ms'])
                for entry in diagnostic_threads[:2])
        input_pool=runtime_diagnostics.get('input_pool')
        output_pool=runtime_diagnostics.get('output_pool')
        if isinstance(input_pool,dict) and isinstance(output_pool,dict):
            queue_text='IN {}/{}  OUT {}/{}'.format(input_pool.get('ready','--'),
                input_pool.get('capacity','--'),output_pool.get('ready','--'),
                output_pool.get('capacity','--'))
    rates=rates or {}
    return dict(count=count,state='PAUSED' if paused else 'RUNNING',detail_page=bool(page),
        mode='DIAGNOSTIC' if mode=='diagnostic' else 'NORMAL',exposure_text=exposure_text,
        exposure_range_text=exposure_range_text,
        pl_text=pl_text,thread_text=thread_text,queue_text=queue_text,
        button_status=button_status,
        rle_fresh=bool(rates),rle_activity_pct=rates.get('rle_activity_pct'),
        input_mb_s=rates.get('input_mb_s'),output_mb_s=rates.get('output_mb_s'),
        input_stall_pct=rates.get('input_stall_pct'),output_stall_pct=rates.get('output_stall_pct'),
        display_fps=perf.get('display_fps'),processing_fps=perf.get('processing_fps'),
        processing_ms=perf.get('processing_ms'),
        cpu_percent=cpu.get('system'),cpu0_percent=cpu.get('cpu0'),cpu1_percent=cpu.get('cpu1'),
        memory_percent=memory_percent,
        static_lut_percent=static_percent['lut'],static_ff_percent=static_percent['ff'],
        static_bram_percent=static_percent['bram_tiles'],static_dsp_percent=static_percent['dsp'])


def runtime_diagnostic_snapshot(runtime, input_pool, output_pool, tick_hz):
    """Keep a bounded, display-ready sample of scheduler and preview-pool state."""
    threads=[]
    if isinstance(runtime,dict):
        interval=runtime.get('interval_s')
        source=runtime.get('threads',{})
        if isinstance(interval,(int,float)) and interval>0 and isinstance(source,dict):
            for tid,entry in source.items():
                if not isinstance(entry,dict):
                    continue
                user=entry.get('utime_ticks_delta')
                system=entry.get('stime_ticks_delta')
                run_ns=entry.get('schedstat_run_ns_delta')
                wait_ns=entry.get('schedstat_wait_ns_delta')
                if (type(user) is not int or user < 0 or type(system) is not int
                        or system < 0 or type(tick_hz) is not int or tick_hz <= 0):
                    continue
                cpu_ms=(user+system)*1000.0/tick_hz
                if not all(isinstance(value,(int,float)) and value>=0 for value in
                           (cpu_ms,run_ns,wait_ns)):
                    continue
                threads.append(dict(tid=str(tid),name=str(entry.get('name') or tid),
                    cpu_time_ms=cpu_ms,run_time_ms=run_ns/1_000_000,
                    wait_time_ms=wait_ns/1_000_000))
    threads.sort(key=lambda entry:(entry['cpu_time_ms'],entry['run_time_ms']),reverse=True)
    return dict(interval_s=(runtime or {}).get('interval_s') if isinstance(runtime,dict) else None,
        threads=threads[:2], input_pool=_pool_occupancy(input_pool),
        output_pool=_pool_occupancy(output_pool))


def _pool_occupancy(pool):
    if not isinstance(pool,dict):
        return None
    return {key:pool.get(key) for key in ('ready','capacity')}


def _read_pool_stats(pool):
    if pool is None:
        return None
    try:
        return pool.stats_snapshot()
    except Exception:
        return None


def record_diagnostic_frame(policy, trace, captures, frame_id, result, counter,
                            gray, packet, *, trajectory_epoch=0, **metadata):
    trace.add(frame_id,result,trajectory_epoch,enabled=policy.trace_enabled)
    if policy.trace_enabled and captures is not None:
        captures.observe(gray,packet,result,counter,trajectory_epoch=trajectory_epoch,**metadata)


def reset_capture_context_on_transition(captures, transition):
    if captures is not None and transition is not None:
        captures.reset_context('diagnostic_mode_changed')


def prepare_preview_binary(packet, output, binary_background=True):
    if binary_background:
        rasterize_into(packet,output)
    return output


def prepare_preview_frame(gray, binary, *, backend, black_frame=None):
    if backend == 'software':
        return gray, binary
    if backend != 'pl':
        raise ValueError('unknown preview backend')
    if black_frame is None:
        black_frame = np.zeros((400, 640), dtype=np.uint8)
    return black_frame, black_frame


class PreviewPublishGate:
    """Limit PL panel frames to 2 Hz unless a visible state changes."""
    def __init__(self, interval_s=.5):
        self.interval_s=interval_s
        self.last_at=None
        self.last_key=None

    def should_publish(self, now, key, *, force=False):
        if force:
            self.last_at,self.last_key=now,key
            return True
        if key==self.last_key or (self.last_at is not None and now-self.last_at<self.interval_s):
            return False
        self.last_at,self.last_key=now,key
        return True


def button_gpio_irq_configured(overlay, channel):
    """Require the HWH-declared GPIO IRQ capability and PYNQ async channel API."""
    try:
        parameters=overlay.ip_dict['btns_gpio']['parameters']
        present=str(parameters['C_INTERRUPT_PRESENT']).strip().lower() in ('1','0x1','true')
    except (AttributeError,KeyError,TypeError):
        return False
    return present and interrupt_available(channel)


def verify_selected_offload_bundle(args, manifest, hwh):
    """Reject selected IRQ/PBO/RAW modes before touching the overlay."""
    if (args.button_input == 'irq' or args.preview_backend == 'pl'
            or getattr(args,'region_backend','native') == 'pl'
            or args.input_mode == 'raw'):
        return verify_offload_capabilities(manifest, hwh)
    return None


class OwnerRegionFeatureReader:
    """Serialize FRG1 reads on the existing RLE driver owner."""
    def __init__(self, reader, owner):
        self.reader,self.owner=reader,owner
        self.last_read_ms=0.0
        self.last_reason='not_read'

    def read(self, frame):
        before=dict(self.reader.reason_counts)
        def operation():
            started=time.perf_counter()
            features=self.reader.read(frame)
            elapsed=(time.perf_counter()-started)*1000
            reasons=[key for key,value in self.reader.reason_counts.items()
                     if value>before.get(key,0)]
            return features,elapsed,(reasons[-1] if reasons else 'accepted')
        features,self.last_read_ms,self.last_reason=self.owner.call(operation)
        return features


def create_region_feature_reader(backend, mmio, *, owner, mmio_length):
    if backend=='native':
        return None
    if backend!='pl':raise ValueError('unknown region backend')
    if mmio is None or mmio_length<0x5000:
        raise ValueError('PBO1 mapping must cover FRG1 record window (at least 0x5000 bytes)')
    return owner.call(lambda:OwnerRegionFeatureReader(PlRegionFeatures(mmio),owner))


def region_feature_frame_summary(feature_status, result, counter):
    return {'feature_status':feature_status,
            'feature_backend_used':counter.last_backend if result is not None else 'not_counted'}


def validate_region_backend(args, manifest, hwh):
    if args.region_backend=='pl':
        if args.preview_backend!='pl':
            raise ValueError('PL region backend requires --preview-backend pl')
        return verify_offload_capabilities(manifest,hwh)
    return None


def button_input_poll_callback(mode, worker_getter):
    """Create the 10 ms compatibility poll hook only when explicitly selected."""
    if mode not in ('irq','polling','off'):
        raise ValueError('unknown button input mode')
    if mode != 'polling':
        return None
    def poll():
        worker=worker_getter()
        if worker is not None:
            worker.poll()
    return poll


class ButtonInputWorker:
    """Use the overlay owner's loop for IRQ waits or its explicit polling hook."""
    def __init__(self, owner, mode, channel, read_bits, on_commands, on_error):
        if mode not in ('irq','polling','off'):
            raise ValueError('unknown button input mode')
        self.owner=owner
        self.mode,self.channel=mode,channel
        self.read_bits,self.on_commands,self.on_error=read_bits,on_commands,on_error
        self.decoder=ButtonDecoder()
        self._task=None
        self._active=False

    def start(self):
        if self.mode=='off':return
        if self.mode=='irq' and not interrupt_available(self.channel):
            raise RuntimeError('GPIO interrupt unavailable; select --button-input polling only for legacy hardware')
        self._active=True
        if self.mode=='irq':
            self._task=self.owner.call(lambda:self.owner.start_background(
                lambda: ButtonEventRunner(self.channel,self.read_bits,self._emit,
                    decoder=self.decoder).run(),on_error=self.on_error))

    def stop(self,timeout=2):
        self._active=False
        if self._task is not None and not self._task.done():
            self.owner.call(lambda:self.owner.cancel_background(self._task,timeout=timeout),
                            timeout=timeout+1)
        self._task=None

    def _emit(self, commands):
        for command in commands:self.on_commands(command)

    def poll(self):
        if self._active and self.mode=='polling':
            commands=self.decoder.update(self.read_bits(),time.monotonic())
            if commands:self._emit(commands)


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
            lease=inputs.wait_for_frame(timeout=None,stop=stop)
            if lease is None:
                continue
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
                    dashboard=meta.get('dashboard'),pl_preview=meta.get('pl_preview',False))
                timings=composer.last_timings_ms
                outputs.publish(slot,lease.session_uuid,lease.frame_id,
                    scale_ns=int(timings['scale_ms']*1e6),draw_ns=int(timings['draw_ms']*1e6))
                slot=None;counters[0]+=1
            finally:
                if slot is not None:outputs.abort_write(slot)
                inputs.release(lease)
    except BaseException as error:
        try:errors.put_nowait(repr(error))
        except queue.Full:pass
        raise
    finally:
        inputs.close();outputs.close()


def build_argument_parser():
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
    parser.add_argument('--button-input',choices=('irq','polling','off'),default='irq',
        help='IRQ is required by new candidates; polling is an explicit legacy-board fallback')
    parser.add_argument('--preview-backend',choices=('software','pl'),default='pl')
    parser.add_argument('--region-backend',choices=('native','pl'),default='native')
    parser.add_argument('--input-mode',choices=('packet','raw'),default='packet')
    parser.add_argument('--display',choices=('on','off'),default='on')
    parser.add_argument('--duration',type=float,default=90)
    parser.add_argument('--frames',type=int,default=1200)
    parser.add_argument('--continuous',action='store_true',help='camera service; bounded RAM diagnostics until explicit stop')
    parser.add_argument('--commands',type=Path,help='JSON map frame number -> ordered control commands')
    parser.add_argument('--inject-drawing-exit',type=int,help='cooperative drawing exit after this processed frame')
    parser.add_argument('--inject-drawing-start-failure',choices=('create','start','after-start'),
        help='inject one drawing process creation/start error on its first launch attempt')
    parser.add_argument('--freeze-telemetry-at-frame',type=int,help='test staleness without stopping safety checks')
    parser.add_argument('--font',default='/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf')
    parser.add_argument('--latin-font',default='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    parser.add_argument('--inherited-lock-fds',nargs=2,type=int,metavar=('DESKTOP_FD','STANDALONE_FD'),
        help=argparse.SUPPRESS)
    return parser


def main(argv=None):
    parser=build_argument_parser()
    args=parser.parse_args(argv)
    if args.snapshot_max_events < 1 or args.snapshot_max_mib < 1:
        parser.error('snapshot storage limits must be positive')
    if not args.execute or not 1<=args.duration<=7200 or not 1<=args.frames<=200000 or (args.continuous and args.source!='camera'):
        parser.error('requires execute, duration 1..7200 and frames 1..200000')
    if args.inject_drawing_start_failure and args.display!='on':
        parser.error('drawing process failure injection requires display on')
    if args.region_backend=='pl' and args.preview_backend!='pl':
        parser.error('--region-backend pl requires --preview-backend pl')
    if args.output.exists() or not args.native_lib.is_file():parser.error('new output and prebuilt library required')
    if args.startup_record and args.startup_record.exists():parser.error('startup record already exists')
    files=verify_bundle(args.manifest,args.manifest_sha256)
    manifest=json.loads(args.manifest.read_text())
    verify_selected_offload_bundle(args,manifest,files['hwh'])
    validate_region_backend(args,manifest,files['hwh'])
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
        preview_submitted_fps=None,
        preview_fps_scope='accepted_for_display_not_scanout',
        recoveries=deque(maxlen=2048),runtime_samples=deque(maxlen=1800),button_raw_sample_count=0,
        button_raw_transitions=deque(maxlen=2048),button_raw_transition_total=0,button_raw_transition_truncated=0,
        before_memory=_memory(),scope='G3 candidate; G4 physical disconnection/cold-start not asserted')
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
        return result
    report['cpu_scope']='system/core normalized 0..100%; process 100% equals one core'
    report['preview_backend']=args.preview_backend;report['input_mode']=args.input_mode
    report['camera_configuration']=dict(by_id=args.camera,requested_format='MJPG',
        validated_decoded_shape=[400,640],decode='IMREAD_GRAYSCALE',negotiated_fps='NOT_QUERIED')
    controls=ControlState(binary_background=True);events=queue.Queue(maxsize=64)
    button_errors=queue.Queue(maxsize=1);button_worker=None
    button_status={'irq':'IRQ READY','polling':'LEGACY POLLING','off':'BUTTONS OFF'}[args.button_input]
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
    def read_button_bits():
        nonlocal previous_raw_button_bits
        bits=int(hw.buttons.read())&15
        sampled_at=time.monotonic()
        with button_raw_lock:
            report['button_raw_sample_count']+=1
            if previous_raw_button_bits is None or bits!=previous_raw_button_bits:
                report['button_raw_transition_total']+=1
                report['button_raw_transitions'].append(dict(monotonic=sampled_at,bits=bits))
                report['button_raw_transition_truncated']=report['button_raw_transition_total']-len(report['button_raw_transitions'])
                previous_raw_button_bits=bits
        return bits
    def enqueue_button_command(command):
        try:events.put_nowait(command)
        except queue.Full:
            report['recoveries'].append(dict(kind='button_event_queue_full',command=command,
                monotonic=time.monotonic()))
    def report_button_error(error):
        try:button_errors.put_nowait(error)
        except queue.Full:pass
    # One manager controls the overlay lifetime. Separate driver domains prevent
    # a blocked display call from taking the RLE counting domain with it.
    video_owner=DriverOwner(poll=button_input_poll_callback(
        args.button_input,lambda:button_worker))
    owner=DriverOwner()
    ctx=mp.get_context('spawn')
    drawing_errors=ctx.Queue(maxsize=1);drawing_counts=ctx.RawArray('Q',2)
    session=uuid.UUID(report['session_id'])
    previous_cpu=_parse_proc_stat(Path('/proc/stat').read_text())
    stages={key:deque(maxlen=20000) for key in ('transfer_ms','count_ms','exposure_control_ms','snapshot_ms','publish_ms','scale_ms','draw_ms','copy_submit_ms')}
    try:
        dma=owner.retain(MMIO(0x40400000,0x1000))
        for offset in (4,0x34):
            state=owner.call(dma.read,offset)
            if state&0x770 or not state&3:raise RuntimeError('RLE DMA not safely stopped')
        report['hp1_readwrite32']=owner.call(sync_hp1_readwrite32, files['hwh'], MMIO)
        hw=video_owner.call(lambda:setup_hardware(files,video_owner,start_display=False),timeout=60)
        report['clocks_mhz']=hw.clocks
        if args.button_input=='irq':
            if not button_gpio_irq_configured(hw.overlay,hw.buttons):
                raise RuntimeError('GPIO IRQ unavailable in btns_gpio HWH; use --button-input polling only for legacy hardware')
        control=owner.retain(MMIO(0x43c20000,0x1000))
        pipeline=owner.call(read_pipeline,control)
        tx=owner.retain(allocate(shape=(RAW_TX_BYTES if args.input_mode=='raw' else TX_BYTES,),dtype='u1'))
        rx=owner.retain(allocate(shape=(RX_CAPACITY,),dtype='u1'))
        transport=RleDmaTransport(hw.overlay.axi_dma_0.sendchannel,hw.overlay.axi_dma_0.recvchannel,
            dma,control,tx,rx,input_mode=args.input_mode)
        report['region_backend']=args.region_backend
        region_reader=None
        pl_preview=None;pbo_mmio=None
        if args.preview_backend=='pl':
            def create_pl_preview():
                nonlocal pbo_mmio
                pbo_mmio=owner.retain(MMIO(0x43c50000,0x10000))
                return PlPreview(pbo_mmio)
            pl_preview=owner.call(create_pl_preview)
        region_reader=create_region_feature_reader(args.region_backend,pbo_mmio,owner=owner,
            mmio_length=0x10000 if pbo_mmio is not None else 0)
        if pipeline['state']['done_seen']:transport._completed_frames=1
        # Validate register signatures and stopped DMA channels before HDMI
        # configure/start; the first transfer must not be the first capability check.
        owner.call(transport._preflight_hardware)
        if args.button_input=='irq':
            button_worker=ButtonInputWorker(video_owner,'irq',hw.buttons,read_button_bits,
                enqueue_button_command,report_button_error)
            button_worker.start()
        elif args.button_input=='polling':
            button_worker=ButtonInputWorker(video_owner,'polling',hw.buttons,read_button_bits,
                enqueue_button_command,report_button_error)
            button_worker.start()
        video_owner.call(start_display_output,hw.hdmi,timeout=60)
        counter=make_runtime_counter(args.native_lib,profile=args.counting_profile)
        exporter=EvidenceExporter()
        report['counting_profile']=args.counting_profile
        report['counting_configuration']=dict(axis=counter.count_axis,line_x=counter.line_x,
            line_y=counter.line_y,direction=counter.direction,motion='left_to_right')
        report['source_identity']=source_identity(Path(__file__).resolve().parents[2])
        diagnostic_policy=DiagnosticPolicy('normal')
        report['diagnostic_mode']='normal';report['diagnostic_transitions']=deque(maxlen=128)
        diagnostic_error=None
        def create_crossing_snapshots():
            return CrossingSnapshots(args.output.with_suffix('.crossings'),
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
            video_pump=VideoPump(outputs,hw,video_owner,session)
        else:video_owner.call(hw.hdmi.close,timeout=3)
        exposure_controller=None;exposure_status=None;exposure_error=None
        if args.source=='camera':
            camera=LatestCamera(args.camera)
            try:
                exposure_controller=ExposureController(V4L2CtlBackend(args.camera))
                exposure_status=exposure_controller.status()
            except Exception as error:
                exposure_error=str(error)
                report['recoveries'].append(dict(kind='exposure_query',error=repr(error),
                    monotonic=time.monotonic()))
        report['counters_before']=owner.call(snapshot_counters,control)
        started=last_sample=time.monotonic();index=submitted=published=dropped=0
        camera_index=-1;next_camera_retry=0;injection_done=False;next_drawing_retry=0
        cache=TelemetryCache();last_resource=last_xadc=last_performance=0
        last_performance_index=last_performance_submitted=0;last_gray=last_metadata=None;last_valid_id=0;rx_total_bytes=0
        black_frame=np.zeros((400,640),dtype=np.uint8)
        panel_gate=PreviewPublishGate(.5);last_pl_accepts=0;binary_fps=None
        last_binary_published=0
        binary_fps_window_at=started
        trajectory_epoch=0;camera_in_error=False
        previous_process={}
        last_rle_stats={'runs':0,'rx_bytes':0}
        runtime_sampler=RuntimeSampler(pid=os.getpid(),interval_s=3.0)
        next_runtime_sample=started+3.0;runtime_sample=None;runtime_diagnostics=None
        previous_pl_counters=report['counters_before'];previous_pl_at=started
        pl_rates=counter_rates(previous_pl_counters,previous_pl_counters,1.0)
        tick_hz=os.sysconf('SC_CLK_TCK');page_size=os.sysconf('SC_PAGE_SIZE')
        while running and (args.continuous or (time.monotonic()-started<args.duration and index<args.frames)):
            now=time.monotonic()
            try:
                button_error=button_errors.get_nowait()
            except queue.Empty:
                button_error=None
            if button_error is not None:
                button_status='IRQ ERROR' if args.button_input=='irq' else 'BUTTON INPUT ERROR'
                report['recoveries'].append(dict(kind='button_input',error=repr(button_error),
                    monotonic=now,mode=args.button_input))
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
            packet=owner.call(transport.transfer,gray,index,timeout=6)
            rx_total_bytes+=packet.actual_bytes
            last_rle_stats={'runs':packet.run_count,'rx_bytes':packet.actual_bytes}
            transfer_end=time.monotonic()
            region_features=region_reader.read(packet) if region_reader is not None and packet.valid else None
            result=advance_count(counter,controls,packet,boundary,capture_monotonic_ns=capture_ns,
                region_features=region_features)
            count_end=time.monotonic()
            if args.region_backend=='pl' and packet.valid:
                last_rle_stats.update(feature_read_ms=region_reader.last_read_ms,
                    **region_feature_frame_summary(region_reader.last_reason,result,counter))
            exposure_control_ms=0.0
            if pl_preview is not None and packet.valid:
                def submit_pl_preview():
                    try:
                        return (pl_preview.submit(packet,result,line_x=counter.line_x,enabled=True),None)
                    except Exception as error:
                        return (False,repr(error))
                accepted,preview_error=owner.call(submit_pl_preview,timeout=2)
                if accepted:last_pl_accepts+=1
                if preview_error is not None:
                    report['recoveries'].append(dict(kind='pl_preview_submit',frame=index,
                        error=preview_error,monotonic=time.monotonic()))
            for command in boundary:
                if command not in ('exposure_up','exposure_down'):continue
                exposure_started=time.monotonic()
                try:
                    if exposure_controller is None:
                        raise RuntimeError('camera exposure control unavailable for this source')
                    if command=='exposure_up':exposure_controller.exposure_up()
                    else:exposure_controller.exposure_down()
                    exposure_status=exposure_controller.status();exposure_error=None
                except Exception as error:
                    exposure_error=str(error)
                    report['recoveries'].append(dict(kind='exposure_adjust',command=command,
                        error=repr(error),frame=index,monotonic=time.monotonic()))
                finally:
                    exposure_control_ms=(time.monotonic()-exposure_started)*1000
                    stages['exposure_control_ms'].append(exposure_control_ms)
                    if exposure_control_ms>100:
                        report['recoveries'].append(dict(kind='exposure_control_slow',command=command,
                            elapsed_ms=exposure_control_ms,frame=index,monotonic=time.monotonic()))
            mode='diagnostic' if controls.diagnostic else 'normal'
            transition=diagnostic_policy.set_mode(mode,frame_id=index,timestamp_ns=capture_ns)
            if transition is not None:
                report['diagnostic_transitions'].append(transition)
                report['diagnostic_mode']=mode
                reset_capture_context_on_transition(captures,transition)
            if diagnostic_policy.trace_enabled and captures is None and args.crossing_snapshots=='on':
                try:captures=create_crossing_snapshots();diagnostic_error=None
                except Exception as error:
                    diagnostic_error=str(error)
                    report['recoveries'].append(dict(kind='diagnostic_writer',error=repr(error),
                        frame=index,monotonic=time.monotonic()))
            if result is not None:
                record_diagnostic_frame(diagnostic_policy,trace,captures,index,result,counter,
                    gray,packet,trajectory_epoch=trajectory_epoch,
                    capture_monotonic_ns=capture_ns,camera_sequence=camera_index if camera else index,
                    paused=controls.paused,commands=boundary,
                    timings={'transfer_ms':(transfer_end-frame_start)*1000,'count_ms':(count_end-transfer_end)*1000})
            snapshot_end=time.monotonic()
            stages['transfer_ms'].append((transfer_end-frame_start)*1000)
            stages['count_ms'].append((count_end-transfer_end)*1000)
            stages['snapshot_ms'].append((snapshot_end-count_end)*1000)
            # These are the last completed frame's measurements, not window
            # averages; retain its identity so dense-frame stalls are visible.
            last_rle_stats.update(frame_id=packet.frame_id,
                camera_sequence=camera_index if camera else index,
                detection_count=len(result.detections) if result is not None else None,
                exposure_control_ms=exposure_control_ms,
                last_frame_timings_ms=dict(transfer_ms=(transfer_end-frame_start)*1000,
                    count_ms=(count_end-transfer_end)*1000,
                    exposure_control_ms=exposure_control_ms,
                    snapshot_ms=(snapshot_end-count_end)*1000,
                    **({'feature_read_ms':last_rle_stats.get('feature_read_ms'),
                        'feature_status':last_rle_stats.get('feature_status'),
                        'feature_backend_used':last_rle_stats.get('feature_backend_used')}
                       if region_reader is not None else {}),
                    **(result.roi_subtimings_ms if result is not None else {})))
            if boundary:report['events'].append(dict(frame=index,commands=boundary,count=counter.total_count))
            if result is not None:
                if args.display=='on':
                    if pl_preview is None:prepare_preview_binary(packet,binary,True)
                    metadata=result_metadata(result,paused=controls.paused,binary_background=True,
                        page=controls.page,trajectory_epoch=trajectory_epoch,count_axis=counter.count_axis,
                        line_position=counter.line_x if counter.count_axis=='x' else counter.line_y,
                        direction=counter.direction)
                    metadata['pl_preview']=pl_preview is not None
                    metadata['preview_backend']=args.preview_backend
                    metadata['status_lines']=['本次会话累计 {}'.format(counter.total_count),
                        '暂停计数' if controls.paused else '运行中',
                        '诊断快照 '+('启用' if diagnostic_policy.trace_enabled and captures is not None else
                            '不可用' if diagnostic_error else '关闭')]
                    if metadata['annotations_truncated']:metadata['status_lines'].append('标注仅显示前128项 / 计数未截断')
                    telemetry=cache.snapshot(time.monotonic())
                    metadata['dashboard']=dashboard_values(counter.total_count,controls.paused,
                    controls.page,telemetry,manifest,mode=diagnostic_policy.mode,
                    exposure=exposure_status,exposure_error=exposure_error,
                    pl_rates=pl_rates,runtime=runtime_sample,
                    runtime_diagnostics=runtime_diagnostics,button_status=button_status)
                    metadata['dashboard']['binary_fps']=binary_fps
                    metadata['dashboard']['preview_submitted_fps']=binary_fps
                    metadata['dashboard']['preview_status']='PL READY' if pl_preview is not None else '软件二值预览'
                    if pl_preview is not None and len(result.detections)>16:
                        metadata['status_lines'].append('PL overlay boxes truncated to 16')
                        metadata['pl_overlay_boxes_truncated']=len(result.detections)-16
                    metadata['dashboard']['exposure_latency_text']=(
                        'EXP {:.0f}ms'.format(exposure_control_ms)
                        if exposure_control_ms>100 else '')
                    performance=telemetry.get('performance')
                    if performance:
                        metadata['status_lines'].append('处理 {:.1f} FPS{}'.format(performance['value']['processing_fps'],' / 过期' if performance['stale'] else ''))
                    stale=[name for name,sample in telemetry.items() if sample['stale']]
                    if stale:metadata['status_lines'].append('过期: '+','.join(stale))
                    if controls.page:
                        metadata['status_lines']+=['640x400 / RLE 50MHz','显示 1280x720 60Hz',
                            'LUT {} / FF {}'.format(manifest.get('resources',{}).get('lut','未知'),manifest.get('resources',{}).get('ff','未知')),
                            'BRAM {} / DSP {}'.format(manifest.get('resources',{}).get('bram_tiles','未知'),manifest.get('resources',{}).get('dsp','未知'))]
                        resource=telemetry.get('resources',{}).get('value',{})
                        metadata['status_lines'].append('CMA '+str(resource.get('memory',{}).get('CmaFree','未知')))
                        cpu=resource.get('cpu',{})
                        metadata['status_lines'].append('CPU0 {:.0f}% / CPU1 {:.0f}% / 系统 {:.0f}%'.format(cpu.get('cpu0',0),cpu.get('cpu1',0),cpu.get('system',0)))
                        process_info=resource.get('processes',{})
                        text=lambda key:('{:.0f}%'.format(process_info[key]['cpu_percent']) if process_info.get(key,{}).get('cpu_percent') is not None else '未知')
                        metadata['status_lines'].append('进程 计数 {} / 绘图 {}'.format(text('count_capture_video'),text('drawing')))
                        available=int(resource.get('memory',{}).get('MemAvailable','0 kB').split()[0])/1024
                        metadata['status_lines'].append('内存 {:.0f} MB / SD {:.1f} GB'.format(available,resource.get('sd_free_bytes',0)/(1024**3)))
                        channels=telemetry.get('xadc',{}).get('value',{}).get('channels',[])
                        temperatures=[c['value_si'] for c in channels if c['channel'].startswith('in_temp') and isinstance(c['value_si'],(int,float))]
                        if temperatures:metadata['status_lines'].append('温度 {:.1f} °C'.format(temperatures[0]))
                        rails={c['label']:c['value_si'] for c in channels if c['label'] in ('vccint','vccaux','vccbram') and isinstance(c['value_si'],(int,float))}
                        rail_text=lambda key:('{:.3f}'.format(rails[key]) if key in rails else '未知')
                        if rails:metadata['status_lines'].append('INT {} / AUX {} / BRAM {} V'.format(rail_text('vccint'),rail_text('vccaux'),rail_text('vccbram')))
                        if performance:metadata['status_lines'].append('RLE {} runs / DMA {} B'.format(performance['value']['runs'],performance['value']['rx_bytes']))
                    last_gray=gray;last_metadata=metadata;last_valid_id=index
                    if process is not None and process.is_alive():
                        selected_gray,selected_binary=prepare_preview_frame(gray,binary,
                            backend=args.preview_backend,black_frame=black_frame)
                        panel_key=(counter.total_count,controls.page,controls.diagnostic,
                            exposure_status,exposure_error,pl_rates,runtime_sample,button_status)
                        if pl_preview is None or panel_gate.should_publish(time.monotonic(),panel_key):
                            if inputs.publish(selected_gray,selected_binary,session,index,metadata):
                                published+=1
                                if pl_preview is None:last_binary_published+=1
                            else:dropped+=1
                    else:dropped+=1
                    stages['publish_ms'].append((time.monotonic()-snapshot_end)*1000)
            elif inputs is not None and last_gray is not None:
                fault=dict(last_metadata);fault['status_lines']=['RLE帧无效 / 本帧拒计','累计 {}'.format(counter.total_count)]
                fault['dashboard']=dict(fault.get('dashboard',{}),state='INVALID FRAME',preview_status='INVALID FRAME')
                if process is not None and process.is_alive():
                    selected_gray,selected_binary=prepare_preview_frame(last_gray,binary,
                        backend=args.preview_backend,black_frame=black_frame)
                    inputs.publish(selected_gray,selected_binary,session,last_valid_id,fault)
            index+=1
            if args.display=='on':
                submitted=video_pump.submitted
                if process is not None and args.inject_drawing_exit is not None and index>=args.inject_drawing_exit and not injection_done:
                    stop_drawing.set();injection_done=True
            sample_now=time.monotonic()
            if sample_now>=next_runtime_sample:
                runtime_sample=runtime_sampler.sample()
                runtime_diagnostics=runtime_diagnostic_snapshot(runtime_sample,
                    _read_pool_stats(inputs),_read_pool_stats(outputs),tick_hz)
                report['runtime_samples'].append(dict(monotonic=sample_now,
                    **runtime_diagnostics))
                current_pl_counters=owner.call(snapshot_counters,control)
                pl_rates=counter_rates(previous_pl_counters,current_pl_counters,
                                       sample_now-previous_pl_at)
                previous_pl_counters=current_pl_counters;previous_pl_at=sample_now
                next_runtime_sample=sample_now+3.0
                if pl_preview is not None:
                    elapsed=sample_now-binary_fps_window_at
                    binary_fps=last_pl_accepts/elapsed if elapsed>0 else None
                    last_pl_accepts=0;binary_fps_window_at=sample_now
                else:
                    elapsed=sample_now-binary_fps_window_at
                    binary_fps=(last_binary_published/elapsed if elapsed>0 else None)
                    last_binary_published=0;binary_fps_window_at=sample_now
                report['preview_submitted_fps']=binary_fps
                report['binary_fps']=binary_fps  # compatibility; scope is explicit above
            if sample_now-last_sample>=2:
                sample_time=time.monotonic();cpu=_parse_proc_stat(Path('/proc/stat').read_text())
                monitor=owner.call(snapshot,hw.monitor)
                vdma_status=owner.call(hw.vdma.read,4)
                if vdma_status&0x770:
                    display_held=True
                    if video_pump is not None:video_pump.request_stop()
                report['samples'].append(dict(monotonic=sample_time,processed=index,submitted=submitted,
                    preview_submitted_fps=report.get('preview_submitted_fps'),
                    preview_fps_scope=report['preview_fps_scope'],
                    monitor=monitor,count=counter.total_count,invalid=counter.invalid_frames,memory=_memory(),
                    cpu=_cpu_usage_percent(previous_cpu,cpu),vdma_status=vdma_status,
                    video_state=video_pump.state if video_pump else 'OFF',telemetry=cache.snapshot(time.monotonic()),
                    crossing_snapshots=captures.status() if captures is not None else {'enabled':False},
                    counters=owner.call(snapshot_counters,control),
                    network=(runtime_sample or {}).get('network')))
                previous_cpu=cpu;last_sample=sample_time
        report['duration_s']=time.monotonic()-started
        report.update(trace_sha256=trace.digest(),trace_frames=trace.frames_seen,
                      trace_annotations_truncated=trace.annotations_truncated)
        if button_worker is not None:button_worker.stop()
        if camera is not None:camera.close();camera=None
        if process is not None:
            stop_drawing.set();process.join(timeout=15)
            if process.is_alive():raise TimeoutError('drawing child failed to stop')
            for slot,state in enumerate(inputs.statuses()):
                if state==READING:inputs._frames.reclaim_reader(slot,dead_consumer=True)
            for slot,state in enumerate(outputs.statuses()):
                if state==WRITING:outputs.reclaim_writer(slot,dead_producer=True)
        if args.display=='on' and video_pump is not None:
            video_pump.request_stop()
            if not video_pump.join(3):display_held=True
            if video_pump.state==DISPLAY_FAIL_HOLD:display_held=True
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
        if args.display=='on' and not display_held:video_owner.call(hw.hdmi.close,timeout=3)
        report['vdma_after']={hex(i):owner.call(hw.vdma.read,i) for i in (0,4)}
        if not report['vdma_after']['0x4']&1 and not display_held:raise RuntimeError('VDMA halt not proven')
        report['pipeline_after']=owner.call(read_pipeline,control)
        owner.close()
        if not display_held:video_owner.close()
        if captures is not None:report['snapshot_writer_drained']=captures.close()
        if exporter is not None:report['report_writer_drained']=exporter.close()
        report.update(state='COUNT_STOPPED_DISPLAY_FAIL_HOLD' if display_held else 'STOPPED',processed=index,submitted=submitted,published=published,
            dropped_preview=dropped,total_count=counter.total_count,invalid_frames=counter.invalid_frames,
            camera_released=True,buffers_released=not display_held,rle_buffers_released=True,after_memory=_memory(),
            drawing_published=int(drawing_counts[0]),drawing_dropped=int(drawing_counts[1]))
        report['timings_ms']={name:{f'p{p}':float(np.percentile(values,p)) if values else None for p in (50,95,99)}
                             for name,values in stages.items()}
        if video_pump is not None and not video_pump.is_alive:report['timings_ms'].update(video_pump.timing_percentiles_ms())
    except BaseException as error:
        if button_worker is not None:
            try:button_worker.stop()
            except BaseException as stop_error:
                report['recoveries'].append(dict(kind='button_worker_stop',error=repr(stop_error),
                    monotonic=time.monotonic()))
        if captures is not None:report['snapshot_writer_drained']=captures.close()
        if exporter is not None:report['report_writer_drained']=exporter.close()
        if stop_drawing is not None:stop_drawing.set()
        if video_pump is not None:video_pump.request_stop()
        if process is not None:process.join(timeout=15)
        if display_frame is not None:owner.retain(display_frame)
        report.update(state='FAIL_HOLD',error=repr(error),diagnostics=owner.diagnostics())
        fail_hold(lambda:write_evidence(args.output,report_snapshot()),'INTEGRATED_FAIL_HOLD '+repr(error))
    write_evidence(args.output,report_snapshot())
    print(json.dumps({k:report[k] for k in ('state','processed','submitted','duration_s','total_count')}),flush=True)
    if display_held:
        # Counting and its buffers stopped safely, but uncertain display CMA is
        # retained until an operator independently establishes safe recovery.
        fail_hold(lambda:None,'DISPLAY_RESOURCES_RETAINED')


if __name__=='__main__':main()
