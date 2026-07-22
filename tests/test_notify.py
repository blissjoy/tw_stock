from src.notify.email_notify import format_candidates_email_body
from src.notify.line_notify import format_candidates_message


def _sample_candidates():
    return [
        {"stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 600.0, "stop_loss": 570.0, "note": "多頭架構＋攻擊量"},
        {"stock_id": "2454", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 1000.0, "stop_loss": 950.0, "note": "多頭架構＋攻擊量"},
    ]


def test_format_candidates_message_empty():
    text = format_candidates_message("2026-07-22", [])
    assert "2026-07-22" in text
    assert "沒有符合條件" in text


def test_format_candidates_message_lists_all_candidates():
    text = format_candidates_message("2026-07-22", _sample_candidates())
    assert "共2檔候選" in text
    assert "2330" in text and "600.00" in text and "570.00" in text
    assert "2454" in text and "1000.00" in text


def test_format_candidates_email_body_empty():
    body = format_candidates_email_body("2026-07-22", [])
    assert "沒有符合條件" in body


def test_format_candidates_email_body_lists_all_candidates():
    body = format_candidates_email_body("2026-07-22", _sample_candidates())
    assert "共2檔候選" in body
    assert "2330" in body and "進場價 600.00" in body and "停損 570.00" in body
