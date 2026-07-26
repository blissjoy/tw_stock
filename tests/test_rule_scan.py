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


def test_scan_golden_tier_reports_sr01_when_crossing_above_recent_head(monkeypatch):
    """R-SR-01：今天收盤剛好突破最近一個轉折高點才回報(不是每天都查詢角色)。"""
    df = _trend_df(60, "up")
    close_last, close_prev = df["close"].iloc[-1], df["close"].iloc[-2]
    head_price = (close_last + close_prev) / 2  # 剛好介於前一天與今天收盤之間，今天才突破
    monkeypatch.setattr(rule_scan, "compute_turning_points", lambda h, l, c, n=5: [
        TurningPoint(type="head", price=head_price, index=df.index[10]),
    ])

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert f"{head_price:.2f}" in results["R-SR-01"]
    assert "轉為" in results["R-SR-01"]


def test_scan_golden_tier_skips_sr01_when_no_head_crossed_today(monkeypatch):
    df = _trend_df(60, "up")
    monkeypatch.setattr(rule_scan, "compute_turning_points", lambda h, l, c, n=5: [
        TurningPoint(type="head", price=99999, index=df.index[10]),  # 遠高於股價，今天不可能突破
    ])

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    assert "R-SR-01" not in rule_ids


def test_scan_golden_tier_reports_sr02_when_crossing_below_recent_bottom(monkeypatch):
    """R-SR-02：今天收盤剛好跌破最近一個轉折低點才回報，是R-SR-01的鏡射。"""
    df = _trend_df(60, "down")
    close_last, close_prev = df["close"].iloc[-1], df["close"].iloc[-2]
    bottom_price = (close_last + close_prev) / 2
    monkeypatch.setattr(rule_scan, "compute_turning_points", lambda h, l, c, n=5: [
        TurningPoint(type="bottom", price=bottom_price, index=df.index[10]),
    ])

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert f"{bottom_price:.2f}" in results["R-SR-02"]


def test_scan_golden_tier_reports_sr08_ma_support_and_resistance_conversion(monkeypatch):
    """R-SR-08：月線支撐/壓力轉換是逐日狀態機函式，這裡只驗證wiring(有文字就回報)，
    底層3日觀察窗邏輯已有tests/test_support_resistance.py專屬測試。"""
    df = _trend_df(60, "up")
    monkeypatch.setattr(rule_scan, "ma_support_conversion_long", lambda close, ma, direction: pd.Series(
        [None] * (len(df) - 1) + ["月線支撐依然有效，多頭趨勢未變"], index=df.index,
    ))
    monkeypatch.setattr(rule_scan, "ma_resistance_conversion_short", lambda close, ma, direction: pd.Series(
        [None] * len(df), index=df.index,
    ))

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert results["R-SR-08"] == "月線支撐依然有效，多頭趨勢未變"


def test_scan_golden_tier_reports_sr15_bullish_support_buy_signal(monkeypatch):
    """R-SR-15：這裡只接了月線(MA20)當支撐來源(書中另外3種切線/前低/缺口未接)，貼近月線
    (2%容忍度)+止跌K棒+多頭趨勢才會回報，這裡直接mock touched條件確認wiring正確。"""
    df = _trend_df(60, "up")
    monkeypatch.setattr(rule_scan, "classify_trend_states_multi_horizon", lambda h, l, c: {
        "短期": ("日線", "多頭", "測試依據"), "中期": ("週線", "多頭", "測試依據"), "長期": ("月線", "多頭", "測試依據"),
    })
    monkeypatch.setattr(rule_scan, "is_bullish_reversal_candle", lambda o, h, l, c: True)
    # 讓最後一天的low/close落在MA20的2%容忍帶內：MA20由compute_ma_set真實算出，這裡直接
    # 把df最後一天的low/high/close改到貼近該天真實MA20附近，比重新mock compute_ma_set簡單。
    ma20_actual = rule_scan.compute_ma_set(df["close"], periods=(5, 10, 20))["MA20"].iloc[-1]
    df.loc[df.index[-1], ["low", "close"]] = [ma20_actual * 0.999, ma20_actual * 1.0]

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "R-SR-15" in results


def test_scan_golden_tier_skips_sr15_when_not_near_ma20(monkeypatch):
    df = _trend_df(60, "up")  # 平滑上漲，最後一天股價遠高於MA20，不會觸及支撐容忍帶
    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]
    assert "R-SR-15" not in rule_ids


def test_scan_golden_tier_skips_ma_channel_when_normal_range(monkeypatch):
    df = _trend_df(60, "up")
    monkeypatch.setattr(
        rule_scan, "ma_channel_breakout_signal",
        lambda close, upper, lower, is_large_volume: pd.Series("軌道內游走（常態）", index=df.index),
    )

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    assert "R-INDICATOR-18" not in rule_ids
