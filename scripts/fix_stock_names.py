"""一次性修正腳本：把 stocks 表裡已經寫成 stock_id 佔位符的 name/industry 欄位，
改成 FinMind TaiwanStockInfo 提供的真實股票名稱/產業別。

背景：scripts/daily_pipeline.py 與 scripts/backfill_history.py 先前在寫入 stocks 表時，
TWSE 路徑沒有取用回應裡的股票名稱欄位、TPEx 路徑雖然已經呼叫過 FinMind 拿到真實名稱卻沒
使用，兩條路徑都用 stock_id 頂替了 name。這兩支腳本本身的寫入邏輯已經修正，這支腳本
負責修正『資料庫裡已經寫進去的舊資料』，跑一次即可，之後新寫入的資料不會再有這個問題。

用法：
    python scripts/fix_stock_names.py --local-db data/tw_stock.db   # 修正本機資料庫
    python scripts/fix_stock_names.py                                # 修正Turso(需先在.env設定憑證)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import finmind_client, storage  # noqa: E402


def fix_stock_names(conn) -> int:
    """用FinMind TaiwanStockInfo的真實名稱/產業別覆蓋stocks表既有列，回傳更新筆數。
    只更新FinMind名單裡查得到的股票，查不到的既有列維持原樣（可能是name本來就正確，
    或是FinMind名單本身沒有涵蓋，兩種情況都不該用stock_id去覆蓋一個可能已經正確的值）。
    """
    stock_info = finmind_client.fetch_stock_info()
    if not stock_info:
        return 0

    existing_ids = {r[0] for r in conn.execute("SELECT stock_id FROM stocks").fetchall()}
    rows = [
        {
            "stock_id": r["stock_id"], "name": r["name"], "market": r["market"],
            "industry": r.get("industry"), "updated_at": datetime.now().isoformat(),
        }
        for r in stock_info
        if r["stock_id"] in existing_ids
    ]
    if rows:
        storage.upsert_stocks(conn, rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-db", default=None, help="指向本機sqlite檔案而非連線Turso")
    args = parser.parse_args()

    if args.local_db:
        conn = storage.init_db(args.local_db)
    else:
        from src.data import turso_client
        conn = turso_client.get_connection()

    updated = fix_stock_names(conn)
    print(f"已修正 {updated} 檔股票的 name/industry 欄位。")
    conn.close()


if __name__ == "__main__":
    main()
