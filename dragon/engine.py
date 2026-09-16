"""Compatibility entry point for Render.

The Dragon engine lives in src.dragon.main. This module intentionally contains
no trading logic so there is only one runtime engine.
"""
from src.dragon.main import main


if __name__ == "__main__":
    main()
