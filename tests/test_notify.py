from src.notify.email_notify import format_candidates_email_body
from src.notify import line_notify
from src.notify.line_notify import format_candidates_flex_message, format_candidates_message


def _sample_candidates():
    return [
        {"stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 600.0, "stop_loss": 570.0, "note": "多頭架構＋攻擊量"},
        {"stock_id": "2454", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 1000.0, "stop_loss": 950.0, "note": "多頭架構＋攻擊量"},
    ]


def test_format_candidates_message_empty():
    text = format_candidates_message("2026-07-22", [], taiex_sar_is_bull=True)
    assert "2026-07-22" in text
    assert text.splitlines()[0] == "加權指數 SAR +"
    assert "沒有符合條件" in text


def test_format_candidates_message_lists_all_candidates():
    text = format_candidates_message("2026-07-22", _sample_candidates(), taiex_sar_is_bull=False)
    assert "共2檔候選" in text
    assert text.splitlines()[0] == "加權指數 SAR -"
    assert "2330" in text
    assert "2454" in text


def test_format_candidates_message_dedupes_same_stock_and_shows_match_count():
    """同一檔股票符合多條規則時，訊息合併成一行並顯示股價與符合規格數。"""
    candidates = [
        {"stock_id": "00937B", "signal_name": "R-MA-23單一均線短線做空（88%）", "entry_price": 14.73, "stop_loss": 14.75, "note": None},
        {"stock_id": "00937B", "signal_name": "R-MA-25單一均線中線做空（88%）", "entry_price": 14.73, "stop_loss": 14.75, "note": None},
        {"stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場（92%）", "entry_price": 600.0, "stop_loss": 570.0, "note": None},
    ]

    text = format_candidates_message(
        "2026-07-22", candidates,
        stock_names={"00937B": "元大美債20正2", "2330": "台積電"},
        current_prices={"00937B": 14.73, "2330": 600.0},
    )

    assert "共2檔候選" in text  # 2檔不重複的股票，不是3條規則紀錄
    lines = text.splitlines()
    assert "・00937B元大美債20正2 14.73 符合規格數2" in lines
    assert "・2330台積電 600.00 符合規格數1" in lines
    # 00937B兩條規則信心加總176(88+88) > 2330單條92，應該排在前面
    assert lines.index("・00937B元大美債20正2 14.73 符合規格數2") < lines.index("・2330台積電 600.00 符合規格數1")


def test_format_candidates_message_missing_name_falls_back_to_stock_id_only():
    candidates = [{"stock_id": "9999", "signal_name": "R-TREND-14多頭短線進場（92%）", "entry_price": 1.0, "stop_loss": 0.9, "note": None}]

    text = format_candidates_message("2026-07-22", candidates)

    assert "・9999 - 符合規格數1" in text


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
    assert "・2330台積電(半導體) - 符合規格數1" in lines
    assert "・2454聯發科(半導體) - 符合規格數1" in lines


def test_format_candidates_message_includes_sar_next_day_items_and_closes():
    preview = {
        "buy_items": [{
            "stock_id": "2330", "name": "台積電", "close": 1450.0,
            "momentum_5d": {
                "foreign": {"current": 15_293_000, "prior": 14_310_000, "trend": "買超力道增強"},
                "trust": {"current": -1_000_000, "prior": -2_000_000, "trend": "賣壓減緩"},
            },
        }],
        "sell_items": [{
            "stock_id": "2454", "name": "聯發科", "close": 1275.0,
            "momentum_5d": {
                "foreign": {"current": -1_000_000, "prior": -500_000, "trend": "賣壓加重"},
                "trust": {"current": 1_000_000, "prior": 2_000_000, "trend": "買超力道減弱"},
            },
        }],
    }

    text = format_candidates_message("2026-11-03", [], sar_next_day_preview=preview)

    assert "【實戰模擬｜明日】" in text
    assert "買進候選：" in text
    assert "・2330 台積電 1450.00 5日力道 外資↑ 投信↑" in text
    assert "賣出觀察：" in text
    assert "・2454 聯發科 1275.00 5日力道 外資↓ 投信↓" in text


def test_format_candidates_message_marks_missing_five_day_institutional_data():
    text = format_candidates_message(
        "2026-11-03",
        [],
        sar_next_day_preview={
            "buy_items": [{"stock_id": "1216", "name": "統一", "close": 81.1, "momentum_5d": None}],
            "sell_items": [],
        },
    )

    assert "・1216 統一 81.10 5日力道 外資資料不足 投信資料不足" in text


def test_format_candidates_message_marks_intraday_sar_preview_as_provisional():
    text = format_candidates_message(
        "2026-11-03",
        [],
        sar_next_day_preview={
            "buy_items": [{"stock_id": "2330", "name": "台積電", "close": 1450.0}],
            "sell_items": [],
        },
        sar_preview_is_intraday=True,
    )

    assert "【實戰模擬｜明日（盤中估算）】" in text
    assert "・2330 台積電 1450.00" in text


def test_format_candidates_message_marks_index_sar_unavailable():
    text = format_candidates_message("2026-11-03", [])

    assert text.splitlines()[0] == "加權指數 SAR 資料不足"


def test_format_candidates_message_shows_empty_sar_preview_lists_and_missing_close():
    text = format_candidates_message(
        "2026-11-03",
        [],
        sar_next_day_preview={
            "buy_items": [],
            "sell_items": [{"stock_id": "2454", "name": "聯發科", "close": None}],
        },
    )

    assert "買進候選：\n無" in text
    assert "・2454 聯發科 -" in text


def test_format_candidates_message_appends_inventory_warnings_section_when_present():
    """2026-08-12新增、2026-08-13改版：使用者要求庫存逃命示警跟候選清單同一則LINE
    訊息一起發送(不分成兩則)，格式是「【庫存警示】」＋逐檔「{名稱} 現{現價} 成{成本}
    {損益%}」。這裡驗證有逃命示警的持股時，訊息裡候選清單段落後面接著出現庫存警示
    段落(同一個字串，不是兩個獨立回傳值)。"""
    candidates = _sample_candidates()
    warnings = [
        {
            "stock_id": "2317", "name": "鴻海", "close": 270.0, "cost_price": 235.76,
            "return_pct": 14.08,
            "momentum_5d": {
                "foreign": {"trend": "買超力道增強"},
                "trust": {"trend": "賣壓減緩"},
            },
        },
        {"stock_id": "6139", "name": "亞翔", "close": 811.0, "cost_price": 813.0, "return_pct": -0.63},
    ]

    text = format_candidates_message("2026-07-22", candidates, inventory_warnings=warnings)

    assert "共2檔候選" in text  # 候選清單段落照常存在
    assert "【庫存警示】" in text
    lines = text.splitlines()
    assert "・鴻海 現270.00 成235.76 +14.1% 5日力道 外資↑ 投信↑" in lines
    assert "・亞翔 現811.00 成813.00 -0.6% 5日力道 外資資料不足 投信資料不足" in lines
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
    assert "・鴻海 現270.00 成235.76 +14.1% 5日力道 外資資料不足 投信資料不足" in text


def test_format_candidates_message_inventory_warning_missing_values_show_dash():
    """成本價/損益%缺值(使用者沒填成本價，或系統暫時查不到現價)時顯示"-"，不能讓
    None直接f-string格式化炸掉整則通知。"""
    text = format_candidates_message(
        "2026-07-22", [],
        inventory_warnings=[{"stock_id": "9999", "name": "測試股", "close": None, "cost_price": None, "return_pct": None}],
    )

    assert "・測試股 現- 成- - 5日力道 外資資料不足 投信資料不足" in text


def _flex_text_components(value):
    if isinstance(value, dict):
        if value.get("type") == "text":
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _flex_text_components(child)
    elif isinstance(value, list):
        for child in value:
            yield from _flex_text_components(child)


def test_format_candidates_flex_message_applies_requested_colors_and_bold_alert():
    message = format_candidates_flex_message(
        "2026-11-03",
        [],
        inventory_warnings=[
            {
                "stock_id": "1216",
                "name": "統一",
                "close": 81.1,
                "cost_price": 90.0,
                "return_pct": -9.9,
                "momentum_5d": {
                    "foreign": {"current": -1000, "trend": "賣壓加重"},
                    "trust": {"current": -2000, "trend": "賣壓加重"},
                },
            },
            {
                "stock_id": "2330",
                "name": "台積電",
                "close": 1400.0,
                "cost_price": 1400.0,
                "return_pct": 0.0,
                "momentum_5d": {
                    "foreign": {"current": 1000, "trend": "買超力道增強"},
                    "trust": {"current": -2000, "trend": "賣壓加重"},
                },
            },
            {
                "stock_id": "2317",
                "name": "鴻海",
                "close": 200.0,
                "cost_price": 180.0,
                "return_pct": 11.1,
                "momentum_5d": {
                    "foreign": {"current": -1000, "trend": "賣壓加重"},
                    "trust": {"current": 0, "trend": "買賣力道持平"},
                },
            },
        ],
        sar_next_day_preview={
            "buy_items": [{
                "stock_id": "2454",
                "name": "聯發科",
                "close": 980.0,
                "momentum_5d": {
                    "foreign": {"current": 1000, "prior": 500, "trend": "買超力道增強"},
                    "trust": {"current": -1000, "prior": -500, "trend": "賣壓加重"},
                },
            }],
            "sell_items": [{
                "stock_id": "2317",
                "name": "鴻海",
                "close": 200.0,
                "momentum_5d": {
                    "foreign": {"current": 0, "prior": 0, "trend": "買賣力道持平"},
                    "trust": {"current": 0, "prior": 0, "trend": "買賣力道持平"},
                },
            }],
        },
        taiex_sar_is_bull=True,
    )
    bubble = message["contents"]
    components = list(_flex_text_components(message))

    assert message["type"] == "flex"
    assert bubble["type"] == "bubble"
    assert bubble["header"]["contents"][0]["color"] == "#C0392B"

    warning_section = next(
        section for section in bubble["body"]["contents"]
        if section["contents"][0].get("text") == "【庫存警示】"
    )
    negative_row, zero_row, positive_row = warning_section["contents"][1:]
    assert negative_row["weight"] == "bold"
    assert negative_row["contents"][1]["color"] == "#27AE60"
    assert negative_row["contents"][3]["color"] == "#27AE60"
    assert negative_row["contents"][5]["color"] == "#27AE60"
    assert "weight" not in zero_row
    assert zero_row["contents"][1]["color"] == "#222222"
    assert zero_row["contents"][3]["color"] == "#C0392B"
    assert zero_row["contents"][5]["color"] == "#27AE60"
    assert positive_row["contents"][1]["color"] == "#C0392B"
    assert positive_row["contents"][3]["color"] == "#27AE60"
    assert positive_row["contents"][5]["color"] == "#222222"
    assert "??? " not in str(message)

    preview_section = next(
        section for section in bubble["body"]["contents"]
        if section["contents"][0].get("text") == "【實戰模擬｜明日】"
    )
    item_row = next(
        component for component in preview_section["contents"]
        if component.get("type") == "text" and "2454 聯發科" in str(component)
    )
    assert item_row["contents"][0]["color"] == "#222222"
    assert item_row["contents"][2]["color"] == "#C0392B"
    assert item_row["contents"][4]["color"] == "#27AE60"
    sell_row = next(
        component for component in preview_section["contents"]
        if component.get("type") == "text" and "2317 鴻海" in str(component)
    )
    assert sell_row["contents"][0]["color"] == "#222222"
    assert sell_row["contents"][2]["color"] == "#222222"
    assert sell_row["contents"][4]["color"] == "#222222"
    assert components


def test_format_candidates_flex_message_colors_bearish_index_green():
    message = format_candidates_flex_message("2026-11-03", [], taiex_sar_is_bull=False)

    assert message["contents"]["header"]["contents"][0]["color"] == "#27AE60"


def test_send_line_broadcast_sends_flex_message_object(monkeypatch):
    captured = {}

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(line_notify, "get_line_channel_token", lambda: "test-token")
    monkeypatch.setattr(
        line_notify.requests,
        "post",
        lambda url, **kwargs: captured.update(url=url, **kwargs) or _Response(),
    )
    message = format_candidates_flex_message(
        "2026-11-03", [], taiex_sar_is_bull=True,
    )

    line_notify.send_line_broadcast(message)

    assert captured["json"]["messages"] == [message]


def test_format_candidates_email_body_empty():
    body = format_candidates_email_body("2026-07-22", [])
    assert "沒有符合條件" in body


def test_format_candidates_email_body_lists_all_candidates():
    body = format_candidates_email_body("2026-07-22", _sample_candidates())
    assert "共2檔候選" in body
    assert "2330" in body and "進場價 600.00" in body and "停損 570.00" in body
