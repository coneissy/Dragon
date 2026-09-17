"""Single-owner Binance Spot market-data engine with resilient WS sharding."""
import asyncio
import json
import random
import time
from decimal import Decimal, InvalidOperation
from typing import Dict

import httpx
import websockets


class MarketData:
    def __init__(self, ws_base: str, symbols, depth_levels=5, stale_ms=1500, api_base="https://api.binance.com", shard_size=40):
        self.ws_base = ws_base.rstrip("/")
        self.api_base = api_base.rstrip("/")
        self.symbols = tuple(sorted({str(s).strip().upper() for s in symbols if str(s).strip()}))
        self.depth_levels = max(5, min(20, int(depth_levels)))
        self.stale_ms = max(250, int(stale_ms))
        self.shard_size = max(1, min(100, int(shard_size)))
        self.books: Dict[str, dict] = {}
        self.last_message_ms = 0
        self.valid_updates = 0
        self.invalid_messages = 0
        self.rejected_updates = 0
        self.connected = False
        self.connected_shards = 0
        self.total_shards = 0
        self.reconnects = 0
        self.disconnects = 0
        self.bootstrap_ok = 0
        self.bootstrap_failed = 0
        self.ws_messages = 0
        self._connected_ids = set()
        self._lock = asyncio.Lock()
        self._logged_first_snapshot = set()
        self._logged_first_message = set()

    def _shards(self):
        return [self.symbols[i:i + self.shard_size] for i in range(0, len(self.symbols), self.shard_size)]

    def _url(self, symbols):
        base = self.ws_base
        if base.endswith("/ws"):
            base = base[:-3]
        if not base.endswith("/stream"):
            base += "/stream"
        streams = "/".join(f"{s.lower()}@depth{self.depth_levels}@100ms" for s in symbols)
        return f"{base}?streams={streams}"

    @staticmethod
    def _clean_levels(levels, limit):
        out = []
        for level in (levels or [])[:limit]:
            if not isinstance(level, (list, tuple)) or len(level) < 2:
                continue
            try:
                price = Decimal(str(level[0]))
                quantity = Decimal(str(level[1]))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if price.is_finite() and quantity.is_finite() and price > 0 and quantity > 0:
                out.append((str(price), str(quantity)))
        return out

    @staticmethod
    def _best_price(levels):
        return Decimal(str(levels[0][0])) if levels else None

    def _is_valid_book(self, bids, asks):
        bid = self._best_price(bids)
        ask = self._best_price(asks)
        return bid is not None and ask is not None and bid < ask

    def _accept(self, symbol, bids, asks, event_ts, update_id, source):
        if not self._is_valid_book(bids, asks):
            self.rejected_updates += 1
            return False
        previous = self.books.get(symbol)
        previous_id = previous.get("last_update_id") if previous else None
        if update_id is not None and previous_id is not None:
            try:
                if int(update_id) < int(previous_id):
                    self.rejected_updates += 1
                    return False
            except (TypeError, ValueError):
                pass
        now = time.time_ns() // 1_000_000
        self.books[symbol] = {
            "bids": bids,
            "asks": asks,
            "updated_ms": now,
            "depth_ts": now,
            "event_ts": int(event_ts),
            "last_update_id": update_id,
            "_source": source,
        }
        self.last_message_ms = now
        self.valid_updates += 1
        return True

    def update_from_payload(self, payload):
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        symbol = str(data.get("s", "")).upper()
        if not symbol or symbol not in self.symbols:
            self.invalid_messages += 1
            return False
        bids = self._clean_levels(data.get("b"), self.depth_levels)
        asks = self._clean_levels(data.get("a"), self.depth_levels)
        if not bids or not asks:
            self.invalid_messages += 1
            return False
        now = time.time_ns() // 1_000_000
        return self._accept(symbol, bids, asks, data.get("E", now), data.get("u"), "binance_ws_depth")

    def _update_rest_book(self, symbol, payload):
        symbol = str(symbol).upper()
        if symbol not in self.symbols or not isinstance(payload, dict):
            self.bootstrap_failed += 1
            return False
        bids = self._clean_levels(payload.get("bids") or payload.get("b"), self.depth_levels)
        asks = self._clean_levels(payload.get("asks") or payload.get("a"), self.depth_levels)
        if not bids or not asks:
            return False
        return self._accept(symbol, bids, asks, time.time_ns() // 1_000_000, payload.get("lastUpdateId"), "binance_rest_depth_bootstrap")

    async def bootstrap(self):
        sem = asyncio.Semaphore(12)
        timeout = httpx.Timeout(5.0, connect=3.0)
        limits = httpx.Limits(max_connections=16, max_keepalive_connections=8)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            async def fetch(symbol):
                async with sem:
                    try:
                        response = await client.get(self.api_base + "/api/v3/depth", params={"symbol": symbol, "limit": self.depth_levels})
                        response.raise_for_status()
                        if not self._update_rest_book(symbol, response.json()):
                            self.bootstrap_failed += 1
                            return False
                        self.bootstrap_ok += 1
                        return True
                    except Exception as exc:
                        self.bootstrap_failed += 1
                        print(f"DRAGON REST_BOOTSTRAP | symbol={symbol} error={exc!s}", flush=True)
                        return False
            results = await asyncio.gather(*(fetch(symbol) for symbol in self.symbols))
        print(f"DRAGON REST_BOOTSTRAP | ok={sum(bool(x) for x in results)} failed={self.bootstrap_failed} symbols={len(self.symbols)}", flush=True)

    def fresh(self, symbol):
        book = self.books.get(str(symbol).upper())
        if not book:
            return False
        age = (time.time_ns() // 1_000_000) - int(book.get("updated_ms", 0))
        return 0 <= age <= self.stale_ms and self._is_valid_book(book.get("bids", []), book.get("asks", []))

    def fresh_books(self, symbols):
        normalized = tuple(dict.fromkeys(str(s).upper() for s in symbols))
        return bool(normalized) and all(self.fresh(s) for s in normalized)

    def snapshot(self, symbols):
        normalized = tuple(dict.fromkeys(str(s).upper() for s in symbols))
        if not normalized or not self.fresh_books(normalized):
            return {}
        return {s: dict(self.books[s]) for s in normalized}

    def stale_symbols(self, symbols):
        now = time.time_ns() // 1_000_000
        out = []
        for symbol in dict.fromkeys(str(s).upper() for s in symbols):
            book = self.books.get(symbol)
            if not book:
                out.append(symbol)
                continue
            age = now - int(book.get("updated_ms", 0))
            if age < 0 or age > self.stale_ms or not self._is_valid_book(book.get("bids", []), book.get("asks", [])):
                out.append(f"{symbol}:{max(age, 0)}ms")
        return out

    async def _run_shard(self, shard_id, symbols):
        attempt = 0
        while True:
            connected_here = False
            try:
                async with websockets.connect(
                    self._url(symbols), ping_interval=10, ping_timeout=10,
                    close_timeout=5, open_timeout=15, max_size=2**20,
                    max_queue=8192, compression=None,
                ) as ws:
                    connected_here = True
                    self._connected_ids.add(shard_id)
                    self.connected_shards = len(self._connected_ids)
                    self.connected = self.connected_shards == self.total_shards
                    attempt = 0
                    print(f"DRAGON WS | shard={shard_id} connected symbols={len(symbols)} connected_shards={self.connected_shards}/{self.total_shards} depth={self.depth_levels}", flush=True)
                    async for raw in ws:
                        self.ws_messages += 1
                        try:
                            payload = json.loads(raw)
                            data = payload.get("data", payload) if isinstance(payload, dict) else {}
                            if shard_id not in self._logged_first_message:
                                self._logged_first_message.add(shard_id)
                                print(f"DRAGON WS_MSG | shard={shard_id} first_message event={data.get('e', '?')} symbol={data.get('s', '?')}", flush=True)
                            if self.update_from_payload(payload) and shard_id not in self._logged_first_snapshot:
                                self._logged_first_snapshot.add(shard_id)
                                print(f"DRAGON WS_DEPTH | shard={shard_id} first valid snapshot symbol={data.get('s', '?')} updates={self.valid_updates}", flush=True)
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
                print(f"DRAGON WS | shard={shard_id} disconnected reason={exc!s} reconnect_in={delay:.2f}s", flush=True)
                await asyncio.sleep(delay)
            finally:
                if connected_here:
                    self._connected_ids.discard(shard_id)
                    self.connected_shards = len(self._connected_ids)
                    self.connected = self.connected_shards == self.total_shards and self.total_shards > 0

    async def run(self):
        if not self.symbols:
            raise RuntimeError("no Binance symbols selected for market data")
        shards = self._shards()
        self.total_shards = len(shards)
        await self.bootstrap()
        tasks = [asyncio.create_task(self._run_shard(i + 1, shard)) for i, shard in enumerate(shards)]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._connected_ids.clear()
            self.connected_shards = 0
            self.connected = False
