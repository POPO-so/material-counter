# WO-0015 boot provenance implementation plan

Goal: Establish only evidence-supported relationships between local boot images,
FSBL, PS initialization and the historical DMA session; retain STOPPED.

Architecture: Reuse previous indexes. Search bounded host directories for actual
boot artifacts, inspect containers/partition tables as data without mounting or
executing them, then compare with historical logs. Separate present disk bytes,
historical captured files, claimed boot files and actual execution evidence.

Tech stack: Python/PowerShell host file inspection, reviewed binary parsing,
official AMD format documentation only when a relevant binary requires it.

1. Read previous report/approval and preserve all previous results.
2. Worker discovery: bounded local file/archive search, source/metadata/hash and
   explicit search limitations; do not touch physical drives or removable media.
3. Worker init-chain: only missing external FSBL/boot/Overlay relationships and
   historical identity clues; reference existing generated-code evidence.
4. Manager statically parses any discovered relevant BOOT/SD image, validating
   lengths/checksums/partition boundaries; never executes target instructions.
5. Prepare exact future OS-file whitelist and frozen command payload offline;
   audit implicit side effects, read-only mount/atime, path types, credentials,
   authentication, output integrity and all failure exits. No live execution.
6. Verify new parsers/payload using offline fixtures where needed, preserve
   source hashes and old evidence, then deliver a provenance table, call chain,
   classifications, risks and a separately scoped authorization request.

All writes remain under this new result directory. No commits, merge, target
binary execution, unknown initialization script, SD write, MMIO or board access.
