"""平均真實波幅(ATR, Average True Range)——標準技術指標，本專案朱家泓／陳家豐規則庫
原本沒有這一項，是「三維過濾法」(見`ai/q3-rules/`)複製外部工具Antigravity儀表板時
新增，純粹用來在個股健檢面板呈現波動度數字，不是任何既有規則的必要輸入。
"""

from __future__ import annotations

import pandas as pd


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1,
    )
    return ranges.max(axis=1)


def average_true_range(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return true_range(high, low, close).rolling(n).mean()
