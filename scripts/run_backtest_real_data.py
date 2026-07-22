"""用真實歷史資料（data/tw_stock.db）跑回測引擎，驗證規則庫組合起來的訊號機制。

目前套用 src/backtest/strategies.py 的示範策略 golden_cross_trend_strategy
（MA5/MA20黃金死亡交叉 R-MA-13/14 + 均線多頭排列 R-MA-08），這是回測引擎自帶的
第一個示範策略，尚未套用完整246條規則庫。逐檔跑、彙總所有交易，並將整體勝率/
平均賺賠代入 R-RISK-03 全年獲利率方程式做交叉驗證。

用法：
    python scripts/run_backtest_real_data.py --market TWSE --min-days 400
    python scripts/run_backtest_real_data.py --market TWSE --stock-id 2330
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backtest.engine import run_backtest, summarize_trades  # noqa: E402
from src.backtest.strategies import golden_cross_trend_strategy  # noqa: E402
from src.risk.money_management import annual_profit_rate  # noqa: E402


def load_stock_universe(conn: sqlite3.Connection, market: str, min_days: int) -> list[str]:
    cur = conn.execute(
        """
        SELECT sp.stock_id
        FROM stock_prices sp JOIN stocks s ON sp.stock_id = s.stock_id
        WHERE s.market = ?
        GROUP BY sp.stock_id
        HAVING COUNT(*) >= ?
        ORDER BY sp.stock_id
        """,
        (market, min_days),
    )
    return [row[0] for row in cur.fetchall()]


def load_ohlc(conn: sqlite3.Connection, stock_id: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close FROM stock_prices WHERE stock_id = ? ORDER BY date",
        conn,
        params=(stock_id,),
    )
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--market", default="TWSE", choices=["TWSE", "TPEx"])
    parser.add_argument("--min-days", type=int, default=400, help="只納入至少有N天股價資料的股票")
    parser.add_argument("--stock-id", default=None, help="只跑單一股票代號（除錯用）")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    stock_ids = [args.stock_id] if args.stock_id else load_stock_universe(conn, args.market, args.min_days)
    print(f"=== 股票池：{args.market}，{len(stock_ids)} 檔（>= {args.min_days} 天資料） ===")
    print("策略：golden_cross_trend_strategy（MA5/MA20黃金死亡交叉 + 均線多頭排列，R-MA-08/13/14）\n")

    all_trades = []
    per_stock_results = []
    skipped = 0

    for stock_id in stock_ids:
        df = load_ohlc(conn, stock_id)
        if len(df) < 60:  # MA20需要至少20天才有值，且需要足夠交易樣本
            skipped += 1
            continue

        entry_signal, exit_signal = golden_cross_trend_strategy(df["close"])
        trades = run_backtest(
            close=df["close"], high=df["high"], low=df["low"],
            entry_signal=entry_signal, exit_signal=exit_signal,
            direction="long", stop_loss_pct=0.07,
        )
        if not trades:
            continue

        summary = summarize_trades(trades)
        for t in trades:
            all_trades.append(t)
        if summary.total_trades > 0:
            per_stock_results.append((stock_id, summary))

    conn.close()

    print(f"股票池中 {skipped} 檔資料量不足被跳過\n")
    print(f"=== 整體彙總（{len(per_stock_results)} 檔有交易紀錄，共 {len(all_trades)} 筆交易）===")

    overall = summarize_trades(all_trades)
    print(f"總交易筆數：{overall.total_trades}（跨 {len(per_stock_results)} 檔獨立股票，非單一帳戶依序執行）")
    print(f"勝場：{overall.win_count}　敗場：{overall.loss_count}")
    print(f"整體勝率：{overall.win_rate:.1%}")
    print(f"平均單筆報酬率：{overall.avg_return_pct:.2%}")
    print(f"平均獲利（勝場）：{overall.avg_win_pct:.2%}　平均虧損（敗場）：{overall.avg_loss_pct:.2%}")
    print(f"單一部位最大虧損：{overall.max_single_loss_pct:.2%}")
    print(
        "註：不呈現「全部12000+筆交易依序複利」的單一數字——那是把1022檔互不相關股票的交易\n"
        "    硬接成一條時間序列，不對應任何真實可執行的資金曲線，會產生誤導性的極端值。"
    )

    # 交叉驗證 R-RISK-03 全年獲利率勝率方程式：N 用「單一股票平均年交易次數」而非跨股票總筆數，
    # 否則等於假設一個帳戶在同一年內平行執行1000多檔股票的所有交易，不符合公式原本的單帳戶假設。
    if overall.total_trades > 0 and per_stock_results:
        avg_trades_per_stock = overall.total_trades / len(per_stock_results)
        years_covered = args.min_days / 245  # 約245個交易日/年
        avg_trades_per_year = avg_trades_per_stock / years_covered
        implied_annual_rate = annual_profit_rate(
            total_trades=avg_trades_per_year,
            win_rate=overall.win_rate,
            profit_pct=overall.avg_win_pct,
            loss_pct=abs(overall.avg_loss_pct),
        )
        print(f"\n=== 交叉驗證 R-RISK-03（全年獲利率勝率方程式）===")
        print(f"單股平均交易次數：{avg_trades_per_stock:.1f}筆／約{years_covered:.1f}年 → 換算年交易次數：{avg_trades_per_year:.1f}筆")
        print(f"代入實測勝率{overall.win_rate:.1%}、平均賺{overall.avg_win_pct:.2%}、平均賠{abs(overall.avg_loss_pct):.2%}：")
        print(f"公式估算單一帳戶年化報酬率：{implied_annual_rate:.1%}")

    per_stock_results.sort(key=lambda x: x[1].total_compounded_return_pct, reverse=True)
    print(f"\n=== 表現最佳 10 檔（依單股複利報酬率排序）===")
    for stock_id, s in per_stock_results[:10]:
        print(f"  {stock_id}: {s.total_trades}筆交易, 勝率{s.win_rate:.0%}, 複利報酬{s.total_compounded_return_pct:.1%}")

    print(f"\n=== 表現最差 10 檔 ===")
    for stock_id, s in per_stock_results[-10:]:
        print(f"  {stock_id}: {s.total_trades}筆交易, 勝率{s.win_rate:.0%}, 複利報酬{s.total_compounded_return_pct:.1%}")


if __name__ == "__main__":
    main()
