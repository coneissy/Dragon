"""Binance Spot market-data engine with sharded WebSocket subscriptions.

Large universes are split across several connections. The feed is kept light
with top-5 partial depth at 100ms while a strict freshness gate remains in
place before any triangle is considered executable.
"""
import asyncio
import json
import random
import time
from decimal import Decimal
from typing import Dict

import websockets


class MarketData:
    def __init__(self, ws_base: str, symbols, depth_levels=5, stale_ms=1500):
        self.ws_base = ws_base.rstrip("/")
        self.symbols = tuple(sorted({str(s).upper() for s in symbols}))
        self.depth_levels = max(5, min(20, int(depth_levels)))
        self.stale_ms = max(250, int(stale_ms))
        self.books: Dict[str, dict] = {}
        self.last_message_ms = 0
        self.valid_updates = 0
        self.invalid_messages = 0
        self.connected = False
        self.connected_shards = 0
        self.total_shards = 0
        self.reconnects = 0
        self.disconnects = 0
        self._lock = asyncio.Lock()
        self._logged_first_snapshot = set()

    def _shards(self):
        shard_size = 40
        return [self.symbols[i:i + shard_size] for i in range(0, len(self.symbols), shard_size)]

    def _url(self, symbols):
        base = self.ws_base
        if base.endswith("/ws"):
            base = base[:-3]
        if not base.endswith("/stream"):
            base += "/stream"
        streams = "/".join(f"{s.lower()}@depth{self.depth_levels}@100ms" for s in symbols)
        return f"{base}?streams={streams}"

    def update_from_payload(self, payload):
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        symbol = str(data.get("s", "")).upper()
        bids = data.get("b")
        asks = data.get("a")
        if not symbol or not bids or not asks:
            self.invalid_messages += 1
            return False
        now = time.time_ns() // 1_000_000

        def clean(levels):
            out = []
            for level in levels[: self.depth_levels]:
                if len(level) < 2:
                    continue
                try:
                    p, q = Decimal(str(level[0])), Decimal(str(level[1]))
                except Exception:
                    continue
                if p > 0 and q > 0:
                    out.append((str(p), str(q)))
            return out

        b, a = clean(bids), clean(asks)
        if not b or not a:
            self.invalid_messages += 1
            return False
        self.books[symbol] = {
            "bids": b,
            "asks": a,
            "updated_ms": now,
            "_source": "binance_ws_partial_depth",
        }
        self.last_message_ms = now
        self.valid_updates += 1
        return True

    def fresh(self, symbol):
        book = self.books.get(symbol.upper())
        if not book:
            return False
        return (time.time_ns() // 1_000_000) - int(book["updated_ms"]) <= self.stale_ms

    def fresh_books(self, symbols):
        return all(self.fresh(s) for s in symbols)

    async def _run_shard(self, shard_id, symbols):
        attempt = 0
        while True:
            try:
                async with self._lock:
                    for symbol in symbols:
                        self.books.pop(symbol, None)
                async with websockets.connect(
                    self._url(symbols),
                    ping_interval=15,
                    ping_timeout=10,
                    close_timeout=5,
                    open_timeout=15,
                    max_size=2**20,
                    max_queue=4096,
                    compression=None,
                ) as ws:
                    attempt = 0
                    async with self._lock:
                        self.connected_shards += 1
                        self.connected = self.connected_shards > 0
                    print(
                        f"DRAGON WS | shard={shard_id} connected symbols={len(symbols)} "
                        f"connected_shards={self.connected_shards}/{self.total_shards} "
                        f"depth={self.depth_levels}", flush=True,
                    )
                    async for raw in ws:
                        try:
                            payload = json.loads(raw)
                            if self.update_from_payload(payload) and shard_id not in self._logged_first_snapshot:
                                self._logged_first_snapshot.add(shard_id)
                                print(
                                    f"DRAGON WS_DEPTH | shard={shard_id} first valid snapshot "
                                    f"symbol={payload.get('data', {}).get('s', '?')} "
                                    f"updates={self.valid_updates}", flush=True,
                                )
                        except Exception as exc:
                            self.invalid_messages += 1
                            print(f"DRAGON WS_PARSE | shard={shard_id} error={exc!s}", flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.reconnects += 1
                self.disconnects += 1
                delay = min(30.0, 2 ** min(attempt, 5)) + random.uniform(0, 0.5)
                attempt += 1
                print(
                    f"DRAGON WS | shard={shard_id} disconnected reason={exc!s} "
                    f"reconnect_in={delay:.2f}s", flush=True,
                )
                await asyncio.sleep(delay)
            finally:
                async with self._lock:
                    if self.connected_shards > 0:
                        self.connected_shards -= 1
                    self.connected = self.connected_shards > 0
                    for symbol in symbols:
                        self.books.pop(symbol, None)

    async def run(self):
        if not self.symbols:
            raise RuntimeError("no Binance symbols selected for market data")
        shards = self._shards()
        self.total_shards = len(shards)
        tasks = [asyncio.create_task(self._run_shard(i + 1, shard)) for i, shard in enumerate(shards)]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
