from dataclasses import dataclass
from decimal import Decimal


@dataclass
class RiskState:
    starting_balance_usdt: Decimal = Decimal("0")
    peak_balance_usdt: Decimal = Decimal("0")
    consecutive_losses: int = 0
    kill_switch: bool = False

    def observe_balance(self, balance_usdt: Decimal) -> None:
        balance = Decimal(str(balance_usdt))
        if self.starting_balance_usdt <= 0:
            self.starting_balance_usdt = balance
        if balance > self.peak_balance_usdt:
            self.peak_balance_usdt = balance
        if self.peak_balance_usdt <= 0:
            self.peak_balance_usdt = balance

    def record(
        self,
        pnl_usdt: Decimal,
        balance_usdt: Decimal,
        *,
        max_consecutive_losses: int = 5,
        max_drawdown_pct: float = 15.0,
    ) -> None:
        pnl = Decimal(str(pnl_usdt))
        balance = Decimal(str(balance_usdt))
        self.observe_balance(balance)
        if pnl < 0:
            self.consecutive_losses += 1
        elif pnl > 0:
            self.consecutive_losses = 0
        if self.consecutive_losses >= max_consecutive_losses:
            self.kill_switch = True
            return
        if self.peak_balance_usdt > 0:
            drawdown = (
                (self.peak_balance_usdt - balance)
                / self.peak_balance_usdt
                * Decimal("100")
            )
            if drawdown >= Decimal(str(max_drawdown_pct)):
                self.kill_switch = True

    def can_trade(self) -> bool:
        return not self.kill_switch


class MaxUniverseRiskState(RiskState):
    """Compatibility state for the max-universe risk policy."""


RISK_STATE = RiskState()


def approved(
    net_bps: Decimal,
    min_net_bps: float,
    notional: Decimal,
    max_notional: float,
    *,
    min_trade_notional: Decimal | None = None,
    risk_state: RiskState | None = None,
) -> bool:
    state = risk_state or RISK_STATE
    if not state.can_trade() or net_bps < Decimal(str(min_net_bps)) or notional <= 0:
        return False
    if max_notional > 0 and notional > Decimal(str(max_notional)):
        return False
    if min_trade_notional is not None and notional < min_trade_notional:
        return False
    return True


def risk_budget(
    free_usdt: Decimal,
    allocation_or_legacy: float,
    max_notional: float,
    min_trade_notional: Decimal | None = None,
    *,
    safety_reserve_usdt: Decimal = Decimal("0"),
    risk_state: RiskState | None = None,
    capital_allocation_pct: float | None = None,
) -> Decimal:
    """Return deployable capital while accepting the legacy call shape."""
    state = risk_state or RISK_STATE
    free = Decimal(str(free_usdt))
    if not state.can_trade() or free <= 0:
        return Decimal("0")
    available = free - max(Decimal("0"), Decimal(str(safety_reserve_usdt)))
    if available <= 0:
        return Decimal("0")
    pct_value = capital_allocation_pct if capital_allocation_pct is not None else allocation_or_legacy
    pct = Decimal(str(pct_value))
    if pct <= 0:
        return Decimal("0")
    budget = available * pct
    if max_notional > 0:
        budget = min(budget, Decimal(str(max_notional)))
    if min_trade_notional is not None and budget < min_trade_notional:
        return Decimal("0")
    return budget
