from __future__ import annotations

"""MAX UNIVERSE v5 opportunity math.

Pure opportunity evaluation for the supplied workbook profile. Observation
venues remain signal-only; Binance remains the execution venue.
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("PyYAML is required for the MAX UNIVERSE profile") from exc


@dataclass(frozen=True)
class Opportunity:
    symbol: str
    buy_venue: str
    sell_venue: str
    gross_edge_bps: Decimal
    fee_bps: Decimal
    slippage_bps: Decimal
    latency_penalty_bps: Decimal
    net_edge_bps: Decimal
    executable_notional_usdt: Decimal
    expected_profit_usdt: Decimal
    score: Decimal
    tier: str
    position_pct: Decimal
    gate: str


def load_profile(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        profile = yaml.safe_load(fh) or {}
    validate_profile(profile)
    return profile


def validate_profile(profile: Mapping[str, Any]) -> None:
    dragon = profile.get("dragon", {})
    if dragon.get("base_currency") != "USDT":
        raise ValueError("MAX UNIVERSE profile must use USDT")
    if Decimal(str(dragon.get("starting_balance", 0))) != Decimal("9"):
        raise ValueError("MAX UNIVERSE starting balance must be 9 USDT")
    if Decimal(str(dragon.get("reserve_balance", 0))) != Decimal("1"):
        raise ValueError("MAX UNIVERSE reserve must be 1 USDT")

    exchanges = profile.get("exchanges", {})
    execution = [k for k, v in exchanges.items() if v.get("role") == "EXECUTION"]
    observation = [k for k, v in exchanges.items() if v.get("role") == "OBSERVATION"]
    if execution != ["binance"]:
        raise ValueError("Binance must be the sole execution venue")
    if len(observation) != 12:
        raise ValueError("MAX UNIVERSE requires exactly 12 observation venues")

    gates = profile.get("hard_gates", {})
    for name in ("binance_connectivity", "fresh_data", "liquidity_check"):
        if not gates.get(name, {}).get("fatal", False):
            raise ValueError(f"{name} must be a fatal gate")


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _tier(score: Decimal, tiers: Mapping[str, Any]) -> tuple[str, Decimal]:
    ordered = sorted(
        ((k, _d(v)) for k, v in tiers.items() if k != "reject_below"),
        key=lambda item: item[1],
        reverse=True,
    )
    for name, threshold in ordered:
        if score >= threshold:
            return name, threshold
    return "REJECT", _d(tiers.get("reject_below", 40))


def score_opportunity(*, gross_edge_bps: Any, liquidity_score: Any, persistence_score: Any, penalties: Any = 0, observing_platforms: int = 1, profile: Mapping[str, Any]) -> tuple[Decimal, str, Decimal]:
    scoring = profile["opportunity_scoring"]
    weights = scoring["weights"]
    bonus_cfg = profile["multi_platform_scoring"]["bonuses"]
    if observing_platforms >= 7:
        bonus = _d(bonus_cfg["seven_plus"])
    elif observing_platforms >= 4:
        bonus = _d(bonus_cfg["four_to_six"])
    elif observing_platforms >= 2:
        bonus = _d(bonus_cfg["two_to_three"])
    else:
        bonus = _d(bonus_cfg["single_platform"])
    score = (
        _d(gross_edge_bps) * _d(weights["edge"])
        + _d(liquidity_score) * _d(weights["liquidity"])
        + _d(persistence_score) * _d(weights["persistence"])
        - min(_d(penalties), _d(scoring["penalty_max"]))
        + bonus
    score = max(Decimal("0"), min(Decimal("100"), score))
    tier, _ = _tier(score, scoring["tiers"])
    return score, tier, bonus


def evaluate_cross_exchange(*, symbol: str, buy_venue: str, sell_venue: str, buy_ask: Any, sell_bid: Any, executable_notional_usdt: Any, buy_fee_bps: Any, sell_fee_bps: Any, slippage_bps: Any, latency_penalty_bps: Any, book_age_ms: Any, depth_ok: bool, binance_connected: bool, observing_platforms: int, liquidity_score: Any = 100, persistence_score: Any = 100, penalties: Any = 0, profile: Mapping[str, Any] | None = None) -> Opportunity:
    if profile is None:
        raise ValueError("profile is required")
    validate_profile(profile)
    gross = (_d(sell_bid) / _d(buy_ask) - Decimal("1")) * Decimal("10000")
    fee = _d(buy_fee_bps) + _d(sell_fee_bps)
    net = gross - fee - _d(slippage_bps) - _d(latency_penalty_bps)
    notional = max(Decimal("0"), _d(executable_notional_usdt))
    score, tier, _ = score_opportunity(gross_edge_bps=gross, liquidity_score=liquidity_score, persistence_score=persistence_score, penalties=penalties, observing_platforms=observing_platforms, profile=profile)
    min_score = _d(profile["opportunity_scoring"]["min_entry_score"])
    min_edge = _d(profile["hard_gates"]["min_edge"]["condition"].split(">=")[1].strip())
    stale_text = profile["hard_gates"]["fresh_data"]["condition"]
    stale_limit = Decimal(stale_text.split("(")[1].split("ms")[0].strip())
    position_limit = _d(profile["dragon"]["tradeable_balance"]) * _d(profile["compounding"]["position_pct"]) / Decimal("100")

    if not binance_connected:
        gate, position_pct = "BINANCE_CONNECTIVITY", Decimal("0")
    elif _d(book_age_ms) >= stale_limit:
        gate, position_pct = "STALE_DATA", Decimal("0")
    elif not depth_ok or notional <= 0:
        gate, position_pct = "LIQUIDITY", Decimal("0")
    elif notional > position_limit:
        gate, position_pct = "MAX_POSITION", Decimal("0")
    elif net < min_edge:
        gate, position_pct = "NET_EDGE", Decimal("0")
    elif score < min_score:
        gate, position_pct = "SCORE", Decimal("0")
    else:
        gate = "PASS"
        position_pct = _d(profile["opportunity_scoring"]["entry_sizes"].get(tier, 0))

    executable = min(notional, position_limit)
    expected = executable * net / Decimal("10000")
    return Opportunity(symbol=symbol, buy_venue=buy_venue, sell_venue=sell_venue, gross_edge_bps=gross, fee_bps=fee, slippage_bps=_d(slippage_bps), latency_penalty_bps=_d(latency_penalty_bps), net_edge_bps=net, executable_notional_usdt=executable, expected_profit_usdt=expected, score=score, tier=tier, position_pct=position_pct, gate=gate)
