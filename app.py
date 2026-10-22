"""Vercel entrypoint (Root Directory = repo root): serves the demo in src/server.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from server import app  # noqa: E402,F401
