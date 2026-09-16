"""Dashboard compatibility module.

``dashboard.html`` is the single UI source and ``src.dragon.main`` is the
single HTTP server. This module no longer owns a second dashboard state store.
"""
from pathlib import Path

HTML = (Path(__file__).resolve().parents[2] / "dashboard.html").read_text(encoding="utf-8")
