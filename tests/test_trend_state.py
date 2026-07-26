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


def _bypass_simplify(monkeypatch):
    """這些測試要驗證的是is_bull_trend/is_bear_trend的wiring本身，不是R-TREND-02降噪
    邏輯(降噪有tests/test_trend.py專屬測試)——用固定的小振幅假資料時，降噪可能會把
    測試特別設計的頭/底配對合併掉，所以這裡把simplify_turning_points短路成原樣傳回。"""
    monkeypatch.setattr(trend_state, "simplify_turning_points", lambda tps, min_swing_pct=0.10: tps)


def test_classify_trend_state_returns_bull_when_heads_and_bottoms_both_rising(monkeypatch):
    _bypass_simplify(monkeypatch)
    monkeypatch.setattr(trend_state, "compute_turning_points", lambda h, l, c, n=5: _fake_points([
        ("bottom", 90), ("head", 100), ("bottom", 95), ("head", 105),
    ]))
    close = pd.Series([100.0])

    assert classify_trend_state(close, close, close) == "多頭"


def test_classify_trend_state_returns_bear_when_heads_and_bottoms_both_falling(monkeypatch):
    _bypass_simplify(monkeypatch)
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
    _bypass_simplify(monkeypatch)
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
    assert result["短期"].timeframe == "日線"
    assert result["中期"].timeframe == "週線"
    assert result["長期"].timeframe == "月線"


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

    assert result["短期"].trend == "空頭"
    assert result["中期"].trend == "多頭"
    assert result["長期"].trend == "多頭"


def test_classify_trend_states_multi_horizon_smoke_test_does_not_crash_on_realistic_data():
    """不mock，直接用真實的日線資料端對端驗證重新取樣+轉折點串接沒有斷掉、回傳合法值。"""
    high, low, close = _make_daily_series()

    result = classify_trend_states_multi_horizon(high, low, close)

    assert set(result.keys()) == {"短期", "中期", "長期"}
    for label, expected_timeframe in [("短期", "日線"), ("中期", "週線"), ("長期", "月線")]:
        assert result[label].timeframe == expected_timeframe
        assert result[label].trend in ("多頭", "空頭", "盤整")
        assert isinstance(result[label].reason, str) and result[label].reason  # 一定要有非空的判斷依據文字


def test_classify_trend_states_multi_horizon_falls_back_to_range_when_resampled_data_too_short():
    """只給很短的日線歷史(例如剛好120天)時，重新取樣出來的月線可能只有4~5根K棒，遠不足以
    找到2組頭與2組底——這時應該安全回傳「盤整」而不是crash，呼叫端(chart_data.py的
    TREND_LOOKBACK_DAYS說明)要留意這個資料量不足的情境。"""
    high, low, close = _make_daily_series(n_days=20)

    result = classify_trend_states_multi_horizon(high, low, close)

    assert result["長期"].trend == "盤整"


def test_classify_trend_states_multi_horizon_reason_shows_actual_head_and_bottom_prices(monkeypatch):
    """使用者質疑「短線顯示空頭/中長線顯示盤整的依據是什麼」——reason必須附上實際的頭部/
    底部價格與頭頭高低/底底高低的判讀，不能只回傳一個「多頭/空頭/盤整」結論字串，使用者才能
    自己核對演算法有沒有算錯。"""
    _bypass_simplify(monkeypatch)
    points = _fake_points([("bottom", 90), ("head", 100), ("bottom", 95), ("head", 105)])
    monkeypatch.setattr(trend_state, "compute_turning_points", lambda h, l, c, n=5: points)
    high, low, close = _make_daily_series()

    result = classify_trend_states_multi_horizon(high, low, close)

    assert result["短期"].trend == "多頭"
    assert "頭頭高" in result["短期"].reason
    assert "底底高" in result["短期"].reason
    assert "100.00" in result["短期"].reason and "105.00" in result["短期"].reason  # 頭：100→105
    assert "90.00" in result["短期"].reason and "95.00" in result["短期"].reason  # 底：90→95


def test_classify_trend_states_multi_horizon_reason_explains_insufficient_turning_points(monkeypatch):
    """轉折點不足2組頭與2組底時判定成「盤整」，reason要說明是「資料不足」，不能讓使用者
    誤以為這是演算法真的判斷出「盤整」這個技術含義。"""
    monkeypatch.setattr(trend_state, "compute_turning_points", lambda h, l, c, n=5: _fake_points([("bottom", 90)]))
    high, low, close = _make_daily_series()

    result = classify_trend_states_multi_horizon(high, low, close)

    assert result["短期"].trend == "盤整"
    assert "轉折點不足" in result["短期"].reason


def _make_series_with_one_reversal(n_days: int = 400):
    """跟_make_daily_series()不同：造一段「先跌later漲」的價格路徑，確保compute_turning_
    points()至少能找到1組頭/底，供freshness測試使用(純單調上升的_make_daily_series()永遠
    不會觸發任何轉折，freshness會固定回傳「尚無確認轉折點」，測不到警語分支)。"""
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    half = n_days // 2
    close = pd.Series(
        [100 - i * 0.1 for i in range(half)] + [100 - half * 0.1 + i * 0.1 for i in range(n_days - half)],
        index=dates,
    )
    return close + 1, close - 1, close


def test_classify_trend_states_multi_horizon_freshness_warns_when_unconfirmed_swing_in_progress(monkeypatch):
    """使用者拿2634實際案例反問「週線/月線明明正在噴出，為什麼還顯示空頭」——freshness要
    明確標註「目前正處於一段還沒被確認的新波段中」，不能讓使用者誤以為trend/reason反映的
    是最新盤面。"""
    monkeypatch.setattr(
        trend_state, "compute_trend_position",
        lambda h, l, c, n=5: pd.DataFrame(
            {"is_at_high": [True], "is_at_low": [False], "swing_pct": [0.519]}, index=c.index[-1:],
        ),
    )
    high, low, close = _make_series_with_one_reversal()

    result = classify_trend_states_multi_horizon(high, low, close)

    assert "⚠️" in result["短期"].freshness
    assert "還沒回頭確認" in result["短期"].freshness
    assert "51.9%" in result["短期"].freshness


def test_classify_trend_states_multi_horizon_freshness_plain_when_no_unconfirmed_swing(monkeypatch):
    monkeypatch.setattr(
        trend_state, "compute_trend_position",
        lambda h, l, c, n=5: pd.DataFrame(
            {"is_at_high": [False], "is_at_low": [False], "swing_pct": [0.0]}, index=c.index[-1:],
        ),
    )
    high, low, close = _make_series_with_one_reversal()

    result = classify_trend_states_multi_horizon(high, low, close)

    assert "⚠️" not in result["短期"].freshness
    assert "最近一次確認的轉折點" in result["短期"].freshness


def test_classify_trend_states_multi_horizon_denoises_insignificant_turning_points():
    """端對端驗證R-TREND-02降噪確實接上了：造一段「先有一個振幅<10%的小雜訊擺動、再走出
    一段真正>=10%的波段」的真實價格路徑，reason裡不應該出現那個雜訊轉折點的價格。"""
    n = 120
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = []
    for i in range(n):
        if i < 40:
            close.append(100.0)  # 暖身用平盤
        elif i < 50:
            close.append(100 + (i - 40) * 0.2)  # 100->102，振幅2%，雜訊
        elif i < 60:
            close.append(102 - (i - 50) * 0.2)  # 102->100，雜訊回落
        else:
            close.append(100 + (i - 60) * 0.5)  # 100->130，振幅30%，真正的波段
    close = pd.Series(close, index=dates)
    high, low = close + 0.3, close - 0.3

    result = classify_trend_states_multi_horizon(high, low, close)

    assert "102.00" not in result["短期"].reason
