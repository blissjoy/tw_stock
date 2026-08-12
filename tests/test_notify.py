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


def test_format_candidates_message_appends_inventory_escape_section_when_present():
    """2026-08-12新增：使用者要求庫存逃命示警跟候選清單同一則LINE訊息一起發送，
    不要分成兩則。這裡驗證有逃命示警時，訊息裡候選清單段落後面接著出現逃命示警段落
    (同一個字串，不是兩個獨立回傳值)。"""
    candidates = _sample_candidates()
    escape_signals = {
        "2317": [{"rule_id": "R-CANDLE-05", "title": "高檔變盤線"}, {"rule_id": "R-TREND-04", "title": "空頭趨勢判定"}],
        "6139": [{"rule_id": "R-ESCAPE-KD-DEATH-CROSS", "title": "KD死亡交叉"}],
    }

    text = format_candidates_message(
        "2026-07-22", candidates, stock_names={"2317": "鴻海", "6139": "亞翔"},
        inventory_escape_signals=escape_signals,
    )

    assert "共2檔候選" in text  # 候選清單段落照常存在
    assert "🚨庫存逃命示警（2檔）：" in text
    lines = text.splitlines()
    assert "・2317鴻海：R-CANDLE-05、R-TREND-04" in lines
    assert "・6139亞翔：R-ESCAPE-KD-DEATH-CROSS" in lines
    # 逃命示警段落要接在候選清單段落之後，不是前面
    candidate_idx = next(i for i, l in enumerate(lines) if l.startswith("【"))
    escape_idx = next(i for i, l in enumerate(lines) if l.startswith("🚨庫存逃命示警"))
    assert candidate_idx < escape_idx


def test_format_candidates_message_skips_inventory_escape_section_when_all_empty():
    """load_escape_signals_for_stocks()回傳的dict可能含「查有持股但沒有逃命示警」的
    股票(值是空清單)——這裡確認全部都是空清單時，不會生出一個空的「🚨庫存逃命示警」
    段落標題。"""
    text = format_candidates_message(
        "2026-07-22", _sample_candidates(), inventory_escape_signals={"2330": [], "2454": []},
    )

    assert "🚨庫存逃命示警" not in text


def test_format_candidates_message_inventory_escape_section_shown_even_when_no_candidates():
    """候選清單是空的(今天沒有符合條件的候選股)也不該影響逃命示警段落照常顯示——
    兩段話是獨立的資訊，候選清單空不代表庫存逃命示警也不重要。"""
    text = format_candidates_message(
        "2026-07-22", [], inventory_escape_signals={"2317": [{"rule_id": "R-CANDLE-05", "title": "高檔變盤線"}]},
    )

    assert "沒有符合條件" in text
    assert "🚨庫存逃命示警（1檔）：" in text
    assert "・2317：R-CANDLE-05" in text


def test_format_candidates_email_body_empty():
    body = format_candidates_email_body("2026-07-22", [])
    assert "沒有符合條件" in body


def test_format_candidates_email_body_lists_all_candidates():
    body = format_candidates_email_body("2026-07-22", _sample_candidates())
    assert "共2檔候選" in body
    assert "2330" in body and "進場價 600.00" in body and "停損 570.00" in body
