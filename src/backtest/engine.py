"""簡化回測框架：把 ai/zhu-rules 規則庫組出的進出場訊號，套用在歷史OHLCV上模擬交易。

設計原則：這裡不重新判斷任何技術面邏輯，呼叫端負責用 src/indicators、src/strategies 等
既有規則函式算出 entry_signal / exit_signal 兩條布林 Series（同一個時間軸），本引擎只負責
「訊號→模擬持倉→記錄交易→統計績效」這一段單純的執行邏輯，讓「規則庫」與「回測執行」保持
清楚分工，规则本身的正確性已由各自的單元測試保證，這裡只驗證「組合起來的進出場機制」對不對。

停損統一用進場價的固定百分比（預設5%，呼應 R-RISK-03 全年獲利率方程式的停損假設），若要套用
R-MA-21 的5%分界法或其他更精細的停損規則，由呼叫端自行算出每筆交易的停損價，透過
`stop_loss_price_fn` 覆寫預設的固定百分比停損。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

Direction = str  # "long" or "short"


@dataclass
class Trade:
    direction: Direction
    entry_index: int
    entry_date: object
    entry_price: float
    stop_price: float = 0.0
    exit_index: int | None = None
    exit_date: object | None = None
    exit_price: float | None = None
    exit_reason: str | None = None

    @property
    def return_pct(self) -> float | None:
        if self.exit_price is None:
            return None
        if self.direction == "long":
            return (self.exit_price - self.entry_price) / self.entry_price
        return (self.entry_price - self.exit_price) / self.entry_price

    @property
    def is_win(self) -> bool | None:
        if self.return_pct is None:
            return None
        return self.return_pct > 0


def run_backtest(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    entry_signal: pd.Series,
    exit_signal: pd.Series,
    direction: Direction = "long",
    stop_loss_pct: float = 0.05,
    stop_loss_price_fn=None,
) -> list[Trade]:
    """逐日模擬單一部位進出場：空手時遇entry_signal進場；持倉時遇exit_signal或觸及停損價出場。

    同一時間只持有0或1個部位（不加碼、不同時多筆）。停損以「盤中最低/最高價是否觸及」判斷，
    觸及當天即以停損價出場；訊號出場則以當天收盤價出場。回測資料結束時若仍持倉，用最後一天
    收盤價強制平倉並標註原因，避免尚未平倉的部位被忽略而低估風險。
    """
    trades: list[Trade] = []
    position: Trade | None = None
    n = len(close)

    for t in range(n):
        if position is None:
            if bool(entry_signal.iloc[t]):
                entry_price = close.iloc[t]
                stop_price = (
                    stop_loss_price_fn(t, entry_price)
                    if stop_loss_price_fn is not None
                    else (entry_price * (1 - stop_loss_pct) if direction == "long" else entry_price * (1 + stop_loss_pct))
                )
                position = Trade(direction=direction, entry_index=t, entry_date=close.index[t], entry_price=entry_price, stop_price=stop_price)
            continue

        stop_price = position.stop_price
        hit_stop = (low.iloc[t] <= stop_price) if direction == "long" else (high.iloc[t] >= stop_price)
        hit_exit = bool(exit_signal.iloc[t])

        if hit_stop or hit_exit:
            position.exit_index = t
            position.exit_date = close.index[t]
            position.exit_price = stop_price if hit_stop else close.iloc[t]
            position.exit_reason = "停損" if hit_stop else "訊號出場"
            trades.append(position)
            position = None

    if position is not None:
        position.exit_index = n - 1
        position.exit_date = close.index[-1]
        position.exit_price = close.iloc[-1]
        position.exit_reason = "回測結束強制平倉"
        trades.append(position)

    return trades


@dataclass
class BacktestSummary:
    total_trades: int
    win_count: int
    loss_count: int
    win_rate: float
    avg_return_pct: float
    total_compounded_return_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    max_single_loss_pct: float


def summarize_trades(trades: list[Trade]) -> BacktestSummary:
    """把交易清單彙整成績效統計，win_rate/avg_return可直接代入 R-RISK-03 全年獲利率方程式驗證。"""
    closed = [t for t in trades if t.return_pct is not None]
    total = len(closed)
    if total == 0:
        return BacktestSummary(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    returns = [t.return_pct for t in closed]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    compounded = 1.0
    for r in returns:
        compounded *= 1 + r

    return BacktestSummary(
        total_trades=total,
        win_count=len(wins),
        loss_count=len(losses),
        win_rate=len(wins) / total,
        avg_return_pct=sum(returns) / total,
        total_compounded_return_pct=compounded - 1.0,
        avg_win_pct=(sum(wins) / len(wins)) if wins else 0.0,
        avg_loss_pct=(sum(losses) / len(losses)) if losses else 0.0,
        max_single_loss_pct=min(returns),
    )
