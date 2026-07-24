import pandas as pd

import src.patterns.trend_state as trend_state
from src.indicators.pivots import TurningPoint
from src.patterns.trend_state import classify_trend_state


def _fake_points(pairs):
    """pairs: 依時間順序排列的(type, price) list，組成假的compute_turning_points()回傳值。"""
    return [TurningPoint(type=t, price=p, index=i) for i, (t, p) in enumerate(pairs)]


def test_classify_trend_state_returns_bull_when_heads_and_bottoms_both_rising(monkeypatch):
    monkeypatch.setattr(trend_state, "compute_turning_points", lambda h, l, c, n=5: _fake_points([
        ("bottom", 90), ("head", 100), ("bottom", 95), ("head", 105),
    ]))
    close = pd.Series([100.0])

    assert classify_trend_state(close, close, close) == "多頭"


def test_classify_trend_state_returns_bear_when_heads_and_bottoms_both_falling(monkeypatch):
    monkeypatch.setattr(trend_state, "compute_turning_points", lambda h, l, c, n=5: _fake_points([
        ("head", 110), ("bottom", 100), ("head", 105), ("bottom", 95),
    ]))
    close = pd.Series([100.0])

    assert classify_trend_state(close, close, close) == "空頭"


def test_classify_trend_state_returns_range_when_not_enough_turning_points(monkeypatch):
    monkeypatch.setattr(trend_state, "compute_turning_points", lambda h, l, c, n=5: _fake_points([
        ("bottom", 90), ("head", 100),
    ]))
    close = pd.Series([100.0])

    assert classify_trend_state(close, close, close) == "盤整"


def test_classify_trend_state_returns_range_when_signals_mixed(monkeypatch):
    """頭頭高但底底低(不符合多頭定義「兩者缺一不可」)，也不符合空頭定義，應歸為盤整。"""
    monkeypatch.setattr(trend_state, "compute_turning_points", lambda h, l, c, n=5: _fake_points([
        ("bottom", 95), ("head", 100), ("bottom", 90), ("head", 105),
    ]))
    close = pd.Series([100.0])

    assert classify_trend_state(close, close, close) == "盤整"


def test_classify_trend_state_separates_heads_and_bottoms_by_chronological_order(monkeypatch):
    """確認heads/bottoms清單各自保留原始交替序列裡的時間順序(不是重新排序)，
    is_bull_trend/is_bear_trend比較的是"最後一個"跟"倒數第二個"，順序顛倒會誤判。"""
    captured = {}

    def _fake_is_bull_trend(heads, bottoms):
        captured["heads"] = heads
        captured["bottoms"] = bottoms
        return False

    monkeypatch.setattr(trend_state, "compute_turning_points", lambda h, l, c, n=5: _fake_points([
        ("bottom", 90), ("head", 100), ("bottom", 95), ("head", 105), ("bottom", 98),
    ]))
    monkeypatch.setattr(trend_state, "is_bull_trend", _fake_is_bull_trend)
    monkeypatch.setattr(trend_state, "is_bear_trend", lambda heads, bottoms: False)
    close = pd.Series([100.0])

    classify_trend_state(close, close, close)

    assert captured["heads"] == [100, 105]
    assert captured["bottoms"] == [90, 95, 98]


def test_classify_trend_state_smoke_test_does_not_crash_on_realistic_data():
    """不mock，直接用真實的多週期價格資料端對端驗證整條串接沒有斷掉、回傳合法值。"""
    n = 60
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    close = pd.Series([100 + i * 0.8 + (5 if i % 10 < 5 else -5) for i in range(n)], index=dates)
    high = close + 1
    low = close - 1

    result = classify_trend_state(high, low, close)

    assert result in ("多頭", "空頭", "盤整")
