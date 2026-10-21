import dataclasses, json, os, sys, time
from pathlib import Path
ROOT=Path('/home/xilinx/board-runtime-currentboot-v2')
sys.path.insert(0,str(ROOT/'scripts/wo0016'))
import run_stream_counter as runner
attempt=Path('/var/lib/wo0016/attempts/6f13f0f0-4433-445f-a023-fa403685aeee/attempt-2')
captured={}
original_run=runner.DoubleBufferStream.run
async def observed_run(self, *args, **kwargs):
    value=await original_run(self,*args,**kwargs)
    captured['frames']=value
    return value
runner.DoubleBufferStream.run=observed_run
code=2
try:
    code=runner.main(sys.argv[1:])
finally:
    if 'frames' in captured:
        with (attempt/'frame-results.json').open('x') as stream:
            json.dump([dataclasses.asdict(frame) for frame in captured['frames']],stream)
            stream.flush()
            os.fsync(stream.fileno())
    with (attempt/'exit-status.json').open('x') as stream:
        json.dump({'exit_code':code,'end_monotonic':time.monotonic(),'frame_records':len(captured.get('frames',()))},stream)
        stream.flush()
        os.fsync(stream.fileno())
raise SystemExit(code)
