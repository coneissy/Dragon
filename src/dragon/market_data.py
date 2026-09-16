"""Single Binance Spot market-data engine.

Uses partial-depth snapshots rather than mixing several competing WS engines.
A reconnect clears the in-memory books; trading resumes only after fresh books
arrive, which prevents stale data from being treated as executable.
"""
import asyncio
import json
import random
import time
from decimal import Decimal
from typing import Dict

import websockets


class MarketData:
    def __init__(self, ws_base: str, symbols, depth_levels=20, stale_ms=500):
        self.ws_base = ws_base.rstrip("/")
        self.symbols = tuple(sorted({str(s).upper() for s in symbols}))
        self.depth_levels = max(5, min(20, int(depth_levels)))
        self.stale_ms = max(50, int(stale_ms))
        self.books: Dict[str, dict] = {}
        self.last_message_ms = 0
        self.connected = False
        self.reconnects = 0

    def _url(self):
        base = self.ws_base
        if base.endswith("/ws"):
            base = base[:-3]
        if not base.endswith("/stream"):
            base += "/stream"
        streams = "/".join(f"{s.lower()}@depth{self.depth_levels}@100ms" for s in self.symbols)
        return f"{base}?streams={streams}"

    def update_from_payload(self, payload):
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        symbol = str(data.get("s", "")).upper()
        bids = data.get("b")
        asks = data.get("a")
        if not symbol or not bids or not asks:
            return False
        now = time.time_ns() // 1_000_000
        def clean(levels):
            out = []
            for level in levels[: self.depth_levels]:
                if len(level) < 2:
                    continue
                p, q = Decimal(str(level[0])), Decimal(str(level[1]))
                if p > 0 and q > 0:
                    out.append((str(p), str(q)))
            return out
        b, a = clean(bids), clean(asks)
        if not b or not a:
            return False
        self.books[symbol] = {"bids": b, "asks": a, "updated_ms": now, "_source": "binance_ws_partial_depth"}
        self.last_message_ms = now
        return True

    def fresh(self, symbol):
        book = self.books.get(symbol.upper())
        if not book:
            return False
        return (time.time_ns() // 1_000_000) - int(book["updated_ms"]) <= self.stale_ms

    def fresh_books(self, symbols):
        return all(self.fresh(s) for s in symbols)

    async def run(self):
        if not self.symbols:
            raise RuntimeError("no Binance symbols selected for market data")
        attempt = 0
        while True:
            try:
                self.books.clear()
                self.connected = False
                async with websockets.connect(
                    self._url(), ping_interval=20, ping_timeout=20,
                    close_timeout=5, open_timeout=15, max_size=2**20,
                    max_queue=4096, compression=None,
                ) as ws:
                    self.connected = True
                    attempt = 0
                    print(f"DRAGON WS | connected symbols={len(self.symbols)}", flush=True)
                    async for raw in ws:
                        payload = json.loads(raw)
                        self.update_from_payload(payload)
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception as exc:
                self.connected = False
                self.reconnects += 1
                delay = min(30.0, 2 ** min(attempt, 5)) + random.uniform(0, 0.5)
                attempt += 1
                print(f"DRAGON WS | disconnected reason={exc!s} reconnect_in={delay:.2f}s", flush=True)
                await asyncio.sleep(delay)
