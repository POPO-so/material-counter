"""640x400 camera and Gray8 runner with default PL RLE or opt-in PL JPEG."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.rle.protocol import (WIDTH, HEIGHT, TX_BYTES, RX_CAPACITY, encode_mask,
                                  decode_packet, reconstruct)
from scripts.rle.components import build_native
from scripts.rle.counter import RleCounter
from scripts.rle.transport import RleDmaTransport
from scripts.rle.artifacts import load_manifest, sha256


def synthetic_frames(count, pattern='moving'):
    for i in range(count):
        gray = np.zeros((HEIGHT, WIDTH), np.uint8)
        y = 140 + (i % 12)*12
        if pattern=='moving':gray[y:y+16, 200:220] = 200
        elif pattern=='noise':gray[:,::2]=255
        elif pattern=='rings':
            for x in (100,300,500):
                gray[y:y+24,x:x+24]=200;gray[y+8:y+16,x+8:x+16]=0
        elif pattern!='empty':raise ValueError('unknown synthetic pattern')
        yield gray


def golden_mask(gray, *, roi=(0,0,WIDTH,HEIGHT), threshold=128, intensity_offset=0, morph_mode=0):
    mapped = np.clip(gray.astype(np.int16)+intensity_offset, 0, 255)
    binary = np.where(mapped >= threshold, 255, 0).astype(np.uint8)
    x0,y0,x1,y1 = roi
    mask = np.zeros_like(binary)
    mask[y0:y1,x0:x1] = binary[y0:y1,x0:x1]
    if morph_mode:
        operation = cv2.erode if morph_mode==1 else cv2.dilate
        mask = operation(mask, np.ones((3,3),np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
        mask[:y0]=0;mask[y1:]=0;mask[:,:x0]=0;mask[:,x1:]=0
    return mask


def decode_camera_frame(raw):
    data=np.asarray(raw)
    if data.dtype!=np.uint8 or data.size<2 or tuple(data.reshape(-1)[:2])!=(255,216):
        raise ValueError("camera must return raw MJPEG with JPEG SOI")
    gray=cv2.imdecode(data.reshape(-1),cv2.IMREAD_GRAYSCALE)
    if gray is None or gray.dtype!=np.uint8 or gray.shape!=(HEIGHT,WIDTH):
        raise ValueError("camera JPEG must decode directly to 640x400 Gray8")
    return gray


def should_check_preload_dma(pynq_state, expected_bit_sha256):
    """Check RLE DMA before load only when the pinned RLE BIT is already loaded."""
    status = pynq_state.get('pl_state_status')
    if status == 'COLD_BOOT_NO_GLOBAL_STATE':
        return False
    if status != 'GLOBAL_STATE_PRESENT':
        raise RuntimeError('PYNQ global state does not establish the loaded BIT identity')
    loaded_path = pynq_state.get('loaded_bit_path')
    loaded_sha1 = pynq_state.get('loaded_bit_sha1')
    loaded_sha256 = pynq_state.get('loaded_bit_sha256')
    if not loaded_path or not isinstance(loaded_sha1, str) or not isinstance(loaded_sha256, str):
        raise RuntimeError('PYNQ global state does not establish a verified loaded BIT')
    path = Path(loaded_path)
    if not path.is_file():
        raise RuntimeError('PYNQ global state loaded BIT file is unavailable for verification')
    contents = path.read_bytes()
    if (hashlib.sha1(contents).hexdigest() != loaded_sha1.lower() or
            hashlib.sha256(contents).hexdigest() != loaded_sha256.lower()):
        raise RuntimeError('PYNQ global state BIT hashes do not match its loaded BIT file')
    return loaded_sha256.lower() == expected_bit_sha256.lower()


def camera_mjpeg_frames(device,count):
    capture=cv2.VideoCapture(device,cv2.CAP_V4L2)
    try:
        if not capture.isOpened(): raise RuntimeError("camera open failed")
        capture.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH,WIDTH);capture.set(cv2.CAP_PROP_FRAME_HEIGHT,HEIGHT)
        capture.set(cv2.CAP_PROP_FPS,60);capture.set(cv2.CAP_PROP_CONVERT_RGB,0)
        capture.set(cv2.CAP_PROP_BUFFERSIZE,1)
        for i in range(count+2):
            ok,raw=capture.read()
            if not ok: raise RuntimeError("camera read failed")
            if i>=2: yield raw
    finally:
        capture.release()


def camera_frames(device,count):
    for raw in camera_mjpeg_frames(device,count):
        yield decode_camera_frame(raw)


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if '--jpeg-pl' in argv:
        if argv.count('--jpeg-pl') != 1:
            raise SystemExit('--jpeg-pl may only be specified once')
        argv.remove('--jpeg-pl')
        from scripts.experiments.run_jpeg_counter import main as jpeg_pl_main
        return jpeg_pl_main(argv)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jpeg-pl',action='store_true',
        help='use raw MJPG through the experimental PL JPEG path and its routed-artifact checks')
    parser.add_argument('--mode',choices=('offline','self-test','camera'),default='offline')
    parser.add_argument('--frames',type=int,default=12)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--boot-id')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--load-overlay',action='store_true')
    parser.add_argument('--native-lib',type=Path)
    parser.add_argument('--device',type=int,default=0)
    parser.add_argument('--threshold',type=int,default=128)
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--morph',type=int,choices=(0,1,2),default=0)
    parser.add_argument('--roi-top',type=int,default=0)
    parser.add_argument('--roi-bottom',type=int,default=HEIGHT)
    parser.add_argument('--line-y',type=int,default=200)
    parser.add_argument('--min-area',type=int,default=48)
    parser.add_argument('--counter-config',type=Path,
        help='JSON MaskCounter settings in original 640x400 coordinates; overrides CLI counting defaults')
    parser.add_argument('--verify-pixels',action='store_true')
    parser.add_argument('--pattern',choices=('moving','empty','noise','rings'),default='moving')
    args=parser.parse_args(argv)
    if args.output.exists():raise FileExistsError(args.output)
    if not 1<=args.frames<=100000:parser.error('frames must be in 1..100000')
    if not 0<=args.threshold<=255 or not -255<=args.offset<=255:parser.error('invalid threshold/offset')
    settings=dict(line_y=args.line_y,roi_top=args.roi_top,roi_bottom=args.roi_bottom,min_area=args.min_area)
    if args.counter_config:
        overrides=json.loads(args.counter_config.read_text(encoding='utf-8'))
        if not isinstance(overrides,dict) or 'library' in overrides:
            parser.error('counter-config must be a JSON object of counting settings')
        settings.update(overrides)
    # Validate counting and ROI settings before any board access or DMA allocation.
    counter=RleCounter(library=None,**settings)
    if (counter.width,counter.height)!=(WIDTH,HEIGHT):parser.error('counter must use 640x400 coordinates')
    top,bottom=counter._roi_bounds()
    if counter.count_axis=='y' and not top<=counter.line_y<bottom:
        parser.error('ROI must contain counting line_y')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    roi=(0,top,WIDTH,bottom)
    config=dict(roi=roi,threshold=args.threshold,intensity_offset=args.offset,morph_mode=args.morph)
    report=dict(status='STARTING',mode=args.mode,resolution=[WIDTH,HEIGHT],frames=0,
        pixel_verified_frames=0,count_accuracy='NOT_MEASURED',tx_bytes=TX_BYTES,rx_capacity=RX_CAPACITY,
        configuration=config,counter_configuration=settings,pattern=args.pattern,
        frames_evidence=[],total_count=0,invalid_frames=0)
    transport=None;allocated=[];held=False
    try:
        if args.mode!='offline':
            if not args.execute or not args.boot_id or not args.manifest or not args.manifest_sha256:
                raise ValueError('hardware requires execute, boot-id, manifest and pinned manifest-sha256')
            if not hasattr(os,'geteuid') or os.geteuid()!=0 or os.environ.get('XILINX_XRT')!='/usr':
                raise RuntimeError('hardware requires root and XILINX_XRT=/usr')
            manifest=load_manifest(args.manifest,args.manifest_sha256)
            from scripts.wo0015 import board_preflight as preflight,board_once
            if preflight.read_boot_id()!=args.boot_id:raise RuntimeError('board boot changed')
            report['boot_id']=args.boot_id
            report['ownership']=preflight.audit_process_ownership()
            report['cma_before']=preflight.read_cma_meminfo()
            import pynq
            from pynq import Overlay,MMIO,allocate
            bit=args.manifest.resolve().parent/manifest['bit_path']
            before_state=preflight.inspect_pynq_global_state(
                pynq.__file__,require_empty_shutdown=True,require_loaded_bit=False)
            expected_bit_loaded=should_check_preload_dma(before_state,manifest['bit_sha256'])
            if expected_bit_loaded:
                # Refuse to overwrite an active DMA transfer on the known RLE overlay.
                before=MMIO(manifest['dma_base'],0x10000)
                before_status=[int(before.read(v)) for v in (0x04,0x34)]
                if any(v&0x770 or not v&3 for v in before_status):
                    raise RuntimeError('existing DMA is active or faulty before overlay load')
            # A cold boot or verified different BIT has no RLE DMA register map;
            # explicitly load the pinned RLE artifact before touching its registers.
            bit_path_matches=(before_state.get('loaded_bit_path') is not None and
                Path(before_state['loaded_bit_path']).resolve()==bit.resolve())
            overlay=Overlay(str(bit),download=(args.load_overlay or not expected_bit_loaded or
                not bit_path_matches))
            report['overlay']=preflight.inspect_pynq_global_state(pynq.__file__,require_empty_shutdown=True,
                expected_bit_path=bit,expected_bit_sha256=manifest['bit_sha256'],require_loaded_bit=True,
                allow_equivalent_bit_path=True)
            api=SimpleNamespace(read_phys32=board_once.read_phys32_readonly,write_phys32=board_once.write_afi1_phys32)
            report['afi1']=board_once.sync_afi1_hp1_32bit(api,SimpleNamespace(hwh_path=bit.with_suffix('.hwh')),stage='rle-start')
            tx=allocate(shape=(TX_BYTES,),dtype=np.uint8);allocated.append(tx)
            rx=allocate(shape=(RX_CAPACITY,),dtype=np.uint8);allocated.append(rx)
            transport=RleDmaTransport(overlay.axi_dma_0.sendchannel,overlay.axi_dma_0.recvchannel,
                MMIO(manifest['dma_base'],0x10000),MMIO(manifest['control_base'],0x10000),tx,rx)
            report['manifest_sha256']=sha256(args.manifest)
        library=args.native_lib
        if library is None:
            library=args.output.parent/('rle_components_v2.dll' if os.name=='nt' else 'rle_components_v2.so')
            if not library.exists():build_native(library)
        counter.library=library
        source=(camera_frames(args.device,args.frames) if args.mode=='camera' else synthetic_frames(args.frames,args.pattern))
        start=time.perf_counter();last=start
        for frame_id,gray in enumerate(source):
            capture_end=time.perf_counter();stamp=time.monotonic_ns()
            if transport is None:
                golden=golden_mask(gray,**config)
                frame=decode_packet(encode_mask(golden,frame_id=frame_id,params_version=1,**config))
            else:
                frame=transport.transfer(gray,frame_id,params_version=1,**config)
            dma_end=time.perf_counter()
            verify=args.verify_pixels or args.mode in ('offline','self-test')
            if verify and frame.valid:
                golden=golden_mask(gray,**config)
                if not np.array_equal(reconstruct(frame),golden):raise RuntimeError('RLE pixel reconstruction mismatch')
                report['pixel_verified_frames']+=1
            verify_end=time.perf_counter()
            result=counter.update_rle(frame,capture_monotonic_ns=stamp)
            end=time.perf_counter()
            report['frames']+=1
            report['total_count']=counter.total_count
            report['invalid_frames']=counter.invalid_frames
            report['frames_evidence'].append(dict(frame_id=frame_id,runs=frame.run_count,
                total_runs=frame.total_run_count,total_fg=frame.total_fg,status=frame.status,
                rx_bytes=frame.actual_bytes,hw_timestamp=frame.hw_timestamp,
                capture_ms=(capture_end-last)*1000,transfer_parse_ms=(dma_end-capture_end)*1000,
                verification_ms=(verify_end-dma_end)*1000,count_ms=(end-verify_end)*1000,
                total_count=counter.total_count,detections=([asdict(d) for d in result.detections] if result else []),
                dma=(transport.snapshot() if transport else None)))
            last=end
        elapsed=time.perf_counter()-start
        if args.mode!='offline':
            if preflight.read_boot_id()!=args.boot_id:raise RuntimeError('board boot changed during run')
        report.update(status=('OFFLINE_VERIFIED' if args.mode=='offline' else 'RLE_BOARD_COMPLETED'),
                      elapsed_s=elapsed,throughput_fps=report['frames']/elapsed)
    except BaseException as exc:
        held=bool(transport and transport.dma_may_be_active)
        report.update(status='FAIL_HOLD' if held else 'FAILED',error=str(exc),error_type=type(exc).__name__)
        if transport and not held and transport.last_rx_bytes:
            raw_path=args.output.with_suffix('.rx.bin')
            with raw_path.open('xb') as raw:
                raw.write(memoryview(transport.rx_buffer).cast('B')[:transport.last_rx_bytes])
            report['failed_rx_path']=str(raw_path)
            report['failed_dma_snapshot']=transport.snapshot()
    finally:
        if not held:
            if transport:transport.close()
            else:
                for buffer in allocated:buffer.freebuffer()
        report['buffers_held']=held
        with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
        print(json.dumps({k:v for k,v in report.items() if k!='frames_evidence'}),flush=True)
        if held:
            print('RLE_FAIL_HOLD: retaining DMA-owned CMA buffers; operator recovery required',flush=True)
            while True:time.sleep(1)
    return 0 if report['status'] in ('OFFLINE_VERIFIED','RLE_BOARD_COMPLETED') else 1


if __name__=='__main__':raise SystemExit(main())
