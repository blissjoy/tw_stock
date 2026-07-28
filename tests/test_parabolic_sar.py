import pandas as pd
import pytest

from src.indicators.parabolic_sar import compute_sar, sar_flip_days_ago, sar_flipped_within


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="B")


def test_compute_sar_returns_empty_series_when_fewer_than_two_days():
    high = pd.Series([10.0], index=_dates(1))
    low = pd.Series([9.0], index=_dates(1))
    close = pd.Series([9.5], index=_dates(1))

    sar_bull, sar_values = compute_sar(high, low, close)

    assert sar_bull.empty
    assert sar_values.isna().all()


def test_compute_sar_two_days_uses_high_comparison_for_initial_bull_flag():
    dates = _dates(2)
    high = pd.Series([10.0, 11.0], index=dates)
    low = pd.Series([9.0, 10.0], index=dates)
    close = pd.Series([9.5, 10.5], index=dates)

    sar_bull, sar_values = compute_sar(high, low, close)

    assert sar_bull.tolist() == [True, True]
    assert sar_values.isna().all()


def test_compute_sar_flips_to_bear_on_sharp_drop_below_sar():
    """手動逐日追算的固定案例(見docstring推導)：連續3天緩步走高後，第5天暴跌，SAR應在
    第5天(index4)翻轉為空頭，前4天維持多頭。"""
    dates = _dates(5)
    high = pd.Series([10.0, 11.0, 12.0, 13.0, 9.0], index=dates)
    low = pd.Series([9.0, 10.0, 10.5, 11.5, 8.0], index=dates)
    close = pd.Series([9.5, 10.5, 11.0, 12.0, 8.5], index=dates)

    sar_bull, sar_values = compute_sar(high, low, close)

    assert sar_bull.tolist() == [True, True, True, True, False]
    assert sar_values.iloc[0] == 9.0
    assert sar_values.iloc[1] == 9.0
    assert round(sar_values.iloc[2], 4) == 9.06
    assert round(sar_values.iloc[3], 4) == 9.2364
    assert sar_values.iloc[4] == 13.0  # 翻轉當天SAR跳回max(前波高點, 當天最高價)


def test_compute_sar_stays_bearish_through_sustained_downtrend():
    dates = _dates(6)
    high = pd.Series([20.0, 19.0, 18.0, 17.0, 16.0, 15.0], index=dates)
    low = pd.Series([19.0, 18.0, 17.0, 16.0, 15.0, 14.0], index=dates)
    close = pd.Series([19.5, 18.5, 17.5, 16.5, 15.5, 14.5], index=dates)

    sar_bull, _ = compute_sar(high, low, close)

    assert not sar_bull.iloc[-1]
    assert sar_bull.tolist().count(True) == 0


@pytest.mark.parametrize(
    "flags, expected_days_ago",
    [
        ([True, True, False, False, False], 3),
        ([False, True, True], 2),
        ([True, True, True], None),
        ([False], None),
    ],
)
def test_sar_flip_days_ago(flags, expected_days_ago):
    sar_bull = pd.Series(flags)

    assert sar_flip_days_ago(sar_bull) == expected_days_ago


def test_sar_flip_days_ago_empty_series_returns_none():
    assert sar_flip_days_ago(pd.Series(dtype=bool)) is None


def test_sar_flipped_within_matches_direction_and_recent_flip():
    sar_bull = pd.Series([True, True, False, False, False])  # 空頭，3天前翻轉

    assert sar_flipped_within(sar_bull, direction="空頭", within_days=3) is True
    assert sar_flipped_within(sar_bull, direction="空頭", within_days=2) is False
    assert sar_flipped_within(sar_bull, direction="多頭", within_days=60) is False


def test_sar_flipped_within_default_one_day_means_flipped_today():
    flipped_today = pd.Series([True, True, False])
    flipped_yesterday = pd.Series([True, False, False])

    assert sar_flipped_within(flipped_today, direction="空頭") is True
    assert sar_flipped_within(flipped_yesterday, direction="空頭") is False


def test_sar_flipped_within_empty_series_returns_false():
    assert sar_flipped_within(pd.Series(dtype=bool), direction="多頭", within_days=1) is False
