from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class SafetyStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class DexSafetyPolicy:
    max_quote_age_ms: int = 1_500
    max_slippage_bps: Decimal = Decimal("50")
    max_gas_quote: Decimal = Decimal("0")
    min_liquidity_quote: Decimal = Decimal("0")
    min_net_edge_bps: Decimal = Decimal("3")
    safety_buffer_bps: Decimal = Decimal("2")
    allowed_chains: frozenset[str] = frozenset()
    allowed_venues: frozenset[str] = frozenset()
    allowed_tokens: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DexSafetyRequest:
    chain: str
    venue: str
    sell_token: str
    buy_token: str
    pool: str
    router: str
    recipient: str
    quote_age_ms: int
    liquidity_quote: Decimal
    slippage_bps: Decimal
    gas_quote: Decimal
    net_edge_bps: Decimal
    simulation_ok: bool
    contracts_verified: bool
    calldata_valid: bool


@dataclass(frozen=True)
class SafetyDecision:
    status: SafetyStatus
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def approved(self) -> bool:
        return self.status is SafetyStatus.APPROVED


class DexSafetyGate:
    """Fail-closed pre-execution safety gate for DEX transactions.

    This gate does not sign or broadcast transactions. It verifies that an
    already-built route is still within Dragon's explicit safety policy.
    """

    def __init__(self, policy: DexSafetyPolicy):
        self.policy = policy

    @staticmethod
    def _normalized(value: str) -> str:
        return value.strip().lower()

    def evaluate(self, request: DexSafetyRequest) -> SafetyDecision:
        p = self.policy
        reasons: list[str] = []

        chain = self._normalized(request.chain)
        venue = self._normalized(request.venue)
        sell_token = self._normalized(request.sell_token)
        buy_token = self._normalized(request.buy_token)
        pool = self._normalized(request.pool)
        router = self._normalized(request.router)
        recipient = self._normalized(request.recipient)

        if not chain or not venue or not sell_token or not buy_token:
            reasons.append("missing route identity")
        if not pool or not router or not recipient:
            reasons.append("missing transaction contract identity")

        if p.allowed_chains and chain not in {self._normalized(x) for x in p.allowed_chains}:
            reasons.append("chain not allowlisted")
        if p.allowed_venues and venue not in {self._normalized(x) for x in p.allowed_venues}:
            reasons.append("DEX venue not allowlisted")
        if p.allowed_tokens and (
            sell_token not in {self._normalized(x) for x in p.allowed_tokens}
            or buy_token not in {self._normalized(x) for x in p.allowed_tokens}
        ):
            reasons.append("token not allowlisted")

        if request.quote_age_ms < 0 or request.quote_age_ms > p.max_quote_age_ms:
            reasons.append("quote stale")
        if request.liquidity_quote < p.min_liquidity_quote:
            reasons.append("insufficient liquidity")
        if request.slippage_bps < 0 or request.slippage_bps > p.max_slippage_bps:
            reasons.append("slippage limit exceeded")
        if request.gas_quote < 0:
            reasons.append("invalid gas quote")
        elif p.max_gas_quote > 0 and request.gas_quote > p.max_gas_quote:
            reasons.append("gas limit exceeded")

        required_edge = p.min_net_edge_bps + p.safety_buffer_bps
        if request.net_edge_bps < required_edge:
            reasons.append("net edge below safety threshold")

        if not request.contracts_verified:
            reasons.append("contracts not verified")
        if not request.calldata_valid:
            reasons.append("calldata validation failed")
        if not request.simulation_ok:
            reasons.append("transaction simulation failed")

        if reasons:
            return SafetyDecision(SafetyStatus.REJECTED, tuple(reasons))
        return SafetyDecision(SafetyStatus.APPROVED)


def approve_or_raise(gate: DexSafetyGate, request: DexSafetyRequest) -> SafetyDecision:
    decision = gate.evaluate(request)
    if not decision.approved:
        raise RuntimeError("DEX safety gate rejected: " + ", ".join(decision.reasons))
    return decision
