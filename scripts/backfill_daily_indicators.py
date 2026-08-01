"""一次性回補均線/SAR快取(daily_indicators)：把現有stock_prices歷史全部算過一輪均線/
SAR，寫進daily_indicators表——候選清單的「篩選方法」(均線多頭排列/SAR翻轉)改成查這張
表(見src/presentation/chart_data.py的load_ma_bullish_flags_from_table()/
load_sar_flip_flags_from_table())，沒有回補過的股票在這張表裡沒有任何紀錄，均線/SAR
篩選會直接判定「不成立」。

⚠️ 這是必要的手動步驟：部署這個改動後，必須先手動跑一次這支腳本，不然候選清單的均線/
SAR篩選會突然全部變成0筆結果。之後每天排程(scripts/daily_pipeline.py)會自動維護最新
資料，不需要重複執行這支腳本，只有在均線/SAR的計算邏輯本身變動、或DB整個重建時才需要
再跑一次。

每檔股票的全部歷史只算一次(不是逐日期各自重算一次回看窗口)，效能上遠比逐日期重算快，
但股票數量多(~2300檔)，實際耗時建議先用--stock-id測試少量股票，或用--limit限制處理
數量，確認速度可接受後再跑全量。

用法：
    python scripts/backfill_daily_indicators.py --db data/tw_stock.db
    python scripts/backfill_daily_indicators.py --db data/tw_stock.db --stock-id 2330
    python scripts/backfill_daily_indicators.py --db data/tw_stock.db --limit 50
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import storage  # noqa: E402
from src.screener.daily_screener import load_trailing_frames  # noqa: E402
from src.screener.indicator_precompute import compute_indicator_rows  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--stock-id", default=None, help="只回補單一股票代號，方便測試")
    parser.add_argument("--limit", type=int, default=None, help="只處理前N檔股票，方便測試")
    parser.add_argument("--min-days", type=int, default=1, help="跟load_trailing_frames()的min_days一致，預設1(不排除任何有資料的股票)")
    args = parser.parse_args()

    conn = storage.init_db(Path(args.db))
    frames = load_trailing_frames(conn, min_days=args.min_days)

    if args.stock_id:
        frames = {args.stock_id: frames[args.stock_id]} if args.stock_id in frames else {}
    elif args.limit:
        frames = dict(list(frames.items())[: args.limit])

    total = len(frames)
    print(f"共{total}檔股票要回補，開始計算均線/SAR...")

    start_time = time.time()
    total_rows = 0
    for i, (stock_id, df) in enumerate(frames.items(), start=1):
        target_dates = set(df.index.strftime("%Y-%m-%d"))
        rows = compute_indicator_rows(stock_id, df, target_dates)
        if rows:
            storage.upsert_daily_indicators(conn, rows)
            total_rows += len(rows)
        if i % 100 == 0 or i == total:
            elapsed = time.time() - start_time
            print(f"  {i}/{total} 檔完成，累計{total_rows}筆，耗時{elapsed:.1f}秒")

    conn.close()
    elapsed = time.time() - start_time
    print(f"回補完成：{total}檔股票，共{total_rows}筆，總耗時{elapsed:.1f}秒。")


if __name__ == "__main__":
    main()
