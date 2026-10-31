set -euo pipefail
cd /tmp/material-perf-v8
/usr/local/share/pynq-venv/bin/python3 /tmp/material-cpu-microbench.py --version v8 --root /tmp/material-perf-v8 --captured-npz /var/lib/material-counter/f1462f32-61d2-44c7-9b42-d46d7d4d64c6/session-30c27c09-d91e-40de-a121-a45684ad2351.crossings/event-000335-e0-f9089/frame-0000009087.rle.npz --output /tmp/performance-v8-microbench.json
