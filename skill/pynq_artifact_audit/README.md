# PYNQ artifact audit

This small, read-only tool checks exact file bytes against a reviewed JSON manifest. It requires Python 3.10 or newer and uses only the Python standard library; it does not load PYNQ, Vivado, network, or hardware APIs.

## Scope

The audit verifies the manifest's exact bytes against the SHA-256 supplied by the reviewer, then hashes every manifest-listed file in chunks. It requires exactly one `bit` and one `hwh` role; `source` and `config` entries are optional. Paths must be relative to the audit root and resolve inside it, including after following links.

A pass means only that the selected files match the reviewed manifest. It does not prove where that manifest came from, that the BIT and HWH were generated together, that either file has a valid hardware format, that dependencies are complete, or that the deployment works on a board. It does not assess visual counting accuracy or select a project-wide license.

## Manifest and use

The manifest is UTF-8 JSON with this shape. Paths use `/` separators and are relative to `--root`:

```json
{
  "schema": 1,
  "files": [
    {"role": "bit", "path": "artifacts/design.bit", "sha256": "<64 hex characters>"},
    {"role": "hwh", "path": "artifacts/design.hwh", "sha256": "<64 hex characters>"},
    {"role": "source", "path": "app/run.py", "sha256": "<64 hex characters>"},
    {"role": "config", "path": "app/config.json", "sha256": "<64 hex characters>"}
  ]
}
```

Supply the manifest digest from an independent review channel; the script does not calculate or approve the expected digest for you. From this skill directory, run:

```powershell
$manifestSha256 = "REPLACE_WITH_REVIEWED_64_CHARACTER_SHA256"
python scripts/audit.py `
  --manifest C:\bundle\manifest.json `
  --manifest-sha256 $manifestSha256 `
  --root C:\bundle
```

`--root` is optional and defaults to the resolved directory containing the manifest. The command prints one JSON object to stdout. `ARTIFACT_HASH_PASS` exits 0; any digest, schema, path, or file error prints `ARTIFACT_HASH_FAIL` and exits 2. The tool does not edit, rename, repair, or write into the manifest or artifact tree.

## Failure meanings

The audit fails closed for a manifest digest mismatch, unsupported schema, malformed digest, duplicate path, missing or unreadable file, unsupported role, missing or repeated BIT/HWH entries, absolute path, parent traversal, or a resolved symlink/junction target outside the selected root. A file-byte mismatch is reported against its manifest entry. Review the JSON `errors` and per-file status fields; do not turn a failure into a pass by regenerating the manifest without reviewing the new artifact set.

## Why pin the exact version

The [2026-11-04 submission-candidate record](../../docs/verification/submission-candidates-20261104.json) identifies a JPEG PL v8 candidate at commit `b269ee01c765a43f0cfa6aeca657b63b34f02b2b`: all 18 packaged source files match that pinned source, while only 12 match the recorded `LJ-01` default-branch snapshot. The record says no final submission was selected, no board was accessed, and the candidate is `ROUTED_NOT_BOARD_VERIFIED`.

The [v8 release receipt](../../data/results/jpeg-pl-board-20261102/jpeg-pl-runtime-v8-release.json) separately records `PACKAGE_HASH_VERIFIED`, 18 source files and 21 ZIP entries verified, but `board_deployed=false` and `physical_hdmi_verified=false`. This illustrates why a digest must be tied to a specific manifest and why a hash pass is not board acceptance. These records describe that candidate only; they do not establish the state of another branch or package.

## Tested behavior

The CLI regression suite uses explicitly synthetic `.bit` and `.hwh` fixture bytes; they are test data, not bitstreams or hardware metadata. Tests cover a valid manifest, damaged and missing files, a changed manifest digest, path traversal and symlink/junction escape, duplicate paths, missing HWH, unsupported schema, malformed SHA-256, and the default root. A skipped link-escape case means the test host could not create either a symlink or junction; the code still resolves and bounds each path before reading it.

The [2026-11-04 verification record](../../docs/verification/pynq-artifact-audit-20261104.json) records 12 CLI tests passing, a 20-file audit of an extracted real v8 package passing, and rejection after changing one BIT byte in that temporary copy. The original ZIP was unchanged. This exercises file-identity checks only; no artifact was loaded onto a board.
