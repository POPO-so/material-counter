# diagnostic50 authentication and supervision Implementation Plan

**Goal:** Offline preparation of separately authorized stages with no credential capture and bounded supervision.

**Architecture:** Native SSH/SFTP and interactive sudo are manual operations in a private terminal. A board-local supervisor starts only after authentication, owns a persistent child, and uses an independent deadline process. Fixed guard records and successful Overlay receipts span stage invocations.

**Tech Stack:** Python standard library, multiprocessing spawn, pytest, OpenSSH.

1. Add failing authentication/entry tests; implement a command-only manual authentication guide and refuse automated network execution.
2. Add failing independent-stage tests; implement explicit stage authorization, fixed claims and Overlay identity receipts in core/child.
3. Add blocking-I/O regressions; implement an independent deadline watchdog before logging/dispatch and enforce absolute command expiry at the board child.
4. Integrate a board-local session runner, read-only health/deployment entry points and full safety gates. No upload/reconnect/reset/guard-delete commands.
5. Run new/old diagnostic, legacy image, DDR and vector regressions; inspect dependency closure, protected hashes and diff whitespace. Save fresh evidence in a new result directory, review the actual changes, emit a new executor manifest and incremental deployment list.

No board connection, execution, upload, production RTL/protected-document edits, commit or push in this workflow.
