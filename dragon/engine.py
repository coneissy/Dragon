"""Render entry point for the unified Dragon engine and MAX connectivity supervisor."""
import threading

from src.dragon.connectivity import run_connectivity_loop
from src.dragon.main import main


def _connectivity_worker() -> None:
    try:
        run_connectivity_loop()
    except Exception as exc:
        print(f"DRAGON CONNECTIVITY | supervisor stopped: {exc!s}", flush=True)


if __name__ == "__main__":
    threading.Thread(target=_connectivity_worker, name="dragon-connectivity", daemon=True).start()
    main()
