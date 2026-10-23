# WO-0015 POSIX offline closeout plan

**Goal:** Harden the existing unapproved B1/B2 source and archive its offline verification while preserving STOPPED_PRECONDITION_UNVERIFIED.

**Architecture:** Worker owns proposal source/fakes/tests/frozen text; Manager owns documents, result indexing and acceptance. Reuse existing rootfs and FSBL evidence. No board/SSH/syscall backend execution, image hashing/extraction or 21-table replay.

1. Add fake-OS boundary tests and retain expected red logs: B1 root/overflow, B2 nonregular/zero/oversize/cross-device/metadata.
2. Implement a shared restricted reader with a 65536B acceptance bound; B1 checks euid before opening anything. B2 nonblocking open, regular-file/positive 32MiB size/device gates before content, root-device metadata, and existing post-read checks.
3. Freeze text-only wrappers and hashes from the tested core; no wrapper execution. Update whitelist/approval wording and metadata-only root stat permission.
4. Replace only the two real FSBL catalog replay tests with cached-result assertions to honor this closeout's no-reparse instruction. Keep decoder synthetic tests and the existing small DTB test.
5. Execute pytest with exactly four explicit host test filenames; no directory or wildcard collection. Record updated method/subtest counts separately from all historical results.
6. Independent review; run manager/integrity.py after without touching restricted-raw; synchronize reports/summary and rebuild the result-directory SHA-256 index excluding itself and __pycache__. Leave all Git changes uncommitted and unmerged; wait for the user's later decision.
