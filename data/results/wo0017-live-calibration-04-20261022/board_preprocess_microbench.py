"""Offline fixed-geometry sampling microbenchmark; no camera or MMIO."""
import json
import time
import numpy as np
import cv2

frame = np.random.RandomState(17).randint(0,256,(400,640,3),dtype=np.uint8)
view = frame[:,:,0]
gray = np.empty((240,320),np.uint8)
mask = np.empty_like(gray)
ys = (np.arange(240)*400//240)[:,None]
xs = (np.arange(320)*2)[None,:]
indices = (ys*640+xs)*3
expected = cv2.resize(view,(320,240),interpolation=cv2.INTER_NEAREST)

def copy3():
    np.copyto(gray[0::3],view[0::5,::2])
    np.copyto(gray[1::3],view[1::5,::2])
    np.copyto(gray[2::3],view[3::5,::2])

def gather():
    np.take(frame.reshape(-1),indices,out=gray,mode='clip')

def advanced():
    np.copyto(gray,view[ys,xs])

timings = {}
for name,run in [('copy3',copy3),('gather',gather),('advanced',advanced)]:
    samples=[]
    for _ in range(100):
        start=time.perf_counter()
        run()
        cv2.threshold(gray,128,255,cv2.THRESH_BINARY,dst=mask)
        samples.append((time.perf_counter()-start)*1000)
    assert np.array_equal(gray,expected)
    timings[name]={'median_ms':float(np.median(samples)), 'p95_ms':float(np.percentile(samples,95))}
print(json.dumps(timings,indent=2))
