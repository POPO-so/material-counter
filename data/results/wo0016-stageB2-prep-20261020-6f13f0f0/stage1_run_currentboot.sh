#!/bin/bash
set -euo pipefail

BOOT="6f13f0f0-4433-445f-a023-fa403685aeee"
RUN="/home/xilinx/board-runtime-currentboot-v2"
MANIFEST_SHA256="c5cb8ebd7bf50e8025d46d798ecf9aedb9cd043df5e16b34de294762dfb3b551"

export XILINX_XRT=/usr

test "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT"
test "$(cat /sys/class/fpga_manager/fpga0/state)" = "operating"
test "$(cat /sys/class/fpga_manager/fpga0/flags)" = "0"
test "$(awk '/^CmaFree:/ {print $2}' /proc/meminfo)" -gt 32768
test ! -e /var/lib/wo0015-s2mm/overlay-only-consumed.json
test ! -e /var/lib/wo0015-s2mm/overlay-only-pass.json
test ! -e /var/lib/wo0015-s2mm/terminal-failure.json
test "$(sha256sum "$RUN/scripts/wo0015/wo15.bit" | awk '{print $1}')" = \
    "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420"
test "$(sha256sum "$RUN/scripts/wo0015/wo15.hwh" | awk '{print $1}')" = \
    "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"

exec /usr/local/share/pynq-venv/bin/python3 -u \
    "$RUN/scripts/wo0015/board_once.py" \
    --mode overlay-only \
    --execute \
    --authorize-stage overlay-only \
    --approve-once \
    --manifest "$RUN/scripts/wo0015/run-manifest.json" \
    --manifest-sha256 "$MANIFEST_SHA256" \
    --timeout-s 60 \
    --evidence-json "$RUN/stage1-currentboot.json"
