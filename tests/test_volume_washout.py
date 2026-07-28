import pandas as pd

from src.indicators.volume_washout import volume_washout_signal


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="B")


def test_volume_washout_signal_true_when_recent_volume_shrinks_to_tenth_of_sustained_peak():
    n = 250
    dates = _dates(n)
    volume = [1000.0] * n
    for i in range(95, 105):
        volume[i] = 100_000.0  # 連續10天量能都很大(持續放量的高峰期)，非單日尖峰
    for i in range(240, 250):
        volume[i] = 9000.0  # 近期均量約是峰值均量的9%，低於10%門檻
    volume_series = pd.Series(volume, index=dates)

    signal = volume_washout_signal(volume_series, lookback=240, shrink_ratio=0.10, recent_window=5)

    assert bool(signal.iloc[-1]) is True


def test_volume_washout_signal_dampens_single_day_volume_spike():
    """單一天的爆量離群值(如大宗交易)經過5日均量平滑後，對高峰基準的影響應大幅降低
    (2026-07-29教訓：真實DB案例1102單日爆量約平常20倍，修正前用單日最大量會誤觸發)。"""
    n = 250
    dates = _dates(n)
    volume = [10_000.0] * n
    volume[100] = 200_000.0  # 單日爆量約平常20倍(貼近真實案例1102的量級)
    volume_series = pd.Series(volume, index=dates)

    signal = volume_washout_signal(volume_series, lookback=240, shrink_ratio=0.10, recent_window=5)

    # 5日均量平滑後高峰基準約為平常的4.8倍(遠低於修正前的20倍)，10%門檻約0.48倍平常量，
    # 近期均量(1萬)並未低於這個門檻，不應觸發
    assert bool(signal.iloc[-1]) is False


def test_volume_washout_signal_false_when_recent_volume_still_large():
    n = 250
    dates = _dates(n)
    volume = [1000.0] * n
    for i in range(95, 105):
        volume[i] = 100_000.0  # 持續放量的高峰期
    for i in range(240, 250):
        volume[i] = 20_000.0  # 近期均量遠高於門檻(峰值10%)，尚未量縮
    volume_series = pd.Series(volume, index=dates)

    signal = volume_washout_signal(volume_series, lookback=240, shrink_ratio=0.10, recent_window=5)

    assert bool(signal.iloc[-1]) is False


def test_volume_washout_signal_none_before_lookback_window_filled():
    n = 50
    dates = _dates(n)
    volume_series = pd.Series([1000.0] * n, index=dates)

    signal = volume_washout_signal(volume_series, lookback=240, shrink_ratio=0.10, recent_window=5)

    assert bool(signal.iloc[-1]) is False  # 還沒累積滿240天，peak為NaN，不觸發
