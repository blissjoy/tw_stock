"""技術指標分類：RSI相對強弱指標（R-INDICATOR-13/14/15）。

RSI(t)=avg_up/(avg_up+avg_down)*100，只要avg_up、avg_down用「同一個N」做rolling平均，
N本身會在比值中相消，因此書中特別提醒的「除以N、不是除以漲跌天數」這個細節，只要
avg_up與avg_down共用同一個rolling window就會自動保證正確，不需要另外特殊處理。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.crossovers import is_death_cross, is_golden_cross
from src.rule_registry import implements_rule


@implements_rule("R-INDICATOR-13")
def rsi(close: pd.Series, n: int = 9) -> pd.Series:
    """RSI(t) = avg_up(t) / (avg_up(t)+avg_down(t)) * 100，avg_up/avg_down皆為最近N日漲跌幅的簡單平均。"""
    delta = close.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    avg_up = up.rolling(window=n, min_periods=n).mean()
    avg_down = down.rolling(window=n, min_periods=n).mean()
    return avg_up / (avg_up + avg_down) * 100


@implements_rule("R-INDICATOR-14")
def rsi_overbought_oversold_signal(rsi_series: pd.Series, overbought: float = 80, oversold: float = 20) -> pd.Series:
    """RSI>80超買，逆勢思考準備賣出/做空；RSI<20超賣，逆勢思考準備回補/做多。書中門檻為80/20，非坊間70/30。"""
    signal = pd.Series(pd.NA, index=rsi_series.index, dtype="object")
    signal = signal.mask(rsi_series > overbought, "超買，逆勢思考準備賣出或做空")
    signal = signal.mask(rsi_series < oversold, "超賣，逆勢思考準備回補或做多")
    return signal


@implements_rule("R-INDICATOR-15")
def rsi_short_long_cross_signal(rsi_short: pd.Series, rsi_long: pd.Series) -> pd.Series:
    """短週期RSI(如RSI6)向上突破長週期RSI(如RSI12)為黃金交叉，買進參考；反之死亡交叉，做空參考。"""
    golden = is_golden_cross(rsi_short, rsi_long)
    dead = is_death_cross(rsi_short, rsi_long)
    signal = pd.Series(pd.NA, index=rsi_short.index, dtype="object")
    signal = signal.mask(golden.fillna(False), "多頭上漲買進參考訊號")
    signal = signal.mask(dead.fillna(False), "空頭下跌做空參考訊號")
    return signal


@implements_rule("R-INDICATOR-16")
def rsi_top_divergence(heads: list[float], rsi_peaks: list[float]) -> str | None:
    """RSI頭部(空頭)背離：股價頭頭高但RSI峰值頭頭低，是預警訊號，需搭配價格跌破前低/頸線的空頭確認才可進場。"""
    if len(heads) < 2 or len(rsi_peaks) < 2:
        return None
    if heads[-1] > heads[-2] and rsi_peaks[-1] < rsi_peaks[-2]:
        return "RSI頭部背離，預警訊號，需搭配價格是否跌破前低/頸線形成空頭確認才可進場"
    return None


@implements_rule("R-INDICATOR-16")
def rsi_bottom_divergence(bottoms: list[float], rsi_troughs: list[float]) -> str | None:
    """RSI底部(多頭)背離：股價底底低但RSI谷值底底高，需搭配價格突破前高/頸線的多頭確認才可進場。"""
    if len(bottoms) < 2 or len(rsi_troughs) < 2:
        return None
    if bottoms[-1] < bottoms[-2] and rsi_troughs[-1] > rsi_troughs[-2]:
        return "RSI底部背離，預警訊號，需搭配價格是否突破前高/頸線形成多頭確認才可進場"
    return None
