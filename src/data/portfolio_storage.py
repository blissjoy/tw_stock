"""庫存清單／觀察清單的獨立SQLite儲存層：跟src/data/storage.py(市場資料)分開放，
依src/data/portfolio_schema.sql建表，提供CRUD輔助函式。風格比照storage.py——不做
ORM包裝，「連線→執行SQL→關閉」。

2026-08-02新增：參考ref-project/tw_stock_analyzer的庫存清單/觀察清單功能移植過來，
但簡化了一部分——ref-project有自己一張scan_cache股價/技術指標快取表，庫存/觀察清單
要顯示「現價/均線/SAR」時走這張表；本專案已經有daily_pipeline.py每天自動更新的
stock_prices/daily_indicators，不需要另外做一套快取或per-股票的背景yfinance更新
worker，查詢/合併邏輯見src/presentation/portfolio_data.py。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

PORTFOLIO_SCHEMA_PATH = Path(__file__).resolve().parent / "portfolio_schema.sql"


def ensure_portfolio_schema(conn: sqlite3.Connection) -> None:
    """對已開啟的本機sqlite連線套用portfolio_schema.sql建表，可重複呼叫
    (CREATE TABLE IF NOT EXISTS)。"""
    conn.executescript(PORTFOLIO_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def init_portfolio_db(db_path: str | Path, check_same_thread: bool = True) -> sqlite3.Connection:
    """依portfolio_schema.sql建立(或沿用既有)本機檔案資料庫，回傳已開啟的連線。
    check_same_thread說明見storage.init_db()。"""
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    ensure_portfolio_schema(conn)
    return conn


# ----------------------------------------------------------------------
# 庫存清單
# ----------------------------------------------------------------------

def add_inventory_stock(
    conn: sqlite3.Connection, stock_id: str, buy_date: str | None = None,
    cost_price: float | None = None, shares: int | None = None, note: str = "",
) -> int:
    """新增一筆庫存批次(lot)——2026-08-02改版：使用者反映實際狀況是分批買入，同一檔
    股票可以呼叫這個函式好幾次，各自是獨立的一筆紀錄(不會互相覆蓋)，回傳新批次的id。
    """
    cur = conn.execute(
        """
        INSERT INTO inventory_stocks (stock_id, buy_date, cost_price, shares, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (stock_id, buy_date, cost_price, shares, note, datetime.now().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def update_inventory_stock(
    conn: sqlite3.Connection, lot_id: int, buy_date: str | None = None,
    cost_price: float | None = None, shares: int | None = None, note: str = "",
) -> None:
    """依批次id更新指定那一筆庫存紀錄(不是依stock_id——同一檔股票可能有多筆批次，
    要精準指定改哪一筆)。stock_id本身不可改(是這筆批次的內容，不是可變欄位，UI
    的編輯對話框對這個欄位一律唯讀)。"""
    conn.execute(
        "UPDATE inventory_stocks SET buy_date = ?, cost_price = ?, shares = ?, note = ? WHERE id = ?",
        (buy_date, cost_price, shares, note, lot_id),
    )
    conn.commit()


def delete_inventory_stock(conn: sqlite3.Connection, lot_id: int) -> None:
    """依批次id刪除指定那一筆庫存紀錄，只影響這一批，同一檔股票的其他批次不受影響。"""
    conn.execute("DELETE FROM inventory_stocks WHERE id = ?", (lot_id,))
    conn.commit()


def clear_all_inventory_stocks(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM inventory_stocks")
    conn.commit()


def list_inventory_stock_ids(conn: sqlite3.Connection) -> list[str]:
    """回傳有庫存紀錄的相異股票代號(不管每檔股票有幾筆批次)，供src/presentation/
    portfolio_data.py查價格快照時用——同一檔股票不管幾筆批次，現價只需要查一次。"""
    cur = conn.execute("SELECT DISTINCT stock_id FROM inventory_stocks ORDER BY stock_id")
    return [row[0] for row in cur.fetchall()]


def list_inventory_rows(conn: sqlite3.Connection) -> list[dict]:
    """一次查出所有庫存批次的id/stock_id/buy_date/cost_price/shares/note(每筆
    批次各自一列)，供src/presentation/portfolio_data.py組明細檢視的DataFrame用，
    避免逐檔各自查一次(N+1查詢)。"""
    cur = conn.execute(
        "SELECT id, stock_id, buy_date, cost_price, shares, note FROM inventory_stocks ORDER BY stock_id, buy_date",
    )
    return [
        {"id": r[0], "stock_id": r[1], "buy_date": r[2], "cost_price": r[3], "shares": r[4], "note": r[5]}
        for r in cur.fetchall()
    ]


def get_inventory_lot(conn: sqlite3.Connection, lot_id: int) -> dict | None:
    """回傳單一庫存批次的內容，供編輯對話框帶入既有值使用。"""
    row = conn.execute(
        "SELECT id, stock_id, buy_date, cost_price, shares, note FROM inventory_stocks WHERE id = ?",
        (lot_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "stock_id": row[1], "buy_date": row[2], "cost_price": row[3], "shares": row[4], "note": row[5]}


# ----------------------------------------------------------------------
# 觀察清單群組
# ----------------------------------------------------------------------

def add_watchlist_group(conn: sqlite3.Connection, group_name: str) -> int:
    """新增群組，回傳新群組的id。群組名稱重複時sqlite3.IntegrityError(UNIQUE
    constraint)，呼叫端(UI)自行處理錯誤提示，這裡不吞掉例外。"""
    cur = conn.execute(
        "INSERT INTO watchlist_groups (group_name, created_at) VALUES (?, ?)",
        (group_name, datetime.now().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def rename_watchlist_group(conn: sqlite3.Connection, group_id: int, new_name: str) -> None:
    conn.execute("UPDATE watchlist_groups SET group_name = ? WHERE id = ?", (new_name, group_id))
    conn.commit()


def delete_watchlist_group(conn: sqlite3.Connection, group_id: int) -> None:
    """刪除群組前先手動刪掉watchlist_stocks裡對應的列，再刪group本身——SQLite預設
    不強制開啟`PRAGMA foreign_keys`，即使schema裡宣告了REFERENCES，也不能完全
    依賴資料庫自動連動刪除，不手動刪的話watchlist_stocks會留下group_id指向不存在
    群組的孤兒列。"""
    conn.execute("DELETE FROM watchlist_stocks WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM watchlist_groups WHERE id = ?", (group_id,))
    conn.commit()


def list_watchlist_groups(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("SELECT id, group_name FROM watchlist_groups ORDER BY id")
    return [{"id": row[0], "group_name": row[1]} for row in cur.fetchall()]


# ----------------------------------------------------------------------
# 觀察清單股票
# ----------------------------------------------------------------------

def add_watchlist_stock(
    conn: sqlite3.Connection, group_id: int, stock_id: str,
    cost_price: float | None = None, shares: int | None = None, note: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO watchlist_stocks (group_id, stock_id, cost_price, shares, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(group_id, stock_id) DO UPDATE SET
            cost_price = excluded.cost_price, shares = excluded.shares, note = excluded.note
        """,
        (group_id, stock_id, cost_price, shares, note, datetime.now().isoformat()),
    )
    conn.commit()


def update_watchlist_stock(
    conn: sqlite3.Connection, group_id: int, stock_id: str,
    cost_price: float | None = None, shares: int | None = None, note: str = "",
) -> None:
    add_watchlist_stock(conn, group_id, stock_id, cost_price, shares, note)


def delete_watchlist_stock(conn: sqlite3.Connection, group_id: int, stock_id: str) -> None:
    conn.execute(
        "DELETE FROM watchlist_stocks WHERE group_id = ? AND stock_id = ?", (group_id, stock_id),
    )
    conn.commit()


def clear_all_watchlist_stocks(conn: sqlite3.Connection, group_id: int) -> None:
    conn.execute("DELETE FROM watchlist_stocks WHERE group_id = ?", (group_id,))
    conn.commit()


def list_watchlist_stock_ids(conn: sqlite3.Connection, group_id: int) -> list[str]:
    cur = conn.execute(
        "SELECT stock_id FROM watchlist_stocks WHERE group_id = ? ORDER BY stock_id", (group_id,),
    )
    return [row[0] for row in cur.fetchall()]


def list_watchlist_rows(conn: sqlite3.Connection, group_id: int) -> list[dict]:
    """一次查出指定群組所有股票的stock_id/cost_price/shares/note，供src/presentation/
    portfolio_data.py組DataFrame用，避免逐檔各自查一次(N+1查詢)。"""
    cur = conn.execute(
        "SELECT stock_id, cost_price, shares, note FROM watchlist_stocks WHERE group_id = ? ORDER BY stock_id",
        (group_id,),
    )
    return [{"stock_id": r[0], "cost_price": r[1], "shares": r[2], "note": r[3]} for r in cur.fetchall()]


def get_watchlist_stock(conn: sqlite3.Connection, group_id: int, stock_id: str) -> dict | None:
    row = conn.execute(
        "SELECT stock_id, cost_price, shares, note FROM watchlist_stocks WHERE group_id = ? AND stock_id = ?",
        (group_id, stock_id),
    ).fetchone()
    if row is None:
        return None
    return {"stock_id": row[0], "cost_price": row[1], "shares": row[2], "note": row[3]}


def add_stocks_to_watchlist(conn: sqlite3.Connection, group_ids: list[int], stock_ids: list[str]) -> None:
    """庫存清單「加入觀察清單」的批次動作用：把stock_ids加進每一個勾選的群組，
    已經存在該群組的股票不覆蓋既有的cost_price/shares/note(用INSERT OR IGNORE，
    跟add_watchlist_stock()的INSERT OR UPDATE語意刻意不同——「加入」是「確保存在」，
    不是「覆蓋成新值」，避免不小心把使用者已經填好的參考成本價/備註清空)。
    """
    now = datetime.now().isoformat()
    rows = [
        (group_id, stock_id, now)
        for group_id in group_ids
        for stock_id in stock_ids
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO watchlist_stocks (group_id, stock_id, created_at) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
