import pandas as pd

from src.indicators.signal_backtest import arbitrate, forward_return_stats


def _close_series(values: list[float]) -> pd.Series:
    dates = pd.date_range("2026-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=dates)


def test_forward_return_stats_computes_win_rate_and_avg_return():
    close = _close_series([100.0, 100.0, 110.0, 90.0, 120.0, 80.0])
    dates = list(close.index)
    # 觸發日0：5天後(index5)從100->80，虧損；觸發日1：4天後(index5)從100->80，虧損
    # 改用horizon=2讓其中一筆賺、一筆賠，驗證win_rate不是0也不是1
    trigger_dates = [dates[0], dates[2]]  # index0(100)+2=index2(110)賺；index2(110)+2=index4(120)賺

    result = forward_return_stats(trigger_dates, close, horizon_days=2)

    assert result["n"] == 2
    assert result["win_rate"] == 1.0
    assert result["avg_return"] > 0


def test_forward_return_stats_excludes_triggers_without_enough_future_data():
    close = _close_series([100.0, 110.0, 120.0])
    dates = list(close.index)
    trigger_dates = [dates[-1]]  # 最後一天觸發，horizon_days=1沒有未來資料可算

    result = forward_return_stats(trigger_dates, close, horizon_days=1)

    assert result == {"n": 0, "win_rate": None, "avg_return": None, "returns": []}


def test_forward_return_stats_excludes_unknown_trigger_dates():
    close = _close_series([100.0, 110.0, 120.0, 130.0])
    unknown_date = pd.Timestamp("2099-01-01")

    result = forward_return_stats([unknown_date], close, horizon_days=1)

    assert result["n"] == 0


def test_forward_return_stats_mixed_win_and_loss():
    close = _close_series([100.0, 105.0, 95.0, 110.0])
    dates = list(close.index)
    trigger_dates = [dates[0], dates[1]]  # index0(100)->index1(105)賺；index1(105)->index2(95)賠

    result = forward_return_stats(trigger_dates, close, horizon_days=1)

    assert result["n"] == 2
    assert result["win_rate"] == 0.5


def test_arbitrate_returns_none_when_directions_agree():
    assert arbitrate("buy", "buy", future_return=0.05) is None


def test_arbitrate_picks_direction_matching_positive_future_return():
    assert arbitrate("buy", "sell", future_return=0.03) == "buy"


def test_arbitrate_picks_direction_matching_negative_future_return():
    assert arbitrate("buy", "sell", future_return=-0.03) == "sell"
