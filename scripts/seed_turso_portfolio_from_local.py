"""一次性腳本：把本機庫存清單/觀察清單資料庫(data/portfolio.db)的內容複製到Turso雲端的
portfolio專用資料庫，供web版部署到雲端後接續使用——照抄scripts/seed_turso_from_local.py
(主DB版本)的結構，差別是這裡操作的是portfolio.db那組表(inventory_stocks/watchlist_
groups/watchlist_stocks)，寫入目標是src/data/turso_client.get_portfolio_connection()
(跟主DB分開的第二個Turso資料庫，見src/data/connection.py的get_default_portfolio_
connection()說明)。

⚠️ 這是一次性初始化操作，會把資料寫進Turso雲端資料庫，執行前務必確認
TURSO_PORTFOLIO_DATABASE_URL是指向你要用的正式資料庫，避免不小心對線上資料庫重複灌入
或灌到錯誤環境。群組用group_name比對是否已存在(唯一約束)，庫存批次/觀察清單股票沒有
天然的去重鍵，重複執行這支腳本會造成資料重複——只跑一次，或執行前先確認目標資料庫是空的。

用法：
    python scripts/seed_turso_portfolio_from_local.py --local-db data/portfolio.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import portfolio_storage, turso_client  # noqa: E402


def seed(local_conn: sqlite3.Connection, turso_conn) -> dict:
    """把local_conn(本機portfolio.db)的庫存批次/觀察清單群組+股票複製到turso_conn。
    回傳統計筆數。"""
    portfolio_storage.ensure_portfolio_schema(turso_conn)

    inventory_rows = portfolio_storage.list_inventory_rows(local_conn)
    for row in inventory_rows:
        portfolio_storage.add_inventory_stock(
            turso_conn, row["stock_id"], row["buy_date"], row["cost_price"],
            row["shares"], row["fee"], row["note"] or "",
        )

    local_groups = portfolio_storage.list_watchlist_groups(local_conn)
    watchlist_stock_count = 0
    for group in local_groups:
        # 目標資料庫已經有同名群組(例如腳本不小心跑第二次)時，add_watchlist_group()對
        # 本機sqlite會丟sqlite3.IntegrityError，但對Turso連線實際會是什麼例外型別沒有
        # 把握(libsql_client的錯誤路徑跟sqlite3模組不是同一套)，這裡改成先查一次目標
        # 資料庫現有群組名稱，用名稱比對決定要新增還是沿用既有id，不依賴特定例外型別。
        existing = {g["group_name"]: g["id"] for g in portfolio_storage.list_watchlist_groups(turso_conn)}
        if group["group_name"] in existing:
            new_group_id = existing[group["group_name"]]
        else:
            new_group_id = portfolio_storage.add_watchlist_group(turso_conn, group["group_name"])
        for stock_row in portfolio_storage.list_watchlist_rows(local_conn, group["id"]):
            portfolio_storage.add_watchlist_stock(
                turso_conn, new_group_id, stock_row["stock_id"],
                stock_row["cost_price"], stock_row["shares"], stock_row["note"] or "",
            )
            watchlist_stock_count += 1

    return {
        "inventory_lots": len(inventory_rows),
        "watchlist_groups": len(local_groups),
        "watchlist_stocks": watchlist_stock_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-db", default=str(ROOT / "data" / "portfolio.db"))
    args = parser.parse_args()

    local_conn = sqlite3.connect(args.local_db)
    turso_conn = turso_client.get_portfolio_connection()

    print(f"開始從 {args.local_db} 灌入庫存清單/觀察清單資料到Turso(portfolio專用資料庫)...")
    stats = seed(local_conn, turso_conn)
    print(f"完成：{stats}")

    local_conn.close()
    turso_conn.close()


if __name__ == "__main__":
    main()
