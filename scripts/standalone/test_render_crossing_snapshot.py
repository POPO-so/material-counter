import json
import cv2
import numpy as np
import pytest
from scripts.standalone.crossing_snapshots import CrossingSnapshots
from scripts.standalone.render_crossing_snapshot import render_event
from scripts.standalone.test_crossing_snapshots import counter, observe


def read_image(path, flags):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), flags)


def test_render_compact_validates_sources_and_reconstructs_pixels(tmp_path):
    root=tmp_path/'captures'
    cap=CrossingSnapshots(root,identity={},storage_mode='compact',context_frames=0,min_free_bytes=0)
    gray,_=observe(cap,counter(),0,315)
    assert cap.close(timeout=10)
    event=next(root.glob('event-*/event.json'))
    out=tmp_path/'rendered'
    result=render_event(event,out)
    binary=read_image(out/'frame-0000000000.binary.png',0)
    assert np.count_nonzero(binary)==144
    assert np.array_equal(read_image(out/'frame-0000000000.gray.png',0),gray)
    assert (out/'frame-0000000000.annotated.png').is_file()
    assert result['frames']==1
    with pytest.raises(FileExistsError): render_event(event,out)
    record=json.loads(event.read_text())
    missing_local=next(iter(record['artifacts']))
    (event.parent/missing_local).unlink()
    assert render_event(event,tmp_path/'shared-fallback-render')['frames']==1

def test_render_refuses_incomplete_reference(tmp_path):
    root=tmp_path/'captures'
    cap=CrossingSnapshots(root,identity={},storage_mode='compact',context_frames=0,min_free_bytes=0)
    observe(cap,counter(),0,315)
    assert cap.close(timeout=10)
    event=next(root.glob('event-*/event.json'))
    record=json.loads(event.read_text())
    name=next(iter(record['shared_frames']))
    record['shared_frames'][name]='frames/e0/missing.png'
    (event.parent/name).unlink()
    event.write_text(json.dumps(record))
    with pytest.raises((ValueError,OSError)): render_event(event,tmp_path/'out')


def test_copied_event_directory_renders_without_shared_tree_and_checks_local_artifacts(tmp_path):
    import shutil
    root=tmp_path/'captures'
    cap=CrossingSnapshots(root,identity={},storage_mode='compact',context_frames=0,min_free_bytes=0)
    observe(cap,counter(),0,315)
    assert cap.close(timeout=10)
    source=next(root.glob('event-*'))
    portable=tmp_path/'portable-root'/'event-copy'
    portable.parent.mkdir()
    shutil.copytree(source,portable)
    assert not (portable.parent/'frames').exists()
    for name in json.loads((portable/'event.json').read_text())['artifacts']:
        assert not portable.joinpath(name).samefile(source/name)
    result=render_event(portable/'event.json',tmp_path/'portable-render')
    assert result['frames']==1

    record=json.loads((portable/'event.json').read_text())
    artifact=next(iter(record['artifacts']))
    (portable/artifact).unlink()
    with pytest.raises((ValueError,OSError)):
        render_event(portable/'event.json',tmp_path/'missing-render')


def test_copied_event_rejects_tampered_local_artifact(tmp_path):
    import shutil
    root=tmp_path/'captures'
    cap=CrossingSnapshots(root,identity={},storage_mode='compact',context_frames=0,min_free_bytes=0)
    observe(cap,counter(),0,315)
    assert cap.close(timeout=10)
    source=next(root.glob('event-*'))
    portable=tmp_path/'portable-root'/'event-copy'
    portable.parent.mkdir()
    shutil.copytree(source,portable)
    name=next(iter(json.loads((portable/'event.json').read_text())['artifacts']))
    (portable/name).write_bytes(b'tampered')
    with pytest.raises(ValueError):
        render_event(portable/'event.json',tmp_path/'tamper-render')
