import random

from src.dragon.calculator import calculate
from src.dragon import fastcalc

PATH = ("AUSDT", "AB", "BUSDT")
ASSETS = ("USDT", "A", "B")
META = {"AUSDT": ("A", "USDT"), "AB": ("A", "B"), "BUSDT": ("B", "USDT")}


def _mkbook(mid, rng):
    bids, asks = [], []
    p = mid
    for _ in range(5):
        p *= 0.9997
        bids.append((f"{p:.8f}", f"{rng.uniform(20, 800):.4f}"))
    p = mid
    for _ in range(5):
        p *= 1.0003
        asks.append((f"{p:.8f}", f"{rng.uniform(20, 800):.4f}"))
    return {"bids": bids, "asks": asks}


def test_net_bps_parity():
    rng = random.Random(1234)
    fees = (10.0, 7.5, 10.0)
    ff = fastcalc.fee_factors(fees)
    checked = 0
    for _ in range(2000):
        books = {k: _mkbook(rng.uniform(0.5, 50), rng) for k in PATH}
        fl_books = {k: fastcalc.parse_book(v["bids"], v["asks"]) for k, v in books.items()}
        start = rng.uniform(10, 500)
        d = calculate(PATH, ASSETS, books, META, start, fees, 3.0)
        f = fastcalc.net_bps(PATH, ASSETS, fl_books, META, start, ff, 3.0)
        assert (d is None) == (f is None)
        if d is None:
            continue
        checked += 1
        tol = max(0.05, abs(float(d.net_bps)) * 1e-5)
        assert abs(float(d.net_bps) - f[0]) < tol
        assert abs(float(d.net_pnl_usdt) - f[2]) < max(start, abs(f[2])) * 1e-4
    assert checked > 100
