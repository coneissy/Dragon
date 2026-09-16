from pathlib import Path

from src.dragon.max_universe import load_profile, score_opportunity


PROFILE = load_profile(Path(__file__).parents[1] / "config" / "max_universe_v5.yaml")


def test_workbook_universe_contract():
    assert PROFILE["dragon"]["starting_balance"] == 9.0
    assert PROFILE["dragon"]["reserve_balance"] == 1.0
    assert PROFILE["compounding"]["tradeable_balance"] == 8.0
    assert PROFILE["compounding"]["position_pct"] == 95
    assert PROFILE["hard_gates"]["min_edge"]["condition"] == "net_profit_bps >= 3"
    assert PROFILE["risk_limits"]["max_consecutive_losses"] == 5
    assert PROFILE["risk_limits"]["max_drawdown_pct"] == 15
    assert PROFILE["risk_limits"]["position_timeout_seconds"] == 15
    assert PROFILE["trading_modes"]["spot_triangular"]["allocation"] == 30
    assert PROFILE["trading_modes"]["spot_perp"]["allocation"] == 70


def test_workbook_tiers_are_ordered_as_specified():
    expected = {"S": 90, "A_plus": 85, "A": 80, "B_plus": 70, "B": 60, "C": 40}
    assert {k: PROFILE["opportunity_scoring"]["tiers"][k] for k in expected} == expected
    for score, tier in [(95, "S"), (87, "A_plus"), (82, "A"), (75, "B_plus"), (65, "B"), (50, "C")]:
        _, actual, _ = score_opportunity(
            gross_edge_bps=score,
            liquidity_score=0,
            persistence_score=0,
            observing_platforms=1,
            profile=PROFILE,
        )
        assert actual == tier
