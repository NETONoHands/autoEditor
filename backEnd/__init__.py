"""Tapa na Lata backend package."""

import sys
from pathlib import Path

BACKEND_ROOT = str(Path(__file__).resolve().parent)
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)