set -e
expected="$1"
source_dir="/var/lib/wo0015-s2mm/boot-${expected}"
target_dir="/tmp/wo0016-phase16-20261019-130149/stage1-capture"
test ! -e "$target_dir"
mkdir -m 700 "$target_dir"
cp "$source_dir/overlay-only-consumed.json" "$target_dir/"
cp "$source_dir/overlay-only-pass.json" "$target_dir/"
cp /tmp/wo0016-phase16-20261019-130149/overlay-evidence.json "$target_dir/"
sha256sum "$target_dir"/*
