"""可重複執行的排程腳本：把本機sqlite（`data/tw_stock.db`）最近N個交易日的市場資料
推到Turso主DB，完全不呼叫任何外部API（FinMind/TWSE/yfinance）——本機Windows工作
排程器已經抓好資料寫進本機sqlite了，這裡只是把同一批資料原封不動複製到Turso，讓
web版不需要自己再對FinMind打一次額外的請求。

⚠️ 這支腳本設計上**一天只能觸發一次**（見README「本機每日排程」章節的排程範例）：
FinMind是每小時限額（不是Turso那種寬裕的月額度），重複抓取才是真正該避免的風險；
但這支腳本本身雖然不呼叫FinMind，Turso的月寫入額度算式（見ai/PLAN.md對應紀錄）是
以「一天一次」為前提算出安全餘裕的，不要複製貼上到多個時段各自觸發一次。

用法：
    # 先用--dry-run確認預計寫入的列數，不會連線/寫入Turso
    python scripts/sync_local_to_turso.py --local-db data/tw_stock.db --dry-run

    # 正式同步（需先在.env或環境變數設定TURSO_DATABASE_URL/TURSO_AUTH_TOKEN）
    python scripts/sync_local_to_turso.py --local-db data/tw_stock.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import storage  # noqa: E402

# 跟scripts/daily_pipeline.py的INDICATOR_REFRESH_WINDOW_DAYS保持一致(那邊改了這裡
# 也要改，理由見該常數的說明：TWSE資料/均線指標可能事後被回補修正，10天是既有pipeline
# 已經在用、驗證過的安全窗口)。不直接import那支腳本，避免連帶載入yfinance/gspread/
# notify等一整組跟同步腳本無關的重量級依賴。
SYNC_WINDOW_DAYS = 10

LOG_PATH = ROOT / "data" / "sync_to_turso_log.jsonl"

PRICE_COLUMNS = ["stock_id", "date", "open", "high", "low", "close", "volume", "trading_money", "trading_turnover", "spread"]
INSTITUTIONAL_COLUMNS = ["stock_id", "date", "investor_type", "buy", "sell"]
MARGIN_COLUMNS = [
    "stock_id", "date", "margin_purchase_buy", "margin_purchase_sell", "margin_purchase_cash_repayment",
    "margin_purchase_yesterday_balance", "margin_purchase_today_balance", "margin_purchase_limit",
    "short_sale_buy", "short_sale_sell", "short_sale_cash_repayment",
    "short_sale_yesterday_balance", "short_sale_today_balance", "short_sale_limit", "offset_loan_and_short",
]
INDICATOR_COLUMNS = [
    "stock_id", "date", "ma5", "ma10", "ma20", "ma60", "ma120", "ma200", "ma240",
    "sar_value", "sar_is_bull", "sar_flip_days_ago", "updated_at",
]
CANDIDATE_COLUMNS = ["date", "stock_id", "signal_name", "entry_price", "stop_loss", "note", "created_at"]


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


def _select_full_table(local_conn: sqlite3.Connection, table: str, columns: list[str]) -> list[dict]:
    cur = local_conn.execute(f"SELECT {', '.join(columns)} FROM {table}")
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def collect_sync_data(local_conn: sqlite3.Connection, days: int) -> dict:
    """從本機sqlite撈出這次要同步的所有資料，回傳{table名: rows}——純讀取，不連Turso，
    dry-run模式跟正式同步都呼叫這個函式，確保兩者「預計同步的資料」完全一致。"""
    dates = _recent_trading_dates(local_conn, days)
    if not dates:
        return {"dates": [], "data": {}}
    start, end = dates[0], dates[-1]

    data = {
        "stocks": _select_full_table(local_conn, "stocks", ["stock_id", "name", "market", "industry", "listing_type", "updated_at"]),
        "delisted_stocks": _select_full_table(local_conn, "delisted_stocks", ["stock_id", "name", "delisted_date", "reason", "noted_at"]),
        "stock_prices": _select_rows(local_conn, "stock_prices", PRICE_COLUMNS, start, end),
        "institutional_investors": _select_rows(local_conn, "institutional_investors", INSTITUTIONAL_COLUMNS, start, end),
        "margin_trading": _select_rows(local_conn, "margin_trading", MARGIN_COLUMNS, start, end),
        "daily_indicators": _select_rows(local_conn, "daily_indicators", INDICATOR_COLUMNS, start, end),
        "daily_candidates": _select_rows(local_conn, "daily_candidates", CANDIDATE_COLUMNS, start, end),
        "daily_data_status": [
            {"date": row[0], "is_intraday": bool(row[1])}
            for row in local_conn.execute(
                "SELECT date, is_intraday FROM daily_data_status WHERE date BETWEEN ? AND ?", (start, end)
            ).fetchall()
        ],
    }
    return {"dates": dates, "date_range": f"{start}~{end}", "data": data}


def sync(local_conn: sqlite3.Connection, turso_conn, days: int = SYNC_WINDOW_DAYS) -> dict:
    """把本機資料同步到turso_conn，每張表各自try/except，一張表寫入失敗不影響其他表。
    回傳{table名: {"count": 列數, "status": "done"/"failed", "error": 錯誤訊息或None}}。"""
    collected = collect_sync_data(local_conn, days)
    if not collected["dates"]:
        return {"date_range": None, "tables": {}}

    storage.ensure_schema(turso_conn)
    data = collected["data"]
    results: dict = {}

    def _write(table: str, upsert_fn) -> None:
        rows = data[table]
        try:
            upsert_fn(rows)
            results[table] = {"count": len(rows), "status": "done", "error": None}
        except Exception as exc:  # noqa: BLE001
            results[table] = {"count": len(rows), "status": "failed", "error": str(exc)}

    _write("stocks", lambda rows: storage.upsert_stocks(turso_conn, rows))
    _write("delisted_stocks", lambda rows: storage.upsert_delisted_stocks(turso_conn, rows))
    _write("stock_prices", lambda rows: storage.upsert_stock_prices(turso_conn, rows))
    _write("institutional_investors", lambda rows: storage.upsert_institutional_investors(turso_conn, rows))
    _write("margin_trading", lambda rows: storage.upsert_margin_trading(turso_conn, rows))
    _write("daily_indicators", lambda rows: storage.upsert_daily_indicators(turso_conn, rows))
    _write("daily_candidates", lambda rows: storage.upsert_daily_candidates(turso_conn, rows))

    try:
        for row in data["daily_data_status"]:
            storage.upsert_daily_data_status(turso_conn, row["date"], row["is_intraday"])
        results["daily_data_status"] = {"count": len(data["daily_data_status"]), "status": "done", "error": None}
    except Exception as exc:  # noqa: BLE001
        results["daily_data_status"] = {"count": len(data["daily_data_status"]), "status": "failed", "error": str(exc)}

    return {"date_range": collected["date_range"], "tables": results}


def _append_log(entry: dict) -> None:
    """append-only紀錄，照抄src/presentation/pipeline_status.py的append_run_snapshot()
    慣例——排程是無人值守執行，之後要確認「昨晚同步到底有沒有跑、寫了多少」不用去翻
    Windows工作排程器的記錄畫面。寫入失敗不影響同步本身是否成功，純粹是輔助紀錄。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"⚠️ 寫入{LOG_PATH}失敗（不影響同步本身）：{exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--days", type=int, default=SYNC_WINDOW_DAYS, help=f"同步最近幾個交易日（預設{SYNC_WINDOW_DAYS}天）")
    parser.add_argument("--dry-run", action="store_true", help="只印出預計同步的列數，不連線/不寫入Turso")
    args = parser.parse_args()

    local_conn = sqlite3.connect(args.local_db)
    timestamp = datetime.now(timezone.utc).isoformat()

    if args.dry_run:
        collected = collect_sync_data(local_conn, args.days)
        counts = {table: len(rows) for table, rows in collected["data"].items()}
        print(f"[dry-run] 交易日範圍：{collected.get('date_range')}")
        print(f"[dry-run] 預計同步列數：{counts}　合計：{sum(counts.values())}")
        _append_log({"timestamp": timestamp, "dry_run": True, "date_range": collected.get("date_range"), "counts": counts})
        local_conn.close()
        return

    from src.data import turso_client  # noqa: E402  （延遲import：--dry-run不需要連線相關依賴）

    turso_conn = turso_client.get_connection()
    print(f"開始從 {args.local_db} 同步最近 {args.days} 個交易日的資料到 Turso...")
    result = sync(local_conn, turso_conn, args.days)
    print(f"完成：{result}")

    _append_log({
        "timestamp": timestamp, "dry_run": False, "date_range": result["date_range"],
        "counts": {table: info["count"] for table, info in result["tables"].items()},
        "status": {table: info["status"] for table, info in result["tables"].items()},
        "errors": {table: info["error"] for table, info in result["tables"].items() if info["error"]},
    })

    failed = [table for table, info in result["tables"].items() if info["status"] == "failed"]
    if failed:
        print(f"⚠️ 以下表同步失敗，請檢查上面的錯誤訊息：{failed}")

    local_conn.close()
    turso_conn.close()


if __name__ == "__main__":
    main()
