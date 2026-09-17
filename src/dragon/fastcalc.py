"""Float hot-path triangle math for high-throughput scanning."""
BPS = 10000.0


def parse_book(bids, asks):
    return {"fbids": [(float(p), float(q)) for p, q in bids], "fasks": [(float(p), float(q)) for p, q in asks]}


def _levels(book, side):
    return book.get("fasks" if side == "BUY" else "fbids") or ()


def _walk(levels, side, amount):
    if amount <= 0.0 or not levels:
        return None
    remaining, output = amount, 0.0
    if side == "SELL":
        for price, qty in levels:
            if price <= 0.0 or qty <= 0.0:
                continue
            take = min(remaining, qty)
            output += take * price
            remaining -= take
            if remaining <= 0.0:
                return output
    else:
        for price, qty in levels:
            if price <= 0.0 or qty <= 0.0:
                continue
            spend = min(remaining, qty * price)
            output += spend / price
            remaining -= spend
            if remaining <= 0.0:
                return output
    return None


def _side(meta, src, dst):
    base, quote = meta
    if src == quote and dst == base:
        return "BUY"
    if src == base and dst == quote:
        return "SELL"
    return None


def top_of_book_bps(path, assets, books, symbol_meta, fee_factor):
    amount = 1.0
    for i, symbol in enumerate(path):
        meta = symbol_meta.get(symbol)
        if not meta:
            return None
        side = _side(meta, assets[i], assets[(i + 1) % 3])
        levels = _levels(books.get(symbol, {}), side or "")
        if side is None or not levels or levels[0][0] <= 0.0:
            return None
        price = levels[0][0]
        amount = amount * price if side == "SELL" else amount / price
    return (amount * fee_factor - 1.0) * BPS


def net_bps(path, assets, books, symbol_meta, start_usdt, fee_factors, safety_cap_bps):
    start = float(start_usdt)
    if start <= 0.0:
        return None
    net_amount, worst_util = start, 0.0
    cap = max(0.0, float(safety_cap_bps))
    for i, symbol in enumerate(path):
        meta = symbol_meta.get(symbol)
        if not meta:
            return None
        side = _side(meta, assets[i], assets[(i + 1) % 3])
        levels = _levels(books.get(symbol, {}), side or "")
        if side is None or not levels:
            return None
        if cap > 0.0:
            price0, qty0 = levels[0]
            liq = qty0 if side == "SELL" else qty0 * price0
            if price0 <= 0.0 or qty0 <= 0.0:
                return None
            worst_util = max(worst_util, min(1.0, net_amount / liq))
        out = _walk(levels, side, net_amount)
        if out is None or out <= 0.0:
            return None
        net_amount = out * fee_factors[i]
    safety = cap * worst_util
    final = net_amount * (1.0 - safety / BPS)
    pnl = final - start
    return pnl / start * BPS, (net_amount - start) / start * BPS, pnl


def fee_factors(fee_bps):
    if isinstance(fee_bps, (tuple, list)):
        values = tuple(float(x) for x in fee_bps)
    else:
        values = (float(fee_bps),) * 3
    return tuple(1.0 - x / BPS for x in values)
