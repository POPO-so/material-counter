# WO-0015 offline audit implementation plan

**Goal:** Close local-image software and boot-chain evidence gaps and harden unapproved B1/B2 proposals without board contact.

**Architecture:** New, isolated result files only. Independent rootfs, FSBL and proposal workers own separate subdirectories; Manager owns integrity snapshots, Golden/cache analysis and acceptance reports. Never import or execute image software or target binaries. Never open restricted-raw or REVISION.

**Tech Stack:** Host Python, read-only binary parsers, ARM static disassembly, mocked remote syscall tests.

1. Manager records pre-existing Git HEAD/status/diff and hashes five historical manifests (320 indexed files), four protected historical directories excluding restricted-raw/cache, and locked BIT/HWH. Hash image and candidate BOOT payloads read-only. Final checks compare identical paths and hashes.
2. rootfs/ owns bounded ext4 parser, synthetic tests and extracted allowlisted PYNQ/base sources. Verify UUID and source hashes. Record AFI width/reset/download call graph, base HP1 width, and runtime-equivalence limits in pynq-afi-and-base-hwh-audit.json.
3. fsbl/ owns ARM disassembly, bytecode decoder tests, complete decoded tables, DTB/U-Boot review and bounded archive filename search. Record machine addresses and all unresolved reachability limits in fsbl-disassembly-and-dtb-audit.json.
4. Manager manager/ owns Golden word/parity analysis and cache branch evidence using the actual historical script and the extracted image sources. Validate first word, full byte distribution and distinct synthetic counterexample; neither synthetic buffer is historical RX.
5. proposals/ owns dependency-injected, mock-only B1/B2 templates and tests: first-read boot mismatch short circuit; no sudo; pinned openat plus pre/post ST_RDONLY and identity checks; fixed SSH/Python isolation options; separate target-read-only and whole-card write boundaries. Two top-level v2 documents remain NOT_APPROVED_DO_NOT_EXECUTE.
6. Run each new targeted suite and a combined new-only suite, preserving distinct red/green and JUnit logs. Review actual additions, source evidence and JSON consistency. Generate verification-report.md and review-brief-v2.md; recheck integrity and preserve STOPPED_PRECONDITION_UNVERIFIED. No commit, merge, board command or approval inferred.
