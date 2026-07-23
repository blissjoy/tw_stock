"""補齊TPEx上櫃股票的歷史股價缺口（一次性維護腳本）。

背景（2026-07-23診斷）：本機`data/tw_stock.db`裡有796檔上櫃股票的最早股價日期晚於
2024-07-22（原本2年回補的基準線）。根本原因：原始的`scripts/backfill_history.py`
（`--start 2024-07-22 --end 2026-07-22`，未加`--tpex-limit`，本來就打算涵蓋全部
~1901檔上櫃股票）當時用FinMind逐股抓，跑到中途撞上FinMind每小時額度上限——
`backfill_log.txt`裡有1098檔明確記錄「FinMind API 連續3次請求失敗」，最終只有
681/1584檔成功，其餘股票根本沒補到歷史資料，卻被誤以為已經回補完成。這個額度問題
後來才在`src/data/finmind_client.py`加上主動節流(_throttle)修好，但為時已晚，
沒有回頭補救那次失敗的回補。

用`src/data/yfinance_client.py`（沒有FinMind那種額度限制的批次下載）重新補齊。
依使用者指示「從最近日期開始往前下載」，分成3個日期區間梯次(而不是一次拉滿2年)
逐步往回補，每個梯次內部再用yfinance_client既有的BATCH_SIZE(500)分批下載。

用法：
    python scripts/backfill_tpex_history_gap.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import storage, yfinance_client  # noqa: E402

DB_PATH = ROOT / "data" / "tw_stock.db"
TARGET_START_DATE = "2024-07-22"

# 從最近日期開始往前，分3個梯次(而不是一次拉滿2年)逐步回補；區間彼此相接不重疊不留縫。
DATE_CHUNKS = [
    ("2026-01-01", "2026-07-24"),
    ("2025-07-22", "2026-01-01"),
    ("2024-07-22", "2025-07-22"),
]


def main() -> None:
    conn = storage.init_db(DB_PATH)

    target_rows = conn.execute(
        """
        SELECT sp.stock_id, MIN(sp.date) AS earliest
        FROM stock_prices sp JOIN stocks s ON sp.stock_id = s.stock_id
        WHERE s.market = 'TPEx'
        GROUP BY sp.stock_id
        HAVING earliest > ?
        """,
        (TARGET_START_DATE,),
    ).fetchall()
    stock_ids = [row[0] for row in target_rows]
    print(f"目標股票數：{len(stock_ids)}")

    total_start = time.time()
    for start_date, end_date in DATE_CHUNKS:
        chunk_start = time.time()
        print(f"\n=== 回補區間 {start_date} ~ {end_date} ===")
        prices_by_stock = yfinance_client.fetch_prices_batch(stock_ids, start_date, end_date, market_suffix=".TWO")
        row_count = 0
        for stock_id, rows in prices_by_stock.items():
            storage.upsert_stock_prices(conn, rows)
            row_count += len(rows)
        elapsed = time.time() - chunk_start
        print(f"完成：{len(prices_by_stock)}/{len(stock_ids)} 檔有資料，共{row_count}筆，耗時{elapsed:.1f}秒")

    conn.close()
    print(f"\n全部完成，總耗時 {time.time() - total_start:.1f} 秒")


if __name__ == "__main__":
    main()
