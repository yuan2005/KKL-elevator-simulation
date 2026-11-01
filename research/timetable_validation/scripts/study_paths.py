"""Portable study locations; private inputs and fresh runs stay out of Git."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
DATA = Path(os.environ.get('KKL_DATA_DIR', ROOT / 'private_data' / 'sis_2026-27')).resolve()
OUTPUT = Path(os.environ.get('KKL_OUTPUT_DIR', ROOT / 'local_runs')).resolve()
