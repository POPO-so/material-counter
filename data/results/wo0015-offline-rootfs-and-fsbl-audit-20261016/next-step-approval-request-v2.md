# WO-0015 proposed next-step approval request v2

> **NOT_APPROVED_DO_NOT_EXECUTE**  
> This is a review draft. It grants no board, SSH, `/proc`, `/boot`, register, Overlay, or DMA access.

The proposed sequence is one ordinary-user B1 metadata capture, followed by a separate review and a separate approval decision before any B2 `BOOT.BIN` capture. Neither stage is authorized by this document. The current project state remains `STOPPED_PRECONDITION_UNVERIFIED`; `afi_authorized=false`.

## Candidate B1 scope

If B1 is separately approved later, one remote process may read only these six distinct paths, in order: first `/proc/sys/kernel/random/boot_id`, then `/proc/sys/kernel/osrelease`, `/proc/version`, `/proc/cmdline`, `/proc/uptime`, and `/proc/self/mountinfo`, followed by one final `boot_id` read. Before opening any other path, the first boot ID must equal `822a9c29-37c0-438c-81c4-da998d57073b`. A mismatch exits immediately with no reads from the other five paths.

Before opening even the first boot ID, the shared B1 core must check `os.geteuid() != 0`; root is rejected with zero file opens. All six files, including initial/final boot ID, use the same helper and exact `os.O_RDONLY | os.O_NOFOLLOW` flags with `MAX_PROC_READ_BYTES = 65536`. EOF-terminated content is bounded; oversized input fails, with at most one overflow-probe byte beyond the acceptance limit. Any `EACCES` or other failure stops at the first error. There is no `sudo`, privilege prompt, retry, alternate path, custom-reader bypass, second interpreter, or fallback. B1 evidence would be reviewed before considering B2.

## Candidate B2 scope

B2 would require a separate approval after B1 review. A future B2 process would recheck the expected boot ID before and after its file transaction and add only `/boot/BOOT.BIN` content. It would open `/boot` with `O_RDONLY | O_DIRECTORY | O_NOFOLLOW`, then open `BOOT.BIN` relative to that pinned directory descriptor with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK`, so an unexpected FIFO cannot block at open before rejection.

Before any BOOT.BIN content read, it must require `bool(f_flag & os.ST_RDONLY)` from `os.fstatvfs(fd)`, a regular file (`stat.S_ISREG(st_before.st_mode)`), and `0 < st_before.st_size <= MAX_BOOT_BIN_BYTES`, with `MAX_BOOT_BIN_BYTES = 32 * 1024 * 1024`. It must compare `os.fstat(dir_fd).st_dev == st_before.st_dev`. The additional metadata-only `os.stat("/", follow_symlinks=False)` records `boot_dev_differs_from_root` in the B2 result to identify a differing root/boot device; false does not rule out a same-device bind mount. Any precondition failure closes both descriptors with zero content reads, including empty/oversized files, FIFO/device/directory/socket or cross-device substitution.

After content reads it must again require `ST_RDONLY`, identical `(st_dev, st_ino, st_size, st_mtime_ns)` values, and content length equal to pre-read size. A short read fails closed, may retain failed evidence already read, and is never retried. Boot change, `EACCES` or I/O errors also stop. It must never mount, remount, unmount, repair, elevate or try another device/path.

## Fixed transport and stop behavior

Any separately reviewed transport proposal would require `-T`, `-o ControlMaster=no`, `-o ControlPath=none`, `-o ClearAllForwardings=yes`, `-o ForwardAgent=no`, `-o ForwardX11=no`, `-o StrictHostKeyChecking=yes`, and the fixed `-o UserKnownHostsFile=C:/Users/POP/.ssh/wo0015-known_hosts`. Its interpreter candidate is fixed as `/usr/bin/python3 -I -S -B`; target presence and identity remain UNVERIFIED and no alternative path is allowed. ClearAllForwardings concerns port-forwarding requests; agent/X11 and control-socket reuse are disabled explicitly by their separate options. This draft contains no destination, credentials, runnable SSH command, or default SSH executor. Text-only frozen B1/B2 script drafts embed the tested source core and explicit wrappers, but their `NOT_APPROVED_DO_NOT_EXECUTE` gate remains set and they were not executed. The existence and contents of the proposed host-key file have not been checked. This work does not create or modify it or any global SSH file; historical host-pin evidence remains untouched and must be identified and reviewed separately. Do not use `accept-new`, `ssh-keyscan`, or other automatic trust creation. Missing or changed key material would cause a hard stop.

No retries or fallback modes are proposed. A permission failure does not authorize elevation. A changed boot ID does not carry the previous approval forward. This draft does not authorize a connection even if the above conditions can be prepared.

## Write boundary

`ST_RDONLY` checks support **目标文件/挂载点只读** at the required check points. They do not prove **整张 SD 卡零写入**. SSH login can still produce logs or other system activity. If the requirement is zero writes to the entire SD card, keep the card offline and do not run B1 or B2 over SSH.

The offline contract tests validate only synthetic in-memory mocks and static proposal text, AST, and hashes. They are not a board test, an SSH test, a file-system syscall test, or an authorization decision. A future request would need to identify the exact target and approved stage after this draft is reviewed.
