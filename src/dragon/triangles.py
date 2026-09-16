import os
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations


@dataclass(frozen=True)
class Triangle:
    symbols: tuple[str, str, str]
    assets: tuple[str, str, str]


def _excluded_assets() -> set[str]:
    raw = os.getenv("EXCLUDED_BASE_ASSETS", "")
    return {asset.strip().upper() for asset in raw.split(",") if asset.strip()}


def build_triangles(exchange_info: dict, max_triangles: int = 5000):
    markets = {}
    for s in exchange_info.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        markets[(s["baseAsset"], s["quoteAsset"])] = s["symbol"]

    excluded = _excluded_assets()
    usdt_assets = sorted(base for base, quote in markets if quote == "USDT" and base not in excluded)
    out = []
    seen = set()
    unlimited = max_triangles <= 0
    for a, b in combinations(usdt_assets, 2):
        a_usdt = markets.get((a, "USDT"))
        b_usdt = markets.get((b, "USDT"))
        if not a_usdt or not b_usdt:
            continue
        for first, second in ((a, b), (b, a)):
            cross = markets.get((first, second))
            if not cross:
                continue
            tri = Triangle((markets[(first, "USDT")], cross, markets[(second, "USDT")]), ("USDT", first, second))
            if tri.symbols in seen:
                continue
            seen.add(tri.symbols)
            out.append(tri)
            if not unlimited and len(out) >= max_triangles:
                return out
    return out


def _walk(symbol: str, side: str, qty: Decimal, books: dict):
    book = books.get(symbol, {})
    levels = book.get("bids" if side == "sell" else "asks") or []
    remaining = qty
    result = Decimal("0")
    for raw_price, raw_qty in levels:
        price = Decimal(str(raw_price))
        level_qty = Decimal(str(raw_qty))
        if price <= 0 or level_qty <= 0:
            continue
        if side == "sell":
            take = min(remaining, level_qty)
            result += take * price
            remaining -= take
        else:
            take_base = min(level_qty, remaining / price)
            result += take_base
            remaining -= take_base * price
        if remaining <= 0:
            break
    if remaining > 0 or result <= 0:
        return None
    return result


def _top_output(symbol: str, side: str, qty: Decimal, books: dict):
    book = books.get(symbol, {})
    levels = book.get("bids" if side == "sell" else "asks") or []
    if not levels:
        return None
    price = Decimal(str(levels[0][0]))
    if price <= 0 or qty <= 0:
        return None
    if side == "sell":
        return qty * price
    return qty / price


def _dynamic_safety_bps(t: Triangle, books: dict, symbol_meta: dict, start: Decimal, max_safety_bps: float) -> Decimal:
    cap = Decimal(str(max(0.0, max_safety_bps)))
    if cap <= 0:
        return Decimal("0")
    amount = start
    ratios = []
    for i, symbol in enumerate(t.symbols):
        meta = symbol_meta.get(symbol)
        if not meta:
            return cap
        src = t.assets[i]
        dst = t.assets[(i + 1) % 3]
        base, quote = meta
        if src == quote and dst == base:
            side = "buy"
        elif src == base and dst == quote:
            side = "sell"
        else:
            return cap
        levels = books.get(symbol, {}).get("bids" if side == "sell" else "asks") or []
        if not levels:
            return cap
        price = Decimal(str(levels[0][0]))
        qty = Decimal(str(levels[0][1]))
        if price <= 0 or qty <= 0:
            return cap
        # Compare input liquidity in the same unit as the input amount.
        # For BUY, the top-level quote value is qty * price. For SELL, the
        # available input is base qty directly. The previous implementation
        # compared SELL base quantity against quote notional, producing a
        # dimensionally invalid liquidity ratio and excessive safety drag.
        input_liquidity = qty * price if side == "buy" else qty
        ratios.append(amount / input_liquidity if input_liquidity > 0 else Decimal("999"))
        out = _walk(symbol, side, amount, books)
        if out is None:
            return cap
        amount = out
    worst = max(ratios, default=Decimal("999"))
    if worst <= Decimal("0.25"):
        selected = Decimal("3")
    elif worst <= Decimal("0.75"):
        selected = Decimal("5")
    elif worst <= Decimal("1.5"):
        selected = Decimal("10")
    else:
        selected = Decimal("20")
    return min(cap, selected)


def evaluate_triangle_outcome(t: Triangle, books, fee_bps, slippage_bps, symbol_meta=None, notional_usdt=1.0):
    symbol_meta = symbol_meta or {}
    start = Decimal(str(notional_usdt))
    if start <= 0 or len(t.symbols) != 3:
        return None

    gross_amount = start
    net_amount = start
    fee_factor = Decimal("1") - Decimal(str(fee_bps)) / Decimal("10000")
    if fee_factor <= 0:
        return None

    legs = []
    total_fee_equivalent = Decimal("0")
    total_depth_drag_bps = Decimal("0")
    top_amount = start

    for i, symbol in enumerate(t.symbols):
        meta = symbol_meta.get(symbol)
        if not meta:
            return None
        src = t.assets[i]
        dst = t.assets[(i + 1) % 3]
        base, quote = meta
        if src == quote and dst == base:
            side = "BUY"
        elif src == base and dst == quote:
            side = "SELL"
        else:
            return None

        side_lower = side.lower()
        levels = books.get(symbol, {}).get("asks" if side == "BUY" else "bids") or []
        if not levels:
            return None
        top_price = Decimal(str(levels[0][0]))

        gross_next = _walk(symbol, side_lower, gross_amount, books)
        net_before_fee = _walk(symbol, side_lower, net_amount, books)
        top_net_output = _top_output(symbol, side_lower, net_amount, books)
        if any(x is None or x <= 0 for x in (gross_next, net_before_fee, top_net_output)):
            return None

        fee = net_before_fee * (Decimal("1") - fee_factor)
        net_next = net_before_fee * fee_factor
        depth_drag_bps = max(Decimal("0"), (Decimal("1") - net_before_fee / top_net_output) * Decimal("10000"))
        total_depth_drag_bps += depth_drag_bps
        legs.append({
            "symbol": symbol,
            "side": side,
            "input_asset": src,
            "output_asset": dst,
            "input_amount": str(net_amount),
            "output_before_fee": str(net_before_fee),
            "output_after_fee": str(net_next),
            "fee": str(fee),
            "top_price": str(top_price),
            "top_output": str(top_net_output),
            "depth_drag_bps": str(depth_drag_bps),
        })
        total_fee_equivalent += fee
        gross_amount = gross_next
        net_amount = net_next
        top_amount = _top_output(symbol, side_lower, top_amount, books)
        if top_amount is None or top_amount <= 0:
            return None

    safety_bps = _dynamic_safety_bps(t, books, symbol_meta, start, slippage_bps)
    gross_pnl = gross_amount - start
    net_pnl_before_safety = net_amount - start
    safety_cost = start * safety_bps / Decimal("10000")
    projected_final = net_amount - safety_cost
    net_pnl = projected_final - start
    gross_bps = gross_pnl / start * Decimal("10000")
    net_bps = net_pnl / start * Decimal("10000")
    fee_drag_bps = (Decimal("1") - (fee_factor ** 3)) * Decimal("10000")
    top_of_book_gross_bps = (top_amount / start - Decimal("1")) * Decimal("10000")

    return {
        "start_usdt": start,
        "gross_final": gross_amount,
        "gross_pnl_usdt": gross_pnl,
        "gross_bps": gross_bps,
        "top_of_book_gross_bps": top_of_book_gross_bps,
        "post_fee_final": net_amount,
        "net_pnl_before_safety_usdt": net_pnl_before_safety,
        "fee_drag_bps": fee_drag_bps,
        "total_fee_equivalent": total_fee_equivalent,
        "depth_drag_bps": total_depth_drag_bps,
        "safety_bps": safety_bps,
        "safety_cost_usdt": safety_cost,
        "final_usdt": projected_final,
        "net_pnl_usdt": net_pnl,
        "net_bps": net_bps,
        "legs": legs,
        "path": t.symbols,
        "first_asset": t.assets[1],
        "second_asset": t.assets[2],
    }


def evaluate_triangle(t: Triangle, books, fee_bps, slippage_bps, symbol_meta=None, notional_usdt=1.0):
    outcome = evaluate_triangle_outcome(t, books, fee_bps, slippage_bps, symbol_meta, notional_usdt)
    if not outcome:
        return None
    return outcome["net_bps"], outcome["gross_bps"], outcome["path"], outcome["first_asset"], outcome["second_asset"]
