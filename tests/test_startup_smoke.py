import importlib
from pathlib import Path


def test_production_entrypoint_imports():
    module = importlib.import_module("src.dragon.engine")
    assert callable(module.main)


def test_max_universe_profile_loads():
    from src.dragon.max_universe import load_profile

    profile = load_profile(Path("config/max_universe_v5.yaml"))
    assert profile["dragon"]["starting_balance"] == 9.0
    assert profile["dragon"]["reserve_balance"] == 1.0
    assert profile["exchanges"]["binance"]["role"] == "EXECUTION"
    assert sum(v.get("role") == "OBSERVATION" for v in profile["exchanges"].values()) == 12
