"""Browser-controlled handoff before the approved 15 s + 3 s sampling countdown.

The core runner and count algorithm are unchanged. This launcher retains returned
FrameResults for audit and waits for one operator click after camera warm-up.
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
                 clock=time.monotonic):
        self.clock = clock
        self.handoff_timeout_s = handoff_timeout_s
        self.countdown_s = countdown_s
        self.audit_callback = None
        self.lock = threading.Lock()
        self._start_event = threading.Event()
        self._operator_authenticated = False
        self.phase = "INITIALIZING"
        self.deadline = None
        self.frame_zero = None
        self.result = None

    def mark_ready(self):
        with self.lock:
            self.phase = "READY"

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
                "result": self.result,
            }


PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stage B-2 投料控制</title><style>
body{margin:0;background:#101820;color:#f7fafc;font:24px system-ui;text-align:center;padding:4vh 5vw}
h1{font-size:32px}#big{font-size:100px;font-weight:800;margin:20px}#msg{min-height:80px}
button{font:700 28px system-ui;padding:22px 36px;border:0;border-radius:14px;background:#46db91;color:#082015;cursor:pointer}
button:disabled{background:#677580;color:#d0d5db;cursor:default}.sub{color:#b7c4cf;font-size:18px;margin-top:30px}
input{font:24px system-ui;padding:12px;border-radius:8px;border:1px solid #8395a3;margin:16px}
</style><h1>Stage B-2 · 5 颗 M6 螺母</h1><div id="big">等待</div>
<div id="msg">相机初始化中，请勿投料</div>
<label>操作口令 <input id="passcode" type="password" autocomplete="off" spellcheck="false"></label>
<button id="start" disabled>相机就绪后可开始</button>
<div class="sub">点击后：15 秒准备 + 3 秒稳姿 → 绿色“立即投料”<br>FRAME_0 后 0~3.5 秒内投完 5 颗；约 0.75 秒/颗<br>放在皮带纵向正中央，远离 ROI 上下边缘；有效采样固定为 165 帧</div>
<script>const big=document.querySelector('#big'),msg=document.querySelector('#msg'),btn=document.querySelector('#start'),passcode=document.querySelector('#passcode');
let submitting=false;btn.onclick=async()=>{if(submitting)return;submitting=true;btn.disabled=true;
try{let r=await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operator_passcode:passcode.value})});
passcode.value='';if(r.status===401){msg.textContent='操作口令不正确，请重新输入';submitting=false;}
else if(!r.ok){msg.textContent='无法开始，请检查许可状态';submitting=false;}}catch(e){passcode.value='';submitting=false;}};
async function poll(){try{const s=await(await fetch('/status',{cache:'no-store'})).json();
btn.disabled=s.phase!=='READY'||submitting;
if(s.phase==='READY'){big.textContent='已就绪';msg.textContent='握好 5 颗螺母，点击后按页面倒计时投料';btn.textContent='开始 18 秒倒计时';}
else if(s.phase==='COUNTDOWN'||s.phase==='STABILIZING'){big.textContent=s.seconds_remaining;msg.textContent=(s.phase==='STABILIZING'?'稳姿中':'准备中')+'，现在不要投料';btn.textContent='倒计时进行中';}
else if(s.phase==='SAMPLING'){big.textContent='立即投料';big.style.color='#46db91';msg.textContent='第 '+s.feed_number+' / 5 颗 · 约 0.75 秒/颗';btn.textContent='正在采样';}
else if(s.phase==='COMPLETE'){big.textContent='采样结束';big.style.color='#f7fafc';msg.textContent='请停止投料。计数 '+(s.result?.total_count??'?')+' / 5，完成 '+(s.result?.frames_processed??0)+' 帧';btn.textContent='本次许可已使用';}
}catch(e){}setTimeout(poll,100);}poll();</script></html>'''


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
