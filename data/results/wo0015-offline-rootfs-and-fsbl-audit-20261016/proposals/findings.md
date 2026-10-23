# WO-0015 offline B1/B2 proposal findings

**Disposition: `NOT_APPROVED_DO_NOT_EXECUTE`.** This is offline design evidence only. No real `/proc` or `/boot` path was opened, no SSH or remote backend was invoked, and no board, register, Overlay, DMA, or restricted-raw operation was performed. No old finalizer was run. Existing historical files were read only and left untouched.

## Findings

1. **B1 now fails closed on boot identity before collecting the other five files.** The tested capture core at `remote_template_v2.py:109` reads `/proc/sys/kernel/random/boot_id` first and checks it against `822a9c29-37c0-438c-81c4-da998d57073b`. A mismatch returns `BOOT_MISMATCH` before any of the other five paths are requested. The wrapper in `frozen-remote-b1.NOT_APPROVED.txt` turns a non-pass status into `SystemExit(3)`; its hardcoded approval marker currently exits with code 2 before any capture call. After that gate, the wrapper also refuses effective UID zero with exit code 4. On the matching path, it reads exactly the five allowlisted paths in order, rereads boot ID, and fails if the raw boot-ID bytes changed. All file reads use the fixed allowlist, bounded reads, `O_RDONLY | O_NOFOLLOW | O_CLOEXEC`, and ordinary process credentials. `EACCES` is terminal; there is no elevation, retry, or alternate path.

2. **B2 pins the directory and file descriptors and verifies the target filesystem before and after reading.** `remote_template_v2.py:151-209` checks the expected boot ID before opening `/boot`; uses `os.open("/boot", O_RDONLY | O_DIRECTORY | O_NOFOLLOW)` followed by `os.open("BOOT.BIN", O_RDONLY | O_NOFOLLOW, dir_fd=dir_fd)`; and requires `ST_RDONLY` from `fstatvfs` before and after the read. It compares exactly `(st_dev, st_ino, st_size, st_mtime_ns)` before/after and now also rejects `len(data) != before.st_size` as `CONTENT_SIZE_MISMATCH`. The already-read bytes may be retained in a failed result, but are never retried. A pre-read writable result stops before the first content read. Both descriptors are closed on all paths; close failure is terminal.

3. **The frozen drafts are textual, self-contained source bundles with a disabled execution gate.** Each `frozen-remote-b*.NOT_APPROVED.txt` embeds the exact tested `remote_template_v2.py` source followed by an explicit wrapper. The wrappers have no SSH client or transport command. Their `EXECUTION_STATUS` comparison is intentionally false in the frozen source, so direct execution exits nonzero before importing the real OS adapter or reading a path. If the marker were separately changed after review, the wrapper refuses effective UID zero; B1/B2 failures still raise nonzero `SystemExit`; a successful result is emitted as one JSON line only. B1 samples and B2 file bytes are base64 encoded; B2 also includes size and SHA-256. Tests parse the text as Python AST and verify exact core-source embedding and SHA-256; they do not execute it.

4. **The proposed known-hosts binding is an unresolved blocker.** The static option data names `C:/Users/POP/.ssh/wo0015-known_hosts` with `StrictHostKeyChecking=yes`, alongside `-T`, `ControlMaster=no`, `ClearAllForwardings=yes`, and remote Python flags `-I -S -B`. The file's existence, contents, and relationship to the historical host-pin evidence were not verified. No host-key file or global SSH configuration was created or edited. Do not proceed while the fixed known-hosts path remains unverified/unbound; automatic trust creation (`accept-new`/`ssh-keyscan`) is excluded.

5. **The read-only claim remains bounded to the opened target and check points.** `ST_RDONLY` supports `目标文件/挂载点只读` at the two B2 checks. It cannot prove `整张 SD 卡零写入`; SSH may create logs or cause other system activity. If whole-card zero-write is required, leave the card offline and do not use SSH.

## Historical comparison

The read-only v1 proposal `../../wo0015-boot-provenance-20261016/os_file_proposal.py:44-53` collected boot ID and then either the other five metadata files or mountinfo plus BOOT.BIN; B2's old mount-topology check is visible at lines 49-52. Its `PinnedReadOnlyFiles` implementation at lines 96-115 used `O_PATH` and reopened `/proc/self/fd/<fd>` with `O_NOATIME`, checking the read-only flag on the pinned and reopened descriptors. The v1 command document had a `sudo -n` wrapper at `../../wo0015-boot-provenance-20261016/command-audit-and-whitelist.md:43` and described that wrapper at line 56. This v2 proposal rejects elevation and instead uses the requested directory-relative `openat` flow and pre/post VFS/stat checks. The old result directory remains unchanged.

## Offline validation evidence

The mock suite invokes `capture_b1` and `capture_b2` from the actual shared core with an injected in-memory OS API. It covers initial and final boot-ID mismatch/change, B1 fixed ordering, B1/B2 `EACCES`, symlink rejection, zero B2 content reads after a writable pre-check, post-read writable status, all four identity fields changing, exact open/openat and fstatvfs ordering, short reads despite unchanged metadata, explicit dependency requirements, frozen-wrapper AST, document policy, and SHA-256 manifests. No test calls the real OS API or any remote code.

The short-read regression was red before the `len(data) == before.st_size` guard and green after it:

- `logs/tdd-red-short-read.xml`: 1 test, 1 expected failure (`OFFLINE_MODEL_PASS` was returned for a short fixture).
- `logs/tdd-green-short-read.xml`: the short-read regression passed after the guard.
- `logs/tdd-green-short-read-and-frozen.xml`: 4 JUnit test entries, 0 failures, covering the short-read regression and frozen-draft static checks.
- Prior-stage `logs/task-d-unit-tests.xml`: **32 JUnit tests, 0 failures, 0 errors, 0 skipped**. This is the independent Task D count and does not include or add to historical counts such as 459/76/43.

Final command, run from this result directory:

```text
C:\fpga\material-counter\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider --confcutdir=. -q proposals/test_proposal_contract_v2.py --junitxml=proposals/logs/task-d-unit-tests.xml
```

SHA-256 manifests:

- `remote-template-sha256.txt`: `6b68476d78b254244eaaae579bed23dc4bceefa7f3b1b1e02e5b390afb85bd91`
- `remote-frozen-sha256.txt`: hashes for both frozen B1/B2 text drafts.

These checks support only the offline mock/static proposal. They do not establish behavior of Linux syscalls on a target, host-key validity, remote interpreter availability, board identity, filesystem topology, or permission to execute either stage.

## Manager transport review

The fixed candidate is `/usr/bin/python3 -I -S -B` (target identity/presence UNVERIFIED). `ControlPath=none` prevents reuse of a configured control socket; `ForwardAgent=no` and `ForwardX11=no` explicitly cover channels not disabled by ClearAllForwardings. No transport has been executed. The additional assertions were red before constants/documents were corrected; logs are in `../manager/transport-review-red.txt` and the final new-only suite.

## POSIX closeout

The current core has no custom reader entry: capture_b1(*, os) checks effective UID first and uses read_b1_file for all six paths and both boot checks. MAX_PROC_READ_BYTES=65536; overflow fails. capture_b2(*, os) uses nonblocking openat, a positive 32MiB size bound, S_ISREG and ST_RDONLY before any content, pinned directory/file device equality, and metadata-only root stat. boot_dev_differs_from_root is in CaptureResult and the frozen B2 JSON payload. Precondition failures close both fds without content reads.

Current proposal suite: 27 methods and 20 subtests; four-file suite: 50 methods and 20 subtests. Red/green evidence is in logs/posix-red.txt and logs/posix-green.txt. Frozen texts are statically parsed/hashed only and never executed. Source SHA-256: `6b68476d78b254244eaaae579bed23dc4bceefa7f3b1b1e02e5b390afb85bd91`. Prior log counts above are historical stage evidence.
