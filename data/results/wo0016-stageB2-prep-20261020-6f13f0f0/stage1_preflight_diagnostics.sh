#!/bin/bash
set -euo pipefail
export XILINX_XRT=/usr
RUN="/home/xilinx/board-runtime-currentboot-v2"
printf 'boot=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
printf 'fpga_state=%s\n' "$(cat /sys/class/fpga_manager/fpga0/state)"
printf 'fpga_flags=%s\n' "$(cat /sys/class/fpga_manager/fpga0/flags)"
printf 'cma_free_kib=%s\n' "$(awk '/^CmaFree:/ {print $2}' /proc/meminfo)"
for path in /var/lib/wo0015-s2mm/overlay-only-consumed.json \
            /var/lib/wo0015-s2mm/overlay-only-pass.json \
            /var/lib/wo0015-s2mm/terminal-failure.json; do
    if test -e "$path"; then printf 'exists=%s\n' "$path"; else printf 'absent=%s\n' "$path"; fi
done
sha256sum "$RUN/scripts/wo0015/wo15.bit" "$RUN/scripts/wo0015/wo15.hwh" \
    "$RUN/scripts/wo0015/run-manifest.json"
stat -c 'mode=%a owner=%U:%G path=%n' "$RUN" "$RUN/scripts/wo0015/board_once.py" \
    "$RUN/scripts/wo0015/board_preflight.py" "$RUN/scripts/wo0015/run-manifest.json"
