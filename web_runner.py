"""Deprecated dashboard compatibility module.

The production HTTP server and state are owned by ``src.dragon.main``.
Legacy imports are retained only so old tooling fails gracefully rather than
starting a second trading engine.
"""
from threading import Lock

LOCK = Lock()
STATE = {}


def event(kind, message, **data):
    print(f"DRAGON {kind} | {message}", flush=True)
    return {"kind": kind, "message": message, **data}


def main():
    from src.dragon.main import main as dragon_main
    return dragon_main()


if __name__ == "__main__":
    main()
