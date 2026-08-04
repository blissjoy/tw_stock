"""回補大盤(^TWII)歷史成交量：改用TWSE官方FMTQIK(大盤統計資訊)的「成交股數」取代
yfinance的volume欄位。

⚠️ **2026-08-04發現的真實bug**：`scripts/daily_pipeline.py`的`fetch_today_taiex()`
2026-08-04當天改用TWSE官方FMTQIK覆蓋volume(見該函式docstring)，但只處理了
`TAIEX_REFETCH_WINDOW_DAYS`天的滾動窗口(當時湊巧因為FMTQIK整月回傳、window又跨到
7月，連帶把整個7月也修好了)，**2026-07-01之前**的歷史資料完全沒被這次修正碰到，
還是舊的yfinance來源——實測發現yfinance的volume單位/量級跟schema其餘欄位(原始股數，
比照個股的慣例)不一致，同一天(2026-06-30)比較，DB裡舊值是7,182,400，TWSE官方數字
是13,140,347,235，整整差了約1829倍。這支腳本一次性回補全部歷史資料的volume，統一
成TWSE官方的原始股數單位。

OHLC(開高低收)完全不動，只覆蓋volume欄位——TWSE沒有提供大盤指數的開高低，這部分
繼續信任既有的yfinance資料(這部分本來就沒有量級不一致的問題)。

用法：
    python scripts/backfill_taiex_volume.py --db data/tw_stock.db
    python scripts/backfill_taiex_volume.py --db data/tw_stock.db --start 2023-01-01 --end 2026-06-30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import storage, twse_client, yfinance_client  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--start", default=None, help="YYYY-MM-DD，預設為DB裡大盤最早的一筆資料日期")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD，預設為DB裡大盤最新的一筆資料日期")
    args = parser.parse_args()

    conn = storage.init_db(Path(args.db))
    stock_id = yfinance_client.TAIEX_STOCK_ID

    date_range = conn.execute(
        "SELECT MIN(date), MAX(date) FROM stock_prices WHERE stock_id = ?", (stock_id,),
    ).fetchone()
    if date_range[0] is None:
        print("DB裡查無大盤資料，未執行任何動作。")
        return
    start_date = args.start or date_range[0]
    end_date = args.end or date_range[1]

    existing_rows = conn.execute(
        "SELECT date, open, high, low, close FROM stock_prices WHERE stock_id = ? AND date >= ? AND date <= ?",
        (stock_id, start_date, end_date),
    ).fetchall()
    ohlc_by_date = {row[0]: row[1:] for row in existing_rows}
    print(f"DB裡{start_date}~{end_date}共{len(ohlc_by_date)}筆既有OHLC。")

    volume_by_date = twse_client.fetch_taiex_volume_range(start_date, end_date)
    print(f"TWSE官方FMTQIK回傳{len(volume_by_date)}筆成交股數。")

    updated_rows = []
    missing_official_volume = []
    for trade_date, (open_, high, low, close) in ohlc_by_date.items():
        volume = volume_by_date.get(trade_date)
        if volume is None:
            missing_official_volume.append(trade_date)
            continue
        updated_rows.append({
            "stock_id": stock_id, "date": trade_date, "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "trading_money": None, "trading_turnover": None, "spread": None,
        })

    storage.upsert_stock_prices(conn, updated_rows)
    conn.close()

    print(f"已更新{len(updated_rows)}筆volume。")
    if missing_official_volume:
        print(f"TWSE官方查無資料、維持原值：{len(missing_official_volume)}筆（{missing_official_volume[:5]}...）")


if __name__ == "__main__":
    main()
