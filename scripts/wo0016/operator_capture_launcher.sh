#!/usr/bin/env bash
# Host-side launcher for the M8 camera-only capture protocol.
#
# Streams the board capture, creates drop_start after BASELINE_COMPLETE,
# waits for the program to observe it (DROP_START_OBSERVED), and only then
# cues the operator. The capture exit code is preserved; no cue is printed
# if any step fails. Test overrides: CAPTURE_CMD, TOUCH_CMD.
set -u -o pipefail
shopt -s lastpipe

HOST=${BOARD_HOST:-xilinx@192.168.2.99}
SESSION_DIR=${SESSION_DIR:?set SESSION_DIR to the board session path}
FRAMES=${FRAMES:-450}
BASELINE=${BASELINE:-60}
TAIL=${TAIL:-90}
START_DELAY=${START_DELAY:-12}
TOOL=${TOOL_PATH:-/home/xilinx/capture-tools/capture_touching_camera_only.py}
CAPTURE_CMD=${CAPTURE_CMD:-}
TOUCH_CMD=${TOUCH_CMD:-}

capture_command() {
    if [ -n "$CAPTURE_CMD" ]; then
        eval "$CAPTURE_CMD"
    else
        ssh -o BatchMode=yes "$HOST" \
            "sudo -n systemd-run --scope -p MemoryMax=512M -- \
             nice -n 19 timeout 180 /usr/local/share/pynq-venv/bin/python3 -B \
             '$TOOL' --output-dir '$SESSION_DIR' --frames $FRAMES \
             --baseline-frames $BASELINE --tail-frames $TAIL \
             --start-delay-s $START_DELAY --format ram --diagnostics"
    fi
}

drop_start_command() {
    if [ -n "$TOUCH_CMD" ]; then
        eval "$TOUCH_CMD"
    else
        ssh -o BatchMode=yes "$HOST" \
            "cat /proc/uptime; sudo -n touch '$SESSION_DIR/events/drop_start'"
    fi
}

baseline_seen=0
awaiting_ack=0
drop_start_ready=0
trigger_uptime=""

capture_command | while IFS= read -r line; do
    case "$line" in
        *BASELINE_COMPLETE*)
            if [ "$baseline_seen" -eq 0 ]; then
                baseline_seen=1
                output=$(drop_start_command 2>&1)
                rc=$?
                if [ "$rc" -ne 0 ]; then
                    printf 'DROP_START_TOUCH_FAILED rc=%s\n%s\n' "$rc" "$output"
                else
                    trigger_uptime=$(printf '%s\n' "$output" | head -1 | awk '{print $1}')
                    awaiting_ack=1
                    printf 'DROP_START_TRIGGERED board_uptime=%s\n' "$trigger_uptime"
                fi
            fi
            ;;
        *DROP_START_OBSERVED*)
            if [ "$awaiting_ack" -eq 1 ]; then
                awaiting_ack=0
                drop_start_ready=1
                mono=$(printf '%s\n' "$line" | sed -n 's/.*monotonic_ns=\([0-9][0-9]*\).*/\1/p')
                if [ -n "$mono" ] && [ -n "$trigger_uptime" ]; then
                    latency=$(awk -v m="$mono" -v t="$trigger_uptime" \
                        'BEGIN { printf "%.1f", m / 1e6 - t * 1000 }')
                    printf 'DROP_START_LATENCY_MS=%s\n' "$latency"
                fi
                printf '\a\a\a\n>>> 现在开始投放（10 颗 M8） <<<\n\a\a\a\n'
                printf 'DROP_START_ACK %s\n' "$line"
            fi
            ;;
    esac
    printf '%s\n' "$line"
done
rc=${PIPESTATUS[0]}

printf 'CAPTURE_RC=%s\n' "$rc"
if [ "$drop_start_ready" -ne 1 ]; then
    printf 'DROP_START_NOT_ACKNOWLEDGED\n'
fi
printf 'VERIFY_MANIFEST %s/manifest.json (status=COMPLETE, frame_count=%s)\n' \
    "$SESSION_DIR" "$FRAMES"
exit "$rc"