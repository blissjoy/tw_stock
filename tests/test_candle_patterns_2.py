import pandas as pd

from src.indicators.candle_patterns_2 import (
    basic_reversal_at_high,
    basic_reversal_at_low,
    bearish_engulfing_at_high,
    bearish_engulfing_three_line_break,
    bearish_harami_at_high,
    bearish_piercing_at_high,
    bullish_cover_low,
    bullish_cover_low_strength,
    bullish_engulfing_at_low,
    bullish_harami_at_low,
    bullish_piercing_at_low,
    dark_cloud_cover,
    dark_cloud_cover_strength,
)


def test_basic_reversal_at_high():
    open_ = pd.Series([100.0, 104.0])
    close = pd.Series([104.0, 100.0])
    is_at_high = pd.Series([True, True])
    result = basic_reversal_at_high(open_, close, is_at_high)
    assert result.tolist() == [False, True]


def test_basic_reversal_at_low():
    open_ = pd.Series([100.0, 96.0])
    close = pd.Series([96.0, 100.0])
    is_at_low = pd.Series([True, True])
    result = basic_reversal_at_low(open_, close, is_at_low)
    assert result.tolist() == [False, True]


def test_dark_cloud_cover_and_strength():
    open_ = pd.Series([100.0, 112.0])
    high = pd.Series([111.0, 113.0])
    close = pd.Series([110.0, 104.0])
    is_at_high = pd.Series([True, True])
    pattern = dark_cloud_cover(open_, high, close, is_at_high)
    strength = dark_cloud_cover_strength(open_, close)
    assert pattern.tolist() == [False, True]
    assert pd.isna(strength.iloc[0])
    assert strength.iloc[1] == "強覆蓋"  # 收盤104 < 前一日紅K中點105


def test_bullish_cover_low_and_strength():
    # 前一日黑K中點=(110+100)/2=105，收盤107突破中點 -> 強覆蓋
    open_ = pd.Series([110.0, 98.0])
    low = pd.Series([99.0, 97.0])
    close = pd.Series([100.0, 107.0])
    is_at_low = pd.Series([True, True])
    pattern = bullish_cover_low(open_, low, close, is_at_low)
    strength = bullish_cover_low_strength(open_, close)
    assert pattern.tolist() == [False, True]
    assert strength.iloc[1] == "強覆蓋"


def test_bearish_harami_at_high():
    open_ = pd.Series([100.0, 105.0, 105.0])
    high = pd.Series([111.0, 108.0, 112.0])
    low = pd.Series([99.0, 100.0, 100.0])
    close = pd.Series([110.0, 106.0, 106.0])
    is_at_high = pd.Series([True, True, True])
    result = bearish_harami_at_high(open_, high, low, close, is_at_high)
    assert result.tolist() == [False, True, False]  # index2的前一根高點112突破index1的108，不再是母子懷抱


def test_bullish_harami_at_low():
    open_ = pd.Series([110.0, 104.0])
    high = pd.Series([111.0, 108.0])
    low = pd.Series([99.0, 100.0])
    close = pd.Series([100.0, 103.0])
    is_at_low = pd.Series([True, True])
    result = bullish_harami_at_low(open_, high, low, close, is_at_low)
    assert result.tolist() == [False, True]


def test_bearish_engulfing_at_high():
    open_ = pd.Series([100.0, 111.0])
    low = pd.Series([95.0, 90.0])
    close = pd.Series([110.0, 94.0])
    is_at_high = pd.Series([True, True])
    result = bearish_engulfing_at_high(open_, low, close, is_at_high)
    assert result.tolist() == [False, True]


def test_bearish_engulfing_three_line_break():
    close = pd.Series([25.0, 24.0, 23.0, 15.0])
    low = pd.Series([19.0, 18.0, 17.0, 16.0])
    result = bearish_engulfing_three_line_break(close, low, lookback=3)
    assert result.iloc[3] == True
    assert result.iloc[0] == False


def test_bullish_engulfing_at_low():
    open_ = pd.Series([110.0, 99.0])
    high = pd.Series([105.0, 120.0])
    close = pd.Series([100.0, 116.0])
    is_at_low = pd.Series([True, True])
    result = bullish_engulfing_at_low(open_, high, close, is_at_low)
    assert result.tolist() == [False, True]


def test_bearish_piercing_at_high():
    open_ = pd.Series([100.0, 105.0])
    low = pd.Series([98.0, 85.0])
    close = pd.Series([108.0, 90.0])
    is_at_high = pd.Series([True, True])
    result = bearish_piercing_at_high(open_, low, close, is_at_high)
    assert result.tolist() == [False, True]


def test_bullish_piercing_at_low():
    open_ = pd.Series([108.0, 102.0])
    high = pd.Series([110.0, 120.0])
    close = pd.Series([100.0, 115.0])
    is_at_low = pd.Series([True, True])
    result = bullish_piercing_at_low(open_, high, close, is_at_low)
    assert result.tolist() == [False, True]
