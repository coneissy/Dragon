"""Dual-venue MAX connectivity supervisor.

This module performs read-only connectivity and universe-discovery checks for
Binance Spot and Binance USDⓈ-M Futures. It never submits orders.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .binance import BinanceClient, BinanceError
from .futures import FuturesClient, FuturesError


def _publish(**updates: Any) -> None:
    try:
        from .main import _state
        _state(**updates)
    except Exception as exc:
        print(f"DRAGON CONNECTIVITY | state publish failed: {exc!s}", flush=True)


def _spot_max_universe(info: dict) -> dict[str, int]:
    rows = info.get("symbols", []) if isinstance(info, dict) else []
    trading = [row for row in rows if row.get("status") == "TRADING"]
    usdt = [row for row in trading if row.get("quoteAsset") == "USDT"]
    return {
        "all_trading_symbols": len(trading),
        "usdt_spot_symbols": len(usdt),
    }


def _futures_max_universe(info: dict) -> dict[str, int]:
    rows = info.get("symbols", []) if isinstance(info, dict) else []
    trading = [row for row in rows if row.get("status") == "TRADING"]
    perpetuals = [
        row for row in trading
        if row.get("contractType") == "PERPETUAL"
        and row.get("quoteAsset") == "USDT"
        and row.get("marginAsset") == "USDT"
    ]
    return {
        "all_trading_contracts": len(trading),
        "usdt_perpetuals": len(perpetuals),
    }


def _spot_probe(client: BinanceClient, key: str, secret: str) -> dict:
    info = client.exchange_info()
    universe = _spot_max_universe(info)
    ticker = client.book_ticker()
    ticker_rows = ticker if isinstance(ticker, list) else []
    result = {
        "public": "CONNECTED",
        "exchange_info": "CONNECTED",
        "book_ticker": "CONNECTED" if ticker_rows else "DEGRADED",
        "max_universe": universe,
        "book_ticker_symbols": len(ticker_rows),
        "authenticated": "NOT_CONFIGURED",
    }
    if key and secret:
        try:
            client.account()
            result["authenticated"] = "CONNECTED"
        except Exception as exc:
            result["authenticated"] = "FAILED"
            result["auth_error"] = str(exc)[:300]
    return result


def _futures_probe(client: FuturesClient, key: str, secret: str) -> dict:
    client.sync_time()
    info = client.exchange_info()
    universe = _futures_max_universe(info)
    tickers = client.all_book_tickers()
    marks = client.all_mark_prices()
    ticker_rows = tickers if isinstance(tickers, list) else []
    mark_rows = marks if isinstance(marks, list) else []
    result = {
        "public": "CONNECTED",
        "server_time": "CONNECTED",
        "exchange_info": "CONNECTED",
        "book_ticker": "CONNECTED" if ticker_rows else "DEGRADED",
        "mark_price": "CONNECTED" if mark_rows else "DEGRADED",
        "max_universe": universe,
        "book_ticker_symbols": len(ticker_rows),
        "mark_price_symbols": len(mark_rows),
        "authenticated": "NOT_CONFIGURED",
        "live_trading": False,
    }
    if key and secret:
        try:
            client.account()
            result["authenticated"] = "CONNECTED"
        except Exception as exc:
            result["authenticated"] = "FAILED"
            result["auth_error"] = str(exc)[:300]
    return result


def run_connectivity_loop() -> None:
    key = os.getenv("BINANCE_API_KEY", "").strip()
    secret = os.getenv("BINANCE_API_SECRET", "").strip()
    spot_base = os.getenv("BINANCE_API_BASE", "https://api.binance.com")
    futures_base = os.getenv("BINANCE_FUTURES_API_BASE", "https://fapi.binance.com")
    poll_seconds = max(15.0, float(os.getenv("CONNECTIVITY_POLL_SECONDS", "60")))
    auth_refresh_seconds = max(60.0, float(os.getenv("CONNECTIVITY_AUTH_REFRESH_SECONDS", "300")))
    last_auth_probe = 0.0

    spot = BinanceClient(spot_base, key, secret)
    futures = FuturesClient(futures_base, key, secret)
    try:
        while True:
            started = time.perf_counter()
            should_probe_auth = bool(key and secret) and (time.monotonic() - last_auth_probe >= auth_refresh_seconds)
            try:
                spot_result = _spot_probe(spot, key if should_probe_auth else "", secret if should_probe_auth else "")
            except (BinanceError, Exception) as exc:
                spot_result = {"public": "FAILED", "error": str(exc)[:400]}

            try:
                futures_result = _futures_probe(futures, key if should_probe_auth else "", secret if should_probe_auth else "")
            except (FuturesError, Exception) as exc:
                futures_result = {"public": "FAILED", "error": str(exc)[:400], "live_trading": False}

            if should_probe_auth:
                last_auth_probe = time.monotonic()

            state = {
                "mode": "MAX_DISCOVERY",
                "timestamp": time.time(),
                "spot": spot_result,
                "futures": futures_result,
                "cycle_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            _publish(connectivity=state)
            print(
                "DRAGON CONNECTIVITY | "
                f"SPOT_MAX={spot_result.get('max_universe', {})} "
                f"SPOT_AUTH={spot_result.get('authenticated')} "
                f"FUTURES_MAX={futures_result.get('max_universe', {})} "
                f"FUTURES_AUTH={futures_result.get('authenticated')} "
                "FUTURES_LIVE=false",
                flush=True,
            )
            time.sleep(poll_seconds)
    finally:
        spot.close()
        futures.close()
