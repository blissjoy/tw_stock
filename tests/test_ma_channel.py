import pandas as pd
import pytest

from src.indicators.ma_channel import ma_channel_bands, ma_channel_breakout_signal


def test_ma_channel_bands():
    # R-INDICATOR-18: 上軌=MA20*(1+r)，下軌=MA20*(1-r)
    ma20 = pd.Series([100.0])
    result = ma_channel_bands(ma20, r=0.15)
    assert result["upper"].iloc[0] == pytest.approx(115.0)
    assert result["lower"].iloc[0] == pytest.approx(85.0)


def test_ma_channel_breakout_signal_requires_volume():
    close = pd.Series([120.0, 80.0, 100.0])
    upper = pd.Series([115.0, 115.0, 115.0])
    lower = pd.Series([85.0, 85.0, 85.0])
    is_large_volume = pd.Series([True, True, False])
    result = ma_channel_breakout_signal(close, upper, lower, is_large_volume)
    assert result.iloc[0] == "帶量突破上軌，偏多趨勢轉強"
    assert result.iloc[1] == "帶量跌破下軌，偏空趨勢轉強"
    assert result.iloc[2] == "軌道內游走（常態）"
