__version__ = "0.1.0"

# Publish authenticated Spot fee state at the earliest stable package boundary.
# This runs before src.dragon.main is imported by the engine, so the live
# scanner cannot miss the fee observation because of later monkey-patching.
def _install_fee_state_bridge():
    try:
        from decimal import Decimal
        from time import time
        from src.dragon.binance import BinanceClient

        if getattr(BinanceClient.account, "_dragon_fee_bridge", False):
            return

        original_account = BinanceClient.account

        def account_with_fee_state(client, *args, **kwargs):
            account = original_account(client, *args, **kwargs)
            try:
                rows = client.trade_fee()
                takers = []
                for row in rows if isinstance(rows, list) else []:
                    if not isinstance(row, dict):
                        continue
                    raw = row.get("takerCommission", row.get("taker"))
                    if raw is None:
                        raw = row.get("takerCommissionRate")
                    if raw is None:
                        continue
                    rate = Decimal(str(raw))
                    if Decimal("0") <= rate <= Decimal("1"):
                        takers.append(rate * Decimal("10000"))
                if not takers:
                    raise RuntimeError("no valid taker rate returned")
                value = max(takers)
                import src.dragon.main as main
                fee_cost = (Decimal("1") - (Decimal("1") - value / Decimal("10000")) ** 3) * Decimal("10000")
                with main.LOCK:
                    main.STATE["dynamic_fee_bps"] = float(value)
                    main.STATE["fee_cost_bps"] = float(fee_cost)
                    main.STATE["fee_source"] = "authenticated_trade_fee"
                    main.STATE["fee_error"] = None
                    main.STATE["last_fee_refresh"] = time()
                main.event("FEE", f"Fee source=authenticated_trade_fee; taker={value:.4f} bps; 3-leg cost={fee_cost:.4f} bps")
            except Exception as exc:
                try:
                    import src.dragon.main as main
                    fallback = Decimal(str(main.Config.from_env().fee_bps))
                    fee_cost = (Decimal("1") - (Decimal("1") - fallback / Decimal("10000")) ** 3) * Decimal("10000")
                    with main.LOCK:
                        main.STATE["dynamic_fee_bps"] = float(fallback)
                        main.STATE["fee_cost_bps"] = float(fee_cost)
                        main.STATE["fee_source"] = "configured_fallback"
                        main.STATE["fee_error"] = str(exc)[:300]
                        main.STATE["last_fee_refresh"] = time()
                    main.event("FEE", f"Fee source=configured_fallback; taker={fallback:.4f} bps; error={str(exc)[:300]}")
                except Exception:
                    pass
            return account

        account_with_fee_state._dragon_fee_bridge = True
        BinanceClient.account = account_with_fee_state
    except Exception:
        pass


_install_fee_state_bridge()
