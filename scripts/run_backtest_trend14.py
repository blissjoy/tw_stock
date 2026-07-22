"""用真實資料回測 R-TREND-14（多頭短線選股與停損停利SOP，信心92/100），
與 run_backtest_real_data.py 的裸MA交叉demo策略對照，回答「高信心度規則的真實勝率」。

本腳本不重新定義任何規則邏輯，只把已實作的規則函式（R-TREND-01轉折點、R-TREND-03多頭
趨勢判定、R-TREND-08頭頭低先知先覺、R-TREND-14進場/停損/出場SOP）依書中描述的時序組裝
成逐日狀態機。轉折點的「多頭趨勢是否成立」在每一天只使用當天為止已經被確認的頭/底，
不使用未來才會確認的轉折點，避免look-ahead。

用法：
    python scripts/run_backtest_trend14.py --market TWSE --min-days 400
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backtest.engine import Trade, summarize_trades  # noqa: E402
from src.indicators.moving_average import sma  # noqa: E402
from src.indicators.trend import (  # noqa: E402
    bull_short_term_entry_ready,
    bull_short_term_exit_action,
    bull_short_term_stop_loss,
    daily_bull_trend_state,
)
from src.risk.money_management import annual_profit_rate  # noqa: E402

# 逐日多頭趨勢狀態機邏輯移到 src/indicators/trend.py 的 daily_bull_trend_state()，
# 讓 src/screener/daily_screener.py 的每日選股也能重用同一份實作，此處保留別名相容既有呼叫寫法。
compute_bull_trend_series = daily_bull_trend_state


def run_trend14_backtest(df: pd.DataFrame) -> list[Trade]:
    close, high, low, open_, volume = df["close"], df["high"], df["low"], df["open"], df["volume"]
    ma5 = sma(close, 5)
    ma10 = sma(close, 10)
    ma20 = sma(close, 20)
    ma10_slope = ma10.diff()
    ma20_slope = ma20.diff()
    volume_prev = volume.shift(1)

    bull_trend = compute_bull_trend_series(high, low, close, n=5)

    trades: list[Trade] = []
    position: Trade | None = None
    entry_price = None
    stop_loss = None

    # 逐日重跑一次多頭趨勢的頭/底追蹤，用來偵測持倉期間是否出現「頭頭低」(R-TREND-14第3條出場條件)
    ma5_state = sma(close, 5)
    heads: list[float] = []
    bottoms: list[float] = []
    state: str | None = None
    group_idx: list[int] = []
    valid_start = ma5_state.first_valid_index()
    start_pos = close.index.get_indexer([valid_start])[0] if valid_start is not None else len(close)

    entry_head_count = 0

    for t in range(len(close)):
        # 同步推進轉折點狀態機（與compute_bull_trend_series同一套邏輯，這裡額外保留頭部序列供出場判斷用）
        new_head_is_lower = False
        if t >= start_pos:
            if close.iloc[t] > ma5_state.iloc[t]:
                cur = "positive"
            elif close.iloc[t] < ma5_state.iloc[t]:
                cur = "negative"
            else:
                cur = state
            if state is None:
                state = cur
                group_idx = [t]
            elif cur == state:
                group_idx.append(t)
            else:
                group_idx.append(t)
                if state == "positive" and cur == "negative":
                    head_pos = max(group_idx, key=lambda j: high.iloc[j])
                    new_head = float(high.iloc[head_pos])
                    if heads and new_head < heads[-1]:
                        new_head_is_lower = True
                    heads.append(new_head)
                elif state == "negative" and cur == "positive":
                    bottom_pos = min(group_idx, key=lambda j: low.iloc[j])
                    bottoms.append(float(low.iloc[bottom_pos]))
                state = cur
                group_idx = [t]

        if position is None:
            if pd.isna(ma20_slope.iloc[t]) or pd.isna(volume_prev.iloc[t]) or pd.isna(ma10.iloc[t]):
                continue
            ready = bull_short_term_entry_ready(
                is_bull_trend=bool(bull_trend.iloc[t]),
                ma10=ma10.iloc[t], ma20=ma20.iloc[t],
                ma10_slope=ma10_slope.iloc[t], ma20_slope=ma20_slope.iloc[t],
                close_t=close.iloc[t], open_t=open_.iloc[t],
                volume_t=volume.iloc[t], volume_prev=volume_prev.iloc[t],
            )
            if ready:
                entry_price = close.iloc[t]
                stop_loss = bull_short_term_stop_loss(entry_bar_low=low.iloc[t])
                position = Trade(direction="long", entry_index=t, entry_date=close.index[t], entry_price=entry_price, stop_price=stop_loss)
                entry_head_count = len(heads)
            continue

        profit_pct = (close.iloc[t] - position.entry_price) / position.entry_price
        has_lower_high = new_head_is_lower and len(heads) > entry_head_count
        action = bull_short_term_exit_action(
            close_t=close.iloc[t], stop_loss=position.stop_price, has_lower_high=has_lower_high,
            profit_pct=profit_pct, ma5_t=ma5.iloc[t],
        )
        if action != "續抱":
            position.exit_index = t
            position.exit_date = close.index[t]
            position.exit_price = close.iloc[t] if not action.startswith("跌破停損") else position.stop_price
            position.exit_reason = action
            trades.append(position)
            position = None

    if position is not None:
        position.exit_index = len(close) - 1
        position.exit_date = close.index[-1]
        position.exit_price = close.iloc[-1]
        position.exit_reason = "回測結束強制平倉"
        trades.append(position)

    return trades


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--market", default="TWSE", choices=["TWSE", "TPEx"])
    parser.add_argument("--min-days", type=int, default=400)
    parser.add_argument("--stock-id", default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    if args.stock_id:
        stock_ids = [args.stock_id]
    else:
        cur = conn.execute(
            """
            SELECT sp.stock_id FROM stock_prices sp JOIN stocks s ON sp.stock_id = s.stock_id
            WHERE s.market = ? GROUP BY sp.stock_id HAVING COUNT(*) >= ? ORDER BY sp.stock_id
            """,
            (args.market, args.min_days),
        )
        stock_ids = [r[0] for r in cur.fetchall()]

    print(f"=== 股票池：{args.market}，{len(stock_ids)} 檔（>= {args.min_days} 天資料） ===")
    print("策略：R-TREND-14 多頭短線選股與停損停利SOP（信心92/100）\n")

    all_trades: list[Trade] = []
    per_stock = []

    for sid in stock_ids:
        df = pd.read_sql_query(
            "SELECT date, open, high, low, close, volume FROM stock_prices WHERE stock_id = ? ORDER BY date",
            conn, params=(sid,),
        )
        if len(df) < 60:
            continue
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        trades = run_trend14_backtest(df)
        if not trades:
            continue
        summary = summarize_trades(trades)
        all_trades.extend(trades)
        if summary.total_trades > 0:
            per_stock.append((sid, summary))

    conn.close()

    overall = summarize_trades(all_trades)
    print(f"=== 整體彙總（{len(per_stock)} 檔有交易紀錄，共 {overall.total_trades} 筆交易）===")
    print(f"勝場：{overall.win_count}　敗場：{overall.loss_count}")
    print(f"整體勝率：{overall.win_rate:.1%}")
    print(f"平均單筆報酬率：{overall.avg_return_pct:.2%}")
    print(f"平均獲利（勝場）：{overall.avg_win_pct:.2%}　平均虧損（敗場）：{overall.avg_loss_pct:.2%}")
    print(f"單一部位最大虧損：{overall.max_single_loss_pct:.2%}")

    from collections import Counter
    reason_counter = Counter()
    reason_win = Counter()
    for t in all_trades:
        if t.return_pct is None:
            continue
        reason_counter[t.exit_reason] += 1
        if t.return_pct > 0:
            reason_win[t.exit_reason] += 1
    print("\n=== 出場原因分布 ===")
    for reason, count in reason_counter.items():
        w = reason_win[reason]
        print(f"  {reason}: {count}筆, 勝率{w/count:.1%}")

    if overall.total_trades > 0 and per_stock:
        avg_trades_per_stock = overall.total_trades / len(per_stock)
        years_covered = args.min_days / 245
        avg_trades_per_year = avg_trades_per_stock / years_covered
        implied_annual_rate = annual_profit_rate(
            total_trades=avg_trades_per_year, win_rate=overall.win_rate,
            profit_pct=overall.avg_win_pct, loss_pct=abs(overall.avg_loss_pct),
        )
        print(f"\n=== 交叉驗證 R-RISK-03 ===")
        print(f"單股平均交易次數：{avg_trades_per_stock:.1f}筆／約{years_covered:.1f}年 → 換算年交易次數：{avg_trades_per_year:.1f}筆")
        print(f"公式估算單一帳戶年化報酬率：{implied_annual_rate:.1%}")

    per_stock.sort(key=lambda x: x[1].total_compounded_return_pct, reverse=True)
    print(f"\n=== 表現最佳 10 檔 ===")
    for sid, s in per_stock[:10]:
        print(f"  {sid}: {s.total_trades}筆, 勝率{s.win_rate:.0%}, 複利報酬{s.total_compounded_return_pct:.1%}")
    print(f"\n=== 表現最差 10 檔 ===")
    for sid, s in per_stock[-10:]:
        print(f"  {sid}: {s.total_trades}筆, 勝率{s.win_rate:.0%}, 複利報酬{s.total_compounded_return_pct:.1%}")


if __name__ == "__main__":
    main()
