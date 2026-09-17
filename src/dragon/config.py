from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Config:
    api_base: str = "https://api.binance.com"
    ws_base: str = "wss://stream.binance.com:9443/ws"
    dry_run: bool = True
    live_trading: bool = False
    # Scaling is centralized here so runtime modules do not read duplicate env values.
    max_platforms: int = 20
    max_symbols_per_platform: int = 10000
    max_ws_symbols: int = 300
    ws_shard_size: int = 40
    min_net_edge_bps: float = 3.0
    min_expected_profit_usdt: float = 0.0
    min_trade_notional_usdt: float = 5.0
    max_notional_usdt: float = 0.0
    fee_bps: float = 10.0
    max_slippage_bps: float = 3.0
    capital_allocation_pct: float = 0.95
    safety_reserve_usdt: float = 1.0
    cooldown_ms: int = 0
    max_triangles: int = 0
    stale_ms: int = 1500
    depth_levels: int = 5
    order_timeout_ms: int = 5000
    max_consecutive_losses: int = 5
    max_drawdown_pct: float = 15.0
    ledger_path: str = "/tmp/dragon_ledger.sqlite3"
    health_fail_open: bool = False

    @staticmethod
    def _bool(name: str, default: bool) -> bool:
        value = os.getenv(name)
        return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_base=os.getenv("BINANCE_API_BASE", cls.api_base).strip().rstrip("/"),
            ws_base=os.getenv("BINANCE_WS_BASE", cls.ws_base).strip().rstrip("/"),
            dry_run=cls._bool("DRY_RUN", cls.dry_run),
            live_trading=cls._bool("LIVE_TRADING", cls.live_trading),
            max_platforms=max(1, int(os.getenv("MAX_PLATFORMS", cls.max_platforms))),
            max_symbols_per_platform=max(1, min(10000, int(os.getenv("MAX_SYMBOLS_PER_PLATFORM", cls.max_symbols_per_platform))),
            max_ws_symbols=max(1, min(10000, int(os.getenv("MAX_WS_SYMBOLS", cls.max_ws_symbols))),
            ws_shard_size=max(1, min(100, int(os.getenv("WS_SHARD_SIZE", cls.ws_shard_size))),
            min_net_edge_bps=max(0.0, float(os.getenv("MIN_NET_EDGE_BPS", cls.min_net_edge_bps))),
            min_expected_profit_usdt=max(0.0, float(os.getenv("MIN_EXPECTED_PROFIT_USDT", cls.min_expected_profit_usdt))),
            min_trade_notional_usdt=max(0.0, float(os.getenv("MIN_TRADE_NOTIONAL_USDT", cls.min_trade_notional_usdt))),
            max_notional_usdt=max(0.0, float(os.getenv("MAX_NOTIONAL_USDT", cls.max_notional_usdt))),
            fee_bps=max(0.0, float(os.getenv("FEE_BPS", cls.fee_bps))),
            max_slippage_bps=max(0.0, float(os.getenv("MAX_SLIPPAGE_BPS", cls.max_slippage_bps))),
            capital_allocation_pct=float(os.getenv("ARB_CAPITAL_ALLOCATION_PCT", cls.capital_allocation_pct)),
            safety_reserve_usdt=max(0.0, float(os.getenv("ARB_SAFETY_RESERVE_USDT", cls.safety_reserve_usdt))),
            cooldown_ms=max(0, int(os.getenv("LIVE_ORDER_COOLDOWN_MS", cls.cooldown_ms))),
            max_triangles=max(0, int(os.getenv("MAX_TRIANGLES", cls.max_triangles))),
            stale_ms=max(250, int(os.getenv("STALE_MS", cls.stale_ms))),
            depth_levels=max(5, min(20, int(os.getenv("DEPTH_LEVELS", cls.depth_levels)))),
            order_timeout_ms=max(1000, int(os.getenv("ORDER_TIMEOUT_MS", cls.order_timeout_ms))),
            max_consecutive_losses=max(1, int(os.getenv("MAX_CONSECUTIVE_LOSSES", cls.max_consecutive_losses))),
            max_drawdown_pct=max(0.1, float(os.getenv("MAX_DRAWDOWN_PCT", cls.max_drawdown_pct))),
            ledger_path=os.getenv("LEDGER_PATH", cls.ledger_path),
            health_fail_open=cls._bool("HEALTH_FAIL_OPEN", cls.health_fail_open),
        )

    def validate(self) -> None:
        if not self.api_base.startswith(("http://", "https://")):
            raise ValueError("BINANCE_API_BASE must be HTTP(S)")
        if not self.ws_base.startswith(("ws://", "wss://")):
            raise ValueError("BINANCE_WS_BASE must be a WebSocket URL")
        if self.live_trading and self.dry_run:
            raise ValueError("LIVE_TRADING=true cannot be combined with DRY_RUN=true")
        if self.max_platforms > 20:
            raise ValueError("MAX_PLATFORMS cannot exceed 20")
        if self.max_symbols_per_platform > 10000:
            raise ValueError("MAX_SYMBOLS_PER_PLATFORM cannot exceed 10000")
        if self.max_ws_symbols > self.max_symbols_per_platform:
            raise ValueError("MAX_WS_SYMBOLS cannot exceed MAX_SYMBOLS_PER_PLATFORM")
        if self.min_trade_notional_usdt < 0 or self.max_notional_usdt < 0:
            raise ValueError("notional limits cannot be negative")
        if self.max_notional_usdt and self.max_notional_usdt < self.min_trade_notional_usdt:
            raise ValueError("MAX_NOTIONAL_USDT cannot be below MIN_TRADE_NOTIONAL_USDT")
        if not 0 < self.capital_allocation_pct <= 1:
            raise ValueError("ARB_CAPITAL_ALLOCATION_PCT must be in (0,1]")
        if self.max_consecutive_losses <= 0 or self.max_drawdown_pct <= 0:
            raise ValueError("risk limits must be positive")
