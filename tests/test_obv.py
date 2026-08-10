import pandas as pd

from src.indicators.obv import on_balance_volume


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="B")


def test_on_balance_volume_adds_on_up_day_subtracts_on_down_day():
    dates = _dates(4)
    close = pd.Series([10.0, 11.0, 10.5, 10.5], index=dates)
    volume = pd.Series([1000, 2000, 1500, 900], index=dates)

    obv = on_balance_volume(close, volume)

    assert obv.iloc[0] == 0  # 第一天沒有前一天可比較，diff()是NaN->0，不加減
    assert obv.iloc[1] == 2000  # 上漲日：+volume
    assert obv.iloc[2] == 2000 - 1500  # 下跌日：-volume
    assert obv.iloc[3] == 2000 - 1500  # 平盤日：不變
