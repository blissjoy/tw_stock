import pandas as pd

import src.screener.rule_scan as rule_scan
from src.indicators.pivots import TurningPoint
from src.screener.rule_scan import scan_golden_tier


def _trend_df(n_days: int, direction: str) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    step = 0.4 if direction == "up" else -0.4
    close = [100 + i * step for i in range(n_days)]
    sign = 1 if direction == "up" else -1
    open_ = [c - 0.1 * sign for c in close]
    high = [max(o, c) + 0.5 for o, c in zip(open_, close)]
    low = [min(o, c) - 0.5 for o, c in zip(open_, close)]
    volume = [1000] * n_days
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def _flat_df(n_days: int = 40) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    return pd.DataFrame(
        {"open": [100.0] * n_days, "high": [101.0] * n_days, "low": [99.0] * n_days,
         "close": [100.0] * n_days, "volume": [1000] * n_days},
        index=dates,
    )


def test_scan_golden_tier_returns_empty_when_not_enough_days():
    df = _trend_df(20, "up")  # 少於MIN_DAYS(30)
    assert scan_golden_tier(df) == []


def test_scan_golden_tier_detects_bullish_signals_on_uptrend():
    df = _trend_df(60, "up")
    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "MA5>MA10>MA20" in results["R-MA-08"]
    assert "R-INDICATOR-22" in results  # 布林中軌上緣騎乘(買訊③)
    assert "超買" in results["R-INDICATOR-14"]


def test_scan_golden_tier_detects_bearish_signals_on_downtrend():
    df = _trend_df(60, "down")
    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "MA5<MA10<MA20" in results["R-MA-09"]
    assert "R-INDICATOR-23" in results  # 布林中軌下緣騎乘(做空訊③)
    assert "超賣" in results["R-INDICATOR-14"]


def test_scan_golden_tier_detects_ma_tangle_when_flat():
    results = {item["rule_id"] for item in scan_golden_tier(_flat_df())}
    assert "R-MA-12" in results


def test_scan_golden_tier_wires_every_underlying_check_correctly(monkeypatch):
    """底層各技術指標函式(黃金交叉/MACD/KD/RSI/布林/量能/K棒幾何)各自都已經有專屬測試
    驗證計算正確性，這裡只驗證rule_scan.py的「串接」本身沒有接錯：把每個底層函式監控
    成一定會觸發，確認每條規則都能被scan_golden_tier正確辨識、附上正確的rule_id。"""
    df = _trend_df(60, "up")
    true_series = pd.Series(True, index=df.index)
    text_series = pd.Series("測試訊號文字", index=df.index, dtype="object")

    monkeypatch.setattr(rule_scan, "is_bullish_aligned", lambda ma_frame: true_series)
    monkeypatch.setattr(rule_scan, "is_bearish_aligned", lambda ma_frame: true_series)
    monkeypatch.setattr(rule_scan, "is_ma_tangled", lambda ma_frame: true_series)
    monkeypatch.setattr(rule_scan, "is_ma_converged", lambda ma_frame, close: true_series)
    monkeypatch.setattr(rule_scan, "is_golden_cross", lambda a, b: true_series)
    monkeypatch.setattr(rule_scan, "is_death_cross", lambda a, b: true_series)
    monkeypatch.setattr(rule_scan, "macd_zero_axis_bull_signal", lambda dif, macd: text_series)
    monkeypatch.setattr(rule_scan, "macd_zero_axis_bear_signal", lambda dif, macd: text_series)
    monkeypatch.setattr(rule_scan, "is_high_dull", lambda k, d: true_series)
    monkeypatch.setattr(rule_scan, "is_low_dull", lambda k, d: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "rsi_overbought_oversold_signal", lambda r: text_series)
    monkeypatch.setattr(rule_scan, "rsi_short_long_cross_signal", lambda a, b: text_series)
    monkeypatch.setattr(rule_scan, "bollinger_buy_signal_3", lambda close, mid, upper: true_series)
    monkeypatch.setattr(rule_scan, "bollinger_sell_signal_3", lambda close, mid, lower: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "is_accumulation_volume", lambda volume, ma5_volume, close: true_series)
    monkeypatch.setattr(rule_scan, "is_reversal_candle_at_high", lambda o, h, l, c, pc: true_series)
    monkeypatch.setattr(rule_scan, "is_reversal_candle_at_low", lambda o, h, l, c, pc: true_series)
    monkeypatch.setattr(rule_scan, "is_hammer_candle", lambda o, h, l, c: true_series)
    monkeypatch.setattr(rule_scan, "is_inverted_hammer_candle", lambda o, h, l, c: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "classify_trend_states_multi_horizon", lambda h, l, c: {
        "短期": ("日線", "多頭", "測試依據"), "中期": ("週線", "多頭", "測試依據"), "長期": ("月線", "多頭", "測試依據"),
    })  # 讓interpret_cross真的算出訊號，不用另外mock
    monkeypatch.setattr(rule_scan, "kd_cross_signal_by_trend", lambda k, d, trend: text_series)
    monkeypatch.setattr(rule_scan, "bollinger_buy_signal_1", lambda close, lower, trend: true_series)
    monkeypatch.setattr(rule_scan, "bollinger_buy_signal_2", lambda close, mid, trend: true_series)
    monkeypatch.setattr(rule_scan, "bollinger_sell_signal_1", lambda close, upper, trend: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "bollinger_sell_signal_2", lambda close, mid, trend: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "granville_buy_signal_1", lambda close, ma20: true_series)
    monkeypatch.setattr(rule_scan, "granville_buy_signal_2", lambda close, low, ma20: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "granville_buy_signal_3", lambda close, ma20: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "granville_buy_signal_4", lambda close, ma20, is_bear_trend: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "granville_sell_signal_1", lambda close, ma20: true_series)
    monkeypatch.setattr(rule_scan, "granville_sell_signal_2", lambda close, high, ma20: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "granville_sell_signal_3", lambda close, ma20: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "granville_sell_signal_4", lambda close, ma20, is_bull_trend: pd.Series(False, index=df.index))

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    expected = [
        "R-MA-08", "R-MA-09", "R-MA-12", "R-MA-16", "R-MA-13", "R-MA-14",
        "R-INDICATOR-02", "R-INDICATOR-03", "R-INDICATOR-11", "R-INDICATOR-14", "R-INDICATOR-15",
        "R-INDICATOR-22", "R-VOLPRICE-01", "R-CANDLE-05", "R-CANDLE-13", "R-CANDLE-25",
        "R-TREND-03", "R-MA-15", "R-INDICATOR-09", "R-MA-19", "R-MA-20",
    ]
    for rule_id in expected:
        assert rule_id in rule_ids, f"{rule_id} 沒有被scan_golden_tier回報"
    # is_low_dull/bollinger_sell_signal_3/is_inverted_hammer_candle/bollinger_sell_signal_1&2
    # 刻意設為False，確認「沒觸發就不列入」的分支也有正確走到(不是每條都無條件回報True)
    assert rule_ids.count("R-INDICATOR-11") == 1  # 只有高檔鈍化觸發，低檔鈍化沒有
    assert "R-INDICATOR-23" not in rule_ids
    assert "R-TREND-04" not in rule_ids  # trend固定為"多頭"，不該同時冒出空頭趨勢
    assert rule_ids.count("R-MA-19") == 1  # 只有買點①觸發，買點②③④沒有
    assert rule_ids.count("R-MA-20") == 1  # 只有賣點①觸發，賣點②③④沒有


def test_scan_golden_tier_reports_bear_trend_and_skips_bull(monkeypatch):
    df = _trend_df(60, "up")
    monkeypatch.setattr(rule_scan, "classify_trend_states_multi_horizon", lambda h, l, c: {
        "短期": ("日線", "空頭", "測試依據"), "中期": ("週線", "空頭", "測試依據"), "長期": ("月線", "空頭", "測試依據"),
    })

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    assert "R-TREND-04" in rule_ids
    assert "R-TREND-03" not in rule_ids


def test_scan_golden_tier_reports_each_horizon_independently_when_they_disagree(monkeypatch):
    """短線走空、長線仍是多頭這種不一致情境，R-TREND-03跟R-TREND-04應該同時各自出現，
    不會互相排擠——這正是分開判斷短/中/長趨勢的核心理由(見trend_state.py)。"""
    df = _trend_df(60, "up")
    monkeypatch.setattr(rule_scan, "classify_trend_states_multi_horizon", lambda h, l, c: {
        "短期": ("日線", "空頭", "測試依據"), "中期": ("週線", "盤整", "測試依據"), "長期": ("月線", "多頭", "測試依據"),
    })

    results = scan_golden_tier(df)
    trend_notes = {item["rule_id"]: item["note"] for item in results if item["rule_id"] in ("R-TREND-03", "R-TREND-04")}

    assert "短期(日線轉折波)" in trend_notes["R-TREND-04"]
    assert "長期(月線轉折波)" in trend_notes["R-TREND-03"]
    assert "依據：測試依據" in trend_notes["R-TREND-04"]  # note要附上reason，不是只有結論
    # 中線是盤整，R-TREND-03/04都不該為了中線多冒出一筆
    assert sum(1 for item in results if item["rule_id"] == "R-TREND-03") == 1
    assert sum(1 for item in results if item["rule_id"] == "R-TREND-04") == 1


def test_scan_golden_tier_skips_ma15_when_trend_is_range(monkeypatch):
    """盤整趨勢下即使發生黃金/死亡交叉，interpret_cross()回傳「無明確訊號」，
    R-MA-15不應該被列入(這是interpret_cross()本身的語意，不是額外過濾邏輯)。"""
    df = _trend_df(60, "up")
    true_series = pd.Series(True, index=df.index)
    monkeypatch.setattr(rule_scan, "classify_trend_states_multi_horizon", lambda h, l, c: {
        "短期": ("日線", "盤整", "測試依據"), "中期": ("週線", "盤整", "測試依據"), "長期": ("月線", "盤整", "測試依據"),
    })
    monkeypatch.setattr(rule_scan, "is_golden_cross", lambda a, b: true_series)

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    assert "R-MA-15" not in rule_ids


def test_scan_golden_tier_skips_ma15_when_no_cross_today(monkeypatch):
    """今天沒有發生黃金/死亡交叉時，R-MA-15不該被評估(即使趨勢是多頭/空頭)。"""
    df = _trend_df(60, "up")
    false_series = pd.Series(False, index=df.index)
    monkeypatch.setattr(rule_scan, "classify_trend_states_multi_horizon", lambda h, l, c: {
        "短期": ("日線", "多頭", "測試依據"), "中期": ("週線", "多頭", "測試依據"), "長期": ("月線", "多頭", "測試依據"),
    })
    monkeypatch.setattr(rule_scan, "is_golden_cross", lambda a, b: false_series)
    monkeypatch.setattr(rule_scan, "is_death_cross", lambda a, b: false_series)

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    assert "R-MA-15" not in rule_ids


def test_scan_golden_tier_reports_macd_and_kd_divergence_from_turning_points(monkeypatch):
    """R-INDICATOR-07(MACD趨勢級背離)/R-INDICATOR-12(KD背離)都需要「股價轉折頭/底」配合
    同一天的MACD OSC/KD K值——用假的轉折點(日期取自df.index，確保能對應到真實算出來的
    MACD/KD數值)驗證wiring本身沒有接錯，底層背離判斷邏輯已有kd.py/macd.py各自的測試。"""
    df = _trend_df(60, "up")
    dates = df.index
    fake_points = [
        TurningPoint(type="bottom", price=90, index=dates[10]),
        TurningPoint(type="head", price=100, index=dates[20]),
        TurningPoint(type="bottom", price=95, index=dates[30]),
        TurningPoint(type="head", price=105, index=dates[40]),
    ]
    monkeypatch.setattr(rule_scan, "compute_turning_points", lambda h, l, c, n=5: fake_points)
    monkeypatch.setattr(rule_scan, "macd_trend_level_bullish_divergence", lambda heads, osc_peaks: True)
    monkeypatch.setattr(rule_scan, "macd_trend_level_bearish_divergence", lambda bottoms, osc_troughs: False)
    monkeypatch.setattr(rule_scan, "kd_peak_divergence", lambda heads, k_peaks: "KD峰背離，趨勢反轉風險升高")
    monkeypatch.setattr(rule_scan, "kd_trough_divergence", lambda bottoms, k_troughs: None)

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "股價頭頭高但OSC紅柱峰值頭頭低" in results["R-INDICATOR-07"]
    assert results["R-INDICATOR-12"] == "KD峰背離，趨勢反轉風險升高"


def test_scan_golden_tier_skips_macd_kd_divergence_when_fewer_than_two_turning_points(monkeypatch):
    """轉折點不足2組頭或2組底時，不應該呼叫背離判斷函式(避免用不足的資料誤判)。"""
    df = _trend_df(60, "up")
    monkeypatch.setattr(rule_scan, "compute_turning_points", lambda h, l, c, n=5: [
        TurningPoint(type="bottom", price=90, index=df.index[10]),
    ])
    called = []
    monkeypatch.setattr(rule_scan, "macd_trend_level_bullish_divergence", lambda heads, osc_peaks: called.append("macd") or False)
    monkeypatch.setattr(rule_scan, "kd_peak_divergence", lambda heads, k_peaks: called.append("kd") or None)

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    assert called == []
    assert "R-INDICATOR-07" not in rule_ids
    assert "R-INDICATOR-12" not in rule_ids


def test_scan_golden_tier_reports_ma_channel_breakout(monkeypatch):
    """R-INDICATOR-18 MA通道突破：函式回傳文字裡不是「常態」字樣時才列入，避免每天都出現。"""
    df = _trend_df(60, "up")
    monkeypatch.setattr(
        rule_scan, "ma_channel_breakout_signal",
        lambda close, upper, lower, is_large_volume: pd.Series("帶量突破上軌，偏多趨勢轉強", index=df.index),
    )

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert results["R-INDICATOR-18"] == "帶量突破上軌，偏多趨勢轉強"


def test_scan_golden_tier_skips_ma_channel_when_normal_range(monkeypatch):
    df = _trend_df(60, "up")
    monkeypatch.setattr(
        rule_scan, "ma_channel_breakout_signal",
        lambda close, upper, lower, is_large_volume: pd.Series("軌道內游走（常態）", index=df.index),
    )

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    assert "R-INDICATOR-18" not in rule_ids
