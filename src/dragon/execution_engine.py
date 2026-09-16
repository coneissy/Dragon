from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping


class ExecutionGateError(RuntimeError):
    """A hard pre-trade condition prevented execution."""


@dataclass(frozen=True)
class ExecutionRequest:
    symbol: str
    buy_venue: str
    sell_venue: str
    net_edge_bps: Decimal
    executable_notional_usdt: Decimal
    signal_ts_ms: float
    path: tuple[str, ...] | None = None
    start_asset: str = "USDT"
    first_asset: str | None = None


class ExecutionEngine:
    """Final execution gate.

    Observation venues are signals only. A cross-exchange opportunity is not
    executed unless both execution legs are explicitly supported. Binance
    triangular execution is delegated to Dragon's existing atomic-ish
    three-leg executor, which performs order-status and balance reconciliation.
    """

    def __init__(self, *, config: Mapping[str, Any], event: Callable | None = None):
        self.config = config
        self.event = event
        self.enabled = bool(config.get("enabled", False))
        self.max_signal_age_ms = Decimal(str(config.get("max_signal_age_ms", 250)))
        self.max_position_pct = Decimal(str(config.get("max_position_pct", 95)))
        self.safety_reserve_usdt = Decimal(str(config.get("safety_reserve_usdt", 1)))
        self.min_net_edge_bps = Decimal(str(config.get("min_net_edge_bps", 3)))
        self.kill_switch = bool(config.get("emergency_kill_switch", True))
        self._halted = False

    def _event(self, kind: str, message: str, **data: Any) -> None:
        if self.event:
            try:
                self.event(kind, message, **data)
            except Exception:
                pass

    def halt(self, reason: str) -> None:
        self._halted = True
        self._event("KILL", reason)

    def reset(self) -> None:
        self._halted = False
        self._event("KILL", "execution halt reset")

    def preflight(
        self,
        request: ExecutionRequest,
        *,
        free_usdt: Decimal,
        now_ms: float | None = None,
        binance_connected: bool,
        binance_authenticated: bool,
        depth_ok: bool,
    ) -> Decimal:
        now = Decimal(str(now_ms if now_ms is not None else time.monotonic() * 1000))
        age = now - Decimal(str(request.signal_ts_ms))

        if not self.enabled:
            raise ExecutionGateError("execution disabled")
        if self._halted and self.kill_switch:
            raise ExecutionGateError("emergency kill switch is active")
        if not binance_connected:
            raise ExecutionGateError("Binance connectivity gate failed")
        if not binance_authenticated:
            raise ExecutionGateError("Binance authentication gate failed")
        if age < 0 or age > self.max_signal_age_ms:
            raise ExecutionGateError(f"signal stale: age_ms={age}")
        if not depth_ok:
            raise ExecutionGateError("executable depth gate failed")
        if request.net_edge_bps < self.min_net_edge_bps:
            raise ExecutionGateError(
                f"net edge below execution threshold: {request.net_edge_bps} < {self.min_net_edge_bps} bps"
            )
        if request.executable_notional_usdt <= 0:
            raise ExecutionGateError("executable notional must be positive")

        available = max(Decimal("0"), free_usdt - self.safety_reserve_usdt)
        position_cap = available * self.max_position_pct / Decimal("100")
        budget = min(request.executable_notional_usdt, position_cap)
        if budget <= 0:
            raise ExecutionGateError("balance/reserve gate failed")
        self._event("EXECUTION_PREFLIGHT", "execution gates passed", symbol=request.symbol, budget=str(budget), age_ms=float(age))
        return budget

    def execute_binance_triangle(
        self,
        *,
        request: ExecutionRequest,
        client: Any,
        filters: Mapping[str, Any],
        free_usdt: Decimal,
        binance_connected: bool,
        binance_authenticated: bool,
        depth_ok: bool,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if request.path is None or len(request.path) != 3:
            raise ExecutionGateError("Binance execution requires a three-symbol path")

        budget = self.preflight(
            request,
            free_usdt=free_usdt,
            binance_connected=binance_connected,
            binance_authenticated=binance_authenticated,
            depth_ok=depth_ok,
        )

        from .executor import execute_triangle

        self._event("EXECUTE", "Binance triangle execution started", path=list(request.path), budget=str(budget))
        result = execute_triangle(
            client,
            list(request.path),
            request.start_asset,
            request.first_asset,
            budget,
            filters,
            dry_run,
        )
        if not result.get("finished") and not dry_run:
            self.halt("execution returned without completed cycle")
            raise ExecutionGateError("execution did not complete")
        self._event("EXECUTE", "Binance triangle execution completed", path=list(request.path), result=result)
        return result

    def explain_cross_exchange_limit(self, request: ExecutionRequest) -> str | None:
        if request.buy_venue == "binance" and request.sell_venue == "binance":
            return None
        return (
            "Cross-exchange execution is not enabled for observation venues: "
            f"{request.buy_venue}->{request.sell_venue}. Both legs require an authenticated "
            "execution adapter and funded inventory. The observation network remains signal-only."
        )
