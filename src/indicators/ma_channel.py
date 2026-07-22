"""技術指標分類：MA通道（正負乖離軌道，R-INDICATOR-18）。"""

from __future__ import annotations

import pandas as pd

from src.rule_registry import implements_rule


@implements_rule("R-INDICATOR-18")
def ma_channel_bands(ma20: pd.Series, r: float = 0.15) -> pd.DataFrame:
    """MA通道上下軌：以MA20為中心線，上下各偏移r(書中範例常用15%，建議區間12%~15%)。"""
    return pd.DataFrame({"upper": ma20 * (1 + r), "lower": ma20 * (1 - r)})


@implements_rule("R-INDICATOR-18")
def ma_channel_breakout_signal(close: pd.Series, upper: pd.Series, lower: pd.Series, is_large_volume: pd.Series) -> pd.Series:
    """帶量突破上軌＝偏多趨勢轉強；帶量跌破下軌＝偏空趨勢轉強；軌道內為常態游走。"""
    signal = pd.Series("軌道內游走（常態）", index=close.index, dtype="object")
    large_vol = is_large_volume.astype(bool)
    signal = signal.mask((close > upper) & large_vol, "帶量突破上軌，偏多趨勢轉強")
    signal = signal.mask((close < lower) & large_vol, "帶量跌破下軌，偏空趨勢轉強")
    return signal
