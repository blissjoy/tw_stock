from src.data.storage import (
    init_db,
    upsert_daily_indicators,
    upsert_holder_shares_distribution,
    upsert_institutional_investors,
    upsert_stock_prices,
    upsert_stocks,
)
from src.presentation import huang_chip_data


def _fresh_conn():
    return init_db(":memory:")


def _seed_stock(conn, stock_id: str = "2330", name: str = "台積電") -> None:
    upsert_stocks(conn, [{"stock_id": stock_id, "name": name, "market": "TWSE", "industry": None, "updated_at": "2026-08-03"}])


def _institutional_row(stock_id: str, date: str, investor_type: str, buy: int, sell: int) -> dict:
    return {"stock_id": stock_id, "date": date, "investor_type": investor_type, "buy": buy, "sell": sell}


# ============================================================
# load_institutional_streak_and_flow (D/E, K~R)
# ============================================================


def test_load_institutional_streak_and_flow_computes_streak_and_lots():
    conn = _fresh_conn()
    _seed_stock(conn)
    rows = []
    for d in range(5):
        date_str = f"2026-07-{28 + d:02d}" if d < 4 else "2026-08-01"
        rows.append(_institutional_row("2330", date_str, "Investment_Trust", buy=2_000_000, sell=1_000_000))
        rows.append(_institutional_row("2330", date_str, "Foreign_Investor", buy=1_000_000, sell=2_000_000))
    upsert_institutional_investors(conn, rows)

    result = huang_chip_data.load_institutional_streak_and_flow(conn, "2330", as_of_date="2026-08-01")

    assert result["invest_streak"]["text"] == "連買5天"
    assert result["foreign_streak"]["text"] == "連賣5天"
    # 每天淨額：投信+1,000,000股=1000張，5天40日加總=5000張；外資-1,000,000股=-1000張
    assert result["flow"]["invest_5d"] == 5000
    assert result["flow"]["foreign_5d"] == -5000


def test_load_institutional_streak_and_flow_ignores_other_investor_types():
    """外資只查Foreign_Investor，不併入Foreign_Dealer_Self——這是使用者明確要求的口徑，
    先跟黃豐凱籌碼分析法原始程式碼一致，不是本專案「個股明細」既有的三大法人併計方式。"""
    conn = _fresh_conn()
    _seed_stock(conn)
    upsert_institutional_investors(conn, [
        _institutional_row("2330", "2026-08-01", "Foreign_Investor", buy=1_000_000, sell=0),
        _institutional_row("2330", "2026-08-01", "Foreign_Dealer_Self", buy=999_000_000, sell=0),
    ])

    result = huang_chip_data.load_institutional_streak_and_flow(conn, "2330", as_of_date="2026-08-01")

    assert result["flow"]["foreign_40d"] == 1000  # 只有Foreign_Investor這1000張，不含Foreign_Dealer_Self


def test_load_institutional_streak_and_flow_flow_is_none_when_no_data_at_all():
    conn = _fresh_conn()
    _seed_stock(conn)

    result = huang_chip_data.load_institutional_streak_and_flow(conn, "2330", as_of_date="2026-08-01")

    assert result["flow"] is None
    assert result["invest_streak"] == {"text": "", "color": "#000000"}


def test_load_institutional_streak_and_flow_defaults_as_of_date_to_latest_price_date():
    conn = _fresh_conn()
    _seed_stock(conn)
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-08-01", "open": 100.0, "high": 100.0, "low": 100.0,
         "close": 100.0, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_institutional_investors(conn, [
        _institutional_row("2330", "2026-08-01", "Investment_Trust", buy=2_000_000, sell=1_000_000),
    ])

    result = huang_chip_data.load_institutional_streak_and_flow(conn, "2330")  # as_of_date=None

    assert result["invest_streak"]["text"] == "連買1天"


# ============================================================
# load_ma_price_position (H)
# ============================================================


def test_load_ma_price_position_compares_today_vs_previous_trading_day():
    conn = _fresh_conn()
    _seed_stock(conn)
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-08-01", "open": 100.0, "high": 105.0, "low": 99.0,
         "close": 104.0, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_indicators(conn, [
        {"stock_id": "2330", "date": "2026-07-31", "ma5": 10.0, "ma10": 10.0, "ma20": 10.0, "ma60": 20.0,
         "ma120": None, "ma200": None, "ma240": None, "sar_value": None, "sar_is_bull": None,
         "sar_flip_days_ago": None, "updated_at": "2026-07-31T17:00:00"},
        {"stock_id": "2330", "date": "2026-08-01", "ma5": 11.0, "ma10": 11.0, "ma20": 11.0, "ma60": 19.0,
         "ma120": None, "ma200": None, "ma240": None, "sar_value": None, "sar_is_bull": None,
         "sar_flip_days_ago": None, "updated_at": "2026-08-01T17:00:00"},
    ])

    result = huang_chip_data.load_ma_price_position(conn, "2330", as_of_date="2026-08-01")

    ma20_line = next(line for line in result["lines"] if line["text"].startswith("MA20"))
    ma60_line = next(line for line in result["lines"] if line["text"].startswith("MA60"))
    assert ma20_line["text"] == "MA20 上揚"  # 11 >= 10
    assert ma60_line["text"] == "MA60 下彎"  # 19 < 20


def test_load_ma_price_position_none_when_fewer_than_2_days_of_indicators():
    conn = _fresh_conn()
    _seed_stock(conn)
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-08-01", "open": 100.0, "high": 105.0, "low": 99.0,
         "close": 104.0, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_indicators(conn, [
        {"stock_id": "2330", "date": "2026-08-01", "ma5": 11.0, "ma10": 11.0, "ma20": 11.0, "ma60": 19.0,
         "ma120": None, "ma200": None, "ma240": None, "sar_value": None, "sar_is_bull": None,
         "sar_flip_days_ago": None, "updated_at": "2026-08-01T17:00:00"},
    ])

    assert huang_chip_data.load_ma_price_position(conn, "2330", as_of_date="2026-08-01") is None


def test_load_ma_price_position_none_when_no_data():
    conn = _fresh_conn()
    _seed_stock(conn)
    assert huang_chip_data.load_ma_price_position(conn, "2330") is None


# ============================================================
# load_weekly_volume_pattern (I)
# ============================================================


def test_load_weekly_volume_pattern_reads_from_stock_prices():
    conn = _fresh_conn()
    _seed_stock(conn)
    rows = []
    # 大量週：2026-01-05~09
    for i, d in enumerate(["05", "06", "07", "08", "09"]):
        rows.append({"stock_id": "2330", "date": f"2026-01-{d}", "open": 15.0, "high": 20.0, "low": 10.0,
                      "close": 15.0, "volume": 100000, "trading_money": None, "trading_turnover": None, "spread": None})
    # 最近一週：2026-01-12~16，量小，收盤價漲破大量週的高點(20)
    for d in ["12", "13", "14", "15", "16"]:
        rows.append({"stock_id": "2330", "date": f"2026-01-{d}", "open": 20.5, "high": 21.0, "low": 20.0,
                      "close": 21.0, "volume": 10, "trading_money": None, "trading_turnover": None, "spread": None})
    upsert_stock_prices(conn, rows)

    result = huang_chip_data.load_weekly_volume_pattern(conn, "2330", as_of_date="2026-01-16")

    assert result["pattern"] == "大量高之上"
    assert result["reference_week_start"] == "2026-01-05"


def test_load_weekly_volume_pattern_none_when_no_data():
    conn = _fresh_conn()
    _seed_stock(conn)
    assert huang_chip_data.load_weekly_volume_pattern(conn, "2330") is None


# ============================================================
# load_holder_change (F/G)
# ============================================================


def test_load_holder_change_reads_from_holder_shares_distribution():
    conn = _fresh_conn()
    _seed_stock(conn)
    upsert_holder_shares_distribution(conn, [
        {"stock_id": "2330", "date": "2026-07-24", "holding_shares_level": "more than 1,000,001",
         "people": 10, "unit": 100, "percent": 10.0, "updated_at": "2026-07-24T00:00:00"},
        {"stock_id": "2330", "date": "2026-07-31", "holding_shares_level": "more than 1,000,001",
         "people": 10, "unit": 100, "percent": 12.0, "updated_at": "2026-07-31T00:00:00"},
    ])

    result = huang_chip_data.load_holder_change(conn, "2330")

    assert result["whale"]["text"] == "大戶爆買 +2.00%"


def test_load_holder_change_none_when_no_data():
    conn = _fresh_conn()
    _seed_stock(conn)
    assert huang_chip_data.load_holder_change(conn, "2330") is None


# ============================================================
# load_huang_chip_row (組合)
# ============================================================


def test_load_huang_chip_row_combines_all_fields_gracefully_when_partial_data():
    """只有法人資料、沒有daily_indicators/holder_shares_distribution時，其餘欄位應該
    分別回傳None，不應該crash或連帶讓其他欄位也壞掉。"""
    conn = _fresh_conn()
    _seed_stock(conn)
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-08-01", "open": 100.0, "high": 100.0, "low": 100.0,
         "close": 100.0, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_institutional_investors(conn, [
        _institutional_row("2330", "2026-08-01", "Investment_Trust", buy=2_000_000, sell=1_000_000),
    ])

    row = huang_chip_data.load_huang_chip_row(conn, "2330")

    assert row["invest_streak"]["text"] == "連買1天"
    assert row["ma_price_position"] is None
    assert row["holder_change"] is None
