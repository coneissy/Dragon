from collections import Counter, deque
from threading import Lock
import time


class Telemetry:
    def __init__(self, recent_limit=200):
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

    def snapshot(self):
        with self._lock:
            return {"counters": dict(self.counters), "recent": list(self.recent)}
