import asyncio
import json
import random
import time
from decimal import Decimal

import websockets

import web_runner
from src.dragon.control import analysis_allowed, trading_allowed
from src.dragon.multi_exchange import MultiExchangeFeeds
from src.dragon.universe import classify_triangle

WS_BACKOFF_MIN = 1.0
WS_BACKOFF_MAX = 60.0
WS_STALE_SECONDS = 10.0
WS_SHARD_SIZE = 100
REST_FALLBACK_SECONDS = 3.0
WS_SILENCE_SECONDS = 8.0


def _metric(name, delta=1):
    with web_runner.LOCK:
        web_runner.STATE[name] = web_runner.STATE.get(name, 0) + delta


def _ws_state(worker_id, **values):
    with web_runner.LOCK:
        statuses = dict(web_runner.STATE.get("ws_shard_status", {}))
        statuses[str(worker_id)] = values.get("status", statuses.get(str(worker_id), "unknown"))
        web_runner.STATE["ws_shard_status"] = statuses
        web_runner.STATE.update(values)
        connected = sum(v == "connected" for v in statuses.values())
        total = len(statuses)
        web_runner.STATE["ws_connected_shards"] = connected
        web_runner.STATE["ws_total_shards"] = total
        web_runner.STATE["ws_connected"] = connected > 0
        web_runner.STATE["ws_health"] = "healthy" if total and connected == total else ("degraded" if connected else "disconnected")


def _parse_depth(raw, levels):
    try:
        msg = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except Exception:
        _metric("ws_invalid_messages")
        return None
    data = msg.get("data", msg) if isinstance(msg, dict) else {}
    if not isinstance(data, dict) or not data.get("s"):
        _metric("ws_non_market_messages")
        return None
    bids, asks = [], []
    for p, q in data.get("b", [])[:levels]:
        try:
            if Decimal(str(p)) > 0 and Decimal(str(q)) > 0:
                bids.append((str(p), str(q)))
        except Exception:
            pass
    for p, q in data.get("a", [])[:levels]:
        try:
            if Decimal(str(p)) > 0 and Decimal(str(q)) > 0:
                asks.append((str(p), str(q)))
        except Exception:
            pass
    if not bids or not asks:
        _metric("ws_invalid_depth_messages")
        return None
    return {"s": str(data["s"]).upper(), "b": bids, "a": asks, "_source": "binance_ws_depth"}


async def _feed_worker(cfg, symbols, queue, worker_id):
    streams = [f"{s.lower()}@depth{cfg.depth_levels}@100ms" for s in symbols]
    if not streams:
        return
    base = cfg.ws_base.rstrip("/")
    if base.endswith("/ws"):
        base = base[:-3] + "/stream"
    elif not base.endswith("/stream"):
        base += "/stream"
    url = base + "?streams=" + "/".join(streams)
    delay = WS_BACKOFF_MIN
    while True:
        try:
            _ws_state(worker_id, status="connecting")
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5, open_timeout=15, max_size=2**24, max_queue=4096, compression=None) as ws:
                delay = WS_BACKOFF_MIN
                _ws_state(worker_id, status="connected")
                web_runner.event("WS", f"Spot shard {worker_id} connected", symbols=len(symbols), streams=len(streams))
                last_message = time.monotonic()
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=WS_STALE_SECONDS)
                    except asyncio.TimeoutError as exc:
                        raise ConnectionError(f"market-data stream silent for {time.monotonic() - last_message:.1f}s") from exc
                    last_message = time.monotonic()
                    _metric("ws_raw_messages")
                    data = _parse_depth(raw, cfg.depth_levels)
                    if not data:
                        continue
                    with web_runner.LOCK:
                        web_runner.STATE["last_ws_depth_update"] = time.time()
                        web_runner.STATE["market_data_updates"] = web_runner.STATE.get("market_data_updates", 0) + 1
                    if queue.full():
                        try:
                            queue.get_nowait()
                            _metric("market_queue_drops")
                        except asyncio.QueueEmpty:
                            pass
                    await queue.put(data)
        except asyncio.CancelledError:
            _ws_state(worker_id, status="stopped")
            raise
        except Exception as exc:
            _ws_state(worker_id, status="reconnecting", ws_last_disconnect=time.time())
            web_runner.event("WS_ERROR", f"Spot shard {worker_id} disconnected: {exc}", shard=worker_id)
            wait = min(WS_BACKOFF_MAX, delay + random.uniform(0, min(5.0, delay * 0.25)))
            _ws_state(worker_id, ws_next_retry_at=time.time() + wait)
            await asyncio.sleep(wait)
            delay = min(WS_BACKOFF_MAX, delay * 2.0)
            _metric("ws_reconnects")


async def _rest_recovery_worker(client, symbols, queue):
    wanted = set(symbols)
    while True:
        try:
            with web_runner.LOCK:
                last_ws = float(web_runner.STATE.get("last_ws_depth_update", 0) or 0)
                silent = last_ws <= 0 or time.time() - last_ws >= WS_SILENCE_SECONDS
                connected = sum(v == "connected" for v in web_runner.STATE.get("ws_shard_status", {}).values())
            if not silent:
                await asyncio.sleep(REST_FALLBACK_SECONDS)
                continue
            payload = await asyncio.to_thread(client.book_ticker)
            count = 0
            for row in payload if isinstance(payload, list) else []:
                if str(row.get("symbol", "")).upper() not in wanted:
                    continue
                if all(row.get(k) for k in ("bidPrice", "bidQty", "askPrice", "askQty")):
                    count += 1
            with web_runner.LOCK:
                web_runner.STATE["rest_fallback_updates"] = web_runner.STATE.get("rest_fallback_updates", 0) + count
                web_runner.STATE["last_rest_market_update"] = time.time()
                web_runner.STATE["rest_fallback_mode"] = "telemetry_only"
            web_runner.event("REST_FALLBACK", f"BBO recovery observed {count} symbols; telemetry only, arbitrage remains WS-depth-only", symbols=count, ws_connected_shards=connected)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _metric("rest_fallback_errors")
            web_runner.event("REST_SCAN_ERROR", f"BookTicker fallback failed: {exc}")
        await asyncio.sleep(REST_FALLBACK_SECONDS)


def _record_book(item, books, dirty, by_symbol, cfg):
    symbol = str(item.get("s", "")).upper()
    if not symbol:
        _metric("market_data_invalid")
        return
    try:
        bids = [(p, q) for p, q in item.get("b", [])[:cfg.depth_levels] if Decimal(str(p)) > 0 and Decimal(str(q)) > 0]
        asks = [(p, q) for p, q in item.get("a", [])[:cfg.depth_levels] if Decimal(str(p)) > 0 and Decimal(str(q)) > 0]
    except Exception:
        _metric("market_data_invalid")
        return
    if not bids or not asks or item.get("_source", "binance_ws_depth") != "binance_ws_depth":
        _metric("non_ws_books_rejected")
        return
    books[symbol] = {"bids": bids, "asks": asks, "depth_ts": time.monotonic() * 1000, "source": "binance_ws_depth"}
    dirty.update(by_symbol.get(symbol, ()))
    _metric("books_populated")


async def full_universe_stream_loop(cfg, client, filters, triangles, symbols, symbol_meta):
    books, dirty, by_symbol = {}, set(), {}
    for idx, triangle in enumerate(triangles):
        for symbol in triangle.symbols:
            by_symbol.setdefault(symbol, []).append(idx)
    shards = [symbols[i:i + WS_SHARD_SIZE] for i in range(0, len(symbols), WS_SHARD_SIZE)]
    queue = asyncio.Queue(maxsize=20000)
    workers = [asyncio.create_task(_feed_worker(cfg, shard, queue, i + 1)) for i, shard in enumerate(shards)]
    workers.append(asyncio.create_task(_rest_recovery_worker(client, symbols, queue)))
    external = MultiExchangeFeeds(web_runner.STATE, web_runner.LOCK, web_runner.event, symbols=symbols)
    external_task = asyncio.create_task(external.run())
    with web_runner.LOCK:
        web_runner.STATE.update({"ws_connected": False, "ws_health": "connecting" if shards else "disconnected", "ws_connected_shards": 0, "ws_total_shards": len(shards), "ws_shards": len(shards), "ws_shard_size": WS_SHARD_SIZE, "symbols": len(symbols), "triangles": len(triangles), "universe_mode": cfg.universe_mode, "universe_scanned": len(triangles), "universe_qualified": 0, "universe_execution_ready": 0, "universe_rejected": 0, "universe_selected": 0, "universe_top": [], "evaluation_attempts": 0, "evaluation_none": 0, "analysis_blocks": 0, "balance_failures": 0, "budget_blocks": 0, "non_ws_books_rejected": 0, "rest_fallback_mode": "telemetry_only"})
    web_runner.event("UNIVERSE", f"Full dynamic Spot universe active: {len(symbols)} symbols, {len(triangles)} triangles, {len(shards)} WS shards", shard_size=WS_SHARD_SIZE)
    web_runner.event("SCAN", f"Scanner armed; WS-depth-only evaluation + BBO recovery telemetry; net-edge threshold={cfg.min_net_edge_bps:g}bps")
    last_balance = 0.0
    free_usdt = Decimal("0")
    balance_ok = False
    last_order_ms = 0.0
    failures = 0
    try:
        while True:
            first = await queue.get()
            batch = [first]
            for _ in range(min(queue.qsize(), 1000)):
                try: batch.append(queue.get_nowait())
                except asyncio.QueueEmpty: break
            for item in batch: _record_book(item, books, dirty, by_symbol, cfg)
            now = time.monotonic() * 1000
            candidates, dirty = list(dirty), set()
            with web_runner.LOCK:
                web_runner.STATE["depth_updates"] = web_runner.STATE.get("depth_updates", 0) + len(batch)
                web_runner.STATE["scans"] = web_runner.STATE.get("scans", 0) + len(candidates)
                web_runner.STATE["last_scan"] = time.time()
                web_runner.STATE["books_ready"] = len(books)
            if not analysis_allowed():
                _metric("analysis_blocks")
                continue
            if now - last_balance >= 5000:
                try:
                    account = await asyncio.to_thread(client.account)
                    free_usdt = next((Decimal(str(x.get("free", "0"))) for x in account.get("balances", []) if x.get("asset") == "USDT"), Decimal("0"))
                    balance_ok = True
                    last_balance = now
                    with web_runner.LOCK: web_runner.STATE["free_usdt"] = str(free_usdt); web_runner.STATE["balance_refreshes"] = web_runner.STATE.get("balance_refreshes", 0) + 1
                except Exception as exc:
                    balance_ok = False; last_balance = now; _metric("balance_failures"); web_runner.event("BALANCE_ERROR", f"balance refresh failed; scanner remains non-executing: {exc}")
            trade_budget = web_runner.risk_budget(free_usdt, cfg.risk_pct, cfg.max_notional_usdt, Decimal(str(cfg.min_trade_notional_usdt)), capital_allocation_pct=cfg.capital_allocation_pct, safety_reserve_usdt=Decimal(str(cfg.safety_reserve_usdt))) if balance_ok else Decimal("0")
            evaluation_notional = trade_budget if trade_budget > 0 else Decimal(str(cfg.min_trade_notional_usdt))
            max_entries = int(trade_budget / Decimal(str(cfg.min_trade_notional_usdt))) if trade_budget > 0 else 0
            with web_runner.LOCK: web_runner.STATE["trade_budget"] = str(trade_budget); web_runner.STATE["scan_notional"] = str(evaluation_notional); web_runner.STATE["universe_max_entries"] = max_entries
            if trade_budget <= 0: _metric("budget_blocks")
            cycle_start = time.monotonic(); cycle_top = []; qualified = ready_count = rejected = 0; reasons = {}
            for idx in candidates:
                triangle = triangles[idx]
                if not all(s in books and books[s].get("source") == "binance_ws_depth" and now - books[s].get("depth_ts", 0) <= cfg.stale_ms for s in triangle.symbols):
                    _metric("stale_or_non_ws_triangle_skips"); continue
                _metric("evaluation_attempts")
                with web_runner.LOCK:
                    authoritative_fee = web_runner.STATE.get("dynamic_fee_bps")
                effective_fee = Decimal(str(authoritative_fee)) if authoritative_fee not in (None, "", 0, 0.0) else Decimal(str(cfg.fee_bps))
                fee_source = web_runner.STATE.get("fee_source", "configured_fallback")
                result = web_runner.evaluate_triangle(triangle, books, effective_fee, cfg.max_slippage_bps, symbol_meta, evaluation_notional)
                if not result: _metric("evaluation_none"); continue
                net_bps, gross_bps, path, first_asset, second_asset = result
                expected_profit = evaluation_notional * net_bps / Decimal("10000")
                edge_ok = net_bps >= Decimal(str(cfg.min_net_edge_bps))
                decision = classify_triangle(triangle, books, symbol_meta, now, evaluation_notional, stale_ms=cfg.stale_ms, max_slippage_bps=Decimal(str(cfg.max_slippage_bps)), net_edge_bps=net_bps, min_net_edge_bps=Decimal(str(cfg.min_net_edge_bps)), expected_profit_usdt=expected_profit, min_expected_profit_usdt=Decimal(str(cfg.min_expected_profit_usdt)))
                eligible = edge_ok and decision.qualified
                ready = eligible and decision.execution_ready and trade_budget >= Decimal(str(cfg.min_trade_notional_usdt)) and max_entries > 0 and balance_ok
                qualified += int(eligible); ready_count += int(ready)
                if not ready:
                    rejected += 1; reason = decision.rejection or ("NET_EDGE" if not edge_ok else "CAPITAL"); reasons[reason] = reasons.get(reason, 0) + 1
                cycle_top.append({"path": path, "tier": decision.tier, "score": float(decision.score), "net_bps": float(net_bps), "gross_bps": float(gross_bps), "expected_profit_usdt": str(expected_profit), "qualified": eligible, "execution_ready": ready, "rejection": decision.rejection, "fee_source": fee_source, "fee_bps": float(effective_fee)})
                web_runner.record_opportunity(path, net_bps, gross_bps, evaluation_notional, eligible=eligible, trade_budget=trade_budget)
                gate = "PASS" if ready else (decision.rejection or ("NET_EDGE" if not edge_ok else "CAPITAL"))
                web_runner.event("CANDIDATE", f"net={net_bps:.3f} gross={gross_bps:.3f} expected=${expected_profit:.6f} gate={gate} fee={effective_fee:.4f}bps source={fee_source}", path=path, net_bps=float(net_bps), gross_bps=float(gross_bps), expected_profit_usdt=str(expected_profit), fee_bps=float(effective_fee), fee_source=fee_source, rejection_reason=gate)
                if not ready: continue
                with web_runner.LOCK: web_runner.STATE["opportunities"] = web_runner.STATE.get("opportunities", 0) + 1; web_runner.STATE["last_opportunity"] = time.time()
                web_runner.event("OPPORTUNITY", f"net={net_bps:.3f} gross={gross_bps:.3f} expected=${expected_profit:.6f}", path=path, net_bps=float(net_bps), gross_bps=float(gross_bps), expected_profit_usdt=str(expected_profit), fee_bps=float(effective_fee))
                now_ms = time.monotonic() * 1000
                if now_ms - last_order_ms < cfg.cooldown_ms or not trading_allowed("spot"): continue
                if not web_runner.approved(net_bps, cfg.min_net_edge_bps, trade_budget, cfg.max_notional_usdt, min_trade_notional=Decimal(str(cfg.min_trade_notional_usdt))):
                    _metric("risk_blocks"); web_runner.event("RISK", "Trade blocked by final edge/notional gate", path=path); continue
                last_order_ms = now_ms
                try:
                    web_runner.event("LIVE", "Three-leg execution requested", path=path, net_bps=float(net_bps), budget=str(trade_budget), expected_profit_usdt=str(expected_profit))
                    execution = await asyncio.to_thread(web_runner.execute_triangle, client, path, "USDT", first_asset, trade_budget, filters, False)
                    if not execution.get("finished") or execution.get("final_asset") != "USDT": raise RuntimeError("execution returned without a completed USDT cycle")
                    web_runner.LEDGER.record(path, trade_budget, execution)
                    with web_runner.LOCK: web_runner.STATE["executions"] = web_runner.STATE.get("executions", 0) + 1; web_runner.STATE["last_execution"] = time.time()
                    web_runner._sync_ledger(); failures = 0
                    web_runner.event("FILLED", f"Triangle fully filled; realized={execution['realized_pnl_usdt']} USDT", path=path)
                except Exception as exc:
                    failures += 1
                    with web_runner.LOCK: web_runner.STATE["execution_errors"] = web_runner.STATE.get("execution_errors", 0) + 1; web_runner.STATE["last_error"] = str(exc)
                    if web_runner.LEDGER is not None: web_runner.LEDGER.record(path, trade_budget, error=exc)
                    web_runner.event("ERROR", str(exc), path=path)
                    if failures >= 3: raise RuntimeError("three consecutive execution failures; engine stopped for safety") from exc
            cycle_top.sort(key=lambda x: (x["net_bps"], x["score"]), reverse=True)
            with web_runner.LOCK:
                web_runner.STATE["universe_qualified"] = qualified; web_runner.STATE["universe_execution_ready"] = ready_count; web_runner.STATE["universe_rejected"] = rejected; web_runner.STATE["universe_rejection_reasons"] = reasons; web_runner.STATE["universe_top"] = cycle_top[:20]; web_runner.STATE["universe_last_cycle_at"] = time.time(); web_runner.STATE["universe_cycle_ms"] = round((time.monotonic() - cycle_start) * 1000, 2); web_runner.STATE["universe_selected"] = min(ready_count, max_entries)
    finally:
        with web_runner.LOCK: web_runner.STATE["ws_connected"] = False; web_runner.STATE["ws_health"] = "stopped"
        external_task.cancel()
        for task in workers: task.cancel()
        await asyncio.gather(external_task, *workers, return_exceptions=True)
