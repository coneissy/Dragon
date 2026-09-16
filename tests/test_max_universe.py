from decimal import Decimal
from pathlib import Path

from src.dragon.max_universe import evaluate_cross_exchange, load_profile


PROFILE = load_profile(Path(__file__).parents[1] / "config" / "max_universe_v5.yaml")


def test_profile_has_binance_plus_twelve_observers():
    exchanges = PROFILE["exchanges"]
    assert [k for k, v in exchanges.items() if v["role"] == "EXECUTION"] == ["binance"]
    assert len([k for k, v in exchanges.items() if v["role"] == "OBSERVATION"]) == 12


def test_net_edge_formula_and_position_cap():
    result = evaluate_cross_exchange(
        symbol="BTCUSDT",
        buy_venue="kucoin",
        sell_venue="binance",
        buy_ask="100.00",
        sell_bid="100.50",
        executable_notional_usdt="8.00",
        buy_fee_bps=10,
        sell_fee_bps=10,
        slippage_bps=3,
        latency_penalty_bps=1,
        book_age_ms=100,
        depth_ok=True,
        binance_connected=True,
        observing_platforms=3,
        profile=PROFILE,
    )
    # Exact multiplicative economics: gross ratio 1.005, then two 10-bps
    # fees, 3-bps slippage, and 1-bps latency penalty.
    assert result.gross_edge_bps == Decimal("50.0")
    assert result.fee_bps == Decimal("19.99")
    assert result.net_edge_bps == Decimal("25.89838687730150000")
    assert result.expected_profit_usdt == Decimal("0.019682774486495140000")
    assert result.executable_notional_usdt == Decimal("7.6")
    assert result.gate == "PASS"


def test_three_leg_fee_cost_is_compounded():
    fee = Decimal("15")
    expected_drag = (Decimal("1") - (Decimal("1") - fee / Decimal("10000")) ** 3) * Decimal("10000")
    assert expected_drag == Decimal("44.93250375")


def test_stale_data_is_fatal():
    result = evaluate_cross_exchange(
        symbol="ETHUSDT",
        buy_venue="okx",
        sell_venue="binance",
        buy_ask=100,
        sell_bid=101,
        executable_notional_usdt=5,
        buy_fee_bps=8,
        sell_fee_bps=10,
        slippage_bps=3,
        latency_penalty_bps=1,
        book_age_ms=500,
        depth_ok=True,
        binance_connected=True,
        observing_platforms=1,
        profile=PROFILE,
    )
    assert result.gate == "STALE_DATA"
    assert result.position_pct == 0


def test_disconnected_binance_blocks():
    result = evaluate_cross_exchange(
        symbol="SOLUSDT",
        buy_venue="bybit",
        sell_venue="binance",
        buy_ask=100,
        sell_bid=101,
        executable_notional_usdt=5,
        buy_fee_bps=10,
        sell_fee_bps=10,
        slippage_bps=3,
        latency_penalty_bps=1,
        book_age_ms=50,
        depth_ok=True,
        binance_connected=False,
        observing_platforms=2,
        profile=PROFILE,
    )
    assert result.gate == "BINANCE_CONNECTIVITY"
    assert result.position_pct == 0
