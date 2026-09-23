from decimal import Decimal

import pytest

from src.dragon.dex_safety import (
    DexSafetyGate,
    DexSafetyPolicy,
    DexSafetyRequest,
    SafetyStatus,
    approve_or_raise,
)


def request(**overrides):
    values = dict(
        chain="ethereum",
        venue="uniswap_v3",
        sell_token="0xSELL",
        buy_token="0xBUY",
        pool="0xPOOL",
        router="0xROUTER",
        recipient="0xRECIPIENT",
        quote_age_ms=100,
        liquidity_quote=Decimal("100000"),
        slippage_bps=Decimal("10"),
        gas_quote=Decimal("2"),
        net_edge_bps=Decimal("10"),
        simulation_ok=True,
        contracts_verified=True,
        calldata_valid=True,
    )
    values.update(overrides)
    return DexSafetyRequest(**values)


def gate():
    return DexSafetyGate(
        DexSafetyPolicy(
            max_quote_age_ms=500,
            max_slippage_bps=Decimal("30"),
            min_liquidity_quote=Decimal("1000"),
            min_net_edge_bps=Decimal("3"),
            safety_buffer_bps=Decimal("2"),
            allowed_chains=frozenset({"ethereum"}),
            allowed_venues=frozenset({"uniswap_v3", "sushi"}),
            allowed_tokens=frozenset({"0xsell", "0xbuy"}),
        )
    )


def test_valid_route_is_approved():
    assert gate().evaluate(request()).status is SafetyStatus.APPROVED


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"quote_age_ms": 501}, "quote stale"),
        ({"liquidity_quote": Decimal("999")}, "insufficient liquidity"),
        ({"slippage_bps": Decimal("31")}, "slippage limit exceeded"),
        ({"net_edge_bps": Decimal("4")}, "net edge below safety threshold"),
        ({"simulation_ok": False}, "transaction simulation failed"),
        ({"contracts_verified": False}, "contracts not verified"),
        ({"calldata_valid": False}, "calldata validation failed"),
    ],
)
def test_unsafe_route_is_rejected(override, reason):
    decision = gate().evaluate(request(**override))
    assert decision.status is SafetyStatus.REJECTED
    assert reason in decision.reasons


def test_unknown_venue_is_rejected():
    decision = gate().evaluate(request(venue="unknown_dex"))
    assert "DEX venue not allowlisted" in decision.reasons


def test_approval_helper_raises_on_rejection():
    with pytest.raises(RuntimeError, match="DEX safety gate rejected"):
        approve_or_raise(gate(), request(net_edge_bps=Decimal("4")))
