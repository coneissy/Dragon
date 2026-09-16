import json

from src.dragon.market_data import MarketData


def test_market_data_parses_combined_depth_snapshot():
    md = MarketData("wss://stream.binance.com:9443/ws", ["BTCUSDT"], 20, 500)
    payload = {"stream": "btcusdt@depth20@100ms", "data": {"s": "BTCUSDT", "b": [["100", "2"]], "a": [["101", "3"]]}}
    assert md.update_from_payload(payload) is True
    assert md.books["BTCUSDT"]["bids"] == [("100", "2")]
    assert md.books["BTCUSDT"]["asks"] == [("101", "3")]


def test_market_data_ignores_subscription_ack():
    md = MarketData("wss://stream.binance.com:9443/ws", ["BTCUSDT"], 20, 500)
    assert md.update_from_payload({"result": None, "id": 1}) is False


def test_market_data_requires_fresh_books():
    md = MarketData("wss://stream.binance.com:9443/ws", ["BTCUSDT"], 20, 500)
    md.update_from_payload({"data": {"s": "BTCUSDT", "b": [["100", "2"]], "a": [["101", "3"]]}})
    assert md.fresh("BTCUSDT") is True
