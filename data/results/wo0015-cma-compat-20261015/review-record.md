# CMA compatibility review

A read-only Worker inspected the existing test interfaces and reviewed the actual CMA resolver, runtime integration, post-allocation gate and new regression tests. The Manager retained design decisions and acceptance.

Initial review found no blocking flaw. The suggestion to cross-check exact CMA metadata against kernel/reserved/peripheral iomem entries was adopted: two tests were observed failing before the shared consistency helper was added. The Worker confirmed the fix on a second review. Source defaults versus the reauthorized package boot are now labelled at the start of README-board.md and beside its example.

Manager acceptance additionally checks the original bundle against source AST definitions: existing preflight functions are preserved; only run_frames' post-allocation validation, the PYNQ physical-address accessor and runtime CMA admission integration change in board_once.py. Added preflight definitions are limited to CMA provenance, parsing and physical-buffer validation. The original packager verifies the frozen RTL/build manifests before generating a new artifact.

The Worker did not access the board or run tests. Test evidence is recorded separately. The current metadata snapshot remains insufficient for runtime admission; a tested fallback is not evidence that its conditions hold on this board.
