from decimal import Decimal

from src.dragon.triangles import Triangle, evaluate_triangle, evaluate_triangle_outcome
from src.dragon.hardening import _fee_cost_bps


def test_triangle_uses_direct_and_inverse_conversions():
    t = Triangle(("ETHUSDT", "ETHBTC", "BTCUSDT"), ("USDT", "ETH", "BTC"))
    books = {
        "ETHUSDT": {"bids": [[100, 5]], "asks": [[101, 5]], "ts": 1},
        "ETHBTC": {"bids": [[0.0102, 5]], "asks": [[0.0103, 5]], "ts": 1},
        "BTCUSDT": {"bids": [[10000, 5]], "asks": [[10001, 5]], "ts": 1},
    }
    result = evaluate_triangle(t, books, fee_bps=1, slippage_bps=0, notional_usdt=10)
    assert result is not None
    net_bps, gross_bps, path, first, second = result
    assert path == t.symbols
    assert first == "ETH" and second == "BTC"
    assert Decimal("99") < gross_bps < Decimal("100")
    assert net_bps < gross_bps


def test_triangle_gross_edge_is_before_fees():
    t = Triangle(("ETHUSDT", "ETHBTC", "BTCUSDT"), ("USDT", "ETH", "BTC"))
    books = {
        "ETHUSDT": {"bids": [[100, 5]], "asks": [[101, 5]], "ts": 1},
        "ETHBTC": {"bids": [[0.0102, 5]], "asks": [[0.0103, 5]], "ts": 1},
        "BTCUSDT": {"bids": [[10000, 5]], "asks": [[10001, 5]], "ts": 1},
    }
    no_fee = evaluate_triangle(t, books, fee_bps=0, slippage_bps=0, notional_usdt=10)
    with_fee = evaluate_triangle(t, books, fee_bps=10, slippage_bps=0, notional_usdt=10)
    assert no_fee is not None and with_fee is not None
    assert no_fee[1] == with_fee[1]
    assert with_fee[0] < no_fee[0]


def test_triangle_returns_none_without_complete_book():
    t = Triangle(("ETHUSDT", "ETHBTC", "BTCUSDT"), ("USDT", "ETH", "BTC"))
    books = {"ETHUSDT": {"bids": [[100, 5]], "asks": [[101, 5]], "ts": 1}}
    assert evaluate_triangle(t, books, 1, 0) is None


def test_three_leg_fee_is_compounded_not_simple_sum():
    fee = Decimal("10")
    cost = _fee_cost_bps(fee, 3)
    expected = (Decimal("1") - (Decimal("1") - fee / Decimal("10000")) ** 3) * Decimal("10000")
    assert cost == expected
    assert Decimal("29.9") < cost < Decimal("30.0")


def test_fee_cost_scales_with_leg_count():
    fee = Decimal("10")
    assert _fee_cost_bps(fee, 1) == fee
    assert _fee_cost_bps(fee, 2) > fee
    assert _fee_cost_bps(fee, 3) > _fee_cost_bps(fee, 2)


def test_three_leg_fee_15_bps_is_exactly_compounded():
    t = Triangle(("ETHUSDT", "ETHBTC", "BTCUSDT"), ("USDT", "ETH", "BTC"))
    books = {
        "ETHUSDT": {"bids": [[100, 5]], "asks": [[100, 5]], "ts": 1},
        "ETHBTC": {"bids": [[0.01, 5]], "asks": [[0.01, 5]], "ts": 1},
        "BTCUSDT": {"bids": [[10000, 5]], "asks": [[10000, 5]], "ts": 1},
    }
    outcome = evaluate_triangle_outcome(t, books, fee_bps=15, slippage_bps=0, notional_usdt=10)
    assert outcome is not None
    expected_drag = (Decimal("1") - (Decimal("1") - Decimal("15") / Decimal("10000")) ** 3) * Decimal("10000")
    assert outcome["fee_drag_bps"] == expected_drag
    assert Decimal("44.9") < outcome["fee_drag_bps"] < Decimal("45.0")
    assert all(leg["fee_bps"] == "15" for leg in outcome["legs"])


def test_diagnostics_expose_break_even_and_fee_equivalent():
    t = Triangle(("ETHUSDT", "ETHBTC", "BTCUSDT"), ("USDT", "ETH", "BTC"))
    books = {
        "ETHUSDT": {"bids": [[100, 5]], "asks": [[100, 5]], "ts": 1},
        "ETHBTC": {"bids": [[0.01, 5]], "asks": [[0.01, 5]], "ts": 1},
        "BTCUSDT": {"bids": [[10000, 5]], "asks": [[10000, 5]], "ts": 1},
    }
    outcome = evaluate_triangle_outcome(t, books, fee_bps=15, slippage_bps=5, notional_usdt=10)
    assert outcome is not None
    assert outcome["total_fee_equivalent"] > 0
    assert outcome["break_even_gross_bps"] > outcome["fee_drag_bps"]
    assert outcome["cost_to_break_even_bps"] == outcome["break_even_gross_bps"] - outcome["gross_bps"]
    assert "depth_adjusted_gross_bps" in outcome
    assert len(outcome["legs"]) == 3
    assert all("depth_drag_bps" in leg and "top_price" in leg for leg in outcome["legs"])
