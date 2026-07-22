import scripts.fix_stock_names as fix_stock_names
from src.data.storage import init_db, upsert_stocks


def _fresh_conn():
    return init_db(":memory:")


def test_fix_stock_names_overwrites_placeholder_name_and_industry(monkeypatch):
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "2330", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    monkeypatch.setattr(fix_stock_names.finmind_client, "fetch_stock_info", lambda: [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"},
    ])

    updated = fix_stock_names.fix_stock_names(conn)

    assert updated == 1
    row = conn.execute("SELECT name, industry FROM stocks WHERE stock_id = '2330'").fetchone()
    assert row == ("台積電", "半導體")


def test_fix_stock_names_skips_stocks_not_in_finmind_list(monkeypatch):
    """只更新FinMind名單裡查得到的股票，資料庫裡其他既有列應保持原樣不被觸碰。"""
    conn = _fresh_conn()
    upsert_stocks(conn, [
        {"stock_id": "2330", "name": "2330", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
        {"stock_id": "9999", "name": "9999", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"},
    ])
    monkeypatch.setattr(fix_stock_names.finmind_client, "fetch_stock_info", lambda: [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"},
    ])

    updated = fix_stock_names.fix_stock_names(conn)

    assert updated == 1
    row = conn.execute("SELECT name FROM stocks WHERE stock_id = '9999'").fetchone()
    assert row == ("9999",)  # 未被觸碰


def test_fix_stock_names_handles_empty_finmind_response(monkeypatch):
    conn = _fresh_conn()
    upsert_stocks(conn, [{"stock_id": "2330", "name": "2330", "market": "TWSE", "industry": None, "updated_at": "2026-07-22"}])
    monkeypatch.setattr(fix_stock_names.finmind_client, "fetch_stock_info", lambda: [])

    updated = fix_stock_names.fix_stock_names(conn)
    assert updated == 0
