"""Prepare a metadata-only probe. No hardware executor is dispatched."""
from pathlib import Path
OUT=Path(__file__).resolve().parent
previous=OUT.parent/'wo0015-first-board-retry2-20261015'
(OUT/'transport.py').write_bytes((previous/'controller.py').read_bytes())
