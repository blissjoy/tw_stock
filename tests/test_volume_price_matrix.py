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
    classify_price_direction_basic,
    classify_q1_price,
    classify_q2_volume,
    classify_q3_position,
    format_volume_price_relation,
    is_volume_price_divergence,
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


def test_classify_matrix_row_sub_stage_rows_carry_caveat_for_ui_display():
    """使用者2026-08-10反映：子階段判斷的侷限不能只寫在文件裡，要能直接從資料
    取得，供UI顯示。R-Q3M-01(低檔初升段)只能程式化到「低檔」，caveat要非空；
    R-Q3M-05(價漲量平·全基期)沒有子階段侷限，caveat應該是None。"""
    row_01 = classify_matrix_row(Q1_UP, Q2_UP, Q3_LOW)
    row_05 = classify_matrix_row(Q1_UP, Q2_FLAT, Q3_MID)

    assert row_01.caveat is not None and "子階段" in row_01.caveat
    assert row_05.caveat is None


def test_classify_matrix_row_pullback_rows_carry_caveat():
    row_08 = classify_matrix_row(Q1_DOWN, Q2_DOWN, Q3_MID, trend_today="多頭")
    assert row_08.caveat is not None


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


def test_classify_price_direction_basic_ignores_breakout_unlike_classify_q1_price():
    """使用者2026-08-10拿合晶(6182)實測發現：當天股價下跌又剛好觸發盤整跌破，
    classify_q1_price()回傳「關鍵點向下跌破」，但「今日量價關係」這句話需要的是
    單純的漲跌(classify_price_direction_basic())，不受同時發生的突破事件影響。"""
    dates = _dates(2)
    close = pd.Series([10.0, 9.0], index=dates)
    breakout_down = pd.Series([False, True], index=dates)

    q1_for_matrix = classify_q1_price(close, breakout_down=breakout_down)
    q1_basic = classify_price_direction_basic(close)

    assert q1_for_matrix.iloc[1] == Q1_BREAKOUT_DOWN
    assert q1_basic.iloc[1] == Q1_DOWN


def test_is_volume_price_divergence_flags_price_up_volume_down_and_price_down_volume_up():
    """使用者2026-08-10拿合晶(6182)「價跌量增(背離)」的實例反映：這種背離標記
    我們系統本來就算得出Q1/Q2，只是沒有組成這句話顯示。"""
    assert is_volume_price_divergence(Q1_UP, Q2_DOWN) is True  # 價漲量縮
    assert is_volume_price_divergence(Q1_DOWN, Q2_UP) is True  # 價跌量增
    assert is_volume_price_divergence(Q1_UP, Q2_UP) is False  # 價漲量增，量價配合
    assert is_volume_price_divergence(Q1_DOWN, Q2_DOWN) is False  # 價跌量縮，量價配合
    assert is_volume_price_divergence(Q1_RANGE, Q2_UP) is False


def test_format_volume_price_relation_matches_pdf_wording():
    assert format_volume_price_relation(Q1_UP, Q2_DOWN) == "價漲量縮(背離)"
    assert format_volume_price_relation(Q1_DOWN, Q2_UP) == "價跌量增(背離)"
    assert format_volume_price_relation(Q1_UP, Q2_UP) == "價漲量增"
    assert format_volume_price_relation(Q1_DOWN, Q2_FLAT) == "價跌量平"
    assert format_volume_price_relation(Q1_RANGE, Q2_UP) is None
    assert format_volume_price_relation(Q1_BREAKOUT_UP, Q2_UP) is None


def test_classify_matrix_row_no_match_for_undefined_combo():
    # 「盤整+關鍵點」PDF原文沒有定義的組合
    row = classify_matrix_row(Q1_RANGE, Q2_UP, Q3_MID)
    assert row is None or row.rule_id in ("R-Q3M-16", "R-Q3M-17")
    # 盤整+量增只在有明確高/低檔時才有定義(16/17)，全基期沒有定義是合理的None
    row_mid = classify_matrix_row(Q1_RANGE, Q2_UP, Q3_MID)
    assert row_mid is None
