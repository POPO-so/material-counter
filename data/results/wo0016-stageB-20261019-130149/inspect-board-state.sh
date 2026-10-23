#!/usr/bin/env bash
set +e
expected="$1"
echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
echo "CmaFree=$(awk '$1 == \"CmaFree:\" { print $2; exit }' /proc/meminfo) KiB"
echo "fpga_manager=$(cat /sys/class/fpga_manager/fpga0/state) flags=$(cat /sys/class/fpga_manager/fpga0/flags)"
ps -eo pid,ppid,stat,comm | awk 'NR == 1 || tolower($4) ~ /(python|dma|pynq)/'
wo15="/var/lib/wo0015-s2mm/boot-${expected}"
wo16="/var/lib/wo0016/boot-${expected}"
for path in "$wo15/overlay-only-consumed.json" "$wo15/overlay-only-pass.json" "$wo15/terminal-failure.json" "$wo16/smoke3-consumed.json" /tmp/wo0016-phase16-20261019-130149/overlay-evidence.json; do
  if [[ -e "$path" || -L "$path" ]]; then
    echo "ARTIFACT_PRESENT=$path"
    stat -c 'owner=%u mode=%a size=%s mtime=%y' "$path"
    if [[ -f "$path" && -r "$path" ]]; then cat "$path"; fi
  else
    echo "ARTIFACT_ABSENT=$path"
  fi
done
