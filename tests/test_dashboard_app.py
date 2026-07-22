import pandas as pd

import dashboard.app as dashboard_app
from dashboard.app import build_candlestick_figure, load_holidays_for_chart, load_latest_candidates, load_price_history
from src.data.storage import init_db, upsert_daily_candidates, upsert_stock_prices, upsert_stocks


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


def test_build_candlestick_figure_uses_ohlc_and_is_not_a_line_chart():
    dates = pd.date_range("2026-07-01", periods=3)
    df = pd.DataFrame(
        {"open": [100, 102, 101], "high": [103, 104, 105], "low": [99, 101, 100], "close": [102, 101, 104], "volume": [1000, 1200, 900]},
        index=dates,
    )

    fig = build_candlestick_figure(df, title="2330")

    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.type == "candlestick"
    assert list(trace.open) == [100, 102, 101]
    assert list(trace.high) == [103, 104, 105]
    assert list(trace.low) == [99, 101, 100]
    assert list(trace.close) == [102, 101, 104]
    assert list(trace.x) == list(dates)


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
    monkeypatch.setattr(dashboard_app.trading_calendar, "holidays_between", lambda start, end: ["2026-01-01"])

    holidays, ok = load_holidays_for_chart(df)
    assert holidays == ["2026-01-01"]
    assert ok is True


def test_load_holidays_for_chart_fails_gracefully_when_fetch_raises(monkeypatch):
    """TWSE假日曆這個端點暫時打不通時，圖表仍應該畫得出來(只是可能有假日空白)，
    不應該讓整個頁面crash。"""
    dates = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame({"open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3], "close": [1, 2, 3]}, index=dates)

    def _raise(*args, **kwargs):
        raise RuntimeError("模擬TWSE暫時打不通")

    monkeypatch.setattr(dashboard_app.trading_calendar, "holidays_between", _raise)

    holidays, ok = load_holidays_for_chart(df)
    assert holidays == []
    assert ok is False

    # 即使假日抓取失敗，圖表本身仍應該正常產生，不crash
    fig = build_candlestick_figure(df, holidays=holidays)
    assert len(fig.layout.xaxis.rangebreaks) == 1
