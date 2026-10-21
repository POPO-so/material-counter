#!/bin/bash
set -e

BOOT=58e6af6c-6660-4d44-b0c5-e18f4a564202
RUN=/home/xilinx/wo0016-phase110-$BOOT

for profile_script in /etc/profile.d/*.sh; do
    if test -r "$profile_script"; then
        . "$profile_script"
    fi
done
set -u
export XILINX_XRT=/usr

test "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT"
exec /usr/local/share/pynq-venv/bin/python3 -u \
    "$RUN/phase110_same_process_launcher.py" \
    --mode board \
    --execute \
    --authorize-stage wo0016-stream \
    --approve-once \
    --manifest "$RUN/wo0015/run-manifest.json" \
    --manifest-sha256 e07316b9398166c6fc7a459faaca271eaf32170c18b4deaa4c16208bcaa933f2 \
    --max-frames 3 \
    --expected-count 0 \
    --material-label static_dark_smoke \
    --timeout-s 60 \
    --evidence-json "$RUN/evidence/phase110-static-dark-smoke-3frames.json"
