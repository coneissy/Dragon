import asyncio
import json
import random
import time
from decimal import Decimal


class _RateLimitedConnect:
    """Async websocket proxy that paces Binance client control messages."""
    def __init__(self, connect_factory, args, kwargs):
        self._connect_factory = connect_factory
        self._args = args
        self._kwargs = kwargs
        self._connection = None
        self._last_control_send = 0.0

    def __await__(self):
        return self._wait().__await__()

    async def _wait(self):
        self._connection = await self._connect_factory(*self._args, **self._kwargs)
        return self

    async def __aenter__(self):
        self._connection = await self._connect_factory(*self._args, **self._kwargs)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return await self._connection.__aexit__(exc_type, exc, tb)

    async def send(self, message):
        is_control = False
        if isinstance(message, str):
            try:
                payload = json.loads(message)
                is_control = isinstance(payload, dict) and isinstance(payload.get("method"), str)
            except (TypeError, ValueError):
                pass
        if is_control:
            now = time.monotonic()
            wait = 0.26 - (now - self._last_control_send)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_control_send = time.monotonic()
        return await self._connection.send(message)

    def __getattr__(self, name):
        if self._connection is None:
            raise AttributeError(name)
        return getattr(self._connection, name)


def main():
    from src.dragon import main as dragon_main
    from src.dragon.hardening import install
    from src.dragon.dashboard import HTML as DASHBOARD_HTML
    import full_universe_runner_v3
    import web_runner
    import websockets
    from src.dragon.triangles import evaluate_triangle_outcome

    async def repaired_feed_worker(cfg, symbols, queue, worker_id):
        """Consume Binance partial-depth snapshots through a combined market-data stream."""
        streams = [f"{s.lower()}@depth{cfg.depth_levels}@100ms" for s in symbols]
        if not streams:
            return
        base = cfg.ws_base.rstrip("/")
        if base.endswith("/ws"):
            base = base[:-3]
        if not base.endswith("/stream"):
            base += "/stream"
        url = base + "?streams=" + "/".join(streams)
        delay = 1.0
        raw_connect = websockets.connect
        while True:
            try:
                full_universe_runner_v3._ws_state(worker_id, status="connecting")
                async with raw_connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    open_timeout=15,
                    max_size=2**24,
                    max_queue=4096,
                    compression=None,
                ) as ws:
                    delay = 1.0
                    full_universe_runner_v3._ws_state(worker_id, status="connected")
                    web_runner.event("WS", f"Spot shard {worker_id} connected; combined partial-depth stream active", symbols=len(symbols), streams=len(streams), endpoint=url.split("?", 1)[0])
                    last_message = time.monotonic()
                    first_depth_seen = False
                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                        except asyncio.TimeoutError as exc:
                            raise ConnectionError(f"market-data stream silent for {time.monotonic() - last_message:.1f}s") from exc
                        last_message = time.monotonic()
                        full_universe_runner_v3._metric("ws_raw_messages")
                        try:
                            payload = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
                        except Exception:
                            full_universe_runner_v3._metric("ws_invalid_messages")
                            continue
                        if isinstance(payload, dict) and "result" in payload and "id" in payload:
                            web_runner.event("WS_ACK", f"Spot shard {worker_id} market stream control response", shard=worker_id, result=payload.get("result"), request_id=payload.get("id"))
                            continue
                        if isinstance(payload, dict):
                            envelope = payload.get("data", payload)
                            stream_name = str(payload.get("stream", ""))
                            if isinstance(envelope, dict) and not envelope.get("s") and stream_name:
                                symbol = stream_name.split("@", 1)[0].upper()
                                bids = envelope.get("bids", envelope.get("b", []))
                                asks = envelope.get("asks", envelope.get("a", []))
                                payload = dict(payload)
                                payload["data"] = dict(envelope)
                                payload["data"]["s"] = symbol
                                payload["data"]["b"] = bids
                                payload["data"]["a"] = asks
                                raw = json.dumps(payload, separators=(",", ":"))
                        data = full_universe_runner_v3._parse_depth(raw, cfg.depth_levels)
                        if not data:
                            continue
                        if not first_depth_seen:
                            first_depth_seen = True
                            web_runner.event("WS_DEPTH", f"Spot shard {worker_id} first valid partial-depth snapshot", shard=worker_id, symbol=data.get("s"), bids=len(data.get("b", [])), asks=len(data.get("a", [])))
                        with web_runner.LOCK:
                            web_runner.STATE["last_ws_depth_update"] = time.time()
                            web_runner.STATE["market_data_updates"] = web_runner.STATE.get("market_data_updates", 0) + 1
                        if queue.full():
                            try:
                                queue.get_nowait()
                                full_universe_runner_v3._metric("market_queue_drops")
                            except asyncio.QueueEmpty:
                                pass
                        await queue.put(data)
            except asyncio.CancelledError:
                full_universe_runner_v3._ws_state(worker_id, status="stopped")
                raise
            except Exception as exc:
                with web_runner.LOCK:
                    web_runner.STATE["ws_disconnects"] = web_runner.STATE.get("ws_disconnects", 0) + 1
                full_universe_runner_v3._ws_state(worker_id, status="reconnecting", ws_last_disconnect=time.time())
                web_runner.event("WS_ERROR", f"Spot shard {worker_id} disconnected: {exc}", shard=worker_id)
                wait = min(60.0, delay + random.uniform(0, min(5.0, delay * 0.25)))
                full_universe_runner_v3._ws_state(worker_id, ws_next_retry_at=time.time() + wait)
                await asyncio.sleep(wait)
                delay = min(60.0, delay * 2.0)
                full_universe_runner_v3._metric("ws_reconnects")

    full_universe_runner_v3._feed_worker = repaired_feed_worker
    dragon_main.stream_loop = full_universe_runner_v3.full_universe_stream_loop

    def hardened_web_evaluate(t, books, fee_bps, slippage_bps, symbol_meta=None, notional_usdt=1.0):
        with dragon_main.LOCK:
            dynamic_fee = dragon_main.STATE.get("dynamic_fee_bps")
        effective_fee = Decimal(str(dynamic_fee)) if dynamic_fee not in (None, "", 0, 0.0) else Decimal(str(fee_bps))

        # This executable-depth projection is authoritative for the scanner.
        # It keeps the displayed model and the execution gate on the same math.
        outcome = evaluate_triangle_outcome(t, books, effective_fee, slippage_bps, symbol_meta, notional_usdt)
        if not outcome:
            with web_runner.LOCK:
                web_runner.STATE.setdefault("evaluation_rejections", {})
                web_runner.STATE["evaluation_rejections"]["NO_EXECUTABLE_DEPTH"] = web_runner.STATE["evaluation_rejections"].get("NO_EXECUTABLE_DEPTH", 0) + 1
            return None

        result = (
            outcome["net_bps"],
            outcome["gross_bps"],
            outcome["path"],
            outcome["first_asset"],
            outcome["second_asset"],
        )
        with web_runner.LOCK:
            diagnostics = web_runner.STATE.setdefault("calculation_diagnostics", {})
            diagnostics["evaluations"] = diagnostics.get("evaluations", 0) + 1
            diagnostics["last_update"] = time.time()
            diagnostics["positive_gross"] = diagnostics.get("positive_gross", 0) + int(outcome["gross_bps"] > 0)
            diagnostics["negative_gross"] = diagnostics.get("negative_gross", 0) + int(outcome["gross_bps"] <= 0)
            diagnostics["positive_net"] = diagnostics.get("positive_net", 0) + int(outcome["net_bps"] > 0)
            diagnostics["negative_net"] = diagnostics.get("negative_net", 0) + int(outcome["net_bps"] <= 0)
            diagnostics["top_book_positive_executable_negative"] = diagnostics.get("top_book_positive_executable_negative", 0) + int(outcome["top_of_book_gross_bps"] > 0 and outcome["gross_bps"] <= 0)
            diagnostics["gross_positive_net_negative"] = diagnostics.get("gross_positive_net_negative", 0) + int(outcome["gross_bps"] > 0 and outcome["net_bps"] <= 0)
            diagnostics["fee_drag_bps"] = float(outcome["fee_drag_bps"])
            diagnostics["break_even_gross_bps"] = float(outcome["break_even_gross_bps"])
            diagnostics["last_path"] = list(outcome["path"])
            web_runner.STATE["last_projection"] = {
                "ts": time.time(),
                "path": list(outcome["path"]),
                "start_usdt": str(outcome["start_usdt"]),
                "gross_final": str(outcome["gross_final"]),
                "gross_pnl_usdt": str(outcome["gross_pnl_usdt"]),
                "gross_bps": float(outcome["gross_bps"]),
                "top_of_book_gross_bps": float(outcome["top_of_book_gross_bps"]),
                "depth_adjusted_gross_bps": float(outcome["depth_adjusted_gross_bps"]),
                "post_fee_final": str(outcome["post_fee_final"]),
                "net_pnl_before_safety_usdt": str(outcome["net_pnl_before_safety_usdt"]),
                "fee_bps_per_leg": float(outcome["fee_bps_per_leg"]),
                "fee_drag_bps": float(outcome["fee_drag_bps"]),
                "total_fee_equivalent": str(outcome["total_fee_equivalent"]),
                "depth_drag_bps": float(outcome["depth_drag_bps"]),
                "safety_bps": float(outcome["safety_bps"]),
                "safety_cost_usdt": str(outcome["safety_cost_usdt"]),
                "break_even_gross_bps": float(outcome["break_even_gross_bps"]),
                "cost_to_break_even_bps": float(outcome["cost_to_break_even_bps"]),
                "final_usdt": str(outcome["final_usdt"]),
                "net_pnl_usdt": str(outcome["net_pnl_usdt"]),
                "net_bps": float(outcome["net_bps"]),
                "legs": outcome["legs"],
            }
        return result

    web_runner.evaluate_triangle = hardened_web_evaluate

    original_web_execute = web_runner.execute_triangle

    def hardened_web_execute(client, path, start_asset, first_asset, budget, filters, dry_run):
        cfg = dragon_main.Config.from_env()
        if cfg.live_trading and not cfg.dry_run:
            account = client.account()
            free_usdt = Decimal(str(next((x.get("free", "0") for x in account.get("balances", []) if x.get("asset") == "USDT"), "0")))
            reserve = Decimal(str(cfg.safety_reserve_usdt))
            if free_usdt - reserve < Decimal(str(budget)):
                with dragon_main.LOCK:
                    dragon_main.STATE.setdefault("rejection", {})
                    dragon_main.STATE["rejection"]["FINAL_BALANCE_REJECTED"] = dragon_main.STATE["rejection"].get("FINAL_BALANCE_REJECTED", 0) + 1
                dragon_main.event("GATE", "Final balance recheck blocked order", free_usdt=str(free_usdt), reserve=str(reserve), budget=str(budget))
                raise RuntimeError("final balance recheck failed: executable balance below budget plus safety reserve")
        return original_web_execute(client, path, start_asset, first_asset, budget, filters, dry_run)

    web_runner.execute_triangle = hardened_web_execute

    install(dragon_main)
    dragon_main.DASHBOARD = DASHBOARD_HTML

    original_connect = websockets.connect

    def resilient_connect(*args, **kwargs):
        kwargs["ping_interval"] = None
        kwargs["ping_timeout"] = 30
        kwargs["close_timeout"] = 5
        return _RateLimitedConnect(original_connect, args, kwargs)

    websockets.connect = resilient_connect

    original_get = dragon_main.Handler.do_GET

    def dashboard_root(self):
        if self.path == "/":
            self.path = "/dashboard"
            try:
                return original_get(self)
            finally:
                self.path = "/"
        return original_get(self)

    dragon_main.Handler.do_GET = dashboard_root

    original_evaluate = dragon_main.evaluate_triangle
    last_signal = {}

    def instrumented_evaluate(*args, **kwargs):
        result = original_evaluate(*args, **kwargs)
        try:
            with dragon_main.LOCK:
                dragon_main.STATE.setdefault("rejection", {})
                dragon_main.STATE.setdefault("rejection_total", 0)
                dragon_main.STATE["rejection_total"] += 1
                if result is None:
                    key = "NO_EXECUTABLE_DEPTH"
                else:
                    net_bps, _gross_bps, path, _first, _second = result
                    cfg = dragon_main.Config.from_env()
                    key = "NET_EDGE_REJECTED" if Decimal(str(net_bps)) < Decimal(str(cfg.min_net_edge_bps)) else "NET_EDGE_PASSED"
                    if key == "NET_EDGE_PASSED":
                        last_signal[tuple(path)] = (time.monotonic() * 1000, Decimal(str(net_bps)))
                dragon_main.STATE["rejection"][key] = dragon_main.STATE["rejection"].get(key, 0) + 1
        except Exception:
            pass
        return result

    dragon_main.evaluate_triangle = instrumented_evaluate

    original_approved = dragon_main.approved

    def guarded_approved(net_bps, min_net_bps, notional, max_notional, *, min_trade_notional=None):
        if not original_approved(net_bps, min_net_bps, notional, max_notional, min_trade_notional=min_trade_notional):
            try:
                with dragon_main.LOCK:
                    dragon_main.STATE.setdefault("rejection", {})
                    dragon_main.STATE["rejection"]["RISK_OR_NOTIONAL"] = dragon_main.STATE["rejection"].get("RISK_OR_NOTIONAL", 0) + 1
                dragon_main.event("RISK", "Trade blocked by risk/notional gate")
            except Exception:
                pass
            return False
        cfg = dragon_main.Config.from_env()
        expected_profit = Decimal(str(notional)) * Decimal(str(net_bps)) / Decimal("10000")
        minimum_profit = Decimal(str(cfg.min_expected_profit_usdt))
        if expected_profit < minimum_profit:
            try:
                with dragon_main.LOCK:
                    dragon_main.STATE.setdefault("rejection", {})
                    dragon_main.STATE["rejection"]["EXPECTED_PROFIT_REJECTED"] = dragon_main.STATE["rejection"].get("EXPECTED_PROFIT_REJECTED", 0) + 1
                dragon_main.event("GATE", "Trade blocked: expected profit below minimum", expected_profit=float(expected_profit), minimum_profit=float(minimum_profit), net_bps=float(net_bps))
            except Exception:
                pass
            return False
        return True

    dragon_main.approved = guarded_approved

    original_execute = dragon_main.execute_triangle

    def guarded_execute(client, path, start_asset, first_asset, budget, filters, dry_run):
        cfg = dragon_main.Config.from_env()
        now_ms = time.monotonic() * 1000
        signal = last_signal.get(tuple(path))
        max_age_ms = min(float(cfg.stale_ms), 250.0)
        if signal is None or now_ms - signal[0] > max_age_ms:
            try:
                with dragon_main.LOCK:
                    dragon_main.STATE.setdefault("rejection", {})
                    dragon_main.STATE["rejection"]["FINAL_SIGNAL_STALE"] = dragon_main.STATE["rejection"].get("FINAL_SIGNAL_STALE", 0) + 1
                dragon_main.event("GATE", "Final signal freshness check blocked order", age_ms=float(now_ms - signal[0]) if signal else None, max_age_ms=max_age_ms)
            except Exception:
                pass
            raise RuntimeError("final signal freshness check failed")
        if not (cfg.live_trading and not cfg.dry_run):
            return original_execute(client, path, start_asset, first_asset, budget, filters, dry_run)
        account = client.account()
        free_usdt = Decimal(str(next((x.get("free", "0") for x in account.get("balances", []) if x.get("asset") == "USDT"), "0")))
        reserve = Decimal(str(cfg.safety_reserve_usdt))
        if free_usdt - reserve < Decimal(str(budget)):
            try:
                with dragon_main.LOCK:
                    dragon_main.STATE.setdefault("rejection", {})
                    dragon_main.STATE["rejection"]["FINAL_BALANCE_REJECTED"] = dragon_main.STATE["rejection"].get("FINAL_BALANCE_REJECTED", 0) + 1
                    dragon_main.STATE["free_usdt"] = str(free_usdt)
                dragon_main.event("GATE", "Final balance recheck blocked order", free_usdt=str(free_usdt), reserve=str(reserve), budget=str(budget))
            except Exception:
                pass
            raise RuntimeError("final balance recheck failed: executable balance below budget plus safety reserve")
        return original_execute(client, path, start_asset, first_asset, budget, filters, dry_run)

    dragon_main.execute_triangle = guarded_execute

    asyncio.run(dragon_main.run())


if __name__ == "__main__":
    main()
