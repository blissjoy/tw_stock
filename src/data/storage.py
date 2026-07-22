"""SQLite 儲存層：依 schema.sql 建表，並提供各表的 upsert 輔助函式。

這裡刻意保持單純的「連線→執行SQL→關閉」風格，不做ORM包裝；批次寫入用 executemany，
所有寫入都用 INSERT ... ON CONFLICT DO UPDATE，讓「重複執行同一天的抓取」是安全的
（覆蓋而非報錯或產生重複列），這對之後 GitHub Actions 排程重跑很重要。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def ensure_schema(conn) -> None:
    """對任一已開啟的連線(本機sqlite3.Connection或src.data.turso_client.TursoConnection皆可)
    套用 schema.sql 建表，可重複呼叫(CREATE TABLE IF NOT EXISTS)。"""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def init_db(db_path: str | Path, check_same_thread: bool = True) -> sqlite3.Connection:
    """依 schema.sql 建立(或沿用既有)本機檔案資料庫，回傳已開啟的連線。

    check_same_thread=False 給像 Streamlit 這種「快取的連線可能被不同執行緒重用」的呼叫端用
    （@st.cache_resource 快取的物件在rerun之間可能不是同一條thread，sqlite3預設會拒絕跨
    thread使用同一個connection並丟出 ProgrammingError）；一般CLI腳本維持預設True即可。
    """
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    ensure_schema(conn)
    return conn


def upsert_stocks(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows的每個dict需含 stock_id/name/market/industry/updated_at 欄位。"""
    conn.executemany(
        """
        INSERT INTO stocks (stock_id, name, market, industry, updated_at)
        VALUES (:stock_id, :name, :market, :industry, :updated_at)
        ON CONFLICT(stock_id) DO UPDATE SET
            name = excluded.name, market = excluded.market,
            industry = excluded.industry, updated_at = excluded.updated_at
        """,
        rows,
    )
    conn.commit()


def upsert_stock_prices(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows的每個dict需含 stock_id/date/open/high/low/close/volume/trading_money/trading_turnover/spread。"""
    conn.executemany(
        """
        INSERT INTO stock_prices
            (stock_id, date, open, high, low, close, volume, trading_money, trading_turnover, spread)
        VALUES
            (:stock_id, :date, :open, :high, :low, :close, :volume, :trading_money, :trading_turnover, :spread)
        ON CONFLICT(stock_id, date) DO UPDATE SET
            open = excluded.open, high = excluded.high, low = excluded.low, close = excluded.close,
            volume = excluded.volume, trading_money = excluded.trading_money,
            trading_turnover = excluded.trading_turnover, spread = excluded.spread
        """,
        rows,
    )
    conn.commit()


def upsert_institutional_investors(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows的每個dict需含 stock_id/date/investor_type/buy/sell。"""
    conn.executemany(
        """
        INSERT INTO institutional_investors (stock_id, date, investor_type, buy, sell)
        VALUES (:stock_id, :date, :investor_type, :buy, :sell)
        ON CONFLICT(stock_id, date, investor_type) DO UPDATE SET
            buy = excluded.buy, sell = excluded.sell
        """,
        rows,
    )
    conn.commit()


def upsert_margin_trading(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows的每個dict需含 margin_trading 表的所有欄位（見 schema.sql）。"""
    conn.executemany(
        """
        INSERT INTO margin_trading (
            stock_id, date, margin_purchase_buy, margin_purchase_sell,
            margin_purchase_cash_repayment, margin_purchase_yesterday_balance,
            margin_purchase_today_balance, margin_purchase_limit,
            short_sale_buy, short_sale_sell, short_sale_cash_repayment,
            short_sale_yesterday_balance, short_sale_today_balance, short_sale_limit,
            offset_loan_and_short
        ) VALUES (
            :stock_id, :date, :margin_purchase_buy, :margin_purchase_sell,
            :margin_purchase_cash_repayment, :margin_purchase_yesterday_balance,
            :margin_purchase_today_balance, :margin_purchase_limit,
            :short_sale_buy, :short_sale_sell, :short_sale_cash_repayment,
            :short_sale_yesterday_balance, :short_sale_today_balance, :short_sale_limit,
            :offset_loan_and_short
        )
        ON CONFLICT(stock_id, date) DO UPDATE SET
            margin_purchase_buy = excluded.margin_purchase_buy,
            margin_purchase_sell = excluded.margin_purchase_sell,
            margin_purchase_cash_repayment = excluded.margin_purchase_cash_repayment,
            margin_purchase_yesterday_balance = excluded.margin_purchase_yesterday_balance,
            margin_purchase_today_balance = excluded.margin_purchase_today_balance,
            margin_purchase_limit = excluded.margin_purchase_limit,
            short_sale_buy = excluded.short_sale_buy,
            short_sale_sell = excluded.short_sale_sell,
            short_sale_cash_repayment = excluded.short_sale_cash_repayment,
            short_sale_yesterday_balance = excluded.short_sale_yesterday_balance,
            short_sale_today_balance = excluded.short_sale_today_balance,
            short_sale_limit = excluded.short_sale_limit,
            offset_loan_and_short = excluded.offset_loan_and_short
        """,
        rows,
    )
    conn.commit()


def upsert_securities_traders(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows的每個dict需含 securities_trader_id/securities_trader/address/phone/updated_at。"""
    conn.executemany(
        """
        INSERT INTO securities_traders (securities_trader_id, securities_trader, address, phone, updated_at)
        VALUES (:securities_trader_id, :securities_trader, :address, :phone, :updated_at)
        ON CONFLICT(securities_trader_id) DO UPDATE SET
            securities_trader = excluded.securities_trader, address = excluded.address,
            phone = excluded.phone, updated_at = excluded.updated_at
        """,
        rows,
    )
    conn.commit()


def upsert_broker_chips(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows的每個dict需含 stock_id/date/securities_trader_id/price/buy/sell。"""
    conn.executemany(
        """
        INSERT INTO broker_chips (stock_id, date, securities_trader_id, price, buy, sell)
        VALUES (:stock_id, :date, :securities_trader_id, :price, :buy, :sell)
        ON CONFLICT(stock_id, date, securities_trader_id, price) DO UPDATE SET
            buy = excluded.buy, sell = excluded.sell
        """,
        rows,
    )
    conn.commit()


def mark_fetched(conn: sqlite3.Connection, dataset: str, stock_id: str, date: str, fetched_at: str) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log (dataset, stock_id, date, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(dataset, stock_id, date) DO UPDATE SET fetched_at = excluded.fetched_at
        """,
        (dataset, stock_id, date, fetched_at),
    )
    conn.commit()


def is_fetched(conn: sqlite3.Connection, dataset: str, stock_id: str, date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM fetch_log WHERE dataset = ? AND stock_id = ? AND date = ?",
        (dataset, stock_id, date),
    ).fetchone()
    return row is not None


def upsert_daily_candidates(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows的每個dict需含 date/stock_id/signal_name/entry_price/stop_loss/note/created_at。"""
    conn.executemany(
        """
        INSERT INTO daily_candidates (date, stock_id, signal_name, entry_price, stop_loss, note, created_at)
        VALUES (:date, :stock_id, :signal_name, :entry_price, :stop_loss, :note, :created_at)
        ON CONFLICT(date, stock_id, signal_name) DO UPDATE SET
            entry_price = excluded.entry_price, stop_loss = excluded.stop_loss,
            note = excluded.note, created_at = excluded.created_at
        """,
        rows,
    )
    conn.commit()


def get_latest_candidates(conn: sqlite3.Connection) -> list[dict]:
    """回傳最新一天的候選清單（若當天無候選則回傳空list，不回溯更早的日期）。"""
    latest = conn.execute("SELECT MAX(date) FROM daily_candidates").fetchone()[0]
    if latest is None:
        return []
    cur = conn.execute(
        """
        SELECT dc.date, dc.stock_id, s.name, dc.signal_name, dc.entry_price, dc.stop_loss, dc.note
        FROM daily_candidates dc JOIN stocks s ON dc.stock_id = s.stock_id
        WHERE dc.date = ? ORDER BY dc.stock_id
        """,
        (latest,),
    )
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]
