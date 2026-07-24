import pandas as pd

import src.presentation.chart_data as chart_data
from src.data.storage import init_db, upsert_daily_candidates, upsert_stock_prices, upsert_stocks
from src.presentation.chart_data import (
    apply_candidate_filters,
    build_candlestick_figure,
    compute_ma_bullish_flags,
    list_candidate_dates,
    load_candidates_for_date,
    load_holidays_for_chart,
    load_price_history,
)


def _fresh_conn():
    return init_db(":memory:")


def test_load_candidates_for_date_returns_empty_when_no_records():
    conn = _fresh_conn()
    df, latest_date = load_candidates_for_date(conn)
    assert df.empty
    assert latest_date is None


def test_load_candidates_for_date_defaults_to_most_recent_date():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-21", "stock_id": "2330", "signal_name": "舊訊號", "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-07-21T18:00:00"},
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": "測試", "created_at": "2026-07-22T18:00:00"},
    ])

    df, latest_date = load_candidates_for_date(conn)
    assert latest_date == "2026-07-22"
    assert len(df) == 1
    assert df.iloc[0]["stock_id"] == "2330"
    assert df.iloc[0]["name"] == "台積電"
    assert df.iloc[0]["signal_name"] == "R-TREND-14多頭短線進場"


def test_load_candidates_for_date_returns_specific_historical_date_when_given():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-21", "stock_id": "2330", "signal_name": "舊訊號", "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-07-21T18:00:00"},
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": "測試", "created_at": "2026-07-22T18:00:00"},
    ])

    df, returned_date = load_candidates_for_date(conn, target_date="2026-07-21")

    assert returned_date == "2026-07-21"
    assert len(df) == 1
    assert df.iloc[0]["signal_name"] == "舊訊號"


def test_load_candidates_for_date_returns_empty_but_echoes_date_when_no_candidates_that_day():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-22T18:00:00"},
    ])

    df, returned_date = load_candidates_for_date(conn, target_date="2026-07-23")

    assert df.empty
    assert returned_date == "2026-07-23"  # 使用者選的日期本身仍要回傳，不是None


def test_load_candidates_for_date_merges_multiple_signals_for_same_stock_into_one_row():
    """同一檔股票同一天同時觸發多條規則時，應該合併成一列顯示，不是一條規則一列
    （這是2026-07-23接上R-SCREEN-11/15後才會出現的情境：同一檔股票可能同時符合
    R-TREND-14跟R-SCREEN-15）。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-23", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 104.0, "stop_loss": 99.0, "note": "多頭架構＋攻擊量", "created_at": "2026-07-23T18:00:00"},
        {"date": "2026-07-23", "stock_id": "2330", "signal_name": "R-SCREEN-15緩漲軌道突破做多",
         "entry_price": 104.0, "stop_loss": 99.0, "note": "軌道突破＋大量長紅K", "created_at": "2026-07-23T18:00:01"},
        {"date": "2026-07-23", "stock_id": "1101", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 50.0, "stop_loss": 45.0, "note": "多頭架構＋攻擊量", "created_at": "2026-07-23T18:00:02"},
    ])

    df, latest_date = load_candidates_for_date(conn)

    assert latest_date == "2026-07-23"
    assert len(df) == 2  # 2330合併成一列，1101單獨一列，總共2列不是3列
    row_2330 = df[df["stock_id"] == "2330"].iloc[0]
    assert row_2330["signal_name"] == "R-TREND-14多頭短線進場\nR-SCREEN-15緩漲軌道突破做多"
    assert row_2330["entry_price"] == 104.0
    assert row_2330["stop_loss"] == 99.0

    row_1101 = df[df["stock_id"] == "1101"].iloc[0]
    assert row_1101["signal_name"] == "R-TREND-14多頭短線進場"  # 只觸發一條規則時，格式維持不變


def test_load_candidates_for_date_computes_pct_change_and_volume_from_stock_prices():
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

    df, _ = load_candidates_for_date(conn)

    row = df.iloc[0]
    assert row["volume"] == 8000
    assert row["pct_change"] == 5.0  # (105-100)/100*100


def test_load_candidates_for_date_pct_change_is_nan_when_no_prior_day_price():
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

    df, _ = load_candidates_for_date(conn)

    assert pd.isna(df.iloc[0]["pct_change"])


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


def test_apply_candidate_filters_returns_unfiltered_when_no_active_filters():
    df = pd.DataFrame({"stock_id": ["2330", "1101"]})
    result = apply_candidate_filters(conn=None, candidates_df=df, active_filter_labels=[])
    assert list(result["stock_id"]) == ["2330", "1101"]


def test_apply_candidate_filters_keeps_only_stocks_matching_ma_bullish(monkeypatch):
    df = pd.DataFrame({"stock_id": ["2330", "1101", "2603"]})
    monkeypatch.setitem(
        chart_data.CANDIDATE_FILTERS, "均線多頭排列（MA5>MA10>MA20）",
        lambda conn, stock_ids: {"2330": True, "1101": False, "2603": True},
    )

    result = apply_candidate_filters(conn=None, candidates_df=df, active_filter_labels=["均線多頭排列（MA5>MA10>MA20）"])

    assert list(result["stock_id"]) == ["2330", "2603"]


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
    assert list(volume_trace.marker.color) == ["#c0392b", "#1a1a1a"]  # 第1天收紅、第2天收黑


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
