"""K棒型態分類：多根K線的中繼／續勢組合型態（R-CANDLE-26/28/29/31）。

上升三法／下降三法的中段K棒數量不固定（書中僅要求「1到數天」），是天生路徑相依的型態，
用逐列迴圈掃描每個可能的中段長度來判斷，而非嘗試硬套固定窗口的向量化寫法（會犧牲正確性）。
上漲連3紅／下跌連3黑則是固定3根，直接向量化。書中對這兩者「是否要求大量」定義本身不對稱
（連3紅明文要求大量、連3黑沒有），程式化時原樣保留這個不對稱，不能自行補平。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.candles import (
    is_black_candle,
    is_mid_long_black_candle,
    is_mid_long_red_candle,
    is_red_candle,
    is_small_black_candle,
    is_small_red_candle,
    is_spindle_candle,
)
from src.rule_registry import implements_rule


@implements_rule("R-CANDLE-26")
def rising_three_methods(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, max_mid: int = 5
) -> pd.Series:
    """上升三法：起漲中長紅 → 1~max_mid根小K/變盤線皆不破起漲棒最低點（母子懷抱）→ 再一根中長紅突破起漲棒最高點。"""
    is_mid_long_red = is_mid_long_red_candle(open_, close)
    is_small_or_reversal = (
        is_small_red_candle(open_, close) | is_small_black_candle(open_, close) | is_spindle_candle(open_, high, low, close)
    )
    n = len(close)
    result = pd.Series(False, index=close.index)
    for t in range(n):
        if not is_mid_long_red.iloc[t]:
            continue
        for k in range(1, max_mid + 1):
            day1 = t - k - 1
            if day1 < 0:
                break
            if not is_mid_long_red.iloc[day1]:
                continue
            day1_low, day1_high = low.iloc[day1], high.iloc[day1]
            mid_ok = all(
                bool(is_small_or_reversal.iloc[s]) and close.iloc[s] >= day1_low for s in range(day1 + 1, t)
            )
            if mid_ok and close.iloc[t] > day1_high:
                result.iloc[t] = True
                break
    return result


@implements_rule("R-CANDLE-29")
def falling_three_methods(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, max_mid: int = 5
) -> pd.Series:
    """下降三法：與上升三法完全鏡射，起跌中長黑 → 中段不過起跌棒最高點 → 再一根中長黑跌破起跌棒最低點。"""
    is_mid_long_black = is_mid_long_black_candle(open_, close)
    is_small_or_reversal = (
        is_small_red_candle(open_, close) | is_small_black_candle(open_, close) | is_spindle_candle(open_, high, low, close)
    )
    n = len(close)
    result = pd.Series(False, index=close.index)
    for t in range(n):
        if not is_mid_long_black.iloc[t]:
            continue
        for k in range(1, max_mid + 1):
            day1 = t - k - 1
            if day1 < 0:
                break
            if not is_mid_long_black.iloc[day1]:
                continue
            day1_low, day1_high = low.iloc[day1], high.iloc[day1]
            mid_ok = all(
                bool(is_small_or_reversal.iloc[s]) and close.iloc[s] <= day1_high for s in range(day1 + 1, t)
            )
            if mid_ok and close.iloc[t] < day1_low:
                result.iloc[t] = True
                break
    return result


@implements_rule("R-CANDLE-28")
def rising_three_red_candles(open_: pd.Series, close: pd.Series, volume: pd.Series, volume_ma: pd.Series, volume_multiple: float = 1.5) -> pd.Series:
    """上漲連3紅：3根連續帶大量的紅K實體棒。書中原文明確要求大量，是與下跌連3黑的關鍵不對稱處。"""
    red = is_red_candle(open_, close)
    large_vol = volume > volume_multiple * volume_ma
    all_red = red & red.shift(1) & red.shift(2)
    all_large_vol = large_vol & large_vol.shift(1) & large_vol.shift(2)
    return (all_red & all_large_vol).fillna(False)


@implements_rule("R-CANDLE-31")
def falling_three_black_candles(open_: pd.Series, close: pd.Series) -> pd.Series:
    """下跌連3黑：3根連續向下黑K實體棒。書中定義不要求大量，不可自行補上大量作為必要條件。"""
    black = is_black_candle(open_, close)
    return (black & black.shift(1) & black.shift(2)).fillna(False)


@implements_rule("R-CANDLE-27")
def one_star_two_yang(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """一星二陽：中長紅K + 恰1根小黑/小紅/變盤線(星) + 中長紅K，固定3根，中段星形天數固定為1，
    與上升三法(中段1~N根可變)是兩者最大的結構差異。"""
    mid_long_red = is_mid_long_red_candle(open_, close)
    star = is_small_red_candle(open_, close) | is_small_black_candle(open_, close) | is_spindle_candle(open_, high, low, close)
    return (mid_long_red.shift(2) & star.shift(1) & mid_long_red).fillna(False)


@implements_rule("R-CANDLE-30")
def one_star_two_yin(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """一星二陰：與一星二陽完全鏡射，中長黑K + 恰1根星形K + 中長黑K。"""
    mid_long_black = is_mid_long_black_candle(open_, close)
    star = is_small_red_candle(open_, close) | is_small_black_candle(open_, close) | is_spindle_candle(open_, high, low, close)
    return (mid_long_black.shift(2) & star.shift(1) & mid_long_black).fillna(False)
