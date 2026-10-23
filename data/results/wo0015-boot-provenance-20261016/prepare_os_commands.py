"""Generate reviewable future remote command strings; never connects or runs them."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import re
import shlex

OUT = Path(__file__).resolve().parent
SOURCE = OUT / "os_file_proposal.py"


def make_command(stage, nonce):
    if stage not in ("os-metadata", "boot-bin") or not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("Fixed stage and lowercase 128-bit nonce required")
    raw = SOURCE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    wrapper = (
        "import base64,hashlib,json,os,sys;"
        "assert os.geteuid()==0 and os.uname().sysname=='Linux' and os.uname().machine=='armv7l';"
        f"src=base64.b64decode({base64.b64encode(raw).decode('ascii')!r},validate=True);"
        f"assert hashlib.sha256(src).hexdigest()=={digest!r};"
        "scope={'__name__':'wo0015_reviewed_os_file_proposal'};"
        "exec(compile(src,'<reviewed-os-file-proposal>','exec'),scope);"
        f"result=scope['collect'](scope['PinnedReadOnlyFiles'](),stage={stage!r},nonce={nonce!r});"
        f"result['verified_source_sha256']={digest!r};"
        "sys.stdout.buffer.write((json.dumps(result,ensure_ascii=True)+'\\n').encode('utf-8'));"
        "sys.stdout.buffer.flush();"
        "sys.exit(0 if result['capture_status']=='COLLECTED_OS_FILES_ONLY' else 2)"
    )
    ast.parse(wrapper)
    argv = ["/usr/bin/sudo", "-n", "--", "/usr/local/share/pynq-venv/bin/python", "-I", "-S", "-B", "-u", "-c", wrapper]
    return {"stage": stage, "nonce": nonce, "source_sha256": digest, "argv": argv, "remote_shell_command": shlex.join(argv), "wrapper_sha256": hashlib.sha256(wrapper.encode()).hexdigest()}


if __name__ == "__main__":
    plans = {"kind": "OFFLINE_REVIEW_EXAMPLES_NEVER_EXECUTED", "nonce_notice": "Repeated zero nonce is an example only; a newly authorized run must generate a fresh host nonce and bind authenticated transport/source identity.", "commands": [make_command(stage, "0" * 32) for stage in ("os-metadata", "boot-bin")]}
    target = OUT / "future-command-plan.json"
    with target.open("x", encoding="utf-8") as f: json.dump(plans, f, indent=2); f.write("\n")
    print(json.dumps({"plans_generated_not_executed": 2, "source_sha256": plans["commands"][0]["source_sha256"]}))
