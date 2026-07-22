"""均線分類：葛蘭碧8大法則——買點四大法則（R-MA-19）與賣點四大法則（R-MA-20）。

全部以股價與20日均線（MA20）的相對關係與方向判斷，做多／做空兩組規則完全鏡射對稱。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.moving_average import ma_direction
from src.rule_registry import implements_rule


def _consecutive_true_streak(flags: pd.Series) -> pd.Series:
    """算出布林 Series 中，每一天為止「連續為 True」的天數（False 時歸零）。"""
    group = (~flags.fillna(False)).cumsum()
    streak = flags.fillna(False).groupby(group).cumcount() + 1
    return streak.where(flags.fillna(False), 0)


def bias_pct(close: pd.Series, ma20: pd.Series) -> pd.Series:
    """乖離率：收盤價偏離MA20的百分比。"""
    return (close - ma20) / ma20 * 100


@implements_rule("R-MA-19")
def granville_buy_signal_1(close: pd.Series, ma20: pd.Series) -> pd.Series:
    """買點①落底反轉：MA20不再下彎（走平或上揚），前一日收盤仍在均線下方，當日收盤向上突破。"""
    direction = ma_direction(ma20)
    direction_ok = direction != "下彎"
    prev_below = close.shift(1) < ma20.shift(1)
    cross_up = close > ma20
    return (direction_ok & prev_below & cross_up).fillna(False)


@implements_rule("R-MA-19")
def granville_buy_signal_2(
    close: pd.Series, low: pd.Series, ma20: pd.Series, proximity_pct: float = 2.0
) -> pd.Series:
    """買點②多頭段回測支撐：均線上揚、股價回檔貼近MA20但未跌破、隨後轉而上漲。可反覆出現。"""
    direction = ma_direction(ma20)
    direction_up = direction == "上揚"
    above_ma = close > ma20
    recent_low = low.rolling(window=3, min_periods=1).min()
    touched_support = (recent_low >= ma20) & ((recent_low - ma20) / ma20 * 100 <= proximity_pct)
    turned_up = close > close.shift(1)
    return (direction_up & above_ma & touched_support & turned_up).fillna(False)


@implements_rule("R-MA-19")
def granville_buy_signal_3(close: pd.Series, ma20: pd.Series, lookback: int = 3) -> pd.Series:
    """買點③短暫跌破快速收復：均線持續上揚，近期曾短暫跌破MA20，當日已收復站上。"""
    direction_up = ma_direction(ma20) == "上揚"
    broke_recently = pd.Series(False, index=close.index)
    for d in range(1, lookback + 1):
        broke_recently |= close.shift(d) < ma20.shift(d)
    recovered = close > ma20
    return (direction_up & broke_recently & recovered).fillna(False)


@implements_rule("R-MA-19")
def granville_buy_signal_4(
    close: pd.Series,
    ma20: pd.Series,
    is_bear_trend: pd.Series,
    down_streak_days: int = 3,
    bias_threshold_pct: float = -15.0,
) -> pd.Series:
    """買點④乖離過大逆勢搶反彈：空頭中連跌N天、負乖離過大，股價開始反彈。僅短打，非趨勢反轉。"""
    down_flags = close < close.shift(1)
    streak_before_today = _consecutive_true_streak(down_flags).shift(1)
    below_ma = close < ma20
    bias_extreme = bias_pct(close, ma20) <= bias_threshold_pct
    rebounding = close > close.shift(1)
    signal = (
        is_bear_trend.astype(bool)
        & below_ma
        & (streak_before_today >= down_streak_days)
        & bias_extreme
        & rebounding
    )
    return signal.fillna(False)


@implements_rule("R-MA-20")
def granville_sell_signal_1(close: pd.Series, ma20: pd.Series) -> pd.Series:
    """賣點①盤頭反轉：MA20不再上揚（走平或下彎），前一日收盤仍在均線上方，當日收盤向下跌破。"""
    direction = ma_direction(ma20)
    direction_ok = direction != "上揚"
    prev_above = close.shift(1) > ma20.shift(1)
    cross_down = close < ma20
    return (direction_ok & prev_above & cross_down).fillna(False)


@implements_rule("R-MA-20")
def granville_sell_signal_2(
    close: pd.Series, high: pd.Series, ma20: pd.Series, proximity_pct: float = 2.0
) -> pd.Series:
    """賣點②空頭段反彈遇壓：均線下彎、股價反彈貼近MA20但未突破、隨後轉而下跌。可反覆出現。"""
    direction_down = ma_direction(ma20) == "下彎"
    below_ma = close < ma20
    recent_high = high.rolling(window=3, min_periods=1).max()
    touched_resistance = (recent_high <= ma20) & ((ma20 - recent_high) / ma20 * 100 <= proximity_pct)
    turned_down = close < close.shift(1)
    return (direction_down & below_ma & touched_resistance & turned_down).fillna(False)


@implements_rule("R-MA-20")
def granville_sell_signal_3(close: pd.Series, ma20: pd.Series, lookback: int = 3) -> pd.Series:
    """賣點③短暫突破快速回跌：均線持續下彎，近期曾短暫突破MA20，當日已回跌至均線下方。"""
    direction_down = ma_direction(ma20) == "下彎"
    broke_recently = pd.Series(False, index=close.index)
    for d in range(1, lookback + 1):
        broke_recently |= close.shift(d) > ma20.shift(d)
    fell_back = close < ma20
    return (direction_down & broke_recently & fell_back).fillna(False)


@implements_rule("R-MA-20")
def granville_sell_signal_4(
    close: pd.Series,
    ma20: pd.Series,
    is_bull_trend: pd.Series,
    up_streak_days: int = 3,
    bias_threshold_pct: float = 15.0,
) -> pd.Series:
    """賣點④乖離過大逆勢搶跌：多頭中連漲N天、正乖離過大，股價開始回檔。僅短打，非趨勢反轉。"""
    up_flags = close > close.shift(1)
    streak_before_today = _consecutive_true_streak(up_flags).shift(1)
    above_ma = close > ma20
    bias_extreme = bias_pct(close, ma20) >= bias_threshold_pct
    pulling_back = close < close.shift(1)
    signal = (
        is_bull_trend.astype(bool)
        & above_ma
        & (streak_before_today >= up_streak_days)
        & bias_extreme
        & pulling_back
    )
    return signal.fillna(False)
