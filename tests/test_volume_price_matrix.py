import pandas as pd

from src.indicators.volume_price_matrix import (
    Q1_BREAKOUT_DOWN,
    Q1_BREAKOUT_UP,
    Q1_DOWN,
    Q1_RANGE,
    Q1_UP,
    Q2_DOWN,
    Q2_FLAT,
    Q2_UP,
    Q3_HIGH,
    Q3_LOW,
    Q3_MID,
    classify_matrix_row,
    classify_q1_price,
    classify_q2_volume,
    classify_q3_position,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="B")


def test_classify_q1_price_up_down_flat():
    dates = _dates(4)
    close = pd.Series([10.0, 11.0, 10.5, 10.5], index=dates)

    q1 = classify_q1_price(close)

    assert q1.iloc[1] == Q1_UP
    assert q1.iloc[2] == Q1_DOWN
    assert q1.iloc[3] == Q1_RANGE  # 平盤且沒有breakout旗標


def test_classify_q1_price_breakout_overrides_up_down():
    dates = _dates(2)
    close = pd.Series([10.0, 11.0], index=dates)
    breakout_up = pd.Series([False, True], index=dates)

    q1 = classify_q1_price(close, breakout_up=breakout_up)

    assert q1.iloc[1] == Q1_BREAKOUT_UP


def test_classify_q2_volume_thresholds_match_r_volprice_01():
    dates = _dates(3)
    volume = pd.Series([1200.0, 500.0, 800.0], index=dates)
    ma5_volume = pd.Series([1000.0, 1000.0, 1000.0], index=dates)

    q2 = classify_q2_volume(volume, ma5_volume)

    assert q2.iloc[0] == Q2_UP  # 1200/1000=1.2 -> 量增門檻
    assert q2.iloc[1] == Q2_DOWN  # 500/1000=0.5 -> 量縮門檻
    assert q2.iloc[2] == Q2_FLAT  # 0.8，介於中間


def test_classify_q3_position_from_trend_position_flags():
    dates = _dates(3)
    is_at_high = pd.Series([True, False, False], index=dates)
    is_at_low = pd.Series([False, True, False], index=dates)

    q3 = classify_q3_position(is_at_high, is_at_low)

    assert q3.iloc[0] == Q3_HIGH
    assert q3.iloc[1] == Q3_LOW
    assert q3.iloc[2] == Q3_MID


def test_classify_matrix_row_01_up_volume_up_at_low():
    row = classify_matrix_row(Q1_UP, Q2_UP, Q3_LOW)
    assert row is not None
    assert row.rule_id == "R-Q3M-01"


def test_classify_matrix_row_pullback_needs_trend_today_to_disambiguate_08_vs_09():
    bull_row = classify_matrix_row(Q1_DOWN, Q2_DOWN, Q3_MID, trend_today="多頭")
    bear_row = classify_matrix_row(Q1_DOWN, Q2_DOWN, Q3_MID, trend_today="空頭")
    unknown_row = classify_matrix_row(Q1_DOWN, Q2_DOWN, Q3_MID, trend_today=None)

    assert bull_row.rule_id == "R-Q3M-08"
    assert bear_row.rule_id == "R-Q3M-09"
    assert unknown_row is None


def test_classify_matrix_row_breakout_ignores_q3():
    row_high = classify_matrix_row(Q1_BREAKOUT_UP, Q2_UP, Q3_HIGH)
    row_low = classify_matrix_row(Q1_BREAKOUT_UP, Q2_UP, Q3_LOW)

    assert row_high.rule_id == "R-Q3M-11"
    assert row_low.rule_id == "R-Q3M-11"


def test_classify_matrix_row_no_match_for_undefined_combo():
    # 「盤整+關鍵點」PDF原文沒有定義的組合
    row = classify_matrix_row(Q1_RANGE, Q2_UP, Q3_MID)
    assert row is None or row.rule_id in ("R-Q3M-16", "R-Q3M-17")
    # 盤整+量增只在有明確高/低檔時才有定義(16/17)，全基期沒有定義是合理的None
    row_mid = classify_matrix_row(Q1_RANGE, Q2_UP, Q3_MID)
    assert row_mid is None
