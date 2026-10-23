"""WO-0015 B1/B2 source template; no entry point and no default executor.

The capture functions require an explicitly supplied OS-API dependency. The
offline tests inject fakes into these same functions. Nothing in this module
constructs the real ``os`` adapter, connects to SSH, or starts a remote process.
"""
import errno
import stat
from dataclasses import dataclass


VERSION = "wo0015-remote-template-v2"
EXECUTION_STATUS = "NOT_APPROVED_DO_NOT_EXECUTE"
EXPECTED_BOOT_ID = "822a9c29-37c0-438c-81c4-da998d57073b"
EXPECTED_BOOT_ID_BYTES = (EXPECTED_BOOT_ID + "\n").encode("ascii")
BOOT_ID = "/proc/sys/kernel/random/boot_id"
B1_OTHER_PATHS = (
    "/proc/sys/kernel/osrelease",
    "/proc/version",
    "/proc/cmdline",
    "/proc/uptime",
    "/proc/self/mountinfo",
)
B1_PATHS = (BOOT_ID, *B1_OTHER_PATHS)
B2_DIRECTORY = "/boot"
B2_FILENAME = "BOOT.BIN"
STAT_FIELDS = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
MAX_PROC_READ_BYTES = 65536
MAX_BOOT_BIN_BYTES = 32 * 1024 * 1024

# Option data only. No code below turns this into an SSH invocation.
SSH_OPTIONS = (
    "-T",
    "-o ControlMaster=no",
    "-o ControlPath=none",
    "-o ClearAllForwardings=yes",
    "-o ForwardAgent=no",
    "-o ForwardX11=no",
    "-o StrictHostKeyChecking=yes",
    "-o UserKnownHostsFile=C:/Users/POP/.ssh/wo0015-known_hosts",
)
KNOWN_HOSTS_OPTION = SSH_OPTIONS[-1]
REMOTE_PYTHON_FLAGS = ("-I", "-S", "-B")
REMOTE_PYTHON_EXECUTABLE = "/usr/bin/python3"


@dataclass(frozen=True)
class CaptureResult:
    status: str
    samples: tuple = ()
    data: bytes = b""
    reason: str = ""
    afi_authorized: bool = False
    boot_dev_differs_from_root: object = None


class _StopCapture(Exception):
    def __init__(self, status, reason, data=b""):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.data = data


def _failure_status(exc):
    if isinstance(exc, PermissionError) or getattr(exc, "errno", None) == errno.EACCES:
        return "EACCES"
    if getattr(exc, "errno", None) == errno.ELOOP:
        return "SYMLINK"
    return "READ_FAILED"


def _read_bounded(os, fd, max_bytes):
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("invalid size limit")
    chunks = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, max_bytes + 1 - total))
        if not chunk:
            return b"".join(chunks)
        if not isinstance(chunk, bytes):
            raise ValueError("read API must return bytes")
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("source exceeds fixed size limit")


def read_b1_file(path, *, os, max_bytes=MAX_PROC_READ_BYTES):
    """Read one exact B1 allowlisted regular file through injected OS APIs."""
    if path not in B1_PATHS:
        raise ValueError("path outside B1 allowlist")
    if type(max_bytes) is not int or max_bytes <= 0 or max_bytes > MAX_PROC_READ_BYTES:
        raise ValueError("invalid B1 size limit")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("B1 source is not a regular file")
        return _read_bounded(os, fd, max_bytes)
    finally:
        os.close(fd)


def capture_b1(*, os):
    """Capture B1 metadata through the bounded, no-follow helper only."""
    try:
        if os.geteuid() == 0:
            return CaptureResult("ROOT_REFUSED", reason="B1 requires a non-root effective uid")
    except Exception as exc:
        return CaptureResult(_failure_status(exc), reason=type(exc).__name__)
    samples = []
    try:
        first_boot = read_b1_file(BOOT_ID, os=os, max_bytes=MAX_PROC_READ_BYTES)
        samples.append((BOOT_ID, first_boot))
    except Exception as exc:
        return CaptureResult(_failure_status(exc), tuple(samples), reason=type(exc).__name__)

    if first_boot.strip() != EXPECTED_BOOT_ID.encode("ascii"):
        return CaptureResult("BOOT_MISMATCH", tuple(samples), reason="other five B1 paths not read")

    for path in B1_OTHER_PATHS:
        try:
            raw = read_b1_file(path, os=os, max_bytes=MAX_PROC_READ_BYTES)
            samples.append((path, raw))
        except Exception as exc:
            return CaptureResult(_failure_status(exc), tuple(samples), reason=type(exc).__name__)

    try:
        final_boot = read_b1_file(BOOT_ID, os=os, max_bytes=MAX_PROC_READ_BYTES)
        samples.append((BOOT_ID, final_boot))
    except Exception as exc:
        return CaptureResult(_failure_status(exc), tuple(samples), reason=type(exc).__name__)

    if final_boot != first_boot:
        return CaptureResult("BOOT_CHANGED", tuple(samples), reason="boot_id changed during B1")
    return CaptureResult("OFFLINE_MODEL_PASS", tuple(samples))


def _identity_tuple(info):
    return tuple(getattr(info, field) for field in STAT_FIELDS)


def capture_b2(*, os):
    """Shared B2 core using the injected OS API; callers must opt in explicitly."""
    try:
        if os.geteuid() == 0:
            return CaptureResult("ROOT_REFUSED", reason="B2 requires a non-root effective uid")
    except Exception as exc:
        return CaptureResult(_failure_status(exc), reason=type(exc).__name__)
    try:
        first_boot = read_b1_file(BOOT_ID, os=os, max_bytes=MAX_PROC_READ_BYTES)
    except Exception as exc:
        return CaptureResult(_failure_status(exc), reason=type(exc).__name__)
    if first_boot.strip() != EXPECTED_BOOT_ID.encode("ascii"):
        return CaptureResult("BOOT_MISMATCH", reason="/boot directory not opened")

    dir_fd = None
    fd = None
    result = None
    cleanup_error = None
    boot_dev_differs_from_root = None
    try:
        dir_fd = os.open("/boot", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open("BOOT.BIN", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)

        before_vfs = os.fstatvfs(fd)
        if not bool(before_vfs.f_flag & os.ST_RDONLY):
            raise _StopCapture("FILESYSTEM_WRITABLE", "pre-read ST_RDONLY absent")
        st_before = os.fstat(fd)
        if not stat.S_ISREG(st_before.st_mode):
            raise _StopCapture("OBJECT_TYPE", "BOOT.BIN is not a regular file")
        if not (0 < st_before.st_size <= MAX_BOOT_BIN_BYTES):
            raise _StopCapture("SIZE_LIMIT", "BOOT.BIN size outside fixed 32 MiB bound")
        dir_st = os.fstat(dir_fd)
        if dir_st.st_dev != st_before.st_dev:
            raise _StopCapture("DEVICE_MISMATCH", "BOOT.BIN and /boot directory are on different devices")
        root_st = os.stat("/", follow_symlinks=False)
        boot_dev_differs_from_root = dir_st.st_dev != root_st.st_dev

        data = _read_bounded(os, fd, MAX_BOOT_BIN_BYTES)

        after_vfs = os.fstatvfs(fd)
        if not bool(after_vfs.f_flag & os.ST_RDONLY):
            raise _StopCapture("FILESYSTEM_WRITABLE", "post-read ST_RDONLY absent", data)
        after = os.fstat(fd)
        if not stat.S_ISREG(after.st_mode):
            raise _StopCapture("OBJECT_TYPE", "BOOT.BIN type changed", data)
        if _identity_tuple(st_before) != _identity_tuple(after):
            raise _StopCapture("STAT_CHANGED", "pinned BOOT.BIN stat tuple changed", data)
        if len(data) != st_before.st_size:
            raise _StopCapture("CONTENT_SIZE_MISMATCH", "read byte count differs from pre-read st_size", data)

        final_boot = read_b1_file(BOOT_ID, os=os, max_bytes=MAX_PROC_READ_BYTES)
        if final_boot != first_boot:
            raise _StopCapture("BOOT_CHANGED", "boot_id changed during B2", data)
        result = CaptureResult("OFFLINE_MODEL_PASS", data=data, boot_dev_differs_from_root=boot_dev_differs_from_root)
    except _StopCapture as stop:
        result = CaptureResult(stop.status, data=stop.data, reason=stop.reason, boot_dev_differs_from_root=boot_dev_differs_from_root)
    except Exception as exc:
        result = CaptureResult(_failure_status(exc), reason=type(exc).__name__, boot_dev_differs_from_root=boot_dev_differs_from_root)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception as exc:
                cleanup_error = exc
        if dir_fd is not None:
            try:
                os.close(dir_fd)
            except Exception as exc:
                cleanup_error = cleanup_error or exc

    if cleanup_error is not None:
        return CaptureResult("CLOSE_FAILED", data=result.data if result else b"", reason=type(cleanup_error).__name__, boot_dev_differs_from_root=boot_dev_differs_from_root)
    return result
