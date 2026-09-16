import os
from dataclasses import dataclass
from itertools import combinations

from .calculator import calculate


@dataclass(frozen=True)
class Triangle:
    symbols: tuple[str, str, str]
    assets: tuple[str, str, str]


def _excluded_assets() -> set[str]:
    raw = os.getenv("EXCLUDED_BASE_ASSETS", "")
    return {x.strip().upper() for x in raw.split(",") if x.strip()}


def build_triangles(exchange_info: dict, max_triangles: int = 5000):
    markets = {}
    for s in exchange_info.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        markets[(s["baseAsset"], s["quoteAsset"])] = s["symbol"]
    excluded = _excluded_assets()
    assets = sorted(base for base, quote in markets if quote == "USDT" and base not in excluded)
    out, seen = [], set()
    unlimited = max_triangles <= 0
    for a, b in combinations(assets, 2):
        for first, second in ((a, b), (b, a)):
            symbols = (markets.get((first, "USDT")), markets.get((first, second)), markets.get((second, "USDT")))
            if not all(symbols):
                continue
            tri = Triangle(symbols, ("USDT", first, second))
            if tri.symbols in seen:
                continue
            seen.add(tri.symbols)
            out.append(tri)
            if not unlimited and len(out) >= max_triangles:
                return out
    return out


def evaluate_triangle_outcome(t, books, fee_bps, slippage_bps, symbol_meta=None, notional_usdt=1.0):
    result = calculate(t.symbols, t.assets, books, symbol_meta or {}, notional_usdt, fee_bps, slippage_bps)
    if result is None:
        return None
    return {
        "start_usdt": result.start_usdt,
        "final_usdt": result.final_usdt,
        "gross_pnl_usdt": result.gross_pnl_usdt,
        "gross_bps": result.gross_bps,
        "net_pnl_usdt": result.net_pnl_usdt,
        "net_bps": result.net_bps,
        "fee_drag_bps": result.fee_drag_bps,
        "depth_drag_bps": result.depth_drag_bps,
        "safety_bps": result.safety_bps,
        "break_even_gross_bps": result.break_even_gross_bps,
        "path": result.path,
        "first_asset": result.assets[1],
        "second_asset": result.assets[2],
        "legs": [vars(leg) for leg in result.legs],
    }


def evaluate_triangle(t, books, fee_bps, slippage_bps, symbol_meta=None, notional_usdt=1.0):
    outcome = evaluate_triangle_outcome(t, books, fee_bps, slippage_bps, symbol_meta, notional_usdt)
    if not outcome:
        return None
    return outcome["net_bps"], outcome["gross_bps"], outcome["path"], outcome["first_asset"], outcome["second_asset"]
