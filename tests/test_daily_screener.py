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


def _build_narrow_range_breakout_df(n_days: int = 60) -> pd.DataFrame:
    """前n_days-1天維持完全相同的高低價(狹幅盤整不擴張)，最後一天中長紅K放量突破。"""
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    open_ = [100.0] * (n_days - 1) + [100.0]
    high = [100.0] * (n_days - 1) + [106.0]
    low = [95.0] * (n_days - 1) + [99.0]
    close = [98.0] * (n_days - 1) + [105.0]  # 最後一天實體漲幅(105-100)/100=5% >= 3.5%門檻
    volume = [1000] * (n_days - 1) + [3000]  # 區間均量1000，突破日3000 >= 2倍門檻
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def test_screen_narrow_range_bottom_breakout_returns_none_when_not_enough_days():
    df = _build_narrow_range_breakout_df(n_days=30)
    assert daily_screener.screen_narrow_range_bottom_breakout(df, min_days=60) is None


def test_screen_narrow_range_bottom_breakout_fires_when_conditions_met():
    df = _build_narrow_range_breakout_df(n_days=60)

    result = daily_screener.screen_narrow_range_bottom_breakout(df, min_days=60)

    assert result is not None
    assert result["signal_name"] == "R-SCREEN-11底部盤整突破鎖股"
    assert result["entry_price"] == df["close"].iloc[-1]
    assert result["stop_loss"] < result["entry_price"]


def test_screen_narrow_range_bottom_breakout_returns_none_when_volume_not_enough():
    df = _build_narrow_range_breakout_df(n_days=60)
    df.loc[df.index[-1], "volume"] = 1100  # 只有區間均量的1.1倍，不到2倍門檻

    assert daily_screener.screen_narrow_range_bottom_breakout(df, min_days=60) is None


def test_screen_narrow_range_bottom_breakout_returns_none_without_prior_consolidation():
    """沒有先形成夠長的橫盤區間(這裡直接用一般上升趨勢資料)，即使最後一天也是大量紅K，
    也不應該被誤判成底部盤整突破。"""
    df = _build_uptrend_df(n_days=60)
    assert daily_screener.screen_narrow_range_bottom_breakout(df, min_days=60) is None


def _build_channel_breakout_df(n_days: int = 60) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    open_ = [100.0] * (n_days - 1) + [100.0]
    high = [102.0] * (n_days - 1) + [107.0]
    low = [98.0] * (n_days - 1) + [99.0]
    close = [100.0] * (n_days - 1) + [106.0]  # 最後一天實體漲幅6% >= 3.5%門檻
    volume = [1000] * (n_days - 1) + [2500]  # 前20日均量1000，突破日2500 >= 2倍門檻
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def test_screen_slow_rally_channel_breakout_returns_none_when_not_enough_days():
    df = _build_channel_breakout_df(n_days=30)
    assert daily_screener.screen_slow_rally_channel_breakout(df, min_days=60) is None


def test_screen_slow_rally_channel_breakout_returns_none_when_no_channel_found():
    """compute_trendlines()算不出up_channel(例如資料裡沒有形成夠格的上升軌道)時，
    不應該誤判成軌道突破。"""
    df = _build_channel_breakout_df(n_days=60)
    assert daily_screener.screen_slow_rally_channel_breakout(df, min_days=60) is None


def test_screen_slow_rally_channel_breakout_fires_when_conditions_met(monkeypatch):
    from src.indicators.trendlines import LinePoint, TrendLine

    df = _build_channel_breakout_df(n_days=60)
    fake_channel = TrendLine(a=LinePoint(0, 90.0), b=LinePoint(1, 90.0), role="resistance")
    monkeypatch.setattr(daily_screener.chart_overlays, "compute_trendlines", lambda df: {"up_channel": fake_channel})

    result = daily_screener.screen_slow_rally_channel_breakout(df, min_days=60)

    assert result is not None
    assert result["signal_name"] == "R-SCREEN-15緩漲軌道突破做多"
    assert result["entry_price"] == df["close"].iloc[-1]
    assert result["stop_loss"] < result["entry_price"]


def test_screen_slow_rally_channel_breakout_returns_none_when_close_below_channel(monkeypatch):
    from src.indicators.trendlines import LinePoint, TrendLine

    df = _build_channel_breakout_df(n_days=60)
    fake_channel = TrendLine(a=LinePoint(0, 200.0), b=LinePoint(1, 200.0), role="resistance")  # 遠高於收盤價
    monkeypatch.setattr(daily_screener.chart_overlays, "compute_trendlines", lambda df: {"up_channel": fake_channel})

    assert daily_screener.screen_slow_rally_channel_breakout(df, min_days=60) is None


def _build_big_black_breakout_df(n_days: int = 65, breakout: bool = True, breakout_volume_ok: bool = True) -> pd.DataFrame:
    """前50天穩定緩升(確保MA5>MA10>MA20多頭排列成立)，第50天出現大量黑K(watch_high=131)，
    之後盤整在watch_high之下，最後一天視參數決定要不要真的收盤突破watch_high、放量。"""
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    open_, high, low, close, volume = [], [], [], [], []

    for i in range(50):
        c = 100.0 + i * 0.5
        open_.append(c - 0.2)
        high.append(c + 0.3)
        low.append(c - 0.3)
        close.append(c)
        volume.append(1000)

    # 第50天(index 50)：多頭排列期間的大量黑K，high=131，收黑，量是前一日的2.5倍
    open_.append(130.0)
    close.append(125.0)
    high.append(131.0)
    low.append(124.0)
    volume.append(2500)

    # index 51~(n_days-2)：盤整在watch_high(131)之下，量能平淡
    for _ in range(51, n_days - 1):
        open_.append(126.0)
        close.append(126.5)
        high.append(127.0)
        low.append(125.5)
        volume.append(1000)

    # 最後一天：依參數決定是否真的突破watch_high、放量
    last_close = 135.0 if breakout else 128.0  # 128仍低於watch_high=131，不算突破
    last_volume = 2200 if breakout_volume_ok else 1050  # 前一天量是1000，2200>=2倍門檻，1050不到
    open_.append(126.0)
    close.append(last_close)
    high.append(max(last_close + 1, 132.0))
    low.append(126.0)
    volume.append(last_volume)

    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def test_screen_breakout_above_big_black_candle_returns_none_when_not_enough_days():
    df = _build_uptrend_df(n_days=30)
    assert daily_screener.screen_breakout_above_big_black_candle(df, min_days=60) is None


def test_screen_breakout_above_big_black_candle_fires_when_conditions_met():
    df = _build_big_black_breakout_df(n_days=65, breakout=True, breakout_volume_ok=True)

    result = daily_screener.screen_breakout_above_big_black_candle(df, min_days=60)

    assert result is not None
    assert result["signal_name"] == "R-CLASSIC-24突破大量黑K買進"
    assert result["entry_price"] == df["close"].iloc[-1]
    assert result["stop_loss"] < result["entry_price"]
    assert "131" in result["note"]  # note裡應該提到黑K高點(watch_high)


def test_screen_breakout_above_big_black_candle_returns_none_when_not_broken_out_yet():
    df = _build_big_black_breakout_df(n_days=65, breakout=False, breakout_volume_ok=True)
    assert daily_screener.screen_breakout_above_big_black_candle(df, min_days=60) is None


def test_screen_breakout_above_big_black_candle_returns_none_when_breakout_volume_not_enough():
    df = _build_big_black_breakout_df(n_days=65, breakout=True, breakout_volume_ok=False)
    assert daily_screener.screen_breakout_above_big_black_candle(df, min_days=60) is None


def test_screen_breakout_above_big_black_candle_returns_none_without_prior_big_black_candle():
    """一般上升趨勢資料裡沒有出現過大量黑K，即使最後一天大漲放量，也不應該誤判成
    「突破大量黑K」訊號(根本沒有黑K可以當作突破基準)。"""
    df = _build_uptrend_df(n_days=65)
    assert daily_screener.screen_breakout_above_big_black_candle(df, min_days=60) is None


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


def test_run_screen_and_store_rerun_same_date_drops_stale_candidates_not_selected_this_time(monkeypatch):
    """同一天可能重跑選股不只一次(手動按「立即重新篩選」按很多次、或補資料後重算)，每次都是
    從資料庫現有資料重新算出完整的候選清單。如果第一次選中A、第二次改成只選中B(A這次已經
    不符合條件)，第二次跑完後A不應該繼續留在daily_candidates裡——否則候選清單會顯示過時的
    結果(這正是2026-07-23實測回補時發現的真實現象：同一天重跑兩次，19檔舊結果沒被清掉，
    跟新的7檔一起顯示，變成26檔)。"""
    conn = init_db(":memory:")
    _seed_stock_prices(conn, "2330", n_days=70)
    _seed_stock_prices(conn, "1101", n_days=70)
    candidate_a = {"stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": None}
    candidate_b = {"stock_id": "1101", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 50.0, "stop_loss": 45.0, "note": None}

    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [candidate_a])
    daily_screener.run_screen_and_store(conn, iso_date="2026-07-23", min_days=60)

    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [candidate_b])
    daily_screener.run_screen_and_store(conn, iso_date="2026-07-23", min_days=60)

    rows = conn.execute("SELECT stock_id FROM daily_candidates WHERE date = '2026-07-23'").fetchall()
    assert rows == [("1101",)]  # 2330(第一次選中)應該被清掉，只留下第二次真正選中的1101


def test_run_screen_and_store_rerun_with_zero_candidates_clears_previous_stale_rows(monkeypatch):
    """就算重跑後這次算出0檔候選，也代表『今天正確答案就是沒有候選股』，一樣要清掉舊紀錄，
    不能因為candidates是空list就跳過清除、讓舊結果繼續殘留。"""
    conn = init_db(":memory:")
    _seed_stock_prices(conn, "2330", n_days=70)
    candidate_a = {"stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": None}

    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [candidate_a])
    daily_screener.run_screen_and_store(conn, iso_date="2026-07-23", min_days=60)

    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [])
    daily_screener.run_screen_and_store(conn, iso_date="2026-07-23", min_days=60)

    count = conn.execute("SELECT COUNT(*) FROM daily_candidates WHERE date = '2026-07-23'").fetchone()[0]
    assert count == 0


def test_run_screen_and_store_does_not_affect_other_dates(monkeypatch):
    """清除舊紀錄只能限定在這次重算的日期，不能誤刪其他日期的歷史候選紀錄。"""
    conn = init_db(":memory:")
    _seed_stock_prices(conn, "2330", n_days=70)
    candidate = {"stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": None}
    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [candidate])

    daily_screener.run_screen_and_store(conn, iso_date="2026-07-22", min_days=60)
    daily_screener.run_screen_and_store(conn, iso_date="2026-07-23", min_days=60)

    count_22 = conn.execute("SELECT COUNT(*) FROM daily_candidates WHERE date = '2026-07-22'").fetchone()[0]
    count_23 = conn.execute("SELECT COUNT(*) FROM daily_candidates WHERE date = '2026-07-23'").fetchone()[0]
    assert count_22 == 1
    assert count_23 == 1
