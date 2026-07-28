import pandas as pd

from src.indicators.margin_trading import (
    MARGIN_LIQUIDATION_RATIO,
    MARGIN_WARNING_RATIO,
    classify_margin_maintenance_state,
    compute_margin_maintenance_ratio,
    margin_oversold_rebound_signal,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="B")


def test_compute_margin_maintenance_ratio_matches_book_example_at_initial_purchase():
    """書中範例：100元買進、6成融資(借60元)，維持率一開始應為166%(100/60)。"""
    dates = _dates(1)
    close = pd.Series([100.0], index=dates)
    buy = pd.Series([1000], index=dates)
    sell = pd.Series([0], index=dates)
    repay = pd.Series([0], index=dates)
    balance = pd.Series([1000], index=dates)

    ratio = compute_margin_maintenance_ratio(close, buy, sell, repay, balance, margin_pct=0.6)

    assert round(ratio.iloc[0], 2) == round(100 / 60, 2)


def test_compute_margin_maintenance_ratio_falls_as_price_falls_with_fixed_cost_basis():
    """買進成本固定在100元，股價跌到72元時，維持率應降到120%(斷頭線)，跌到81元時應為135%(警戒)。"""
    dates = _dates(3)
    close = pd.Series([100.0, 81.0, 72.0], index=dates)
    buy = pd.Series([1000, 0, 0], index=dates)
    sell = pd.Series([0, 0, 0], index=dates)
    repay = pd.Series([0, 0, 0], index=dates)
    balance = pd.Series([1000, 1000, 1000], index=dates)

    ratio = compute_margin_maintenance_ratio(close, buy, sell, repay, balance, margin_pct=0.6)

    assert round(ratio.iloc[1], 2) == round(81 / 60, 2)
    assert round(ratio.iloc[2], 2) == round(72 / 60, 2)
    assert ratio.iloc[1] >= MARGIN_WARNING_RATIO
    assert round(ratio.iloc[2], 2) == MARGIN_LIQUIDATION_RATIO


def test_compute_margin_maintenance_ratio_unchanged_when_balance_only_decreases():
    """賣出/償還導致餘額減少時，剩餘部位的加權平均成本不變，維持率只反映股價變動。"""
    dates = _dates(2)
    close = pd.Series([100.0, 100.0], index=dates)
    buy = pd.Series([1000, 0], index=dates)
    sell = pd.Series([0, 400], index=dates)
    repay = pd.Series([0, 0], index=dates)
    balance = pd.Series([1000, 600], index=dates)

    ratio = compute_margin_maintenance_ratio(close, buy, sell, repay, balance, margin_pct=0.6)

    assert round(ratio.iloc[0], 4) == round(ratio.iloc[1], 4)


def test_compute_margin_maintenance_ratio_weighted_average_on_new_purchase():
    """第2天股價200元時再加碼買進(餘額翻倍)，加權平均成本應為100與200的均值150。"""
    dates = _dates(2)
    close = pd.Series([100.0, 200.0], index=dates)
    buy = pd.Series([1000, 1000], index=dates)
    sell = pd.Series([0, 0], index=dates)
    repay = pd.Series([0, 0], index=dates)
    balance = pd.Series([1000, 2000], index=dates)

    ratio = compute_margin_maintenance_ratio(close, buy, sell, repay, balance, margin_pct=0.6)

    expected_avg_cost = (100 * 1000 + 200 * 1000) / 2000  # 150
    assert round(ratio.iloc[1], 4) == round(200 / (expected_avg_cost * 0.6), 4)


def test_compute_margin_maintenance_ratio_none_when_no_margin_balance():
    dates = _dates(1)
    close = pd.Series([100.0], index=dates)
    buy = pd.Series([0], index=dates)
    sell = pd.Series([0], index=dates)
    repay = pd.Series([0], index=dates)
    balance = pd.Series([0], index=dates)

    ratio = compute_margin_maintenance_ratio(close, buy, sell, repay, balance)

    assert pd.isna(ratio.iloc[0])


def test_classify_margin_maintenance_state_thresholds():
    assert classify_margin_maintenance_state(1.66) == "正常"
    assert classify_margin_maintenance_state(1.30) == "警戒區(爹不疼娘不愛)"
    assert classify_margin_maintenance_state(1.10) == "已跌破斷頭線"
    assert classify_margin_maintenance_state(None) == "無融資部位"
    assert classify_margin_maintenance_state(float("nan")) == "無融資部位"


def test_margin_oversold_rebound_signal_requires_consecutive_days_below_liquidation():
    dates = _dates(5)
    ratio = pd.Series([1.30, 1.15, 1.10, 1.05, 1.66], index=dates)

    signal = margin_oversold_rebound_signal(ratio, min_consecutive_days=3)

    assert bool(signal.iloc[3]) is True  # 第2~4天(index1,2,3)連續3天(1.15,1.10,1.05)都<120%
    assert bool(signal.iloc[2]) is False  # 往回數只有2天(index1,2)在120%以下，還不夠3天
    assert bool(signal.iloc[4]) is False  # 已經反彈回166%，不再符合
