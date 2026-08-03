import sqlite3

import pytest

from src.data import portfolio_storage


def _fresh_conn():
    conn = sqlite3.connect(":memory:")
    portfolio_storage.ensure_portfolio_schema(conn)
    return conn


# ----------------------------------------------------------------------
# 庫存清單(2026-08-02改版：同一檔股票可以分批買入，每筆各自是獨立的批次(lot)，
# 用自動遞增id操作，不是stock_id)
# ----------------------------------------------------------------------

def test_add_inventory_stock_then_list_and_get():
    conn = _fresh_conn()
    lot_id = portfolio_storage.add_inventory_stock(
        conn, "2330", buy_date="2026-07-01", cost_price=800.0, shares=1000, fee=21.0, note="核心持股",
    )

    assert portfolio_storage.list_inventory_stock_ids(conn) == ["2330"]
    lot = portfolio_storage.get_inventory_lot(conn, lot_id)
    assert lot == {
        "id": lot_id, "stock_id": "2330", "buy_date": "2026-07-01",
        "cost_price": 800.0, "shares": 1000, "fee": 21.0, "note": "核心持股",
    }


def test_add_inventory_stock_twice_creates_two_separate_lots():
    """同一檔股票分批買入時，兩次呼叫應該各自是獨立的一筆紀錄，不會互相覆蓋——
    這是2026-08-02改版的核心需求(原本stock_id當主鍵時第二次呼叫會覆蓋第一次)。"""
    conn = _fresh_conn()
    lot1 = portfolio_storage.add_inventory_stock(conn, "2330", buy_date="2026-07-01", cost_price=800.0, shares=1000, note="第一批")
    lot2 = portfolio_storage.add_inventory_stock(conn, "2330", buy_date="2026-07-20", cost_price=850.0, shares=500, note="第二批")

    assert lot1 != lot2
    assert portfolio_storage.list_inventory_stock_ids(conn) == ["2330"]  # 相異股票代號只有一個
    rows = portfolio_storage.list_inventory_rows(conn)
    assert len(rows) == 2
    assert portfolio_storage.get_inventory_lot(conn, lot1)["cost_price"] == 800.0
    assert portfolio_storage.get_inventory_lot(conn, lot2)["cost_price"] == 850.0


def test_update_inventory_stock_only_affects_specified_lot():
    conn = _fresh_conn()
    lot1 = portfolio_storage.add_inventory_stock(conn, "2330", cost_price=800.0, shares=1000, note="")
    lot2 = portfolio_storage.add_inventory_stock(conn, "2330", cost_price=850.0, shares=500, note="")

    portfolio_storage.update_inventory_stock(conn, lot1, buy_date="2026-07-01", cost_price=820.0, shares=1500, note="更新")

    updated = portfolio_storage.get_inventory_lot(conn, lot1)
    assert updated["cost_price"] == 820.0
    assert updated["shares"] == 1500
    assert updated["buy_date"] == "2026-07-01"
    unaffected = portfolio_storage.get_inventory_lot(conn, lot2)
    assert unaffected["cost_price"] == 850.0


def test_delete_inventory_stock_only_affects_specified_lot():
    conn = _fresh_conn()
    lot1 = portfolio_storage.add_inventory_stock(conn, "2330", cost_price=800.0, shares=1000, note="")
    lot2 = portfolio_storage.add_inventory_stock(conn, "2330", cost_price=850.0, shares=500, note="")

    portfolio_storage.delete_inventory_stock(conn, lot1)

    assert portfolio_storage.get_inventory_lot(conn, lot1) is None
    assert portfolio_storage.get_inventory_lot(conn, lot2) is not None
    assert portfolio_storage.list_inventory_stock_ids(conn) == ["2330"]  # 還有lot2，股票代號還在


def test_clear_all_inventory_stocks():
    conn = _fresh_conn()
    portfolio_storage.add_inventory_stock(conn, "2330", cost_price=800.0, shares=1000, note="")
    portfolio_storage.add_inventory_stock(conn, "2454", cost_price=900.0, shares=500, note="")
    portfolio_storage.clear_all_inventory_stocks(conn)

    assert portfolio_storage.list_inventory_stock_ids(conn) == []


def test_list_inventory_rows_returns_all_lots_sorted_by_stock_id_then_buy_date():
    conn = _fresh_conn()
    portfolio_storage.add_inventory_stock(conn, "2454", cost_price=900.0, shares=500, note="")
    portfolio_storage.add_inventory_stock(conn, "2330", buy_date="2026-07-20", cost_price=850.0, shares=500, note="第二批")
    portfolio_storage.add_inventory_stock(conn, "2330", buy_date="2026-07-01", cost_price=800.0, shares=1000, note="第一批")

    rows = portfolio_storage.list_inventory_rows(conn)
    assert [r["stock_id"] for r in rows] == ["2330", "2330", "2454"]
    assert [r["buy_date"] for r in rows[:2]] == ["2026-07-01", "2026-07-20"]


def test_inventory_stock_can_be_added_without_cost_price_or_shares():
    conn = _fresh_conn()
    lot_id = portfolio_storage.add_inventory_stock(conn, "2330")

    lot = portfolio_storage.get_inventory_lot(conn, lot_id)
    assert lot["cost_price"] is None
    assert lot["buy_date"] is None
    assert lot["shares"] is None


# ----------------------------------------------------------------------
# 觀察清單群組
# ----------------------------------------------------------------------

def test_add_watchlist_group_then_list():
    conn = _fresh_conn()
    group_id = portfolio_storage.add_watchlist_group(conn, "半導體")

    groups = portfolio_storage.list_watchlist_groups(conn)
    assert groups == [{"id": group_id, "group_name": "半導體"}]


def test_add_watchlist_group_duplicate_name_raises():
    conn = _fresh_conn()
    portfolio_storage.add_watchlist_group(conn, "半導體")

    with pytest.raises(sqlite3.IntegrityError):
        portfolio_storage.add_watchlist_group(conn, "半導體")


def test_rename_watchlist_group():
    conn = _fresh_conn()
    group_id = portfolio_storage.add_watchlist_group(conn, "半導體")
    portfolio_storage.rename_watchlist_group(conn, group_id, "半導體股")

    groups = portfolio_storage.list_watchlist_groups(conn)
    assert groups == [{"id": group_id, "group_name": "半導體股"}]


def test_delete_watchlist_group_cascades_to_its_stocks():
    """SQLite預設不強制開啟PRAGMA foreign_keys，刪除群組時要確認
    watchlist_stocks裡對應的列真的被連動刪除，不是變成孤兒列。"""
    conn = _fresh_conn()
    group_id = portfolio_storage.add_watchlist_group(conn, "半導體")
    portfolio_storage.add_watchlist_stock(conn, group_id, "2330")
    portfolio_storage.add_watchlist_stock(conn, group_id, "2454")

    portfolio_storage.delete_watchlist_group(conn, group_id)

    assert portfolio_storage.list_watchlist_groups(conn) == []
    assert portfolio_storage.list_watchlist_stock_ids(conn, group_id) == []


def test_delete_watchlist_group_does_not_affect_other_groups():
    conn = _fresh_conn()
    group_a = portfolio_storage.add_watchlist_group(conn, "半導體")
    group_b = portfolio_storage.add_watchlist_group(conn, "金融股")
    portfolio_storage.add_watchlist_stock(conn, group_a, "2330")
    portfolio_storage.add_watchlist_stock(conn, group_b, "2882")

    portfolio_storage.delete_watchlist_group(conn, group_a)

    assert [g["id"] for g in portfolio_storage.list_watchlist_groups(conn)] == [group_b]
    assert portfolio_storage.list_watchlist_stock_ids(conn, group_b) == ["2882"]


# ----------------------------------------------------------------------
# 觀察清單股票
# ----------------------------------------------------------------------

def test_add_watchlist_stock_then_list_and_get():
    conn = _fresh_conn()
    group_id = portfolio_storage.add_watchlist_group(conn, "半導體")
    portfolio_storage.add_watchlist_stock(conn, group_id, "2330", cost_price=850.0, shares=None, note="觀察中")

    assert portfolio_storage.list_watchlist_stock_ids(conn, group_id) == ["2330"]
    stock = portfolio_storage.get_watchlist_stock(conn, group_id, "2330")
    assert stock == {"stock_id": "2330", "cost_price": 850.0, "shares": None, "note": "觀察中"}


def test_same_stock_can_be_in_multiple_groups():
    conn = _fresh_conn()
    group_a = portfolio_storage.add_watchlist_group(conn, "半導體")
    group_b = portfolio_storage.add_watchlist_group(conn, "權值股")
    portfolio_storage.add_watchlist_stock(conn, group_a, "2330")
    portfolio_storage.add_watchlist_stock(conn, group_b, "2330")

    assert portfolio_storage.list_watchlist_stock_ids(conn, group_a) == ["2330"]
    assert portfolio_storage.list_watchlist_stock_ids(conn, group_b) == ["2330"]


def test_delete_watchlist_stock():
    conn = _fresh_conn()
    group_id = portfolio_storage.add_watchlist_group(conn, "半導體")
    portfolio_storage.add_watchlist_stock(conn, group_id, "2330")
    portfolio_storage.delete_watchlist_stock(conn, group_id, "2330")

    assert portfolio_storage.list_watchlist_stock_ids(conn, group_id) == []


def test_clear_all_watchlist_stocks_only_affects_given_group():
    conn = _fresh_conn()
    group_a = portfolio_storage.add_watchlist_group(conn, "半導體")
    group_b = portfolio_storage.add_watchlist_group(conn, "金融股")
    portfolio_storage.add_watchlist_stock(conn, group_a, "2330")
    portfolio_storage.add_watchlist_stock(conn, group_b, "2882")

    portfolio_storage.clear_all_watchlist_stocks(conn, group_a)

    assert portfolio_storage.list_watchlist_stock_ids(conn, group_a) == []
    assert portfolio_storage.list_watchlist_stock_ids(conn, group_b) == ["2882"]


def test_add_stocks_to_watchlist_bulk_adds_to_multiple_groups():
    conn = _fresh_conn()
    group_a = portfolio_storage.add_watchlist_group(conn, "半導體")
    group_b = portfolio_storage.add_watchlist_group(conn, "權值股")

    portfolio_storage.add_stocks_to_watchlist(conn, [group_a, group_b], ["2330", "2454"])

    assert portfolio_storage.list_watchlist_stock_ids(conn, group_a) == ["2330", "2454"]
    assert portfolio_storage.list_watchlist_stock_ids(conn, group_b) == ["2330", "2454"]


def test_add_stocks_to_watchlist_does_not_overwrite_existing_values():
    """「加入觀察清單」是「確保存在」，不是「覆蓋成新值」——已經在群組裡、且已經填了
    參考成本價/備註的股票，批次加入時不應該被清空。"""
    conn = _fresh_conn()
    group_id = portfolio_storage.add_watchlist_group(conn, "半導體")
    portfolio_storage.add_watchlist_stock(conn, group_id, "2330", cost_price=900.0, shares=100, note="既有備註")

    portfolio_storage.add_stocks_to_watchlist(conn, [group_id], ["2330"])

    stock = portfolio_storage.get_watchlist_stock(conn, group_id, "2330")
    assert stock["cost_price"] == 900.0
    assert stock["note"] == "既有備註"


def test_list_watchlist_rows_returns_all_fields():
    conn = _fresh_conn()
    group_id = portfolio_storage.add_watchlist_group(conn, "半導體")
    portfolio_storage.add_watchlist_stock(conn, group_id, "2454", cost_price=900.0, shares=500, note="")
    portfolio_storage.add_watchlist_stock(conn, group_id, "2330", cost_price=800.0, shares=1000, note="")

    rows = portfolio_storage.list_watchlist_rows(conn, group_id)
    assert [r["stock_id"] for r in rows] == ["2330", "2454"]
