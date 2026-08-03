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

# 台股手續費：政府公告(牌告)上限費率0.1425%(買賣雙邊都收)，實際收取金額是券商
# 自訂折扣後的結果，不是政府規定的固定金額——折數依券商/帳戶等級而不同，沒有
# 統一答案。這裡的折扣依使用者實際持有的鴻海庫存反推校準：3筆分批買入
# (9,975/12,475/27,060元)，用0.1425%×30%(3折)=0.04275%逐筆算、各自四捨五入到
# 整數元再加總得到21元——這個折扣同時也吻合使用者拿目前市值(現價×總股數)算出的
# 「以現價賣出」預估手續費21元(見estimate_sell_cost())，買賣雙邊套用同一個折扣，
# 因此採用3折當預設值。之後如果實際折扣不同，改BROKER_COMMISSION_DISCOUNT這個
# 常數即可，不需要使用者自己手動輸入每一筆的手續費(2026-08-02改版：原本讓使用者
# 自己填實際金額，但使用者反映庫存/證券app本來就內含這筆費用，新增庫存時不應該
# 還要自己查來填，改成系統自動估算)。
#
# ⚠️ 2026-08-02第二次修正：第一版誤把使用者提供的21元當成「3筆買進手續費加總」
# 反推校準，使用者糾正：21元是「以目前現價賣出」的預估手續費，不是買入手續費。
# 這是兩個不同的數字——買進手續費(estimate_buy_fee())是實際已經付出的錢，計入
# 成本基礎；賣出手續費是「如果現在賣出」的假設性支出，連同賣出才會課徵的證券
# 交易稅(TWSE_TRANSACTION_TAX_RATE)一起，是計算「帳面損益/報酬率」(這個功能
# 本來就是用現價模擬「現在賣掉」的損益)時要從市值扣除的部分，見
# estimate_sell_cost()跟_merge_holdings_with_snapshot()。
TWSE_MAX_COMMISSION_RATE = 0.001425  # 政府公告(牌告)手續費上限0.1425%，買賣雙邊都收
TWSE_TRANSACTION_TAX_RATE = 0.003  # 證券交易稅0.3%，政府規定只課賣方，不因券商折扣而變動
BROKER_COMMISSION_DISCOUNT = 0.3  # 3折，依使用者實際數字反推校準，買賣雙邊套用同一折扣
MIN_COMMISSION_FEE = 1  # 多數數位券商最低手續費約1元


def estimate_buy_fee(cost_price: float | None, shares: int | None) -> int | None:
    """估算買進手續費(四捨五入到整數元)，計入成本基礎——這是實際買進時真正付出的
    錢，不是`estimate_sell_cost()`那種「如果現在賣出」的假設性金額。成本價或股數
    缺值時無從估算，回傳None。"""
    if cost_price is None or shares is None or pd.isna(cost_price) or pd.isna(shares):
        return None
    raw_fee = cost_price * shares * TWSE_MAX_COMMISSION_RATE * BROKER_COMMISSION_DISCOUNT
    return max(round(raw_fee), MIN_COMMISSION_FEE)


def estimate_sell_cost(market_value: float | None) -> int | None:
    """估算「以目前市值賣出」要付出的總成本(手續費+證券交易稅，四捨五入到整數元
    後加總)——`_merge_holdings_with_snapshot()`算「帳面損益/報酬率」時，本來就是
    用目前現價模擬「現在賣掉這個部位」的結果，因此除了買進時已經付出的手續費
    (成本基礎的一部分)，也要扣掉賣出當下才會發生的手續費、以及只課賣方的0.3%
    證券交易稅，帳面損益才會跟券商app顯示的「預估賣出損益」一致。市值缺值或
    小於等於0時回傳None/0，不強行估算一個沒有意義的金額。
    """
    if market_value is None or pd.isna(market_value):
        return None
    if market_value <= 0:
        return 0
    commission = max(round(market_value * TWSE_MAX_COMMISSION_RATE * BROKER_COMMISSION_DISCOUNT), MIN_COMMISSION_FEE)
    tax = round(market_value * TWSE_TRANSACTION_TAX_RATE)
    return commission + tax


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
    "close", "pct_change", "market_value", "sell_fee", "profit", "return_pct", "today_change_value",
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

    2026-08-02新增：holdings_df如果有`fee`欄位(買進手續費，庫存清單才有這個概念，
    觀察清單沒有)，會計入成本基礎——`total_cost = cost_price*shares + fee`。
    holdings_df沒有`fee`欄位時(觀察清單的呼叫)視為0，不影響觀察清單既有行為/數字。

    ⚠️ 2026-08-02第二次修正：「帳面損益/報酬率」本來就是用目前現價模擬「現在把
    這個部位賣掉」的結果(不是「已實現」的損益)，所以除了買進成本(cost_price*
    shares+買進手續費)，也要扣掉「現在賣出」當下才會發生的成本——賣出手續費
    (跟買進手續費一樣的券商折扣，但用目前市值算，見estimate_sell_cost())、以及
    只課賣方的0.3%證券交易稅。第一版只把買進手續費計入成本基礎、完全沒有處理
    賣出端的成本，使用者實測發現算出來的損益/報酬率跟券商app顯示的「預估賣出
    損益」對不上，才發現這個缺口：
        total_cost(買進成本) = cost_price*shares + buy_fee
        sell_fee(賣出成本，手續費+證交稅) = estimate_sell_cost(market_value)
        net_proceeds(賣出淨得) = market_value - sell_fee
        profit = net_proceeds - total_cost
        return_pct = profit / total_cost * 100
    ⚠️ 因為手續費/證交稅都是「一筆金額」不是「每股金額」，要換算進報酬率必須
    知道總股數，所以return_pct的計算條件是「cost_price+shares+close都齊全」
    才會算，跟profit的條件對齊：只填成本價沒填股數時，沒辦法把費用攤進報酬率，
    顯示一個沒扣費用、跟其他有填股數的列基準不一致的數字反而更容易誤導。

    買進手續費是系統自動估算(見estimate_buy_fee())、寫進DB時就已經算好的數字
    (見desktop/main_window.py的_StockEditDialog)，正常情況下這裡讀到的fee欄位
    應該都已經有值；如果是None(例如這個功能上線前就存在的舊資料，DB裡沒有這個
    欄位的值)，這裡用cost_price/shares當場補估一次，不需要使用者手動回去逐筆
    編輯才能看到正確的損益。賣出手續費+證交稅則本來就不存進DB(每天現價都在變、
    是即時模擬出來的假設性支出，不是已經發生的歷史紀錄)，每次呼叫都用目前
    market_value現算，見output_columns裡的`sell_fee`欄位。
    """
    output_columns = ["stock_id", "name", *extra_columns, "cost_price", "shares", "fee", "note", *_DERIVED_COLUMNS]
    if holdings_df.empty:
        return pd.DataFrame(columns=output_columns)
    snapshot_df = _load_price_and_sar_snapshot(main_conn, holdings_df["stock_id"].tolist())
    merged = holdings_df.merge(snapshot_df, on="stock_id", how="left")
    if "fee" not in merged.columns:
        # 觀察清單沒有fee概念，不套用買進手續費估算，固定視為0。
        merged["fee"] = 0.0
        fee = merged["fee"]
    else:
        merged["fee"] = merged.apply(
            lambda r: r["fee"] if pd.notna(r["fee"]) else estimate_buy_fee(r["cost_price"], r["shares"]),
            axis=1,
        )
        fee = merged["fee"].fillna(0)

    has_price_and_shares = merged["close"].notna() & merged["shares"].notna()
    merged["market_value"] = merged["close"] * merged["shares"]
    merged.loc[~has_price_and_shares, "market_value"] = None
    merged["sell_fee"] = merged["market_value"].apply(estimate_sell_cost)

    has_cost_basis = merged["cost_price"].notna() & merged["shares"].notna() & merged["close"].notna()
    total_cost = merged["cost_price"] * merged["shares"] + fee
    net_proceeds = merged["market_value"].fillna(0) - merged["sell_fee"].fillna(0)
    merged["profit"] = net_proceeds - total_cost
    merged.loc[~has_cost_basis, "profit"] = None

    merged["return_pct"] = merged["profit"] / total_cost * 100
    merged.loc[~has_cost_basis | (total_cost == 0), "return_pct"] = None

    # 今日資產變動：跟昨收比，不是跟成本價比——反映「今天這個資產部位漲跌了多少」，
    # 不是累積損益(那是profit欄位的職責)。
    has_prev_close_and_shares = merged["prev_close"].notna() & merged["shares"].notna() & merged["close"].notna()
    merged["today_change_value"] = (merged["close"] - merged["prev_close"]) * merged["shares"]
    merged.loc[~has_prev_close_and_shares, "today_change_value"] = None

    return merged[output_columns]


def load_inventory_lots(main_conn, portfolio_conn) -> pd.DataFrame:
    """回傳庫存清單「明細」DataFrame：每筆批次(lot)各自一列，含id/buy_date，
    依stock_id/buy_date排序——使用者可能分批買入同一檔股票，每批各自的成本價/
    股數/手續費/買入日期都要能獨立編輯，不能合併成一列。市值/帳面損益/報酬率是
    「這一批」自己的數字(用這批自己的cost_price/shares/fee算)，不是整檔股票的
    加總，整檔股票的彙總數字見load_inventory_summary()。
    """
    rows = portfolio_storage.list_inventory_rows(portfolio_conn)
    holdings_df = pd.DataFrame(rows, columns=["id", "stock_id", "buy_date", "cost_price", "shares", "fee", "note"])
    return _merge_holdings_with_snapshot(holdings_df, main_conn, extra_columns=["id", "buy_date"])


def load_inventory_summary(main_conn, portfolio_conn) -> pd.DataFrame:
    """回傳庫存清單「依股票彙總」DataFrame：同一檔股票的所有批次合併成一列，
    成本價用加權平均(只採計cost_price跟shares都有填的批次，避免只填了其中一項的
    批次把平均拉偏)、持股數是所有批次(shares有填的)加總、手續費是所有批次加總
    (不論該批cost_price/shares是否有填，手續費本身是使用者實際付出的錢，能加就
    加)、lot_count是這檔股票有幾筆批次紀錄——供使用者快速看「這檔股票整體」的
    損益，不用自己心算好幾批加權平均。`avg_cost_price * total_shares +
    total_fee`在數學上等於`sum(cost_price_i * shares_i + fee_i)`，加權平均
    沒有失真。這個檢視是唯讀的衍生資料，沒有單一lot id可以編輯/刪除，UI要編輯/
    刪除特定批次得展開明細(load_inventory_lots())操作。
    """
    lots_df = load_inventory_lots(main_conn, portfolio_conn)
    if lots_df.empty:
        return pd.DataFrame(
            columns=["stock_id", "name", "cost_price", "shares", "fee", "lot_count", *_DERIVED_COLUMNS],
        )

    def _aggregate(group: pd.DataFrame) -> pd.Series:
        priced = group[group["cost_price"].notna() & group["shares"].notna()]
        total_shares = group["shares"].sum() if group["shares"].notna().any() else None
        avg_cost_price = (
            (priced["cost_price"] * priced["shares"]).sum() / priced["shares"].sum()
            if not priced.empty and priced["shares"].sum() else None
        )
        total_fee = group["fee"].sum() if group["fee"].notna().any() else None
        return pd.Series({
            "name": group["name"].iloc[0],
            "cost_price": avg_cost_price,
            "shares": total_shares,
            "fee": total_fee,
            "lot_count": len(group),
        })

    aggregated = lots_df.groupby("stock_id", as_index=False).apply(_aggregate, include_groups=False)
    holdings_df = aggregated[["stock_id", "cost_price", "shares", "fee"]].copy()
    holdings_df["note"] = ""
    merged = _merge_holdings_with_snapshot(holdings_df, main_conn)
    return merged.merge(aggregated[["stock_id", "lot_count"]], on="stock_id").drop(columns="note")[
        ["stock_id", "name", "cost_price", "shares", "fee", "lot_count", *_DERIVED_COLUMNS]
    ]


def load_watchlist(main_conn, portfolio_conn, group_id: int) -> pd.DataFrame:
    """回傳指定觀察清單群組的DataFrame(含現價/損益/報酬率等衍生欄位)，依stock_id排序。"""
    rows = portfolio_storage.list_watchlist_rows(portfolio_conn, group_id)
    holdings_df = pd.DataFrame(rows, columns=["stock_id", "cost_price", "shares", "note"])
    return _merge_holdings_with_snapshot(holdings_df, main_conn)
