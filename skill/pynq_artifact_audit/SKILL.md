---
name: pynq-artifact-audit
description: Read-only verification of reviewed PYNQ BIT/HWH and runtime-package file hashes; does not establish board acceptance.
---

# PYNQ Artifact Audit

Use this skill to check that files in a PYNQ deployment bundle match a separately reviewed manifest and its SHA-256. The audit reads files only; it does not import PYNQ, access a board or network, inspect artifact formats, establish that BIT and HWH came from the same build, prove dependency closure, or measure counting accuracy.

Run `scripts/audit.py` with the reviewed manifest digest. Treat `ARTIFACT_HASH_PASS` only as byte-level agreement with that manifest. Read [README.md](README.md) for the schema, command examples, failure meanings, and the version-locking evidence case.
