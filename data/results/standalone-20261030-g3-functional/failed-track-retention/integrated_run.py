"""G3 candidate: one RLE/HDMI owner, independent drawing, bounded shared frames."""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import signal
import time
import uuid

import cv2
import numpy as np

from .bundle import verify_bundle, verify_runtime, sha256
from .camera import LatestCamera
from .compositor import GrayComposer
from .controls import ButtonDecoder, ControlState
from .count_controls import advance_count
from .driver_owner import DriverOwner
from .evidence import write_evidence,fail_hold
from .g1_run import setup_hardware, snapshot, _memory, _parse_proc_stat, _cpu_usage_percent
from .g2_readback import read_pipeline, snapshot_counters
from .pressure import make_input, counter_delta, validate_transfers
from .preview_inputs import PreviewInputs
from .video_pump import VideoPump, DISPLAY_FAIL_HOLD
from .telemetry_cache import TelemetryCache
from .probe import _collect_iio
from .shared_frames import SharedFrames, WRITING, READING
from scripts.rle.counter import RleCounter
from scripts.rle.protocol import TX_BYTES, RX_CAPACITY
from scripts.rle.transport import RleDmaTransport
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


def result_metadata(result, *, paused, binary_background, page):
    return dict(count=result.total_count, paused=paused,
        binary_background=binary_background,page=page,
        boxes=[[d.x_min,d.y_min,d.x_max,d.y_max] for d in result.detections],
        tracks=[[str(t['track_id']),int(t['detection']['centroid_x']),
                 int(t['detection']['centroid_y'])] for t in result.tracks])


def drawing_process(inputs, outputs, stop, errors, counters, font, latin_font):
    """Own ordinary RAM only; a drawing failure cannot free CMA or reload PL."""
    cv2.setNumThreads(2)
    composer=GrayComposer(font,latin_font_path=latin_font,gray_interpolation=cv2.INTER_LINEAR)
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
                composer.render(lease.gray,lease.binary,lease.session_uuid,lease.frame_id,
                    boxes=meta['boxes'],tracks=meta['tracks'],status_lines=lines,
                    binary_background=meta['binary_background'],output=pixels,line_y=200)
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


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--boot-id',required=True)
    parser.add_argument('--native-lib',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source',choices=('replay','camera'),default='replay')
    parser.add_argument('--camera',default='/dev/v4l/by-id/usb-HSK_USB_Camera_20210616103-video-index0')
    parser.add_argument('--display',choices=('on','off'),default='on')
    parser.add_argument('--duration',type=float,default=90)
    parser.add_argument('--frames',type=int,default=1200)
    parser.add_argument('--continuous',action='store_true',help='camera service; bounded RAM diagnostics until explicit stop')
    parser.add_argument('--commands',type=Path,help='JSON map frame number -> ordered control commands')
    parser.add_argument('--inject-drawing-exit',type=int,help='cooperative drawing exit after this processed frame')
    parser.add_argument('--freeze-telemetry-at-frame',type=int,help='test staleness without stopping safety checks')
    parser.add_argument('--font',default='/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf')
    parser.add_argument('--latin-font',default='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    args=parser.parse_args(argv)
    if not args.execute or not 1<=args.duration<=7200 or not 1<=args.frames<=200000 or (args.continuous and args.source!='camera'):
        parser.error('requires execute, duration 1..7200 and frames 1..200000')
    if args.output.exists() or not args.native_lib.is_file():parser.error('new output and prebuilt library required')
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
    for name in ('/tmp/material-counter-desktop.lock','/run/material-standalone.lock'):
        stream=open(name,'a');fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(stream)
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
        diagnostic_limits={'samples':1800,'events':2048,'recoveries':2048,'frame_trace':20000},
        session_id=str(uuid.uuid4()),ownership=audit_process_ownership(),state='STARTING',
        material_accuracy='NOT_MEASURED',samples=deque(maxlen=1800),events=deque(maxlen=2048),
        recoveries=deque(maxlen=2048),frame_trace=deque(maxlen=20000),
        before_memory=_memory(),scope='G3 candidate; G4 physical disconnection/cold-start not asserted')
    controls=ControlState();decoder=ButtonDecoder();events=queue.Queue(maxsize=64)
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
        if hw is not None:
            for command in decoder.update(int(hw.buttons.read())&15,time.monotonic()):
                events.put_nowait(command)
    # One manager controls the overlay lifetime. Separate driver domains prevent
    # a blocked display call from taking the RLE counting domain with it.
    video_owner=DriverOwner()
    owner=DriverOwner(poll=poll)
    ctx=mp.get_context('spawn')
    drawing_errors=ctx.Queue(maxsize=1);drawing_counts=ctx.RawArray('Q',2)
    session=uuid.UUID(report['session_id'])
    previous_cpu=_parse_proc_stat(Path('/proc/stat').read_text());last_cpu=time.monotonic()
    stages={key:deque(maxlen=20000) for key in ('transfer_ms','count_ms','publish_ms','scale_ms','draw_ms','copy_submit_ms')}
    try:
        dma=owner.retain(MMIO(0x40400000,0x1000))
        for offset in (4,0x34):
            state=owner.call(dma.read,offset)
            if state&0x770 or not state&3:raise RuntimeError('RLE DMA not safely stopped')
        hw=video_owner.call(setup_hardware,files,video_owner,timeout=60)
        report['clocks_mhz']=hw.clocks
        control=owner.retain(MMIO(0x43c20000,0x1000))
        pipeline=owner.call(read_pipeline,control)
        tx=owner.retain(allocate(shape=(TX_BYTES,),dtype='u1'))
        rx=owner.retain(allocate(shape=(RX_CAPACITY,),dtype='u1'))
        transport=RleDmaTransport(hw.overlay.axi_dma_0.sendchannel,hw.overlay.axi_dma_0.recvchannel,dma,control,tx,rx)
        if pipeline['state']['done_seen']:transport._completed_frames=1
        counter=RleCounter(library=args.native_lib)
        binary=np.empty((400,640),np.uint8)
        if args.display=='on':
            inputs=PreviewInputs.create(ctx);outputs=SharedFrames.create(ctx)
            def start_drawing():
                nonlocal process,stop_drawing
                stop_drawing=ctx.Event()
                process=ctx.Process(target=drawing_process,args=(inputs,outputs,stop_drawing,
                    drawing_errors,drawing_counts,args.font,args.latin_font))
                process.start()
                report['drawing_pid']=process.pid
            start_drawing()
            video_pump=VideoPump(outputs,hw,video_owner,session)
        else:video_owner.call(hw.hdmi.close,timeout=3)
        if args.source=='camera':camera=LatestCamera(args.camera)
        report['counters_before']=owner.call(snapshot_counters,control)
        started=last_sample=time.monotonic();index=submitted=published=dropped=0
        camera_index=-1;next_camera_retry=0;injection_done=False;next_drawing_retry=0
        cache=TelemetryCache();last_resource=last_xadc=last_performance=0
        last_performance_index=0;last_gray=last_metadata=None;last_valid_id=0;rx_lengths=[]
        while running and (args.continuous or (time.monotonic()-started<args.duration and index<args.frames)):
            now=time.monotonic()
            if export_requested:
                report.update(state='RUNNING',processed=index,submitted=video_pump.submitted if video_pump else 0,
                    duration_s=now-started,total_count=counter.total_count,invalid_frames=counter.invalid_frames,
                    export_monotonic=now)
                exported=args.output.with_name(args.output.stem+'.snapshot-'+str(exports)+'.json')
                try:write_evidence(exported,report)
                except OSError as error:
                    report['recoveries'].append(dict(kind='evidence_export',error=repr(error),monotonic=now))
                exports+=1;export_requested=False
            frozen=args.freeze_telemetry_at_frame is not None and index>=args.freeze_telemetry_at_frame
            if not frozen:
                if now-last_resource>=3:
                    cpu=_parse_proc_stat(Path('/proc/stat').read_text())
                    disk=os.statvfs('/home/xilinx')
                    cache.record('resources',dict(memory=_memory(),cpu=_cpu_usage_percent(previous_cpu,cpu),
                        sd_free_bytes=disk.f_bavail*disk.f_frsize),3,time.monotonic())
                    previous_cpu=cpu;last_resource=now
                if now-last_xadc>=5:
                    sensor_errors=[];iio=_collect_iio(Path('/'),sensor_errors)
                    cache.record('xadc',dict(channels=iio['channels'],errors=sensor_errors),5,time.monotonic())
                    last_xadc=now
                if now-last_performance>=2:
                    fps=(index-last_performance_index)/(now-last_performance) if last_performance else 0
                    cache.record('performance',dict(processing_fps=fps,processed=index),2,time.monotonic())
                    last_performance=now;last_performance_index=index
            if args.display=='on' and not process.is_alive() and now>=next_drawing_retry:
                process.join(timeout=0)
                # Only the confirmed dead child held these leases. CMA remains
                # with the owner and HDMI keeps scanning throughout recovery.
                for slot,state in enumerate(inputs.statuses()):
                    if state==READING:inputs._frames.reclaim_reader(slot,dead_consumer=True)
                for slot,state in enumerate(outputs.statuses()):
                    if state==WRITING:outputs.reclaim_writer(slot,dead_producer=True)
                report['recoveries'].append(dict(kind='drawing',frame=index,exitcode=process.exitcode,monotonic=now))
                process.close()
                try:drawing_errors.get_nowait()
                except queue.Empty:pass
                start_drawing()
                next_drawing_retry=now+5
            if args.source=='camera':
                if camera is None:
                    if now<next_camera_retry:time.sleep(.01);continue
                    camera=LatestCamera(args.camera);camera_index=-1
                try:
                    camera_index,gray,captured=camera.read(after=camera_index,timeout=.25)
                except (RuntimeError,TimeoutError) as error:
                    camera.close();camera=None;next_camera_retry=time.monotonic()+5
                    report['recoveries'].append(dict(kind='camera',frame=index,error=repr(error),monotonic=now))
                    if inputs is not None and last_gray is not None:
                        fault=dict(last_metadata);fault['status_lines']=['相机断连 / 停止新增计数','每5秒重试 / 恢复新轨迹','累计 {}'.format(counter.total_count)]
                        inputs.publish(last_gray,binary,session,last_valid_id,fault)
                    old_total=counter.total_count;counter=RleCounter(library=args.native_lib);counter.total_count=old_total
                    continue
                capture_ns=int(captured*1e9)
            else:
                gray=make_input('moving',index);capture_ns=(index+1)*33_333_333
            frame_start=time.monotonic()
            boundary=list(commands.get(str(index),()))
            while True:
                try:boundary.append(events.get_nowait())
                except queue.Empty:break
            packet=owner.call(transport.transfer,gray,index,timeout=6)
            rx_lengths.append(packet.actual_bytes)
            transfer_end=time.monotonic()
            result=advance_count(counter,controls,packet,boundary,capture_monotonic_ns=capture_ns)
            count_end=time.monotonic()
            stages['transfer_ms'].append((transfer_end-frame_start)*1000)
            stages['count_ms'].append((count_end-transfer_end)*1000)
            if boundary:report['events'].append(dict(frame=index,commands=boundary,count=counter.total_count))
            if result is not None:
                report['frame_trace'].append(dict(frame=index,count=result.total_count,
                    detections=[asdict(d) for d in result.detections],tracks=result.tracks,
                    events=[asdict(e) for e in result.events],suppressed=[asdict(e) for e in result.suppressed_events]))
                if args.display=='on':
                    rasterize_into(packet,binary)
                    metadata=result_metadata(result,paused=controls.paused,binary_background=controls.binary_background,page=controls.page)
                    metadata['status_lines']=['本次会话累计 {}'.format(counter.total_count),
                        '暂停计数' if controls.paused else '运行中',
                        '重启清零 / 不写盘']
                    telemetry=cache.snapshot(time.monotonic())
                    performance=telemetry.get('performance')
                    if performance:
                        metadata['status_lines'].append('处理 {:.1f} FPS{}'.format(performance['value']['processing_fps'],' / 过期' if performance['stale'] else ''))
                    for name,sample in telemetry.items():
                        if sample['stale']:metadata['status_lines'].append('{} / 过期'.format(name))
                    if controls.page:
                        metadata['status_lines']+=['640x400 / RLE 50MHz','显示 1280x720 60Hz',
                            'LUT {} / FF {}'.format(manifest.get('resources',{}).get('lut','未知'),manifest.get('resources',{}).get('ff','未知'))]
                        resource=telemetry.get('resources',{}).get('value',{})
                        metadata['status_lines'].append('CMA '+str(resource.get('memory',{}).get('CmaFree','未知')))
                        for core,value in resource.get('cpu',{}).items():metadata['status_lines'].append('{} {:.1f}%'.format(core,value))
                        for channel in telemetry.get('xadc',{}).get('value',{}).get('channels',[]):
                            if channel['unit']=='m°C':metadata['status_lines'].append('温度 {} °C'.format(channel['value_si']))
                    last_gray=gray;last_metadata=metadata;last_valid_id=index
                    if inputs.publish(gray,binary,session,index,metadata):published+=1
                    else:dropped+=1
                    stages['publish_ms'].append((time.monotonic()-count_end)*1000)
            elif inputs is not None and last_gray is not None:
                fault=dict(last_metadata);fault['status_lines']=['RLE帧无效 / 本帧拒计','累计 {}'.format(counter.total_count)]
                inputs.publish(last_gray,binary,session,last_valid_id,fault)
            index+=1
            if args.display=='on':
                submitted=video_pump.submitted
                if args.inject_drawing_exit is not None and index>=args.inject_drawing_exit and not injection_done:
                    stop_drawing.set();injection_done=True
            if time.monotonic()-last_sample>=2:
                sample_time=time.monotonic();cpu=_parse_proc_stat(Path('/proc/stat').read_text())
                monitor=owner.call(snapshot,hw.monitor)
                vdma_status=owner.call(hw.vdma.read,4)
                if vdma_status&0x770:
                    display_held=True
                    if video_pump is not None:video_pump.request_stop()
                report['samples'].append(dict(monotonic=sample_time,processed=index,submitted=submitted,
                    monitor=monitor,count=counter.total_count,invalid=counter.invalid_frames,memory=_memory(),
                    cpu=_cpu_usage_percent(previous_cpu,cpu),vdma_status=vdma_status,
                    video_state=video_pump.state if video_pump else 'OFF',telemetry=cache.snapshot(time.monotonic()),
                    counters=owner.call(snapshot_counters,control)))
                previous_cpu=cpu;last_sample=sample_time
        report['duration_s']=time.monotonic()-started
        if camera is not None:camera.close();camera=None
        if process is not None:
            stop_drawing.set();process.join(timeout=15)
            if process.is_alive():raise TimeoutError('drawing child failed to stop')
            for slot,state in enumerate(inputs.statuses()):
                if state==READING:inputs._frames.reclaim_reader(slot,dead_consumer=True)
            for slot,state in enumerate(outputs.statuses()):
                if state==WRITING:outputs.reclaim_writer(slot,dead_producer=True)
            video_pump.request_stop()
            if not video_pump.join(3):display_held=True
            if video_pump.state==DISPLAY_FAIL_HOLD:display_held=True
            submitted=video_pump.submitted
            inputs.close()
            if not video_pump.is_alive:outputs.close()
        report['monitor_after']=owner.call(snapshot,hw.monitor)
        report['counters_after']=owner.call(snapshot_counters,control)
        report['counter_delta']=counter_delta(report['counters_before'],report['counters_after'])
        if index:
            report['transfer_consistency']=validate_transfers(report['counter_delta'],index,rx_lengths)
        else:
            if any(report['counter_delta']['counters'][key] for key in ('input_beats','output_beats')):
                raise RuntimeError('unexpected DMA traffic without processed frames')
            report['transfer_consistency']={'valid':True,'frames':0,'expected_input_beats':0,'expected_output_beats':0}
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
        report.update(state='COUNT_STOPPED_DISPLAY_FAIL_HOLD' if display_held else 'STOPPED',processed=index,submitted=submitted,published=published,
            dropped_preview=dropped,total_count=counter.total_count,invalid_frames=counter.invalid_frames,
            camera_released=True,buffers_released=not display_held,rle_buffers_released=True,after_memory=_memory(),
            drawing_published=int(drawing_counts[0]),drawing_dropped=int(drawing_counts[1]))
        report['timings_ms']={name:{f'p{p}':float(np.percentile(values,p)) if values else None for p in (50,95,99)}
                             for name,values in stages.items()}
        if video_pump is not None and not video_pump.is_alive:report['timings_ms'].update(video_pump.timing_percentiles_ms())
    except BaseException as error:
        if stop_drawing is not None:stop_drawing.set()
        if video_pump is not None:video_pump.request_stop()
        if process is not None:process.join(timeout=15)
        if display_frame is not None:owner.retain(display_frame)
        report.update(state='FAIL_HOLD',error=repr(error),diagnostics=owner.diagnostics())
        fail_hold(lambda:write_evidence(args.output,report),'INTEGRATED_FAIL_HOLD '+repr(error))
    write_evidence(args.output,report)
    print(json.dumps({k:report[k] for k in ('state','processed','submitted','duration_s','total_count')}),flush=True)
    if display_held:
        # Counting and its buffers stopped safely, but uncertain display CMA is
        # retained until an operator independently establishes safe recovery.
        fail_hold(lambda:None,'DISPLAY_RESOURCES_RETAINED')


if __name__=='__main__':main()
