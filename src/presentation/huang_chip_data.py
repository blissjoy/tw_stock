"""黃豐凱籌碼分析法的查詢層：把`src/indicators/huang_chip_signals.py`的純計算函式接上
本專案本地DB，組成觀察清單(desktop/main_window.py)要顯示的一列資料。之後若要擴充到
庫存清單／個股資訊，直接重用這裡的函式即可，不用重寫查詢邏輯。

D/E(法人近期籌碼)、H(均線狀態)、I(週K型態)、K~R(法人買賣超張數)完全不需要呼叫任何
API——本專案每日排程本來就已經維護`institutional_investors`/`stock_prices`/
`daily_indicators`這三張表，直接查本地DB即可。只有F/G(大戶/散戶持股週變化)需要
`holder_shares_distribution`表的資料，這張表目前沒有排程自動維護(見
`src/data/finmind_client.py`的`fetch_holding_shares_per()`說明)，查無資料時回傳
None，呼叫端顯示「尚未有資料」，不是crash。

⚠️ 外資只查`investor_type = "Foreign_Investor"`，刻意不併入`Foreign_Dealer_Self`
(跟`src/presentation/stock_detail_data.py`既有的三大法人併計口徑不同)——這是使用者
2026-08-04明確要求「先跟可驗證來源(黃豐凱籌碼分析法程式碼)一致」，之後驗證通過會
回頭詢問是否要改成跟系統既有口徑一致，屆時再改這裡的investor_type查詢條件即可。

2026-08-07新增批次版本(`load_huang_chip_rows_batch()`等`*_batch`函式)：原本觀察清單
逐股呼叫`load_huang_chip_row()`，單股就要9條SQL(含3條重複的「查這檔股票最新交易日」)，
N檔股票=9N條依序執行的SQL。本機sqlite這樣感覺不出來，但web版部署到雲端連的是Turso
(見`src/data/connection.py`)，每條SQL都是一次真實網路來回，N+1模式在雲端會被放大成
好幾秒的延遲(2026-08-07實測：正式站10檔股票的觀察清單群組切換要8.9秒，幾乎沒股票的
群組只要2.7~3.2秒，診斷過程見`ai/PLAN.md`)。批次版本把「9N條逐股SQL」改成固定6條
「WHERE stock_id IN (...)」的批次SQL(不管幾檔股票，SQL條數不變)。原本逐股版本
(`load_institutional_streak_and_flow`等)保留不動、簽章不變(不影響現有呼叫端/測試)，
內部改成呼叫批次版本、只傳一檔股票的包裝寫法，避免同一段查詢邏輯維護兩份。

批次版本的簡化假設：批次查詢一律用「這批股票裡最晚的as_of_date」當SQL的upper bound
(`date <= ?`)，不是每檔股票各自的as_of_date——因為對任一檔股票而言，它自己的資料
「date <= 自己的as_of_date」這件事恆成立(as_of_date定義就是該股票最新的資料日期)，
而「自己的as_of_date」必定 <= 批次最晚日期，所以用批次最晚日期當SQL的upper bound
不會漏掉任何一檔股票原本查得到的資料列——之後在Python裡再依「這檔股票自己的as_of_date」
過濾/截斷，結果跟逐股查詢完全等價。批次SQL刻意不加lower bound(不限制「多久以前」)：
一次抓一批股票的完整歷史雖然列數變多，但用一條SQL傳輸大量小筆資料，仍然遠比對雲端DB
發送N次獨立網路來回快，不需要為了縮減列數再另外設計lower bound(避免額外的邊界案例
風險，例如某檔股票很久沒交易、lower bound抓早了反而漏掉它需要的資料)。
"""

from __future__ import annotations

from src.indicators import huang_chip_signals as signals

# 法人買賣超回看天數：對應原程式碼抓90個曆日(涵蓋約40個交易日)，供D/E連續天數判讀、
# K~R的40/20/10/5日加總共用同一批資料。
INSTITUTIONAL_LOOKBACK_DAYS = 90

# 週K大量型態回看天數：對應原程式碼抓370個曆日——見huang_chip_signals.
# classify_weekly_volume_pattern()docstring的52週精確度說明，這裡照抄原本的抓法。
WEEKLY_PATTERN_LOOKBACK_DAYS = 370


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


def _resolve_as_of_dates(conn, stock_ids: list[str], as_of_date: str | None) -> dict[str, str | None]:
    """批次版：回傳{stock_id: as_of_date}。as_of_date有傳入值時每檔股票套用同一個
    日期；未傳入(None)時一條SQL查出這批股票各自在stock_prices的最新日期，取代逐股
    各查一次(原本`_resolve_as_of_date()`的N+1來源之一，同一檔股票在
    `load_huang_chip_row()`裡還會被3個子函式各自重複呼叫一次)。"""
    if as_of_date is not None:
        return {sid: as_of_date for sid in stock_ids}
    if not stock_ids:
        return {}
    cur = conn.execute(
        f"SELECT stock_id, MAX(date) FROM stock_prices WHERE stock_id IN ({_placeholders(len(stock_ids))}) GROUP BY stock_id",
        stock_ids,
    )
    resolved = dict(cur.fetchall())
    return {sid: resolved.get(sid) for sid in stock_ids}


def _resolve_as_of_date(conn, stock_id: str, as_of_date: str | None) -> str | None:
    return _resolve_as_of_dates(conn, [stock_id], as_of_date).get(stock_id)


def _fetch_institutional_net_desc_batch(
    conn, stock_ids: list[str], investor_type: str, as_of_dates: dict[str, str | None], lookback_days: int,
) -> dict[str, list[float]]:
    """回傳{stock_id: [依日期新到舊排序的(買-賣)淨額(股數), ...]}，每檔股票最多
    lookback_days筆(截斷邏輯見模組docstring的說明)。"""
    result: dict[str, list[float]] = {sid: [] for sid in stock_ids}
    valid_ids = [sid for sid in stock_ids if as_of_dates.get(sid)]
    if not valid_ids:
        return result
    max_date = max(as_of_dates[sid] for sid in valid_ids)
    cur = conn.execute(
        f"""
        SELECT stock_id, date, buy, sell FROM institutional_investors
        WHERE stock_id IN ({_placeholders(len(valid_ids))}) AND investor_type = ? AND date <= ?
        ORDER BY stock_id, date DESC
        """,
        [*valid_ids, investor_type, max_date],
    )
    rows_by_stock: dict[str, list[tuple[str, float, float]]] = {}
    for stock_id, row_date, buy, sell in cur.fetchall():
        rows_by_stock.setdefault(stock_id, []).append((row_date, buy, sell))
    for sid in valid_ids:
        own_date = as_of_dates[sid]
        rows = [r for r in rows_by_stock.get(sid, []) if r[0] <= own_date][:lookback_days]
        result[sid] = [buy - sell for _, buy, sell in rows]
    return result


def _fetch_institutional_net_desc(conn, stock_id: str, investor_type: str, as_of_date: str, lookback_days: int) -> list[float]:
    return _fetch_institutional_net_desc_batch(conn, [stock_id], investor_type, {stock_id: as_of_date}, lookback_days)[stock_id]


def load_institutional_streak_and_flow_batch(
    conn, stock_ids: list[str], as_of_date: str | None = None, _as_of_dates: dict[str, str | None] | None = None,
) -> dict[str, dict]:
    """`load_institutional_streak_and_flow()`的批次版，回傳{stock_id: 該函式原本的
    回傳dict}。`_as_of_dates`是內部參數，供`load_huang_chip_rows_batch()`傳入已經
    批次算好的日期、避免同一批股票的as_of_date被4個子批次函式各自重複查一次。"""
    if not stock_ids:
        return {}
    as_of_dates = _as_of_dates if _as_of_dates is not None else _resolve_as_of_dates(conn, stock_ids, as_of_date)
    invest_nets = _fetch_institutional_net_desc_batch(conn, stock_ids, "Investment_Trust", as_of_dates, INSTITUTIONAL_LOOKBACK_DAYS)
    foreign_nets = _fetch_institutional_net_desc_batch(conn, stock_ids, "Foreign_Investor", as_of_dates, INSTITUTIONAL_LOOKBACK_DAYS)

    result: dict[str, dict] = {}
    for sid in stock_ids:
        invest_net = invest_nets.get(sid, [])
        foreign_net = foreign_nets.get(sid, [])
        flow = None
        if invest_net or foreign_net:
            flow = {
                "foreign_40d": signals.sum_institutional_flow_lots(foreign_net, 40),
                "invest_40d": signals.sum_institutional_flow_lots(invest_net, 40),
                "foreign_20d": signals.sum_institutional_flow_lots(foreign_net, 20),
                "invest_20d": signals.sum_institutional_flow_lots(invest_net, 20),
                "foreign_10d": signals.sum_institutional_flow_lots(foreign_net, 10),
                "invest_10d": signals.sum_institutional_flow_lots(invest_net, 10),
                "foreign_5d": signals.sum_institutional_flow_lots(foreign_net, 5),
                "invest_5d": signals.sum_institutional_flow_lots(invest_net, 5),
            }
        result[sid] = {
            "invest_streak": signals.classify_institutional_streak(invest_net),
            "foreign_streak": signals.classify_institutional_streak(foreign_net),
            "flow": flow,
        }
    return result


def load_institutional_streak_and_flow(conn, stock_id: str, as_of_date: str | None = None) -> dict:
    """組合D/E(投信/外資連續買賣超狀態)＋K~R(40/20/10/5日買賣超張數)。

    回傳{"invest_streak", "foreign_streak", "flow"}，"flow"是{"foreign_40d",
    "invest_40d", "foreign_20d", "invest_20d", "foreign_10d", "invest_10d",
    "foreign_5d", "invest_5d"}或None——對應原程式碼「投信、外資兩份資料都是空的才
    整段跳過不寫」，只要其中一份有資料就照樣算(缺的那份自然加總為0，不是跳過)。
    """
    return load_institutional_streak_and_flow_batch(conn, [stock_id], as_of_date)[stock_id]


def load_ma_price_position_batch(
    conn, stock_ids: list[str], as_of_date: str | None = None, _as_of_dates: dict[str, str | None] | None = None,
) -> dict[str, dict | None]:
    """`load_ma_price_position()`的批次版，回傳{stock_id: 該函式原本的回傳值}。"""
    result: dict[str, dict | None] = {sid: None for sid in stock_ids}
    if not stock_ids:
        return result
    as_of_dates = _as_of_dates if _as_of_dates is not None else _resolve_as_of_dates(conn, stock_ids, as_of_date)
    valid_ids = [sid for sid in stock_ids if as_of_dates.get(sid)]
    if not valid_ids:
        return result

    max_date = max(as_of_dates[sid] for sid in valid_ids)
    cur = conn.execute(
        f"""
        SELECT stock_id, date, ma20, ma60 FROM daily_indicators
        WHERE stock_id IN ({_placeholders(len(valid_ids))}) AND date <= ?
        ORDER BY stock_id, date DESC
        """,
        [*valid_ids, max_date],
    )
    rows_by_stock: dict[str, list[tuple[str, float, float]]] = {}
    for stock_id, row_date, ma20, ma60 in cur.fetchall():
        rows_by_stock.setdefault(stock_id, []).append((row_date, ma20, ma60))

    # 每檔股票只需要「今天」那一列的open/close，不是完整價格史——先算出每檔股票的
    # today_date，再用這批today_date的最小值當lower bound，避免抓進不需要的歷史價格。
    today_date_by_stock: dict[str, str] = {}
    trimmed_rows_by_stock: dict[str, list[tuple[str, float, float]]] = {}
    for sid in valid_ids:
        own_date = as_of_dates[sid]
        rows = [r for r in rows_by_stock.get(sid, []) if r[0] <= own_date][:2]
        if len(rows) < 2:
            continue
        trimmed_rows_by_stock[sid] = rows
        today_date_by_stock[sid] = rows[0][0]

    if not today_date_by_stock:
        return result

    price_ids = list(today_date_by_stock.keys())
    min_today_date = min(today_date_by_stock.values())
    price_cur = conn.execute(
        f"""
        SELECT stock_id, date, open, close FROM stock_prices
        WHERE stock_id IN ({_placeholders(len(price_ids))}) AND date >= ?
        """,
        [*price_ids, min_today_date],
    )
    price_by_key: dict[tuple[str, str], tuple[float, float]] = {
        (stock_id, row_date): (open_, close_) for stock_id, row_date, open_, close_ in price_cur.fetchall()
    }

    for sid, today_date in today_date_by_stock.items():
        price = price_by_key.get((sid, today_date))
        if price is None:
            continue
        (today_date_, ma20_today, ma60_today), (_, ma20_yesterday, ma60_yesterday) = trimmed_rows_by_stock[sid]
        open_today, close_today = price
        result[sid] = signals.classify_ma_price_position(
            ma20_today, ma20_yesterday, ma60_today, ma60_yesterday, close_today, open_today,
        )
    return result


def load_ma_price_position(conn, stock_id: str, as_of_date: str | None = None) -> dict | None:
    """H欄：均線狀態。需要「今天」跟「上一個實際有daily_indicators紀錄的交易日」的
    MA20/MA60，不是自然日的前一天(daily_indicators本來就只在交易日有紀錄)。"""
    return load_ma_price_position_batch(conn, [stock_id], as_of_date)[stock_id]


def load_weekly_volume_pattern_batch(
    conn, stock_ids: list[str], as_of_date: str | None = None, _as_of_dates: dict[str, str | None] | None = None,
) -> dict[str, dict | None]:
    """`load_weekly_volume_pattern()`的批次版，回傳{stock_id: 該函式原本的回傳值}。"""
    result: dict[str, dict | None] = {sid: None for sid in stock_ids}
    if not stock_ids:
        return result
    as_of_dates = _as_of_dates if _as_of_dates is not None else _resolve_as_of_dates(conn, stock_ids, as_of_date)
    valid_ids = [sid for sid in stock_ids if as_of_dates.get(sid)]
    if not valid_ids:
        return result

    max_date = max(as_of_dates[sid] for sid in valid_ids)
    cur = conn.execute(
        f"""
        SELECT stock_id, date, high, low, close, volume FROM stock_prices
        WHERE stock_id IN ({_placeholders(len(valid_ids))}) AND date <= ? AND date >= date(?, ?)
        ORDER BY stock_id, date ASC
        """,
        [*valid_ids, max_date, max_date, f"-{WEEKLY_PATTERN_LOOKBACK_DAYS} days"],
    )
    rows_by_stock: dict[str, list[dict]] = {}
    for stock_id, row_date, high, low, close, volume in cur.fetchall():
        rows_by_stock.setdefault(stock_id, []).append(
            {"date": row_date, "high": high, "low": low, "close": close, "volume": volume}
        )

    for sid in valid_ids:
        own_date = as_of_dates[sid]
        rows = [r for r in rows_by_stock.get(sid, []) if r["date"] <= own_date]
        result[sid] = signals.classify_weekly_volume_pattern(rows)
    return result


def load_weekly_volume_pattern(conn, stock_id: str, as_of_date: str | None = None) -> dict | None:
    """I欄：週K型態(大量K判斷)，取最近370個曆日的日K(對應原程式碼的抓法)。"""
    return load_weekly_volume_pattern_batch(conn, [stock_id], as_of_date)[stock_id]


def get_latest_holder_update_time(conn) -> str | None:
    """回傳holder_shares_distribution表裡最新的updated_at時間戳，代表F/G資料最近
    一次被寫入(不管是backfill、daily_pipeline.py的排程、還是使用者手動觸發)的時間。
    跟`src.presentation.chart_data.get_latest_update_time()`(股價/法人資料版本)
    同一種用途，供desktop/main_window.py偵測觀察清單背景資料有沒有被更新過、需不需要
    自動重新整理畫面(見_check_for_external_watchlist_update())。查無任何資料回傳None。
    """
    row = conn.execute("SELECT MAX(updated_at) FROM holder_shares_distribution").fetchone()
    return row[0] if row is not None else None


def load_holder_change_batch(conn, stock_ids: list[str]) -> dict[str, dict | None]:
    """`load_holder_change()`的批次版，回傳{stock_id: 該函式原本的回傳值}。"""
    result: dict[str, dict | None] = {sid: None for sid in stock_ids}
    if not stock_ids:
        return result
    cur = conn.execute(
        f"SELECT stock_id, date, holding_shares_level, percent FROM holder_shares_distribution "
        f"WHERE stock_id IN ({_placeholders(len(stock_ids))})",
        stock_ids,
    )
    rows_by_stock: dict[str, dict[str, list[dict]]] = {sid: {} for sid in stock_ids}
    for stock_id, row_date, level, percent in cur.fetchall():
        rows_by_stock[stock_id].setdefault(row_date, []).append({"holding_shares_level": level, "percent": percent})
    for sid in stock_ids:
        result[sid] = signals.classify_holder_change(rows_by_stock[sid])
    return result


def load_holder_change(conn, stock_id: str) -> dict | None:
    """F/G欄：大戶/散戶持股週變化。查`holder_shares_distribution`表——這張表目前沒有
    排程自動維護，查無資料時回傳None，呼叫端顯示「尚未有資料」。"""
    return load_holder_change_batch(conn, [stock_id])[stock_id]


def load_huang_chip_rows_batch(conn, stock_ids: list[str], as_of_date: str | None = None) -> dict[str, dict]:
    """`load_huang_chip_row()`的批次版：一次組出多檔股票的D~R全部欄位，回傳
    {stock_id: 該函式原本的回傳dict}。觀察清單/庫存清單/Google Sheet匯出都應該改用
    這個函式取代「逐股呼叫`load_huang_chip_row()`」的迴圈——這是2026-08-07修「web版
    切換觀察清單很慢」的根本解，見模組docstring最上方的說明。stock_ids為空回傳{}。
    """
    if not stock_ids:
        return {}
    as_of_dates = _resolve_as_of_dates(conn, stock_ids, as_of_date)
    streak_and_flow = load_institutional_streak_and_flow_batch(conn, stock_ids, _as_of_dates=as_of_dates)
    ma_positions = load_ma_price_position_batch(conn, stock_ids, _as_of_dates=as_of_dates)
    weekly_patterns = load_weekly_volume_pattern_batch(conn, stock_ids, _as_of_dates=as_of_dates)
    holder_changes = load_holder_change_batch(conn, stock_ids)
    return {
        sid: {
            "invest_streak": streak_and_flow[sid]["invest_streak"],
            "foreign_streak": streak_and_flow[sid]["foreign_streak"],
            "flow": streak_and_flow[sid]["flow"],
            "ma_price_position": ma_positions[sid],
            "weekly_volume_pattern": weekly_patterns[sid],
            "holder_change": holder_changes[sid],
        }
        for sid in stock_ids
    }


def load_huang_chip_row(conn, stock_id: str, as_of_date: str | None = None) -> dict:
    """組合觀察清單一列所需的D~R全部欄位，供UI直接使用。單股查詢用；批次場景(觀察清單
    整個群組)請改用`load_huang_chip_rows_batch()`，避免N檔股票=9N條逐股SQL的效能問題。
    """
    return load_huang_chip_rows_batch(conn, [stock_id], as_of_date)[stock_id]
