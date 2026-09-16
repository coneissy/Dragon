import asyncio
import time
from decimal import Decimal


class _RateLimitedConnect:
    """Async websocket proxy that paces Binance client control messages."""
    def __init__(self, connect_factory, args, kwargs):
        self._connect_factory = connect_factory; self._args = args; self._kwargs = kwargs; self._connection = None; self._last_control_send = 0.0
    def __await__(self): return self._wait().__await__()
    async def _wait(self): self._connection = await self._connect_factory(*self._args, **self._kwargs); return self
    async def __aenter__(self): self._connection = await self._connect_factory(*self._args, **self._kwargs); return self
    async def __aexit__(self, exc_type, exc, tb): return await self._connection.__aexit__(exc_type, exc, tb)
    async def send(self, message):
        is_control = False
        if isinstance(message, str):
            try:
                import json
                payload = json.loads(message); is_control = isinstance(payload, dict) and isinstance(payload.get("method"), str)
            except (TypeError, ValueError): pass
        if is_control:
            now = time.monotonic(); wait = 0.26 - (now - self._last_control_send)
            if wait > 0: await asyncio.sleep(wait)
            self._last_control_send = time.monotonic()
        return await self._connection.send(message)
    def __getattr__(self, name):
        if self._connection is None: raise AttributeError(name)
        return getattr(self._connection, name)


def main():
    from src.dragon import main as dragon_main
    from src.dragon.hardening import install
    from src.dragon.dashboard import HTML as DASHBOARD_HTML
    from full_universe_runner_v3 import full_universe_stream_loop
    import web_runner
    import websockets
    from src.dragon.triangles import evaluate_triangle_outcome

    dragon_main.stream_loop = full_universe_stream_loop

    original_web_evaluate = web_runner.evaluate_triangle

    def hardened_web_evaluate(t, books, fee_bps, slippage_bps, symbol_meta=None, notional_usdt=1.0):
        with dragon_main.LOCK:
            dynamic_fee = dragon_main.STATE.get("dynamic_fee_bps")
        effective_fee = Decimal(str(dynamic_fee)) if dynamic_fee not in (None, "", 0, 0.0) else Decimal(str(fee_bps))
        outcome = evaluate_triangle_outcome(t, books, effective_fee, slippage_bps, symbol_meta, notional_usdt)
        if outcome:
            with web_runner.LOCK:
                web_runner.STATE["last_projection"] = {
                    "ts": time.time(),
                    "path": list(outcome["path"]),
                    "start_usdt": str(outcome["start_usdt"]),
                    "gross_final": str(outcome["gross_final"]),
                    "gross_pnl_usdt": str(outcome["gross_pnl_usdt"]),
                    "gross_bps": float(outcome["gross_bps"]),
                    "post_fee_final": str(outcome["post_fee_final"]),
                    "total_fee_equivalent": str(outcome["total_fee_equivalent"]),
                    "safety_bps": float(outcome["safety_bps"]),
                    "safety_cost_usdt": str(outcome["safety_cost_usdt"]),
                    "final_usdt": str(outcome["final_usdt"]),
                    "net_pnl_usdt": str(outcome["net_pnl_usdt"]),
                    "net_bps": float(outcome["net_bps"]),
                    "legs": outcome["legs"],
                }
        return original_web_evaluate(t, books, effective_fee, slippage_bps, symbol_meta, notional_usdt)

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
        kwargs["ping_interval"] = None; kwargs["ping_timeout"] = 30; kwargs["close_timeout"] = 5
        return _RateLimitedConnect(original_connect, args, kwargs)
    websockets.connect = resilient_connect

    original_get = dragon_main.Handler.do_GET
    def dashboard_root(self):
        if self.path == "/":
            self.path = "/dashboard"
            try: return original_get(self)
            finally: self.path = "/"
        return original_get(self)
    dragon_main.Handler.do_GET = dashboard_root

    original_evaluate = dragon_main.evaluate_triangle
    last_signal = {}
    def instrumented_evaluate(*args, **kwargs):
        result = original_evaluate(*args, **kwargs)
        try:
            with dragon_main.LOCK:
                dragon_main.STATE.setdefault("rejection", {}); dragon_main.STATE.setdefault("rejection_total", 0); dragon_main.STATE["rejection_total"] += 1
                if result is None: key = "NO_EXECUTABLE_DEPTH"
                else:
                    net_bps, _gross_bps, path, _first, _second = result; cfg = dragon_main.Config.from_env()
                    key = "NET_EDGE_REJECTED" if Decimal(str(net_bps)) < Decimal(str(cfg.min_net_edge_bps)) else "NET_EDGE_PASSED"
                    if key == "NET_EDGE_PASSED": last_signal[tuple(path)] = (time.monotonic() * 1000, Decimal(str(net_bps)))
                dragon_main.STATE["rejection"][key] = dragon_main.STATE["rejection"].get(key, 0) + 1
        except Exception: pass
        return result
    dragon_main.evaluate_triangle = instrumented_evaluate

    original_approved = dragon_main.approved
    def guarded_approved(net_bps, min_net_bps, notional, max_notional, *, min_trade_notional=None):
        if not original_approved(net_bps, min_net_bps, notional, max_notional, min_trade_notional=min_trade_notional):
            try:
                with dragon_main.LOCK:
                    dragon_main.STATE.setdefault("rejection", {}); dragon_main.STATE["rejection"]["RISK_OR_NOTIONAL"] = dragon_main.STATE["rejection"].get("RISK_OR_NOTIONAL", 0) + 1
                dragon_main.event("RISK", "Trade blocked by risk/notional gate")
            except Exception: pass
            return False
        cfg = dragon_main.Config.from_env(); expected_profit = Decimal(str(notional)) * Decimal(str(net_bps)) / Decimal("10000"); minimum_profit = Decimal(str(cfg.min_expected_profit_usdt))
        if expected_profit < minimum_profit:
            try:
                with dragon_main.LOCK:
                    dragon_main.STATE.setdefault("rejection", {}); dragon_main.STATE["rejection"]["EXPECTED_PROFIT_REJECTED"] = dragon_main.STATE["rejection"].get("EXPECTED_PROFIT_REJECTED", 0) + 1
                dragon_main.event("GATE", "Trade blocked: expected profit below minimum", expected_profit=float(expected_profit), minimum_profit=float(minimum_profit), net_bps=float(net_bps))
            except Exception: pass
            return False
        return True
    dragon_main.approved = guarded_approved

    original_execute = dragon_main.execute_triangle
    def guarded_execute(client, path, start_asset, first_asset, budget, filters, dry_run):
        cfg = dragon_main.Config.from_env(); now_ms = time.monotonic() * 1000; signal = last_signal.get(tuple(path)); max_age_ms = min(float(cfg.stale_ms), 250.0)
        if signal is None or now_ms - signal[0] > max_age_ms:
            try:
                with dragon_main.LOCK:
                    dragon_main.STATE.setdefault("rejection", {}); dragon_main.STATE["rejection"]["FINAL_SIGNAL_STALE"] = dragon_main.STATE["rejection"].get("FINAL_SIGNAL_STALE", 0) + 1
                dragon_main.event("GATE", "Final signal freshness check blocked order", age_ms=float(now_ms - signal[0]) if signal else None, max_age_ms=max_age_ms)
            except Exception: pass
            raise RuntimeError("final signal freshness check failed")
        if not (cfg.live_trading and not cfg.dry_run): return original_execute(client, path, start_asset, first_asset, budget, filters, dry_run)
        account = client.account(); free_usdt = Decimal(str(next((x.get("free", "0") for x in account.get("balances", []) if x.get("asset") == "USDT"), "0"))); reserve = Decimal(str(cfg.safety_reserve_usdt))
        if free_usdt - reserve < Decimal(str(budget)):
            try:
                with dragon_main.LOCK:
                    dragon_main.STATE.setdefault("rejection", {}); dragon_main.STATE["rejection"]["FINAL_BALANCE_REJECTED"] = dragon_main.STATE["rejection"].get("FINAL_BALANCE_REJECTED", 0) + 1; dragon_main.STATE["free_usdt"] = str(free_usdt)
                dragon_main.event("GATE", "Final balance recheck blocked order", free_usdt=str(free_usdt), reserve=str(reserve), budget=str(budget))
            except Exception: pass
            raise RuntimeError("final balance recheck failed: executable balance below budget plus safety reserve")
        return original_execute(client, path, start_asset, first_asset, budget, filters, dry_run)
    dragon_main.execute_triangle = guarded_execute

    asyncio.run(dragon_main.run())


if __name__ == "__main__": main()
