from decimal import Decimal

from src.dragon.risk import MaxUniverseRiskState, risk_budget


def test_max_universe_initial_budget_is_95_percent_of_tradeable_balance():
    assert risk_budget(
        Decimal("9"),
        0.0015,
        0,
        Decimal("5"),
        capital_allocation_pct=0.95,
        safety_reserve_usdt=Decimal("1"),
    ) == Decimal("7.60")


def test_dynamic_budget_compounds():
    assert risk_budget(
        Decimal("20"),
        0.0015,
        0,
        Decimal("5"),
        capital_allocation_pct=0.95,
        safety_reserve_usdt=Decimal("1"),
    ) == Decimal("18.05")


def test_five_losses_trigger_kill_switch():
    state = MaxUniverseRiskState()
    for _ in range(5):
        state.record(Decimal("-0.01"), Decimal("8.0"))
    assert state.kill_switch is True
    assert state.can_trade() is False


def test_fifteen_percent_drawdown_triggers_kill_switch():
    state = MaxUniverseRiskState()
    state.observe_balance(Decimal("10"))
    state.record(Decimal("-1.5"), Decimal("8.5"))
    assert state.kill_switch is True
