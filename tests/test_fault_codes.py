"""FAIRINO main/sub fault-code text."""
from fault_codes import describe


def test_no_fault_has_no_text():
    assert describe(None, None) is None
    assert describe(0, 0) is None


def test_documented_pair():
    text = describe(1, 122)
    assert text.description == "Linear insertion motion failed"
    assert text.category == "Command point error"
    assert text.resettable is True
    assert text.documented


def test_row_level_resettability_overrides_category():
    assert describe(1, 20).resettable is False
    assert describe(1, 25).description == "Axis 3 speed limit exceeded"


def test_unknown_main_code_still_prints_numbers():
    text = describe(117, 4)
    assert text.category is None
    assert "117/4" in text.description
    assert text.resettable is None
