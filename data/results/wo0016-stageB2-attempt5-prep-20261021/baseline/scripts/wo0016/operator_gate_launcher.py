"""Browser-controlled handoff before the approved 15 s + 3 s sampling countdown.

The core runner and count algorithm are unchanged. This launcher retains returned
FrameResults for audit and waits for one operator click after camera warm-up.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time


class OperatorGate:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.start_event = threading.Event()
        self.phase = "INITIALIZING"
        self.deadline = None
        self.frame_zero = None
        self.result = None

    def mark_ready(self):
        with self.lock:
            self.phase = "READY"

    def request_start(self):
        with self.lock:
            if self.phase != "READY" or self.start_event.is_set():
                return False
            self.phase = "START_REQUESTED"
            self.start_event.set()
            return True

    def wait_for_operator(self, *, timeout_s):
        self.mark_ready()
        if not self.start_event.wait(timeout_s):
            raise RuntimeError("operator start was not received before the handoff timeout")

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
                                min(5, max(1, int(now - self.frame_zero) + 1))),
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
</style><h1>Stage B-2 · 5 颗 M6 螺母</h1><div id="big">等待</div>
<div id="msg">相机初始化中，请勿投料</div><button id="start" disabled>相机就绪后可开始</button>
<div class="sub">点击后：15 秒准备 + 3 秒稳姿 → 绿色“立即投料”<br>每秒 1 颗，连续 5 颗；有效采样固定为 165 帧</div>
<script>const big=document.querySelector('#big'),msg=document.querySelector('#msg'),btn=document.querySelector('#start');
let submitting=false;btn.onclick=async()=>{if(submitting)return;submitting=true;btn.disabled=true;
try{let r=await fetch('/start',{method:'POST',headers:{'X-Operator-Token':'__TOKEN__'}});if(!r.ok){submitting=false;}}catch(e){submitting=false;}};
async function poll(){try{const s=await(await fetch('/status',{cache:'no-store'})).json();
btn.disabled=s.phase!=='READY'||submitting;
if(s.phase==='READY'){big.textContent='已就绪';msg.textContent='握好 5 颗螺母，点击后按页面倒计时投料';btn.textContent='开始 18 秒倒计时';}
else if(s.phase==='COUNTDOWN'||s.phase==='STABILIZING'){big.textContent=s.seconds_remaining;msg.textContent=(s.phase==='STABILIZING'?'稳姿中':'准备中')+'，现在不要投料';btn.textContent='倒计时进行中';}
else if(s.phase==='SAMPLING'){big.textContent='立即投料';big.style.color='#46db91';msg.textContent='第 '+s.feed_number+' / 5 颗 · 每秒 1 颗';btn.textContent='正在采样';}
else if(s.phase==='COMPLETE'){big.textContent='采样结束';big.style.color='#f7fafc';msg.textContent='请停止投料。计数 '+(s.result?.total_count??'?')+' / 5，完成 '+(s.result?.frames_processed??0)+' 帧';btn.textContent='本次许可已使用';}
}catch(e){}setTimeout(poll,100);}poll();</script></html>'''


def make_server(gate, *, port, token):
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
                self.respond(200, PAGE.replace("__TOKEN__", token).encode(), "text/html; charset=utf-8")
            elif self.path == "/status":
                self.respond(200, json.dumps(gate.snapshot()).encode(), "application/json")
            else:
                self.respond(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/start" or self.headers.get("X-Operator-Token") != token:
                self.respond(403, b"forbidden", "text/plain")
                return
            self.respond(200 if gate.request_start() else 409, b"{}", "application/json")

    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-dir", type=Path, required=True)
    parser.add_argument("--control-port", type=int, default=8081)
    parser.add_argument("--handoff-timeout-s", type=float, default=300)
    parser.add_argument("runner_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    runner_args = args.runner_args[1:] if args.runner_args[:1] == ["--"] else args.runner_args
    import run_stream_counter as runner
    run_options = runner._parser().parse_args(runner_args)
    gate = OperatorGate()
    token = secrets.token_hex(16)
    server = make_server(gate, port=args.control_port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_wait = runner._countdown_wait
    original_emit = runner._emit_milestone
    original_run = runner.DoubleBufferStream.run
    captured = {}

    def audit_event(name):
        with (args.attempt_dir / "operator-control-events.jsonl").open("a") as stream:
            stream.write(json.dumps({"event": name, "monotonic": time.monotonic()}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def wait(duration_s, *, label):
        if label == "OPERATOR_PREARM":
            audit_event("WAITING_FOR_BROWSER_OPERATOR_CLICK")
            print("[OPERATOR_GATE] CAMERA_READY_WAITING_FOR_BROWSER_CLICK", flush=True)
            gate.wait_for_operator(timeout_s=args.handoff_timeout_s)
            audit_event("BROWSER_OPERATOR_CLICK_ACCEPTED")
            gate.begin_countdown(float(duration_s) + 3.0)
        return original_wait(duration_s, label=label)

    def emit(name, at, log_path, *, boot_id):
        value = original_emit(name, at, log_path, boot_id=boot_id)
        gate.milestone(name, at)
        return value

    async def observe(self, *run_args, **kwargs):
        value = await original_run(self, *run_args, **kwargs)
        captured["frames"] = value
        return value

    runner._countdown_wait = wait
    runner._emit_milestone = emit
    runner.DoubleBufferStream.run = observe
    code = 2
    try:
        code = runner.main(runner_args)
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
