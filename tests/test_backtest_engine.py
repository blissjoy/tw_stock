import pandas as pd
import pytest

from src.backtest.engine import Trade, run_backtest, summarize_trades


def test_run_backtest_exits_on_signal():
    close = pd.Series([10.0, 11.0, 12.0, 13.0, 10.0])
    high = pd.Series([10.5, 11.5, 12.5, 13.5, 10.5])
    low = pd.Series([9.5, 10.5, 11.5, 12.5, 9.5])
    entry_signal = pd.Series([False, True, False, False, False])
    exit_signal = pd.Series([False, False, False, True, False])

    trades = run_backtest(close, high, low, entry_signal, exit_signal, stop_loss_pct=0.05)

    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_index == 1 and trade.entry_price == 11.0
    assert trade.exit_index == 3 and trade.exit_price == 13.0
    assert trade.exit_reason == "訊號出場"
    assert trade.return_pct == pytest.approx((13.0 - 11.0) / 11.0)
    assert trade.is_win == True


def test_run_backtest_exits_on_stop_loss():
    close = pd.Series([10.0, 11.0, 9.0, 8.0, 7.0])
    high = pd.Series([10.5, 11.5, 9.5, 8.5, 7.5])
    low = pd.Series([9.5, 10.5, 8.5, 7.5, 6.5])
    entry_signal = pd.Series([False, True, False, False, False])
    exit_signal = pd.Series([False, False, False, False, False])

    trades = run_backtest(close, high, low, entry_signal, exit_signal, stop_loss_pct=0.05)

    assert len(trades) == 1
    trade = trades[0]
    # 進場價11，停損價 = 11*0.95 = 10.45；第2天(index2)低點8.5觸及停損
    assert trade.stop_price == pytest.approx(10.45)
    assert trade.exit_index == 2
    assert trade.exit_price == pytest.approx(10.45)
    assert trade.exit_reason == "停損"
    assert trade.is_win == False


def test_run_backtest_force_closes_open_position_at_end():
    close = pd.Series([10.0, 11.0, 12.0])
    high = pd.Series([10.5, 11.5, 12.5])
    low = pd.Series([9.5, 10.5, 11.5])
    entry_signal = pd.Series([False, True, False])
    exit_signal = pd.Series([False, False, False])

    trades = run_backtest(close, high, low, entry_signal, exit_signal, stop_loss_pct=0.05)

    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_index == 2
    assert trade.exit_price == 12.0
    assert trade.exit_reason == "回測結束強制平倉"


def test_run_backtest_short_direction_mirrors_long():
    close = pd.Series([10.0, 9.0, 11.0])
    high = pd.Series([10.5, 9.5, 11.5])
    low = pd.Series([9.5, 8.5, 10.5])
    entry_signal = pd.Series([False, True, False])
    exit_signal = pd.Series([False, False, False])

    trades = run_backtest(close, high, low, entry_signal, exit_signal, direction="short", stop_loss_pct=0.05)

    assert len(trades) == 1
    trade = trades[0]
    # 放空進場價9，停損價 = 9*1.05 = 9.45；第2天(index2)高點11.5觸及停損
    assert trade.stop_price == pytest.approx(9.45)
    assert trade.exit_reason == "停損"
    assert trade.return_pct == pytest.approx((9.0 - 9.45) / 9.0)


def test_summarize_trades_matches_hand_calculation():
    def make_trade(exit_price):
        return Trade(direction="long", entry_index=0, entry_date=None, entry_price=100.0, exit_index=1, exit_date=None, exit_price=exit_price, exit_reason="x")

    trades = [make_trade(110.0), make_trade(95.0), make_trade(120.0), make_trade(97.0)]
    summary = summarize_trades(trades)

    assert summary.total_trades == 4
    assert summary.win_count == 2
    assert summary.loss_count == 2
    assert summary.win_rate == pytest.approx(0.5)
    assert summary.avg_win_pct == pytest.approx(0.15)
    assert summary.avg_loss_pct == pytest.approx(-0.04)
    assert summary.max_single_loss_pct == pytest.approx(-0.05)
    assert summary.total_compounded_return_pct == pytest.approx(1.1 * 0.95 * 1.2 * 0.97 - 1)


def test_summarize_trades_empty_list():
    summary = summarize_trades([])
    assert summary.total_trades == 0
    assert summary.win_rate == 0.0
