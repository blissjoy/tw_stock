import pandas as pd

from src.indicators.candle_patterns_3 import (
    falling_three_black_candles,
    falling_three_methods,
    one_star_two_yang,
    one_star_two_yin,
    rising_three_methods,
    rising_three_red_candles,
)


def test_rising_three_methods_variable_length_mid_section():
    # R-CANDLE-26: 起漲中長紅 -> 中段小K不破起漲棒最低點 -> 再一根中長紅突破起漲棒最高點
    open_ = pd.Series([100.0, 105.0, 104.0, 105.0])
    high = pd.Series([109.0, 105.5, 105.5, 112.5])
    low = pd.Series([99.0, 103.5, 103.5, 104.5])
    close = pd.Series([108.0, 104.0, 105.0, 112.0])
    result = rising_three_methods(open_, high, low, close)
    assert result.tolist() == [False, False, False, True]


def test_falling_three_methods_mirrors_rising():
    # R-CANDLE-29: 與上升三法完全鏡射
    open_ = pd.Series([110.0, 103.0, 104.0, 103.0])
    high = pd.Series([111.0, 104.5, 104.5, 103.5])
    low = pd.Series([99.0, 102.5, 102.5, 95.5])
    close = pd.Series([100.0, 104.0, 103.0, 96.0])
    result = falling_three_methods(open_, high, low, close)
    assert result.tolist() == [False, False, False, True]


def test_rising_three_red_candles_requires_large_volume():
    # R-CANDLE-28: 書中明文要求「大量」，3根連續紅K缺一根大量即不成立
    open_ = pd.Series([100.0, 100.0, 100.0, 100.0])
    close = pd.Series([99.0, 101.0, 102.0, 103.0])
    volume = pd.Series([1000.0, 2000.0, 2100.0, 2200.0])
    volume_ma = pd.Series([1000.0] * 4)
    result = rising_three_red_candles(open_, close, volume, volume_ma)
    assert result.tolist() == [False, False, False, True]


def test_falling_three_black_candles_does_not_require_volume():
    # R-CANDLE-31: 書中定義不要求大量，與上漲連3紅不對稱，不可自行補上大量門檻
    open_ = pd.Series([100.0, 100.0, 100.0, 100.0])
    close = pd.Series([101.0, 99.0, 98.0, 97.0])
    result = falling_three_black_candles(open_, close)
    assert result.tolist() == [False, False, False, True]


def test_one_star_two_yang_fixed_single_star_day():
    # R-CANDLE-27: 中長紅K + 恰1根星形K(小紅/小黑/變盤線) + 中長紅K，固定3根
    open_ = pd.Series([100.0, 106.0, 107.0])
    close = pd.Series([106.0, 107.0, 113.0])  # day1+6%(中長紅) day2+0.94%(小紅=星) day3+5.6%(中長紅)
    high = pd.Series([108.0, 109.0, 115.0])
    low = pd.Series([99.0, 105.0, 106.0])
    result = one_star_two_yang(open_, high, low, close)
    assert result.tolist() == [False, False, True]


def test_one_star_two_yin_mirrors_yang():
    open_ = pd.Series([106.0, 100.0, 99.0])
    close = pd.Series([100.0, 99.0, 93.0])  # day1-5.7%(中長黑) day2-1%(小黑=星) day3-6.1%(中長黑)
    high = pd.Series([107.0, 101.0, 100.0])
    low = pd.Series([99.0, 98.0, 92.0])
    result = one_star_two_yin(open_, high, low, close)
    assert result.tolist() == [False, False, True]
