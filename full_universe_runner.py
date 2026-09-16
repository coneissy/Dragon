"""Deprecated compatibility shim.

The MAX UNIVERSE scanner is now implemented only in ``src.dragon.main``.
This file intentionally contains no scanner, calculator, or execution logic.
"""
from src.dragon.main import main


def run(*_args, **_kwargs):
    return main()
