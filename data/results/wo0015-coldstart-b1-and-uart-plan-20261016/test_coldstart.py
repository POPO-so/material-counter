import errno
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

HERE = Path(__file__).resolve().parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def core():
    assert (HERE / 'coldstart_b1.py').exists(), 'coldstart implementation missing'
    return load('coldstart_b1')

NEW = b'12345678-1234-1234-1234-123456789abc\n'
OLD = '822a9c29-37c0-438c-81c4-da998d57073b'
BOOT = '/proc/sys/kernel/random/boot_id'
OTHERS = ('/proc/sys/kernel/osrelease','/proc/version','/proc/cmdline','/proc/uptime','/proc/self/mountinfo')

class FakeOS:
    O_RDONLY = 0
    O_NOFOLLOW = 131072
    def __init__(self, first=NEW, last=None, root=False, fault=None, content=b'ok\n', chunk=4096):
        self.first, self.last = first, first if last is None else last
        self.root, self.fault, self.content, self.chunk = root, fault, content, chunk
        self.opens, self.closed, self.reads, self.fds = [], [], [], {}
    def geteuid(self): return 0 if self.root else 1000
    def open(self, path, flags):
        assert flags == self.O_RDONLY | self.O_NOFOLLOW
        self.opens.append(path)
        if self.fault == path: raise PermissionError(errno.EACCES, 'denied')
        fd = len(self.opens)
        data = (self.first if self.opens.count(BOOT)==1 else self.last) if path == BOOT else self.content
        self.fds[fd] = data
        return fd
    def fstat(self, fd): return SimpleNamespace(st_mode=0o100444)
    def read(self, fd, size):
        self.reads.append((fd,size))
        n = min(size,self.chunk)
        data,self.fds[fd] = self.fds[fd][:n],self.fds[fd][n:]
        return data
    def close(self,fd): self.closed.append(fd)

def collect(fake, **kw):
    return core().collect_b1_coldstart(fake, mode=kw.get('mode','coldstart_register_candidate'), retired_boot_ids=(OLD,), expected_boot_id=kw.get('expected'))

def test_root_opens_nothing():
    f=FakeOS(root=True); r=collect(f)
    assert not r['valid'] and f.opens == []

@pytest.mark.parametrize('first',[b'bad\n',NEW.upper(),NEW+b'\n',NEW+b' ',b'\xff',b'',OLD.encode()+b'\n'])
def test_bad_or_retired_boot_shortcircuits(first):
    f=FakeOS(first=first); r=collect(f)
    assert not r['valid'] and f.opens == [BOOT]

@pytest.mark.parametrize('kw',[{'mode':'approved'},{'expected':OLD}])
def test_explicit_mismatch_shortcircuits(kw):
    f=FakeOS(); r=collect(f,**kw)
    assert not r['valid'] and f.opens == [BOOT]

def test_boot_changed_invalidates():
    f=FakeOS(last=b'87654321-1234-1234-1234-123456789abc\n'); r=collect(f)
    assert not r['valid'] and r['reason']=='BOOT_ID_CHANGED'
    assert f.opens == [BOOT,*OTHERS,BOOT]

def test_candidate_stays_unapproved():
    f=FakeOS(chunk=1); r=collect(f)
    assert r['valid'] and r['status']=='CANDIDATE_BOOT_ID_UNAPPROVED'
    assert r['state']=='STOPPED_CANDIDATE_BOOT_UNAPPROVED'
    assert r['b2_executed'] is False and r['afi_authorized'] is False
    assert r['boot_id_first']==r['boot_id_last']==NEW.decode().rstrip('\n')
    assert f.opens == [BOOT,*OTHERS,BOOT] and len(f.closed)==7

@pytest.mark.parametrize('path',OTHERS)
def test_eacces_stops_without_retry(path):
    f=FakeOS(fault=path);r=collect(f)
    assert not r['valid'] and f.opens == [BOOT,*OTHERS[:OTHERS.index(path)+1]]
    assert len(f.closed)==len(f.opens)-1

@pytest.mark.parametrize('size,valid',[(65535,True),(65536,True),(65537,False)])
def test_bounded_eof(size,valid):
    f=FakeOS(content=b'x'*size);r=collect(f)
    assert r['valid'] is valid
    if not valid: assert f.opens==[BOOT,OTHERS[0]]
    assert len(f.closed)==len(f.opens)

@pytest.mark.parametrize('fault',[OSError(errno.ELOOP,'symlink'),OSError(errno.EIO,'I/O'),InterruptedError('interrupted')])
def test_open_error_terminal(fault):
    f=FakeOS(); orig=f.open
    def fail(path,flags):
        if path==OTHERS[0]: f.opens.append(path); raise fault
        return orig(path,flags)
    f.open=fail;r=collect(f)
    assert not r['valid'] and f.opens == [BOOT,OTHERS[0]] and len(f.closed)==1

def test_read_error_closes_no_retry():
    f=FakeOS(); f.read=lambda *a: (_ for _ in ()).throw(OSError(errno.EIO,'read'))
    assert not collect(f)['valid'] and f.opens==[BOOT] and f.closed==[1]

@pytest.mark.parametrize('text,present,ro',[
    ('1 0 8:1 / / rw - ext4 /dev/root rw\n',False,False),
    ('2 1 8:1 / /boot rw,relatime - vfat /dev/mmcblk0p1 rw\n',True,False),
    ('2 1 8:1 / /boot ro,relatime - vfat /dev/mmcblk0p1 rw\n',True,True),
    ('2 1 8:1 / /boot ro - vfat /dev/mmcblk0p1 ro\n3 1 8:2 / /boot rw - ext4 /dev/x rw\n',True,False)])
def test_mountinfo(text,present,ro):
    assert (HERE/'host_mountinfo.py').exists(), 'mountinfo implementation missing'
    r=load('host_mountinfo').parse_boot_mountinfo(text)
    assert r['boot_present'] is present and r['readonly_precondition_observed'] is ro
    assert r['b2_authorized'] is False

def test_malformed_mountinfo_fails_closed():
    assert (HERE/'host_mountinfo.py').exists()
    r=load('host_mountinfo').parse_boot_mountinfo('broken\n')
    assert not r['readonly_precondition_observed'] and r['parse_errors']

def test_retired_iterator_cannot_bypass_exclusion():
    f=FakeOS(first=OLD.encode()+b'\n')
    r=core().collect_b1_coldstart(f, mode='coldstart_register_candidate', retired_boot_ids=iter((OLD,)))
    assert not r['valid'] and f.opens==[BOOT]

def test_euid_error_invalid_zero_opens():
    f=FakeOS()
    f.geteuid=lambda: (_ for _ in ()).throw(OSError('euid unavailable'))
    r=collect(f)
    assert not r['valid'] and not f.opens

def test_euid_first_operation():
    f=FakeOS(); calls=[]; original=f.open
    f.geteuid=lambda: calls.append('geteuid') or 1000
    f.open=lambda *args: (calls.append('open'),original(*args))[1]
    assert collect(f)['valid'] and calls[0]=='geteuid' and calls.count('geteuid')==1

def test_normalized_equal_but_raw_boot_changed_invalid():
    f=FakeOS(last=NEW[:-1]);r=collect(f)
    assert not r['valid'] and r['reason']=='BOOT_ID_CHANGED'

def test_nonregular_and_stat_error_close():
    for statfn in [lambda fd: SimpleNamespace(st_mode=0o020444),
                   lambda fd: (_ for _ in ()).throw(OSError('fstat failed'))]:
        f=FakeOS();f.fstat=statfn;r=collect(f)
        assert not r['valid'] and f.opens==[BOOT] and f.closed==[1]

def test_mount_type_and_escaped_unrelated_path():
    r=load('host_mountinfo').parse_boot_mountinfo('2 1 8:1 / /boot ro - vfat /dev/x rw\n3 1 8:2 / /other\\040mount rw - ext4 /dev/y rw\n')
    assert r['mounts'][0]['filesystem']=='vfat' and r['readonly_precondition_observed']
