# WO-0015 AFI1 precondition clarification implementation plan

Goal: Resolve only missing documentary evidence and implement an offline-tested,
OS-files-only live evidence collector. Keep STOPPED_PRECONDITION_UNVERIFIED.

Architecture: Separate raw OS evidence collection from register admission. No
caller-supplied booleans can permit AFI access. The collector has no register
reader, network transport, PYNQ import, or runnable board CLI. File samples bind
raw bytes and SHA-256 to one bounded boot/uptime window and a fresh challenge.
Unresolved reset interpretation/current state remain explicit blockers.

Tech stack: Python standard library, pytest/fake filesystem; official AMD docs.

1. Reuse prior report and evidence; Worker checks versioned UG585, another checks
   generated C/Tcl/PS and saved startup path. Write only fresh result subfolders.
2. Add collector regression tests first: raw bytes, strict locks, same boot/time,
   exact file sources, ownership visibility, malformed records, fail closed.
3. Implement OS-only collector plus guarded read-only file backend, with no
   writable device or register capability. Do not execute the real backend.
4. Audit source and tests independently, verify historical evidence/artifact
   hashes, and write version/conflict tables, next minimal authorization and risks.
5. Record targeted test results and source hashes; no deployment/commit/merge.

New files stay under this result directory. No RTL, hardware artifacts, existing
production executor, prior reports or guards are edited. Any future transport
or register probe requires a separate reviewed and authorized action.
