# WO-0015 B1/B2 command audit and file whitelist v2

> **NOT_APPROVED_DO_NOT_EXECUTE**  
> Offline proposal only. No board connection, SSH attempt, `/proc` read, `/boot` read, register access, Overlay load, or DMA operation was performed for this review.

This revision records the requested B1/B2 safety contract. It does not include a runnable SSH command, credentials, or a default executor. `proposals/remote_template_v2.py` contains source-reviewable capture logic with a required injected OS dependency and no entry point; offline tests pass only fake dependencies to those same functions. `proposals/frozen-remote-b1.NOT_APPROVED.txt` and `proposals/frozen-remote-b2.NOT_APPROVED.txt` are text-only frozen source drafts that embed that tested core and an explicit wrapper. Their `EXECUTION_STATUS` gate is still `NOT_APPROVED_DO_NOT_EXECUTE`; their text was statically parsed and hashed, never run. There is no transport launcher.

## B1 whitelist and fail-fast order

B1 first calls `os.geteuid()` inside the shared capture core. Effective UID zero is rejected before any file is opened. After that gate, the first file opened must be the boot ID:

`/proc/sys/kernel/random/boot_id`

The first bytes must match the historical authorization baseline `822a9c29-37c0-438c-81c4-da998d57073b`. If they do not match, the process exits immediately; it must not open any of the other five paths. If the boot ID matches, the only remaining B1 paths are:

| Order | Path | Limit | Rule |
|---|---|---:|---|
| 1 | `/proc/sys/kernel/random/boot_id` | 65536 B | First file read; expected boot required before any other path |
| 2 | `/proc/sys/kernel/osrelease` | 65536 B | One bounded file transaction |
| 3 | `/proc/version` | 65536 B | One bounded file transaction |
| 4 | `/proc/cmdline` | 65536 B | One bounded file transaction |
| 5 | `/proc/uptime` | 65536 B | One bounded file transaction |
| 6 | `/proc/self/mountinfo` | 65536 B | One bounded file transaction; descriptive evidence only |

All six paths, including both boot-ID checks, use the same restricted helper: `os.open(path, os.O_RDONLY | os.O_NOFOLLOW)` and `MAX_PROC_READ_BYTES = 65536`. The helper requires a regular file and bounded EOF-terminated content. Oversized content fails immediately; at the limit, at most a one-byte overflow probe distinguishes EOF from excess data, and excess data is never accepted as a sample. No custom reader bypasses the helper.

After those five transactions, the process must reread `boot_id` and stop if it differs from the first bytes. A read error, including `EACCES`, stops the process at that point. There is no retry, privilege change, alternative path, or fallback. These are six distinct files and at most seven file-open/read transactions, each of which may need bounded read syscalls and an EOF check.

The process must use ordinary-user permissions for every path. The frozen wrapper exits nonzero if effective UID is zero and must not invoke `sudo` or another elevation method. `EACCES` is a terminal rejection; it must not trigger a prompt, PAM authentication, `auth.log` activity caused by elevation, or a second command.

## B2 file and mount contract

B2 is a separately reviewed stage, after B1 evidence is assessed. A future B2 process must also check the same boot ID before opening `/boot` and recheck it after the file transaction. B2 adds only the regular file `/boot/BOOT.BIN`; it must not use `mountinfo` text as the sole read-only check.

The required directory-relative sequence is:

```python
dir_fd = os.open("/boot", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
fd = os.open("BOOT.BIN", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
```

`O_NONBLOCK` prevents an unexpected FIFO name from blocking at open before its type can be rejected. Before reading any BOOT.BIN content byte, complete all gates: `os.fstatvfs(fd)` must have `bool(f_flag & os.ST_RDONLY)` true; `st_before = os.fstat(fd)` must satisfy `stat.S_ISREG(st_before.st_mode)`; and `0 < st_before.st_size <= MAX_BOOT_BIN_BYTES`, where `MAX_BOOT_BIN_BYTES = 32 * 1024 * 1024` (32 MiB). Empty/oversized files, directories, FIFO, character/block devices and sockets are rejected with zero content reads and both descriptors closed.

Also before content reads, capture `dir_st = os.fstat(dir_fd)` and require `dir_st.st_dev == st_before.st_dev`. A cross-device file/mount substitution is rejected with zero content reads. The only additional root metadata query is `root_st = os.stat("/", follow_symlinks=False)`; record `boot_dev_differs_from_root = (dir_st.st_dev != root_st.st_dev)` in capture metadata and the frozen B2 JSON payload. This is a device-identity indicator; a false value alone does not exclude a same-device bind mount. No root-directory content is read.

Read the file through that pinned descriptor only after all gates. Immediately after content reads, require `ST_RDONLY` again via `os.fstatvfs(fd)` and capture `os.fstat(fd)` again. The tuples `(st_dev, st_ino, st_size, st_mtime_ns)` must match exactly, and content length must equal the pre-read `st_size`. Early EOF/short read fails closed; any already-read bytes may be retained as failed evidence, with no retry. Close both descriptors on every path.

Any symlink rejection, non-regular object, missing flag, writable filesystem, changed stat tuple, short read, changed boot ID, `EACCES`, or I/O error stops the stage. In particular, a writable `fstatvfs` result before the content read requires zero content reads. Do not retry, remount, mount, unmount, change flags, try another path, or escalate privileges. `O_NOATIME` is not a fallback for the `ST_RDONLY` requirement.

`ST_RDONLY` is the required kernel VFS assertion for the filesystem containing the opened target file at the two check points. This establishes **目标文件/挂载点只读** for the target and those check points. It does not establish **整张 SD 卡零写入**: an SSH login can still cause system logging or other activity. If whole-card zero-write is required, keep the card offline and do not use SSH.

## SSH and interpreter option whitelist

If a later, separately approved stage is ever prepared, its SSH option set must include all of the following:

| Option | Required value | Purpose |
|---|---|---|
| `-T` | enabled | No pseudo-terminal |
| `-o ControlMaster=no` | fixed | Do not create a control master |
| `-o ControlPath=none` | fixed | Do not reuse a configured control socket |
| `-o ClearAllForwardings=yes` | fixed | Clear configured port-forwarding requests |
| `-o ForwardAgent=no` | fixed | Disable agent forwarding explicitly |
| `-o ForwardX11=no` | fixed | Disable X11 forwarding explicitly |
| `-o StrictHostKeyChecking=yes` | fixed | Reject unknown or changed host keys |
| `-o UserKnownHostsFile=C:/Users/POP/.ssh/wo0015-known_hosts` | fixed absolute path | Use one explicitly named host-key file |

The existence and contents of the proposed host-key file, target identity, and current SSH reachability were not inspected or verified here. This work does not create or modify that file or any global SSH file. Historical host-pin evidence remains untouched and must be identified and reviewed separately; do not use `accept-new`, `ssh-keyscan`, or another automatic trust-creation path. A missing/untrusted key must fail closed; do not accept a key interactively or switch to another known-hosts file. No host, username, key, address, or connection command is supplied in this proposal.

The remote interpreter candidate is fixed as `/usr/bin/python3 -I -S -B`. Its presence and identity on a future target remain UNVERIFIED and must be reviewed before any later approval; no alternate path is permitted. No `sudo`, PTY, control-socket reuse, forwarding, retry, or fallback command is part of this proposal.

## Command disposition and evidence boundary

The earlier v1 command document included a noninteractive `sudo` wrapper. That v1 command is superseded for any future consideration and must not be copied into a new request. The old files remain unchanged as historical evidence. This v2 document describes policy and syscall order only; it does not implement or invoke them.

Offline tests use synthetic bytes and in-memory mock methods only. The frozen drafts receive static AST and SHA-256 checks; neither draft is executed. No test opens `/proc` or `/boot`, creates a real SSH process, calls a remote backend, or grants AFI/DMA authorization. Current status remains `STOPPED_PRECONDITION_UNVERIFIED` and `afi_authorized=false`.
