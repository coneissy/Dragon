from decimal import Decimal

from src.dragon.calculator import calculate


def books():
    return {
        "AUSDT": {"asks": [["10", "100"]], "bids": [["9.9", "100"]]},
        "AB": {"asks": [["2.01", "100"]], "bids": [["2", "100"]]},
        "BUSDT": {"asks": [["5.01", "100"]], "bids": [["6", "100"]]},
    }


def meta():
    return {"AUSDT": ("A", "USDT"), "AB": ("A", "B"), "BUSDT": ("B", "USDT")}


def test_three_leg_depth_math_returns_expected_final_usdt():
    result = calculate(("AUSDT", "AB", "BUSDT"), ("USDT", "A", "B"), books(), meta(), Decimal("10"), Decimal("0"), Decimal("0"))
    assert result is not None
    assert result.final_usdt == Decimal("12")
    assert result.net_bps == Decimal("2000")
    assert result.profitable


def test_fee_is_applied_once_per_leg():
    result = calculate(("AUSDT", "AB", "BUSDT"), ("USDT", "A", "B"), books(), meta(), Decimal("10"), Decimal("10"), Decimal("0"))
    assert result is not None
    # 10 bps = 0.10%, so each leg retains 0.999.
    expected = Decimal("10") * Decimal("0.999") ** 3 * Decimal("1.2")
    assert abs(result.final_usdt - expected) < Decimal("0.0000000001")


def test_missing_depth_rejects_path():
    data = books()
    data["AB"]["bids"] = []
    assert calculate(("AUSDT", "AB", "BUSDT"), ("USDT", "A", "B"), data, meta(), Decimal("10"), Decimal("0"), Decimal("0")) is None
