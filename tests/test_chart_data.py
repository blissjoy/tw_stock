import pandas as pd
import pytest

import src.presentation.chart_data as chart_data
from src.data.storage import init_db, upsert_daily_candidates, upsert_daily_indicators, upsert_stock_prices, upsert_stocks
from src.presentation.chart_data import (
    apply_candidate_filters,
    build_candlestick_figure,
    compute_ma_bullish_flags,
    compute_sar_flip_flags,
    get_latest_candidate_update_time,
    get_latest_update_time,
    get_stock_name,
    list_candidate_dates,
    load_holidays_for_chart,
    load_ma_bullish_flags_from_table,
    load_price_history,
    load_sar_flip_flags_from_table,
    load_stock_universe_for_date,
    resolve_stock_id,
)
from src.screener.indicator_precompute import compute_indicator_rows


def _fresh_conn():
    return init_db(":memory:")


def _populate_indicators(conn, stock_id: str, price_rows: list[dict]) -> None:
    """把price_rows(list of stock_prices dict)算成均線/SAR、寫進daily_indicators，
    模擬run_screen_and_store()/backfill_daily_indicators.py會做的事，供需要
    daily_indicators已經有資料的測試使用。"""
    df = pd.DataFrame(price_rows)
    df.index = pd.to_datetime(df["date"])
    target_dates = {r["date"] for r in price_rows}
    rows = compute_indicator_rows(stock_id, df, target_dates)
    upsert_daily_indicators(conn, rows)


def test_load_stock_universe_for_date_returns_empty_when_no_records():
    conn = _fresh_conn()
    df, latest_date, is_intraday = load_stock_universe_for_date(conn)
    assert df.empty
    assert latest_date is None
    assert is_intraday is False


def test_load_stock_universe_for_date_defaults_to_most_recent_date():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體", "updated_at": "2026-07-22"}])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-21", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
        {"stock_id": "2330", "date": "2026-07-22", "open": 100.0, "high": 105.0, "low": 100.0, "close": 104.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-21", "stock_id": "2330", "signal_name": "舊訊號", "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-07-21T18:00:00"},
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": "測試", "created_at": "2026-07-22T18:00:00"},
    ])

    df, latest_date, is_intraday = load_stock_universe_for_date(conn)
    assert latest_date == "2026-07-22"
    assert len(df) == 1
    assert df.iloc[0]["stock_id"] == "2330"
    assert df.iloc[0]["name"] == "台積電"
    assert df.iloc[0]["industry"] == "半導體"
    assert df.iloc[0]["signal_name"] == "R-TREND-14多頭短線進場"
    assert is_intraday is False  # 沒有daily_data_status紀錄時預設視為已收盤


def test_load_stock_universe_for_date_includes_stocks_without_any_triggered_rule():
    """2026-08-02改版：候選清單的基礎池不再只有daily_candidates(已觸發某條朱家泓規則的
    股票)，而是當天有股價資料的全市場股票——沒有觸發任何規則的股票也應該出現在這裡，
    signal_name/entry_price/stop_loss是None，由apply_candidate_filters()視篩選條件
    決定要不要保留、要不要補上描述文字。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-22", "open": 100.0, "high": 105.0, "low": 100.0, "close": 104.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
        {"stock_id": "1101", "date": "2026-07-22", "open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0,
         "volume": 2000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 104.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-22T18:00:00"},
    ])

    df, _, _ = load_stock_universe_for_date(conn)

    assert set(df["stock_id"]) == {"2330", "1101"}  # 1101沒觸發任何規則，但仍出現在全市場清單裡
    row_1101 = df[df["stock_id"] == "1101"].iloc[0]
    assert pd.isna(row_1101["signal_name"])
    assert pd.isna(row_1101["entry_price"])
    assert pd.isna(row_1101["stop_loss"])


def test_load_stock_universe_for_date_excludes_stocks_without_price_data_that_day():
    """跟上一個測試相反的情境：股票存在於`stocks`表，但當天完全沒有股價資料(例如還沒
    開始交易、或資料缺漏)——這種股票連漲跌幅/均線/SAR都無從算起，應該直接排除，不是
    用NaN價格硬塞一列進候選清單。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "9999", "name": "尚未交易", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-22", "open": 100.0, "high": 105.0, "low": 100.0, "close": 104.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 104.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-22T18:00:00"},
    ])

    df, _, _ = load_stock_universe_for_date(conn)

    assert set(df["stock_id"]) == {"2330"}


def test_load_stock_universe_for_date_excludes_taiex_index():
    """大盤(market='INDEX')不是一檔可以交易的股票，不該出現在全市場掃描結果裡，跟
    src.screener.daily_screener.load_trailing_frames()排除INDEX的邏輯一致。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "^TWII", "name": "台股加權指數", "market": "INDEX", "industry": None, "updated_at": "2026-07-22"},
    ])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-22", "open": 100.0, "high": 105.0, "low": 100.0, "close": 104.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
        {"stock_id": "^TWII", "date": "2026-07-22", "open": 20000.0, "high": 20100.0, "low": 19900.0, "close": 20050.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 104.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-22T18:00:00"},
    ])

    df, _, _ = load_stock_universe_for_date(conn)

    assert set(df["stock_id"]) == {"2330"}


def test_get_latest_update_time_returns_none_when_no_stocks():
    conn = _fresh_conn()
    assert get_latest_update_time(conn) is None


def test_get_latest_update_time_returns_max_updated_at():
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-24T09:00:00"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-24T10:00:00"},
    ])
    assert get_latest_update_time(conn) == "2026-07-24T10:00:00"


def test_get_latest_candidate_update_time_returns_none_when_no_candidates():
    conn = _fresh_conn()
    assert get_latest_candidate_update_time(conn) is None


def test_get_latest_candidate_update_time_returns_max_created_at():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-21", "stock_id": "2330", "signal_name": "舊訊號", "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-07-21T18:00:00"},
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "新訊號", "entry_price": 104.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-22T18:30:00"},
    ])
    assert get_latest_candidate_update_time(conn) == "2026-07-22T18:30:00"


def test_load_stock_universe_for_date_returns_specific_historical_date_when_given():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-21", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
        {"stock_id": "2330", "date": "2026-07-22", "open": 100.0, "high": 105.0, "low": 100.0, "close": 104.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-21", "stock_id": "2330", "signal_name": "舊訊號", "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-07-21T18:00:00"},
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": "測試", "created_at": "2026-07-22T18:00:00"},
    ])

    df, returned_date, _ = load_stock_universe_for_date(conn, target_date="2026-07-21")

    assert returned_date == "2026-07-21"
    assert len(df) == 1
    assert df.iloc[0]["signal_name"] == "舊訊號"


def test_load_stock_universe_for_date_returns_empty_but_echoes_date_when_no_price_data_that_day():
    """跟舊版(load_candidates_for_date)語意不同：基礎池改成全市場(見上面
    test_load_stock_universe_for_date_includes_stocks_without_any_triggered_rule)，
    「查無資料」現在代表「這天完全沒有任何股票的股價資料」，不是單純「沒有觸發規則」——
    這裡刻意不幫2330補07-23的股價，驗證INNER JOIN stock_prices會讓這天的全市場清單
    正確回傳空DataFrame，日期字串本身仍要回傳。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-22", "open": 100.0, "high": 105.0, "low": 100.0, "close": 104.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-22T18:00:00"},
    ])

    df, returned_date, _ = load_stock_universe_for_date(conn, target_date="2026-07-23")

    assert df.empty
    assert returned_date == "2026-07-23"  # 使用者選的日期本身仍要回傳，不是None


def test_load_stock_universe_for_date_merges_multiple_signals_for_same_stock_into_one_row():
    """同一檔股票同一天同時觸發多條規則時，應該合併成一列顯示，不是一條規則一列
    （這是2026-07-23接上R-SCREEN-11/15後才會出現的情境：同一檔股票可能同時符合
    R-TREND-14跟R-SCREEN-15）。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-23", "open": 100.0, "high": 105.0, "low": 100.0, "close": 104.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
        {"stock_id": "1101", "date": "2026-07-23", "open": 48.0, "high": 51.0, "low": 47.0, "close": 50.0,
         "volume": 2000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-23", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 104.0, "stop_loss": 99.0, "note": "多頭架構＋攻擊量", "created_at": "2026-07-23T18:00:00"},
        {"date": "2026-07-23", "stock_id": "2330", "signal_name": "R-SCREEN-15緩漲軌道突破做多",
         "entry_price": 104.0, "stop_loss": 99.0, "note": "軌道突破＋大量長紅K", "created_at": "2026-07-23T18:00:01"},
        {"date": "2026-07-23", "stock_id": "1101", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 50.0, "stop_loss": 45.0, "note": "多頭架構＋攻擊量", "created_at": "2026-07-23T18:00:02"},
    ])

    df, latest_date, _ = load_stock_universe_for_date(conn)

    assert latest_date == "2026-07-23"
    assert len(df) == 2  # 2330合併成一列，1101單獨一列，總共2列不是3列
    row_2330 = df[df["stock_id"] == "2330"].iloc[0]
    assert row_2330["signal_name"] == "R-TREND-14多頭短線進場\nR-SCREEN-15緩漲軌道突破做多"
    assert row_2330["entry_price"] == 104.0
    assert row_2330["stop_loss"] == 99.0

    row_1101 = df[df["stock_id"] == "1101"].iloc[0]
    assert row_1101["signal_name"] == "R-TREND-14多頭短線進場"  # 只觸發一條規則時，格式維持不變


def test_load_stock_universe_for_date_sorts_by_total_confidence_descending():
    """預設排序改成「這檔股票當天符合的所有規則信心分數加總」由高到低，不是股票代號——
    使用者要優先看到最值得留意的候選股，不是隨機的代號順序。1101觸發2條規則(87+92=179)
    應該排在只觸發1條規則的2330(87)前面，即使股票代號2330數字比較小。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "3008", "name": "大立光", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-23", "open": 100.0, "high": 105.0, "low": 100.0, "close": 104.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
        {"stock_id": "1101", "date": "2026-07-23", "open": 48.0, "high": 51.0, "low": 47.0, "close": 50.0,
         "volume": 2000, "trading_money": None, "trading_turnover": None, "spread": None},
        {"stock_id": "3008", "date": "2026-07-23", "open": 1950.0, "high": 2050.0, "low": 1940.0, "close": 2000.0,
         "volume": 500, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-23", "stock_id": "2330", "signal_name": "R-CLASSIC-24突破大量黑K買進（87%）",
         "entry_price": 104.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-23T18:00:00"},
        {"date": "2026-07-23", "stock_id": "1101", "signal_name": "R-CLASSIC-24突破大量黑K買進（87%）",
         "entry_price": 50.0, "stop_loss": 45.0, "note": None, "created_at": "2026-07-23T18:00:01"},
        {"date": "2026-07-23", "stock_id": "1101", "signal_name": "R-TREND-14多頭短線進場（92%）",
         "entry_price": 50.0, "stop_loss": 45.0, "note": None, "created_at": "2026-07-23T18:00:02"},
        {"date": "2026-07-23", "stock_id": "3008", "signal_name": "R-SCREEN-15緩漲軌道突破做多（88%）",
         "entry_price": 2000.0, "stop_loss": 1900.0, "note": None, "created_at": "2026-07-23T18:00:03"},
    ])

    df, _, _ = load_stock_universe_for_date(conn)

    assert list(df["stock_id"]) == ["1101", "3008", "2330"]  # 179 > 88 > 87


def test_load_stock_universe_for_date_breaks_confidence_ties_by_sar_distance_pct():
    """2026-08-03新增：信心分數加總同分時，改用SAR距離%由大到小當第2順位——1101的
    SAR距離%(+4%)比3008(-5%)大，即使兩者信心分數同樣是87分，1101應該排在前面。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "3008", "name": "大立光", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    upsert_stock_prices(conn, [
        {"stock_id": "1101", "date": "2026-07-23", "open": 48.0, "high": 51.0, "low": 47.0, "close": 50.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
        {"stock_id": "3008", "date": "2026-07-23", "open": 1950.0, "high": 2050.0, "low": 1940.0, "close": 2000.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-23", "stock_id": "1101", "signal_name": "R-CLASSIC-24突破大量黑K買進（87%）",
         "entry_price": 50.0, "stop_loss": 45.0, "note": None, "created_at": "2026-07-23T18:00:00"},
        {"date": "2026-07-23", "stock_id": "3008", "signal_name": "R-SCREEN-15緩漲軌道突破做多（87%）",
         "entry_price": 2000.0, "stop_loss": 1900.0, "note": None, "created_at": "2026-07-23T18:00:01"},
    ])
    upsert_daily_indicators(conn, [
        {"stock_id": "1101", "date": "2026-07-23", "ma5": None, "ma10": None, "ma20": None, "ma60": None,
         "ma120": None, "ma240": None, "sar_value": 52.0, "sar_is_bull": True, "sar_flip_days_ago": 3,
         "updated_at": "2026-07-23T18:00:00"},  # (52-50)/50*100 = +4%
        {"stock_id": "3008", "date": "2026-07-23", "ma5": None, "ma10": None, "ma20": None, "ma60": None,
         "ma120": None, "ma240": None, "sar_value": 1900.0, "sar_is_bull": True, "sar_flip_days_ago": 3,
         "updated_at": "2026-07-23T18:00:00"},  # (1900-2000)/2000*100 = -5%
    ])

    df, _, _ = load_stock_universe_for_date(conn)

    assert list(df["stock_id"]) == ["1101", "3008"]


def test_load_stock_universe_for_date_breaks_remaining_ties_by_volume():
    """信心分數加總跟SAR距離%都同分時，改用成交量由大到小當第3順位——1101成交量
    (5000張)比3008(2000張)大，兩者應該排在前面。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "3008", "name": "大立光", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    upsert_stock_prices(conn, [
        {"stock_id": "1101", "date": "2026-07-23", "open": 48.0, "high": 51.0, "low": 47.0, "close": 50.0,
         "volume": 5_000_000, "trading_money": None, "trading_turnover": None, "spread": None},
        {"stock_id": "3008", "date": "2026-07-23", "open": 1950.0, "high": 2050.0, "low": 1940.0, "close": 2000.0,
         "volume": 2_000_000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-23", "stock_id": "1101", "signal_name": "R-CLASSIC-24突破大量黑K買進（87%）",
         "entry_price": 50.0, "stop_loss": 45.0, "note": None, "created_at": "2026-07-23T18:00:00"},
        {"date": "2026-07-23", "stock_id": "3008", "signal_name": "R-SCREEN-15緩漲軌道突破做多（87%）",
         "entry_price": 2000.0, "stop_loss": 1900.0, "note": None, "created_at": "2026-07-23T18:00:01"},
    ])
    upsert_daily_indicators(conn, [
        {"stock_id": "1101", "date": "2026-07-23", "ma5": None, "ma10": None, "ma20": None, "ma60": None,
         "ma120": None, "ma240": None, "sar_value": 52.0, "sar_is_bull": True, "sar_flip_days_ago": 3,
         "updated_at": "2026-07-23T18:00:00"},  # (52-50)/50*100 = +4%
        {"stock_id": "3008", "date": "2026-07-23", "ma5": None, "ma10": None, "ma20": None, "ma60": None,
         "ma120": None, "ma240": None, "sar_value": 2080.0, "sar_is_bull": True, "sar_flip_days_ago": 3,
         "updated_at": "2026-07-23T18:00:00"},  # (2080-2000)/2000*100 = +4%，跟1101同分
    ])

    df, _, _ = load_stock_universe_for_date(conn)

    assert list(df["stock_id"]) == ["1101", "3008"]


def test_load_stock_universe_for_date_computes_pct_change_and_volume_from_stock_prices():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-21", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "volume": 5000, "trading_money": None, "trading_turnover": None, "spread": None},
        {"stock_id": "2330", "date": "2026-07-22", "open": 100.0, "high": 106.0, "low": 100.0, "close": 105.0,
         "volume": 8000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 105.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-22T18:00:00"},
    ])

    df, _, _ = load_stock_universe_for_date(conn)

    row = df.iloc[0]
    assert row["volume"] == 8000
    assert row["pct_change"] == 5.0  # (105-100)/100*100


def test_load_stock_universe_for_date_pct_change_is_nan_when_no_prior_day_price():
    """新上市或本機資料庫還沒有前一個交易日資料時，漲跌幅算不出來，應該是NaN不是crash或0。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-22", "open": 100.0, "high": 106.0, "low": 100.0, "close": 105.0,
         "volume": 8000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 105.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-22T18:00:00"},
    ])

    df, _, _ = load_stock_universe_for_date(conn)

    assert pd.isna(df.iloc[0]["pct_change"])


def test_load_stock_universe_for_date_reports_intraday_true_when_status_flagged(monkeypatch):
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-24"}])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-24", "open": 100.0, "high": 106.0, "low": 100.0, "close": 105.0,
         "volume": 8000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-24", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 105.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-24T10:00:00"},
    ])
    from src.data.storage import upsert_daily_data_status
    upsert_daily_data_status(conn, "2026-07-24", is_intraday=True)

    _, _, is_intraday = load_stock_universe_for_date(conn, target_date="2026-07-24")

    assert is_intraday is True


def test_load_stock_universe_for_date_reports_intraday_false_when_status_flagged_final():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-23"}])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-23", "open": 100.0, "high": 106.0, "low": 100.0, "close": 105.0,
         "volume": 8000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-23", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 105.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-23T18:00:00"},
    ])
    from src.data.storage import upsert_daily_data_status
    upsert_daily_data_status(conn, "2026-07-23", is_intraday=False)

    _, _, is_intraday = load_stock_universe_for_date(conn, target_date="2026-07-23")

    assert is_intraday is False


def test_get_stock_name_returns_name_when_found():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    assert get_stock_name(conn, "2330") == "台積電"


def test_get_stock_name_returns_none_when_not_found():
    conn = _fresh_conn()
    assert get_stock_name(conn, "9999") is None


def test_list_candidate_dates_returns_dates_descending():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-21", "stock_id": "2330", "signal_name": "A", "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-07-21T18:00:00"},
        {"date": "2026-07-23", "stock_id": "2330", "signal_name": "B", "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-07-23T18:00:00"},
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "C", "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-07-22T18:00:00"},
    ])

    assert list_candidate_dates(conn) == ["2026-07-23", "2026-07-22", "2026-07-21"]


def test_load_price_history_returns_ascending_order_and_respects_limit():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    rows = [
        {"stock_id": "2330", "date": f"2026-07-{d:02d}", "open": 100 + d, "high": 101 + d, "low": 99 + d,
         "close": 100.5 + d, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(1, 6)
    ]
    upsert_stock_prices(conn, rows)

    df = load_price_history(conn, "2330", days=3)
    assert len(df) == 3
    assert list(df.index.strftime("%Y-%m-%d")) == ["2026-07-03", "2026-07-04", "2026-07-05"]  # 依日期遞增排序
    assert df["close"].iloc[-1] == 105.5


def test_load_price_history_returns_empty_for_unknown_stock():
    conn = _fresh_conn()
    df = load_price_history(conn, "9999")
    assert df.empty


def test_resolve_stock_id_matches_exact_stock_id():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    assert resolve_stock_id(conn, "2330") == "2330"


def test_resolve_stock_id_matches_exact_name():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    assert resolve_stock_id(conn, "台積電") == "2330"


def test_resolve_stock_id_matches_partial_name():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    assert resolve_stock_id(conn, "台積") == "2330"


def test_resolve_stock_id_prefers_exact_stock_id_over_name_match():
    """股票代號剛好跟另一檔股票的名稱片段撞在一起時，代號完全相符應該優先。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    assert resolve_stock_id(conn, "2330") == "2330"


def test_resolve_stock_id_returns_none_when_no_match():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    assert resolve_stock_id(conn, "不存在的股票") is None


def test_resolve_stock_id_returns_none_for_blank_query():
    conn = _fresh_conn()
    assert resolve_stock_id(conn, "   ") is None


def test_load_price_history_computes_full_ma_set_with_lookback_buffer():
    """MA5/MA20要在整個顯示範圍(days=10)內都有值，不能因為只抓了10天資料就整條是NaN——
    這代表函式有正確多抓 max(FULL_PERIODS) 天的緩衝資料來算均線，抓完才裁切回10天。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    n_days = 300  # 足夠讓MA240在最後10天視窗內每天都有值
    rows = [
        {"stock_id": "2330", "date": f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}", "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0 + d * 0.1, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(n_days)
    ]
    upsert_stock_prices(conn, rows)

    df = load_price_history(conn, "2330", days=10)

    assert len(df) == 10
    for col in ("MA5", "MA10", "MA20", "MA60", "MA120", "MA240"):
        assert col in df.columns
        assert df[col].notna().all(), f"{col} 在顯示視窗內不應該有NaN(緩衝資料應該足夠)"


def test_load_price_history_includes_macd_and_kd_columns():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    n_days = 60
    rows = [
        {"stock_id": "2330", "date": f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}", "open": 100.0, "high": 101.0 + d * 0.1, "low": 99.0,
         "close": 100.0 + d * 0.1, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(n_days)
    ]
    upsert_stock_prices(conn, rows)

    df = load_price_history(conn, "2330", days=10)

    for col in ("DIF", "MACD", "OSC", "K", "D"):
        assert col in df.columns


def test_compute_ma_bullish_flags_true_when_ma5_gt_ma10_gt_ma20():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    # 持續上漲的收盤價序列，足以讓MA5>MA10>MA20成立(多頭排列)
    rows = [
        {"stock_id": "2330", "date": f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}", "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0 + d * 0.5, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(40)
    ]
    upsert_stock_prices(conn, rows)

    flags = compute_ma_bullish_flags(conn, ["2330"])
    assert flags["2330"] is True


def test_compute_ma_bullish_flags_batched_query_does_not_cross_contaminate_stocks():
    """2026-08-01效能調校：改成一次批次查詢多檔股票(取代逐檔各自查詢一次)，這裡驗證
    分組邏輯正確——2330持續上漲(多頭排列成立)、1101持續下跌(多頭排列不成立)，兩檔
    股票的收盤價資料在同一批次查詢裡不能被混在一起判斷。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    rows = []
    for d in range(40):
        date_str = f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}"
        rows.append({
            "stock_id": "2330", "date": date_str, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0 + d * 0.5, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None,
        })
        rows.append({
            "stock_id": "1101", "date": date_str, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0 - d * 0.5, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None,
        })
    upsert_stock_prices(conn, rows)

    flags = compute_ma_bullish_flags(conn, ["2330", "1101"])

    assert flags["2330"] is True
    assert flags["1101"] is False


def test_compute_ma_bullish_flags_empty_stock_ids_returns_empty_dict():
    conn = _fresh_conn()
    assert compute_ma_bullish_flags(conn, []) == {}


def test_load_ma_bullish_flags_from_table_matches_live_computed_result():
    """查daily_indicators表算出的結果，要跟即時計算版本(compute_ma_bullish_flags)
    在同一份資料上算出的結果一致——這是2026-08-02改版的核心保證：查表只是換一個
    資料來源，不是重新定義均線多排的判斷邏輯。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    rows_2330 = [
        {"stock_id": "2330", "date": f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}", "open": 100.0 + d, "high": 101.0 + d,
         "low": 99.0 + d, "close": 100.0 + d, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(25)
    ]
    rows_1101 = [
        {"stock_id": "1101", "date": f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}", "open": 50.0, "high": 50.5,
         "low": 49.5, "close": 50.0, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(25)
    ]
    upsert_stock_prices(conn, rows_2330 + rows_1101)
    _populate_indicators(conn, "2330", rows_2330)
    _populate_indicators(conn, "1101", rows_1101)
    as_of_date = rows_2330[-1]["date"]

    table_flags = load_ma_bullish_flags_from_table(conn, ["2330", "1101"], periods=(5, 10, 20), as_of_date=as_of_date)
    live_flags = compute_ma_bullish_flags(conn, ["2330", "1101"], periods=(5, 10, 20), as_of_date=as_of_date)

    assert table_flags == live_flags
    assert table_flags["2330"] is True   # 持續上漲，均線多排成立
    assert table_flags["1101"] is False  # 持平走勢，均線糾結在一起不成立


def test_load_ma_bullish_flags_from_table_false_when_no_row_for_that_date():
    """股票沒有回補過、或該日期還沒有對應的daily_indicators列，視為不成立，不拋例外。"""
    conn = _fresh_conn()
    flags = load_ma_bullish_flags_from_table(conn, ["9999"], periods=(5, 10, 20), as_of_date="2026-07-22")
    assert flags == {"9999": False}


def test_load_ma_bullish_flags_from_table_empty_stock_ids_returns_empty_dict():
    conn = _fresh_conn()
    assert load_ma_bullish_flags_from_table(conn, [], periods=(5, 10, 20), as_of_date="2026-07-22") == {}


def test_load_sar_flip_flags_from_table_matches_live_computed_result():
    """跟上面均線的測試同理：查表結果要跟即時計算版本(compute_sar_flip_flags)一致。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    highs = [10.0, 11.0, 12.0, 13.0, 9.0]
    lows = [9.0, 10.0, 10.5, 11.5, 8.0]
    rows = [
        {"stock_id": "2330", "date": f"2026-07-{15 + d:02d}", "open": highs[d], "high": highs[d], "low": lows[d],
         "close": (highs[d] + lows[d]) / 2, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(5)
    ]
    upsert_stock_prices(conn, rows)
    _populate_indicators(conn, "2330", rows)
    as_of_date = rows[-1]["date"]

    table_flags = load_sar_flip_flags_from_table(conn, ["2330"], direction="空頭", within_days=1, as_of_date=as_of_date)
    live_flags = compute_sar_flip_flags(conn, ["2330"], direction="空頭", within_days=1, as_of_date=as_of_date)

    assert table_flags == live_flags == {"2330": True}


def test_load_sar_flip_flags_from_table_false_when_direction_mismatch():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    highs = [10.0, 11.0, 12.0, 13.0, 9.0]
    lows = [9.0, 10.0, 10.5, 11.5, 8.0]
    rows = [
        {"stock_id": "2330", "date": f"2026-07-{15 + d:02d}", "open": highs[d], "high": highs[d], "low": lows[d],
         "close": (highs[d] + lows[d]) / 2, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(5)
    ]
    upsert_stock_prices(conn, rows)
    _populate_indicators(conn, "2330", rows)
    as_of_date = rows[-1]["date"]

    flags = load_sar_flip_flags_from_table(conn, ["2330"], direction="多頭", within_days=1, as_of_date=as_of_date)

    assert flags == {"2330": False}


def test_load_sar_flip_flags_from_table_false_when_no_row_for_that_date():
    conn = _fresh_conn()
    flags = load_sar_flip_flags_from_table(conn, ["9999"], direction="多頭", within_days=1, as_of_date="2026-07-22")
    assert flags == {"9999": False}


def test_load_sar_flip_flags_from_table_empty_stock_ids_returns_empty_dict():
    conn = _fresh_conn()
    assert load_sar_flip_flags_from_table(conn, [], direction="多頭", within_days=1, as_of_date="2026-07-22") == {}


def test_compute_ma_bullish_flags_false_when_not_enough_history():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    rows = [
        {"stock_id": "2330", "date": f"2025-01-{d:02d}", "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(1, 6)  # 只有5天，不夠算MA20
    ]
    upsert_stock_prices(conn, rows)

    flags = compute_ma_bullish_flags(conn, ["2330"])
    assert flags["2330"] is False


def test_compute_ma_bullish_flags_extends_to_ma120_when_periods_given():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    # 持續上漲130天，足以讓MA5>MA10>MA20>MA120成立
    rows = [
        {"stock_id": "2330", "date": f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}", "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0 + d * 0.5, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(130)
    ]
    upsert_stock_prices(conn, rows)

    flags = compute_ma_bullish_flags(conn, ["2330"], periods=(5, 10, 20, 120))
    assert flags["2330"] is True


def test_compute_ma_bullish_flags_extended_periods_false_when_not_enough_history():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    rows = [
        {"stock_id": "2330", "date": f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}", "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0 + d * 0.5, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(40)  # 只有40天，不夠算MA120
    ]
    upsert_stock_prices(conn, rows)

    flags = compute_ma_bullish_flags(conn, ["2330"], periods=(5, 10, 20, 120))
    assert flags["2330"] is False


def test_candidate_filter_ma240_requires_ma120_in_the_chain_too():
    """2026-07-29修正：「...>MA240」篩選條件先前用periods=(5,10,20,240)，會漏檢查MA120，
    導致MA20>MA240成立、但MA120實際上比MA240還低(不是真正的完整多排)時仍誤判為True。
    修正後改用periods=(5,10,20,120,240)，這裡建構一組「MA20>MA240但MA120<MA240」的
    價格數列，驗證修正後的篩選條件正確回傳False，而不是照舊只檢查MA20>MA240。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])

    # 最舊120天收盤價100(拉高MA240但不影響MA120)，中間100天收盤價50(同時拉低MA120/MA240)，
    # 最近20天緩步從100漲到119(讓MA5>MA10>MA20成立)。算出來MA20≈109.5、MA240≈79.96、
    # MA120≈59.92：MA20>MA240成立，但MA120<MA240，代表中段的120天其實比MA240的長期
    # 均值還差，並非真正「短中長期一路遞減」的多排。
    rows = []
    for d in range(240):
        if d < 120:
            close = 100.0
        elif d < 220:
            close = 50.0
        else:
            close = 100.0 + (d - 220)
        rows.append({
            "stock_id": "2330", "date": f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}",
            "open": close, "high": close + 1, "low": close - 1, "close": close,
            "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None,
        })
    upsert_stock_prices(conn, rows)

    old_buggy_flags = compute_ma_bullish_flags(conn, ["2330"], periods=(5, 10, 20, 240))
    assert old_buggy_flags["2330"] is True  # 舊寫法會漏掉MA120，誤判為多排成立

    filter_fn = chart_data.CANDIDATE_FILTERS["均線多頭排列（...>MA240）"]
    fixed_flags = filter_fn(conn, ["2330"], None)
    assert fixed_flags["2330"] is False  # 修正後正確抓出MA120<MA240，不算完整多排


def test_candidate_filters_includes_ma120_and_ma240_extensions():
    assert "均線多頭排列（...>MA120）" in chart_data.CANDIDATE_FILTERS
    assert "均線多頭排列（...>MA240）" in chart_data.CANDIDATE_FILTERS


def test_candidate_filter_defaults_only_checks_ma5_10_20_by_default():
    assert chart_data.CANDIDATE_FILTER_DEFAULTS["均線多頭排列（MA5>MA10>MA20）"] is True
    assert chart_data.CANDIDATE_FILTER_DEFAULTS["均線多頭排列（...>MA120）"] is False
    assert chart_data.CANDIDATE_FILTER_DEFAULTS["均線多頭排列（...>MA240）"] is False


def test_apply_candidate_filters_returns_unfiltered_when_no_active_filters():
    df = pd.DataFrame({"stock_id": ["2330", "1101"]})
    result = apply_candidate_filters(conn=None, candidates_df=df, active_filter_labels=[])
    assert list(result["stock_id"]) == ["2330", "1101"]


def test_apply_candidate_filters_keeps_only_stocks_matching_ma_bullish(monkeypatch):
    df = pd.DataFrame({"stock_id": ["2330", "1101", "2603"]})
    monkeypatch.setitem(
        chart_data.CANDIDATE_FILTERS, "均線多頭排列（MA5>MA10>MA20）",
        lambda conn, stock_ids, as_of_date: {"2330": True, "1101": False, "2603": True},
    )

    result = apply_candidate_filters(conn=None, candidates_df=df, active_filter_labels=["均線多頭排列（MA5>MA10>MA20）"])

    assert list(result["stock_id"]) == ["2330", "2603"]


def test_apply_candidate_filters_zhu_rule_only_keeps_rows_with_a_signal_name():
    """2026-08-02改版：「朱家泓技術分析」勾選框不再對signal_name字串做Rule ID比對(候選
    清單改成全市場基礎池後，那個規則ID正規表示式比對法已經沒有意義)，改成單純檢查
    signal_name是否非空——非空代表這檔股票當天確實出現在daily_candidates(觸發過某條
    朱家泓規則)，None代表是靠均線/SAR全市場掃描補進來、當天沒有觸發任何規則的股票。"""
    df = pd.DataFrame({
        "stock_id": ["2330", "9999"],
        "signal_name": ["R-TREND-14多頭短線進場（92%）", None],
    })

    result = apply_candidate_filters(conn=None, candidates_df=df, active_filter_labels=[], zhu_rule_only=True)

    assert list(result["stock_id"]) == ["2330"]


def test_apply_candidate_filters_unfiltered_when_zhu_rule_only_false():
    df = pd.DataFrame({
        "stock_id": ["2330", "9999"],
        "signal_name": ["R-TREND-14多頭短線進場（92%）", None],
    })

    result = apply_candidate_filters(conn=None, candidates_df=df, active_filter_labels=[], zhu_rule_only=False)

    assert list(result["stock_id"]) == ["2330", "9999"]


def test_apply_candidate_filters_appends_matched_condition_to_signal_name(monkeypatch):
    """2026-08-04改版：使用者反映「訊號」欄位只在signal_name原本是None時才補上符合的
    篩選條件描述，容易誤解成「篩選條件沒生效」(勾了SAR翻轉，但那些剛好也觸發別的規則、
    signal_name本來就非空的股票，訊號欄位完全看不到SAR字樣)——改成不管signal_name
    原本是不是空的，一律把符合的篩選條件描述接在後面(用換行分隔)：2330(原本None)
    直接顯示條件文字；9527(已有真訊號R-TREND-14)則是規則文字後面換行接上條件描述，
    不會因為剛好也觸發別的規則就看不到「為什麼」出現在清單裡。"""
    df = pd.DataFrame({
        "stock_id": ["2330", "9527"],
        "signal_name": [None, "R-TREND-14多頭短線進場（92%）"],
    })
    monkeypatch.setitem(
        chart_data.CANDIDATE_FILTERS, "均線多頭排列（MA5>MA10>MA20）",
        lambda conn, stock_ids, as_of_date: {"2330": True, "9527": True},
    )

    result = apply_candidate_filters(
        conn=None, candidates_df=df, active_filter_labels=["均線多頭排列（MA5>MA10>MA20）"],
    )

    row_2330 = result[result["stock_id"] == "2330"].iloc[0]
    assert row_2330["signal_name"] == "均線多頭排列（MA5>MA10>MA20）"
    row_9527 = result[result["stock_id"] == "9527"].iloc[0]
    assert row_9527["signal_name"] == "R-TREND-14多頭短線進場（92%）\n均線多頭排列（MA5>MA10>MA20）"


def test_apply_candidate_filters_full_market_scan_includes_stocks_without_zhu_signal(monkeypatch):
    """使用者2026-08-02釐清的語意：勾MA5>MA10>MA20+SAR、但不勾朱家泓技術分析，應該等同
    對全市場做「均線多排+SAR翻轉」掃描，不受「當天有沒有觸發朱家泓規則」限制——即使
    stock_id完全沒有出現在daily_candidates(signal_name是None)，只要符合勾選的方法
    條件就要留在結果裡。"""
    df = pd.DataFrame({
        "stock_id": ["2330", "1101"],
        "signal_name": [None, None],  # 兩檔都沒觸發任何朱家泓規則
    })
    monkeypatch.setitem(
        chart_data.CANDIDATE_FILTERS, "均線多頭排列（MA5>MA10>MA20）",
        lambda conn, stock_ids, as_of_date: {"2330": True, "1101": False},
    )

    result = apply_candidate_filters(
        conn=None, candidates_df=df, active_filter_labels=["均線多頭排列（MA5>MA10>MA20）"], zhu_rule_only=False,
    )

    assert list(result["stock_id"]) == ["2330"]


def test_apply_candidate_filters_zhu_rule_only_narrows_full_market_scan_further(monkeypatch):
    """勾MA5>MA10>MA20+朱家泓技術分析：在均線條件的基礎上，再要求當天有出現在
    daily_candidates——兩個2330都符合均線條件，但只有真正有signal_name的那檔會留下來。"""
    df = pd.DataFrame({
        "stock_id": ["2330", "1101"],
        "signal_name": ["R-TREND-14多頭短線進場（92%）", None],
    })
    monkeypatch.setitem(
        chart_data.CANDIDATE_FILTERS, "均線多頭排列（MA5>MA10>MA20）",
        lambda conn, stock_ids, as_of_date: {"2330": True, "1101": True},
    )

    result = apply_candidate_filters(
        conn=None, candidates_df=df, active_filter_labels=["均線多頭排列（MA5>MA10>MA20）"], zhu_rule_only=True,
    )

    assert list(result["stock_id"]) == ["2330"]


def test_twse_tick_size_matches_official_price_tiers():
    """台灣證交所公告的股票升降單位：<10:0.01／10~50:0.05／50~100:0.1／100~500:0.5／
    500~1000:1／>=1000:5，邊界值(10/50/100/500/1000)歸入「較高」那一級。"""
    assert chart_data._twse_tick_size(9.99) == 0.01
    assert chart_data._twse_tick_size(10) == 0.05
    assert chart_data._twse_tick_size(49.99) == 0.05
    assert chart_data._twse_tick_size(50) == 0.1
    assert chart_data._twse_tick_size(99.99) == 0.1
    assert chart_data._twse_tick_size(100) == 0.5
    assert chart_data._twse_tick_size(499.99) == 0.5
    assert chart_data._twse_tick_size(500) == 1
    assert chart_data._twse_tick_size(999.99) == 1
    assert chart_data._twse_tick_size(1000) == 5
    assert chart_data._twse_tick_size(2350) == 5


def test_price_axis_dtick_is_a_multiple_of_the_actual_tick_size():
    """使用者反映價格Y軸每5元一格太少(太粗)，應該依股票實際的升降單位決定格線間距——
    這裡驗證算出來的dtick確實是該股票實際tick size的整數倍(格線都落在真正可能成交的
    價位上)，不是像Plotly預設那樣抓一個跟股票本身無關的間距。"""
    dates = pd.date_range("2026-01-01", periods=5)
    df = pd.DataFrame(
        {"open": [2300] * 5, "high": [2400, 2450, 2500, 2380, 2360], "low": [2200, 2250, 2280, 2300, 2290],
         "close": [2350] * 5, "volume": [1000] * 5},
        index=dates,
    )
    dtick = chart_data._price_axis_dtick(df)
    tick = chart_data._twse_tick_size(float(df["close"].iloc[-1]))
    assert dtick % tick == 0 or round(dtick / tick, 6) == round(dtick / tick)
    assert dtick > 0


def test_build_candlestick_figure_price_yaxis_dtick_reflects_tick_size():
    dates = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame(
        {"open": [2300] * 3, "high": [2400, 2450, 2500], "low": [2200, 2250, 2280],
         "close": [2350] * 3, "volume": [1000] * 3},
        index=dates,
    )

    fig = build_candlestick_figure(df)

    expected_dtick = chart_data._price_axis_dtick(df)
    assert fig.layout.yaxis.dtick == expected_dtick


def test_price_axis_range_matches_high_low_with_padding():
    dates = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame(
        {"open": [150] * 3, "high": [201.0, 190, 195], "low": [122.5, 140, 135], "close": [150] * 3, "volume": [1000] * 3},
        index=dates,
    )

    low, high = chart_data._price_axis_range(df)

    span = 201.0 - 122.5
    assert low == pytest.approx(122.5 - span * 0.05)
    assert high == pytest.approx(201.0 + span * 0.05)


def test_price_axis_range_handles_flat_price_without_crashing():
    """最高最低價剛好相同(例如漲跌停鎖死一整天)時，span=0不能拿來當除數/比例基準，
    要退回用價位本身的比例當padding，不能回傳一個上下限相等的range(Plotly會顯示
    一條沒有高度的軸)。"""
    dates = pd.date_range("2026-01-01", periods=2)
    df = pd.DataFrame({"open": [100] * 2, "high": [100, 100], "low": [100, 100], "close": [100] * 2, "volume": [1000] * 2}, index=dates)

    low, high = chart_data._price_axis_range(df)

    assert low < 100 < high


def test_build_candlestick_figure_price_yaxis_range_not_distorted_by_extreme_trendline():
    """2026-08-04修正「K線圖縮成一小條」bug：使用者回報3231(緯創)的K線被壓縮在畫面
    中間一小段——查證是下降切線/軌道線外推到最新一天時算出負值(-2.6)，遠低於K棒
    實際價格範圍(122.5~201.0)，Plotly預設的Y軸autorange把這個離譜的外推值也算
    進去，才會把K棒本身擠成一小段。這裡直接用同樣的數字重現：K棒價格範圍窄，但
    疊圖的下降切線在圖表最後一天算出遠低於K棒範圍的負值，驗證修正後Y軸range
    只反映K棒本身，不會被切線的極端外推值拉開。
    """
    from src.indicators.trendlines import LinePoint, TrendLine

    dates = pd.date_range("2026-01-01", periods=10)
    df = pd.DataFrame(
        {
            "open": [150.0] * 10, "high": [201.0] + [190.0] * 9, "low": [122.5] + [135.0] * 9,
            "close": [150.0] * 10, "volume": [1000] * 10,
        },
        index=dates,
    )
    # 陡峭下降線：從高點(0, 200)到(1, 150)，斜率-50，外推到x=9時已經跌到-200
    steep_down_line = TrendLine(a=LinePoint(x=0, y=200.0), b=LinePoint(x=1, y=150.0), role="resistance")

    fig = build_candlestick_figure(
        df, trendlines={"down_tangent": steep_down_line}, show_trendline_keys=("down_tangent",),
    )

    yaxis_range = fig.layout.yaxis.range
    # Y軸下限應該貼近K棒實際最低價(122.5)附近，不是被外推到負值的切線拉到-200這種
    # 離譜範圍——用一個寬鬆但足以偵測bug重現的門檻(K棒最低價再往下50元都不合理)。
    assert yaxis_range[0] > 122.5 - 50
    # 切線本身還是要有畫出來(只是Y軸不會為了它而失真)，確認trace確實包含很低的y值
    trendline_trace = next(t for t in fig.data if t.name == "下降切線")
    assert min(trendline_trace.y) < 0


def test_build_candlestick_figure_uses_ohlc_and_is_not_a_line_chart():
    dates = pd.date_range("2026-07-01", periods=3)
    df = pd.DataFrame(
        {"open": [100, 102, 101], "high": [103, 104, 105], "low": [99, 101, 100], "close": [102, 101, 104], "volume": [1000, 1200, 900]},
        index=dates,
    )

    fig = build_candlestick_figure(df, title="2330")

    assert len(fig.data) == 2  # K線 + 成交量子圖，無均線(ma_periods預設空)
    trace = fig.data[0]
    assert trace.type == "candlestick"
    assert list(trace.open) == [100, 102, 101]
    assert list(trace.high) == [103, 104, 105]
    assert list(trace.low) == [99, 101, 100]
    assert list(trace.close) == [102, 101, 104]
    assert list(trace.x) == list(dates)


def test_build_candlestick_figure_adds_volume_subplot_with_up_down_colors():
    dates = pd.date_range("2026-07-01", periods=2)
    df = pd.DataFrame(
        {"open": [100, 102], "high": [103, 104], "low": [99, 101], "close": [102, 101], "volume": [1000, 1200]},
        index=dates,
    )

    fig = build_candlestick_figure(df)

    volume_trace = next(t for t in fig.data if t.type == "bar")
    assert list(volume_trace.y) == [1000, 1200]
    assert list(volume_trace.marker.color) == ["#c0392b", "#27ae60"]  # 第1天收紅、第2天收黑(2026-08-02改綠色)


def test_build_candlestick_figure_adds_selected_ma_lines():
    dates = pd.date_range("2026-07-01", periods=3)
    df = pd.DataFrame(
        {
            "open": [100, 102, 101], "high": [103, 104, 105], "low": [99, 101, 100], "close": [102, 101, 104],
            "volume": [1000, 1200, 900], "MA5": [101, 102, 103], "MA20": [98, 99, 100], "MA60": [95, 96, 97],
        },
        index=dates,
    )

    fig = build_candlestick_figure(df, ma_periods=(5, 20))

    line_traces = {t.name: t for t in fig.data if t.type == "scatter"}
    assert set(line_traces.keys()) == {"MA5", "MA20"}  # MA60沒被選到，不應該出現
    assert list(line_traces["MA5"].y) == [101, 102, 103]
    assert list(line_traces["MA20"].y) == [98, 99, 100]


def test_build_candlestick_figure_draws_selected_trendlines():
    from src.indicators.trendlines import LinePoint, TrendLine

    dates = pd.date_range("2026-01-01", periods=5)
    df = pd.DataFrame(
        {"open": [100] * 5, "high": [105] * 5, "low": [95] * 5, "close": [102] * 5, "volume": [1000] * 5},
        index=dates,
    )
    trendlines = {
        "up_tangent": TrendLine(a=LinePoint(0, 95.0), b=LinePoint(2, 97.0), role="support"),
        "down_tangent": TrendLine(a=LinePoint(0, 105.0), b=LinePoint(2, 103.0), role="resistance"),
    }

    fig = build_candlestick_figure(df, trendlines=trendlines, show_trendline_keys=("up_tangent",))

    line_names = {t.name for t in fig.data if t.type == "scatter"}
    assert line_names == {"上升切線"}  # 只有被選到的up_tangent會被畫出來，down_tangent不會


def test_build_candlestick_figure_ignores_trendline_key_not_in_dict():
    dates = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame(
        {"open": [100] * 3, "high": [105] * 3, "low": [95] * 3, "close": [102] * 3, "volume": [1000] * 3},
        index=dates,
    )

    fig = build_candlestick_figure(df, trendlines={}, show_trendline_keys=("up_tangent",))

    assert not any(t.type == "scatter" for t in fig.data)


def test_build_candlestick_figure_draws_support_resistance_levels_when_enabled():
    dates = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame(
        {"open": [100] * 3, "high": [105] * 3, "low": [95] * 3, "close": [102] * 3, "volume": [1000] * 3},
        index=dates,
    )
    sr_levels = [
        {"price": 90.0, "type": "bottom", "role": "支撐", "date": dates[0]},
        {"price": 110.0, "type": "head", "role": "壓力", "date": dates[1]},
    ]

    fig = build_candlestick_figure(df, sr_levels=sr_levels, show_support_resistance=True)

    line_names = {t.name for t in fig.data if t.type == "scatter"}
    assert "支撐 90.00" in line_names
    assert "壓力 110.00" in line_names


def test_build_candlestick_figure_hides_support_resistance_when_disabled():
    dates = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame(
        {"open": [100] * 3, "high": [105] * 3, "low": [95] * 3, "close": [102] * 3, "volume": [1000] * 3},
        index=dates,
    )
    sr_levels = [{"price": 90.0, "type": "bottom", "role": "支撐", "date": dates[0]}]

    fig = build_candlestick_figure(df, sr_levels=sr_levels, show_support_resistance=False)

    assert not any(t.type == "scatter" for t in fig.data)


def test_build_candlestick_figure_skips_ma_period_missing_from_dataframe():
    """例如資料天數不夠、MA240整條是NaN被join進來但欄位仍存在，或欄位根本不存在，
    都不應該讓畫圖crash——沒有對應欄位的天期直接跳過不畫。"""
    dates = pd.date_range("2026-07-01", periods=2)
    df = pd.DataFrame(
        {"open": [100, 102], "high": [103, 104], "low": [99, 101], "close": [102, 101], "volume": [1000, 1200]},
        index=dates,
    )

    fig = build_candlestick_figure(df, ma_periods=(5, 240))  # df裡沒有MA5/MA240欄位

    line_traces = [t for t in fig.data if t.type == "scatter"]
    assert line_traces == []


def test_build_candlestick_figure_adds_macd_subplot_when_enabled():
    dates = pd.date_range("2026-07-01", periods=3)
    df = pd.DataFrame(
        {
            "open": [100, 102, 101], "high": [103, 104, 105], "low": [99, 101, 100], "close": [102, 101, 104],
            "volume": [1000, 1200, 900], "DIF": [1.0, 1.2, 1.5], "MACD": [0.8, 0.9, 1.0], "OSC": [0.2, 0.3, -0.1],
        },
        index=dates,
    )

    fig = build_candlestick_figure(df, show_macd=True)

    assert fig.layout.yaxis3.title.text == "MACD"
    osc_trace = next(t for t in fig.data if t.name == "OSC")
    assert list(osc_trace.marker.color) == ["#c0392b", "#c0392b", "#27ae60"]  # 正值紅柱、負值綠柱
    dif_trace = next(t for t in fig.data if t.name == "DIF")
    assert list(dif_trace.y) == [1.0, 1.2, 1.5]


def test_build_candlestick_figure_adds_kd_subplot_when_enabled():
    dates = pd.date_range("2026-07-01", periods=3)
    df = pd.DataFrame(
        {
            "open": [100, 102, 101], "high": [103, 104, 105], "low": [99, 101, 100], "close": [102, 101, 104],
            "volume": [1000, 1200, 900], "K": [50.0, 60.0, 70.0], "D": [45.0, 55.0, 65.0],
        },
        index=dates,
    )

    fig = build_candlestick_figure(df, show_kd=True)

    k_trace = next(t for t in fig.data if t.name == "K")
    d_trace = next(t for t in fig.data if t.name == "D")
    assert list(k_trace.y) == [50.0, 60.0, 70.0]
    assert list(d_trace.y) == [45.0, 55.0, 65.0]


def test_build_candlestick_figure_adds_sar_markers_when_enabled():
    dates = pd.date_range("2026-07-01", periods=3)
    df = pd.DataFrame(
        {
            "open": [100, 102, 101], "high": [103, 104, 105], "low": [99, 101, 100], "close": [102, 101, 104],
            "volume": [1000, 1200, 900], "SAR": [98.0, 98.5, 99.0], "SAR_BULL": [True, True, False],
        },
        index=dates,
    )

    fig = build_candlestick_figure(df, show_sar=True)

    sar_trace = next(t for t in fig.data if t.name == "SAR")
    assert sar_trace.mode == "markers"
    assert list(sar_trace.y) == [98.0, 98.5, 99.0]
    # 2026-08-04修正：多頭(偏多/看漲)用紅、空頭(偏空/看跌)用綠，跟K棒本身「漲紅跌綠」
    # 同一套配色慣例(使用者反映原本「多頭綠、空頭紅」不直覺)。
    assert list(sar_trace.marker.color) == ["#c0392b", "#c0392b", "#27ae60"]  # 多頭紅、空頭綠


def test_build_candlestick_figure_omits_sar_trace_when_columns_missing_or_disabled():
    """show_sar=True但df裡沒有SAR/SAR_BULL欄位時(例如舊呼叫端)不應該crash，只是不畫；
    show_sar=False(預設)時即使欄位存在也不畫。"""
    dates = pd.date_range("2026-07-01", periods=2)
    df = pd.DataFrame(
        {"open": [100, 102], "high": [103, 104], "low": [99, 101], "close": [102, 101], "volume": [1000, 1200],
         "SAR": [98.0, 98.5], "SAR_BULL": [True, True]},
        index=dates,
    )

    fig_disabled = build_candlestick_figure(df, show_sar=False)
    assert not any(t.name == "SAR" for t in fig_disabled.data)

    df_no_sar = df.drop(columns=["SAR", "SAR_BULL"])
    fig_missing_cols = build_candlestick_figure(df_no_sar, show_sar=True)
    assert not any(t.name == "SAR" for t in fig_missing_cols.data)


def test_load_price_history_includes_sar_columns():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    rows = [
        {"stock_id": "2330", "date": f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}", "open": 100.0 + d * 0.1,
         "high": 101.0 + d * 0.1, "low": 99.0 + d * 0.1, "close": 100.0 + d * 0.1,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(60)
    ]
    upsert_stock_prices(conn, rows)

    df = load_price_history(conn, "2330", days=30)

    assert "SAR" in df.columns
    assert "SAR_BULL" in df.columns
    assert df["SAR_BULL"].dtype == bool


def test_build_candlestick_figure_omits_macd_kd_traces_when_columns_missing():
    """show_macd/show_kd=True但df裡沒有對應欄位時(例如舊呼叫端)，不應該crash，只是不畫。"""
    dates = pd.date_range("2026-07-01", periods=2)
    df = pd.DataFrame(
        {"open": [100, 102], "high": [103, 104], "low": [99, 101], "close": [102, 101], "volume": [1000, 1200]},
        index=dates,
    )

    fig = build_candlestick_figure(df, show_macd=True, show_kd=True)

    assert not any(t.name in ("OSC", "DIF", "MACD訊號線", "K", "D") for t in fig.data)


def test_build_candlestick_figure_row_count_unchanged_when_macd_kd_disabled():
    dates = pd.date_range("2026-07-01", periods=2)
    df = pd.DataFrame(
        {"open": [100, 102], "high": [103, 104], "low": [99, 101], "close": [102, 101], "volume": [1000, 1200]},
        index=dates,
    )

    fig = build_candlestick_figure(df, show_macd=False, show_kd=False)

    assert len(fig.data) == 2


def test_build_candlestick_figure_macd_kd_add_parameter_and_hover_value_annotations():
    """使用者反映MACD/KD子圖看不出目前用的參數、hover也不會顯示當天數值——修法是右上角
    標示固定參數(MACD(12,26,9)/KD(N=9,D=3))、左上角顯示「最新一天」的數值(desktop版
    另外用JS在hover時動態覆寫成當天數值，見desktop/chart_render.py)。hover-value那則
    annotation要用name標記，讓JS能在不知道annotations清單實際順序的情況下找到它更新。"""
    dates = pd.date_range("2026-07-01", periods=3)
    df = pd.DataFrame(
        {
            "open": [100, 102, 101], "high": [103, 104, 105], "low": [99, 101, 100], "close": [102, 101, 104],
            "volume": [1000, 1200, 900], "DIF": [1.0, 1.2, 1.5], "MACD": [0.8, 0.9, 1.0], "OSC": [0.2, 0.3, -0.1],
            "K": [50.0, 60.0, 70.0], "D": [45.0, 55.0, 65.0],
        },
        index=dates,
    )

    fig = build_candlestick_figure(df, show_macd=True, show_kd=True)

    texts = [a.text for a in fig.layout.annotations]
    assert "MACD(12,26,9)" in texts
    assert "KD(N=9,D=3)" in texts
    macd_value_annotation = next(a for a in fig.layout.annotations if a.name == "macd-hover-value")
    kd_value_annotation = next(a for a in fig.layout.annotations if a.name == "kd-hover-value")
    # 預設(還沒hover)顯示的是「最新一天」(最後一列)的數值
    assert "1.50" in macd_value_annotation.text and "1.00" in macd_value_annotation.text and "-0.10" in macd_value_annotation.text
    assert "70.0" in kd_value_annotation.text and "65.0" in kd_value_annotation.text


def test_build_candlestick_figure_title_is_positioned_to_not_overlap_legend():
    """使用者回報左上角股票代號(title)跟上方legend(均線/切線清單)重疊——修法是title釘在
    最上緣(yanchor="top", y=1)、legend的底部貼齊繪圖區頂部往上長(yanchor="bottom",
    y=1.01)，兩者往不同方向從各自的錨點延伸，才不會疊在一起。"""
    dates = pd.date_range("2026-07-01", periods=3)
    df = pd.DataFrame(
        {"open": [100, 102, 101], "high": [103, 104, 105], "low": [99, 101, 100], "close": [102, 101, 104], "volume": [1000, 1200, 900]},
        index=dates,
    )

    fig = build_candlestick_figure(df, title="2330 台積電")

    assert fig.layout.title.text == "2330 台積電"
    # title的錨點是自己的"top"邊釘在y=1(繪圖區頂端)往下長；legend的錨點是自己的"bottom"邊
    # 釘在y=1.01(繪圖區頂端再往上一點點)往上長——兩者分別往相反方向延伸，才不會疊在一起
    # (不能只比較y數值大小，因為兩者的yanchor語意不同，y數值大不代表視覺位置更高)。
    assert fig.layout.title.yanchor == "top" and fig.layout.title.y == 1
    assert fig.layout.legend.yanchor == "bottom" and fig.layout.legend.y == 1.01


def test_build_candlestick_figure_no_title_when_not_given():
    """桌面版不傳title(改用固定CSS列顯示代號+名稱，見desktop/chart_render.py)，這裡要
    確認預設維持空標題，不會意外印出None或其他字面值。"""
    dates = pd.date_range("2026-07-01", periods=2)
    df = pd.DataFrame(
        {"open": [100, 102], "high": [103, 104], "low": [99, 101], "close": [102, 101], "volume": [1000, 1200]},
        index=dates,
    )

    fig = build_candlestick_figure(df)

    assert not fig.layout.title.text


def test_build_candlestick_figure_sets_weekend_and_holiday_rangebreaks():
    dates = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame(
        {"open": [100, 102, 101], "high": [103, 104, 105], "low": [99, 101, 100], "close": [102, 101, 104], "volume": [1000, 1200, 900]},
        index=dates,
    )

    fig = build_candlestick_figure(df, holidays=["2026-01-01", "2026-02-28"])

    rangebreaks = fig.layout.xaxis.rangebreaks
    assert len(rangebreaks) == 2
    assert rangebreaks[0].bounds == ("sat", "mon")
    assert rangebreaks[1].values == ("2026-01-01", "2026-02-28")


def test_build_candlestick_figure_only_weekend_rangebreak_when_no_holidays():
    dates = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame(
        {"open": [100, 102, 101], "high": [103, 104, 105], "low": [99, 101, 100], "close": [102, 101, 104], "volume": [1000, 1200, 900]},
        index=dates,
    )

    fig = build_candlestick_figure(df, holidays=None)

    assert len(fig.layout.xaxis.rangebreaks) == 1


def test_load_holidays_for_chart_returns_empty_list_for_empty_df():
    holidays, ok = load_holidays_for_chart(pd.DataFrame())
    assert holidays == []
    assert ok is True


def test_load_holidays_for_chart_returns_holidays_when_fetch_succeeds(monkeypatch):
    dates = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame({"close": [1, 2, 3]}, index=dates)
    monkeypatch.setattr(chart_data.trading_calendar, "holidays_between", lambda start, end: ["2026-01-01"])

    holidays, ok = load_holidays_for_chart(df)
    assert holidays == ["2026-01-01"]
    assert ok is True


def test_load_holidays_for_chart_fails_gracefully_when_fetch_raises(monkeypatch):
    """TWSE假日曆這個端點暫時打不通時，圖表仍應該畫得出來(只是可能有假日空白)，
    不應該讓整個頁面crash。"""
    dates = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame({"open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3], "close": [1, 2, 3], "volume": [100, 200, 300]}, index=dates)

    def _raise(*args, **kwargs):
        raise RuntimeError("模擬TWSE暫時打不通")

    monkeypatch.setattr(chart_data.trading_calendar, "holidays_between", _raise)

    holidays, ok = load_holidays_for_chart(df)
    assert holidays == []
    assert ok is False

    # 即使假日抓取失敗，圖表本身仍應該正常產生，不crash
    fig = build_candlestick_figure(df, holidays=holidays)
    assert len(fig.layout.xaxis.rangebreaks) == 1


def test_load_holidays_for_chart_includes_weekday_gaps_not_in_official_calendar(monkeypatch):
    """2026-08-03新增：官方假日曆抓不到的臨時休市日(例如颱風假)，只要df裡對應的
    平日缺資料，也要納入休市清單——不然rangebreaks不會壓縮這天，圖上會留下斷點。
    這裡模擬2026-07-10(週五)這種官方假日曆完全沒有、但df裡確實缺資料的情境(查證
    真實案例：TWSE官方端點對2026-07-10回傳0筆資料，證實是真正的休市日，但不在
    trading_calendar.holidays_between()抓到的年度假日曆裡)。
    """
    dates = pd.to_datetime(["2026-07-08", "2026-07-09", "2026-07-13", "2026-07-14"])  # 07-10(五)缺資料
    df = pd.DataFrame({"close": [1, 2, 3, 4]}, index=dates)
    monkeypatch.setattr(chart_data.trading_calendar, "holidays_between", lambda start, end: [])

    holidays, ok = load_holidays_for_chart(df)

    assert "2026-07-10" in holidays
    assert ok is True


def test_load_holidays_for_chart_merges_official_and_implied_holidays(monkeypatch):
    """官方假日曆抓到的假日、跟資料缺口反推出的假日要合併，不是互相取代。"""
    dates = pd.to_datetime(["2026-01-01", "2026-01-05"])  # 01-02(五)~01-04(日)缺資料
    df = pd.DataFrame({"close": [1, 2]}, index=dates)
    monkeypatch.setattr(chart_data.trading_calendar, "holidays_between", lambda start, end: ["2026-06-19"])

    holidays, ok = load_holidays_for_chart(df)

    assert "2026-06-19" in holidays  # 官方假日曆抓到的
    assert "2026-01-02" in holidays  # 資料缺口反推的(週五)
    assert ok is True


def test_load_holidays_for_chart_falls_back_to_implied_holidays_only_when_fetch_fails(monkeypatch):
    """官方假日曆抓取失敗時，至少還有資料缺口反推出的假日可用，不是完全空清單。"""
    dates = pd.to_datetime(["2026-07-08", "2026-07-09", "2026-07-13"])  # 07-10(五)缺資料
    df = pd.DataFrame({"close": [1, 2, 3]}, index=dates)

    def _raise(*args, **kwargs):
        raise RuntimeError("模擬TWSE暫時打不通")

    monkeypatch.setattr(chart_data.trading_calendar, "holidays_between", _raise)

    holidays, ok = load_holidays_for_chart(df)

    assert holidays == ["2026-07-10"]
    assert ok is False


def test_compute_sar_flip_flags_false_when_not_enough_history():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_stock_prices(conn, [
        {"stock_id": "2330", "date": "2026-07-21", "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])

    flags = compute_sar_flip_flags(conn, ["2330"], direction="多頭", within_days=1)
    assert flags["2330"] is False


def test_compute_sar_flip_flags_detects_bearish_flip_on_sharp_drop():
    """持續走高多天後最後一天暴跌，SAR應在最後一天翻轉為空頭(見src/indicators/parabolic_sar.py
    的手動追算案例)。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    highs = [10.0, 11.0, 12.0, 13.0, 9.0]
    lows = [9.0, 10.0, 10.5, 11.5, 8.0]
    rows = [
        {"stock_id": "2330", "date": f"2026-07-{15 + d:02d}", "open": highs[d], "high": highs[d], "low": lows[d],
         "close": (highs[d] + lows[d]) / 2, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(5)
    ]
    upsert_stock_prices(conn, rows)

    flags_bear = compute_sar_flip_flags(conn, ["2330"], direction="空頭", within_days=1)
    assert flags_bear["2330"] is True

    flags_bull = compute_sar_flip_flags(conn, ["2330"], direction="多頭", within_days=1)
    assert flags_bull["2330"] is False


def test_compute_sar_flip_flags_batched_query_does_not_cross_contaminate_stocks():
    """2026-08-01效能調校：改成一次批次查詢多檔股票，這裡驗證分組邏輯正確——2330最後
    一天暴跌(應翻轉為空頭)、1101維持平穩走勢(不應該翻轉)，兩檔股票的high/low/close
    資料在同一批次查詢裡不能被混在一起算。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    highs = [10.0, 11.0, 12.0, 13.0, 9.0]
    lows = [9.0, 10.0, 10.5, 11.5, 8.0]
    # 1101用穩定上漲的走勢(不會翻轉為空頭)，跟2330(最後一天暴跌)明顯對比——刻意不用
    # 完全持平的價格序列，那種簡併資料本身就可能觸發SAR演算法初始種子的邊界情況，
    # 不是真正驗證「批次查詢有沒有分組正確」這件事所需要的。
    rows = []
    for d in range(5):
        date_str = f"2026-07-{15 + d:02d}"
        rows.append({
            "stock_id": "2330", "date": date_str, "open": highs[d], "high": highs[d], "low": lows[d],
            "close": (highs[d] + lows[d]) / 2, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None,
        })
        rows.append({
            "stock_id": "1101", "date": date_str, "open": 50.0 + d, "high": 51.0 + d, "low": 49.0 + d,
            "close": 50.0 + d, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None,
        })
    upsert_stock_prices(conn, rows)

    flags = compute_sar_flip_flags(conn, ["2330", "1101"], direction="空頭", within_days=1)

    assert flags["2330"] is True
    assert flags["1101"] is False


def test_compute_sar_flip_flags_empty_stock_ids_returns_empty_dict():
    conn = _fresh_conn()
    assert compute_sar_flip_flags(conn, []) == {}


def test_compute_sar_flip_flags_as_of_date_ignores_rows_after_that_date():
    """2026-08-01發現的bug：候選清單可以瀏覽「過去某一天」，但SAR/均線篩選條件原本不管
    as_of_date、永遠用DB目前最新的資料算——SAR是路徑相關指標，多算了「之後才發生」的
    交易日會讓翻轉判斷的日期往後推移。這裡建構2330在07-19暴跌翻轉為空頭、之後07-20/
    07-21延續下跌(不會再翻轉)的走勢：以07-19為準(as_of_date)應該判定「1天內翻轉」為
    True；不傳as_of_date(退回用DB目前最新的07-21)時，翻轉其實發生在3天前，「1天內
    翻轉」應該是False——證明沒設定as_of_date會把候選清單瀏覽中的歷史日期誤判成DB目前
    最新日期。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    highs = [10.0, 11.0, 12.0, 13.0, 9.0, 8.5, 8.0]
    lows = [9.0, 10.0, 10.5, 11.5, 8.0, 7.5, 7.0]
    rows = [
        {"stock_id": "2330", "date": f"2026-07-{15 + d:02d}", "open": highs[d], "high": highs[d], "low": lows[d],
         "close": (highs[d] + lows[d]) / 2, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(7)
    ]
    upsert_stock_prices(conn, rows)

    flags_as_of_flip_date = compute_sar_flip_flags(
        conn, ["2330"], direction="空頭", within_days=1, as_of_date="2026-07-19"
    )
    assert flags_as_of_flip_date["2330"] is True

    flags_using_latest_db_data = compute_sar_flip_flags(conn, ["2330"], direction="空頭", within_days=1)
    assert flags_using_latest_db_data["2330"] is False


def test_apply_candidate_filters_as_of_date_scopes_sar_and_ma_to_historical_candidate_date():
    """2026-08-02改版：SAR/均線篩選改成查daily_indicators表(見load_sar_flip_flags_
    from_table())，「as_of_date」現在直接對應要查表的日期，不再是「用date<=as_of_date
    篩stock_prices回看窗口」——這裡驗證apply_candidate_filters()確實把as_of_date傳給
    查表函式，讀到的是那一天的列，不是候選清單基礎池裡剛好也在candidates_df的其他日期。
    價格資料在07-19暴跌翻轉為空頭，之後07-20/07-21延續下跌(不會再翻轉)：以07-19為準
    「1天內翻轉」應該成立，以07-21(最新一天)為準則已經是3天前翻轉的事，不成立。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    highs = [10.0, 11.0, 12.0, 13.0, 9.0, 8.5, 8.0]
    lows = [9.0, 10.0, 10.5, 11.5, 8.0, 7.5, 7.0]
    rows = [
        {"stock_id": "2330", "date": f"2026-07-{15 + d:02d}", "open": highs[d], "high": highs[d], "low": lows[d],
         "close": (highs[d] + lows[d]) / 2, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(7)
    ]
    upsert_stock_prices(conn, rows)
    _populate_indicators(conn, "2330", rows)
    df = pd.DataFrame({"stock_id": ["2330"], "signal_name": ["R-TREND-14多頭短線進場（92%）"]})

    result_as_of_flip_date = apply_candidate_filters(
        conn, df, [], sar_flip_option={"direction": "空頭", "within_days": 1}, as_of_date="2026-07-19",
    )
    assert list(result_as_of_flip_date["stock_id"]) == ["2330"]

    result_as_of_later_date = apply_candidate_filters(
        conn, df, [], sar_flip_option={"direction": "空頭", "within_days": 1}, as_of_date="2026-07-21",
    )
    assert list(result_as_of_later_date["stock_id"]) == []


def test_fetch_recent_columns_batched_as_of_date_excludes_later_rows():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    rows = [
        {"stock_id": "2330", "date": f"2026-07-{15 + d:02d}", "open": 10.0 + d, "high": 10.0 + d,
         "low": 10.0 + d, "close": 10.0 + d, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None}
        for d in range(5)
    ]
    upsert_stock_prices(conn, rows)

    result = chart_data._fetch_recent_columns_batched(conn, ["2330"], ["close"], lookback_days=10, as_of_date="2026-07-17")

    assert result["2330"]["close"] == [10.0, 11.0, 12.0]  # 只到07-17，07-18/07-19被排除


def test_apply_candidate_filters_sar_flip_option_filters_by_direction(monkeypatch):
    df = pd.DataFrame({"stock_id": ["2330", "1101", "2603"]})
    monkeypatch.setattr(
        chart_data, "load_sar_flip_flags_from_table",
        lambda conn, stock_ids, direction, within_days, as_of_date=None: {"2330": True, "1101": False, "2603": True},
    )

    result = apply_candidate_filters(
        conn=None, candidates_df=df, active_filter_labels=[],
        sar_flip_option={"direction": "多頭", "within_days": 1},
    )

    assert list(result["stock_id"]) == ["2330", "2603"]


def test_apply_candidate_filters_returns_unfiltered_when_sar_flip_option_is_none():
    df = pd.DataFrame({"stock_id": ["2330", "1101"]})
    result = apply_candidate_filters(conn=None, candidates_df=df, active_filter_labels=[], sar_flip_option=None)
    assert list(result["stock_id"]) == ["2330", "1101"]
