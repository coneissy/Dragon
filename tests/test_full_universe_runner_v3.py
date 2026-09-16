import json
from types import SimpleNamespace

from full_universe_runner_v3 import _parse_depth, _record_book


def test_parse_depth_accepts_combined_binance_payload():
    payload = json.dumps({
        "stream": "btcusdt@depth20@100ms",
        "data": {"s": "BTCUSDT", "b": [["100", "2"]], "a": [["101", "3"]]},
    })
    result = _parse_depth(payload, 20)
    assert result["s"] == "BTCUSDT"
    assert result["b"] == [("100", "2")]
    assert result["a"] == [("101", "3")]


def test_parse_depth_rejects_subscription_ack():
    assert _parse_depth(json.dumps({"result": None, "id": 1}), 20) is None


def test_record_book_marks_triangles_dirty():
    books = {}
    dirty = set()
    by_symbol = {"BTCUSDT": [0, 1]}
    cfg = SimpleNamespace(depth_levels=20)
    _record_book({"s": "BTCUSDT", "b": [["100", "2"]], "a": [["101", "3"]]}, books, dirty, by_symbol, cfg)
    assert "BTCUSDT" in books
    assert dirty == {0, 1}
    assert books["BTCUSDT"]["source"] == "binance_ws_depth"
