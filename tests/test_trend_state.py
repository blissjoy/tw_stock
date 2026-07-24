import pandas as pd

import src.patterns.trend_state as trend_state
from src.indicators.pivots import TurningPoint
from src.patterns.trend_state import (
    TREND_TURNING_POINT_N,
    classify_trend_state,
    classify_trend_states_multi_horizon,
)


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


def _make_daily_series(n_days: int = 400):
    """造一段跨越足夠長時間(預設約1.5年交易日)的日線close，讓resample成週線/月線後
    仍有夠多根K棒可用。"""
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    close = pd.Series([100 + i * 0.1 for i in range(n_days)], index=dates)
    return close + 1, close - 1, close  # high, low, close


def test_classify_trend_states_multi_horizon_uses_fixed_n_and_resamples_by_timeframe(monkeypatch):
    """依R-INDICATOR-10「做短線看日線、中期看週線、長期看月線」的定義，短/中/長三個天期
    都用同一個N=5呼叫classify_trend_state(演算法參數不變)，差別在於餵進去的high/low/close
    是重新取樣過的週線/月線資料(比原始日線筆數少)，不是像R-TREND-01那樣改N。"""
    captured = []

    def _fake_classify(h, l, c, n=5):
        captured.append((len(c), n))
        return "多頭"

    monkeypatch.setattr(trend_state, "classify_trend_state", _fake_classify)
    high, low, close = _make_daily_series()

    result = classify_trend_states_multi_horizon(high, low, close)

    assert [n for _, n in captured] == [TREND_TURNING_POINT_N] * 3
    daily_len, weekly_len, monthly_len = (length for length, _ in captured)
    assert daily_len == len(close)
    assert weekly_len < daily_len  # 週線筆數應該遠少於日線
    assert monthly_len < weekly_len  # 月線筆數應該又比週線更少
    assert result["短線"].timeframe == "日線"
    assert result["中線"].timeframe == "週線"
    assert result["長線"].timeframe == "月線"


def test_classify_trend_states_multi_horizon_can_disagree_across_periods(monkeypatch):
    """日線走空、週線仍是多頭這種不一致的情境，三個天期應該各自獨立算出結果，
    不會被互相覆蓋——這正是使用者要求分開顯示短/中/長趨勢的核心理由。"""
    call_order = []

    def _fake_classify(h, l, c, n=5):
        call_order.append(len(c))
        return "空頭" if len(call_order) == 1 else "多頭"  # 第一次呼叫(日線)走空，其餘走多

    monkeypatch.setattr(trend_state, "classify_trend_state", _fake_classify)
    high, low, close = _make_daily_series()

    result = classify_trend_states_multi_horizon(high, low, close)

    assert result["短線"].trend == "空頭"
    assert result["中線"].trend == "多頭"
    assert result["長線"].trend == "多頭"


def test_classify_trend_states_multi_horizon_smoke_test_does_not_crash_on_realistic_data():
    """不mock，直接用真實的日線資料端對端驗證重新取樣+轉折點串接沒有斷掉、回傳合法值。"""
    high, low, close = _make_daily_series()

    result = classify_trend_states_multi_horizon(high, low, close)

    assert set(result.keys()) == {"短線", "中線", "長線"}
    for label, expected_timeframe in [("短線", "日線"), ("中線", "週線"), ("長線", "月線")]:
        assert result[label].timeframe == expected_timeframe
        assert result[label].trend in ("多頭", "空頭", "盤整")


def test_classify_trend_states_multi_horizon_falls_back_to_range_when_resampled_data_too_short():
    """只給很短的日線歷史(例如剛好120天)時，重新取樣出來的月線可能只有4~5根K棒，遠不足以
    找到2組頭與2組底——這時應該安全回傳「盤整」而不是crash，呼叫端(chart_data.py的
    TREND_LOOKBACK_DAYS說明)要留意這個資料量不足的情境。"""
    high, low, close = _make_daily_series(n_days=20)

    result = classify_trend_states_multi_horizon(high, low, close)

    assert result["長線"].trend == "盤整"
