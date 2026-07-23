import os
import sqlite3
import tempfile

import libsql_client
import pytest

from src.data.storage import ensure_schema, get_latest_candidates, upsert_daily_candidates, upsert_stock_prices, upsert_stocks
from src.data.turso_client import TursoConnection, _force_https_scheme, _to_positional


def test_force_https_scheme_converts_libsql_scheme_to_https():
    assert _force_https_scheme("libsql://twstock-joywang.aws-ap-northeast-1.turso.io") == \
        "https://twstock-joywang.aws-ap-northeast-1.turso.io"


def test_force_https_scheme_preserves_path_and_query():
    assert _force_https_scheme("libsql://host.turso.io/path?x=1") == "https://host.turso.io/path?x=1"


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


@pytest.fixture
def real_libsql_client_conn():
    """實測用的真實 libsql_client 連線（透過本機file:模式，不需要網路或Turso帳號），
    用來驗證 TursoConnection 對「真正會用到的那個套件」的介面轉接是否正確，
    而不是只測我們自己捏造的假物件。"""
    path = os.path.join(tempfile.gettempdir(), f"_turso_client_test_{os.getpid()}.db")
    if os.path.exists(path):
        os.remove(path)
    raw = libsql_client.create_client_sync(f"file:{path}")
    yield TursoConnection(raw)
    raw.close()
    if os.path.exists(path):
        os.remove(path)


def test_turso_connection_against_real_libsql_client_full_round_trip(real_libsql_client_conn):
    """對真實libsql_client套件跑一次完整的 ensure_schema + upsert + query 流程，
    確認ResultSet包裝(_ResultSetCursor)、批次寫入(.batch())、無commit()都運作正常。"""
    conn = real_libsql_client_conn
    ensure_schema(conn)

    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "1101", "name": "台泥", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    upsert_daily_candidates(conn, [
        {"date": "2026-07-22", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 104.0, "stop_loss": 99.0, "note": "測試", "created_at": "2026-07-22T18:00:00"},
    ])

    candidates = get_latest_candidates(conn)
    assert len(candidates) == 1
    assert candidates[0]["stock_id"] == "2330"
    assert candidates[0]["name"] == "台積電"
    assert candidates[0]["entry_price"] == 104.0

    # 確認 fetchone()/description 介面轉接正確
    row = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()
    assert row == (2,)
    cur = conn.execute("SELECT stock_id, name FROM stocks ORDER BY stock_id")
    assert [c[0] for c in cur.description] == ["stock_id", "name"]
    assert cur.fetchall() == [("1101", "台泥"), ("2330", "台積電")]


def test_turso_connection_ensure_schema_twice_does_not_crash(real_libsql_client_conn):
    """ensure_schema()應該是冪等操作(CREATE TABLE/INDEX IF NOT EXISTS)，重複呼叫（例如
    daily_pipeline.py與Streamlit儀表板各自獨立呼叫get_conn()）不應該crash。這裡對真實
    libsql_client連續呼叫兩次，確認「表格已存在」的情境下第二次仍正常。"""
    conn = real_libsql_client_conn
    ensure_schema(conn)
    ensure_schema(conn)  # 不應該拋出例外

    row = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()
    assert row == (0,)


def test_executescript_retries_and_recovers_from_transient_keyerror(monkeypatch):
    """libsql_client 0.3.1在HTTP 200但回應JSON形狀不是預期的{"result": ...}時，會丟出
    未預期的裸KeyError('result')，這可能代表真正瞬間性的問題(例如短暫網路異常)，也可能是
    像Turso寫入額度用完這種持續性狀態(見_execute_schema_statement_with_retry的docstring)。
    這裡模擬「前兩次是瞬間問題、第三次恢復」，確認短暫重試能處理真正瞬間性的情況，不會在
    第一次失敗就整段放棄。"""
    import src.data.turso_client as turso_client_module
    monkeypatch.setattr(turso_client_module.time, "sleep", lambda _: None)

    class _FailsTwiceThenSucceeds:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls < 3:
                raise KeyError("result")  # 模擬libsql_client 0.3.1的真實錯誤型態

    raw = _FailsTwiceThenSucceeds()
    conn = TursoConnection(raw)

    conn.executescript("PRAGMA foreign_keys = ON;")  # 不應該拋出例外

    assert raw.calls == 3  # 前兩次失敗都有重試，第三次成功後不再繼續嘗試


def test_executescript_raises_after_retries_exhausted_on_persistent_keyerror(monkeypatch):
    """如果KeyError不是瞬間問題、而是持續發生(例如Turso寫入額度用完，重試次數用完仍失敗)，
    不應該被靜默吞掉到底——否則等於永遠看不出schema真的建立失敗了，呼叫端(daily_pipeline.py
    這種真的需要寫入成功的流程)也才有機會捕捉這個例外、決定要不要用降級方式處理。"""
    import src.data.turso_client as turso_client_module
    monkeypatch.setattr(turso_client_module.time, "sleep", lambda _: None)

    class _AlwaysRaises:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            raise KeyError("result")

    raw = _AlwaysRaises()
    conn = TursoConnection(raw)

    with pytest.raises(KeyError):
        conn.executescript("CREATE TABLE IF NOT EXISTS t (a INT);")

    assert raw.calls == turso_client_module._SCHEMA_RETRY_ATTEMPTS


def test_executescript_reraises_non_keyerror_immediately_without_retry():
    """非KeyError的例外代表真正的SQL錯誤(不是套件在併發衝突下的已知bug訊號)，應該立即往外拋，
    不要浪費時間重試、也不能被靜默吞掉(避免掩蓋真正的錯誤)。"""

    class _RaisesOnExecute:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            raise RuntimeError("模擬真實的SQL錯誤")

    raw = _RaisesOnExecute()
    conn = TursoConnection(raw)

    with pytest.raises(RuntimeError, match="模擬真實的SQL錯誤"):
        conn.executescript("INSERT INTO t (a) VALUES (1);")

    assert raw.calls == 1  # 沒有重試


def test_turso_connection_batch_writes_many_rows_against_real_client(real_libsql_client_conn):
    """驗證executemany對真實client會走.batch()分chunk路徑（非逐列迴圈），且chunk邊界(500)
    前後的筆數都能正確寫入，不會漏掉最後一個不滿一個chunk的餘數。"""
    conn = real_libsql_client_conn
    ensure_schema(conn)

    rows = [
        {"stock_id": f"{1000 + i}", "name": f"股票{i}", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}
        for i in range(1201)  # 2個完整chunk(500) + 1個餘數chunk(201)
    ]
    upsert_stocks(conn, rows)

    count = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    assert count == 1201
