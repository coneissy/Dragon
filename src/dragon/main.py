"""Dragon MAX UNIVERSE runtime: one scanner, one calculator, one decision path.

The workbook is the strategy specification. Execution remains Binance-only;
other venues are observation inputs and are never treated as execution venues.
"""
import asyncio
import json
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .binance import BinanceClient, BinanceError
from .calculator import calculate
from .config import Config
from .executor import execute_triangle
from .ledger import Ledger
from .market_data import MarketData
from .risk import RiskState, risk_budget
from .telemetry import Telemetry
from .triangles import build_triangles

STATE = {
    "health": "starting", "universe_mode": "MAX_UNIVERSE", "dry_run": True,
    "live": False, "binance_authenticated": False, "free_usdt": 0.0,
    "symbols": 0, "triangles": 0, "scans": 0, "opportunities": 0,
    "evaluation_attempts": 0, "universe_qualified": 0, "universe_execution_ready": 0,
    "universe_rejected": 0, "universe_selected": 0, "best_net_edge_bps": 0.0,
    "warnings": [], "top_opportunities": [], "universe_top": [],
    "ws_connected_shards": 0, "ws_total_shards": 0, "ws_health": "connecting",
    "ws_reconnects": 0, "ws_disconnects": 0, "depth_updates": 0,
    "ws_messages": 0, "ws_invalid_messages": 0, "rest_bootstrap_ok": 0, "rest_bootstrap_failed": 0,
    "ledger_filled": 0, "realized_pnl_usdt": 0.0, "balance_refreshes": 0,
    "controls": {"kill_switch": False}, "recent": [],
}
STATE_LOCK = threading.Lock()


def _state(**updates):
    with STATE_LOCK:
        STATE.update(updates)
        return dict(STATE)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] not in {"/health", "/api/status", "/dashboard"}:
            self.send_response(404); self.end_headers(); return
        if self.path.split("?", 1)[0] == "/dashboard":
            html = (Path(__file__).resolve().parents[2] / "dashboard.html").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(html))); self.end_headers(); self.wfile.write(html); return
        body = json.dumps({"ok": True, "state": dict(STATE)}, separators=(",", ":")).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_):
        return


def start_health_server(port=10000):
    server = ThreadingHTTPServer(("0.0.0.0", int(port)), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _filters(exchange_info):
    out = {}
    for row in exchange_info.get("symbols", []):
        if row.get("status") != "TRADING":
            continue
        f = {x.get("filterType"): x for x in row.get("filters", [])}
        lot = f.get("LOT_SIZE", {})
        market_lot = f.get("MARKET_LOT_SIZE", {})
        notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL", {})
        out[row["symbol"]] = {
            "baseAsset": row["baseAsset"], "quoteAsset": row["quoteAsset"],
            "stepSize": market_lot.get("stepSize") or lot.get("stepSize", "0"),
            "minQty": market_lot.get("minQty") or lot.get("minQty", "0"),
            "maxQty": market_lot.get("maxQty") or lot.get("maxQty", "0"),
            "minNotional": notional.get("minNotional", "0"),
            "maxNotional": notional.get("maxNotional", "0"),
        }
    return out


def _top_symbols(rows, cap):
    ranked = []
    for row in rows:
        if str(row.get("symbol", "")).endswith("USDT") and row.get("symbol", "") not in {"USDCUSDT", "BUSDUSDT"}:
            try:
                ranked.append((Decimal(str(row.get("quoteVolume", "0"))), row["symbol"]))
            except Exception:
                pass
    ranked.sort(reverse=True)
    return [s for _, s in ranked[: max(30, int(cap))]]


def _score(net_bps):
    return max(0.0, min(100.0, 40.0 + float(net_bps) - 3.0))


def _tier(score):
    if score >= 90: return "S"
    if score >= 85: return "A+"
    if score >= 80: return "A"
    if score >= 70: return "B+"
    if score >= 60: return "B"
    if score >= 40: return "C"
    return "D"


async def _run(cfg: Config):
    client = BinanceClient(cfg.api_base, __import__("os").getenv("BINANCE_API_KEY", ""), __import__("os").getenv("BINANCE_API_SECRET", ""))
    telemetry = Telemetry()
    risk = RiskState()
    ledger = Ledger(cfg.ledger_path)
    start_health_server(int(__import__("os").getenv("PORT", "10000")))
    try:
        exchange_info = client.exchange_info()
        filters = _filters(exchange_info)
        ticker = client.ticker_24hr()
        symbols = _top_symbols(ticker, int(__import__("os").getenv("MAX_WS_SYMBOLS", "300")))
        triangles = build_triangles(exchange_info, cfg.max_triangles)
        needed = sorted({s for t in triangles for s in t.symbols if s in filters})
        if len(needed) > int(__import__("os").getenv("MAX_WS_SYMBOLS", "300")):
            rank = {s: i for i, s in enumerate(symbols)}
            needed.sort(key=lambda s: rank.get(s, 10**9))
            needed = needed[: int(__import__("os").getenv("MAX_WS_SYMBOLS", "300"))]
        selected = set(needed)
        triangles = [t for t in triangles if all(s in selected for s in t.symbols)]
        md = MarketData(cfg.ws_base, needed, cfg.depth_levels, cfg.stale_ms, cfg.api_base)
        _state(symbols=len(needed), triangles=len(triangles), ws_total_shards=max(1, len(md._shards())), ws_health="connecting", health="starting", dry_run=cfg.dry_run, live=cfg.live_trading and not cfg.dry_run)
        ws_task = asyncio.create_task(md.run())
        last_balance = Decimal("9")
        if cfg.live_trading and not cfg.dry_run:
            try:
                account = client.account()
                balances = {x["asset"]: Decimal(str(x.get("free", "0"))) for x in account.get("balances", [])}
                last_balance = balances.get("USDT", Decimal("0"))
                _state(binance_authenticated=True)
            except Exception as exc:
                _state(warnings=[f"Binance authentication failed: {exc}"])
                raise
        risk.observe_balance(last_balance)
        cycle = 0
        last_trade = 0.0
        while True:
            cycle += 1
            started = time.perf_counter()
            now = time.time()
            connected_shards = md.connected_shards
            connected = connected_shards > 0
            _state(ws_connected_shards=connected_shards, ws_total_shards=md.total_shards or max(1, len(md._shards())), ws_health="healthy" if connected_shards == (md.total_shards or 0) else ("degraded" if connected else "reconnecting"), ws_reconnects=md.reconnects, ws_disconnects=md.disconnects, depth_updates=md.valid_updates, ws_messages=md.ws_messages, ws_invalid_messages=md.invalid_messages, rest_bootstrap_ok=md.bootstrap_ok, rest_bootstrap_failed=md.bootstrap_failed, scans=cycle)
            if not connected:
                await asyncio.sleep(0.25)
                continue
            budget = risk_budget(last_balance, cfg.capital_allocation_pct, cfg.max_notional_usdt, Decimal(str(cfg.min_trade_notional_usdt)), safety_reserve_usdt=Decimal(str(cfg.safety_reserve_usdt)), risk_state=risk)
            best = None; candidates = []
            for tri in triangles:
                _state(evaluation_attempts=STATE["evaluation_attempts"] + 1)
                if not md.fresh_books(tri.symbols):
                    telemetry.candidate(tri.symbols, status="REJECT", reason="STALE_BOOK")
                    continue
                result = calculate(tri.symbols, tri.assets, md.books, {s: (filters[s]["baseAsset"], filters[s]["quoteAsset"]) for s in tri.symbols}, budget, cfg.fee_bps, cfg.max_slippage_bps)
                if result is None:
                    telemetry.candidate(tri.symbols, status="REJECT", reason="INSUFFICIENT_DEPTH")
                    continue
                score = _score(result.net_bps)
                eligible = result.net_bps >= Decimal(str(cfg.min_net_edge_bps)) and result.net_pnl_usdt >= Decimal(str(cfg.min_expected_profit_usdt)) and score >= 40 and budget >= Decimal(str(cfg.min_trade_notional_usdt))
                reason = None if eligible else ("BELOW_NET_EDGE" if result.net_bps < Decimal(str(cfg.min_net_edge_bps)) else "RISK_LIMIT")
                telemetry.candidate(tri.symbols, status="ACCEPT" if eligible else "REJECT", reason=reason, result=result, notional=budget)
                item = {"path": tri.symbols, "net_bps": float(result.net_bps), "gross_bps": float(result.gross_bps), "score": score, "tier": _tier(score), "eligible": eligible, "rejection_reason": reason, "evaluation_notional": str(budget), "trade_budget": str(budget)}
                candidates.append(item)
                if eligible and (best is None or result.net_bps > best[0].net_bps): best = (result, tri, score)
            candidates.sort(key=lambda x: (x["net_bps"], x["score"]), reverse=True)
            _state(universe_top=candidates[:50], universe_qualified=sum(1 for x in candidates if x["net_bps"] >= cfg.min_net_edge_bps), universe_rejected=sum(1 for x in candidates if not x["eligible"]), universe_execution_ready=sum(1 for x in candidates if x["eligible"]), universe_selected=1 if best else 0, opportunities=len(candidates), best_net_edge_bps=candidates[0]["net_bps"] if candidates else 0.0, top_opportunities=candidates[:20], universe_cycle_ms=(time.perf_counter()-started)*1000, universe_last_cycle_at=now)
            if best and (now - last_trade) * 1000 >= cfg.cooldown_ms:
                result, tri, score = best
                _state(universe_selected=1)
                if cfg.live_trading and not cfg.dry_run:
                    try:
                        execute_triangle(client, tri.symbols, "USDT", tri.assets[1], budget, filters, dry_run=False)
                        last_trade = time.time()
                        _state(ledger_filled=STATE["ledger_filled"] + 1)
                    except Exception as exc:
                        telemetry.record("EXECUTION_REJECT", str(exc), path=list(tri.symbols))
                else:
                    telemetry.record("DRY_RUN", f"selected {tri.symbols} net={result.net_bps:.3f}bps score={score:.1f}")
                    last_trade = time.time()
            snap = telemetry.snapshot()
            _state(recent=snap["recent"][-50:], controls={"kill_switch": risk.kill_switch}, health="healthy", ws_health="healthy" if md.connected_shards == md.total_shards else "degraded")
            await asyncio.sleep(max(0.05, cfg.cooldown_ms / 1000.0 if cfg.cooldown_ms else 0.30))
    finally:
        client.close()


def main():
    cfg = Config.from_env(); cfg.validate()
    try:
        asyncio.run(_run(cfg))
    except KeyboardInterrupt:
        _state(health="stopped")


if __name__ == "__main__":
    main()
