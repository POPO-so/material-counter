---
name: overlay-artifact-admission
description: Use when preparing or reviewing a PYNQ/Vivado overlay bundle, BIT/HWH pair, or manifest before loading an overlay. Check content hashes, path containment, build readiness, HWH topology and declared capabilities offline, and report precise stop conditions and limits.
---

# Overlay artifact admission

Admit the exact BIT/HWH/manifest set before any hardware access. The goal is to reject a stale, mismatched, unsafe-path, or structurally incompatible bundle while the check is still offline.

## Scope

- Apply to exported overlay artifacts and manifests before programming PL.
- Use the repository's existing verifier where possible. In this project, `scripts/standalone/bundle.py` provides `verify_bundle(manifest_path, expected_sha256)` and `verify_offload_capabilities(manifest, hwh)`.
- Keep source commit, hardware-input snapshot, build identity, BIT, HWH, manifest, and package index as distinct identities. Bind them with recorded hashes; filenames alone are not proof of a matching build.
- Do not load an overlay, access registers, install dependencies, or change a manifest as part of the offline gate.

## Workflow

1. Obtain the manifest path and expected manifest SHA-256 from an independently reviewed build/package record. Do not calculate a digest and then use that same unreviewed value as the trust anchor.
2. Run the existing project verifier in the repository root:

   ```sh
   python -c "from scripts.standalone.bundle import verify_bundle; print(verify_bundle('PATH/TO/display-manifest.json', 'EXPECTED_SHA256'))"
   ```

   This verifies the manifest digest, schema, `ready_for_board`, safe relative paths, BIT/HWH hashes, matching artifact basenames, expected HWH address map, VDMA parameters, and—when present—the fixed offload capability contract and HWH IRQ wiring.
3. Preserve command, exit status, stdout/stderr, and the manifest/BIT/HWH digests in a new evidence record. Do not overwrite prior build evidence.
4. If any gate fails, stop. Report the exact exception and the artifact identity. Do not repair the manifest in place, substitute a similarly named artifact, or proceed to board access.
5. For the board phase, use the existing reviewed boot path only after offline admission. `boot_integrated.py` calls `verify_bundle`, checks runtime identity, then performs its hardware-side checks. The offline result alone does not prove runtime register signatures or loaded-bitstream identity.

## Failure and invalidation conditions

Reject or invalidate the result when the expected manifest digest is missing or not independently reviewed; any artifact digest differs; the HWH is missing, malformed, or does not match the BIT basename; `ready_for_board` is false; a path escapes the manifest directory; required address/topology/VDMA fields differ; the declared offload contract or IRQ wiring differs; or the source/hardware association is not traceable to the tested build.

Re-run the gate after any change to the manifest, BIT, HWH, package contents, or pinned identity mapping. A successful gate does not establish that register signatures are present in the loaded PL; `bundle.py` explicitly says PBO1/RAW1 register signatures are checked at runtime.

## Verification effect and limits

A pass establishes that the examined files are internally consistent with the expected manifest digest and the verifier's fixed structural contract. It does not establish successful overlay loading, physical I/O, timing closure beyond the supplied build evidence, camera/display behavior, safe DMA shutdown, or counting accuracy. Do not call an offline pass a board test.

## Source case

Reusable behavior is implemented in `scripts/standalone/bundle.py`: `verify_bundle` pins the manifest and both artifacts, checks path containment, HWH address map, and VDMA parameters; `verify_offload_capabilities` compares the fixed capability contract and checks HWH ranges and GPIO IRQ wiring. `scripts/standalone/boot_integrated.py` calls this verifier before runtime admission. The dated JPEG experiment report also illustrates why an artifact result must stay tied to its own candidate rather than being transferred to a merged snapshot: `data/results/jpeg-pl-board-20261102/README.md`.
