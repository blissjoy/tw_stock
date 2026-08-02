"""庫存清單／觀察清單的查詢＋合併邏輯：把src/data/portfolio_storage.py(獨立DB，只存
使用者自己的持股/觀察名單)跟主DB(stocks/stock_prices/daily_indicators，市場資料)
的資料合併成畫面要顯示的DataFrame，並算出市值/損益/報酬率等衍生欄位。

2026-08-02新增。跟chart_data.py職責分工一致：這裡只負責「查詢+合併+算衍生欄位」，
寫入操作(新增/編輯/刪除庫存或觀察清單股票)留在portfolio_storage.py。

⚠️ 這裡刻意不用SQLite的`ATTACH DATABASE`把兩個DB合併成一次SQL查完(ref-project的
做法)——本專案的主DB連線可能是本機sqlite、也可能是Turso(遠端，見src/data/
connection.py)，ATTACH語法對Turso連線無效。改成兩條連線各自查、在Python/pandas
端用stock_id合併，跟load_industry_rotation()等函式已經在用的「多次查詢+pandas
合併」風格一致，不是新模式。
"""

from __future__ import annotations

import pandas as pd

from src.data import portfolio_storage


def _load_price_and_sar_snapshot(main_conn, stock_ids: list[str]) -> pd.DataFrame:
    """查主DB裡每檔股票「各自最新一天」的名稱/收盤價/漲跌幅/SAR資料，供庫存清單／
    觀察清單顯示「現價」用。跟chart_data.load_stock_universe_for_date()不同的是，
    這裡不是「同一個target_date」查一批股票，而是每檔股票各自的最新一天(用MAX(date)
    子查詢)——庫存/觀察清單的股票不見得都在同一天有資料(例如某檔剛下市或資料還沒
    回補)。查無資料的股票不會出現在回傳的DataFrame裡，呼叫端(load_inventory()/
    load_watchlist())用pandas merge(how="left")保留這些股票、缺值欄位為NaN。
    """
    empty_columns = ["stock_id", "name", "close", "prev_close", "pct_change", "sar_value", "sar_status", "sar_distance_pct"]
    if not stock_ids:
        return pd.DataFrame(columns=empty_columns)
    placeholders = ",".join("?" * len(stock_ids))
    cur = main_conn.execute(
        f"""
        SELECT s.stock_id, s.name, sp.close AS today_close,
               (SELECT sp2.close FROM stock_prices sp2
                WHERE sp2.stock_id = s.stock_id AND sp2.date < sp.date
                ORDER BY sp2.date DESC LIMIT 1) AS prev_close,
               di.sar_value AS sar_value, di.sar_is_bull AS sar_is_bull
        FROM stocks s
        JOIN stock_prices sp ON sp.stock_id = s.stock_id
            AND sp.date = (SELECT MAX(sp3.date) FROM stock_prices sp3 WHERE sp3.stock_id = s.stock_id)
        LEFT JOIN daily_indicators di ON di.stock_id = s.stock_id AND di.date = sp.date
        WHERE s.stock_id IN ({placeholders})
        """,
        stock_ids,
    )
    columns = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=columns)
    if df.empty:
        return pd.DataFrame(columns=empty_columns)

    df["pct_change"] = (df["today_close"] - df["prev_close"]) / df["prev_close"] * 100
    df["sar_status"] = df["sar_is_bull"].map(lambda v: ("多頭" if v else "空頭") if pd.notna(v) else None)
    df["sar_distance_pct"] = (df["sar_value"] - df["today_close"]) / df["today_close"] * 100
    return df.rename(columns={"today_close": "close"})[empty_columns]


_DERIVED_COLUMNS = [
    "close", "pct_change", "market_value", "profit", "return_pct", "today_change_value",
    "sar_value", "sar_status", "sar_distance_pct",
]


def _merge_holdings_with_snapshot(holdings_df: pd.DataFrame, main_conn, extra_columns: list[str] = ()) -> pd.DataFrame:
    """holdings_df需含stock_id/cost_price/shares/note欄位(來自inventory_stocks或
    watchlist_stocks)，合併主DB的價格/SAR快照後算市值/帳面損益/報酬率/今日資產變動。
    成本價或持股數缺值時，依賴這兩者的衍生欄位是None，不crash——使用者本來就可以
    只記錄「我在追蹤這檔股票」而不填成本價/股數。

    extra_columns：holdings_df裡除了stock_id/cost_price/shares/note之外，呼叫端
    還想保留的欄位(例如庫存明細的id/buy_date)，會原樣帶到回傳結果裡、排在
    stock_id/name後面——觀察清單/庫存彙總不需要這些，傳空list即可。
    """
    output_columns = ["stock_id", "name", *extra_columns, "cost_price", "shares", "note", *_DERIVED_COLUMNS]
    if holdings_df.empty:
        return pd.DataFrame(columns=output_columns)
    snapshot_df = _load_price_and_sar_snapshot(main_conn, holdings_df["stock_id"].tolist())
    merged = holdings_df.merge(snapshot_df, on="stock_id", how="left")

    has_price_and_shares = merged["close"].notna() & merged["shares"].notna()
    merged["market_value"] = merged["close"] * merged["shares"]
    merged.loc[~has_price_and_shares, "market_value"] = None

    has_cost_price_and_shares = merged["cost_price"].notna() & merged["shares"].notna() & merged["close"].notna()
    merged["profit"] = (merged["close"] - merged["cost_price"]) * merged["shares"]
    merged.loc[~has_cost_price_and_shares, "profit"] = None

    has_cost_price = merged["cost_price"].notna() & merged["close"].notna() & (merged["cost_price"] != 0)
    merged["return_pct"] = (merged["close"] - merged["cost_price"]) / merged["cost_price"] * 100
    merged.loc[~has_cost_price, "return_pct"] = None

    # 今日資產變動：跟昨收比，不是跟成本價比——反映「今天這個資產部位漲跌了多少」，
    # 不是累積損益(那是profit欄位的職責)。
    has_prev_close_and_shares = merged["prev_close"].notna() & merged["shares"].notna() & merged["close"].notna()
    merged["today_change_value"] = (merged["close"] - merged["prev_close"]) * merged["shares"]
    merged.loc[~has_prev_close_and_shares, "today_change_value"] = None

    return merged[output_columns]


def load_inventory_lots(main_conn, portfolio_conn) -> pd.DataFrame:
    """回傳庫存清單「明細」DataFrame：每筆批次(lot)各自一列，含id/buy_date，
    依stock_id/buy_date排序——使用者可能分批買入同一檔股票，每批各自的成本價/
    股數/買入日期都要能獨立編輯，不能合併成一列。市值/帳面損益/報酬率是「這一批」
    自己的數字(用這批自己的cost_price/shares算)，不是整檔股票的加總，整檔股票的
    彙總數字見load_inventory_summary()。
    """
    rows = portfolio_storage.list_inventory_rows(portfolio_conn)
    holdings_df = pd.DataFrame(rows, columns=["id", "stock_id", "buy_date", "cost_price", "shares", "note"])
    return _merge_holdings_with_snapshot(holdings_df, main_conn, extra_columns=["id", "buy_date"])


def load_inventory_summary(main_conn, portfolio_conn) -> pd.DataFrame:
    """回傳庫存清單「依股票彙總」DataFrame：同一檔股票的所有批次合併成一列，
    成本價用加權平均(只採計cost_price跟shares都有填的批次，避免只填了其中一項的
    批次把平均拉偏)、持股數是所有批次(shares有填的)加總、lot_count是這檔股票
    有幾筆批次紀錄——供使用者快速看「這檔股票整體」的損益，不用自己心算好幾批
    加權平均。這個檢視是唯讀的衍生資料，沒有單一lot id可以編輯/刪除，UI要編輯/
    刪除特定批次得切到明細檢視(load_inventory_lots())操作。
    """
    lots_df = load_inventory_lots(main_conn, portfolio_conn)
    if lots_df.empty:
        return pd.DataFrame(
            columns=["stock_id", "name", "cost_price", "shares", "lot_count", *_DERIVED_COLUMNS],
        )

    def _aggregate(group: pd.DataFrame) -> pd.Series:
        priced = group[group["cost_price"].notna() & group["shares"].notna()]
        total_shares = group["shares"].sum() if group["shares"].notna().any() else None
        avg_cost_price = (
            (priced["cost_price"] * priced["shares"]).sum() / priced["shares"].sum()
            if not priced.empty and priced["shares"].sum() else None
        )
        return pd.Series({
            "name": group["name"].iloc[0],
            "cost_price": avg_cost_price,
            "shares": total_shares,
            "lot_count": len(group),
        })

    aggregated = lots_df.groupby("stock_id", as_index=False).apply(_aggregate, include_groups=False)
    holdings_df = aggregated[["stock_id", "cost_price", "shares"]].copy()
    holdings_df["note"] = ""
    merged = _merge_holdings_with_snapshot(holdings_df, main_conn)
    return merged.merge(aggregated[["stock_id", "lot_count"]], on="stock_id").drop(columns="note")[
        ["stock_id", "name", "cost_price", "shares", "lot_count", *_DERIVED_COLUMNS]
    ]


def load_watchlist(main_conn, portfolio_conn, group_id: int) -> pd.DataFrame:
    """回傳指定觀察清單群組的DataFrame(含現價/損益/報酬率等衍生欄位)，依stock_id排序。"""
    rows = portfolio_storage.list_watchlist_rows(portfolio_conn, group_id)
    holdings_df = pd.DataFrame(rows, columns=["stock_id", "cost_price", "shares", "note"])
    return _merge_holdings_with_snapshot(holdings_df, main_conn)
