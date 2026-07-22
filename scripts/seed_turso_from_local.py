"""一次性腳本：把本機的完整歷史資料庫（供離線回測研究用，531MB+）近期滾動窗口複製進 Turso，
讓每日 pipeline（scripts/daily_pipeline.py）上線第一天就有足夠歷史資料可以算 MA240 等指標，
不必等一年慢慢累積。之後 Turso 上的資料改由 daily_pipeline.py 每天增量更新，這支腳本只需要跑一次。

⚠️ 這是一次性初始化操作，會把資料寫進 Turso 雲端資料庫，執行前務必確認 TURSO_DATABASE_URL
是指向你要用的正式資料庫，避免不小心對線上資料庫重複灌入或灌到錯誤環境。

用法：
    python scripts/seed_turso_from_local.py --local-db data/tw_stock.db --days 400
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import storage, turso_client  # noqa: E402

PRICE_COLUMNS = ["stock_id", "date", "open", "high", "low", "close", "volume", "trading_money", "trading_turnover", "spread"]
INSTITUTIONAL_COLUMNS = ["stock_id", "date", "investor_type", "buy", "sell"]
MARGIN_COLUMNS = [
    "stock_id", "date", "margin_purchase_buy", "margin_purchase_sell", "margin_purchase_cash_repayment",
    "margin_purchase_yesterday_balance", "margin_purchase_today_balance", "margin_purchase_limit",
    "short_sale_buy", "short_sale_sell", "short_sale_cash_repayment",
    "short_sale_yesterday_balance", "short_sale_today_balance", "short_sale_limit", "offset_loan_and_short",
]


def _recent_trading_dates(local_conn: sqlite3.Connection, days: int) -> list[str]:
    rows = local_conn.execute(
        "SELECT DISTINCT date FROM stock_prices ORDER BY date DESC LIMIT ?", (days,)
    ).fetchall()
    return sorted(r[0] for r in rows)


def _select_rows(local_conn: sqlite3.Connection, table: str, columns: list[str], start: str, end: str) -> list[dict]:
    cur = local_conn.execute(
        f"SELECT {', '.join(columns)} FROM {table} WHERE date BETWEEN ? AND ?", (start, end)
    )
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def seed(local_conn: sqlite3.Connection, turso_conn, days: int) -> dict:
    """把local_conn裡最近days個交易日的股票基本資料+股價+法人+資券複製到turso_conn。回傳統計筆數。"""
    storage.ensure_schema(turso_conn)

    dates = _recent_trading_dates(local_conn, days)
    if not dates:
        return {"dates": 0, "stocks": 0, "prices": 0, "institutional": 0, "margin": 0}
    start, end = dates[0], dates[-1]

    stock_rows = [
        dict(zip(["stock_id", "name", "market", "industry", "updated_at"], row))
        for row in local_conn.execute("SELECT stock_id, name, market, industry, updated_at FROM stocks").fetchall()
    ]
    storage.upsert_stocks(turso_conn, stock_rows)

    price_rows = _select_rows(local_conn, "stock_prices", PRICE_COLUMNS, start, end)
    storage.upsert_stock_prices(turso_conn, price_rows)

    institutional_rows = _select_rows(local_conn, "institutional_investors", INSTITUTIONAL_COLUMNS, start, end)
    storage.upsert_institutional_investors(turso_conn, institutional_rows)

    margin_rows = _select_rows(local_conn, "margin_trading", MARGIN_COLUMNS, start, end)
    storage.upsert_margin_trading(turso_conn, margin_rows)

    return {
        "dates": len(dates), "date_range": f"{start}~{end}", "stocks": len(stock_rows),
        "prices": len(price_rows), "institutional": len(institutional_rows), "margin": len(margin_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--days", type=int, default=400, help="灌入最近幾個交易日（預設400天，足夠算MA240等指標）")
    args = parser.parse_args()

    local_conn = sqlite3.connect(args.local_db)
    turso_conn = turso_client.get_connection()

    print(f"開始從 {args.local_db} 灌入最近 {args.days} 個交易日的資料到 Turso...")
    stats = seed(local_conn, turso_conn, args.days)
    print(f"完成：{stats}")

    local_conn.close()
    turso_conn.close()


if __name__ == "__main__":
    main()
