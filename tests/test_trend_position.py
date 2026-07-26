import pandas as pd

from src.indicators.trend_position import compute_trend_position


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="B")


def test_compute_trend_position_detects_at_high_after_significant_rally_then_flat_top():
    """從80漲到104(31%漲幅，遠超R-TREND-18的10%門檻)後打平在高點，應判定為「本波段高檔」，
    且is_at_low同一天必須是False(兩者互斥)。"""
    n = 60
    dates = _dates(n)
    close = []
    for i in range(n):
        if i < 20:
            close.append(100 - i * 1.0)  # 100 -> 81，先跌出一個底
        elif i < 50:
            close.append(80 + (i - 20) * 0.8)  # 80 -> 104，漲幅31%
        else:
            close.append(104.0)  # 打平在高點
    close = pd.Series(close, index=dates)
    high, low = close + 0.5, close - 0.5

    result = compute_trend_position(high, low, close, n=5)

    assert bool(result["is_at_high"].iloc[-1]) is True
    assert bool(result["is_at_low"].iloc[-1]) is False
    assert result["swing_pct"].iloc[-1] > 0.10


def test_compute_trend_position_detects_at_low_after_significant_decline_then_flat_bottom():
    """R-CLASSIC-15的鏡射版本：從119跌到96(19%跌幅)後打平在低點，應判定為「本波段低檔」。"""
    n = 60
    dates = _dates(n)
    close = []
    for i in range(n):
        if i < 20:
            close.append(100 + i * 1.0)  # 100 -> 119，先漲出一個頭
        elif i < 50:
            close.append(120 - (i - 20) * 0.8)  # 120 -> 96
        else:
            close.append(96.0)  # 打平在低點
    close = pd.Series(close, index=dates)
    high, low = close + 0.5, close - 0.5

    result = compute_trend_position(high, low, close, n=5)

    assert bool(result["is_at_low"].iloc[-1]) is True
    assert bool(result["is_at_high"].iloc[-1]) is False
    assert result["swing_pct"].iloc[-1] > 0.10


def test_compute_trend_position_stays_false_when_swing_below_10pct_threshold():
    """小幅上下震盪(振幅遠低於10%門檻)，全程都不該被判定為高檔或低檔——這種盤整格局
    對應R-SCREEN-04「位置」構面裡的「盤整」，不該被誤判成任何一種波段極端位置。"""
    n = 60
    dates = _dates(n)
    close = pd.Series([100.0 + (2 if i % 10 < 5 else -2) for i in range(n)], index=dates)
    high, low = close + 0.3, close - 0.3

    result = compute_trend_position(high, low, close, n=5)

    assert not result["is_at_high"].any()
    assert not result["is_at_low"].any()


def test_compute_trend_position_turns_off_once_pullback_exceeds_tolerance_zone():
    """漲了超過10%之後，一旦從波段高點回落超過容忍帶(5%)，is_at_high應該立刻轉為False——
    不需要等到SMA(n)正式翻轉確認新轉折點才失效，這是容忍帶存在的意義：即時反映「已經不在
    高點附近」，比死板地等正式轉折確認更貼近「趨勢位置」這個概念本身的即時性。"""
    n = 60
    dates = _dates(n)
    close = []
    for i in range(n):
        if i < 20:
            close.append(100 - i * 1.0)  # 100 -> 81
        elif i < 40:
            close.append(80 + (i - 20) * 1.5)  # 80 -> 110，漲幅37.5%
        else:
            close.append(110 - (i - 40) * 0.6)  # 回落，20天後跌到98(離高點110超過5%)
    close = pd.Series(close, index=dates)
    high, low = close + 0.5, close - 0.5

    result = compute_trend_position(high, low, close, n=5)

    # 回落中段(離高點已經超過5%容忍帶，但新一波下跌還沒達到10%門檻)：兩者皆應為False
    assert bool(result["is_at_high"].iloc[48]) is False
    assert bool(result["is_at_low"].iloc[48]) is False


def test_compute_trend_position_returns_all_false_when_not_enough_warmup_days():
    dates = _dates(3)
    close = pd.Series([100.0, 99.0, 98.0], index=dates)
    result = compute_trend_position(close, close, close, n=5)

    assert not result["is_at_high"].any()
    assert not result["is_at_low"].any()


def test_compute_trend_position_is_at_high_and_is_at_low_never_both_true_same_day():
    n = 60
    dates = _dates(n)
    close = []
    for i in range(n):
        if i < 20:
            close.append(100 - i * 1.0)
        elif i < 50:
            close.append(80 + (i - 20) * 0.8)
        else:
            close.append(104.0)
    close = pd.Series(close, index=dates)
    high, low = close + 0.5, close - 0.5

    result = compute_trend_position(high, low, close, n=5)

    assert (result["is_at_high"] & result["is_at_low"]).sum() == 0
