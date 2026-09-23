from dragon.dex_market import DexEvent, DexMarketGraph, DexPool, normalize_event


def test_graph_keeps_same_pair_across_venues():
    graph = DexMarketGraph()
    graph.upsert_pool(DexPool("ethereum", "dex-a", "0x1", "USDC", "WETH"))
    graph.upsert_pool(DexPool("ethereum", "dex-b", "0x2", "WETH", "USDC"))

    pools = graph.pools_for_pair("ethereum", "usdc", "weth")

    assert len(pools) == 2
    assert graph.candidate_pairs("ethereum") == (("usdc", "weth"),)


def test_event_processing_is_idempotent():
    graph = DexMarketGraph()
    event = DexEvent("evt-1", "ethereum", "dex-a", "0x1", 1, "0xblock", "Swap", 1000, {})

    assert graph.apply_event(event) is True
    assert graph.apply_event(event) is False


def test_normalize_event_requires_identity():
    event = normalize_event({
        "eventId": "evt-1",
        "chain": "ethereum",
        "venue": "dex-a",
        "pool": "0x1",
        "blockNumber": 10,
        "blockHash": "0xblock",
        "eventType": "Swap",
        "timestampMs": 1000,
        "payload": {"amount0": "1"},
    })

    assert event.event_id == "evt-1"
    assert event.block_number == 10
