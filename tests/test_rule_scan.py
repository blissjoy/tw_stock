import pandas as pd

import src.screener.rule_scan as rule_scan
from src.indicators.pivots import TurningPoint
from src.indicators.trendlines import LinePoint, TrendLine
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


def test_scan_golden_tier_reports_gap_up_today():
    """R-GAP-01：今天的最低價高於昨天最高價，構成向上跳空缺口。"""
    df = _trend_df(60, "up")
    prev_high = float(df["high"].iloc[-2])
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [
        prev_high + 5, prev_high + 6, prev_high + 4, prev_high + 5.5,
    ]

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "向上跳空缺口" in results["R-GAP-01"]


def test_scan_golden_tier_no_gap_when_prices_overlap():
    df = _trend_df(60, "up")  # 平滑連續上漲，每天高低價都跟前一天重疊，不構成缺口
    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]
    assert "R-GAP-01" not in rule_ids


def test_scan_golden_tier_reports_true_and_false_gap_fill(monkeypatch):
    """R-GAP-19真封口/R-GAP-20假封口：往回找到最近一次缺口後，用「今天」的K棒評估——
    這裡直接mock底層is_true_fill/false_fill_reasons驗證wiring，兩者的判斷邏輯本身已有
    tests/test_gaps.py專屬測試。"""
    df = _trend_df(60, "up")
    prev_high = float(df["high"].iloc[-3])
    # 在倒數第2天造一個缺口，讓往回搜尋能找到(從len-2開始往前找，第一個命中就是它)
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [
        prev_high + 5, prev_high + 6, prev_high + 4, prev_high + 5.5,
    ]
    monkeypatch.setattr(rule_scan, "is_true_fill", lambda *a, **k: True)

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}
    assert "真封口" in results["R-GAP-19"]

    monkeypatch.setattr(rule_scan, "is_true_fill", lambda *a, **k: False)
    monkeypatch.setattr(rule_scan, "false_fill_reasons", lambda *a, **k: ["量縮，跌破力道不足"])

    results2 = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}
    assert "假封口" in results2["R-GAP-20"]
    assert "量縮" in results2["R-GAP-20"]


def test_scan_golden_tier_reports_line11_when_up_tangent_broken_today(monkeypatch):
    """R-LINE-11：重用chart_overlays.compute_trendlines()已經算好的role(角色互換就地更新
    在裡面)，這裡只驗證「今天」跟「昨天(少一天資料)」比較後，只在剛好變化的那天回報。"""
    df = _trend_df(60, "up")
    support_line = TrendLine(a=LinePoint(0, 90), b=LinePoint(10, 95), role="support")
    resistance_line = TrendLine(a=LinePoint(0, 90), b=LinePoint(10, 95), role="resistance")

    def fake_compute_trendlines(d, ma_window=5):
        return {"up_tangent": resistance_line if len(d) == len(df) else support_line}

    monkeypatch.setattr(rule_scan, "compute_trendlines", fake_compute_trendlines)

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "轉為壓力" in results["R-LINE-11"]


def test_scan_golden_tier_skips_line11_when_already_broken_yesterday(monkeypatch):
    df = _trend_df(60, "up")
    resistance_line = TrendLine(a=LinePoint(0, 90), b=LinePoint(10, 95), role="resistance")
    monkeypatch.setattr(rule_scan, "compute_trendlines", lambda d, ma_window=5: {"up_tangent": resistance_line})

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    assert "R-LINE-11" not in rule_ids


def test_scan_golden_tier_reports_line12_when_down_tangent_broken_today(monkeypatch):
    """R-LINE-12：R-LINE-11的鏡射版本(下降切線遭突破，原壓力轉支撐)。"""
    df = _trend_df(60, "up")
    resistance_line = TrendLine(a=LinePoint(0, 90), b=LinePoint(10, 95), role="resistance")
    support_line = TrendLine(a=LinePoint(0, 90), b=LinePoint(10, 95), role="support")

    def fake_compute_trendlines(d, ma_window=5):
        return {"down_tangent": support_line if len(d) == len(df) else resistance_line}

    monkeypatch.setattr(rule_scan, "compute_trendlines", fake_compute_trendlines)

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "轉為支撐" in results["R-LINE-12"]


def test_scan_golden_tier_reports_line14_when_channel_breakout_today(monkeypatch):
    """R-LINE-14：股價突破上升軌道線上緣，只在「今天」剛好突破時回報(不是每天都在軌道
    線上方就重複列出)。"""
    df = _trend_df(60, "up")
    channel = TrendLine(a=LinePoint(0, 90), b=LinePoint(1, 91), role="resistance")
    monkeypatch.setattr(rule_scan, "compute_trendlines", lambda d, ma_window=5: {"up_channel": channel})
    monkeypatch.setattr(rule_scan, "check_channel_breakout", lambda line, x, close: x == len(df) - 1)

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "突破上升軌道線" in results["R-LINE-14"]


def test_scan_golden_tier_skips_line14_when_broken_out_since_yesterday(monkeypatch):
    df = _trend_df(60, "up")
    channel = TrendLine(a=LinePoint(0, 90), b=LinePoint(1, 91), role="resistance")
    monkeypatch.setattr(rule_scan, "compute_trendlines", lambda d, ma_window=5: {"up_channel": channel})
    monkeypatch.setattr(rule_scan, "check_channel_breakout", lambda line, x, close: True)  # 昨天今天都算突破

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    assert "R-LINE-14" not in rule_ids


def test_scan_golden_tier_reports_line15_when_channel_breakdown_today(monkeypatch):
    """R-LINE-15：R-LINE-14的鏡射版本(跌破下降軌道線下緣)。"""
    df = _trend_df(60, "up")
    channel = TrendLine(a=LinePoint(0, 90), b=LinePoint(1, 89), role="support")
    monkeypatch.setattr(rule_scan, "compute_trendlines", lambda d, ma_window=5: {"down_channel": channel})
    monkeypatch.setattr(rule_scan, "check_channel_breakdown", lambda line, x, close: x == len(df) - 1)

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "跌破下降軌道線" in results["R-LINE-15"]


def test_scan_golden_tier_reports_trend08_bull_change_warning(monkeypatch):
    """R-TREND-08：前提是「昨天為止」多頭已確立(daily_bull_trend_state的前一天)，今天
    最新一組頭/底任一個出現裂痕(頭頭低或底底低)才預警——用假轉折點+固定昨天多頭狀態
    驗證wiring，底層邏輯本身已有tests/test_trend.py專屬測試。"""
    df = _trend_df(60, "up")
    dates = df.index
    fake_points = [
        TurningPoint(type="bottom", price=90, index=dates[10]),
        TurningPoint(type="head", price=105, index=dates[20]),
        TurningPoint(type="bottom", price=95, index=dates[30]),
        TurningPoint(type="head", price=100, index=dates[40]),  # 頭頭低(100<105)
    ]
    monkeypatch.setattr(rule_scan, "compute_turning_points", lambda h, l, c, n=5: fake_points)
    monkeypatch.setattr(rule_scan, "daily_bull_trend_state", lambda h, l, c, n=5: pd.Series(True, index=df.index))
    monkeypatch.setattr(rule_scan, "daily_bear_trend_state", lambda h, l, c, n=5: pd.Series(False, index=df.index))

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "頭頭低" in results["R-TREND-08"]


def test_scan_golden_tier_skips_trend08_when_bull_trend_not_confirmed_yesterday(monkeypatch):
    df = _trend_df(60, "up")
    dates = df.index
    fake_points = [
        TurningPoint(type="bottom", price=90, index=dates[10]),
        TurningPoint(type="head", price=105, index=dates[20]),
        TurningPoint(type="bottom", price=95, index=dates[30]),
        TurningPoint(type="head", price=100, index=dates[40]),
    ]
    monkeypatch.setattr(rule_scan, "compute_turning_points", lambda h, l, c, n=5: fake_points)
    monkeypatch.setattr(rule_scan, "daily_bull_trend_state", lambda h, l, c, n=5: pd.Series(False, index=df.index))

    rule_ids = [item["rule_id"] for item in scan_golden_tier(df)]

    assert "R-TREND-08" not in rule_ids


def test_scan_golden_tier_reports_trend09_bear_change_warning(monkeypatch):
    """R-TREND-09：R-TREND-08的鏡射版本(頭頭高或底底高預警空頭改變)。"""
    df = _trend_df(60, "up")
    dates = df.index
    fake_points = [
        TurningPoint(type="head", price=105, index=dates[10]),
        TurningPoint(type="bottom", price=90, index=dates[20]),
        TurningPoint(type="head", price=100, index=dates[30]),
        TurningPoint(type="bottom", price=95, index=dates[40]),  # 底底高(95>90)
    ]
    monkeypatch.setattr(rule_scan, "compute_turning_points", lambda h, l, c, n=5: fake_points)
    monkeypatch.setattr(rule_scan, "daily_bull_trend_state", lambda h, l, c, n=5: pd.Series(False, index=df.index))
    monkeypatch.setattr(rule_scan, "daily_bear_trend_state", lambda h, l, c, n=5: pd.Series(True, index=df.index))

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "底底高" in results["R-TREND-09"]


def test_scan_golden_tier_reports_classic02_big_black_breaks_uptrend_line(monkeypatch):
    """R-CLASSIC-02：每次掃描都會呼叫big_black_breaks_uptrend_line(無if閘門)，直接mock
    它驗證wiring即可，底層邏輯本身已有tests/test_classic_patterns.py專屬測試。"""
    df = _trend_df(60, "up")
    monkeypatch.setattr(rule_scan, "big_black_breaks_uptrend_line", lambda *a, **k: "空頭確認：上升切線轉壓力")

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert results["R-CLASSIC-02"] == "空頭確認：上升切線轉壓力"


def test_scan_golden_tier_reports_classic03_double_top_and_classic28_double_bottom(monkeypatch):
    """R-CLASSIC-03(M頭頸線)/R-CLASSIC-28(雙盤底，鏡射)：用假轉折點組出「兩頭夾一底」與
    「兩底夾一頭」，驗證頸線/壓力線的抓取邏輯，再mock最終組合函式驗證wiring。"""
    df = _trend_df(60, "up")
    dates = df.index
    fake_points = [
        TurningPoint(type="head", price=110, index=dates[10]),
        TurningPoint(type="bottom", price=95, index=dates[20]),
        TurningPoint(type="head", price=108, index=dates[30]),
        TurningPoint(type="bottom", price=100, index=dates[40]),
        TurningPoint(type="head", price=105, index=dates[50]),
    ]
    monkeypatch.setattr(rule_scan, "compute_turning_points", lambda h, l, c, n=5: fake_points)
    monkeypatch.setattr(rule_scan, "double_top_neckline_break", lambda *a, **k: "M頭頸線跌破，空頭確認")
    monkeypatch.setattr(rule_scan, "double_bottom_breakout_signal", lambda *a, **k: True)
    monkeypatch.setattr(rule_scan, "double_bottom_platform_breakout", lambda sig: "雙盤底大量紅K突破進場" if sig else None)

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "M頭頸線跌破" in results["R-CLASSIC-03"]
    assert "雙盤底" in results["R-CLASSIC-28"]


def test_scan_golden_tier_reports_classic05_break_below_two_day_volume_low(monkeypatch):
    """R-CLASSIC-05：往回找連續2日大量交易日(mock is_big_volume_vs_ma5)，今天黑K跌破這2日
    低點、昨天還沒跌破才回報。"""
    df = _trend_df(60, "up")
    idx = df.index
    big_vol = pd.Series(False, index=idx)
    big_vol.iloc[-5] = True
    big_vol.iloc[-6] = True
    monkeypatch.setattr(rule_scan, "is_big_volume_vs_ma5", lambda v, ma5v: big_vol)
    df.loc[idx[-6], ["open", "high", "low", "close"]] = [102, 103, 100, 101]
    df.loc[idx[-5], ["open", "high", "low", "close"]] = [101, 102, 100, 101.5]
    df.loc[idx[-2], ["open", "high", "low", "close"]] = [103, 104, 100.5, 103.5]
    df.loc[idx[-1], ["open", "high", "low", "close"]] = [103, 103.5, 98, 99]
    monkeypatch.setattr(rule_scan, "break_below_two_day_high_volume_low", lambda *a, **k: "跌破高檔連2日大量低點，一日反轉停利")

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "一日反轉停利" in results["R-CLASSIC-05"]


def test_scan_golden_tier_reports_classic25_break_above_two_day_volume_high(monkeypatch):
    """R-CLASSIC-25：R-CLASSIC-05的多空鏡射版本(低檔連2日大量被突破)。"""
    df = _trend_df(60, "up")
    idx = df.index
    big_vol = pd.Series(False, index=idx)
    big_vol.iloc[-5] = True
    big_vol.iloc[-6] = True
    big_vol.iloc[-1] = True
    monkeypatch.setattr(rule_scan, "is_big_volume_vs_ma5", lambda v, ma5v: big_vol)
    df.loc[idx[-6], ["open", "high", "low", "close"]] = [100, 105, 99, 104]
    df.loc[idx[-5], ["open", "high", "low", "close"]] = [104, 106, 103, 105]
    df.loc[idx[-2], ["open", "high", "low", "close"]] = [100, 105.5, 99, 101]
    df.loc[idx[-1], ["open", "high", "low", "close"]] = [101, 108, 100.5, 107]
    monkeypatch.setattr(rule_scan, "break_above_two_day_low_volume_high", lambda *a, **k: "突破低檔連2日大量高點，一日反轉轉強")

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "一日反轉轉強" in results["R-CLASSIC-25"]


def test_scan_golden_tier_reports_classic07_gap_down_black_reversal_at_high(monkeypatch):
    """R-CLASSIC-07：每次掃描都會呼叫gap_down_black_reversal_at_high(無if閘門)，直接mock
    驗證wiring。"""
    df = _trend_df(60, "up")
    monkeypatch.setattr(rule_scan, "gap_down_black_reversal_at_high", lambda *a, **k: "高檔跳空黑K回檔反轉，空頭確認")

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert results["R-CLASSIC-07"] == "高檔跳空黑K回檔反轉，空頭確認"


def test_scan_golden_tier_reports_classic09_chase_short_on_bounce_break(monkeypatch):
    """R-CLASSIC-09：空頭趨勢中(_trend_df(60,"down")天然滿足頭頭低底底低)，往回找一根帶量
    反彈紅K，今天跌破它的低點、昨天還沒跌破才回報。"""
    df = _trend_df(60, "down")
    idx = df.index
    df.loc[idx[-5], ["open", "high", "low", "close", "volume"]] = [90, 96, 89, 95, 5000]
    df.loc[idx[-4], ["open", "high", "low", "close"]] = [94, 95, 92, 93]
    df.loc[idx[-3], ["open", "high", "low", "close"]] = [93, 94, 91, 92]
    df.loc[idx[-2], ["open", "high", "low", "close"]] = [97, 98, 95, 96]
    df.loc[idx[-1], ["open", "high", "low", "close"]] = [88, 89, 83, 85]
    monkeypatch.setattr(
        rule_scan, "classify_trend_states_multi_horizon",
        lambda h, l, c: {
            "短期": ("日線", rule_scan.TREND_BEAR, "mock"),
            "中期": ("週線", rule_scan.TREND_BEAR, "mock"),
            "長期": ("月線", rule_scan.TREND_BEAR, "mock"),
        },
    )
    monkeypatch.setattr(rule_scan, "chase_short_on_bounce_break", lambda *a, **k: "跌破反彈紅K低點，追空點確認")

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "追空點確認" in results["R-CLASSIC-09"]


def test_scan_golden_tier_reports_classic12_gap_down_continuation(monkeypatch):
    """R-CLASSIC-12：重用R-GAP-19/20已經在用的recent_gap往回搜尋機制，篩選向下缺口且尚未
    真封口，直接mock最終組合函式驗證wiring。"""
    df = _trend_df(60, "up")
    prev_low = float(df["low"].iloc[-3])
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [
        prev_low - 5, prev_low - 4, prev_low - 6, prev_low - 4.5,
    ]
    monkeypatch.setattr(rule_scan, "is_true_fill", lambda *a, **k: False)
    monkeypatch.setattr(rule_scan, "false_fill_reasons", lambda *a, **k: ["量縮，跌破力道不足"])
    monkeypatch.setattr(rule_scan, "gap_down_continuation", lambda *a, **k: "缺口下再破底，續空/加碼放空點")

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "續空" in results["R-CLASSIC-12"]


def test_scan_golden_tier_reports_classic13_bull_to_bear_break_last_low(monkeypatch):
    """R-CLASSIC-13：往回找最近一個「當時仍在多頭趨勢中」確認的轉折低點(mock
    daily_bull_trend_state)，今天跌破、昨天還沒跌破才回報。"""
    df = _trend_df(60, "up")
    dates = df.index
    fake_bottoms = [
        TurningPoint(type="bottom", price=95, index=dates[20]),
        TurningPoint(type="bottom", price=98, index=dates[40]),
    ]
    monkeypatch.setattr(rule_scan, "compute_turning_points", lambda h, l, c, n=5: fake_bottoms)
    bull_state = pd.Series(False, index=df.index)
    bull_state.iloc[40] = True
    monkeypatch.setattr(rule_scan, "daily_bull_trend_state", lambda h, l, c, n=5: bull_state)
    monkeypatch.setattr(rule_scan, "bull_to_bear_break_last_low", lambda *a, **k: "跌破多頭最後低點，多頭趨勢終結，快速下跌警訊")
    df.loc[df.index[-2], "close"] = 99
    df.loc[df.index[-1], "close"] = 90

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "多頭趨勢終結" in results["R-CLASSIC-13"]


def test_scan_golden_tier_reports_classic15_break_below_down_channel(monkeypatch):
    """R-CLASSIC-15：重用R-LINE-15已經算好的down_channel，直接mock最終組合函式驗證wiring。"""
    df = _trend_df(60, "up")
    channel = TrendLine(a=LinePoint(0, 90), b=LinePoint(1, 89), role="support")
    monkeypatch.setattr(rule_scan, "compute_trendlines", lambda d, ma_window=5: {"down_channel": channel})
    monkeypatch.setattr(rule_scan, "break_below_down_channel", lambda *a, **k: "支撐轉壓力，跌勢由緩降轉為急跌")

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "急跌" in results["R-CLASSIC-15"]


def test_scan_golden_tier_reports_classic33_breakout_above_up_channel(monkeypatch):
    """R-CLASSIC-33：R-CLASSIC-15的鏡射版本(重用R-LINE-14的up_channel)。"""
    df = _trend_df(60, "up")
    channel = TrendLine(a=LinePoint(0, 90), b=LinePoint(1, 91), role="resistance")
    monkeypatch.setattr(rule_scan, "compute_trendlines", lambda d, ma_window=5: {"up_channel": channel})
    monkeypatch.setattr(rule_scan, "breakout_above_up_channel", lambda *a, **k: "漲勢自緩步盤堅轉為加速噴出，全書最強力多頭訊號")

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "加速噴出" in results["R-CLASSIC-33"]


def test_scan_golden_tier_reports_classic27_bear_rebound_consolidate_above_ma20_breakout(monkeypatch):
    """R-CLASSIC-27：重用R-CANDLE-04的consolidation_box，「昨天為止」已經確立盤整才成立
    前提(shift後看的是倒數第2天)，直接mock最終組合函式驗證wiring。"""
    df = _trend_df(60, "up")
    fake_box = pd.DataFrame(
        {
            "breakout_up": [False] * len(df),
            "breakout_down": [False] * len(df),
            "upper_neckline": [110.0] * len(df),
            "lower_neckline": [95.0] * len(df),
            "is_consolidating": [False] * (len(df) - 2) + [True, True],
            "group_len": [1] * len(df),
        },
        index=df.index,
    )
    monkeypatch.setattr(rule_scan, "detect_consolidation_breakout", lambda o, h, l, c, min_bars=20: fake_box)
    monkeypatch.setattr(rule_scan, "bear_rebound_consolidate_above_ma20_breakout", lambda *a, **k: "反彈站穩月線盤整後突破買點")

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "突破買點" in results["R-CLASSIC-27"]


def test_scan_golden_tier_reports_classic30_ma_tangle_breakout(monkeypatch):
    """R-CLASSIC-30：書中原文直接沿用「均線糾結向上突破做多SOP」(R-MA-17)，這裡只做直通，
    mock底層ma_tangle_breakout_long_entry驗證wiring，真的ma_tangle_breakout()是純函式
    直接讓它跑。"""
    df = _trend_df(60, "up")
    monkeypatch.setattr(rule_scan, "ma_tangle_breakout_long_entry", lambda *a, **k: pd.Series(True, index=df.index))

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "均線糾結" in results["R-CLASSIC-30"]


def test_scan_golden_tier_reports_classic32_island_reversal(monkeypatch):
    """R-CLASSIC-32：往回找「今天」向上缺口之前的向下缺口+中間盤整天數(用真實價格造出
    兩個真缺口，不mock detect_gap，避免影響R-GAP-01/19/20共用的偵測邏輯)，只mock最終的
    低檔島型反轉判定+組合函式驗證wiring。"""
    df = _trend_df(60, "up")
    idx = df.index
    gap_pos = len(idx) - 25
    df.loc[idx[gap_pos], ["open", "high", "low", "close"]] = [55, 58, 50, 56]
    for i in range(gap_pos + 1, len(idx) - 1):
        df.loc[idx[i], ["open", "high", "low", "close"]] = [55, 58, 52, 56]
    df.loc[idx[-1], ["open", "high", "low", "close"]] = [200, 205, 195, 202]
    monkeypatch.setattr(rule_scan, "detect_island_reversal_bottom", lambda *a, **k: {"category": "低檔島型反轉"})
    monkeypatch.setattr(rule_scan, "island_reversal", lambda sig: "島型反轉，強烈低檔反轉訊號" if sig else None)

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "島型反轉" in results["R-CLASSIC-32"]


def test_scan_golden_tier_reports_candle01_when_prev_bar_signal_not_neutral(monkeypatch):
    """R-CANDLE-01：前一日高低點支撐壓力，直接mock底層prev_bar_support_resistance_signal
    驗證wiring，判斷邏輯本身已有專屬測試。"""
    df = _trend_df(60, "up")
    monkeypatch.setattr(
        rule_scan, "prev_bar_support_resistance_signal",
        lambda close, high, low, lookback=1: pd.Series(["買方力量轉強"] * len(df), index=df.index),
    )

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert results["R-CANDLE-01"] == "買方力量轉強"


def test_scan_golden_tier_skips_candle01_when_prev_bar_signal_neutral(monkeypatch):
    df = _trend_df(60, "up")
    monkeypatch.setattr(
        rule_scan, "prev_bar_support_resistance_signal",
        lambda close, high, low, lookback=1: pd.Series(["多空未表態"] * len(df), index=df.index),
    )

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "R-CANDLE-01" not in results


def test_scan_golden_tier_reports_candle04_breakout_up(monkeypatch):
    """R-CANDLE-04：橫盤突破確認，直接mock底層detect_consolidation_breakout驗證wiring，
    橫盤/突破判斷邏輯本身已有tests/test_consolidation.py專屬測試。"""
    df = _trend_df(60, "up")
    fake_box = pd.DataFrame(
        {
            "breakout_up": [False] * (len(df) - 1) + [True],
            "breakout_down": [False] * len(df),
            "upper_neckline": [110.0] * len(df),
            "lower_neckline": [95.0] * len(df),
            "is_consolidating": [False] * len(df),
            "group_len": [1] * len(df),
        },
        index=df.index,
    )
    monkeypatch.setattr(rule_scan, "detect_consolidation_breakout", lambda o, h, l, c, min_bars=20: fake_box)

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "橫盤突破確認" in results["R-CANDLE-04"]
    assert "110.00" in results["R-CANDLE-04"]


def test_scan_golden_tier_reports_candle04_breakdown(monkeypatch):
    df = _trend_df(60, "up")
    fake_box = pd.DataFrame(
        {
            "breakout_up": [False] * len(df),
            "breakout_down": [False] * (len(df) - 1) + [True],
            "upper_neckline": [110.0] * len(df),
            "lower_neckline": [95.0] * len(df),
            "is_consolidating": [False] * len(df),
            "group_len": [1] * len(df),
        },
        index=df.index,
    )
    monkeypatch.setattr(rule_scan, "detect_consolidation_breakout", lambda o, h, l, c, min_bars=20: fake_box)

    results = {item["rule_id"]: item["note"] for item in scan_golden_tier(df)}

    assert "橫盤跌破確認" in results["R-CANDLE-04"]
    assert "95.00" in results["R-CANDLE-04"]
