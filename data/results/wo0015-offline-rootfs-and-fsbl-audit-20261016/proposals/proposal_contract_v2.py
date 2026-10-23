"""Offline fake OS API for tests of remote_template_v2's injected core."""
import errno
import stat
from types import SimpleNamespace

import remote_template_v2 as template


class OfflineOSMock:
    """Memory-only stand-in for the exact os API used by the source template."""

    O_RDONLY = 0x01
    O_DIRECTORY = 0x02
    O_NOFOLLOW = 0x04
    O_CLOEXEC = 0x08
    O_NONBLOCK = 0x10
    ST_RDONLY = 0x01

    def __init__(
        self,
        *,
        boot_ids=None,
        fail_proc_path=None,
        directory_type="directory",
        file_type="regular",
        readonly=(True, True),
        stat_before=None,
        stat_after=None,
        fail_at=None,
        payload=b"synthetic BOOT.BIN fixture",
        file_size=None,
        directory_dev=1,
        file_dev=1,
        root_dev=1,
        euid=1000,
        proc_payloads=None,
    ):
        self.events = []
        self.boot_ids = list(boot_ids or [template.EXPECTED_BOOT_ID_BYTES])
        self.boot_id_index = 0
        self.fail_proc_path = fail_proc_path
        self.directory_type = directory_type
        self.file_type = file_type
        self.readonly = tuple(readonly)
        self.fail_at = fail_at
        self.payload = payload
        self.directory_dev = directory_dev
        self.file_dev = file_dev
        self.root_dev = root_dev
        self.euid = euid
        self.proc_payloads = dict(proc_payloads or {})
        self.stat_before = dict(zip(template.STAT_FIELDS, (file_dev, 2, len(payload) if file_size is None else file_size, 4))) if stat_before is None else dict(stat_before)
        self.stat_after = dict(self.stat_before if stat_after is None else stat_after)
        self._fd_counter = 100
        self._files = {}
        self._boot_vfs_count = 0
        self._boot_stat_count = 0
        self.boot_read_count = 0
        self.proc_read_count = 0

    def geteuid(self):
        self.events.append(("geteuid", self.euid))
        return self.euid

    @property
    def proc_open_paths(self):
        return [event[1] for event in self.events if event[0] == "open" and event[1] in template.B1_PATHS]

    def open(self, path, flags, *, dir_fd=None):
        self.events.append(("open", path, flags, dir_fd))
        if path in template.B1_PATHS:
            expected = self.O_RDONLY | self.O_NOFOLLOW
            if dir_fd is not None or flags != expected:
                raise AssertionError("B1 must use ordinary-user read-only no-follow open")
            if path == self.fail_proc_path:
                raise PermissionError(errno.EACCES, "synthetic EACCES")
            if path in self.proc_payloads:
                raw = self.proc_payloads[path]
            elif path == template.BOOT_ID:
                index = min(self.boot_id_index, len(self.boot_ids) - 1)
                raw = self.boot_ids[index]
                self.boot_id_index += 1
            else:
                raw = ("fixture:" + path).encode("ascii")
            return self._new_fd("proc", raw)

        if path == "/boot" and dir_fd is None:
            expected = self.O_RDONLY | self.O_DIRECTORY | self.O_NOFOLLOW
            if self.fail_at == "open_dir":
                raise PermissionError(errno.EACCES, "synthetic EACCES")
            if flags != expected:
                raise AssertionError("/boot must use O_RDONLY|O_DIRECTORY|O_NOFOLLOW")
            if self.directory_type == "symlink":
                raise OSError(errno.ELOOP, "synthetic directory symlink")
            if self.directory_type != "directory":
                raise OSError(errno.ENOTDIR, "synthetic non-directory")
            return "dirfd"

        if path == "BOOT.BIN" and dir_fd == "dirfd":
            expected = self.O_RDONLY | self.O_NOFOLLOW | self.O_NONBLOCK
            if self.fail_at == "openat":
                raise PermissionError(errno.EACCES, "synthetic EACCES")
            if flags != expected:
                raise AssertionError("BOOT.BIN must use dirfd O_RDONLY|O_NOFOLLOW|O_NONBLOCK")
            if self.file_type == "symlink":
                raise OSError(errno.ELOOP, "synthetic BOOT.BIN symlink")
            self._files["bootfd"] = {"kind": "boot", "data": self.payload, "pos": 0}
            return "bootfd"

        raise AssertionError("unexpected path or dir_fd")

    def fstat(self, fd):
        self.events.append(("fstat", fd))
        if fd == "dirfd":
            return SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o555,
                st_dev=self.directory_dev,
                st_ino=11,
                st_size=0,
                st_mtime_ns=3,
            )
        if fd == "bootfd":
            if self.fail_at == ("fstat_before" if self._boot_stat_count == 0 else "fstat_after"):
                raise PermissionError(errno.EACCES, "synthetic EACCES")
            fields = self.stat_before if self._boot_stat_count == 0 else self.stat_after
            self._boot_stat_count += 1
            return SimpleNamespace(
                st_mode={
                    "regular": stat.S_IFREG,
                    "fifo": stat.S_IFIFO,
                    "char": stat.S_IFCHR,
                    "block": stat.S_IFBLK,
                    "directory": stat.S_IFDIR,
                    "socket": stat.S_IFSOCK,
                }.get(self.file_type, stat.S_IFREG) | 0o444,
                st_dev=fields["st_dev"],
                st_ino=fields["st_ino"],
                st_size=fields["st_size"],
                st_mtime_ns=fields["st_mtime_ns"],
            )
        entry = self._files[fd]
        return SimpleNamespace(
            st_mode=stat.S_IFREG | 0o444,
            st_dev=1,
            st_ino=2,
            st_size=len(entry["data"]),
            st_mtime_ns=3,
        )

    def fstatvfs(self, fd):
        self.events.append(("fstatvfs", fd))
        if fd != "bootfd":
            raise AssertionError("fstatvfs must use BOOT.BIN fd")
        point = "fstatvfs_before" if self._boot_vfs_count == 0 else "fstatvfs_after"
        if self.fail_at == point:
            raise PermissionError(errno.EACCES, "synthetic EACCES")
        readonly = self.readonly[min(self._boot_vfs_count, len(self.readonly) - 1)]
        self._boot_vfs_count += 1
        return SimpleNamespace(f_flag=self.ST_RDONLY if readonly else 0)

    def stat(self, path, *, follow_symlinks=True):
        self.events.append(("stat", path, follow_symlinks))
        if path != "/" or follow_symlinks:
            raise AssertionError("only non-following root stat is permitted")
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o555, st_dev=self.root_dev)

    def read(self, fd, size):
        self.events.append(("read", fd, size))
        if fd == "bootfd":
            if self.fail_at == "read":
                raise PermissionError(errno.EACCES, "synthetic EACCES")
            self.boot_read_count += 1
        else:
            entry_for_kind = self._files[fd]
            if entry_for_kind["kind"] == "proc":
                self.proc_read_count += 1
        entry = self._files[fd]
        start = entry["pos"]
        end = min(start + size, len(entry["data"]))
        entry["pos"] = end
        return entry["data"][start:end]

    def close(self, fd):
        self.events.append(("close", fd))
        self._files.pop(fd, None)

    def _new_fd(self, kind, data):
        fd = "fd-" + str(self._fd_counter)
        self._fd_counter += 1
        self._files[fd] = {"kind": kind, "data": data, "pos": 0}
        return fd
