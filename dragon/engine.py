import asyncio
import json
import os
import random
import time
from pathlib import Path
from urllib.parse import urlsplit


async def _production_feed_worker(cfg, symbols, queue, worker_id):
    """Stream Binance Spot partial-depth snapshots with resilient reconnects."""
    import websockets
    import web_runner
    import full_universe_runner_v3

    streams = [f"{s.lower()}@depth{cfg.depth_levels}@100ms" for s in symbols]
    if not streams:
        return

    base = cfg.ws_base.rstrip("/")
    if base.endswith("/ws"):
        base = base[:-3]
    if not base.endswith("/stream"):
        base += "/stream"
    url = base + "?streams=" + "/".join(streams)

    # Keep the retry state across short-lived/flapping connections. A connection
    # only earns a backoff reset after it has stayed healthy for this long.
    retry_attempt = 0
    base_delay = 1.0
    max_delay = 60.0
    stable_reset_after = 30.0

    while True:
        connected_at = None
        try:
            full_universe_runner_v3._ws_state(worker_id, status="connecting")
            async with websockets.connect(
                url,
                ping_interval=15,
                ping_timeout=30,
                close_timeout=5,
                open_timeout=15,
                max_size=2**24,
                max_queue=4096,
                compression=None,
            ) as ws:
                connected_at = time.monotonic()
                full_universe_runner_v3._ws_state(worker_id, status="connected", ws_next_retry_at=None)
                web_runner.event(
                    "WS",
                    f"Spot shard {worker_id} connected; combined partial-depth stream active",
                    symbols=len(symbols),
                    streams=len(streams),
                    endpoint=base,
                    retry_attempt=retry_attempt,
                )
                last_message = time.monotonic()
                first_depth_seen = False

                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    except asyncio.TimeoutError as exc:
                        raise ConnectionError(
                            f"market-data stream silent for {time.monotonic() - last_message:.1f}s"
                        ) from exc
                    last_message = time.monotonic()
                    full_universe_runner_v3._metric("ws_raw_messages")

                    try:
                        payload = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
                    except Exception:
                        full_universe_runner_v3._metric("ws_invalid_messages")
                        continue

                    if isinstance(payload, dict) and "result" in payload and "id" in payload:
                        web_runner.event(
                            "WS_ACK",
                            f"Spot shard {worker_id} stream control response",
                            shard=worker_id,
                            request_id=payload.get("id"),
                            result=payload.get("result"),
                        )
                        continue

                    if not isinstance(payload, dict):
                        continue

                    envelope = payload.get("data", payload)
                    stream_name = str(payload.get("stream", ""))
                    if not isinstance(envelope, dict):
                        continue

                    symbol = str(envelope.get("s", "")).upper()
                    bids = envelope.get("b", [])
                    asks = envelope.get("a", [])
                    if not symbol and stream_name:
                        symbol = stream_name.split("@", 1)[0].upper()
                        bids = envelope.get("bids", [])
                        asks = envelope.get("asks", [])

                    if not symbol or not bids or not asks:
                        full_universe_runner_v3._metric("ws_non_market_messages")
                        continue

                    normalized = {
                        "s": symbol,
                        "b": bids,
                        "a": asks,
                        "_source": "binance_ws_depth",
                    }
                    if not first_depth_seen:
                        first_depth_seen = True
                        web_runner.event(
                            "WS_DEPTH",
                            f"Spot shard {worker_id} first valid partial-depth snapshot",
                            shard=worker_id,
                            symbol=symbol,
                            bids=len(bids),
                            asks=len(asks),
                        )

                    with web_runner.LOCK:
                        web_runner.STATE["last_ws_depth_update"] = time.time()
                        web_runner.STATE["market_data_updates"] = web_runner.STATE.get("market_data_updates", 0) + 1

                    if queue.full():
                        try:
                            queue.get_nowait()
                            full_universe_runner_v3._metric("market_queue_drops")
                        except asyncio.QueueEmpty:
                            pass
                    await queue.put(normalized)

        except asyncio.CancelledError:
            full_universe_runner_v3._ws_state(worker_id, status="stopped")
            raise
        except Exception as exc:
            with web_runner.LOCK:
                web_runner.STATE["ws_disconnects"] = web_runner.STATE.get("ws_disconnects", 0) + 1

            uptime = (time.monotonic() - connected_at) if connected_at is not None else 0.0
            # Only reset after a genuinely stable connection. This prevents a
            # flapping socket from repeatedly reconnecting at 1 second forever.
            if uptime >= stable_reset_after:
                retry_attempt = 0
            else:
                retry_attempt += 1

            exponent = max(0, retry_attempt - 1)
            raw_delay = min(max_delay, base_delay * (2 ** exponent))
            jitter = random.uniform(0.0, min(5.0, raw_delay * 0.25))
            wait = min(max_delay, raw_delay + jitter)

            full_universe_runner_v3._ws_state(
                worker_id,
                status="reconnecting",
                ws_last_disconnect=time.time(),
                ws_next_retry_at=time.time() + wait,
            )
            web_runner.event(
                "WS_ERROR",
                f"Spot shard {worker_id} disconnected: {exc}; reconnecting with backoff",
                shard=worker_id,
                retry_attempt=retry_attempt,
                connection_uptime_s=round(uptime, 2),
                retry_delay_s=round(wait, 2),
                reset_after_s=stable_reset_after,
            )
            await asyncio.sleep(wait)
            full_universe_runner_v3._metric("ws_reconnects")


async def run_production():
    """Run exactly one Dragon Spot full-universe engine plus its dashboard."""
    os.environ.setdefault("PORT", "10000")

    import web_runner
    import full_universe_runner_v3
    from src.dragon.binance import BinanceClient
    from src.dragon.config import Config
    from src.dragon.ledger import Ledger
    from src.dragon.triangles import build_triangles

    original_get = web_runner.Handler.do_GET
    dashboard_path = Path(__file__).resolve().parent.parent / "dashboard.html"

    def robust_get(self):
        raw_path = self.path or "/"
        normalized = urlsplit(raw_path).path or "/"
        if normalized in ("/dashboard", "/dashboard/"):
            if dashboard_path.is_file():
                body = dashboard_path.read_bytes()
            else:
                body = web_runner.DASHBOARD.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.path = normalized
        try:
            return original_get(self)
        finally:
            self.path = raw_path

    web_runner.Handler.do_GET = robust_get
    web_runner.start_health_server()
    print(
        f"DRAGON HTTP | dashboard listening on 0.0.0.0:{os.environ['PORT']} routes=/,/dashboard,/health,/healthz",
        flush=True,
    )

    cfg = Config.from_env()
    cfg.validate()

    with web_runner.LOCK:
        web_runner.STATE["started_at"] = time.time()
        web_runner.STATE["live"] = cfg.live_trading
        web_runner.STATE["dry_run"] = cfg.dry_run
        web_runner.STATE["status"] = "starting"

    web_runner.LEDGER = Ledger()
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    client = BinanceClient(cfg.api_base, api_key, api_secret)

    try:
        if cfg.live_trading and not cfg.dry_run:
            if not api_key or not api_secret:
                raise RuntimeError("LIVE_TRADING requires BINANCE_API_KEY and BINANCE_API_SECRET")
            account = client.account()
            with web_runner.LOCK:
                web_runner.STATE["binance_authenticated"] = True
            free = next((x.get("free", "0") for x in account.get("balances", []) if x.get("asset") == "USDT"), "0")
            with web_runner.LOCK:
                web_runner.STATE["free_usdt"] = str(free)
            web_runner.event("AUTH", "Binance API authenticated successfully", usdt_free=str(free))
        else:
            web_runner.event("SAFE", "Live execution disabled")

        info = client.exchange_info()
        filters = web_runner.make_filters(info)
        symbol_meta = web_runner._symbol_meta(info)
        triangles = build_triangles(info, cfg.max_triangles)
        symbols = sorted({s for triangle in triangles for s in triangle.symbols})

        with web_runner.LOCK:
            web_runner.STATE["triangles"] = len(triangles)
            web_runner.STATE["symbols"] = len(symbols)
            web_runner.STATE["market_streams"] = len(symbols)

        web_runner.event("UNIVERSE", f"FULL SPOT UNIVERSE active: triangles={len(triangles)} symbols={len(symbols)}")
        web_runner.event("START", f"Dragon full-universe engine ready; live={cfg.live_trading and not cfg.dry_run}")

        full_universe_runner_v3._feed_worker = _production_feed_worker
        await full_universe_runner_v3.full_universe_stream_loop(
            cfg, client, filters, triangles, symbols, symbol_meta
        )
    finally:
        client.close()


def main():
    asyncio.run(run_production())


if __name__ == "__main__":
    main()
