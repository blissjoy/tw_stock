"""能量潮(OBV, On-Balance Volume)——標準技術指標，同`atr.py`，是「三維過濾法」
(見`ai/q3-rules/`)複製外部工具Antigravity儀表板時新增，本專案既有規則庫沒有這一項。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def on_balance_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()
