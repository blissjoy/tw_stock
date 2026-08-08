import pandas as pd
import pytest

from src.patterns.latest_day_summary import (
    classify_latest_candle_name,
    detect_latest_day_candle_patterns,
    detect_latest_day_volume_signals,
    summarize_latest_day,
    summarize_volume_vs_ma5,
)


def _df(rows: list[dict]) -> pd.DataFrame:
    # trend_state.classify_trend_states_multi_horizon()要resample成週線/月線，需要
    # DatetimeIndex(跟chart_data.load_price_history()回傳的真實資料一致的慣例)，
    # 不能用預設的RangeIndex，否則.resample()會直接拋TypeError。
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2026-01-01", periods=len(rows), freq="B")
    return df


def _flat_row(close: float = 100.0, volume: float = 1000.0) -> dict:
    return {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": volume}


def _swing_rows(start: float, mid: float, end: float, leg1_days: int, leg2_days: int) -> list[dict]:
    """造出一段「先走到mid、再走到end」的連續價格路徑，供is_at_high/is_at_low需要的「先有一段
    >=10%真實波段」測試資料使用(見src/indicators/trend_position.py的MIN_SWING_PCT門檻)。
    start->mid->end分別代表兩段走勢的起訖價，方向由正負差自動決定(可以是漲後跌或跌後漲)。"""
    rows: list[dict] = []
    for leg_start, leg_end, days in ((start, mid, leg1_days), (mid, end, leg2_days)):
        step = (leg_end - leg_start) / days
        for i in range(days):
            c = leg_start + step * (i + 1)
            rows.append({
                "open": c - step * 0.5,
                "high": max(c, c - step) + 0.3,
                "low": min(c, c - step) - 0.3,
                "close": c,
                "volume": 1000,
            })
    return rows


def test_classify_latest_candle_name_long_red():
    df = _df([{"open": 100.0, "high": 115.0, "low": 95.0, "close": 107.0, "volume": 1000}])
    assert classify_latest_candle_name(df) == "長紅K"


def test_classify_latest_candle_name_long_black():
    df = _df([{"open": 107.0, "high": 112.0, "low": 92.0, "close": 100.0, "volume": 1000}])
    assert classify_latest_candle_name(df) == "長黑K"


def test_classify_latest_candle_name_doji():
    df = _df([{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000}])
    assert classify_latest_candle_name(df) == "十字線"


def test_classify_latest_candle_name_hammer():
    df = _df([{"open": 100.0, "high": 103.0, "low": 90.0, "close": 102.0, "volume": 1000}])
    assert classify_latest_candle_name(df) == "槌子線"


def test_classify_latest_candle_name_inverted_hammer():
    df = _df([{"open": 100.0, "high": 113.0, "low": 99.0, "close": 98.0, "volume": 1000}])
    assert classify_latest_candle_name(df) == "倒槌子線"


def test_detect_latest_day_candle_patterns_basic_reversal_at_high():
    # 沿用 tests/test_candle_patterns_2.py 已驗證過的型態資料(open_=[100,104], close=[104,100])，
    # 前面墊一段先跌後漲(80->104，漲幅30%)的真實波段，讓is_at_high(見trend_position.py)能
    # 判定「打平在高點」這段確實處於本波段高檔，不是只有幾何型態成立、位置條件仍是預設值。
    rows = _swing_rows(100, 80, 104, 20, 20) + [_flat_row(104.0) for _ in range(5)] + [
        {"open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0, "volume": 1000},
        {"open": 104.0, "high": 105.0, "low": 99.0, "close": 100.0, "volume": 1000},
    ]
    df = _df(rows)
    hits = detect_latest_day_candle_patterns(df)
    assert "基本反轉（高檔）" in hits


def test_detect_latest_day_candle_patterns_falling_three_black_candles():
    rows = [_flat_row(120.0) for _ in range(5)] + [
        {"open": 110.0, "high": 111.0, "low": 104.0, "close": 105.0, "volume": 1000},
        {"open": 105.0, "high": 106.0, "low": 99.0, "close": 100.0, "volume": 1000},
        {"open": 100.0, "high": 101.0, "low": 94.0, "close": 95.0, "volume": 1000},
    ]
    df = _df(rows)
    hits = detect_latest_day_candle_patterns(df)
    assert "下跌連3黑" in hits


def test_detect_latest_day_candle_patterns_evening_star():
    # 前面墊一段先跌後漲(80->100，漲幅25%)的真實波段，讓is_at_high判定完成夜星那天(右側
    # 長黑K收盤)仍在本波段高檔容忍帶內——反轉完成日本身就是離開高點的那天，見
    # trend_position.py docstring說明為何刻意沿用「翻轉前」狀態判斷。
    rows = _swing_rows(120, 80, 100, 20, 20) + [_flat_row(100.0) for _ in range(3)] + [
        {"open": 100.0, "high": 109.0, "low": 99.0, "close": 108.0, "volume": 1000},   # 左：中長紅(>3.5%)
        {"open": 108.5, "high": 109.5, "low": 108.0, "close": 108.7, "volume": 1000},  # 中：小紅/星形
        {"open": 108.0, "high": 108.5, "low": 100.0, "close": 101.0, "volume": 1000},  # 右：中長黑(>3.5%)
    ]
    df = _df(rows)
    hits = detect_latest_day_candle_patterns(df)
    assert "夜星" in hits


def test_detect_latest_day_candle_patterns_morning_star():
    # 鏡射版本：先漲後跌(80->120->100)，讓is_at_low判定完成晨星那天(右側長紅K收盤)仍在
    # 本波段低檔容忍帶內。
    rows = _swing_rows(80, 120, 100, 20, 20) + [_flat_row(100.0) for _ in range(3)] + [
        {"open": 108.0, "high": 109.0, "low": 99.0, "close": 100.0, "volume": 1000},   # 左：中長黑(>3.5%)
        {"open": 99.5, "high": 100.0, "low": 99.0, "close": 99.7, "volume": 1000},     # 中：小紅/星形
        {"open": 100.0, "high": 108.5, "low": 99.5, "close": 107.0, "volume": 1000},   # 右：中長紅(>3.5%)
    ]
    df = _df(rows)
    hits = detect_latest_day_candle_patterns(df)
    assert "晨星" in hits


def test_detect_latest_day_candle_patterns_empty_when_too_short():
    df = _df([_flat_row()])
    assert detect_latest_day_candle_patterns(df) == []


def test_detect_latest_day_volume_signals_attack_volume():
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(5)] + [
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1300},  # 前5日均量1000的1.3倍
    ]
    df = _df(rows)
    hits = detect_latest_day_volume_signals(df)
    assert any("攻擊量" in h for h in hits)


def test_detect_latest_day_volume_signals_big_volume_vs_prev_day():
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(3)] + [
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 2500},  # 前一日量的2倍以上
    ]
    df = _df(rows)
    hits = detect_latest_day_volume_signals(df)
    assert any("爆量" in h for h in hits)


def test_detect_latest_day_volume_signals_pothole_volume():
    """R-VOLPRICE-07凹洞量：大黑K(day-3，量3000=3倍ma5量1000)→隔日窒息量確認(day-2，量1400
    <=大黑K量的一半)→再隔日(day-1/今天)量增(1500>1400)紅K收復，應該觸發凹洞量。"""
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(5)] + [
        {"open": 100.0, "high": 101.0, "low": 94.0, "close": 95.0, "volume": 3000},  # day-3大黑K(-5%)+爆量
        {"open": 94.0, "high": 94.5, "low": 89.0, "close": 90.0, "volume": 1400},  # day-2續跌+量縮(窒息量成立)
        {"open": 90.0, "high": 93.5, "low": 89.5, "close": 93.0, "volume": 1500},  # day-1(今天)量增紅K收復
    ]
    df = _df(rows)
    hits = detect_latest_day_volume_signals(df)
    assert any("凹洞量" in h for h in hits)


def test_detect_latest_day_volume_signals_no_pothole_when_too_short():
    """不足3天資料時，凹洞量判斷需要的「窒息量錨點」抓不到，不應該觸發也不應該crash。"""
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(2)]
    df = _df(rows)
    hits = detect_latest_day_volume_signals(df)
    assert not any("凹洞量" in h for h in hits)


def test_detect_latest_day_volume_signals_empty_when_too_short():
    df = _df([_flat_row()])
    assert detect_latest_day_volume_signals(df) == []


def test_summarize_volume_vs_ma5_returns_none_when_too_short():
    """basic_volume(n=5)需要至少5天才有值，資料不足時不該給出誤導性結論。"""
    df = _df([_flat_row() for _ in range(4)])
    assert summarize_volume_vs_ma5(df) is None


def test_summarize_volume_vs_ma5_attack_volume():
    # ⚠️ basic_volume()的rolling(5)包含當天自己，5日均量不是「前5天flat值1000」，而是
    # (1000*4+1300)/5=1060，ratio=1300/1060≈1.226，不是天真地當成1300/1000=1.3。
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(5)] + [
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1300},
    ]
    df = _df(rows)
    result = summarize_volume_vs_ma5(df)
    assert result["is_above"] is True
    assert result["ratio"] == pytest.approx(1300 / 1060)
    assert "攻擊量" in result["note"]


def test_summarize_volume_vs_ma5_explosive_at_high_means_distribution_warning():
    """爆大量(2倍以上)出現在高檔，依朱老師原理要提防主力出貨，不是單純偏多訊號。
    ⚠️ 用4000(不是2500)才能扣掉rolling自己算進去的稀釋效果、確實跨過2倍門檻
    (見test_summarize_volume_vs_ma5_attack_volume的rolling說明)。"""
    rows = _swing_rows(100, 80, 104, 20, 20) + [_flat_row(104.0, volume=1000.0) for _ in range(5)] + [
        {"open": 104.0, "high": 108.0, "low": 103.0, "close": 105.0, "volume": 4000},
    ]
    df = _df(rows)
    result = summarize_volume_vs_ma5(df)
    assert result["is_above"] is True
    assert result["ratio"] == pytest.approx(4000 / 1600)
    assert "爆大量" in result["note"] and "出貨" in result["note"]


def test_summarize_volume_vs_ma5_explosive_not_at_high_means_bullish_attack():
    """爆大量出現在起漲位置(不是高檔)，依朱老師原理仍屬偏多的攻擊性大量。"""
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(5)] + [
        {"open": 100.0, "high": 108.0, "low": 99.0, "close": 105.0, "volume": 4000},
    ]
    df = _df(rows)
    result = summarize_volume_vs_ma5(df)
    assert result["is_above"] is True
    assert result["ratio"] == pytest.approx(4000 / 1600)
    assert "爆大量" in result["note"] and "攻擊性大量" in result["note"]


def test_summarize_volume_vs_ma5_stop_fall_volume():
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(5)] + [
        {"open": 100.0, "high": 100.5, "low": 98.0, "close": 99.0, "volume": 400},
    ]
    df = _df(rows)
    result = summarize_volume_vs_ma5(df)
    assert result["is_above"] is False
    assert result["ratio"] == pytest.approx(400 / 880)
    assert "止跌量" in result["note"]


def test_summarize_volume_vs_ma5_general_low_volume():
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(5)] + [
        {"open": 100.0, "high": 100.5, "low": 99.0, "close": 99.5, "volume": 700},  # 縮量但沒到0.5倍
    ]
    df = _df(rows)
    result = summarize_volume_vs_ma5(df)
    assert result["is_above"] is False
    assert "觀望" in result["note"]


def test_summarize_latest_day_includes_volume_vs_ma5_key():
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(5)] + [
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1300},
    ]
    df = _df(rows)
    summary = summarize_latest_day(df)
    assert summary["volume_vs_ma5"] is not None
    assert summary["volume_vs_ma5"]["is_above"] is True


def test_summarize_latest_day_returns_empty_structure_for_empty_df():
    result = summarize_latest_day(pd.DataFrame())
    assert result == {"candle_name": None, "patterns": [], "volume_signals": [], "volume_vs_ma5": None, "trend": None}


def test_summarize_latest_day_combines_all_parts():
    rows = [_flat_row(100.0) for _ in range(5)] + [
        {"open": 100.0, "high": 115.0, "low": 95.0, "close": 107.0, "volume": 1300},
    ]
    df = _df(rows)
    result = summarize_latest_day(df)
    assert result["candle_name"] == "長紅K"
    assert isinstance(result["patterns"], list)
    assert isinstance(result["volume_signals"], list)
    # trend是短/中/長三種天期各自的判斷結果(見trend_state.classify_trend_states_multi_horizon)，
    # 不是單一字串。resample成週線/月線需要DatetimeIndex，df沒有日期索引時
    # (`_df()`用預設RangeIndex)，週/中/長三個天期會直接算出「盤整」而不是crash——
    # 這裡只驗證結構正確，資料量/索引不足以支撐真正的趨勢判斷不在這個測試的範圍內。
    assert set(result["trend"].keys()) == {"短期", "中期", "長期"}
    assert result["trend"]["短期"].timeframe == "日線"
    assert result["trend"]["中期"].timeframe == "週線"
    assert result["trend"]["長期"].timeframe == "月線"
    for horizon in result["trend"].values():
        assert horizon.trend in ("多頭", "空頭", "盤整")
        assert isinstance(horizon.reason, str) and horizon.reason  # 一定要有非空的判斷依據文字


def test_summarize_latest_day_uses_trend_df_for_trend_classification_when_given():
    """trend_df有給的話，trend欄位應該用trend_df(通常涵蓋更長歷史，見chart_data.py的
    TREND_LOOKBACK_DAYS)算，不是用df本身(可能只是顯示窗口截出來的一小段)——
    這是週線/月線需要足夠長日線歷史才能重新取樣出夠多根K棒的直接後果。"""
    rows = [_flat_row(100.0) for _ in range(5)] + [
        {"open": 100.0, "high": 115.0, "low": 95.0, "close": 107.0, "volume": 1300},
    ]
    df = _df(rows)

    dates = pd.date_range("2024-01-01", periods=400, freq="B")
    trend_rows = pd.DataFrame(
        {"open": [100.0] * 400, "high": [101.0] * 400, "low": [99.0] * 400, "close": [100.0] * 400,
         "volume": [1000] * 400},
        index=dates,
    )

    result = summarize_latest_day(df, trend_df=trend_rows)

    # candle_name/patterns/volume_signals仍然是df(最後一列)算出來的，不受trend_df影響
    assert result["candle_name"] == "長紅K"
    assert set(result["trend"].keys()) == {"短期", "中期", "長期"}
