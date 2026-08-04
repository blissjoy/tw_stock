from src.data.portfolio_storage import add_watchlist_group, add_watchlist_stock, init_portfolio_db
from src.data.storage import init_db, upsert_daily_candidates, upsert_stock_prices, upsert_stocks
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


# ============================================================
# 選股清單(候選清單)匯出(2026-08-04新增)
# ============================================================


def test_build_candidate_list_table_has_headers_and_row(monkeypatch):
    """套用跟UI候選清單同一套預設篩選(見build_candidate_list_table()說明)——這裡
    只驗證表格組裝/接線邏輯，不重新測試apply_candidate_filters()本身的篩選邏輯
    是否正確(那部分tests/test_chart_data.py已經覆蓋)，跟test_daily_pipeline.py
    測試LINE/Email通知篩選接線時同一種做法：直接讓篩選函式原樣放行。"""
    monkeypatch.setattr(watchlist_export.chart_data, "apply_candidate_filters", lambda conn, df, *a, **k: df)
    main_conn = _main_conn()
    _seed_stock(main_conn, "2330", "台積電", 100.0)
    upsert_daily_candidates(main_conn, [
        {"date": "2026-08-03", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-08-03T18:00:00"},
    ])

    table = watchlist_export.build_candidate_list_table(main_conn)

    assert table["values"][1] == watchlist_export._CANDIDATE_HEADERS
    assert table["values"][0][0].startswith("更新時間：")
    assert table["values"][0][1] == "候選清單日期：2026-08-03"
    assert table["values"][2][0] == "2330"
    assert table["values"][2][1] == "台積電"


def test_build_candidate_list_table_empty_when_no_candidates():
    main_conn = _main_conn()

    table = watchlist_export.build_candidate_list_table(main_conn)

    assert len(table["values"]) == 2  # 只有2列表頭，沒有任何資料列
    assert table["values"][0][1] == ""  # 沒有latest_date，不顯示候選清單日期


def test_build_candidate_list_table_colors_name_column_by_listing_type(monkeypatch):
    monkeypatch.setattr(watchlist_export.chart_data, "apply_candidate_filters", lambda conn, df, *a, **k: df)
    main_conn = _main_conn()
    upsert_stocks(main_conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "listing_type": "twse", "updated_at": "2026-08-03"}])
    upsert_stock_prices(main_conn, [
        {"stock_id": "2330", "date": "2026-08-03", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_daily_candidates(main_conn, [
        {"date": "2026-08-03", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-08-03T18:00:00"},
    ])

    table = watchlist_export.build_candidate_list_table(main_conn)

    name_col = watchlist_export._CANDIDATE_HEADERS.index("名稱")
    assert table["text_colors"][(2, name_col)] == "#0000CC"


def test_export_candidate_list_writes_to_fixed_worksheet_title(monkeypatch):
    monkeypatch.setattr(watchlist_export.chart_data, "apply_candidate_filters", lambda conn, df, *a, **k: df)
    main_conn = _main_conn()
    _seed_stock(main_conn, "2330", "台積電", 100.0)
    upsert_daily_candidates(main_conn, [
        {"date": "2026-08-03", "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
         "entry_price": 100.0, "stop_loss": 95.0, "note": None, "created_at": "2026-08-03T18:00:00"},
    ])

    captured = {}

    def _fake_write(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(watchlist_export.google_sheets_client, "write_formatted_table", _fake_write)

    result = watchlist_export.export_candidate_list(main_conn, spreadsheet_id="fake-sheet-id", interactive=False)

    assert result is True
    assert captured["worksheet_title"] == "選股清單"
    assert captured["spreadsheet_id"] == "fake-sheet-id"
    assert captured["interactive"] is False


def test_export_candidate_list_returns_false_when_no_candidates(monkeypatch):
    main_conn = _main_conn()
    monkeypatch.setattr(watchlist_export.google_sheets_client, "write_formatted_table", lambda **kwargs: None)

    result = watchlist_export.export_candidate_list(main_conn, spreadsheet_id="fake-sheet-id", interactive=False)

    assert result is False
