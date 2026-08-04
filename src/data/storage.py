"""SQLite 儲存層：依 schema.sql 建表，並提供各表的 upsert 輔助函式。

這裡刻意保持單純的「連線→執行SQL→關閉」風格，不做ORM包裝；批次寫入用 executemany，
所有寫入都用 INSERT ... ON CONFLICT DO UPDATE，讓「重複執行同一天的抓取」是安全的
（覆蓋而非報錯或產生重複列），這對之後 GitHub Actions 排程重跑很重要。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def ensure_schema(conn) -> None:
    """對任一已開啟的連線(本機sqlite3.Connection或src.data.turso_client.TursoConnection皆可)
    套用 schema.sql 建表，可重複呼叫(CREATE TABLE IF NOT EXISTS)。"""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate_schema(conn)
    conn.commit()


def _migrate_schema(conn) -> None:
    """補齊「表已經存在、但缺少後來才新增的欄位」的情況，跟src/data/portfolio_storage.py
    的_migrate_portfolio_schema()同一個理由/同一種做法：CREATE TABLE IF NOT EXISTS只在表
    完全不存在時才會建表，對已經累積過歷史資料的daily_indicators表不會自動補上schema.sql
    後來新增的欄位。用PRAGMA table_info()檢查欄位是否存在，不存在才ALTER TABLE ADD
    COLUMN，可重複呼叫不會重複執行。

    2026-08-04：ma200是第一個踩到這個問題的欄位(SAR翻轉篩選的「股價相對長期均線」條件
    改用ma200，見schema.sql該欄位的說明)，之後daily_indicators再新增欄位都要在這裡比照
    補一行。"""
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_indicators)").fetchall()}
    if "ma200" not in existing_columns:
        conn.execute("ALTER TABLE daily_indicators ADD COLUMN ma200 REAL")


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


def delete_daily_candidates_for_date(conn: sqlite3.Connection, iso_date: str) -> None:
    """刪除指定日期的所有候選紀錄。daily_screener.run_screen_and_store()在寫入這次重算的
    結果前會先呼叫這個函式——同一天可能重跑選股好幾次(例如手動按「立即重新篩選」按了不只
    一次、或補資料後又重算一次)，每次重算都是用『當下資料庫裡現有資料』從頭算出完整的候選
    清單，不是增量疊加；如果不先清掉舊紀錄，只用INSERT...ON CONFLICT DO UPDATE的話，
    「上一次選中、但這次已經不再符合條件」的股票會繼續留在表裡，讓候選清單顯示過時的
    結果。這裡刪除後由呼叫端寫入這次的完整結果，確保每個日期的候選清單永遠只反映『最新一次
    重算』的正確狀態。"""
    conn.execute("DELETE FROM daily_candidates WHERE date = ?", (iso_date,))
    conn.commit()


def upsert_daily_data_status(conn: sqlite3.Connection, iso_date: str, is_intraday: bool) -> None:
    """記錄某天的資料是官方最終收盤價(is_intraday=False)還是盤中即時價備援(True)，見
    schema.sql的daily_data_status說明。同一天重複寫入會覆蓋成最新一次抓取的實際狀態
    (例如盤中先抓一次、收盤後再抓一次，flag會自動從True修正回False)。"""
    conn.execute(
        """
        INSERT INTO daily_data_status (date, is_intraday, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET is_intraday = excluded.is_intraday, updated_at = excluded.updated_at
        """,
        (iso_date, int(is_intraday), datetime.now().isoformat()),
    )
    conn.commit()


def upsert_daily_indicators(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows的每個dict需含 stock_id/date/ma5/ma10/ma20/ma60/ma120/ma200/ma240/sar_value/
    sar_is_bull/sar_flip_days_ago/updated_at，見schema.sql的daily_indicators說明。
    來源見src/screener/indicator_precompute.py。"""
    conn.executemany(
        """
        INSERT INTO daily_indicators
            (stock_id, date, ma5, ma10, ma20, ma60, ma120, ma200, ma240, sar_value, sar_is_bull, sar_flip_days_ago, updated_at)
        VALUES
            (:stock_id, :date, :ma5, :ma10, :ma20, :ma60, :ma120, :ma200, :ma240, :sar_value, :sar_is_bull, :sar_flip_days_ago, :updated_at)
        ON CONFLICT(stock_id, date) DO UPDATE SET
            ma5 = excluded.ma5, ma10 = excluded.ma10, ma20 = excluded.ma20,
            ma60 = excluded.ma60, ma120 = excluded.ma120, ma200 = excluded.ma200, ma240 = excluded.ma240,
            sar_value = excluded.sar_value, sar_is_bull = excluded.sar_is_bull,
            sar_flip_days_ago = excluded.sar_flip_days_ago, updated_at = excluded.updated_at
        """,
        rows,
    )
    conn.commit()


def upsert_holder_shares_distribution(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows的每個dict需含 stock_id/date/holding_shares_level/people/unit/percent/
    updated_at，見schema.sql的holder_shares_distribution說明。來源見
    src/data/finmind_client.py的fetch_holding_shares_per()。"""
    conn.executemany(
        """
        INSERT INTO holder_shares_distribution
            (stock_id, date, holding_shares_level, people, unit, percent, updated_at)
        VALUES
            (:stock_id, :date, :holding_shares_level, :people, :unit, :percent, :updated_at)
        ON CONFLICT(stock_id, date, holding_shares_level) DO UPDATE SET
            people = excluded.people, unit = excluded.unit, percent = excluded.percent,
            updated_at = excluded.updated_at
        """,
        rows,
    )
    conn.commit()


def upsert_delisted_stocks(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows的每個dict需含 stock_id/name/delisted_date/reason/noted_at，見schema.sql的
    delisted_stocks說明。ON CONFLICT時只更新reason/noted_at，不覆蓋已知的delisted_date
    (第一次記錄時如果查得到確切日期就不該被之後「查不到」的重試覆蓋掉)。"""
    conn.executemany(
        """
        INSERT INTO delisted_stocks (stock_id, name, delisted_date, reason, noted_at)
        VALUES (:stock_id, :name, :delisted_date, :reason, :noted_at)
        ON CONFLICT(stock_id) DO UPDATE SET
            reason = excluded.reason, noted_at = excluded.noted_at
        """,
        rows,
    )
    conn.commit()


def list_delisted_stock_ids(conn: sqlite3.Connection) -> set[str]:
    """回傳目前已記錄下市的股票代號集合，供排程跳過重複下載嘗試、UI排除顯示用。"""
    return {row[0] for row in conn.execute("SELECT stock_id FROM delisted_stocks").fetchall()}


def get_daily_data_status(conn: sqlite3.Connection, iso_date: str) -> bool | None:
    """回傳指定日期是否為盤中即時價(True)/官方收盤價(False)；查無紀錄回傳None(例如這個
    功能上線前就存在的歷史資料，一律不特別標示，視為已收盤)。"""
    row = conn.execute("SELECT is_intraday FROM daily_data_status WHERE date = ?", (iso_date,)).fetchone()
    return bool(row[0]) if row is not None else None


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
