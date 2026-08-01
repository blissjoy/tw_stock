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
    assert "2330" in text
    assert "2454" in text


def test_format_candidates_message_dedupes_same_stock_and_shows_match_count():
    """2026-08-01改版：同一檔股票符合多條規則時(screen_all_stocks()刻意不去重)，
    LINE訊息應該合併成一行「{代號}{名稱} 符合規格數{n}」，不是逐條規則各自一行。"""
    candidates = [
        {"stock_id": "00937B", "signal_name": "R-MA-23單一均線短線做空（88%）", "entry_price": 14.73, "stop_loss": 14.75, "note": None},
        {"stock_id": "00937B", "signal_name": "R-MA-25單一均線中線做空（88%）", "entry_price": 14.73, "stop_loss": 14.75, "note": None},
        {"stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場（92%）", "entry_price": 600.0, "stop_loss": 570.0, "note": None},
    ]

    text = format_candidates_message("2026-07-22", candidates, stock_names={"00937B": "元大美債20正2", "2330": "台積電"})

    assert "共2檔候選" in text  # 2檔不重複的股票，不是3條規則紀錄
    lines = text.splitlines()
    assert "・00937B元大美債20正2 符合規格數2" in lines
    assert "・2330台積電 符合規格數1" in lines
    # 00937B兩條規則信心加總176(88+88) > 2330單條92，應該排在前面
    assert lines.index("・00937B元大美債20正2 符合規格數2") < lines.index("・2330台積電 符合規格數1")


def test_format_candidates_message_missing_name_falls_back_to_stock_id_only():
    candidates = [{"stock_id": "9999", "signal_name": "R-TREND-14多頭短線進場（92%）", "entry_price": 1.0, "stop_loss": 0.9, "note": None}]

    text = format_candidates_message("2026-07-22", candidates)

    assert "・9999 符合規格數1" in text


def test_format_candidates_email_body_empty():
    body = format_candidates_email_body("2026-07-22", [])
    assert "沒有符合條件" in body


def test_format_candidates_email_body_lists_all_candidates():
    body = format_candidates_email_body("2026-07-22", _sample_candidates())
    assert "共2檔候選" in body
    assert "2330" in body and "進場價 600.00" in body and "停損 570.00" in body
