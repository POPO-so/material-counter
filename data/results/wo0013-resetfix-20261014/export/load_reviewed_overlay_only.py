"""Prepared for separate human approval; never invoked by the diagnostic."""
import argparse
import datetime
import hashlib
import json
import os
import time
from pathlib import Path
import xml.etree.ElementTree as ET

EVENT_LOG=None

def event(stage,**fields):
    line=json.dumps(dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(),monotonic_s=time.monotonic(),stage=stage,**fields))
    if EVENT_LOG is not None:
        with EVENT_LOG.open('a',encoding='utf-8') as log:
            log.write(line+'\n'); log.flush(); os.fsync(log.fileno())
    print(line,flush=True)

def main():
    global EVENT_LOG
    parser=argparse.ArgumentParser()
    parser.add_argument('export_dir',type=Path)
    parser.add_argument('--load-reviewed-overlay',action='store_true')
    args=parser.parse_args()
    directory=args.export_dir.resolve()
    manifest=json.loads((directory/'manifest.json').read_text())
    for name in ['wo0013_phase_a.bit','wo0013_phase_a.hwh']:
        value=hashlib.sha256((directory/name).read_bytes()).hexdigest()
        if value!=manifest['artifacts'][name]['sha256']: raise RuntimeError('SHA256 mismatch '+name)
    doc=ET.parse(directory/'wo0013_phase_a.hwh')
    reset=next(m for m in doc.findall('.//MODULE') if m.attrib.get('INSTANCE')=='reset_50m')
    params={p.attrib['NAME']:p.attrib['VALUE'] for p in reset.findall('./PARAMETERS/PARAMETER')}
    aux=next(p for p in reset.findall('./PORTS/PORT') if p.attrib['NAME']=='aux_reset_in')
    if params['C_AUX_RESET_HIGH']!='1' or aux.attrib.get('POLARITY')!='ACTIVE_HIGH' or aux.attrib.get('SIGNAME')!='constant_low_dout':
        raise RuntimeError('Reviewed reset repair is absent')
    event('files_and_reset_metadata_verified')
    if not args.load_reviewed_overlay:
        event('offline_metadata_only_no_hardware_access'); return
    if os.geteuid()!=0: raise RuntimeError('Use user-authenticated privileged terminal')
    marker=directory/'load-only-attempt.json'
    with marker.open('x') as f: json.dump({'operation':'one_overlay_load_no_monitor_no_dma'},f)
    EVENT_LOG=directory/'load-only-events.jsonl'
    from pynq import Overlay
    event('overlay_download_begin')
    overlay=Overlay(str(directory/'wo0013_phase_a.bit'),download=True)
    event('overlay_download_returned',ip_names=sorted(overlay.ip_dict))
    event('STOP_BEFORE_MMIO_AND_DMA')

if __name__=='__main__': main()
