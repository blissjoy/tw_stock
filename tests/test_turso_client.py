import sqlite3

from src.data.storage import upsert_stock_prices, upsert_stocks
from src.data.turso_client import TursoConnection, _to_positional


def test_to_positional_converts_named_params_in_declared_order():
    sql = "INSERT INTO t (a, b, c) VALUES (:a, :b, :c)"
    positional_sql, params = _to_positional(sql, {"c": 3, "a": 1, "b": 2})
    assert positional_sql == "INSERT INTO t (a, b, c) VALUES (?, ?, ?)"
    assert params == (1, 2, 3)


def test_turso_connection_executemany_with_named_params_round_trips():
    """驗證storage.py既有的具名參數SQL透過TursoConnection轉接後，行為與直接用sqlite3一致。"""
    raw = sqlite3.connect(":memory:")
    raw.execute(
        "CREATE TABLE stocks (stock_id TEXT PRIMARY KEY, name TEXT, market TEXT, industry TEXT, updated_at TEXT)"
    )
    raw.execute(
        "CREATE TABLE stock_prices (stock_id TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, "
        "volume INTEGER, trading_money INTEGER, trading_turnover INTEGER, spread REAL, "
        "PRIMARY KEY (stock_id, date))"
    )
    conn = TursoConnection(raw)

    upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    upsert_stock_prices(conn, [{
        "stock_id": "2330", "date": "2026-07-22", "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0,
        "volume": 1000000, "trading_money": 104000000, "trading_turnover": 500, "spread": 2.0,
    }])

    row = raw.execute("SELECT close FROM stock_prices WHERE stock_id='2330'").fetchone()
    assert row == (104.0,)


def test_turso_connection_executemany_falls_back_to_loop_when_raw_lacks_executemany():
    """模擬底層client沒有executemany的情況(逐列呼叫execute)，確認具名參數仍正確轉換。"""
    raw = sqlite3.connect(":memory:")
    raw.execute("CREATE TABLE stocks (stock_id TEXT PRIMARY KEY, name TEXT, market TEXT, industry TEXT, updated_at TEXT)")

    class _NoExecuteMany:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=None):
            return self._inner.execute(sql, params) if params is not None else self._inner.execute(sql)

        def commit(self):
            self._inner.commit()

    conn = TursoConnection(_NoExecuteMany(raw))
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    count = raw.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    assert count == 2


def test_turso_connection_execute_with_positional_tuple_passthrough():
    raw = sqlite3.connect(":memory:")
    raw.execute("CREATE TABLE fetch_log (dataset TEXT, stock_id TEXT, date TEXT, fetched_at TEXT)")
    conn = TursoConnection(raw)

    conn.execute(
        "INSERT INTO fetch_log (dataset, stock_id, date, fetched_at) VALUES (?, ?, ?, ?)",
        ("TaiwanStockPrice", "2330", "2026-07-22", "2026-07-22T18:00:00"),
    )
    conn.commit()
    row = raw.execute("SELECT dataset FROM fetch_log").fetchone()
    assert row == ("TaiwanStockPrice",)
