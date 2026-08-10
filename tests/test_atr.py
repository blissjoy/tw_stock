import pandas as pd

from src.indicators.atr import average_true_range, true_range


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="B")


def test_true_range_uses_largest_of_three_ranges():
    dates = _dates(3)
    high = pd.Series([10.0, 12.0, 11.0], index=dates)
    low = pd.Series([9.0, 10.5, 9.5], index=dates)
    close = pd.Series([9.5, 11.5, 10.0], index=dates)

    tr = true_range(high, low, close)

    # day2: high-low=1.5, |high-prev_close|=|12-9.5|=2.5, |low-prev_close|=|10.5-9.5|=1.0 -> max=2.5
    assert tr.iloc[1] == 2.5


def test_average_true_range_is_rolling_mean_of_true_range():
    n = 20
    dates = _dates(n)
    high = pd.Series([100.0 + i for i in range(n)], index=dates)
    low = pd.Series([98.0 + i for i in range(n)], index=dates)
    close = pd.Series([99.0 + i for i in range(n)], index=dates)

    atr = average_true_range(high, low, close, n=14)

    assert pd.isna(atr.iloc[12])
    assert atr.iloc[13] > 0
