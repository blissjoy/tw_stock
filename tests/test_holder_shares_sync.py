import sqlite3

from src.data import holder_shares_sync, portfolio_storage, storage


def _fresh_conn():
    return storage.init_db(":memory:")


def _fresh_portfolio_conn():
    conn = sqlite3.connect(":memory:")
    portfolio_storage.ensure_portfolio_schema(conn)
    return conn


def _seed_stock(conn, stock_id: str) -> None:
    storage.upsert_stocks(conn, [{"stock_id": stock_id, "name": stock_id, "market": "TWSE", "industry": None, "updated_at": "2026-08-04"}])


# ============================================================
# fetch_and_store_holder_shares
# ============================================================


def test_fetch_and_store_holder_shares_writes_rows_for_each_stock(monkeypatch):
    conn = _fresh_conn()
    _seed_stock(conn, "2330")
    _seed_stock(conn, "2454")

    def _fake_fetch(stock_id, start_date, end_date):
        return [{"stock_id": stock_id, "date": "2026-07-31", "holding_shares_level": "more than 1,000,001",
                  "people": 10, "unit": 100, "percent": 5.0}]

    monkeypatch.setattr(holder_shares_sync.finmind_client, "fetch_holding_shares_per", _fake_fetch)

    count = holder_shares_sync.fetch_and_store_holder_shares(conn, ["2330", "2454"])

    assert count == 2
    rows = conn.execute("SELECT stock_id FROM holder_shares_distribution ORDER BY stock_id").fetchall()
    assert [r[0] for r in rows] == ["2330", "2454"]


def test_fetch_and_store_holder_shares_skips_failed_stock_and_continues(monkeypatch):
    conn = _fresh_conn()
    _seed_stock(conn, "2330")
    _seed_stock(conn, "2454")

    def _fake_fetch(stock_id, start_date, end_date):
        if stock_id == "2330":
            raise RuntimeError("模擬FinMind暫時性錯誤")
        return [{"stock_id": stock_id, "date": "2026-07-31", "holding_shares_level": "more than 1,000,001",
                  "people": 10, "unit": 100, "percent": 5.0}]

    monkeypatch.setattr(holder_shares_sync.finmind_client, "fetch_holding_shares_per", _fake_fetch)

    count = holder_shares_sync.fetch_and_store_holder_shares(conn, ["2330", "2454"])

    assert count == 1
    assert conn.execute("SELECT COUNT(*) FROM holder_shares_distribution WHERE stock_id = '2330'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM holder_shares_distribution WHERE stock_id = '2454'").fetchone()[0] == 1


def test_fetch_and_store_holder_shares_empty_list_returns_zero_without_network_call(monkeypatch):
    conn = _fresh_conn()

    def _fail(*args, **kwargs):
        raise AssertionError("stock_ids為空時不應該呼叫FinMind")

    monkeypatch.setattr(holder_shares_sync.finmind_client, "fetch_holding_shares_per", _fail)

    assert holder_shares_sync.fetch_and_store_holder_shares(conn, []) == 0


# ============================================================
# refresh_watchlist_holder_shares
# ============================================================


def test_refresh_watchlist_holder_shares_only_covers_watchlist_stocks(monkeypatch):
    conn = _fresh_conn()
    _seed_stock(conn, "2330")
    portfolio_conn = _fresh_portfolio_conn()
    group_id = portfolio_storage.add_watchlist_group(portfolio_conn, "半導體")
    portfolio_storage.add_watchlist_stock(portfolio_conn, group_id, "2330")

    fetch_calls = []

    def _fake_fetch(stock_id, start_date, end_date):
        fetch_calls.append(stock_id)
        return [{"stock_id": stock_id, "date": "2026-07-31", "holding_shares_level": "more than 1,000,001",
                  "people": 10, "unit": 100, "percent": 12.0}]

    monkeypatch.setattr(holder_shares_sync.finmind_client, "fetch_holding_shares_per", _fake_fetch)

    count = holder_shares_sync.refresh_watchlist_holder_shares(conn, portfolio_conn)

    assert fetch_calls == ["2330"]
    assert count == 1


def test_refresh_watchlist_holder_shares_returns_zero_when_watchlist_empty():
    conn = _fresh_conn()
    portfolio_conn = _fresh_portfolio_conn()

    assert holder_shares_sync.refresh_watchlist_holder_shares(conn, portfolio_conn) == 0


# ============================================================
# list_stock_ids_without_holder_data
# ============================================================


def test_list_stock_ids_without_holder_data_returns_only_missing_ones():
    conn = _fresh_conn()
    _seed_stock(conn, "2330")
    _seed_stock(conn, "2454")
    storage.upsert_holder_shares_distribution(conn, [
        {"stock_id": "2330", "date": "2026-07-31", "holding_shares_level": "more than 1,000,001",
         "people": 10, "unit": 100, "percent": 12.0, "updated_at": "2026-07-31T00:00:00"},
    ])

    result = holder_shares_sync.list_stock_ids_without_holder_data(conn, ["2330", "2454"])

    assert result == ["2454"]


def test_list_stock_ids_without_holder_data_empty_input_returns_empty():
    conn = _fresh_conn()
    assert holder_shares_sync.list_stock_ids_without_holder_data(conn, []) == []


def test_list_stock_ids_without_holder_data_all_missing_when_table_empty():
    conn = _fresh_conn()
    _seed_stock(conn, "2330")
    assert holder_shares_sync.list_stock_ids_without_holder_data(conn, ["2330"]) == ["2330"]
