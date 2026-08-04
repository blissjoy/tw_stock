from src.data.portfolio_storage import add_watchlist_group, add_watchlist_stock, init_portfolio_db
from src.data.storage import init_db, upsert_stock_prices, upsert_stocks
from src.presentation import watchlist_export


def _main_conn():
    return init_db(":memory:")


def _portfolio_conn():
    return init_portfolio_db(":memory:")


def _seed_stock(conn, stock_id: str, name: str, close: float) -> None:
    upsert_stocks(conn, [{"stock_id": stock_id, "name": name, "market": "TWSE", "industry": None, "updated_at": "2026-08-03"}])
    upsert_stock_prices(conn, [
        {"stock_id": stock_id, "date": "2026-08-03", "open": close, "high": close, "low": close,
         "close": close, "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])


def test_build_group_table_has_two_header_rows_and_data_rows():
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", 100.0)
    group_id = add_watchlist_group(portfolio_conn, "測試群組")
    add_watchlist_stock(portfolio_conn, group_id, "2330", cost_price=None, shares=None, note=None)

    table = watchlist_export.build_group_table(main_conn, portfolio_conn, group_id)

    assert len(table["values"]) == 3  # 2列表頭 + 1檔股票
    assert table["values"][1][:2] == ["股票代號", "名稱"]
    assert table["values"][2][0] == "2330"
    assert table["values"][2][1] == "台積電"


def test_build_group_table_group_label_row_has_merges_and_background():
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", 100.0)
    group_id = add_watchlist_group(portfolio_conn, "測試群組")
    add_watchlist_stock(portfolio_conn, group_id, "2330", cost_price=None, shares=None, note=None)

    table = watchlist_export.build_group_table(main_conn, portfolio_conn, group_id)

    assert table["values"][0][11] == "法人近期籌碼"
    assert table["values"][0][17] == "法人買賣超（張數）"
    assert (0, 11) in table["background_colors"]
    assert (0, 11, 0, 12) in table["merges"]  # 投信/外資合併
    assert (0, 17, 0, 24) in table["merges"]  # 法人買賣超8欄合併


def test_build_group_table_a1_has_export_timestamp():
    """2026-08-04新增：A1(header_row0第一欄)要放這次匯出的時間，使用者才知道
    Google Sheet上的資料是什麼時候匯出的。"""
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    group_id = add_watchlist_group(portfolio_conn, "測試群組")

    table = watchlist_export.build_group_table(main_conn, portfolio_conn, group_id)

    assert table["values"][0][0].startswith("更新時間：")


def test_build_group_table_colors_name_column_by_listing_type():
    """2026-08-04新增：名稱欄依市場別上色(上市藍/上櫃黑/興櫃灰)，跟desktop/
    main_window.py的觀察清單表格共用同一份對照表(src.presentation.portfolio_
    data.listing_type_color())。"""
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", 100.0)
    upsert_stocks(main_conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "listing_type": "twse", "updated_at": "2026-08-03"}])
    group_id = add_watchlist_group(portfolio_conn, "測試群組")
    add_watchlist_stock(portfolio_conn, group_id, "2330", cost_price=None, shares=None, note=None)

    table = watchlist_export.build_group_table(main_conn, portfolio_conn, group_id)

    name_col = 1  # "股票代號","名稱"是前兩欄
    assert table["text_colors"][(2, name_col)] == "#0000CC"


def test_build_group_table_empty_group_still_has_headers():
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    group_id = add_watchlist_group(portfolio_conn, "空群組")

    table = watchlist_export.build_group_table(main_conn, portfolio_conn, group_id)

    assert len(table["values"]) == 2
    assert len(table["values"][1]) == 25  # 11個既有欄位 + 14個籌碼欄位


def test_export_all_watchlist_groups_counts_groups(monkeypatch):
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", 100.0)
    group_id_1 = add_watchlist_group(portfolio_conn, "群組1")
    add_watchlist_group(portfolio_conn, "群組2")
    add_watchlist_stock(portfolio_conn, group_id_1, "2330", cost_price=None, shares=None, note=None)

    exported = []
    monkeypatch.setattr(
        watchlist_export, "export_watchlist_group",
        lambda main_conn, portfolio_conn, group_id, group_name, spreadsheet_id, interactive=True: exported.append((group_id, group_name)),
    )

    count = watchlist_export.export_all_watchlist_groups(main_conn, portfolio_conn, spreadsheet_id="fake-sheet-id")

    assert count == 2
    assert len(exported) == 2
