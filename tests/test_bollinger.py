import pandas as pd
import pytest

from src.indicators.bollinger import (
    band_slope_direction,
    bollinger_bands,
    bollinger_bands_flat,
    bollinger_buy_signal_1,
    bollinger_buy_signal_2,
    bollinger_buy_signal_3,
    bollinger_buy_signal_4,
    bollinger_sell_signal_1,
    bollinger_sell_signal_3,
    bollinger_sell_signal_4,
    bollinger_touch_exit_signal,
    channel_pullback_warning,
    channel_squeeze_guidance,
    classify_three_band_shape,
    profit_take_exit_below_ma5_black_candle,
)


def test_bollinger_bands_matches_hand_calculation():
    # R-INDICATOR-20: 中軌=MA20，上/下軌=中軌±2倍標準差（此處n=3,num_std=1驗證公式本身）
    close = pd.Series([10.0, 12.0, 11.0])
    result = bollinger_bands(close, n=3, num_std=1)
    assert result["mid"].iloc[2] == pytest.approx(11.0)
    assert result["upper"].iloc[2] == pytest.approx(12.0)  # std=1.0 (樣本標準差)
    assert result["lower"].iloc[2] == pytest.approx(10.0)


def test_bollinger_touch_exit_signal():
    close = pd.Series([116.0, 84.0])
    upper = pd.Series([115.0, 115.0])
    lower = pd.Series([85.0, 85.0])
    holding = pd.Series(["多", "空"])
    result = bollinger_touch_exit_signal(close, upper, lower, holding)
    assert result.iloc[0] == "短線出場訊號（做多出場）"
    assert result.iloc[1] == "短線回補訊號（做空回補）"


def test_bollinger_bands_flat():
    upper = pd.Series([100.0, 100.05, 105.0])
    mid = pd.Series([90.0, 90.02, 95.0])
    lower = pd.Series([80.0, 80.03, 85.0])
    result = bollinger_bands_flat(upper, mid, lower, slope_threshold=0.01)
    assert result.tolist() == [False, True, False]


def test_bollinger_buy_signals():
    # 買訊①：空頭由下往上穿越下軌
    assert bollinger_buy_signal_1(
        pd.Series([84.0, 86.0]), pd.Series([85.0, 85.0]), pd.Series(["空頭", "空頭"])
    ).tolist() == [False, True]

    # 買訊②：多頭曾跌破中軌，近期再站上中軌
    assert bollinger_buy_signal_2(
        pd.Series([95.0, 89.0, 96.0]), pd.Series([90.0, 90.0, 90.0]), pd.Series(["多頭"] * 3)
    ).tolist() == [False, False, True]

    # 買訊③：價格在中軌與上軌之間向上運行
    assert bollinger_buy_signal_3(
        pd.Series([91.0, 93.0]), pd.Series([90.0, 90.0]), pd.Series([100.0, 100.0])
    ).tolist() == [False, True]

    # 買訊④：三軌走平時，突破盤整區上緣
    assert bollinger_buy_signal_4(
        pd.Series([105.0, 95.0]), pd.Series([True, True]), pd.Series([100.0, 100.0])
    ).tolist() == [True, False]


def test_bollinger_sell_signals_mirror_buy():
    # 做空訊①：多頭由上往下穿越上軌
    assert bollinger_sell_signal_1(
        pd.Series([116.0, 114.0]), pd.Series([115.0, 115.0]), pd.Series(["多頭", "多頭"])
    ).tolist() == [False, True]

    # 做空訊③：價格在中軌與下軌之間向下運行
    assert bollinger_sell_signal_3(
        pd.Series([89.0, 87.0]), pd.Series([90.0, 90.0]), pd.Series([80.0, 80.0])
    ).tolist() == [False, True]

    # 做空訊④：三軌走平時，跌破盤整區下緣
    assert bollinger_sell_signal_4(
        pd.Series([95.0, 105.0]), pd.Series([True, True]), pd.Series([100.0, 100.0])
    ).tolist() == [True, False]


def test_band_slope_direction():
    # R-INDICATOR-24: 單日變動超過1%門檻(沿用R-INDICATOR-21走平門檻)才判為上揚/下彎
    band = pd.Series([100.0, 102.0, 101.9])
    result = band_slope_direction(band)
    assert result.iloc[1] == "上揚"   # +2%
    assert result.iloc[2] == "走平"   # -0.098%，未達1%門檻


def test_band_slope_direction_detects_clear_down_move():
    band = pd.Series([100.0, 95.0])
    assert band_slope_direction(band).iloc[1] == "下彎"


def test_classify_three_band_shape_all_combinations():
    up = pd.Series(["上揚", "下彎", "走平", "上揚", "下彎"])
    mid = pd.Series(["上揚", "下彎", "走平", "下彎", "上揚"])
    low = pd.Series(["上揚", "下彎", "走平", "下彎", "上揚"])
    result = classify_three_band_shape(up, mid, low)
    assert result.iloc[0] == "三軌同時向上，強勢多頭延續"
    assert result.iloc[1] == "三軌同時向下，強勢空頭延續"
    assert result.iloc[2] == "三軌走平，方向不明，建議觀望等待通道開口突破"
    assert result.iloc[3] == "罕見組合（上軌揚升、中下軌下彎）"
    assert result.iloc[4] == "上軌轉下、中下軌仍向上，通道縮小，回檔或漸趨整理"


def test_channel_squeeze_guidance_depends_on_long_term_trend():
    squeeze = "上軌轉下、中下軌仍向上，通道縮小，回檔或漸趨整理"
    assert channel_squeeze_guidance(squeeze, "多頭") == "強勢整理，可持股觀望或逢回短多"
    assert channel_squeeze_guidance(squeeze, "空頭") == "弱勢整理，宜持空單續抱或反彈續空"
    assert channel_squeeze_guidance("三軌同時向上，強勢多頭延續", "多頭") == "三軌同時向上，強勢多頭延續"


def test_channel_pullback_warning():
    assert channel_pullback_warning("上揚", close_below_mid=True) is None
    assert channel_pullback_warning("走平", close_below_mid=False) == "多頭進入盤整或急速回檔階段"
    assert channel_pullback_warning("下彎", close_below_mid=True) == "多頭進入盤整或急速回檔階段；跌破中軌，容易進一步觸及下軌"


def test_profit_take_exit_below_ma5_black_candle():
    close = pd.Series([100.0, 94.0])
    ma5 = pd.Series([95.0, 95.0])
    is_black = pd.Series([False, True])
    assert profit_take_exit_below_ma5_black_candle(close, ma5, is_black).tolist() == [False, True]
