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
            "ma5": None, "ma10": None, "ma20": None, "ma60": None, "ma120": None, "ma240": None,
            "sar_value": indicator["sar_value"], "sar_is_bull": indicator["sar_is_bull"],
            "sar_flip_days_ago": 1, "updated_at": "2026-08-02T00:00:00",
        }])


def test_load_inventory_lots_empty_when_no_holdings():
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()

    df = portfolio_data.load_inventory_lots(main_conn, portfolio_conn)

    assert df.empty
    assert list(df.columns) == [
        "stock_id", "name", "id", "buy_date", "cost_price", "shares", "note",
        "close", "pct_change", "market_value", "profit", "return_pct", "today_change_value",
        "sar_value", "sar_status", "sar_distance_pct",
    ]


def test_load_inventory_lots_computes_market_value_profit_and_return_pct():
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
    assert row["stock_id"] == "2330"
    assert row["name"] == "台積電"
    assert row["buy_date"] == "2026-07-01"
    assert row["close"] == 910.0
    assert math.isclose(row["pct_change"], (910.0 - 900.0) / 900.0 * 100)
    assert row["market_value"] == 910.0 * 1000
    assert row["profit"] == (910.0 - 850.0) * 1000
    assert math.isclose(row["return_pct"], (910.0 - 850.0) / 850.0 * 100)
    assert row["today_change_value"] == (910.0 - 900.0) * 1000
    assert row["sar_value"] == 880.0
    assert row["sar_status"] == "多頭"
    assert math.isclose(row["sar_distance_pct"], (880.0 - 910.0) / 910.0 * 100)


def test_load_inventory_lots_derived_fields_are_none_when_cost_price_or_shares_missing():
    main_conn = _main_conn()
    portfolio_conn = _portfolio_conn()
    _seed_stock(main_conn, "2330", "台積電", [{"date": "2026-07-31", "close": 910.0}])
    portfolio_storage.add_inventory_stock(portfolio_conn, "2330", cost_price=None, shares=None, note="還沒決定成本")

    df = portfolio_data.load_inventory_lots(main_conn, portfolio_conn)

    row = df.iloc[0]
    assert row["close"] == 910.0
    assert pd.isna(row["market_value"])
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
        "stock_id", "name", "cost_price", "shares", "lot_count",
        "close", "pct_change", "market_value", "profit", "return_pct", "today_change_value",
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
