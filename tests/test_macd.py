import pandas as pd
import pytest

from src.indicators.macd import (
    bear_low_divergence,
    bear_osc_growing_continuation,
    bear_osc_momentum_divergence,
    bear_osc_re_growth_short_signal,
    bear_osc_shrinking_rebound,
    bull_high_divergence,
    bull_osc_growing_continuation,
    bull_osc_momentum_divergence,
    bull_osc_re_growth_buy_signal,
    bull_osc_shrinking_pullback,
    ema,
    green_to_red_bullish_signal,
    green_to_red_bullish_signal_low,
    macd_swing_high_divergence,
    macd_swing_low_divergence,
    macd_trend_level_bearish_divergence,
    macd_trend_level_bullish_divergence,
    macd_zero_axis_bear_signal,
    macd_zero_axis_bull_signal,
    red_to_green_bearish_signal,
    red_to_green_bearish_signal_low,
)


def test_ema_matches_hand_calculation_with_adjust_false():
    # EMA(N,t) = Close[t]*(2/(N+1)) + EMA(N,t-1)*(1-2/(N+1))，span=2 -> alpha=2/3，種子=第一筆值
    series = pd.Series([1.0, 2.0, 3.0])
    result = ema(series, 2)
    assert result.iloc[0] == pytest.approx(1.0)
    assert result.iloc[1] == pytest.approx(2 * (2 / 3) + 1 * (1 / 3))  # 1.6667
    assert result.iloc[2] == pytest.approx(3 * (2 / 3) + result.iloc[1] * (1 / 3))  # 2.5556


def test_macd_zero_axis_bull_signal():
    # R-INDICATOR-02: 0軸上黃金交叉=買進；0軸上死亡交叉=短線獲利了結(仍屬回檔)
    dif = pd.Series([1.0, 2.0, 3.0, 0.5])
    macd_line = pd.Series([2.0, 1.0, 1.0, 3.0])
    result = macd_zero_axis_bull_signal(dif, macd_line)
    assert pd.isna(result.iloc[0])
    assert result.iloc[1] == "多方買進訊號"
    assert pd.isna(result.iloc[2])
    assert result.iloc[3] == "短線獲利了結訊號（回檔，多頭格局不變，除非後續跌破0軸）"


def test_macd_zero_axis_bear_signal():
    # R-INDICATOR-03: 0軸下死亡交叉=賣出/做空；0軸下黃金交叉=空單回補(僅屬反彈)
    dif = pd.Series([-1.0, -2.0, -3.0, -0.5])
    macd_line = pd.Series([-2.0, -1.0, -1.0, -3.0])
    result = macd_zero_axis_bear_signal(dif, macd_line)
    assert result.iloc[1] == "空方賣出（做空）訊號"
    assert result.iloc[3] == "空單回補訊號（僅屬空頭反彈，除非後續站上0軸）"


def test_macd_trend_level_divergence():
    # R-INDICATOR-07: 頭頭高但OSC紅柱峰值頭頭低=高檔背離；底底低但綠柱谷值底底高=低檔背離
    assert macd_trend_level_bullish_divergence(heads=[10, 12], osc_peaks=[5, 3]) is True
    assert macd_trend_level_bullish_divergence(heads=[10, 12], osc_peaks=[3, 5]) is False
    assert macd_trend_level_bearish_divergence(bottoms=[10, 8], osc_troughs=[-5, -3]) is True
    assert macd_trend_level_bearish_divergence(bottoms=[10, 8], osc_troughs=[-3, -5]) is False


def test_macd_red_bar_bull_momentum_7_points():
    # R-INDICATOR-04
    assert bull_osc_shrinking_pullback(3, 5) is True
    assert bull_osc_shrinking_pullback(-1, 5) is False
    assert bull_osc_growing_continuation(5, 3) is True
    assert bull_osc_momentum_divergence(osc_t=5, osc_prev=3, close_t=100, recent_high=105) is True
    assert bull_osc_momentum_divergence(osc_t=5, osc_prev=3, close_t=110, recent_high=105) is False
    assert bull_osc_re_growth_buy_signal(True, 5, 3) is True
    assert green_to_red_bullish_signal(osc_prev=-2, green_was_shrinking=True, osc_t=1) is True
    assert red_to_green_bearish_signal(osc_prev=2, red_was_shrinking=True, osc_t=-1) is True
    assert bull_high_divergence(True, True) is True
    assert bull_high_divergence(True, False) is False


def test_macd_green_bar_bear_momentum_7_points_mirrors_red():
    # R-INDICATOR-05
    assert bear_osc_shrinking_rebound(-3, -5) is True
    assert bear_osc_growing_continuation(-5, -3) is True
    assert bear_osc_momentum_divergence(osc_t=-5, osc_prev=-3, close_t=100, recent_low=95) is True
    assert bear_osc_re_growth_short_signal(True, -5, -3) is True
    assert red_to_green_bearish_signal_low(osc_prev=2, red_was_shrinking=True, osc_t=-1) is True
    assert green_to_red_bullish_signal_low(osc_prev=-2, green_was_shrinking=True, osc_t=1) is True
    assert bear_low_divergence(True, True) is True


def test_macd_swing_divergence():
    # R-INDICATOR-06: 單一波段內的高檔/低檔背離
    assert macd_swing_high_divergence(True, osc_t=3, osc_at_prior_swing_high=5) is True
    assert macd_swing_high_divergence(False, osc_t=3, osc_at_prior_swing_high=5) is False
    assert macd_swing_low_divergence(True, osc_t=-3, osc_at_prior_swing_low=-5) is True
