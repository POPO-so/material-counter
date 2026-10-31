"""Create a Git-blob-exact source-only bundle for the later v8 package stage."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from io import BytesIO


COMMIT = "e2fb5952bfc4f36c3eacd53a8bdb6bdeec6c4fe2"
BRANCH = "perf/board-performance-20261031"
EVIDENCE_DIR = Path(__file__).resolve().parent


def git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], input=input_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_DIR)
    args = parser.parse_args()
    git_root = args.git_root.resolve(strict=True)
    evidence = args.evidence_dir.resolve(strict=True)

    commit = git(git_root, "rev-parse", f"{COMMIT}^{{commit}}").decode().strip()
    if commit != COMMIT:
        raise ValueError("fixed source commit did not resolve exactly")
    branch_commit = git(git_root, "rev-parse", f"refs/heads/{BRANCH}").decode().strip()
    git(git_root, "merge-base", "--is-ancestor", COMMIT, branch_commit)

    report_path = evidence / "v8-microbench.json"
    source_report = json.loads(report_path.read_text(encoding="utf-8"))
    modules = sorted(source_report["source"]["sha256_by_path"])
    if len(modules) != 43 or "scripts/standalone/performance_replay.py" not in modules:
        raise ValueError("expected the 42 existing modules plus performance_replay.py")
    if any(not name.startswith("scripts/") or not name.endswith(".py") for name in modules):
        raise ValueError("source list contains a non-application Python path")

    entries = []
    output = evidence / "v8-source-modules-e2fb595.tar.gz"
    with tarfile.open(output, mode="w:gz", compresslevel=9) as archive:
        for path in modules:
            object_spec = f"{COMMIT}:{path}"
            content = git(git_root, "cat-file", "blob", object_spec)
            blob_id = git(git_root, "rev-parse", object_spec).decode().strip()
            info = tarfile.TarInfo(path)
            info.size = len(content)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, BytesIO(content))
            entries.append({
                "path": path,
                "git_blob": blob_id,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "crlf_count": content.count(b"\r\n"),
                "lf_count": content.count(b"\n"),
            })

    index = {
        "schema": "material-counter.git-source-bundle.v1",
        "source_commit": COMMIT,
        "source_branch": BRANCH,
        "module_count": len(entries),
        "modules": entries,
        "archive": output.name,
        "archive_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    (evidence / "v8-source-module-correspondence.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "source_commit": COMMIT,
        "source_branch": BRANCH,
        "module_count": len(entries),
        "archive": output.name,
        "archive_sha256": index["archive_sha256"],
        "module_sha256_mismatches_vs_cpu_copy": [
            item["path"] for item in entries
            if item["sha256"] != source_report["source"]["sha256_by_path"].get(item["path"])
        ],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
