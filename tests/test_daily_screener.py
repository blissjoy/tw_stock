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


def _build_downtrend_df(n_days: int = 70) -> pd.DataFrame:
    """_build_uptrend_df()的鏡射版本，供空頭方向的screen_*測試使用。"""
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    close = [200 - i * 0.3 for i in range(n_days)]
    close[-1] = close[-2] * 0.97  # 最後一天跳空下跌3%，確保黑K實體跌幅>2%
    open_ = [c + 0.2 for c in close]
    open_[-1] = close[-2]
    high = [c + 0.5 for c in close]
    low = [c - 0.5 for c in close]
    volume = [1000] * n_days
    volume[-1] = 1500
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def _build_golden_cross_df(n_days: int = 40) -> pd.DataFrame:
    """前段持平、最後一天急拉，讓MA10剛好在「最後一天」上穿MA20(is_golden_cross只在真正
    穿越當天才是True，維持在上方的隔幾天都不算)——R-MA-28兩條均線黃金交叉用。"""
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    flat_len = n_days - 1
    close = [100.0] * flat_len + [112.0]
    high = [c + 0.5 for c in close]
    low = [c - 0.5 for c in close]
    open_ = [c - 0.2 for c in close]
    volume = [1000] * n_days
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def _build_death_cross_df(n_days: int = 40) -> pd.DataFrame:
    """_build_golden_cross_df()的鏡射版本(R-MA-29兩條均線死亡交叉用)。"""
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    flat_len = n_days - 1
    close = [200.0] * flat_len + [188.0]
    high = [c + 0.5 for c in close]
    low = [c - 0.5 for c in close]
    open_ = [c + 0.2 for c in close]
    volume = [1000] * n_days
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def test_screen_bear_short_term_entry_fires_when_conditions_met(monkeypatch):
    df = _build_downtrend_df(n_days=70)
    monkeypatch.setattr(
        daily_screener, "daily_bear_trend_state",
        lambda high, low, close, n=5: pd.Series(True, index=close.index),
    )
    result = daily_screener.screen_bear_short_term_entry(df, min_days=60)
    assert result is not None
    assert result["signal_name"] == "R-TREND-15空頭短線進場（92%）"
    assert result["entry_price"] == df["close"].iloc[-1]
    assert result["stop_loss"] > result["entry_price"]


def test_screen_bear_short_term_entry_returns_none_when_not_bear_trend(monkeypatch):
    df = _build_downtrend_df(n_days=70)
    monkeypatch.setattr(
        daily_screener, "daily_bear_trend_state",
        lambda high, low, close, n=5: pd.Series(False, index=close.index),
    )
    assert daily_screener.screen_bear_short_term_entry(df, min_days=60) is None


def test_screen_single_ma_short_term_long_fires_when_conditions_met(monkeypatch):
    df = _build_uptrend_df(n_days=70)
    monkeypatch.setattr(
        daily_screener, "daily_bull_trend_state",
        lambda high, low, close, n=5: pd.Series(True, index=close.index),
    )
    result = daily_screener.screen_single_ma_short_term_long(df, min_days=60)
    assert result is not None
    assert result["signal_name"] == "R-MA-22單一均線短線做多（88%）"
    assert result["stop_loss"] < result["entry_price"]


def test_screen_single_ma_short_term_short_fires_when_conditions_met(monkeypatch):
    df = _build_downtrend_df(n_days=70)
    monkeypatch.setattr(
        daily_screener, "daily_bear_trend_state",
        lambda high, low, close, n=5: pd.Series(True, index=close.index),
    )
    result = daily_screener.screen_single_ma_short_term_short(df, min_days=60)
    assert result is not None
    assert result["signal_name"] == "R-MA-23單一均線短線做空（88%）"
    assert result["stop_loss"] > result["entry_price"]


def test_screen_single_ma_mid_term_long_fires_when_conditions_met(monkeypatch):
    df = _build_uptrend_df(n_days=70)
    monkeypatch.setattr(
        daily_screener, "daily_bull_trend_state",
        lambda high, low, close, n=5: pd.Series(True, index=close.index),
    )
    result = daily_screener.screen_single_ma_mid_term_long(df, min_days=60)
    assert result is not None
    assert result["signal_name"] == "R-MA-24單一均線中線做多（88%）"


def test_screen_single_ma_mid_term_short_fires_when_conditions_met(monkeypatch):
    df = _build_downtrend_df(n_days=70)
    monkeypatch.setattr(
        daily_screener, "daily_bear_trend_state",
        lambda high, low, close, n=5: pd.Series(True, index=close.index),
    )
    result = daily_screener.screen_single_ma_mid_term_short(df, min_days=60)
    assert result is not None
    assert result["signal_name"] == "R-MA-25單一均線中線做空（88%）"


def test_screen_dual_ma_long_term_long_fires_on_golden_cross():
    df = _build_golden_cross_df()
    result = daily_screener.screen_dual_ma_long_term_long(df, min_days=30)
    assert result is not None
    assert result["signal_name"] == "R-MA-28兩條均線長線做多（89%）"
    assert result["stop_loss"] < result["entry_price"]


def test_screen_dual_ma_long_term_long_returns_none_without_cross():
    df = _build_uptrend_df(n_days=70)  # 平滑上漲，交叉發生在暖身期附近而非最後一天
    result = daily_screener.screen_dual_ma_long_term_long(df, min_days=60)
    assert result is None


def test_screen_dual_ma_long_term_short_fires_on_death_cross():
    df = _build_death_cross_df()
    result = daily_screener.screen_dual_ma_long_term_short(df, min_days=30)
    assert result is not None
    assert result["signal_name"] == "R-MA-29兩條均線長線做空（89%）"
    assert result["stop_loss"] > result["entry_price"]


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
    assert result["signal_name"] == "R-TREND-14多頭短線進場（92%）"
    assert result["entry_price"] == df["close"].iloc[-1]
    assert result["stop_loss"] < result["entry_price"]


def test_screen_bull_short_term_entry_returns_none_when_not_bull_trend(monkeypatch):
    df = _build_uptrend_df(n_days=70)
    monkeypatch.setattr(
        daily_screener, "daily_bull_trend_state",
        lambda high, low, close, n=5: pd.Series(False, index=close.index),
    )
    assert daily_screener.screen_bull_short_term_entry(df, min_days=60) is None


def test_analyze_stock_signals_returns_empty_when_nothing_matches():
    df = _build_uptrend_df(n_days=20)  # 天數不足_SCREEN_FUNCTIONS(60)與黃金層掃描(30)兩邊的門檻
    assert daily_screener.analyze_stock_signals(df, min_days=60) == []


def test_analyze_stock_signals_includes_confidence_and_rule_description(monkeypatch):
    df = _build_uptrend_df(n_days=70)
    monkeypatch.setattr(
        daily_screener, "daily_bull_trend_state",
        lambda high, low, close, n=5: pd.Series(True, index=close.index),
    )
    import src.screener.rule_scan as rule_scan
    monkeypatch.setattr(rule_scan, "scan_golden_tier", lambda df, trend_df=None: [])  # 這裡只驗證_SCREEN_FUNCTIONS路徑，黃金層另有專屬測試

    matches = daily_screener.analyze_stock_signals(df, min_days=60)

    # 這段均線持續上漲的合成資料，除了R-TREND-14以外，均線分類的單一均線短/中線做多戰法
    # (R-MA-22/24)進場條件(收盤突破MA5且突破前一日高點)也會一併成立，都是合理觸發，
    # 不是重複或錯誤——依信心分數由高到低排序，R-TREND-14(92%)應該排第一。
    assert len(matches) >= 1
    match = matches[0]
    assert match["rule_id"] == "R-TREND-14"
    assert match["title"] == "多頭短線進場"
    assert match["confidence"] == 92
    assert match["description"]  # 從ai/zhu-rules/查到的完整解讀文字，非空
    assert match["reference"]
    assert matches == sorted(matches, key=lambda m: -m["confidence"])


def test_analyze_stock_signals_sorts_by_confidence_descending(monkeypatch):
    def fake_low(df, min_days=60):
        return {"signal_name": "R-FAKE-01假規則甲（60%）", "entry_price": 1.0, "stop_loss": 0.9, "note": None}

    def fake_high(df, min_days=60):
        return {"signal_name": "R-FAKE-02假規則乙（95%）", "entry_price": 1.0, "stop_loss": 0.9, "note": None}

    monkeypatch.setattr(daily_screener, "_SCREEN_FUNCTIONS", (fake_low, fake_high))

    matches = daily_screener.analyze_stock_signals(pd.DataFrame({"close": [1]}), min_days=0)

    assert [m["rule_id"] for m in matches] == ["R-FAKE-02", "R-FAKE-01"]
    assert matches[0]["description"] is None  # 查無此規則(假規則)，優雅回傳None不crash


def test_analyze_stock_signals_merges_duplicate_rule_id_notes(monkeypatch):
    """使用者實測2317鴻海反映「個股分析」面板裡的規則列表跟候選清單不一致、疑似重複——
    追查後確認scan_golden_tier()裡R-TREND-03/04這類規則因為短/中/長三種天期各自獨立
    判斷，同一個rule_id可能被add()呼叫多次、note文字不同(天期不同)。這裡驗證合併成
    一筆，note是用換行接起來的多行文字，畫面上不會出現同一條規則名稱重複列出兩次。"""
    monkeypatch.setattr(daily_screener, "_SCREEN_FUNCTIONS", ())
    import src.screener.rule_scan as rule_scan
    monkeypatch.setattr(
        rule_scan, "scan_golden_tier",
        lambda df, trend_df=None: [
            {"rule_id": "R-TREND-03", "note": "短期(日線轉折波)：頭頭高且底底高，多頭趨勢成立"},
            {"rule_id": "R-TREND-03", "note": "中期(週線轉折波)：頭頭高且底底高，多頭趨勢成立"},
        ],
    )

    matches = daily_screener.analyze_stock_signals(pd.DataFrame({"close": [1]}), min_days=0)

    assert len(matches) == 1
    assert matches[0]["rule_id"] == "R-TREND-03"
    assert matches[0]["note"] == (
        "短期(日線轉折波)：頭頭高且底底高，多頭趨勢成立\n"
        "中期(週線轉折波)：頭頭高且底底高，多頭趨勢成立"
    )


def test_summarize_signal_matches_returns_zeros_when_empty():
    summary = daily_screener.summarize_signal_matches([])
    assert summary == {"total": 0, "bullish": 0, "bearish": 0, "other": 0, "top_match": None}


def test_summarize_signal_matches_classifies_by_title_keyword_and_picks_top_match():
    # 依信心分數由高到低排列，符合analyze_stock_signals()的輸出慣例(summarize_signal_
    # matches()假設輸入已排序，直接取第一筆當top_match，不會自己重新排序)。
    matches = [
        {"rule_id": "R-GAP-01", "title": "缺口基本定義與偵測規則", "confidence": 94, "note": "今天出現向下跳空缺口", "description": "d", "reference": "r"},
        {"rule_id": "R-TREND-14", "title": "多頭短線進場", "confidence": 92, "note": "多頭排列成立", "description": "d", "reference": "r"},
        {"rule_id": "R-CANDLE-33", "title": "機械化空頭K線交易規則", "confidence": 89, "note": None, "description": "d", "reference": "r"},
    ]

    summary = daily_screener.summarize_signal_matches(matches)

    assert summary["total"] == 3
    assert summary["bullish"] == 1  # R-TREND-14標題含"多"不含"空"
    assert summary["bearish"] == 1  # R-CANDLE-33標題含"空"不含"多"
    assert summary["other"] == 1  # R-GAP-01標題兩者皆無
    # matches已依信心排序，top_match直接取第一筆(信心94%的R-GAP-01排第一，不是重新排序找最高)
    assert summary["top_match"]["rule_id"] == "R-GAP-01"


def test_summarize_signal_matches_title_with_both_keywords_counts_as_other():
    matches = [
        {"rule_id": "R-X-01", "title": "多空操作心法", "confidence": 80, "note": None, "description": None, "reference": None},
    ]

    summary = daily_screener.summarize_signal_matches(matches)

    assert summary["bullish"] == 0
    assert summary["bearish"] == 0
    assert summary["other"] == 1


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
    assert result["signal_name"] == "R-SCREEN-11底部盤整突破鎖股（89%）"
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
    assert result["signal_name"] == "R-SCREEN-15緩漲軌道突破做多（88%）"
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
    assert result["signal_name"] == "R-CLASSIC-24突破大量黑K買進（87%）"
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


def _build_breakaway_gap_df(n_days: int = 65, gap_up: bool = True, big_volume: bool = True) -> pd.DataFrame:
    """前n_days-1天維持完全相同的高低價(底部盤整不擴張，上緣=100)，最後一天視參數決定
    要不要真的向上跳空突破盤整區上緣、放量。"""
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    open_ = [97.0] * (n_days - 1)
    high = [100.0] * (n_days - 1)
    low = [95.0] * (n_days - 1)
    close = [98.0] * (n_days - 1)
    volume = [1000] * (n_days - 1)

    if gap_up:
        last_open, last_close, last_high, last_low = 106.0, 109.0, 110.0, 105.0  # low=105>前一日high=100，缺口成立
    else:
        last_open, last_close, last_high, last_low = 99.0, 100.5, 101.0, 98.0  # low=98<=前一日high=100，沒有跳空
    last_volume = 2500 if big_volume else 1050  # 20日均量約1000，2500>=2倍門檻，1050不到

    open_.append(last_open)
    close.append(last_close)
    high.append(last_high)
    low.append(last_low)
    volume.append(last_volume)

    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def test_screen_breakaway_gap_up_returns_none_when_not_enough_days():
    df = _build_breakaway_gap_df(n_days=30)
    assert daily_screener.screen_breakaway_gap_up(df, min_days=60) is None


def test_screen_breakaway_gap_up_fires_with_strong_signal_when_big_volume():
    df = _build_breakaway_gap_df(n_days=65, gap_up=True, big_volume=True)

    result = daily_screener.screen_breakaway_gap_up(df, min_days=60)

    assert result is not None
    assert result["signal_name"] == "R-GAP-09打底完成向上突破缺口（90%）"
    assert result["entry_price"] == df["close"].iloc[-1]
    assert result["stop_loss"] < result["entry_price"]
    assert "強力買進訊號" in result["note"]
    assert "100" in result["note"]  # note裡應該提到缺口下緣(原盤整區上緣，轉為支撐)


def test_screen_breakaway_gap_up_still_fires_but_weaker_when_volume_not_enough():
    """detect_breakaway_gap_up()本身在量能不足時不是回傳None，而是回傳訊號強度降低的
    版本(書中的規則語意本來就是如此，不是這裡另外加的邏輯)，跟其他規則「量不夠就不算」
    的模式不同，這裡刻意測這個差異，避免以後改壞了都不知道。"""
    df = _build_breakaway_gap_df(n_days=65, gap_up=True, big_volume=False)

    result = daily_screener.screen_breakaway_gap_up(df, min_days=60)

    assert result is not None
    assert "缺乏大量配合" in result["note"]


def test_screen_breakaway_gap_up_returns_none_when_no_gap():
    df = _build_breakaway_gap_df(n_days=65, gap_up=False)
    assert daily_screener.screen_breakaway_gap_up(df, min_days=60) is None


def test_screen_breakaway_gap_up_returns_none_without_prior_consolidation():
    """沒有先形成夠長的底部盤整區間(這裡直接用一般上升趨勢資料)，即使最後一天也符合
    跳空條件，也不應該被誤判成「打底完成」突破缺口。"""
    df = _build_uptrend_df(n_days=65)
    assert daily_screener.screen_breakaway_gap_up(df, min_days=60) is None


def _build_breakaway_gap_down_df(n_days: int = 65, gap_down: bool = True) -> pd.DataFrame:
    """_build_breakaway_gap_df()的鏡射版本(R-GAP-14用)：前n_days-1天維持相同高低價
    (盤整區下緣=95)，最後一天視參數決定要不要真的向下跳空跌破盤整區下緣。"""
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    open_ = [97.0] * (n_days - 1)
    high = [100.0] * (n_days - 1)
    low = [95.0] * (n_days - 1)
    close = [98.0] * (n_days - 1)
    volume = [1000] * (n_days - 1)

    if gap_down:
        last_open, last_close, last_high, last_low = 89.0, 86.0, 90.0, 85.0  # high=90<前一日low=95，缺口成立
    else:
        last_open, last_close, last_high, last_low = 96.0, 94.5, 97.0, 93.0  # high=97>=前一日low=95，沒有跳空

    open_.append(last_open)
    close.append(last_close)
    high.append(last_high)
    low.append(last_low)
    volume.append(1000)

    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def test_screen_breakaway_gap_down_fires_when_conditions_met():
    """R-GAP-14做頭完成向下跌破缺口：R-GAP-09(已接)的鏡射版本，不需要大量配合。"""
    df = _build_breakaway_gap_down_df(n_days=65, gap_down=True)

    result = daily_screener.screen_breakaway_gap_down(df, min_days=60)

    assert result is not None
    assert result["signal_name"] == "R-GAP-14做頭完成向下跌破缺口（91%）"
    assert result["entry_price"] == df["close"].iloc[-1]
    assert result["stop_loss"] > result["entry_price"]
    assert "95" in result["note"]  # note裡應該提到缺口上緣(原盤整區下緣，轉為壓力)


def test_screen_breakaway_gap_down_returns_none_when_no_gap():
    df = _build_breakaway_gap_down_df(n_days=65, gap_down=False)
    assert daily_screener.screen_breakaway_gap_down(df, min_days=60) is None


def test_screen_breakaway_gap_down_returns_none_without_prior_topping_box():
    """沒有先形成夠長的頭部盤整區間(這裡直接用一般下跌趨勢資料)，即使最後一天也符合
    跳空條件，也不應該被誤判成「做頭完成」跌破缺口。"""
    df = _build_downtrend_df(n_days=65)
    assert daily_screener.screen_breakaway_gap_down(df, min_days=60) is None


def test_screen_mechanical_long_fires_when_close_breaks_above_prev_high():
    """R-CANDLE-32：機械化多頭K線交易規則本身是純粹的逐日狀態機(不依賴任何K線型態
    辨識)，直接用真實資料觸發即可，不需要mock。"""
    n_days = 65
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    close = [100.0] * n_days
    close[-1] = 112.0  # 最後一天收盤突破前一天高點(100.5)
    high = [100.5] * n_days
    low = [99.5] * n_days
    open_ = [100.0] * n_days
    volume = [1000] * n_days
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)

    result = daily_screener.screen_mechanical_long(df, min_days=60)
    assert result is not None
    assert result["signal_name"] == "R-CANDLE-32機械化多頭K線交易規則（89%）"
    assert result["entry_price"] == 112.0
    assert result["stop_loss"] < result["entry_price"]


def test_screen_mechanical_long_returns_none_when_no_breakout():
    n_days = 65
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    df = pd.DataFrame(
        {"open": [100.0] * n_days, "high": [100.5] * n_days, "low": [99.5] * n_days,
         "close": [100.0] * n_days, "volume": [1000] * n_days},
        index=dates,
    )
    assert daily_screener.screen_mechanical_long(df, min_days=60) is None


def test_screen_mechanical_short_fires_when_close_breaks_below_prev_low():
    """R-CANDLE-33：R-CANDLE-32的鏡射版本。"""
    n_days = 65
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    close = [100.0] * n_days
    close[-1] = 88.0  # 最後一天收盤跌破前一天低點(99.5)
    high = [100.5] * n_days
    low = [99.5] * n_days
    open_ = [100.0] * n_days
    volume = [1000] * n_days
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)

    result = daily_screener.screen_mechanical_short(df, min_days=60)
    assert result is not None
    assert result["signal_name"] == "R-CANDLE-33機械化空頭K線交易規則（89%）"
    assert result["entry_price"] == 88.0
    assert result["stop_loss"] > result["entry_price"]


def test_screen_mechanical_short_returns_none_when_no_breakdown():
    n_days = 65
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    df = pd.DataFrame(
        {"open": [100.0] * n_days, "high": [100.5] * n_days, "low": [99.5] * n_days,
         "close": [100.0] * n_days, "volume": [1000] * n_days},
        index=dates,
    )
    assert daily_screener.screen_mechanical_short(df, min_days=60) is None


def test_screen_all_stocks_aggregates_multiple_candidates(monkeypatch):
    monkeypatch.setattr(
        daily_screener, "daily_bull_trend_state",
        lambda high, low, close, n=5: pd.Series(True, index=close.index),
    )
    df_ok = _build_uptrend_df(70)
    df_short = _build_uptrend_df(30)
    candidates = daily_screener.screen_all_stocks({"2330": df_ok, "1101": df_short}, min_days=60)
    # df_ok同時符合R-TREND-14跟均線分類的單一均線短/中線做多戰法(R-MA-22/24)，都是合理
    # 觸發；df_short天數不足(30<60)，1101不應該出現在候選清單裡。
    assert len(candidates) >= 1
    assert all(c["stock_id"] == "2330" for c in candidates)


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


def test_load_trailing_frames_excludes_index_market_rows():
    """大盤(market="INDEX"，見src/data/yfinance_client.py的fetch_taiex_prices())不是
    一檔可以交易的股票，不該被個股批次選股邏輯(screen_all_stocks的進場價/停損建議等
    規則)誤判成候選標的、混進daily_candidates候選清單。"""
    conn = init_db(":memory:")
    _seed_stock_prices(conn, "2330", n_days=70)
    upsert_stocks(conn, [{"stock_id": "^TWII", "name": "台股加權指數", "market": "INDEX", "industry": None, "updated_at": "2026-07-22"}])
    rows = [
        {
            "stock_id": "^TWII", "date": f"2026-{(1 + d // 28):02d}-{(1 + d % 28):02d}",
            "open": 17000.0, "high": 17100.0, "low": 16950.0, "close": 17050.0, "volume": 5000000,
            "trading_money": None, "trading_turnover": None, "spread": None,
        }
        for d in range(70)
    ]
    upsert_stock_prices(conn, rows)

    frames = daily_screener.load_trailing_frames(conn, min_days=60)

    assert set(frames.keys()) == {"2330"}


def test_run_screen_and_store_defaults_to_latest_price_date_when_iso_date_omitted(monkeypatch):
    """iso_date未指定時應該用stock_prices裡實際的最新交易日，不是date.today()這個日曆
    日期——真實案例：本機DB最後交易日其實是2026-07-24(週五)，但週六按「立即重新篩選」
    (兩個前端都是呼叫run_screen_and_store(conn)、不傳iso_date)時寫成了2026-07-25(週六)，
    候選清單日期下拉選單因此冒出一個實際上沒有任何交易發生的日期。"""
    conn = init_db(":memory:")
    _seed_stock_prices(conn, "2330", n_days=70)  # _seed_stock_prices的日期產生規則下，最後一天是2026-03-14
    candidate = {"stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場", "entry_price": 104.0, "stop_loss": 99.0, "note": None}
    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [candidate])

    daily_screener.run_screen_and_store(conn, min_days=60)  # 不傳iso_date

    dates = [row[0] for row in conn.execute("SELECT DISTINCT date FROM daily_candidates").fetchall()]
    assert dates == ["2026-03-14"]


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
