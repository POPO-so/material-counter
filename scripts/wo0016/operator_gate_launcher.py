"""Authenticated browser handoff before the approved finite sampling countdown.

The page remains a control surface only: sampling still requires the validated
operator session, the applicable one-shot permit, and the runner's fail-closed
hardware checks. Returned FrameResults are retained for audit.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
import weakref


_ACTIVE_CONTROL_GATES = weakref.WeakSet()
_ACTIVE_CONTROL_GATES_LOCK = threading.Lock()


def is_registered_operator_gate_session(gate):
    with _ACTIVE_CONTROL_GATES_LOCK:
        return gate in _ACTIVE_CONTROL_GATES


class OperatorGate:
    def __init__(self, *, handoff_timeout_s=300, countdown_s=18.0,
                 clock=time.monotonic, require_permit_sha256=False):
        self.clock = clock
        self.handoff_timeout_s = handoff_timeout_s
        self.countdown_s = countdown_s
        self.audit_callback = None
        self.lock = threading.Lock()
        self._start_event = threading.Event()
        self._operator_authenticated = False
        self.require_permit_sha256 = bool(require_permit_sha256)
        self.permit_sha256 = None
        self.phase = "INITIALIZING"
        self.deadline = None
        self.frame_zero = None
        self.result = None

    def mark_ready(self):
        with self.lock:
            if self.require_permit_sha256 and self.permit_sha256 is None:
                raise RuntimeError("operator gate requires a validated permit SHA-256 before READY")
            self.phase = "READY"

    def bind_permit_sha256(self, digest):
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("permit SHA-256 must be 64 lowercase hexadecimal characters")
        with self.lock:
            if self.permit_sha256 is not None and self.permit_sha256 != digest:
                raise RuntimeError("operator gate permit SHA-256 is already bound")
            self.permit_sha256 = digest

    def wait_for_operator(self, *, timeout_s=None, countdown_s=None):
        timeout = self.handoff_timeout_s if timeout_s is None else timeout_s
        countdown = self.countdown_s if countdown_s is None else countdown_s
        if self.audit_callback is not None:
            self.audit_callback("WAITING_FOR_BROWSER_OPERATOR_CLICK")
        print("[OPERATOR_GATE] CAMERA_READY_WAITING_FOR_BROWSER_CLICK", flush=True)
        self.mark_ready()
        if not self._start_event.wait(timeout):
            with self.lock:
                self.phase = "TIMED_OUT"
            raise RuntimeError("operator start was not received before the handoff timeout")
        with self.lock:
            if not self._operator_authenticated:
                self.phase = "REJECTED_UNAUTHENTICATED_START"
                raise RuntimeError("operator start did not come from an authenticated browser request")
        if self.audit_callback is not None:
            self.audit_callback("BROWSER_OPERATOR_CLICK_ACCEPTED")
        self.begin_countdown(countdown)

    def begin_countdown(self, seconds):
        with self.lock:
            self.phase = "COUNTDOWN"
            self.deadline = self.clock() + seconds

    def milestone(self, name, at):
        with self.lock:
            if name == "CAMERA_OPEN_AND_STABILIZING_START":
                self.phase = "STABILIZING"
                self.deadline = at + 3.0
            elif name == "FRAME_0_CAPTURE_START_FEED_NOW":
                self.phase = "SAMPLING"
                self.frame_zero = at

    def finish(self, result):
        with self.lock:
            self.phase = "COMPLETE"
            self.result = result

    def snapshot(self):
        with self.lock:
            now = self.clock()
            return {
                "phase": self.phase,
                "seconds_remaining": (None if self.deadline is None else
                                      max(0, math.ceil(self.deadline - now))),
                "feed_number": (None if self.frame_zero is None else
                                min(5, max(1, int(max(0.0, now - self.frame_zero) / 0.75) + 1))),
                "sampling_elapsed_s": (None if self.frame_zero is None else
                                       max(0.0, now - self.frame_zero)),
                "permit_sha256": self.permit_sha256,
                "permit_binding_required": self.require_permit_sha256,
                "result": self.result,
            }


PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WO-0016 · Operator Console</title><style>
:root{color-scheme:dark;--bg:#07111b;--panel:#0f1d29;--panel2:#142635;--line:#243b4b;--cyan:#42daca;--green:#45e39a;--amber:#f3ba63;--text:#e7f0f7;--muted:#8fa4b7}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 8% 0,#183348 0,transparent 38%),var(--bg);color:var(--text);font:15px system-ui,sans-serif}
.shell{max-width:1120px;margin:auto;padding:28px 20px}.top{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:20px;gap:16px}
.eyebrow,.mono{font:700 11px ui-monospace,monospace;letter-spacing:.13em;color:var(--cyan)}h1{font-size:21px;margin:6px 0 0}.state{border:1px solid #335466;border-radius:99px;padding:8px 12px;color:#b9d1de;font:12px ui-monospace,monospace}
.grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr);gap:16px}.card{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:15px;padding:20px;box-shadow:0 16px 42px #0004}
.hero{min-height:310px;display:flex;flex-direction:column;justify-content:space-between}.label{display:block;color:var(--muted);font:700 10px ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase}
#big{font-size:clamp(56px,9vw,96px);font-weight:800;letter-spacing:-.05em;line-height:1;margin:24px 0 12px;color:var(--text)}#msg{color:#b7c7d3;min-height:42px;line-height:1.5}
.side{display:grid;gap:12px;align-content:start}.metric{font:12px ui-monospace,monospace;color:#c7d8e2;overflow-wrap:anywhere;margin-top:9px}.metric strong{color:var(--text)}.required{color:var(--amber)}.bound{color:var(--green)}
.operator{grid-column:1/-1}.operator-row{display:flex;gap:12px;align-items:center;margin-top:14px}.passcode{flex:1;min-width:0;background:#08131d;color:var(--text);border:1px solid #355063;border-radius:9px;padding:13px;font:16px ui-monospace,monospace}
button{border:1px solid #4cf0af;border-radius:9px;background:linear-gradient(135deg,#44e49a,#24b98c);color:#062019;padding:13px 19px;font:800 14px system-ui;cursor:pointer}button:disabled{border-color:#3a4b57;background:#344451;color:#a3b0ba;cursor:default}
.help{margin:16px 0 0;color:var(--muted);font-size:13px;line-height:1.8}
@media(max-width:760px){.shell{padding:18px 12px}.grid{grid-template-columns:1fr}.operator{grid-column:auto}.hero{min-height:270px}.operator-row{align-items:stretch;flex-direction:column}.top{align-items:flex-start;flex-direction:column}}
</style></head><body><div class="shell">
<header class="top"><div><div class="eyebrow">POPO-SO / MATERIAL COUNTER / CONTROL PLANE</div><h1>Stage B-2 · M6 Material Run</h1></div><span class="state" id="phase">INITIALIZING</span></header>
<main class="grid"><section class="card hero"><div><span class="label">Operator handoff</span><div id="big">等待</div><div id="msg">相机初始化中，请勿投料</div></div><div class="mono">FINITE RUN · 165 FRAMES · FAIL-HOLD ENABLED</div></section>
<aside class="side"><section class="card"><span class="label">Permit binding · SHA-256</span><div class="metric" id="permit">等待执行器校验许可</div></section>
<section class="card"><span class="label">Sampling window</span><div class="metric">固定 <strong>165 帧</strong> · 不支持重试</div><div class="metric">状态由执行器单调时钟驱动</div></section>
<section class="card"><span class="label">Security</span><div class="metric">口令只通过带外渠道提供并输入</div><div class="metric">页面与状态接口不返回口令</div></section></aside>
<section class="card operator"><span class="label">Authenticated operator action</span><div class="operator-row"><input id="passcode" class="passcode" type="password" autocomplete="off" spellcheck="false" placeholder="输入带外操作口令"><button id="start" disabled>等待相机就绪</button></div>
<p class="help">点击后：15 秒准备 + 3 秒稳姿 → 绿色“立即投料”。FRAME_0 后 0~3.5 秒内投完 5 颗；约 0.75 秒/颗。放在皮带纵向正中央，远离 ROI 上下边缘。进入检测窗口的实际实体数需独立记录。</p></section></main></div>
<script>const big=document.querySelector('#big'),msg=document.querySelector('#msg'),btn=document.querySelector('#start'),passcode=document.querySelector('#passcode'),phase=document.querySelector('#phase'),permit=document.querySelector('#permit');
let submitting=false;btn.onclick=async()=>{if(submitting)return;submitting=true;btn.disabled=true;
try{let r=await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operator_passcode:passcode.value})});
passcode.value='';if(r.status===401){msg.textContent='操作口令不正确，请重新输入';submitting=false;}
else if(!r.ok){msg.textContent='无法开始，请检查许可状态';submitting=false;}}catch(e){passcode.value='';submitting=false;}};
async function poll(){try{const s=await(await fetch('/status',{cache:'no-store'})).json();phase.textContent=s.phase;const isBound=!!s.permit_sha256;permit.textContent=isBound?'BOUND · '+s.permit_sha256.slice(0,16)+'…':'等待执行器校验许可';permit.className='metric '+(isBound?'bound':(s.permit_binding_required?'required':''));
btn.disabled=s.phase!=='READY'||submitting||(s.permit_binding_required&&!isBound);
if(s.phase==='READY'){big.textContent='已就绪';msg.textContent='等待操作员确认；请按独立观察计划准备物料';btn.textContent='开始 18 秒倒计时';}
else if(s.phase==='COUNTDOWN'||s.phase==='STABILIZING'){big.textContent=s.seconds_remaining;msg.textContent=(s.phase==='STABILIZING'?'稳姿中':'准备中')+'，现在不要投料';btn.textContent='倒计时进行中';}
else if(s.phase==='SAMPLING'){big.textContent='立即投料';big.style.color='var(--green)';msg.textContent='第 '+s.feed_number+' / 5 颗 · 约 0.75 秒/颗';btn.textContent='正在采样';}
else if(s.phase==='COMPLETE'){big.textContent='采样结束';big.style.color='var(--text)';msg.textContent='请停止投料。计数 '+(s.result?.total_count??'?')+'，完成 '+(s.result?.frames_processed??0)+' 帧';btn.textContent='本次许可已使用';}
}catch(e){}setTimeout(poll,150);}poll();</script></body></html>'''


def make_server(gate, *, port, host="127.0.0.1"):
    operator_passcode = secrets.token_urlsafe(32)
    with _ACTIVE_CONTROL_GATES_LOCK:
        if gate in _ACTIVE_CONTROL_GATES:
            raise ValueError("operator gate already has a registered control server")
        _ACTIVE_CONTROL_GATES.add(gate)
    print(f"[OPERATOR_GATE] One-time operator passcode: {operator_passcode}", flush=True)

    def accept_authenticated_browser_start():
        with gate.lock:
            if gate.phase != "READY" or gate._start_event.is_set():
                return False
            gate.phase = "START_REQUESTED"
            gate._operator_authenticated = True
            gate._start_event.set()
            return True

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, payload, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/":
                self.respond(200, PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/status":
                self.respond(200, json.dumps(gate.snapshot()).encode(), "application/json")
            else:
                self.respond(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/start":
                self.respond(404, b"not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 4096:
                    raise ValueError("invalid body length")
                request = json.loads(self.rfile.read(length))
                candidate = request.get("operator_passcode") if isinstance(request, dict) else None
                if not isinstance(candidate, str):
                    authorized = False
                    started = False
                else:
                    try:
                        authorized = hmac.compare_digest(
                            candidate.encode("utf-8"), operator_passcode.encode("utf-8")
                        )
                    except UnicodeError:
                        authorized = False
                    started = accept_authenticated_browser_start() if authorized else False
            except (ValueError, UnicodeError, json.JSONDecodeError):
                authorized = False
                started = False
            if not authorized:
                self.respond(401, b"unauthorized", "text/plain")
                return
            self.respond(200 if started else 409, b"{}", "application/json")

    class GateServer(ThreadingHTTPServer):
        def server_close(self):
            try:
                super().server_close()
            finally:
                with _ACTIVE_CONTROL_GATES_LOCK:
                    _ACTIVE_CONTROL_GATES.discard(gate)

    try:
        return GateServer((host, port), Handler)
    except BaseException:
        with _ACTIVE_CONTROL_GATES_LOCK:
            _ACTIVE_CONTROL_GATES.discard(gate)
        raise


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-dir", type=Path, required=True)
    parser.add_argument("--control-port", type=int, default=8081)
    parser.add_argument("--host", default="127.0.0.1",
                        help="control listener address (default: loopback)")
    parser.add_argument("--handoff-timeout-s", type=float, default=300)
    parser.add_argument("runner_args", nargs=argparse.REMAINDER)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    runner_args = args.runner_args[1:] if args.runner_args[:1] == ["--"] else args.runner_args
    import run_stream_counter as runner
    run_options = runner._parser().parse_args(runner_args)
    gate = OperatorGate(
        handoff_timeout_s=args.handoff_timeout_s,
        countdown_s=(float(run_options.operator_prearm_delay_s)
                     + float(run_options.stabilization_delay_s)),
        require_permit_sha256=bool(
            run_options.mode == "board" and run_options.no_consume_guard
        ),
    )
    gate_launcher_sha256 = hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("[OPERATOR_GATE] WARNING: non-loopback binding uses plaintext HTTP; "
              "the one-time passcode is exposed to network interception. Prefer a "
              "loopback listener with SSH forwarding; forwarding does not protect "
              "a non-loopback listener.", flush=True)
    server = make_server(gate, host=args.host, port=args.control_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_emit = runner._emit_milestone
    original_run = runner.DoubleBufferStream.run
    captured = {}

    def audit_event(name):
        with (args.attempt_dir / "operator-control-events.jsonl").open("a") as stream:
            stream.write(json.dumps({"event": name, "monotonic": time.monotonic()}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def emit(name, at, log_path, *, boot_id):
        value = original_emit(name, at, log_path, boot_id=boot_id)
        gate.milestone(name, at)
        return value

    async def observe(self, *run_args, **kwargs):
        value = await original_run(self, *run_args, **kwargs)
        captured["frames"] = value
        return value

    gate.audit_callback = audit_event
    runner._emit_milestone = emit
    runner.DoubleBufferStream.run = observe
    code = 2
    try:
        code = runner.main(runner_args, operator_gate_sha256=gate_launcher_sha256,
                           operator_gate=gate)
    finally:
        if "frames" in captured:
            with (args.attempt_dir / "frame-results.json").open("x") as stream:
                json.dump([dataclasses.asdict(frame) for frame in captured["frames"]], stream)
                stream.flush()
                os.fsync(stream.fileno())
        result_path = Path(run_options.evidence_json)
        result = json.loads(result_path.read_text()) if result_path.exists() else {"status": "FAILED"}
        gate.finish({key: result.get(key) for key in ("status", "total_count", "frames_processed", "throughput_fps")})
        audit_event("EXECUTOR_EXITED")
        with (args.attempt_dir / "exit-status.json").open("x") as stream:
            json.dump({"exit_code": code, "end_monotonic": time.monotonic(), "frame_records": len(captured.get("frames", ()))}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        time.sleep(2)
        server.shutdown()
        server.server_close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
