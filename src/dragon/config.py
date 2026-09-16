from dataclasses import dataclass
import os

FIXED_STARTING_CAPITAL_USDT = 9.0
FIXED_SAFETY_RESERVE_USDT = 1.0
FIXED_DEPLOYABLE_USDT = 8.0
FIXED_MIN_TRADE_NOTIONAL_USDT = 5.0
FIXED_MAX_NOTIONAL_USDT = 0.0
FIXED_DECISION_CYCLE_SECONDS = 30
FIXED_UNIVERSE_SYMBOL_CAP = 1000
FIXED_PROBABILITY_SAMPLE_SIZE = 1000
FIXED_ROLLING_WINDOWS = (100, 500, 1000)
FIXED_NET_EDGE_FLOOR_BPS = 3.0
FIXED_NET_EDGE_REFERENCE_BPS = 20.0
FIXED_SPOT_ONLY = False
FIXED_FUTURES_ENABLED = True
FIXED_LEVERAGE_ENABLED = False
FIXED_MARTINGALE_ENABLED = False
FIXED_POSITION_ALLOCATION_PCT = 0.95
FIXED_POSITION_TIMEOUT_SECONDS = 15
FIXED_MAX_CONSECUTIVE_LOSSES = 5
FIXED_MAX_DRAWDOWN_PCT = 15.0
FIXED_REENTRY_DELAY_SECONDS = 0.0
FIXED_STALE_MS = 500

@dataclass(frozen=True)
class Config:
    api_base: str = "https://api.binance.com"
    ws_base: str = "wss://stream.binance.com:9443/ws"
    dry_run: bool = True
    live_trading: bool = False
    min_net_edge_bps: float = FIXED_NET_EDGE_FLOOR_BPS
    min_expected_profit_usdt: float = 0.0
    min_trade_notional_usdt: float = FIXED_MIN_TRADE_NOTIONAL_USDT
    max_notional_usdt: float = FIXED_MAX_NOTIONAL_USDT
    max_slippage_bps: float = 3.0
    fee_bps: float = 10.0
    risk_pct: float = 0.0015
    capital_allocation_pct: float = FIXED_POSITION_ALLOCATION_PCT
    safety_reserve_usdt: float = FIXED_SAFETY_RESERVE_USDT
    cooldown_ms: int = 0
    max_triangles: int = 0
    stale_ms: int = FIXED_STALE_MS
    poll_interval_seconds: float = 0.02
    order_timeout_ms: int = 5000
    position_timeout_seconds: int = FIXED_POSITION_TIMEOUT_SECONDS
    max_consecutive_losses: int = FIXED_MAX_CONSECUTIVE_LOSSES
    max_drawdown_pct: float = FIXED_MAX_DRAWDOWN_PCT
    depth_levels: int = 20
    decision_cycle_seconds: int = FIXED_DECISION_CYCLE_SECONDS
    universe_mode: str = "MAX_UNIVERSE"
    health_fail_open: bool = False
    dex_enabled: bool = False
    dex_quote_url: str = ""
    dex_max_gas_quote: float = 0.0
    dex_max_latency_ms: int = 2000

    @staticmethod
    def _bool(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def from_env(cls) -> "Config":
        requested_min_edge = float(os.getenv("MIN_NET_EDGE_BPS", str(cls.min_net_edge_bps)))
        requested_max_notional = float(os.getenv("MAX_NOTIONAL_USDT", str(cls.max_notional_usdt)))
        return cls(
            api_base=os.getenv("BINANCE_API_BASE", cls.api_base).strip().rstrip("/"),
            ws_base=os.getenv("BINANCE_WS_BASE", cls.ws_base).strip().rstrip("/"),
            dry_run=cls._bool("DRY_RUN", cls.dry_run),
            live_trading=cls._bool("LIVE_TRADING", cls.live_trading),
            min_net_edge_bps=max(FIXED_NET_EDGE_FLOOR_BPS, requested_min_edge),
            min_expected_profit_usdt=float(os.getenv("MIN_EXPECTED_PROFIT_USDT", str(cls.min_expected_profit_usdt))),
            min_trade_notional_usdt=FIXED_MIN_TRADE_NOTIONAL_USDT,
            max_notional_usdt=max(0.0, requested_max_notional),
            max_slippage_bps=float(os.getenv("MAX_SLIPPAGE_BPS", str(cls.max_slippage_bps))),
            fee_bps=float(os.getenv("FEE_BPS", str(cls.fee_bps))),
            risk_pct=float(os.getenv("ARB_RISK_PCT", str(cls.risk_pct))),
            capital_allocation_pct=FIXED_POSITION_ALLOCATION_PCT,
            safety_reserve_usdt=FIXED_SAFETY_RESERVE_USDT,
            cooldown_ms=0,
            max_triangles=int(os.getenv("MAX_TRIANGLES", str(cls.max_triangles))),
            stale_ms=FIXED_STALE_MS,
            poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", str(cls.poll_interval_seconds))),
            order_timeout_ms=int(os.getenv("ORDER_TIMEOUT_MS", str(cls.order_timeout_ms))),
            position_timeout_seconds=FIXED_POSITION_TIMEOUT_SECONDS,
            max_consecutive_losses=FIXED_MAX_CONSECUTIVE_LOSSES,
            max_drawdown_pct=FIXED_MAX_DRAWDOWN_PCT,
            depth_levels=int(os.getenv("DEPTH_LEVELS", str(cls.depth_levels))),
            decision_cycle_seconds=FIXED_DECISION_CYCLE_SECONDS,
            universe_mode="MAX_UNIVERSE",
            health_fail_open=cls._bool("HEALTH_FAIL_OPEN", cls.health_fail_open),
            dex_enabled=cls._bool("DEX_ENABLED", cls.dex_enabled),
            dex_quote_url=os.getenv("DEX_QUOTE_URL", cls.dex_quote_url).strip(),
            dex_max_gas_quote=float(os.getenv("DEX_MAX_GAS_QUOTE", str(cls.dex_max_gas_quote))),
            dex_max_latency_ms=int(os.getenv("DEX_MAX_LATENCY_MS", str(cls.dex_max_latency_ms))),
        )

    @property
    def slippage_bps(self) -> float:
        return self.max_slippage_bps

    def validate(self) -> None:
        if not self.api_base.startswith(("https://", "http://")):
            raise ValueError("BINANCE_API_BASE must be an HTTP(S) URL")
        if not self.ws_base.startswith(("wss://", "ws://")):
            raise ValueError("BINANCE_WS_BASE must be a WebSocket URL")
        if self.min_trade_notional_usdt != FIXED_MIN_TRADE_NOTIONAL_USDT:
            raise ValueError("MIN_TRADE_NOTIONAL_USDT is fixed at 5 USDT for the MAX UNIVERSE profile")
        if self.safety_reserve_usdt != FIXED_SAFETY_RESERVE_USDT or self.decision_cycle_seconds != FIXED_DECISION_CYCLE_SECONDS:
            raise ValueError("MAX UNIVERSE core quota was modified")
        if self.stale_ms != FIXED_STALE_MS or self.cooldown_ms != 0:
            raise ValueError("MAX UNIVERSE freshness/re-entry controls are fixed")
        if self.max_notional_usdt < 0:
            raise ValueError("MAX_NOTIONAL_USDT cannot be negative; 0 means dynamic/no fixed cap")
        if self.min_net_edge_bps < FIXED_NET_EDGE_FLOOR_BPS:
            raise ValueError("MIN_NET_EDGE_BPS cannot be tuned below the fixed 3 bps floor")
        if self.max_notional_usdt != 0 and self.max_notional_usdt < self.min_trade_notional_usdt:
            raise ValueError("MAX_NOTIONAL_USDT cannot be below MIN_TRADE_NOTIONAL_USDT unless it is 0")
        if self.risk_pct <= 0 or self.risk_pct > 0.01:
            raise ValueError("ARB_RISK_PCT must be >0 and <=0.01")
        if self.capital_allocation_pct != FIXED_POSITION_ALLOCATION_PCT:
            raise ValueError("MAX UNIVERSE position allocation is fixed at 95%")
        if not 0 < self.capital_allocation_pct <= 1:
            raise ValueError("capital allocation must be between 0 and 1")
        if self.min_expected_profit_usdt < 0:
            raise ValueError("MIN_EXPECTED_PROFIT_USDT cannot be negative")
        if self.min_net_edge_bps < 0 or self.fee_bps < 0 or self.max_slippage_bps < 0:
            raise ValueError("edge, fee, and slippage limits cannot be negative")
        if self.order_timeout_ms <= 0 or self.position_timeout_seconds <= 0:
            raise ValueError("execution timing values are invalid")
        if self.max_consecutive_losses <= 0 or self.max_drawdown_pct <= 0:
            raise ValueError("MAX UNIVERSE risk limits must be positive")
        if not 1 <= self.depth_levels <= 100:
            raise ValueError("DEPTH_LEVELS must be between 1 and 100")
        if self.decision_cycle_seconds <= 0:
            raise ValueError("DECISION_CYCLE_SECONDS must be positive")
        if self.universe_mode != "MAX_UNIVERSE":
            raise ValueError("UNIVERSE_MODE must be MAX_UNIVERSE")
        if self.dex_max_gas_quote < 0 or self.dex_max_latency_ms <= 0:
            raise ValueError("DEX cost/latency limits are invalid")
        if self.dex_enabled and not self.dex_quote_url:
            raise ValueError("DEX_ENABLED=true requires DEX_QUOTE_URL")
        if self.live_trading and self.dry_run:
            raise ValueError("LIVE_TRADING=true cannot be combined with DRY_RUN=true")
