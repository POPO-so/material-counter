set -e
boot="$1"
wo15="/var/lib/wo0015-s2mm/boot-${boot}"
wo16="/var/lib/wo0016/boot-${boot}"
for path in "$wo15/overlay-only-consumed.json" "$wo15/overlay-only-pass.json" "$wo15/terminal-failure.json" "$wo16/smoke3-consumed.json" "$wo16/stageb-batch-consumed.json" /tmp/wo0016-phase16-20261019-130149/smoke3-analysis.json /tmp/wo0016-phase16-20261019-130149/smoke3-evidence.json; do
  if [[ -e "$path" || -L "$path" ]]; then echo "PRESENT=$path"; stat -c 'owner=%u mode=%a size=%s' "$path"; else echo "ABSENT=$path"; fi
done
