import threading
import time

from scripts.standalone import evidence


def test_report_export_is_nonblocking_and_copies_mutable_state(tmp_path, monkeypatch):
    from scripts.standalone.async_evidence import EvidenceExporter
    entered, release = threading.Event(), threading.Event()
    written=[]
    def slow(path, value):
        entered.set()
        assert release.wait(5)
        written.append(value)
    monkeypatch.setattr(evidence, 'write_evidence', slow)
    writer=EvidenceExporter()
    report={'samples':[{'frame':1}]}
    assert writer.submit(tmp_path/'one.json',report)
    assert entered.wait(2)
    report['samples'][0]['frame']=99
    assert writer.submit(tmp_path/'two.json',report)
    assert not writer.submit(tmp_path/'three.json',report)
    assert writer.status()['dropped']==1
    release.set()
    assert writer.close(5)
    assert written[0]['samples'][0]['frame']==1
    assert writer.status()['saved']==2


def test_export_failure_is_observable_without_exiting_thread(tmp_path,monkeypatch):
    from scripts.standalone.async_evidence import EvidenceExporter
    def fail(*args):raise OSError('SD write failed')
    monkeypatch.setattr(evidence,'write_evidence',fail)
    writer=EvidenceExporter()
    assert writer.submit(tmp_path/'one.json',{'frame':1})
    assert writer.close(5)
    status=writer.status()
    assert status['errors']==1 and 'SD write failed' in status['last_error']
    assert not writer.submit(tmp_path/'closed.json',{})
