#!/usr/bin/env bash
# Host-side one-key drop_end sender for the M8 capture protocol.
#
# Establishes a persistent SSH master so the marker is one key press away.
# Refuses to send a second drop_end for the same session. Test override:
# TOUCH_CMD (reads one key from stdin, then runs the command).
set -u

HOST=${BOARD_HOST:-xilinx@192.168.2.99}
SESSION_DIR=${SESSION_DIR:?set SESSION_DIR to the board session path}
CONTROL=${SSH_CONTROL:-/tmp/m8-capture-%r@%h:%p}
TOUCH_CMD=${TOUCH_CMD:-}

send() {
    if [ -n "$TOUCH_CMD" ]; then
        eval "$TOUCH_CMD"
    else
        ssh -o BatchMode=yes -o ControlPath="$CONTROL" "$HOST" \
            "cat /proc/uptime; sudo -n touch '$SESSION_DIR/events/drop_end'"
    fi
}

if [ -n "$TOUCH_CMD" ]; then
    exists=$([ -e "${DROP_END_PATH:-/nonexistent}" ] && echo yes || echo no)
else
    ssh -o BatchMode=yes -o ControlMaster=auto -o ControlPath="$CONTROL" \
        -o ControlPersist=900 -fN "$HOST" 2>/dev/null || true
    exists=$(ssh -o BatchMode=yes -o ControlPath="$CONTROL" "$HOST" \
        "test -e '$SESSION_DIR/events/drop_end' && echo yes || echo no" 2>/dev/null)
fi
if [ "$exists" = "yes" ]; then
    printf 'DROP_END_ALREADY_EXISTS (not sent again)\n'
    exit 2
fi

read -n 1 -r -p "最后一颗离开投放位后按任意键发送 drop_end..." _
printf '\n'
output=$(send 2>&1)
rc=$?
printf '%s\n' "$output"
printf 'DROP_END_SENT rc=%s board_uptime=%s\n' "$rc" \
    "$(printf '%s\n' "$output" | head -1 | awk '{print $1}')"
exit "$rc"