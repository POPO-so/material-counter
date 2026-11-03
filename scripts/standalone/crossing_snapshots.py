"""Bounded, asynchronous evidence for visible boxes and counted crossings.

This module owns ordinary RAM and files only; it never changes counting state.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict, fields
from functools import lru_cache
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import queue
import shutil
import threading
import time

import cv2
import numpy as np


@lru_cache(maxsize=16)
def _parameter_names(counter_type):
    names = set()
    for cls in counter_type.__mro__:
        names.update(inspect.signature(cls.__init__).parameters)
    return tuple(sorted(names))


def _parameters(counter):
    values = {}
    for name in _parameter_names(type(counter)):
        if hasattr(counter, name):
            value = getattr(counter, name)
            if isinstance(value, (str, int, float, bool, type(None))):
                values[name] = value
    return values


def _detection_metadata(detection, converted):
    key = id(detection)
    cached = converted.get(key)
    if cached is not None and cached[0] is detection:
        return deepcopy(cached[1])
    value = asdict(detection)
    converted[key] = (detection, value)
    return value


class CrossingSnapshots:
    def __init__(self, root, *, identity, context_frames=2, queue_size=8,
                 max_events=1000, max_bytes=512*1024**2, min_free_bytes=256*1024**2,
                 storage_mode='full', density_threshold=10, slow_frame_ms=20.0,
                 trigger_min_interval_ns=1_000_000_000):
        if any(type(v) is not int or v < 1 for v in (queue_size, max_events, max_bytes)):
            raise ValueError('positive queue and storage limits required')
        if type(context_frames) is not int or not 0 <= context_frames <= 5:
            raise ValueError('context_frames must be in 0..5')
        if type(min_free_bytes) is not int or min_free_bytes < 0:
            raise ValueError('min_free_bytes must be nonnegative')
        if storage_mode not in ('full', 'compact'):
            raise ValueError("storage_mode must be 'full' or 'compact'")
        if type(density_threshold) is not int or density_threshold < 1:
            raise ValueError('density_threshold must be a positive integer')
        if type(slow_frame_ms) is not float or not np.isfinite(slow_frame_ms) or slow_frame_ms <= 0:
            raise ValueError('slow_frame_ms must be a positive finite float')
        if type(trigger_min_interval_ns) is not int or trigger_min_interval_ns < 0:
            raise ValueError('trigger_min_interval_ns must be a nonnegative integer')
        self.storage_mode = storage_mode
        self.density_threshold = density_threshold
        self.slow_frame_ms = slow_frame_ms
        self.trigger_min_interval_ns = trigger_min_interval_ns
        self._shared_frames = {}  # (epoch, frame_id) -> paths and digests only
        self.root = Path(root)
        self.identity = dict(identity)
        self.context_frames = context_frames
        self.max_events, self.max_bytes, self.min_free_bytes = max_events, max_bytes, min_free_bytes
        self._history = deque(maxlen=context_frames)
        self._pending = []
        self._previous = {}
        self._touched = set()
        self._epoch = None
        self._last_rejected_ns = None
        self._last_noncrossing_ns = None
        self._sequence = 0
        self._queue = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._disabled = threading.Event()
        self._lock = threading.Lock()
        self._stats = dict(observed=0, triggered=0, saved=0, bytes_written=0,
                           dropped_queue=0, dropped_storage=0, write_errors=0,
                           capture_errors=0, dropped_disabled=0, last_error=None)
        self._thread = threading.Thread(target=self._worker, name='crossing-snapshots', daemon=True)
        self._thread.start()

    def _update(self, **values):
        with self._lock:
            for key, value in values.items():
                if key == 'last_error':
                    self._stats[key] = value
                else:
                    self._stats[key] += value

    def status(self):
        with self._lock:
            return dict(self._stats, enabled=True, accepting=not self._stop.is_set() and not self._disabled.is_set(),
                        root=str(self.root), pending=len(self._pending),
                        queue_depth=self._queue.qsize(), writer_alive=self._thread.is_alive(),
                        max_events=self.max_events, max_bytes=self.max_bytes, storage_mode=self.storage_mode,
                        min_free_bytes=self.min_free_bytes, context_frames=self.context_frames,
                        density_threshold=self.density_threshold,
                        slow_frame_ms=self.slow_frame_ms)

    def observe(self, gray, packet, result, counter, *, trajectory_epoch=0,
                capture_monotonic_ns, camera_sequence, paused, commands, timings=None):
        """Copy only bounded RAM; all image encoding and filesystem I/O is deferred."""
        if self._stop.is_set() or self._disabled.is_set():
            return
        try:
            self._observe(gray, packet, result, counter, trajectory_epoch,
                          capture_monotonic_ns, camera_sequence, paused, commands, timings)
        except Exception as error:
            self._update(capture_errors=1, last_error=repr(error))

    def _observe(self, gray, packet, result, counter, epoch, capture_ns, sequence, paused, commands, timings):
        self._update(observed=1)
        if self._epoch != epoch:
            self._flush('trajectory_reset')
            self._history.clear()
            self._previous.clear()
            self._touched.clear()
            self._last_rejected_ns = None
            self._epoch = epoch
        if result is None or not packet.valid:
            # Never label a rejected RLE packet as an ordinary binary frame.
            self._flush('invalid_packet')
            self._history.clear()
            return
        axis = counter.count_axis
        line = counter.line_x if axis == 'x' else counter.line_y
        triggers, current = [], {}
        for track in result.tracks:
            if track['missed'] or track['last_frame'] != result.frame_id:
                continue
            key = '{}:{}'.format(epoch, track['track_id'])
            box = track['detection']
            current[key] = box
            if box[axis+'_min'] <= line <= box[axis+'_max'] and key not in self._touched:
                triggers.append(dict(kind='box_at_line', track_key=key))
                self._touched.add(key)
            old = self._previous.get(key)
            if old is None and track['streak'] == 1 and not track['armed']:
                triggers.append(dict(kind='new_track_after_line', track_key=key))
            if old is not None:
                before, after = old['centroid_'+axis]-line, box['centroid_'+axis]-line
                if before*after < 0 or (before != 0 and after == 0):
                    triggers.append(dict(kind='centroid_crossing', track_key=key))
        active_keys = {'{}:{}'.format(epoch, t['track_id']) for t in result.tracks if not t['finished']}
        self._touched.intersection_update(active_keys)
        # Keep missed tracks' last visible position for a gap crossing.
        self._previous = {key: value for key, value in self._previous.items() if key in active_keys}
        self._previous.update(current)
        for kind, events in (('counted', result.events), ('suppressed', result.suppressed_events)):
            triggers.extend(dict(kind=kind, track_key='{}:{}'.format(epoch, event.track_id),
                                 event=asdict(event)) for event in events)
        rejected = getattr(counter, 'rejected_detections', ())
        detection_metadata = {}
        near_rejected = [_detection_metadata(d, detection_metadata) for d in rejected
                         if getattr(d, axis+'_min') <= line <= getattr(d, axis+'_max')]
        if near_rejected and (self._last_rejected_ns is None or capture_ns-self._last_rejected_ns >= 1_000_000_000):
            triggers.append(dict(kind='rejected_at_line', detections=near_rejected))
            self._last_rejected_ns = capture_ns
        timings_ms = dict(timings or {}, **result.roi_subtimings_ms)
        # Non-crossing diagnostics: sustained high density, slow frames and
        # write backlog. These cover lag that has no line-crossing trigger.
        # Rate limited so sustained conditions produce one bounded group per
        # interval instead of flooding the bounded queue.
        region_ms = float(timings_ms.get('rle_regions_ms', 0.0) or 0.0)
        track_ms = float(timings_ms.get('track_ms', 0.0) or 0.0)
        queue_depth = self._queue.qsize()
        # Pending jobs normally wait for future context frames; that is not
        # writer congestion and must not trigger another snapshot by itself.
        backlog = queue_depth >= max(1, self._queue.maxsize - 1)
        split_deferred = getattr(counter, 'last_split_stats', {}).get('deferred', 0) > 0
        if (len(result.detections) >= self.density_threshold
                or region_ms + track_ms > self.slow_frame_ms
                or backlog or split_deferred):
            if (self._last_noncrossing_ns is None
                    or capture_ns - self._last_noncrossing_ns >= self.trigger_min_interval_ns):
                self._last_noncrossing_ns = capture_ns
                if len(result.detections) >= self.density_threshold:
                    kind = 'high_density'
                elif region_ms + track_ms > self.slow_frame_ms:
                    kind = 'slow_frame'
                elif split_deferred:
                    kind = 'split_budget'
                else:
                    kind = 'queue_backlog'
                triggers.append(dict(kind=kind, detections=len(result.detections),
                                     region_ms=region_ms, track_ms=track_ms,
                                     queue_depth=queue_depth, pending=len(self._pending)))
        metadata = dict(frame_id=result.frame_id, trajectory_epoch=epoch,
            capture_monotonic_ns=capture_ns, camera_sequence=sequence,
            observed_wall_time_ns=time.time_ns(), wall_clock_trusted=False,
            paused=paused, commands=list(commands), total_count=result.total_count,
            tracker_parameters=_parameters(counter),
            count_line=dict(axis=axis, position=line, direction=counter.direction),
            packet={f.name: getattr(packet, f.name) for f in fields(packet)
                    if f.name not in ('runs', 'row_projection', 'column_projection')},
            detections=[_detection_metadata(d, detection_metadata) for d in result.detections],
            raw_detections=[_detection_metadata(d, detection_metadata) for d in result.raw_detections],
            rejected_detections=[_detection_metadata(d, detection_metadata) for d in rejected],
            tracks=result.tracks, associations=list(getattr(counter, 'last_associations', ())),
            cluster_parent_diagnostics=deepcopy(
                getattr(counter, 'last_cluster_parent_diagnostics', ())
            ),
            split_stats=dict(getattr(counter, 'last_split_stats', {})),
            split_diagnostics=deepcopy(getattr(counter, 'last_split_diagnostics', ())),
            events=[asdict(e) for e in result.events],
            suppressed_events=[asdict(e) for e in result.suppressed_events],
            timings_ms=timings_ms)
        frame = dict(metadata=metadata, gray=np.array(gray, copy=True),
                     runs=packet.runs.copy(), rows=packet.row_projection.copy(),
                     columns=packet.column_projection.copy())
        remaining = []
        for job in self._pending:
            job['frames'].append(frame)
            job['remaining'] -= 1
            if job['remaining'] == 0:
                self._enqueue(job, 'complete')
            else:
                remaining.append(job)
        self._pending = remaining
        if triggers:
            self._sequence += 1
            self._update(triggered=1)
            job = dict(sequence=self._sequence, trigger_frame_id=result.frame_id,
                       trajectory_epoch=epoch, triggers=triggers,
                       frames=list(self._history)+[frame], remaining=self.context_frames)
            if self.context_frames:
                self._pending.append(job)
            else:
                self._enqueue(job, 'complete')
        self._history.append(frame)

    def _enqueue(self, job, reason):
        job['context_complete'] = job['remaining'] == 0
        job['context_end_reason'] = reason
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self._update(dropped_queue=1, last_error='snapshot queue full')

    def _flush(self, reason):
        for job in self._pending:
            self._enqueue(job, reason)
        self._pending.clear()

    def reset_context(self, reason='diagnostic_mode_changed'):
        """End pending context and isolate future observations from prior frames."""
        if self._stop.is_set():
            return
        self._flush(reason)
        self._history.clear()
        self._previous.clear()
        self._touched.clear()
        self._last_rejected_ns = None
        self._epoch = None

    def close(self, timeout=2):
        if not self._stop.is_set():
            self._flush('stopped')
            self._history.clear()
            self._stop.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _worker(self):
        ready = False
        try:
            self.root.mkdir(parents=True, exist_ok=False)
            ready = True
        except Exception as error:
            self._update(write_errors=1, last_error=repr(error))
            self._disabled.set()
        while not self._stop.is_set() or not self._queue.empty():
            try:
                job = self._queue.get(timeout=.05)
            except queue.Empty:
                continue
            try:
                if ready and not self._disabled.is_set():
                    self._write_job(job)
                else:
                    self._update(dropped_disabled=1)
            except Exception as error:
                self._update(write_errors=1, last_error=repr(error))
                # An SD error may leave a partial group. Stop further disk writes
                # for this session instead of accumulating unbounded partials.
                ready = False
                self._disabled.set()
            finally:
                self._queue.task_done()

    def _write_job(self, job):
        if self.storage_mode == 'compact':
            return self._write_compact_job(job)
        return self._write_full_job(job)

    def _write_full_job(self, job):
        stats = self.status()
        if (stats['saved'] >= self.max_events or
                shutil.disk_usage(self.root).free < self.min_free_bytes):
            self._update(dropped_storage=1, last_error='snapshot count limit or free disk reserve reached')
            self._disabled.set()
            return
        payload = {}
        for frame in job['frames']:
            meta = frame['metadata']
            prefix = 'frame-{:010d}'.format(meta['frame_id'])
            gray = frame['gray']
            binary = np.zeros_like(gray)
            for word in frame['runs']:
                value = int(word)
                binary[value >> 20, (value >> 10) & 1023:(value & 1023)+1] = 255
            annotated = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            line = meta['count_line']
            position = line['position']
            points = ((position, 0), (position, gray.shape[0]-1)) if line['axis']=='x' else ((0, position), (gray.shape[1]-1, position))
            cv2.line(annotated, *points, (0, 255, 255), 1)
            for box in meta['rejected_detections']:
                cv2.rectangle(annotated, (box['x_min'], box['y_min']), (box['x_max'], box['y_max']), (0, 0, 255), 1)
            for box in meta['detections']:
                cv2.rectangle(annotated, (box['x_min'], box['y_min']), (box['x_max'], box['y_max']), (0, 255, 0), 1)
            for track in meta['tracks']:
                box = track['detection']
                label = '{}:{} C{} A{} K{} M{}'.format(meta['trajectory_epoch'], track['track_id'],
                    int(track['confirmed']), int(track['armed']), int(track['counted']), track['missed'])
                cv2.putText(annotated, label, (box['x_min'], max(10, box['y_min']-4)),
                            cv2.FONT_HERSHEY_SIMPLEX, .32, (255, 160, 0), 1)
            cv2.putText(annotated, 'frame={} count={} paused={}'.format(meta['frame_id'], meta['total_count'], meta['paused']),
                        (5, 16), cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 255, 255), 1)
            for name, pixels in (('gray', gray), ('binary', binary), ('annotated', annotated)):
                ok, encoded = cv2.imencode('.png', pixels, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                if not ok:
                    raise OSError('snapshot PNG encoding failed')
                payload[prefix+'.'+name+'.png'] = encoded.tobytes()
            buffer = io.BytesIO()
            np.savez(buffer, runs=frame['runs'], row_projection=frame['rows'], column_projection=frame['columns'])
            payload[prefix+'.rle.npz'] = buffer.getvalue()
        record = dict(schema='material-counter.crossing-snapshot.v2', identity=self.identity,
            trigger_frame_id=job['trigger_frame_id'], trajectory_epoch=job['trajectory_epoch'],
            triggers=job['triggers'], context_complete=job['context_complete'],
            context_end_reason=job['context_end_reason'], requested_context_frames=self.context_frames,
            available_pre_frames=sum(f['metadata']['frame_id'] < job['trigger_frame_id'] for f in job['frames']),
            frames=[f['metadata'] for f in job['frames']],
            artifacts={name: dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()) for name, data in payload.items()})
        payload['event.json'] = json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        size = sum(map(len, payload.values()))
        if stats['bytes_written']+size > self.max_bytes or shutil.disk_usage(self.root).free-size < self.min_free_bytes:
            self._update(dropped_storage=1, last_error='snapshot byte limit or free disk reserve reached')
            self._disabled.set()
            return
        directory = self.root/('event-{:06d}-e{}-f{}'.format(job['sequence'], job['trajectory_epoch'], job['trigger_frame_id']))
        directory.mkdir()
        # event.json is the completion marker and is always written last.
        for name, data in payload.items():
            target = directory/name
            if name == 'event.json':
                target = directory/'event.json.partial'
            try:
                with target.open('xb') as stream:
                    stream.write(data)
            finally:
                if target.exists():
                    self._update(bytes_written=target.stat().st_size)
            if name == 'event.json':
                target.rename(directory/'event.json')
        self._update(saved=1)


    def _write_compact_job(self, job):
        stats = self.status()
        if (stats['saved'] >= self.max_events or
                shutil.disk_usage(self.root).free < self.min_free_bytes):
            self._update(dropped_storage=1, last_error='snapshot count limit or free disk reserve reached')
            self._disabled.set()
            return
        frame_payloads = {}
        artifact_info = {}
        new_bytes = 0
        for frame in job['frames']:
            meta = frame['metadata']
            key = (meta['trajectory_epoch'], meta['frame_id'])
            prefix = 'frame-{:010d}'.format(meta['frame_id'])
            names = (prefix+'.gray.png', prefix+'.rle.npz')
            if key not in self._shared_frames and key not in frame_payloads:
                ok, encoded = cv2.imencode('.png', frame['gray'], [cv2.IMWRITE_PNG_COMPRESSION, 1])
                if not ok:
                    raise OSError('snapshot PNG encoding failed')
                buffer = io.BytesIO()
                np.savez(buffer, runs=frame['runs'], row_projection=frame['rows'],
                         column_projection=frame['columns'])
                blobs = (encoded.tobytes(), buffer.getvalue())
                frame_payloads[key] = dict(zip(names, blobs))
                new_bytes += sum(map(len, blobs))
            cached = self._shared_frames.get(key)
            fresh = frame_payloads.get(key)
            for name in names:
                if cached:
                    digest=cached[name]['sha256']; size=cached[name]['bytes']
                    path=cached[name]['path']
                    if not path.is_file():
                        raise OSError('shared frame index path is missing')
                    stored=path.read_bytes()
                    if len(stored)!=size or hashlib.sha256(stored).hexdigest()!=digest:
                        raise OSError('shared frame index digest mismatch')
                else:
                    blob=fresh[name]; digest=hashlib.sha256(blob).hexdigest(); size=len(blob)
                artifact_info[name] = dict(bytes=size, sha256=digest)
        record = dict(schema='material-counter.crossing-snapshot.v2', identity=self.identity,
            storage_mode='compact', trigger_frame_id=job['trigger_frame_id'],
            trajectory_epoch=job['trajectory_epoch'], triggers=job['triggers'],
            context_complete=job['context_complete'], context_end_reason=job['context_end_reason'],
            requested_context_frames=self.context_frames,
            available_pre_frames=sum(f['metadata']['frame_id'] < job['trigger_frame_id'] for f in job['frames']),
            frames=[f['metadata'] for f in job['frames']], artifacts=artifact_info)
        shared = {}
        for frame in job['frames']:
            meta=frame['metadata']; key=(meta['trajectory_epoch'],meta['frame_id'])
            prefix='frame-{:010d}'.format(meta['frame_id'])
            for suffix in ('gray.png','rle.npz'):
                name=prefix+'.'+suffix
                shared[name]=(Path('frames')/('e{}'.format(meta['trajectory_epoch']))/name).as_posix()
        record['shared_frames'] = shared
        event_bytes = json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        required = new_bytes + len(event_bytes)
        if (stats['bytes_written']+required > self.max_bytes or
                shutil.disk_usage(self.root).free-required < self.min_free_bytes):
            self._update(dropped_storage=1, last_error='snapshot byte limit or free disk reserve reached')
            self._disabled.set()
            return
        # Publish shared frame files atomically before event completion metadata.
        for key, blobs in frame_payloads.items():
            epoch, frame_id = key
            shared_dir = self.root/'frames'/('e{}'.format(epoch))
            shared_dir.mkdir(parents=True, exist_ok=True)
            stored = {}
            for name, blob in blobs.items():
                target = shared_dir/name
                partial = target.with_name(target.name+'.partial')
                try:
                    with partial.open('xb') as stream:
                        stream.write(blob); stream.flush(); os.fsync(stream.fileno())
                    partial.replace(target)
                finally:
                    accounted=partial if partial.exists() else target
                    if accounted.is_file():
                        self._update(bytes_written=accounted.stat().st_size)
                digest=hashlib.sha256(blob).hexdigest()
                stored[name]=dict(path=target, bytes=len(blob), sha256=digest)
            self._shared_frames[key]=stored
        directory=self.root/('event-{:06d}-e{}-f{}'.format(job['sequence'],job['trajectory_epoch'],job['trigger_frame_id']))
        directory.mkdir()
        # Hard links preserve legacy artifact paths without duplicating image payloads.
        for name, rel in shared.items():
            source=self.root/Path(rel)
            os.link(source, directory/name)
        partial=directory/'event.json.partial'
        complete=directory/'event.json'
        try:
            with partial.open('xb') as stream:
                stream.write(event_bytes)
            partial.replace(complete)
        finally:
            accounted=partial if partial.exists() else complete
            if accounted.is_file():
                self._update(bytes_written=accounted.stat().st_size)
        self._update(saved=1)
