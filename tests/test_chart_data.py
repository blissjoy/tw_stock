import pandas as pd

import src.presentation.chart_data as chart_data
from src.data.storage import init_db, upsert_daily_candidates, upsert_stock_prices, upsert_stocks
from src.presentation.chart_data import build_candlestick_figure, load_holidays_for_chart, load_latest_candidates, load_price_history


def _fresh_conn():
    return init_db(":memory:")


def test_load_latest_candidates_returns_empty_when_no_records():
    conn = _fresh_conn()
    df, latest_date = load_latest_candidates(conn)
    assert df.empty
    assert latest_date is None


def test_load_latest_candidates_returns_only_the_most_recent_date():
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-21", "stock_id": "2330", "signal_name": "舊訊號", "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-07-21T18:00:00"},
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": "測試", "created_at": "2026-07-22T18:00:00"},
    ])

    df, latest_date = load_latest_candidates(conn)
    assert latest_date == "2026-07-22"
    assert len(df) == 1
    assert df.iloc[0]["stock_id"] == "2330"
    assert df.iloc[0]["name"] == "台積電"
    assert df.iloc[0]["signal_name"] == "R-TREND-14多頭短線進場"


def test_load_latest_candidates_merges_multiple_signals_for_same_stock_into_one_row():
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

    df, latest_date = load_latest_candidates(conn)

    assert latest_date == "2026-07-23"
    assert len(df) == 2  # 2330合併成一列，1101單獨一列，總共2列不是3列
    row_2330 = df[df["stock_id"] == "2330"].iloc[0]
    assert row_2330["signal_name"] == "R-TREND-14多頭短線進場、R-SCREEN-15緩漲軌道突破做多"
    assert "R-TREND-14多頭短線進場：多頭架構＋攻擊量" in row_2330["note"]
    assert "R-SCREEN-15緩漲軌道突破做多：軌道突破＋大量長紅K" in row_2330["note"]
    assert row_2330["entry_price"] == 104.0
    assert row_2330["stop_loss"] == 99.0

    row_1101 = df[df["stock_id"] == "1101"].iloc[0]
    assert row_1101["signal_name"] == "R-TREND-14多頭短線進場"  # 只觸發一條規則時，格式維持不變


def test_load_latest_candidates_skips_null_note_when_merging():
    """合併note時，若其中一條規則的note是None，不應該把"None"字樣混進合併結果裡。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-23", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 104.0, "stop_loss": 99.0, "note": None, "created_at": "2026-07-23T18:00:00"},
        {"date": "2026-07-23", "stock_id": "2330", "signal_name": "R-SCREEN-15緩漲軌道突破做多",
         "entry_price": 104.0, "stop_loss": 99.0, "note": "軌道突破", "created_at": "2026-07-23T18:00:01"},
    ])

    df, _ = load_latest_candidates(conn)

    assert df.iloc[0]["note"] == "R-SCREEN-15緩漲軌道突破做多：軌道突破"


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
