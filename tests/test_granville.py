import pandas as pd

from src.indicators.granville import (
    granville_buy_signal_1,
    granville_buy_signal_2,
    granville_buy_signal_3,
    granville_buy_signal_4,
    granville_sell_signal_1,
    granville_sell_signal_2,
    granville_sell_signal_3,
    granville_sell_signal_4,
)


def test_granville_buy_signal_1_bottom_reversal():
    # 買點①：MA20不再下彎，前一日收盤仍在均線下方，當日收盤向上突破
    close = pd.Series([9.0, 9.0, 11.0])
    ma20 = pd.Series([10.0, 10.0, 10.0])
    result = granville_buy_signal_1(close, ma20)
    assert result.tolist() == [False, False, True]


def test_granville_buy_signal_2_pullback_to_support():
    # 買點②：均線上揚，股價回檔貼近但未跌破MA20，隨後轉而上漲，可反覆出現
    ma20 = pd.Series([8.0, 9.0, 10.0, 10.0])
    close = pd.Series([10.5, 10.3, 10.6, 10.6])
    low = pd.Series([10.1, 10.05, 10.02, 10.02])
    result = granville_buy_signal_2(close, low, ma20)
    assert result.tolist() == [False, False, True, False]


def test_granville_buy_signal_3_quick_recovery_after_brief_break():
    # 買點③：均線持續上揚，近期曾短暫跌破，當日已收復站上
    ma20 = pd.Series([10.0, 10.5, 11.0, 11.5])
    close = pd.Series([10.5, 10.2, 9.8, 11.8])
    result = granville_buy_signal_3(close, ma20)
    assert result.tolist() == [False, False, False, True]


def test_granville_buy_signal_4_oversold_rebound_in_bear_trend():
    # 買點④：空頭中連跌>=3天、負乖離>=15%，股價開始反彈
    close = pd.Series([100.0, 95.0, 90.0, 84.0, 90.0])
    ma20 = pd.Series([110.0] * 5)
    is_bear_trend = pd.Series([True] * 5)
    result = granville_buy_signal_4(close, ma20, is_bear_trend)
    assert result.tolist() == [False, False, False, False, True]


def test_granville_sell_signal_1_top_reversal():
    close = pd.Series([11.0, 11.0, 9.0])
    ma20 = pd.Series([10.0, 10.0, 10.0])
    result = granville_sell_signal_1(close, ma20)
    assert result.tolist() == [False, False, True]


def test_granville_sell_signal_2_rebound_to_resistance():
    ma20 = pd.Series([12.0, 11.0, 10.0, 10.0])
    close = pd.Series([9.5, 9.7, 9.4, 9.4])
    high = pd.Series([9.9, 9.95, 9.98, 9.98])
    result = granville_sell_signal_2(close, high, ma20)
    assert result.tolist() == [False, False, True, False]


def test_granville_sell_signal_3_quick_fallback_after_brief_break():
    ma20 = pd.Series([10.0, 9.5, 9.0, 8.5])
    close = pd.Series([9.5, 9.8, 10.2, 8.0])
    result = granville_sell_signal_3(close, ma20)
    assert result.tolist() == [False, False, False, True]


def test_granville_sell_signal_4_overbought_pullback_in_bull_trend():
    close = pd.Series([100.0, 105.0, 110.0, 116.0, 110.0])
    ma20 = pd.Series([95.0] * 5)
    is_bull_trend = pd.Series([True] * 5)
    result = granville_sell_signal_4(close, ma20, is_bull_trend)
    assert result.tolist() == [False, False, False, False, True]
