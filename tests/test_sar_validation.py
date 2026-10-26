import sqlite3

import pandas as pd
import pytest

from src.backtest.sar_portfolio import (
    SarStrategyParams,
    load_sar_market_data_with_recomputed_indicators,
)
from src.backtest.sar_validation import run_sar_historical_validation


def _market_row(date, stock_id="2330", **overrides):
    row = {
        "date": date,
        "stock_id": stock_id,
        "name": f"股票{stock_id}",
        "open": 49.0,
        "high": 50.0,
        "low": 48.0,
        "close": 50.0,
        "volume": 1_000_000,
        "ma5": 51.0,
        "ma10": 50.5,
        "ma20": 50.2,
        "ma60": 47.0,
        "ma120": 46.0,
        "ma200": 45.0,
        "sar_value": 49.0,
        "sar_is_bull": 1,
        "sar_flip_days_ago": 1,
    }
    row.update(overrides)
    return row


def test_validation_runs_reference_and_holdout_as_separate_portfolios():
    dates = pd.bdate_range("2026-01-05", periods=8).strftime("%Y-%m-%d").tolist()
    data = pd.DataFrame([_market_row(date) for date in dates])

    result = run_sar_historical_validation(
        data,
        dates[0],
        dates[3],
        dates[4],
        dates[7],
        100_000,
        SarStrategyParams(),
    )

    assert result.reference.start_date == dates[0]
    assert result.reference.label == "前段期間"
    assert result.reference.end_date == dates[3]
    assert result.holdout.start_date == dates[4]
    assert result.holdout.label == "後段期間"
    assert result.holdout.end_date == dates[7]
    assert result.reference.trading_days == 4
    assert result.holdout.trading_days == 4
    assert result.reference.signal_count > 0
    assert result.holdout.cash_benchmark_return_pct == 0
    assert result.holdout.total_return_pct < 0
    assert len(result.holdout_equity_curve) == 4
    assert result.holdout_equity_curve[0].total_assets == 100_000


def test_validation_rejects_overlapping_periods():
    dates = pd.bdate_range("2026-01-05", periods=5).strftime("%Y-%m-%d").tolist()
    data = pd.DataFrame([_market_row(date) for date in dates])

    with pytest.raises(ValueError, match="不可重疊"):
        run_sar_historical_validation(
            data,
            dates[0],
            dates[2],
            dates[2],
            dates[4],
            100_000,
            SarStrategyParams(),
        )


def test_validation_rejects_period_without_market_dates():
    dates = pd.bdate_range("2026-01-05", periods=4).strftime("%Y-%m-%d").tolist()
    data = pd.DataFrame([_market_row(date) for date in dates[:2]])

    with pytest.raises(ValueError, match="沒有可用交易日"):
        run_sar_historical_validation(
            data,
            dates[0],
            dates[1],
            dates[2],
            dates[3],
            100_000,
            SarStrategyParams(),
        )


def test_validation_recomputes_indicator_history_without_writing_market_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stocks (stock_id TEXT PRIMARY KEY, name TEXT, market TEXT)")
    conn.execute(
        """
        CREATE TABLE stock_prices (
            stock_id TEXT, date TEXT, open REAL, high REAL, low REAL,
            close REAL, volume INTEGER, PRIMARY KEY (stock_id, date)
        )
        """
    )
    conn.execute("CREATE TABLE daily_indicators (stock_id TEXT, date TEXT)")
    conn.execute("INSERT INTO daily_indicators VALUES ('9999', '2020-01-01')")
    conn.execute("INSERT INTO stocks VALUES ('2330', '台積電', 'TWSE')")
    dates = pd.bdate_range("2025-01-01", periods=205)
    rows = []
    for index, date in enumerate(dates):
        close = 20 + index * 0.05
        rows.append((
            "2330",
            date.strftime("%Y-%m-%d"),
            close - 0.2,
            close + 1,
            close - 1,
            close,
            1_000_000,
        ))
    conn.executemany("INSERT INTO stock_prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    market_data = load_sar_market_data_with_recomputed_indicators(
        conn,
        dates[-1].strftime("%Y-%m-%d"),
    )

    assert len(market_data) == 205
    assert pd.isna(market_data.iloc[198]["ma200"])
    assert market_data.iloc[199]["ma200"] == pytest.approx(
        market_data.iloc[0:200]["close"].mean(),
    )
    assert pd.notna(market_data.iloc[-1]["sar_value"])
    assert conn.execute(
        "SELECT COUNT(*) FROM daily_indicators"
    ).fetchone()[0] == 1
    conn.close()
