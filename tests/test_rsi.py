import pandas as pd
import pytest

from src.indicators.rsi import (
    rsi,
    rsi_bottom_divergence,
    rsi_overbought_oversold_signal,
    rsi_short_long_cross_signal,
    rsi_top_divergence,
)


def test_rsi_divides_up_and_down_by_same_n():
    # R-INDICATOR-13: avg_up/avg_down都除以同一個N(rolling window)，書中強調的「除以N而非漲跌天數」細節
    close = pd.Series([10.0, 11.0, 9.0, 12.0])  # diffs: +1, -2, +3
    result = rsi(close, n=3)
    assert result.iloc[:3].isna().all()
    # 最近3筆diff: up=[1,0,3]=4/3, down=[0,2,0]=2/3 -> RSI=4/(4+2)*100
    assert result.iloc[3] == pytest.approx(66.6667, rel=1e-3)


def test_rsi_overbought_oversold_uses_80_20_not_70_30():
    result = rsi_overbought_oversold_signal(pd.Series([85.0, 50.0, 15.0, 80.0, 20.0]))
    assert result.iloc[0] == "超買，逆勢思考準備賣出或做空"
    assert pd.isna(result.iloc[1])
    assert result.iloc[2] == "超賣，逆勢思考準備回補或做多"
    assert pd.isna(result.iloc[3])  # 剛好80，未超過門檻
    assert pd.isna(result.iloc[4])  # 剛好20，未低於門檻


def test_rsi_short_long_cross_signal():
    rsi_short = pd.Series([9.0, 10.0, 11.0, 12.0, 9.0])
    rsi_long = pd.Series([10.0] * 5)
    result = rsi_short_long_cross_signal(rsi_short, rsi_long)
    assert result.iloc[2] == "多頭上漲買進參考訊號"
    assert result.iloc[4] == "空頭下跌做空參考訊號"


def test_rsi_top_and_bottom_divergence():
    assert rsi_top_divergence(heads=[10, 12], rsi_peaks=[70, 60]) == "RSI頭部背離，預警訊號，需搭配價格是否跌破前低/頸線形成空頭確認才可進場"
    assert rsi_top_divergence(heads=[10, 12], rsi_peaks=[60, 70]) is None
    assert rsi_bottom_divergence(bottoms=[10, 8], rsi_troughs=[30, 40]) == "RSI底部背離，預警訊號，需搭配價格是否突破前高/頸線形成多頭確認才可進場"
    assert rsi_bottom_divergence(bottoms=[10, 12], rsi_troughs=[30, 40]) is None
