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
    remaining = amount
    output = ZERO
    for raw_price, raw_qty in _levels(book, side):
        price = Decimal(str(raw_price))
        qty = Decimal(str(raw_qty))
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


def _side(meta: tuple[str, str], src: str, dst: str):
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
    amount = start
    worst = ZERO
    for i, symbol in enumerate(path):
        meta = symbol_meta.get(symbol)
        if not meta:
            return cap
        side = _side(meta, assets[i], assets[(i + 1) % 3])
        if side is None:
            return cap
        levels = _levels(books.get(symbol, {}), side)
        if not levels:
            return cap
        price = Decimal(str(levels[0][0]))
        qty = Decimal(str(levels[0][1]))
        if price <= ZERO or qty <= ZERO:
            return cap
        liquidity = qty if side == "SELL" else qty * price
        worst = max(worst, min(Decimal("1"), amount / liquidity))
        out = _walk(books[symbol], side, amount)
        if out is None:
            return cap
        amount = out
    return cap * max(ZERO, min(Decimal("1"), worst))


def calculate(path, assets, books, symbol_meta, start_usdt, fee_bps, safety_cap_bps):
    """Calculate one triangular path using executable order-book depth.

    The calculation is deliberately pure: no network calls, balances, orders, or
    global state. Every leg consumes the previous leg's actual quantity, applies
    its fee once, and the final result is reconciled to USDT.
    """
    if len(path) != 3 or len(assets) != 3 or start_usdt <= ZERO:
        return None
    fee = Decimal(str(fee_bps))
    factor = _fee_factor(fee)
    if fee < ZERO or factor <= ZERO:
        return None

    amount = Decimal(str(start_usdt))
    gross_amount = amount
    legs = []
    fee_total = ZERO
    depth_total = ZERO

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

        before = _walk(book, side, amount)
        top = _top_output(book, side, amount)
        if before is None or top is None or before <= ZERO or top <= ZERO:
            return None
        leg_fee = before * fee / BPS
        after = before * factor
        drag = max(ZERO, (Decimal("1") - before / top) * BPS)
        fee_total += leg_fee
        depth_total += drag
        legs.append(LegResult(symbol, side, assets[i], assets[(i + 1) % 3], amount, before, after, leg_fee, top, drag))
        amount = after
        if amount <= ZERO:
            return None

    safety = _safety_bps(path, assets, books, symbol_meta, Decimal(str(start_usdt)), safety_cap_bps)
    safety_factor = Decimal("1") - safety / BPS
    final = amount * safety_factor
    gross_pnl = gross_amount
    gross_pnl = gross_amount
    gross_pnl = (legs[0].output_before_fee * Decimal("1")) if False else (amount / (factor ** 3) - Decimal(str(start_usdt)))
    gross_bps = gross_pnl / Decimal(str(start_usdt)) * BPS
    net_pnl = final - Decimal(str(start_usdt))
    net_bps = net_pnl / Decimal(str(start_usdt)) * BPS
    execution_multiplier = (amount / Decimal(str(start_usdt))) * safety_factor
    break_even = (Decimal("1") / execution_multiplier - Decimal("1")) * BPS if execution_multiplier > ZERO else ZERO
    return Calculation(
        tuple(path), tuple(assets), Decimal(str(start_usdt)), final,
        gross_pnl, net_pnl, gross_bps, net_bps,
        fee_total / Decimal(str(start_usdt)) * BPS,
        depth_total, safety, break_even, tuple(legs),
    )
