from dataclasses import dataclass
from decimal import Decimal


@dataclass
class MaxUniverseRiskState:
    """Runtime guardrails from the MAX UNIVERSE v5 workbook."""

    peak_balance_usdt: Decimal = Decimal("0")
    consecutive_losses: int = 0
    kill_switch: bool = False

    def observe_balance(self, balance_usdt: Decimal) -> None:
        balance = Decimal(str(balance_usdt))
        if balance > self.peak_balance_usdt:
            self.peak_balance_usdt = balance

    def record(self, pnl_usdt: Decimal, balance_usdt: Decimal, *, max_consecutive_losses: int = 5, max_drawdown_pct: float = 15.0) -> None:
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
            drawdown_pct = (self.peak_balance_usdt - balance) / self.peak_balance_usdt * Decimal("100")
            if drawdown_pct >= Decimal(str(max_drawdown_pct)):
                self.kill_switch = True

    def can_trade(self) -> bool:
        return not self.kill_switch


def approved(
    net_bps: Decimal,
    min_net_bps: float,
    notional: Decimal,
    max_notional: float,
    *,
    min_trade_notional: Decimal | None = None,
    risk_state: MaxUniverseRiskState | None = None,
) -> bool:
    if risk_state is not None and not risk_state.can_trade():
        return False
    if net_bps < Decimal(str(min_net_bps)):
        return False
    if not Decimal("0") < notional:
        return False
    # max_notional=0 means dynamic/no fixed dollar cap.
    if max_notional > 0 and notional > Decimal(str(max_notional)):
        return False
    if min_trade_notional is not None and notional < min_trade_notional:
        return False
    return True


def risk_budget(
    free_usdt: Decimal,
    risk_pct: float,
    max_notional: float,
    min_trade_notional: Decimal | None = None,
    *,
    capital_allocation_pct: float | None = None,
    safety_reserve_usdt: Decimal = Decimal("0"),
) -> Decimal:
    """Calculate the MAX UNIVERSE executable budget.

    95% of tradeable balance is allocated per entry after the 1 USDT reserve.
    A positive max_notional remains an optional hard ceiling; zero means no
    fixed dollar ceiling so the allocation can compound with the account.
    """
    if free_usdt <= 0:
        return Decimal("0")
    if Decimal(str(risk_pct)) <= 0:
        return Decimal("0")
    reserve = max(Decimal("0"), Decimal(str(safety_reserve_usdt)))
    available = free_usdt - reserve
    if available <= 0:
        return Decimal("0")
    pct = Decimal(str(capital_allocation_pct if capital_allocation_pct is not None else risk_pct))
    if pct <= 0:
        return Decimal("0")
    budget = available * pct
    if max_notional > 0:
        budget = min(budget, Decimal(str(max_notional)))
    if min_trade_notional is not None and budget < min_trade_notional:
        return Decimal("0")
    return budget
