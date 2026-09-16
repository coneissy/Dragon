from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")
BPS = Decimal("10000")

@dataclass(frozen=True)
class LegResult:
    symbol: str
    side: str
    input_asset: str
    output_asset: str
    input_amount: Decimal
    output_before_fee: Decimal
    output_after_fee: Decimal
    fee: Decimal
    top_output: Decimal
    depth_drag_bps: Decimal

@dataclass(frozen=True)
class Calculation:
    path: tuple[str, str, str]
    assets: tuple[str, str, str]
    start_usdt: Decimal
    final_usdt: Decimal
    gross_pnl_usdt: Decimal
    net_pnl_usdt: Decimal
    gross_bps: Decimal
    net_bps: Decimal
    fee_drag_bps: Decimal
    depth_drag_bps: Decimal
    safety_bps: Decimal
    break_even_gross_bps: Decimal
    legs: tuple[LegResult, ...]

    @property
    def profitable(self) -> bool:
        return self.net_pnl_usdt > ZERO

def _fee_factor(fee_bps: Decimal) -> Decimal:
    return Decimal("1") - fee_bps / BPS

def _levels(book: dict, side: str):
    return book.get("asks" if side == "BUY" else "bids") or []

def _walk(book: dict, side: str, amount: Decimal):
    if amount <= ZERO:
        return None
    remaining, output = amount, ZERO
    for raw_price, raw_qty in _levels(book, side):
        price, qty = Decimal(str(raw_price)), Decimal(str(raw_qty))
        if price <= ZERO or qty <= ZERO:
            continue
        if side == "SELL":
            take = min(remaining, qty)
            output += take * price
            remaining -= take
        else:
            spend = min(remaining, qty * price)
            output += spend / price
            remaining -= spend
        if remaining <= ZERO:
            return output
    return None

def _top_output(book: dict, side: str, amount: Decimal):
    levels = _levels(book, side)
    if not levels or amount <= ZERO:
        return None
    price = Decimal(str(levels[0][0]))
    if price <= ZERO:
        return None
    return amount * price if side == "SELL" else amount / price

def _side(meta, src, dst):
    base, quote = meta
    if src == quote and dst == base:
        return "BUY"
    if src == base and dst == quote:
        return "SELL"
    return None

def _safety_bps(path, assets, books, symbol_meta, start, cap_bps):
    cap = max(ZERO, Decimal(str(cap_bps)))
    if cap == ZERO:
        return ZERO
    amount, worst = start, ZERO
    for i, symbol in enumerate(path):
        meta = symbol_meta.get(symbol)
        if not meta:
            return cap
        side = _side(meta, assets[i], assets[(i + 1) % 3])
        levels = _levels(books.get(symbol, {}), side or "")
        if side is None or not levels:
            return cap
        price, qty = Decimal(str(levels[0][0])), Decimal(str(levels[0][1]))
        if price <= ZERO or qty <= ZERO:
            return cap
        liquidity = qty if side == "SELL" else qty * price
        worst = max(worst, min(Decimal("1"), amount / liquidity))
        amount = _walk(books[symbol], side, amount)
        if amount is None:
            return cap
    return cap * worst

def calculate(path, assets, books, symbol_meta, start_usdt, fee_bps, safety_cap_bps):
    """Pure executable triangular-arbitrage calculation using actual depth."""
    start = Decimal(str(start_usdt))
    fee = Decimal(str(fee_bps))
    if len(path) != 3 or len(assets) != 3 or start <= ZERO or fee < ZERO:
        return None
    factor = _fee_factor(fee)
    if factor <= ZERO:
        return None

    net_amount = start
    gross_amount = start
    fee_total = ZERO
    depth_total = ZERO
    legs = []

    for i, symbol in enumerate(path):
        meta = symbol_meta.get(symbol)
        if not meta:
            return None
        side = _side(meta, assets[i], assets[(i + 1) % 3])
        if side is None:
            return None
        book = books.get(symbol, {})
        if not _levels(book, side):
            return None

        net_before = _walk(book, side, net_amount)
        gross_before = _walk(book, side, gross_amount)
        top = _top_output(book, side, net_amount)
        if net_before is None or gross_before is None or top is None:
            return None
        if net_before <= ZERO or gross_before <= ZERO or top <= ZERO:
            return None

        leg_fee = net_before * fee / BPS
        net_after = net_before * factor
        drag = max(ZERO, (Decimal("1") - net_before / top) * BPS)
        fee_total += leg_fee
        depth_total += drag
        legs.append(LegResult(symbol, side, assets[i], assets[(i + 1) % 3], net_amount, net_before, net_after, leg_fee, top, drag))
        net_amount = net_after
        gross_amount = gross_before

    safety = _safety_bps(path, assets, books, symbol_meta, start, safety_cap_bps)
    final = net_amount * (Decimal("1") - safety / BPS)
    gross_pnl = gross_amount - start
    net_pnl = final - start
    gross_bps = gross_pnl / start * BPS
    net_bps = net_pnl / start * BPS
    multiplier = final / start
    break_even = (Decimal("1") / multiplier - Decimal("1")) * BPS if multiplier > ZERO else ZERO
    return Calculation(tuple(path), tuple(assets), start, final, gross_pnl, net_pnl, gross_bps, net_bps, fee_total / start * BPS, depth_total, safety, break_even, tuple(legs))
