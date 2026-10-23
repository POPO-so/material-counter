WO=/home/xilinx/wo0015
MANIFEST="$WO/wo0015-manifest.json"
MANIFEST_SHA256='e4d4da53e66c2fd4f1a3df082439dd439e54fa4ef1ba422367cab25ae9e98935'
sudo -n env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u "$WO/board_once.py" --mode overlay-only --manifest "$MANIFEST" --manifest-sha256 "$MANIFEST_SHA256" --execute --authorize-stage overlay-only --approve-once --timeout-s 5 --evidence-json "$WO/afi1-rerun/overlay-only-result.json"
sudo -n env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u "$WO/board_once.py" --mode dma-single --manifest "$MANIFEST" --manifest-sha256 "$MANIFEST_SHA256" --execute --authorize-stage dma-single --approve-once --timeout-s 5 --evidence-json "$WO/afi1-rerun/dma-single-result.json"
