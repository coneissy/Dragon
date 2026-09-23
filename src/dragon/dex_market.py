from __future__ import annotations

"""Provider-neutral DEX market graph and event intelligence primitives.

This layer is deliberately read-only: it normalizes pool updates and builds
a graph that can be consumed by quote/opportunity engines. It never signs or
broadcasts transactions.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class DexPool:
    chain: str
    venue: str
    pool: str
    token0: str
    token1: str
    liquidity_quote: Decimal = Decimal("0")
    updated_ms: int = 0
    verified: bool = False

    def tokens(self) -> tuple[str, str]:
        return self.token0.lower(), self.token1.lower()


@dataclass(frozen=True)
class DexEvent:
    event_id: str
    chain: str
    venue: str
    pool: str
    block_number: int
    block_hash: str
    event_type: str
    timestamp_ms: int
    payload: dict


class DexMarketGraph:
    """In-memory canonical pool graph with idempotent event application."""

    def __init__(self) -> None:
        self.pools: dict[tuple[str, str], DexPool] = {}
        self.events_seen: set[str] = set()

    @staticmethod
    def _key(chain: str, pool: str) -> tuple[str, str]:
        return chain.strip().lower(), pool.strip().lower()

    def upsert_pool(self, pool: DexPool) -> None:
        if not pool.chain.strip() or not pool.venue.strip() or not pool.pool.strip():
            raise ValueError("pool identity is required")
        if not pool.token0.strip() or not pool.token1.strip():
            raise ValueError("pool tokens are required")
        self.pools[self._key(pool.chain, pool.pool)] = pool

    def apply_event(self, event: DexEvent) -> bool:
        """Record an event once. Return False for duplicates."""
        event_id = event.event_id.strip()
        if not event_id:
            raise ValueError("event_id is required")
        if event_id in self.events_seen:
            return False
        self.events_seen.add(event_id)
        return True

    def pools_for_pair(self, chain: str, token_a: str, token_b: str) -> tuple[DexPool, ...]:
        wanted = {token_a.strip().lower(), token_b.strip().lower()}
        if len(wanted) != 2:
            return ()
        return tuple(
            pool for pool in self.pools.values()
            if pool.chain.strip().lower() == chain.strip().lower()
            and set(pool.tokens()) == wanted
        )

    def candidate_pairs(self, chain: str) -> tuple[tuple[str, str], ...]:
        pairs = {
            tuple(sorted(pool.tokens()))
            for pool in self.pools.values()
            if pool.chain.strip().lower() == chain.strip().lower()
        }
        return tuple(sorted(pairs))


def normalize_event(raw: dict) -> DexEvent:
    """Normalize an already-validated webhook/RPC event payload.

    Signature/authentication belongs at the transport boundary.
    """
    required = ("eventId", "chain", "venue", "pool", "blockNumber", "blockHash", "eventType", "timestampMs")
    missing = [name for name in required if raw.get(name) in (None, "")]
    if missing:
        raise ValueError("missing event fields: " + ", ".join(missing))
    return DexEvent(
        event_id=str(raw["eventId"]),
        chain=str(raw["chain"]),
        venue=str(raw["venue"]),
        pool=str(raw["pool"]),
        block_number=int(raw["blockNumber"]),
        block_hash=str(raw["blockHash"]),
        event_type=str(raw["eventType"]),
        timestamp_ms=int(raw["timestampMs"]),
        payload=dict(raw.get("payload") or {}),
    )
