import pandas as pd

import src.screener.daily_screener as daily_screener
from src.data.storage import init_db, upsert_stock_prices, upsert_stocks


def _build_uptrend_df(n_days: int = 70) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    close = [100 + i * 0.3 for i in range(n_days)]
    close[-1] = close[-2] * 1.03  # 最後一天跳漲3%，確保紅K實體漲幅>2%
    open_ = [c - 0.2 for c in close]
    open_[-1] = close[-2]  # 最後一天開盤=前一天收盤，讓漲幅完全反映在close-open
    high = [c + 0.5 for c in close]
    low = [c - 0.5 for c in close]
    volume = [1000] * n_days
    volume[-1] = 1500  # 前一日量的1.5倍 >= 1.3倍攻擊量門檻
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def test_screen_bull_short_term_entry_returns_none_when_not_enough_days():
    df = _build_uptrend_df(n_days=30)
    assert daily_screener.screen_bull_short_term_entry(df, min_days=60) is None


def test_screen_bull_short_term_entry_fires_when_conditions_met(monkeypatch):
    df = _build_uptrend_df(n_days=70)
    monkeypatch.setattr(
        daily_screener, "daily_bull_trend_state",
        lambda high, low, close, n=5: pd.Series(True, index=close.index),
    )

    result = daily_screener.screen_bull_short_term_entry(df, min_days=60)
    assert result is not None
    assert result["signal_name"] == "R-TREND-14多頭短線進場"
    assert result["entry_price"] == df["close"].iloc[-1]
    assert result["stop_loss"] < result["entry_price"]


def test_screen_bull_short_term_entry_returns_none_when_not_bull_trend(monkeypatch):
    df = _build_uptrend_df(n_days=70)
    monkeypatch.setattr(
        daily_screener, "daily_bull_trend_state",
        lambda high, low, close, n=5: pd.Series(False, index=close.index),
    )
    assert daily_screener.screen_bull_short_term_entry(df, min_days=60) is None


def test_screen_all_stocks_aggregates_multiple_candidates(monkeypatch):
    monkeypatch.setattr(
        daily_screener, "daily_bull_trend_state",
        lambda high, low, close, n=5: pd.Series(True, index=close.index),
    )
    df_ok = _build_uptrend_df(70)
    df_short = _build_uptrend_df(30)
    candidates = daily_screener.screen_all_stocks({"2330": df_ok, "1101": df_short}, min_days=60)
    assert len(candidates) == 1
    assert candidates[0]["stock_id"] == "2330"


def _seed_stock_prices(conn, stock_id: str, n_days: int) -> None:
    upsert_stocks(conn, [{"stock_id": stock_id, "name": stock_id, "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    rows = [
        {
            "stock_id": stock_id, "date": f"2026-{(1 + d // 28):02d}-{(1 + d % 28):02d}",
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000,
            "trading_money": None, "trading_turnover": None, "spread": None,
        }
        for d in range(n_days)
    ]
    upsert_stock_prices(conn, rows)


def test_load_trailing_frames_only_includes_stocks_with_enough_days():
    conn = init_db(":memory:")
    _seed_stock_prices(conn, "2330", n_days=70)
    _seed_stock_prices(conn, "1101", n_days=30)

    frames = daily_screener.load_trailing_frames(conn, min_days=60)

    assert set(frames.keys()) == {"2330"}
    assert len(frames["2330"]) == 70
    assert list(frames["2330"].columns) == ["open", "high", "low", "close", "volume"]


def test_run_screen_and_store_writes_candidates_and_returns_them(monkeypatch):
    conn = init_db(":memory:")
    _seed_stock_prices(conn, "2330", n_days=70)
    fake_candidate = {
        "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
        "entry_price": 104.0, "stop_loss": 99.0, "note": "測試",
    }
    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [fake_candidate])

    candidates = daily_screener.run_screen_and_store(conn, iso_date="2026-07-22", min_days=60)

    assert candidates == [fake_candidate]
    row = conn.execute("SELECT stock_id, signal_name FROM daily_candidates WHERE date = '2026-07-22'").fetchone()
    assert row == ("2330", "R-TREND-14多頭短線進場")


def test_run_screen_and_store_writes_nothing_when_no_candidates(monkeypatch):
    conn = init_db(":memory:")
    _seed_stock_prices(conn, "2330", n_days=70)
    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [])

    candidates = daily_screener.run_screen_and_store(conn, iso_date="2026-07-22", min_days=60)

    assert candidates == []
    count = conn.execute("SELECT COUNT(*) FROM daily_candidates").fetchone()[0]
    assert count == 0
