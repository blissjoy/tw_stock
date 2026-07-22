import pandas as pd

from src.backtest.engine import run_backtest, summarize_trades
from src.backtest.strategies import golden_cross_trend_strategy


def _synthetic_close_series() -> pd.Series:
    # 前25天緩跌、接著30天急漲(確保MA5黃金交叉MA20且多頭排列)、最後15天急跌(確保死亡交叉)
    down = [100 - i * 0.5 for i in range(25)]
    up = [down[-1] + (i + 1) * 1.5 for i in range(30)]
    down2 = [up[-1] - (i + 1) * 2.0 for i in range(15)]
    return pd.Series(down + up + down2)


def test_golden_cross_strategy_wires_into_backtest_engine():
    close = _synthetic_close_series()
    high = close + 0.5
    low = close - 0.5

    entry_signal, exit_signal = golden_cross_trend_strategy(close, periods=(5, 10, 20))
    assert len(entry_signal) == len(close)
    assert entry_signal.dtype == bool
    assert exit_signal.dtype == bool

    trades = run_backtest(close, high, low, entry_signal, exit_signal, stop_loss_pct=0.05)
    assert len(trades) >= 1

    summary = summarize_trades(trades)
    assert summary.total_trades == len(trades)
    assert 0.0 <= summary.win_rate <= 1.0
    # 急漲段應該至少捕捉到一筆正報酬交易
    assert any(t.return_pct is not None and t.return_pct > 0 for t in trades)
