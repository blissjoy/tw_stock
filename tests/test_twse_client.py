from src.data.twse_client import (
    _parse_roc_slash_date,
    format_date,
    parse_institutional_investors,
    parse_margin_trading,
    parse_stock_prices,
    parse_taiex_volume,
)


def test_format_date():
    # TWSE回應裡的date欄位本身已是西元年YYYYMMDD，不是民國年（頁面標題文字才是民國年，容易混淆）
    assert format_date("20250715") == "2025-07-15"
    assert format_date("20260101") == "2026-01-01"


def test_parse_stock_prices_filters_non_4digit_codes():
    raw = {
        "stat": "OK",
        "date": "20250715",
        "tables": [
            {
                "title": "114年07月15日 每日收盤行情(全部)",
                "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差"],
                "data": [
                    ["1101", "台泥", "10,000,000", "5,000", "500,000,000", "50.00", "51.00", "49.50", "50.50", "<p style= color:red>+</p>", "0.50"],
                    ["006201", "元大富櫃50", "100,000", "50", "4,000,000", "40.00", "41.00", "39.50", "40.50", "<p style= color:red>+</p>", "0.50"],
                ],
            }
        ],
    }
    rows = parse_stock_prices(raw)
    assert len(rows) == 1
    row = rows[0]
    assert row["stock_id"] == "1101"
    assert row["date"] == "2025-07-15"
    assert row["open"] == 50.0
    assert row["high"] == 51.0
    assert row["low"] == 49.5
    assert row["close"] == 50.5
    assert row["volume"] == 10000000
    assert row["trading_money"] == 500000000
    assert row["trading_turnover"] == 5000
    assert row["spread"] == 0.5


def test_parse_stock_prices_returns_empty_on_holiday():
    raw = {"stat": "很抱歉，沒有符合條件的資料!", "type": "ALL"}
    assert parse_stock_prices(raw) == []


def test_parse_stock_prices_skips_rows_with_no_trade_placeholder():
    # 收盤欄位為"--"或空字串代表當日該股票無成交，不可轉成收盤價=0（會讓下游報酬率計算除以0）
    raw = {
        "stat": "OK",
        "date": "20250715",
        "tables": [
            {
                "title": "114年07月15日 每日收盤行情(全部)",
                "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差"],
                "data": [
                    ["1101", "台泥", "10,000,000", "5,000", "500,000,000", "50.00", "51.00", "49.50", "50.50", "<p style= color:red>+</p>", "0.50"],
                    ["1203", "神隆", "0", "0", "0", "--", "--", "--", "--", "", "--"],
                    ["2330", "台積電", "0", "0", "0", "", "", "", "", "", ""],
                ],
            }
        ],
    }
    rows = parse_stock_prices(raw)
    assert [r["stock_id"] for r in rows] == ["1101"]


def test_parse_institutional_investors_maps_five_categories():
    # T86欄位順序：代號,名稱,外陸資買,外陸資賣,外陸資買賣超,外資自營商買,外資自營商賣,外資自營商買賣超,
    #             投信買,投信賣,投信買賣超,自營商買賣超合計,自營商買(自行),自營商賣(自行),自營商買賣超(自行),
    #             自營商買(避險),自營商賣(避險),自營商買賣超(避險),三大法人買賣超合計
    raw = {
        "stat": "OK",
        "date": "20250715",
        "data": [
            ["1101", "台泥",
             "100", "50", "50",
             "10", "5", "5",
             "20", "8", "12",
             "0",
             "30", "12", "18",
             "40", "15", "25",
             "999"],
        ],
    }
    rows = parse_institutional_investors(raw)
    by_type = {r["investor_type"]: r for r in rows}
    assert by_type["Foreign_Investor"]["buy"] == 100
    assert by_type["Foreign_Investor"]["sell"] == 50
    assert by_type["Foreign_Dealer_Self"]["buy"] == 10
    assert by_type["Foreign_Dealer_Self"]["sell"] == 5
    assert by_type["Investment_Trust"]["buy"] == 20
    assert by_type["Investment_Trust"]["sell"] == 8
    assert by_type["Dealer_self"]["buy"] == 30
    assert by_type["Dealer_self"]["sell"] == 12
    assert by_type["Dealer_Hedging"]["buy"] == 40
    assert by_type["Dealer_Hedging"]["sell"] == 15
    assert all(r["date"] == "2025-07-15" for r in rows)


def test_parse_margin_trading():
    raw = {
        "stat": "OK",
        "date": "20250715",
        "tables": [
            {
                "title": "114年07月15日 融資融券彙總 (全部)",
                "data": [
                    ["1101", "台泥", "439", "1249", "47", "9592", "8735", "3308375",
                     "280", "0", "1", "500", "779", "3308375", "0", ""],
                ],
            }
        ],
    }
    rows = parse_margin_trading(raw)
    assert len(rows) == 1
    row = rows[0]
    assert row["stock_id"] == "1101"
    assert row["margin_purchase_buy"] == 439
    assert row["margin_purchase_sell"] == 1249
    assert row["margin_purchase_today_balance"] == 8735
    assert row["short_sale_buy"] == 280
    assert row["short_sale_sell"] == 0
    assert row["short_sale_today_balance"] == 779
    assert row["offset_loan_and_short"] == 0


# ============================================================
# FMTQIK(大盤統計資訊，2026-08-04新增)
# ============================================================


def test_parse_roc_slash_date():
    assert _parse_roc_slash_date("115/08/03") == "2026-08-03"
    assert _parse_roc_slash_date("115/01/01") == "2026-01-01"


def test_parse_taiex_volume_returns_date_to_volume_map():
    # 真實回應樣本(2026-08-04實測)：['115/08/03', '11,427,047,935', '885,506,043,091', '4,191,882', '43,386.41', '266.66']
    raw = {
        "stat": "OK",
        "data": [
            ["115/08/03", "11,427,047,935", "885,506,043,091", "4,191,882", "43,386.41", "266.66"],
            ["115/08/04", "9,876,543,210", "800,000,000,000", "4,000,000", "43,360.66", "0.00"],
        ],
    }
    result = parse_taiex_volume(raw)
    assert result == {"2026-08-03": 11427047935, "2026-08-04": 9876543210}


def test_parse_taiex_volume_empty_when_stat_not_ok():
    # 查詢還沒有任何交易日資料的月份時的實際回應型態
    assert parse_taiex_volume({"stat": "查詢無資料"}) == {}
