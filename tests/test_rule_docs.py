from src.rule_docs import load_rule_doc


def test_load_rule_doc_returns_fields_for_known_rule_id():
    doc = load_rule_doc("R-TREND-14")
    assert doc is not None
    assert "解讀" in doc
    assert doc["信心"].startswith("92")


def test_load_rule_doc_returns_none_for_unknown_rule_id():
    assert load_rule_doc("R-DOES-NOT-EXIST-99") is None
