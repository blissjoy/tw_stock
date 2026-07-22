"""回測框架範例策略：示範如何把規則庫既有函式組成 entry/exit 訊號餵給回測引擎。

這裡不是新規則，是「規則庫→回測」的接線範例：用MA5/MA20黃金死亡交叉(R-MA-13/14)
搭配均線多頭排列(R-MA-08)作為示範策略，用來驗證回測引擎本身的訊號消費機制，也讓之後
要組別的策略（例如接上R-TREND-03多頭趨勢判定、R-MA-19葛蘭碧買點等）有現成的範例可抄。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.crossovers import is_death_cross, is_golden_cross
from src.indicators.moving_average import compute_ma_set, is_bullish_aligned


def golden_cross_trend_strategy(close: pd.Series, periods: tuple[int, ...] = (5, 10, 20), confirm_window: int = 5) -> tuple[pd.Series, pd.Series]:
    """進場：短均線黃金交叉長均線後confirm_window天內，均線完成多頭排列（呼應R-MA-05：短均線先轉彎、
    長均線後轉彎，排列通常晚於交叉幾天才完成，不要求兩者同一天發生）；出場：短均線死亡交叉長均線。"""
    ma_set = compute_ma_set(close, periods=periods)
    short_col, long_col = f"MA{periods[0]}", f"MA{periods[-1]}"
    bullish = is_bullish_aligned(ma_set, periods=periods)
    golden = is_golden_cross(ma_set[short_col], ma_set[long_col])
    golden_recently = golden.rolling(window=confirm_window, min_periods=1).max().fillna(0).astype(bool)
    just_aligned = bullish & ~bullish.shift(1).fillna(False).astype(bool)
    entry_signal = (just_aligned & golden_recently).fillna(False)
    exit_signal = is_death_cross(ma_set[short_col], ma_set[long_col]).fillna(False)
    return entry_signal, exit_signal
