import sqlite3

from src.data.storage import (
    delete_daily_candidates_for_date,
    get_daily_data_status,
    init_db,
    is_fetched,
    mark_fetched,
    upsert_broker_chips,
    upsert_daily_candidates,
    upsert_daily_data_status,
    upsert_institutional_investors,
    upsert_margin_trading,
    upsert_securities_traders,
    upsert_stock_prices,
    upsert_stocks,
)


def _fresh_db() -> sqlite3.Connection:
    return init_db(":memory:")


def test_init_db_check_same_thread_false_allows_cross_thread_use():
    """Streamlit的@st.cache_resource快取連線可能在不同thread被重用，確認
    check_same_thread=False時，從另一條thread操作連線不會丟ProgrammingError。"""
    import threading

    conn = init_db(":memory:", check_same_thread=False)
    errors = []

    def _use_from_other_thread():
        try:
            upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_use_from_other_thread)
    t.start()
    t.join()

    assert errors == []
    assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 1


def test_init_db_creates_all_expected_tables():
    conn = _fresh_db()
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "stocks", "stock_prices", "institutional_investors", "margin_trading",
        "securities_traders", "broker_chips", "fetch_log",
    }
    assert expected.issubset(tables)


def test_upsert_stocks_inserts_then_updates_on_conflict():
    conn = _fresh_db()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體", "updated_at": "2026-07-22T00:00:00"}])
    row = conn.execute("SELECT name, market FROM stocks WHERE stock_id = '2330'").fetchone()
    assert row == ("台積電", "TWSE")

    upsert_stocks(conn, [{"stock_id": "2330", "name": "台灣積體電路", "market": "TWSE", "industry": "半導體", "updated_at": "2026-07-23T00:00:00"}])
    row2 = conn.execute("SELECT name FROM stocks WHERE stock_id = '2330'").fetchone()
    assert row2 == ("台灣積體電路",)
    count = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    assert count == 1  # 不應產生重複列


def test_upsert_stock_prices_round_trip_and_update():
    conn = _fresh_db()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    row = {"stock_id": "2330", "date": "2026-07-22", "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0,
           "volume": 1000000, "trading_money": 104000000, "trading_turnover": 500, "spread": 2.0}
    upsert_stock_prices(conn, [row])

    fetched = conn.execute("SELECT open, close, volume FROM stock_prices WHERE stock_id='2330' AND date='2026-07-22'").fetchone()
    assert fetched == (100.0, 104.0, 1000000)

    row["close"] = 106.0
    upsert_stock_prices(conn, [row])
    fetched2 = conn.execute("SELECT close FROM stock_prices WHERE stock_id='2330' AND date='2026-07-22'").fetchone()
    assert fetched2 == (106.0,)


def test_upsert_institutional_investors_composite_key():
    conn = _fresh_db()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    rows = [
        {"stock_id": "2330", "date": "2026-07-22", "investor_type": "Foreign_Investor", "buy": 1000, "sell": 800},
        {"stock_id": "2330", "date": "2026-07-22", "investor_type": "Investment_Trust", "buy": 200, "sell": 300},
    ]
    upsert_institutional_investors(conn, rows)
    count = conn.execute("SELECT COUNT(*) FROM institutional_investors WHERE stock_id='2330' AND date='2026-07-22'").fetchone()[0]
    assert count == 2


def test_upsert_margin_trading_and_broker_chips_with_fk():
    conn = _fresh_db()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_margin_trading(conn, [{
        "stock_id": "2330", "date": "2026-07-22",
        "margin_purchase_buy": 100, "margin_purchase_sell": 50, "margin_purchase_cash_repayment": 0,
        "margin_purchase_yesterday_balance": 1000, "margin_purchase_today_balance": 1050,
        "margin_purchase_limit": 500000,
        "short_sale_buy": 10, "short_sale_sell": 20, "short_sale_cash_repayment": 0,
        "short_sale_yesterday_balance": 200, "short_sale_today_balance": 210, "short_sale_limit": 500000,
        "offset_loan_and_short": 0,
    }])
    margin_row = conn.execute("SELECT margin_purchase_today_balance FROM margin_trading WHERE stock_id='2330'").fetchone()
    assert margin_row == (1050,)

    upsert_securities_traders(conn, [{"securities_trader_id": "1020", "securities_trader": "合庫", "address": None, "phone": None, "updated_at": "2026-07-22"}])
    upsert_broker_chips(conn, [{"stock_id": "2330", "date": "2026-07-22", "securities_trader_id": "1020", "price": 508.0, "buy": 4000, "sell": 2000}])
    chip_row = conn.execute("SELECT buy, sell FROM broker_chips WHERE stock_id='2330' AND securities_trader_id='1020'").fetchone()
    assert chip_row == (4000, 2000)


def test_fetch_log_round_trip():
    conn = _fresh_db()
    assert is_fetched(conn, "TaiwanStockPrice", "2330", "2026-07-22") is False
    mark_fetched(conn, "TaiwanStockPrice", "2330", "2026-07-22", "2026-07-22T18:00:00")
    assert is_fetched(conn, "TaiwanStockPrice", "2330", "2026-07-22") is True


def _candidate_row(date_str: str, stock_id: str) -> dict:
    return {
        "date": date_str, "stock_id": stock_id, "signal_name": "R-TREND-14多頭短線進場",
        "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": f"{date_str}T18:00:00",
    }


def test_delete_daily_candidates_for_date_removes_only_that_date():
    conn = _fresh_db()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_daily_candidates(conn, [_candidate_row("2026-07-22", "2330"), _candidate_row("2026-07-23", "2330")])

    delete_daily_candidates_for_date(conn, "2026-07-22")

    remaining = conn.execute("SELECT date FROM daily_candidates").fetchall()
    assert remaining == [("2026-07-23",)]  # 只刪指定日期，2026-07-23的紀錄不受影響


def test_delete_daily_candidates_for_date_is_noop_when_nothing_to_delete():
    conn = _fresh_db()
    delete_daily_candidates_for_date(conn, "2026-07-22")  # 不應該拋出例外
    count = conn.execute("SELECT COUNT(*) FROM daily_candidates").fetchone()[0]
    assert count == 0


def test_get_daily_data_status_returns_none_when_no_record():
    conn = _fresh_db()
    assert get_daily_data_status(conn, "2026-07-24") is None


def test_upsert_daily_data_status_round_trips_intraday_flag():
    conn = _fresh_db()
    upsert_daily_data_status(conn, "2026-07-24", is_intraday=True)
    assert get_daily_data_status(conn, "2026-07-24") is True


def test_upsert_daily_data_status_overwrites_previous_value_for_same_date():
    """同一天先在盤中抓一次(is_intraday=True)、收盤後再抓一次(is_intraday=False)，
    第二次應該覆蓋掉第一次的flag，不是疊加或保留舊值。"""
    conn = _fresh_db()
    upsert_daily_data_status(conn, "2026-07-24", is_intraday=True)
    upsert_daily_data_status(conn, "2026-07-24", is_intraday=False)
    assert get_daily_data_status(conn, "2026-07-24") is False
