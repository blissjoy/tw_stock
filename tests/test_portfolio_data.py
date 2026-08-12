import math

import pandas as pd

from src.data import portfolio_storage
from src.data.storage import init_db, upsert_daily_indicators, upsert_stock_prices, upsert_stocks
from src.presentation import portfolio_data


def _main_conn():
    return init_db(":memory:")


def _portfolio_conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    portfolio_storage.ensure_portfolio_schema(conn)
    return conn


def _seed_stock(main_conn, stock_id: str, name: str, prices: list[dict], indicator: dict | None = None) -> None:
    upsert_stocks(main_conn, [{"stock_id": stock_id, "name": name, "market": "TWSE", "industry": "測試業", "updated_at": "2026-08-02T00:00:00"}])
    upsert_stock_prices(main_conn, [
        {
            "stock_id": stock_id, "date": p["date"], "open": p["close"], "high": p["close"], "low": p["close"],
            "close": p["close"], "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None,
        }
        for p in prices
    ])
    if indicator is not None:
        upsert_daily_indicators(main_conn, [{
            "stock_id": stock_id, "date": prices[-1]["date"],
            "ma5": None, "ma10": None, "ma20": None, "ma60": None, "ma120": None, "ma200": None, "ma240": None,
            "sar_value": indicator["sar_value"], "sar_is_bull": indicator["sar_is_bull"],
            "sar_flip_days_ago": 1, "trend_is_at_high": None, "trend_is_at_low": None, "trend_swing_pct": None,
            "updated_at": "2026-08-02T00:00:00",
        }])


def test_load_inventory_lots_empty_when_no_holdings():
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()

    df = portfolio_data.load_inventory_lots(main_conn, portfolio_conn)

    assert df.empty
    assert list(df.columns) == [
        "stock_id", "name", "listing_type", "id", "buy_date", "cost_price", "shares", "fee", "note",
        "close", "pct_change", "market_value", "sell_fee", "profit", "return_pct", "today_change_value",
        "sar_value", "sar_status", "sar_distance_pct",
    ]


def test_estimate_buy_fee_matches_users_real_hon_hai_holding():
    """反推校準的基準案例：使用者實際持有的鴻海庫存分3批買入(9,975/12,475/
    27,060元)，券商app顯示合計手續費21元——逐筆估算後加總應該精確吻合，這是
    BROKER_COMMISSION_DISCOUNT(3折)這個常數的校準依據，不能只是「差不多」。
    """
    assert portfolio_data.estimate_buy_fee(199.5, 50) == 4
    assert portfolio_data.estimate_buy_fee(249.5, 50) == 5
    assert portfolio_data.estimate_buy_fee(246.0, 110) == 12


def test_estimate_buy_fee_returns_none_when_cost_price_or_shares_missing():
    assert portfolio_data.estimate_buy_fee(None, 100) is None
    assert portfolio_data.estimate_buy_fee(100.0, None) is None


def test_estimate_buy_fee_applies_minimum_fee_floor():
    """交易金額很小時，估算出來的手續費不能低於最低手續費門檻(多數數位券商
    約1元)。"""
    assert portfolio_data.estimate_buy_fee(1.0, 1) == portfolio_data.MIN_COMMISSION_FEE


def test_estimate_sell_cost_combines_commission_and_transaction_tax():
    """使用者糾正：「21元」是券商app顯示的『以目前現價賣出』預估手續費，不是買入
    手續費加總——賣出手續費用跟買進同一個折扣(3折)，但另外要加計只課賣方、不能
    打折的0.3%證券交易稅，這兩筆都是「如果現在賣出」才會發生的成本。這裡用使用者
    實際持有的鴻海庫存範例(現價250.5×總股數210=市值52,605)驗證精確金額，不能只是
    「差不多」：手續費=round(52605×0.001425×0.3)=22，證交稅=round(52605×0.003)=158，
    合計180。
    """
    assert portfolio_data.estimate_sell_cost(52605.0) == 180


def test_estimate_sell_cost_returns_none_when_market_value_missing():
    assert portfolio_data.estimate_sell_cost(None) is None
    assert portfolio_data.estimate_sell_cost(float("nan")) is None


def test_estimate_sell_cost_returns_zero_for_non_positive_market_value():
    assert portfolio_data.estimate_sell_cost(0) == 0


def test_estimate_sell_cost_applies_minimum_commission_floor():
    """交易金額很小時，賣出手續費(不含證交稅那部分)一樣不能低於最低手續費門檻。"""
    tiny_value = 1.0
    tax = round(tiny_value * portfolio_data.TWSE_TRANSACTION_TAX_RATE)
    assert portfolio_data.estimate_sell_cost(tiny_value) == portfolio_data.MIN_COMMISSION_FEE + tax


def test_load_inventory_lots_auto_estimates_fee_when_not_stored():
    """使用者新增庫存時不用自己填手續費，系統依成本價×股數自動估算並計入損益——
    這是2026-08-02第二次改版的核心需求：手續費「已經是券商app內含」，畫面上
    不需要使用者手動輸入，但預估損益一定要納入。
    """
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(
        main_conn, "2330", "台積電",
        [{"date": "2026-07-30", "close": 900.0}, {"date": "2026-07-31", "close": 910.0}],
        indicator={"sar_value": 880.0, "sar_is_bull": 1},
    )
    portfolio_storage.add_inventory_stock(portfolio_conn, "2330", buy_date="2026-07-01", cost_price=850.0, shares=1000, note="核心持股")

    df = portfolio_data.load_inventory_lots(main_conn, portfolio_conn)

    assert len(df) == 1
    row = df.iloc[0]
    expected_fee = portfolio_data.estimate_buy_fee(850.0, 1000)
    assert row["fee"] == expected_fee
    assert row["stock_id"] == "2330"
    assert row["name"] == "台積電"
    assert row["buy_date"] == "2026-07-01"
    assert row["close"] == 910.0
    assert math.isclose(row["pct_change"], (910.0 - 900.0) / 900.0 * 100)
    assert row["market_value"] == 910.0 * 1000
    expected_sell_fee = portfolio_data.estimate_sell_cost(910.0 * 1000)
    assert row["sell_fee"] == expected_sell_fee
    total_cost = 850.0 * 1000 + expected_fee
    net_proceeds = 910.0 * 1000 - expected_sell_fee
    assert row["profit"] == net_proceeds - total_cost
    assert math.isclose(row["return_pct"], (net_proceeds - total_cost) / total_cost * 100)
    assert row["today_change_value"] == (910.0 - 900.0) * 1000
    assert row["sar_value"] == 880.0
    assert row["sar_status"] == "多頭"
    assert math.isclose(row["sar_distance_pct"], (880.0 - 910.0) / 910.0 * 100)


def test_load_inventory_lots_profit_and_return_pct_deduct_fee():
    """使用者反映帳面損益/報酬率要扣除手續費才會跟證券app一致——買進手續費是
    一筆固定金額，成本基礎是cost_price*shares+fee，不是單純cost_price*shares；
    另外還要扣掉「如果現在賣出」才會發生的賣出手續費+證交稅(estimate_sell_
    cost())，帳面損益才是「現在賣掉能拿到多少淨額」的正確模擬。這裡明確傳入
    fee(模擬已經存好的舊資料)，確認不會被自動估算覆蓋掉既有的值。
    """
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", [{"date": "2026-07-31", "close": 910.0}])
    portfolio_storage.add_inventory_stock(portfolio_conn, "2330", cost_price=850.0, shares=1000, fee=21.0, note="")

    df = portfolio_data.load_inventory_lots(main_conn, portfolio_conn)

    row = df.iloc[0]
    assert row["fee"] == 21.0
    expected_sell_fee = portfolio_data.estimate_sell_cost(910.0 * 1000)
    assert row["sell_fee"] == expected_sell_fee
    total_cost = 850.0 * 1000 + 21.0
    net_proceeds = 910.0 * 1000 - expected_sell_fee
    assert row["profit"] == net_proceeds - total_cost
    assert math.isclose(row["return_pct"], (net_proceeds - total_cost) / total_cost * 100)


def test_load_inventory_lots_derived_fields_are_none_when_cost_price_or_shares_missing():
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", [{"date": "2026-07-31", "close": 910.0}])
    portfolio_storage.add_inventory_stock(portfolio_conn, "2330", cost_price=None, shares=None, note="還沒決定成本")

    df = portfolio_data.load_inventory_lots(main_conn, portfolio_conn)

    row = df.iloc[0]
    assert row["close"] == 910.0
    assert pd.isna(row["market_value"])
    assert pd.isna(row["sell_fee"])
    assert pd.isna(row["profit"])
    assert pd.isna(row["return_pct"])
    assert pd.isna(row["today_change_value"])


def test_load_inventory_lots_stock_not_in_main_db_shows_none_price():
    """使用者可能把還沒被本系統資料庫追蹤的股票加進庫存清單(例如剛上市)，這種
    情況price/SAR相關欄位應該是None，不是整列消失或crash。"""
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    portfolio_storage.add_inventory_stock(portfolio_conn, "9999", cost_price=100.0, shares=100, note="")

    df = portfolio_data.load_inventory_lots(main_conn, portfolio_conn)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["stock_id"] == "9999"
    assert pd.isna(row["close"])
    assert pd.isna(row["market_value"])


def test_load_inventory_summary_empty_when_no_holdings():
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()

    df = portfolio_data.load_inventory_summary(main_conn, portfolio_conn)

    assert df.empty
    assert list(df.columns) == [
        "stock_id", "name", "listing_type", "cost_price", "shares", "fee", "lot_count",
        "close", "pct_change", "market_value", "sell_fee", "profit", "return_pct", "today_change_value",
        "sar_value", "sar_status", "sar_distance_pct",
    ]


def test_load_inventory_summary_computes_weighted_average_cost_price():
    """分兩批買入同一檔股票：第一批800元/1000股，第二批850元/500股，彙總後平均
    成本價應該是加權平均(800*1000+850*500)/1500，不是簡單平均(800+850)/2。"""
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", [{"date": "2026-07-31", "close": 910.0}])
    portfolio_storage.add_inventory_stock(portfolio_conn, "2330", buy_date="2026-07-01", cost_price=800.0, shares=1000, note="第一批")
    portfolio_storage.add_inventory_stock(portfolio_conn, "2330", buy_date="2026-07-20", cost_price=850.0, shares=500, note="第二批")

    df = portfolio_data.load_inventory_summary(main_conn, portfolio_conn)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["stock_id"] == "2330"
    assert row["shares"] == 1500
    assert math.isclose(row["cost_price"], (800.0 * 1000 + 850.0 * 500) / 1500)
    assert row["lot_count"] == 2
    assert row["market_value"] == 910.0 * 1500


def test_load_inventory_summary_sums_fee_across_lots_and_deducts_from_profit():
    """彙總損益要包含所有批次的買進手續費加總——avg_cost_price*total_shares+
    total_fee在數學上等於sum(cost_price_i*shares_i+fee_i)，加權平均不會失真；
    另外也要扣掉「以彙總後的總市值現在賣出」的賣出手續費+證交稅，是對整個部位
    算一次(不是逐批分別估算再加總)，因為賣出是針對「這檔股票目前的總持股」
    這個整體部位模擬的。"""
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", [{"date": "2026-07-31", "close": 910.0}])
    portfolio_storage.add_inventory_stock(portfolio_conn, "2330", cost_price=800.0, shares=1000, fee=20.0, note="第一批")
    portfolio_storage.add_inventory_stock(portfolio_conn, "2330", cost_price=850.0, shares=500, fee=15.0, note="第二批")

    df = portfolio_data.load_inventory_summary(main_conn, portfolio_conn)

    row = df.iloc[0]
    assert row["fee"] == 35.0
    expected_sell_fee = portfolio_data.estimate_sell_cost(910.0 * 1500)
    assert row["sell_fee"] == expected_sell_fee
    total_cost = 800.0 * 1000 + 850.0 * 500 + 20.0 + 15.0
    net_proceeds = 910.0 * 1500 - expected_sell_fee
    assert row["profit"] == net_proceeds - total_cost
    assert math.isclose(row["return_pct"], (net_proceeds - total_cost) / total_cost * 100)


def test_load_inventory_summary_ignores_lots_missing_cost_price_or_shares_in_weighted_average():
    """其中一批沒填成本價/股數時，加權平均只用「有填完整」的批次算，不會讓缺值
    批次把平均拉偏或直接crash。"""
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", [{"date": "2026-07-31", "close": 910.0}])
    portfolio_storage.add_inventory_stock(portfolio_conn, "2330", cost_price=800.0, shares=1000, note="第一批")
    portfolio_storage.add_inventory_stock(portfolio_conn, "2330", cost_price=None, shares=None, note="還沒決定")

    df = portfolio_data.load_inventory_summary(main_conn, portfolio_conn)

    row = df.iloc[0]
    assert row["cost_price"] == 800.0
    assert row["shares"] == 1000  # 第二批shares是None，加總時被忽略
    assert row["lot_count"] == 2


def test_load_escape_signals_for_stocks_keeps_only_is_escape_matches_per_stock(monkeypatch):
    """庫存清單畫面要標示哪些持股現在有逃命示警——這裡驗證①每檔股票各自拿到
    自己的訊號清單(不會混到別檔的)②只保留is_escape=True的項目，一般訊號被過濾掉。
    用price_df最後一筆收盤價分辨是哪一檔股票在呼叫analyze_stock_signals()，
    不用真的接規則庫(跟test_daily_screener.py同一套monkeypatch風格)。"""
    main_conn = _main_conn()
    _seed_stock(main_conn, "2330", "台積電", [{"date": "2026-08-01", "close": 100.0}])
    _seed_stock(main_conn, "2454", "聯發科", [{"date": "2026-08-01", "close": 200.0}])

    def fake_analyze(price_df, trend_df=None):
        last_close = price_df["close"].iloc[-1]
        if last_close == 100.0:
            return [
                {"rule_id": "R-ESCAPE-1", "is_escape": True, "date": "2026-08-01"},
                {"rule_id": "R-NORMAL-1", "is_escape": False, "date": "2026-08-01"},
            ]
        return [{"rule_id": "R-NORMAL-2", "is_escape": False, "date": "2026-08-01"}]

    monkeypatch.setattr("src.screener.daily_screener.analyze_stock_signals", fake_analyze)

    result = portfolio_data.load_escape_signals_for_stocks(main_conn, ["2330", "2454"])

    assert [m["rule_id"] for m in result["2330"]] == ["R-ESCAPE-1"]
    assert result["2454"] == []


def test_load_escape_signals_for_stocks_returns_empty_list_when_no_price_data():
    """股票代號查無股價資料(例如已下市或打錯代號)時回傳空清單，不拋例外——
    庫存清單本來就可能收錄查不到最新股價的股票，見load_inventory_lots()同一個
    「不假造資料」原則。"""
    main_conn = _main_conn()

    result = portfolio_data.load_escape_signals_for_stocks(main_conn, ["9999"])

    assert result == {"9999": []}


def test_load_watchlist_returns_only_stocks_in_given_group():
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", [{"date": "2026-07-31", "close": 910.0}])
    _seed_stock(main_conn, "2454", "聯發科", [{"date": "2026-07-31", "close": 1200.0}])
    group_a = portfolio_storage.add_watchlist_group(portfolio_conn, "半導體")
    group_b = portfolio_storage.add_watchlist_group(portfolio_conn, "其他")
    portfolio_storage.add_watchlist_stock(portfolio_conn, group_a, "2330")
    portfolio_storage.add_watchlist_stock(portfolio_conn, group_b, "2454")

    df_a = portfolio_data.load_watchlist(main_conn, portfolio_conn, group_a)
    df_b = portfolio_data.load_watchlist(main_conn, portfolio_conn, group_b)

    assert df_a["stock_id"].tolist() == ["2330"]
    assert df_b["stock_id"].tolist() == ["2454"]


def test_listing_type_color_maps_twse_tpex_emerging_and_defaults_to_black():
    assert portfolio_data.listing_type_color("twse") == "#0000CC"
    assert portfolio_data.listing_type_color("tpex") == "#000000"
    assert portfolio_data.listing_type_color("emerging") == "#555555"
    assert portfolio_data.listing_type_color(None) == "#000000"
    assert portfolio_data.listing_type_color("unknown") == "#000000"


def test_load_watchlist_includes_listing_type():
    """2026-08-04新增：listing_type("twse"/"tpex"/"emerging")供觀察清單依市場別
    顯示不同字體顏色用(藍/黑/灰)，見src/data/finmind_client.py的fetch_stock_
    info()說明。"""
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", [{"date": "2026-07-31", "close": 910.0}])
    upsert_stocks(main_conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "測試業", "listing_type": "twse", "updated_at": "2026-08-02T00:00:00"}])
    group_id = portfolio_storage.add_watchlist_group(portfolio_conn, "半導體")
    portfolio_storage.add_watchlist_stock(portfolio_conn, group_id, "2330")

    df = portfolio_data.load_watchlist(main_conn, portfolio_conn, group_id)

    assert df.iloc[0]["listing_type"] == "twse"


def test_load_watchlist_without_cost_price_has_none_derived_fields_but_has_price():
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", [{"date": "2026-07-31", "close": 910.0}])
    group_id = portfolio_storage.add_watchlist_group(portfolio_conn, "半導體")
    portfolio_storage.add_watchlist_stock(portfolio_conn, group_id, "2330", note="純觀察")

    df = portfolio_data.load_watchlist(main_conn, portfolio_conn, group_id)

    row = df.iloc[0]
    assert row["close"] == 910.0
    assert row["note"] == "純觀察"
    assert pd.isna(row["market_value"])
