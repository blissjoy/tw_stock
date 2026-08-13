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


def test_format_candidates_message_appends_industry_when_provided():
    """2026-08-13新增：使用者要求候選清單每一行加上產業別，格式「{代號}{名稱}(產業別)
    符合規格數{n}」。省略stock_industries或查無產業別時維持原本不帶括號的格式
    (見test_format_candidates_message_missing_name_falls_back_to_stock_id_only()，
    向下相容)。"""
    candidates = _sample_candidates()

    text = format_candidates_message(
        "2026-07-22", candidates,
        stock_names={"2330": "台積電", "2454": "聯發科"},
        stock_industries={"2330": "半導體", "2454": "半導體"},
    )

    lines = text.splitlines()
    assert "・2330台積電(半導體) 符合規格數1" in lines
    assert "・2454聯發科(半導體) 符合規格數1" in lines


def test_format_candidates_message_appends_inventory_warnings_section_when_present():
    """2026-08-12新增、2026-08-13改版：使用者要求庫存逃命示警跟候選清單同一則LINE
    訊息一起發送(不分成兩則)，格式是「【庫存警示】」＋逐檔「{名稱} 現{現價} 成{成本}
    {損益%}」。這裡驗證有逃命示警的持股時，訊息裡候選清單段落後面接著出現庫存警示
    段落(同一個字串，不是兩個獨立回傳值)。"""
    candidates = _sample_candidates()
    warnings = [
        {"stock_id": "2317", "name": "鴻海", "close": 270.0, "cost_price": 235.76, "return_pct": 14.08},
        {"stock_id": "6139", "name": "亞翔", "close": 811.0, "cost_price": 813.0, "return_pct": -0.63},
    ]

    text = format_candidates_message("2026-07-22", candidates, inventory_warnings=warnings)

    assert "共2檔候選" in text  # 候選清單段落照常存在
    assert "【庫存警示】" in text
    lines = text.splitlines()
    assert "・鴻海 現270.00 成235.76 +14.1%" in lines
    assert "・亞翔 現811.00 成813.00 -0.6%" in lines
    # 庫存警示段落要接在候選清單段落之後，不是前面
    candidate_idx = next(i for i, l in enumerate(lines) if l.startswith("【2026"))
    warning_idx = lines.index("【庫存警示】")
    assert candidate_idx < warning_idx


def test_format_candidates_message_shows_safe_when_checked_but_no_warnings():
    """inventory_warnings傳空清單(不是None)代表「有檢查、目前沒有任何逃命示警」，
    要顯示「安全」——跟inventory_warnings=None(沒有執行檢查，整段【庫存警示】都
    不出現，見下一個測試)是不同的狀態，不能混為一談。"""
    text = format_candidates_message("2026-07-22", _sample_candidates(), inventory_warnings=[])

    assert "【庫存警示】" in text
    assert "安全" in text


def test_format_candidates_message_omits_inventory_section_when_not_checked():
    """inventory_warnings維持預設值None(呼叫端的庫存檢查本身失敗/沒有執行)時，
    訊息裡完全不出現「【庫存警示】」這段——不能讓使用者誤以為「有檢查、沒問題」。"""
    text = format_candidates_message("2026-07-22", _sample_candidates())

    assert "【庫存警示】" not in text


def test_format_candidates_message_inventory_warnings_shown_even_when_no_candidates():
    """候選清單是空的(今天沒有符合條件的候選股)也不該影響庫存警示段落照常顯示——
    兩段話是獨立的資訊，候選清單空不代表庫存警示也不重要。"""
    text = format_candidates_message(
        "2026-07-22", [],
        inventory_warnings=[{"stock_id": "2317", "name": "鴻海", "close": 270.0, "cost_price": 235.76, "return_pct": 14.08}],
    )

    assert "沒有符合條件" in text
    assert "【庫存警示】" in text
    assert "・鴻海 現270.00 成235.76 +14.1%" in text


def test_format_candidates_message_inventory_warning_missing_values_show_dash():
    """成本價/損益%缺值(使用者沒填成本價，或系統暫時查不到現價)時顯示"-"，不能讓
    None直接f-string格式化炸掉整則通知。"""
    text = format_candidates_message(
        "2026-07-22", [],
        inventory_warnings=[{"stock_id": "9999", "name": "測試股", "close": None, "cost_price": None, "return_pct": None}],
    )

    assert "・測試股 現- 成- -" in text


def test_format_candidates_email_body_empty():
    body = format_candidates_email_body("2026-07-22", [])
    assert "沒有符合條件" in body


def test_format_candidates_email_body_lists_all_candidates():
    body = format_candidates_email_body("2026-07-22", _sample_candidates())
    assert "共2檔候選" in body
    assert "2330" in body and "進場價 600.00" in body and "停損 570.00" in body
