"""Dragon production runtime: one market-data owner, one calculator, one decision path."""
import asyncio
import json
import os
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .binance import BinanceClient
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
    "platform_capacity": 20, "symbols_per_platform_capacity": 10000,
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
        route = self.path.split("?", 1)[0]
        if route not in {"/health", "/api/status", "/dashboard"}:
            self.send_response(404); self.end_headers(); return
        if route == "/dashboard":
            html = (Path(__file__).resolve().parents[2] / "dashboard.html").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(html))); self.end_headers(); self.wfile.write(html); return
        with STATE_LOCK:
            state = dict(STATE)
            state["controls"] = dict(STATE.get("controls", {}))
        body = json.dumps({"ok": state.get("health") not in {"failed", "stopped"}, "state": state}, separators=(",", ":")).encode()
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
        symbol = str(row.get("symbol", ""))
        if symbol.endswith("USDT") and symbol not in {"USDCUSDT", "BUSDUSDT"}:
            try:
                ranked.append((Decimal(str(row.get("quoteVolume", "0"))), symbol))
            except Exception:
                pass
    ranked.sort(reverse=True)
    return [s for _, s in ranked[:max(30, int(cap))]]


async def _run(cfg: Config):
    client = BinanceClient(cfg.api_base, os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", ""))
    telemetry = Telemetry()
    risk = RiskState()
    ledger = Ledger(cfg.ledger_path)
    start_health_server(int(os.getenv("PORT", "10000")))
    ws_task = None
    try:
        exchange_info = client.exchange_info()
        filters = _filters(exchange_info)
        ticker = client.ticker_24hr()
        ranked_symbols = _top_symbols(ticker, cfg.max_ws_symbols)
        triangles = build_triangles(exchange_info, cfg.max_triangles)
        # Select only triangles whose three legs are in the bounded market-data universe.
        universe = set(ranked_symbols)
        needed = sorted({s for t in triangles for s in t.symbols if s in filters and s in universe})
        if len(needed) > cfg.max_ws_symbols:
            rank = {s: i for i, s in enumerate(ranked_symbols)}
            needed.sort(key=lambda s: rank.get(s, 10**9))
            needed = needed[:cfg.max_ws_symbols]
        selected = set(needed)
        triangles = [t for t in triangles if all(s in selected for s in t.symbols)]
        md = MarketData(cfg.ws_base, needed, cfg.depth_levels, cfg.stale_ms, cfg.api_base, shard_size=cfg.ws_shard_size)
        _state(
            platform_capacity=cfg.max_platforms,
            symbols_per_platform_capacity=cfg.max_symbols_per_platform,
            symbols=len(needed), triangles=len(triangles),
            ws_total_shards=max(1, len(md._shards())), ws_health="connecting",
            health="starting", dry_run=cfg.dry_run,
            live=cfg.live_trading and not cfg.dry_run,
        )
        ws_task = asyncio.create_task(md.run())

        # Dry-run uses an explicit simulation balance. Live mode reads Binance account state.
        balance = Decimal(str(cfg.simulation_balance_usdt))
        if cfg.live_trading and not cfg.dry_run:
            account = client.account()
            balances = {x["asset"]: Decimal(str(x.get("free", "0"))) for x in account.get("balances", [])}
            balance = balances.get("USDT", Decimal("0"))
            _state(binance_authenticated=True, free_usdt=float(balance), balance_refreshes=1)
        else:
            _state(binance_authenticated=False, free_usdt=float(balance))
        risk.observe_balance(balance)

        cycle = 0
        last_trade = 0.0
        while True:
            cycle += 1
            started = time.perf_counter()
            now = time.time()
            connected_shards = md.connected_shards
            total_shards = md.total_shards or max(1, len(md._shards()))
            ws_healthy = connected_shards == total_shards and total_shards > 0
            _state(
                ws_connected_shards=connected_shards, ws_total_shards=total_shards,
                ws_health="healthy" if ws_healthy else ("degraded" if connected_shards else "reconnecting"),
                ws_reconnects=md.reconnects, ws_disconnects=md.disconnects,
                depth_updates=md.valid_updates, ws_messages=md.ws_messages,
                ws_invalid_messages=md.invalid_messages, rest_bootstrap_ok=md.bootstrap_ok,
                rest_bootstrap_failed=md.bootstrap_failed, scans=cycle,
            )
            if not connected_shards:
                await asyncio.sleep(0.25)
                continue

            budget = risk_budget(
                balance, cfg.capital_allocation_pct, cfg.max_notional_usdt,
                Decimal(str(cfg.min_trade_notional_usdt)),
                safety_reserve_usdt=Decimal(str(cfg.safety_reserve_usdt)), risk_state=risk,
            )
            best = None
            candidates = []
            for tri in triangles:
                with STATE_LOCK:
                    STATE["evaluation_attempts"] += 1
                if not md.fresh_books(tri.symbols):
                    telemetry.candidate(tri.symbols, status="REJECT", reason="STALE_BOOK")
                    continue
                books = md.snapshot(tri.symbols)
                if len(books) != 3:
                    telemetry.candidate(tri.symbols, status="REJECT", reason="NO_LIQUIDITY")
                    continue
                result = calculate(
                    tri.symbols, tri.assets, books,
                    {s: (filters[s]["baseAsset"], filters[s]["quoteAsset"]) for s in tri.symbols},
                    budget, cfg.fee_bps, cfg.max_slippage_bps,
                )
                if result is None:
                    telemetry.candidate(tri.symbols, status="REJECT", reason="INSUFFICIENT_DEPTH")
                    continue
                eligible = (
                    risk.can_trade()
                    and result.net_bps >= Decimal(str(cfg.min_net_edge_bps))
                    and result.net_pnl_usdt >= Decimal(str(cfg.min_expected_profit_usdt))
                    and budget >= Decimal(str(cfg.min_trade_notional_usdt))
                )
                reason = None if eligible else ("BELOW_NET_EDGE" if result.net_bps < Decimal(str(cfg.min_net_edge_bps)) else "RISK_LIMIT")
                telemetry.candidate(tri.symbols, status="ACCEPT" if eligible else "REJECT", reason=reason, result=result, notional=budget)
                item = {
                    "path": tri.symbols, "net_bps": float(result.net_bps),
                    "gross_bps": float(result.gross_bps), "eligible": eligible,
                    "rejection_reason": reason, "evaluation_notional": str(budget),
                    "trade_budget": str(budget), "depth_drag_bps": float(result.depth_drag_bps),
                    "fee_drag_bps": float(result.fee_drag_bps), "safety_bps": float(result.safety_bps),
                    "break_even_gross_bps": float(result.break_even_gross_bps),
                }
                candidates.append(item)
                if eligible and (best is None or result.net_bps > best[0].net_bps):
                    best = (result, tri)

            candidates.sort(key=lambda x: x["net_bps"], reverse=True)
            _state(
                universe_top=candidates[:50],
                universe_qualified=sum(1 for x in candidates if x["net_bps"] > 0),
                universe_rejected=sum(1 for x in candidates if not x["eligible"]),
                universe_execution_ready=sum(1 for x in candidates if x["eligible"]),
                universe_selected=1 if best else 0, opportunities=len(candidates),
                best_net_edge_bps=candidates[0]["net_bps"] if candidates else 0.0,
                top_opportunities=candidates[:20],
                universe_cycle_ms=(time.perf_counter() - started) * 1000,
                universe_last_cycle_at=now,
            )

            if best and risk.can_trade() and (now - last_trade) * 1000 >= cfg.cooldown_ms:
                result, tri = best
                if cfg.live_trading and not cfg.dry_run:
                    try:
                        execution = execute_triangle(client, tri.symbols, "USDT", tri.assets[1], budget, filters, dry_run=False)
                        last_trade = time.time()
                        _state(ledger_filled=STATE["ledger_filled"] + 1, last_execution=execution)
                    except Exception as exc:
                        telemetry.record("EXECUTION_REJECT", str(exc), path=list(tri.symbols))
                        risk.record(Decimal("0"), balance, max_consecutive_losses=cfg.max_consecutive_losses, max_drawdown_pct=cfg.max_drawdown_pct)
                else:
                    telemetry.record("DRY_RUN", f"selected {tri.symbols} net={result.net_bps:.3f}bps")
                    last_trade = time.time()

            snap = telemetry.snapshot()
            _state(
                recent=snap.get("recent", [])[-50:],
                controls={"kill_switch": risk.kill_switch},
                health="healthy" if connected_shards else "degraded",
            )
            await asyncio.sleep(max(0.05, cfg.cooldown_ms / 1000.0 if cfg.cooldown_ms else 0.30))
    except Exception as exc:
        _state(health="failed", warnings=STATE.get("warnings", []) + [str(exc)[:500]])
        raise
    finally:
        if ws_task:
            ws_task.cancel()
            await asyncio.gather(ws_task, return_exceptions=True)
        client.close()
        try:
            ledger.close()
        except Exception:
            pass


def main():
    cfg = Config.from_env()
    cfg.validate()
    try:
        asyncio.run(_run(cfg))
    except KeyboardInterrupt:
        _state(health="stopped")


if __name__ == "__main__":
    main()
