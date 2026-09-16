"""Deprecated compatibility shim for the old v3 scanner."""
from src.dragon.main import main


def run(*_args, **_kwargs):
    return main()
