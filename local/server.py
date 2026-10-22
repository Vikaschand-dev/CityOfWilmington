"""Local end-to-end simulator launcher; the app lives in src/server.py.

  python local/server.py        -> http://127.0.0.1:8311
"""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent.parent / "src" / "server.py"), run_name="__main__")
