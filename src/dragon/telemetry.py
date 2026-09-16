"""Single decision telemetry store.

Every candidate is recorded with explicit mathematical values and, when
rejected, a machine-readable reason. This replaces scattered logging paths.
"""
from collections import Counter, deque
from threading import Lock
import time


class Telemetry:
    def __init__(self, recent_limit=500):
        self._lock = Lock()
        self.counters = Counter()
        self.recent = deque(maxlen=recent_limit)

    def record(self, kind, message, **data):
        item = {"ts": time.time(), "kind": kind, "message": message, **data}
        with self._lock:
            self.counters[kind] += 1
            self.recent.append(item)
        print(f"DRAGON {kind} | {message}", flush=True)
        return item

    def candidate(self, path, *, status, reason=None, result=None, notional=None):
        data = {"path": list(path), "status": status, "reason": reason, "notional_usdt": str(notional) if notional is not None else None}
        if result is not None:
            for name in ("gross_bps", "net_bps", "fee_drag_bps", "depth_drag_bps", "safety_bps", "gross_pnl_usdt", "net_pnl_usdt", "break_even_gross_bps"):
                value = getattr(result, name, None)
                data[name] = str(value) if value is not None else None
        return self.record("CANDIDATE", f"{status} {reason or ''}".strip(), **data)

    def snapshot(self):
        with self._lock:
            return {"counters": dict(self.counters), "recent": list(self.recent)}
