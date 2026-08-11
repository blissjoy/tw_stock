import pandas as pd

from src.screener.escape_signals import (
    ESCAPE_KD_DEATH_CROSS_RULE_ID,
    detect_kd_death_cross,
    is_escape_signal,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="B")


def test_is_escape_signal_true_for_pure_bearish_rule_ids():
    assert is_escape_signal("R-MA-14", "MA5下穿MA10，死亡交叉") is True
    assert is_escape_signal("R-CLASSIC-01", "任何note") is True


def test_is_escape_signal_false_for_bullish_rule_ids_not_in_curated_list():
    assert is_escape_signal("R-CANDLE-13", "低檔反轉K棒幾何成立") is False
    assert is_escape_signal("R-MA-13", "MA5上穿MA10，黃金交叉") is False


def test_is_escape_signal_uses_keyword_for_ambiguous_shared_rule_ids():
    assert is_escape_signal("R-STRATEGY-07", "多頭完成反轉，預期初期出現大跌") is True
    assert is_escape_signal("R-STRATEGY-07", "空頭完成反轉，預期初期出現大漲") is False
    assert is_escape_signal("R-INDICATOR-07", "股價頭頭高但OSC紅柱峰值頭頭低，趨勢級高檔背離，提示多轉空") is True
    assert is_escape_signal("R-INDICATOR-07", "股價底底低但OSC綠柱谷值(絕對值)底底高，趨勢級低檔背離，提示空轉多") is False
    assert is_escape_signal("R-VOLPRICE-06", "空方換手失敗，持續下跌") is True
    assert is_escape_signal("R-VOLPRICE-06", "多方力量轉強，反彈確認") is False


def test_is_escape_signal_handles_missing_note():
    assert is_escape_signal("R-STRATEGY-07", None) is False


def test_detect_kd_death_cross_true_when_k_crosses_below_d():
    # 先漲後急跌，K反應比D快，急跌當天(最後一天)K由上往下穿越D——實際交叉點以
    # compute_kd()的平滑計算結果為準，這裡先跑過一次確認交叉真的落在最後一天。
    high = [100.0 + i for i in range(15)] + [114.0]
    low = [98.0 + i for i in range(15)] + [112.0]
    close = [99.0 + i for i in range(15)] + [113.0]
    dates = _dates(len(close))
    df = pd.DataFrame({"high": high, "low": low, "close": close}, index=dates)

    assert detect_kd_death_cross(df) is True


def test_detect_kd_death_cross_false_when_not_enough_data():
    df = pd.DataFrame({"high": [1, 2], "low": [1, 2], "close": [1, 2]}, index=_dates(2))
    assert detect_kd_death_cross(df) is False


def test_escape_kd_death_cross_rule_id_is_distinct_constant():
    assert ESCAPE_KD_DEATH_CROSS_RULE_ID == "R-ESCAPE-KD-DEATH-CROSS"
