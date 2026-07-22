import pandas as pd
import pytest

from src.indicators.kd import (
    compute_kd,
    is_high_dull,
    is_low_dull,
    kd_cross_signal_by_trend,
    kd_peak_divergence,
    kd_trough_divergence,
    select_kd_timeframe,
)


def test_compute_kd_matches_hand_calculation():
    # R-INDICATOR-08: K=RSV(N日)；D=最近d_period日「C-Ln」總和 除以「Hn-Ln」總和 *100（書中3日加總簡化式，此處N=3,d=2驗證）
    high = pd.Series([12.0, 13.0, 14.0, 13.0, 15.0])
    low = pd.Series([9.0, 10.0, 10.0, 11.0, 11.0])
    close = pd.Series([10.0, 12.0, 13.0, 12.0, 14.0])
    result = compute_kd(high, low, close, n=3, d_period=2)

    assert result["K"].iloc[2] == pytest.approx(80.0)   # (13-9)/(14-9)*100
    assert result["K"].iloc[3] == pytest.approx(50.0)   # (12-10)/(14-10)*100
    assert result["K"].iloc[4] == pytest.approx(80.0)   # (14-10)/(15-10)*100
    assert result["D"].iloc[3] == pytest.approx(66.6667, rel=1e-3)  # (4+2)/(5+4)*100
    assert result["D"].iloc[4] == pytest.approx(66.6667, rel=1e-3)  # (2+4)/(4+5)*100


def test_kd_cross_signal_by_trend_bull_bear_range():
    # R-INDICATOR-09: 多頭黃金=買點/死亡=賣點；空頭死亡=空點/黃金=回補點；盤整訊號皆無效
    k = pd.Series([9.0, 10.0, 11.0])
    d = pd.Series([10.0, 10.0, 10.0])

    bull = kd_cross_signal_by_trend(k, d, pd.Series(["多頭"] * 3))
    assert bull.iloc[2] == "參考買點"

    bear = kd_cross_signal_by_trend(k, d, pd.Series(["空頭"] * 3))
    assert bear.iloc[2] == "空單參考回補點"

    range_ = kd_cross_signal_by_trend(k, d, pd.Series(["盤整"] * 3))
    assert range_.iloc[2] == "訊號無效，不宜依KD交叉進出"


def test_select_kd_timeframe():
    assert select_kd_timeframe("短線") == "日線"
    assert select_kd_timeframe("中期") == "週線"
    assert select_kd_timeframe("長期") == "月線"
    with pytest.raises(ValueError):
        select_kd_timeframe("超短線")


def test_kd_divergence_only_valid_in_20_to_80_zone():
    # R-INDICATOR-12: 峰值/谷值皆需落在20~80非鈍化區間，訊號才可信
    assert kd_peak_divergence(heads=[10, 12], k_peaks=[70, 60]) == "KD峰背離，趨勢反轉風險升高"
    assert kd_peak_divergence(heads=[10, 12], k_peaks=[85, 60]) == "KD雖呈背離型態，但落在鈍化區，訊號可信度低，需回歸股價與價量判斷"
    assert kd_trough_divergence(bottoms=[10, 8], k_troughs=[30, 40]) == "KD底背離，股價隨時會反彈或落底"
    assert kd_trough_divergence(bottoms=[10, 12], k_troughs=[30, 40]) is None  # 底部未創新低，不成立


def test_is_high_dull_requires_consecutive_n_days():
    # R-INDICATOR-11: K、D連續3天(預設)皆>=80才算高檔鈍化；window在第4天(index3)才首度湊滿3天True
    k = pd.Series([75.0, 81.0, 82.0, 83.0, 79.0])
    d = pd.Series([75.0, 80.0, 81.0, 82.0, 78.0])
    result = is_high_dull(k, d, n=3)
    assert result.tolist() == [False, False, False, True, False]


def test_is_low_dull_requires_consecutive_n_days():
    # R-INDICATOR-11: 低檔鈍化與高檔完全對稱
    k = pd.Series([25.0, 19.0, 18.0, 17.0, 21.0])
    d = pd.Series([25.0, 20.0, 19.0, 18.0, 22.0])
    result = is_low_dull(k, d, n=3)
    assert result.tolist() == [False, False, False, True, False]
