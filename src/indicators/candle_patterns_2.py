"""K棒型態分類：高檔／低檔的雙K線反轉組合型態（各5種，共10條規則，互為鏡射對稱）。

書中每一種型態的成立條件都明文包含「出現在高檔／低檔」這個位置前提，不是額外的加分解讀，
所以這裡的 is_at_high / is_at_low 是必要參數（由外部的趨勢位置模組供應，尚未實作前呼叫端
可先傳全 True 的 Series 只看幾何型態）。強弱覆蓋分級、3線反紅/黑等書中有明確數字定義的
延伸判斷一併實作；「近期漲跌幅door>=15%」的操作建議、「次日開盤位置」等敘述性建議則留待
Layer3 策略層決定如何使用這裡輸出的布林/分類 Series，不在此處硬編成固定文字。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.candles import is_black_candle, is_red_candle
from src.rule_registry import implements_rule


def _three_line_break_high(close: pd.Series, low: pd.Series, n: int = 3) -> pd.Series:
    """收盤是否一次跌破前 n 根K棒的最低點（3線反黑的判斷式）。"""
    rolling_min_low = low.shift(1).rolling(window=n, min_periods=n).min()
    return (close < rolling_min_low).fillna(False)


def _three_line_break_low(close: pd.Series, high: pd.Series, n: int = 3) -> pd.Series:
    """收盤是否一次突破前 n 根K棒的最高點（3線反紅的判斷式）。"""
    rolling_max_high = high.shift(1).rolling(window=n, min_periods=n).max()
    return (close > rolling_max_high).fillna(False)


@implements_rule("R-CANDLE-06")
def basic_reversal_at_high(open_: pd.Series, close: pd.Series, is_at_high: pd.Series, pct_threshold: float = 0.03) -> pd.Series:
    """高檔紅K黑K基本反轉型態：前一日中長紅(>=3%)＋當日中長黑(>=3%)，出現在高檔，6型態中力道最弱。"""
    day1_up_pct = (close.shift(1) - open_.shift(1)) / open_.shift(1)
    day2_down_pct = (open_ - close) / open_
    pattern = (
        is_red_candle(open_.shift(1), close.shift(1)) & (day1_up_pct >= pct_threshold)
        & is_black_candle(open_, close) & (day2_down_pct >= pct_threshold)
    )
    return (pattern & is_at_high.astype(bool)).fillna(False)


@implements_rule("R-CANDLE-14")
def basic_reversal_at_low(open_: pd.Series, close: pd.Series, is_at_low: pd.Series, pct_threshold: float = 0.03) -> pd.Series:
    """低檔黑K紅K基本反轉型態：前一日中長黑(>=3%)＋當日中長紅(>=3%)，出現在低檔，與R-CANDLE-06鏡射。"""
    day1_down_pct = (open_.shift(1) - close.shift(1)) / open_.shift(1)
    day2_up_pct = (close - open_) / open_
    pattern = (
        is_black_candle(open_.shift(1), close.shift(1)) & (day1_down_pct >= pct_threshold)
        & is_red_candle(open_, close) & (day2_up_pct >= pct_threshold)
    )
    return (pattern & is_at_low.astype(bool)).fillna(False)


@implements_rule("R-CANDLE-08")
def dark_cloud_cover(open_: pd.Series, high: pd.Series, close: pd.Series, is_at_high: pd.Series) -> pd.Series:
    """高檔長黑覆蓋（烏雲罩頂）：次日開高創新高，收盤跌入前一日紅K實體內（未跌破最低點，否則升級為長黑吞噬）。"""
    prev_open, prev_close, prev_high = open_.shift(1), close.shift(1), high.shift(1)
    pattern = (
        is_red_candle(prev_open, prev_close)
        & is_at_high.astype(bool)
        & (open_ > prev_open)
        & (high > prev_high)
        & (close < prev_close) & (close > prev_open)
    )
    return pattern.fillna(False)


@implements_rule("R-CANDLE-08")
def dark_cloud_cover_strength(open_: pd.Series, close: pd.Series) -> pd.Series:
    """長黑覆蓋強弱分級：收盤跌破前一日紅K實體中點＝強覆蓋，否則為弱覆蓋。僅在覆蓋型態成立時才有意義。"""
    prev_mid = (open_.shift(1) + close.shift(1)) / 2
    return pd.Series(pd.NA, index=close.index, dtype="object").mask(close < prev_mid, "強覆蓋").mask(close >= prev_mid, "弱覆蓋")


@implements_rule("R-CANDLE-16")
def bullish_cover_low(open_: pd.Series, low: pd.Series, close: pd.Series, is_at_low: pd.Series) -> pd.Series:
    """低檔長紅覆蓋（旭日東升）：次日開低創新低，收盤突破平盤且進入前一日黑K實體內（鏡射R-CANDLE-08）。"""
    prev_open, prev_close, prev_low = open_.shift(1), close.shift(1), low.shift(1)
    pattern = (
        is_black_candle(prev_open, prev_close)
        & is_at_low.astype(bool)
        & (open_ < prev_open)
        & (low < prev_low)
        & (close > prev_close) & (close < prev_open)
    )
    return pattern.fillna(False)


@implements_rule("R-CANDLE-16")
def bullish_cover_low_strength(open_: pd.Series, close: pd.Series) -> pd.Series:
    """長紅覆蓋強弱分級：收盤突破前一日黑K實體中點＝強覆蓋，否則為弱覆蓋。"""
    prev_mid = (open_.shift(1) + close.shift(1)) / 2
    return pd.Series(pd.NA, index=close.index, dtype="object").mask(close > prev_mid, "強覆蓋").mask(close <= prev_mid, "弱覆蓋")


@implements_rule("R-CANDLE-09")
def bearish_harami_at_high(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, is_at_high: pd.Series) -> pd.Series:
    """高檔母子懷抱：前一日中長紅（母線），當日子線不創新高不破低，完全被母線高低點包覆。"""
    prev_open, prev_close = open_.shift(1), close.shift(1)
    prev_high, prev_low = high.shift(1), low.shift(1)
    is_engulfed = (high <= prev_high) & (low >= prev_low)
    pattern = is_red_candle(prev_open, prev_close) & is_at_high.astype(bool) & is_engulfed
    return pattern.fillna(False)


@implements_rule("R-CANDLE-17")
def bullish_harami_at_low(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, is_at_low: pd.Series) -> pd.Series:
    """低檔母子懷抱（光明在望）：前一日中長黑（母線），當日子線完全被母線高低點包覆，鏡射R-CANDLE-09。"""
    prev_open, prev_close = open_.shift(1), close.shift(1)
    prev_high, prev_low = high.shift(1), low.shift(1)
    is_engulfed = (high <= prev_high) & (low >= prev_low)
    pattern = is_black_candle(prev_open, prev_close) & is_at_low.astype(bool) & is_engulfed
    return pattern.fillna(False)


@implements_rule("R-CANDLE-10")
def bearish_engulfing_at_high(open_: pd.Series, low: pd.Series, close: pd.Series, is_at_high: pd.Series, lookback: int = 3) -> pd.Series:
    """高檔長黑吞噬（主力出貨）：黑K實體完全吞噬前一日紅K實體，且收盤跌破前一日最低點。本節最強反轉訊號之一。"""
    prev_open, prev_close, prev_low = open_.shift(1), close.shift(1), low.shift(1)
    is_engulf = (open_ >= prev_close) & (close <= prev_open)
    is_break_low = close < prev_low
    pattern = is_red_candle(prev_open, prev_close) & is_black_candle(open_, close) & is_at_high.astype(bool) & is_engulf & is_break_low
    return pattern.fillna(False)


@implements_rule("R-CANDLE-10")
def bearish_engulfing_three_line_break(close: pd.Series, low: pd.Series, lookback: int = 3) -> pd.Series:
    """3線反黑：1根黑K一次跌破前2~3根K線的最低點，反轉力道更強（供R-CANDLE-10/11共用）。"""
    return _three_line_break_high(close, low, lookback)


@implements_rule("R-CANDLE-18")
def bullish_engulfing_at_low(open_: pd.Series, high: pd.Series, close: pd.Series, is_at_low: pd.Series, lookback: int = 3) -> pd.Series:
    """低檔長紅吞噬（主力進貨）：紅K實體完全吞噬前一日黑K實體，且收盤突破前一日最高點，鏡射R-CANDLE-10。"""
    prev_open, prev_close, prev_high = open_.shift(1), close.shift(1), high.shift(1)
    is_engulf = (open_ <= prev_close) & (close >= prev_open)
    is_break_high = close > prev_high
    pattern = is_black_candle(prev_open, prev_close) & is_red_candle(open_, close) & is_at_low.astype(bool) & is_engulf & is_break_high
    return pattern.fillna(False)


@implements_rule("R-CANDLE-18")
def bullish_engulfing_three_line_break(close: pd.Series, high: pd.Series, lookback: int = 3) -> pd.Series:
    """3線反紅：1根紅K一次突破前2~3根K線的最高點（供R-CANDLE-18/19共用）。"""
    return _three_line_break_low(close, high, lookback)


@implements_rule("R-CANDLE-11")
def bearish_piercing_at_high(open_: pd.Series, low: pd.Series, close: pd.Series, is_at_high: pd.Series) -> pd.Series:
    """高檔長黑貫穿（一路向下）：黑K開盤未開高（直接下跌），收盤跌破前一日最低點；與長黑吞噬同屬最強反轉訊號。"""
    prev_open, prev_close, prev_low = open_.shift(1), close.shift(1), low.shift(1)
    is_open_not_higher = open_ <= prev_close
    is_break_low = close < prev_low
    pattern = is_red_candle(prev_open, prev_close) & is_black_candle(open_, close) & is_at_high.astype(bool) & is_open_not_higher & is_break_low
    return pattern.fillna(False)


@implements_rule("R-CANDLE-19")
def bullish_piercing_at_low(open_: pd.Series, high: pd.Series, close: pd.Series, is_at_low: pd.Series) -> pd.Series:
    """低檔長紅貫穿（一路向上）：紅K開高走高，收盤突破前一日最高點（含影線），鏡射R-CANDLE-11。"""
    prev_open, prev_close, prev_high = open_.shift(1), close.shift(1), high.shift(1)
    is_open_high = open_ >= prev_close
    is_break_high = close > prev_high
    pattern = is_black_candle(prev_open, prev_close) & is_red_candle(open_, close) & is_at_low.astype(bool) & is_open_high & is_break_high
    return pattern.fillna(False)
