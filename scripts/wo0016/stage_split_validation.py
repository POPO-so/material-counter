"""Prepare or explicitly upload a new isolated source directory; never run FPGA."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import zipfile

REMOTE = r'''
import hashlib, json, os, pathlib, stat, subprocess, sys, zipfile
root = pathlib.Path(sys.argv[1])
parent = pathlib.Path('/home/xilinx/wo0017')
if root.parent != parent or parent.resolve() != parent or root.is_symlink():
    raise ValueError('unexpected deployment parent or symlink')
root.mkdir(mode=0o700, exist_ok=False)
archive = root / 'deployment.zip'
digest = hashlib.sha256()
with archive.open('xb') as output:
    while True:
        chunk = sys.stdin.buffer.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        output.write(chunk)
if digest.hexdigest() != sys.argv[2]:
    raise ValueError('archive SHA-256 mismatch; partial directory retained, do not reuse')
with zipfile.ZipFile(archive) as stored:
    names = set()
    for item in stored.infolist():
        path = pathlib.PurePosixPath(item.filename)
        if path.is_absolute() or '..' in path.parts or '\\' in item.filename or item.filename in names:
            raise ValueError('unsafe or duplicate ZIP path')
        if stat.S_ISLNK(item.external_attr >> 16):
            raise ValueError('ZIP symlinks are prohibited')
        names.add(item.filename)
        target = root.joinpath(*path.parts)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with stored.open(item) as source, target.open('xb') as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
subprocess.run([sys.executable, str(root/'tools/verify_split_package.py'), '--root', str(root),
                '--manifest-sha256', sys.argv[3]], check=True)
print(json.dumps({'status':'UPLOADED_ONLY', 'deployment':str(root),
                  'hardware_status':'NOT_VERIFIED', 'camera_opened':False, 'dma_started':False}))
'''


def stage(archive, sha256, manifest_sha256, host, directory, worker_python, *, execute=False):
    for value in (sha256, manifest_sha256):
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("expected a pinned SHA-256")
    if re.fullmatch(r"[A-Za-z0-9_.@][A-Za-z0-9_.@:-]*", host) is None:
        raise ValueError("host must be an explicit SSH destination")
    if re.fullmatch(r"/home/xilinx/wo0017/split-validation-[A-Za-z0-9_-]+", directory) is None:
        raise ValueError("only a new split-validation directory is allowed")
    if (not worker_python.startswith("/") or "\x00" in worker_python
            or ".." in PurePosixPath(worker_python).parts
            or re.fullmatch(r"python(?:3(?:\.\d+)?)?", PurePosixPath(worker_python).name) is None):
        raise ValueError("worker Python must be an absolute Python executable path")
    raw = Path(archive).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("local archive SHA-256 mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as stored:
        if hashlib.sha256(stored.read("PACKAGE.json")).hexdigest() != manifest_sha256:
            raise ValueError("local PACKAGE.json SHA-256 mismatch")
    if not execute:
        return {"status": "DRY_RUN", "host": host, "directory": directory,
                "network_actions": 0, "hardware_actions": 0}
    command = " ".join(shlex.quote(item) for item in
                       (worker_python, "-c", REMOTE, directory, sha256, manifest_sha256))
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", host, command],
                            input=raw, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError("exclusive upload failed; preserve partial directory, do not retry it: "
                           + result.stderr.decode(errors="replace")[-2000:])
    return {"status": "UPLOADED_ONLY", "hardware_status": "NOT_VERIFIED",
            "remote_output": result.stdout.decode(errors="replace")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--worker-python", default="/usr/local/share/pynq-venv/bin/python3")
    parser.add_argument("--authorize-upload-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(stage(args.archive, args.archive_sha256, args.manifest_sha256, args.host,
                           args.directory, args.worker_python, execute=args.authorize_upload_only), indent=2))
