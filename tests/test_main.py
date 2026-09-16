from decimal import Decimal

from src.dragon.main import _score, _tier


def test_score_respects_40_point_entry_gate():
    assert _score(3) == 40
    assert _score(13) == 50
    assert _score(63) == 100


def test_tiers_match_workbook_ranges():
    assert _tier(95) == "S"
    assert _tier(87) == "A+"
    assert _tier(82) == "A"
    assert _tier(74) == "B+"
    assert _tier(65) == "B"
    assert _tier(45) == "C"
    assert _tier(39) == "D"
