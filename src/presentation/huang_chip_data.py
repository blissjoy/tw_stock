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
"""

from __future__ import annotations

from src.indicators import huang_chip_signals as signals

# 法人買賣超回看天數：對應原程式碼抓90個曆日(涵蓋約40個交易日)，供D/E連續天數判讀、
# K~R的40/20/10/5日加總共用同一批資料。
INSTITUTIONAL_LOOKBACK_DAYS = 90

# 週K大量型態回看天數：對應原程式碼抓370個曆日——見huang_chip_signals.
# classify_weekly_volume_pattern()docstring的52週精確度說明，這裡照抄原本的抓法。
WEEKLY_PATTERN_LOOKBACK_DAYS = 370


def _resolve_as_of_date(conn, stock_id: str, as_of_date: str | None) -> str | None:
    if as_of_date is not None:
        return as_of_date
    row = conn.execute("SELECT MAX(date) FROM stock_prices WHERE stock_id = ?", (stock_id,)).fetchone()
    return row[0] if row is not None else None


def _fetch_institutional_net_desc(
    conn, stock_id: str, investor_type: str, as_of_date: str, lookback_days: int,
) -> list[float]:
    """回傳指定investor_type(原始FinMind分類，"Foreign_Investor"/"Investment_Trust")
    依日期新到舊排序的(買-賣)淨額(股數)。"""
    cur = conn.execute(
        """
        SELECT buy, sell FROM institutional_investors
        WHERE stock_id = ? AND investor_type = ? AND date <= ?
        ORDER BY date DESC LIMIT ?
        """,
        (stock_id, investor_type, as_of_date, lookback_days),
    )
    return [buy - sell for buy, sell in cur.fetchall()]


def load_institutional_streak_and_flow(conn, stock_id: str, as_of_date: str | None = None) -> dict:
    """組合D/E(投信/外資連續買賣超狀態)＋K~R(40/20/10/5日買賣超張數)。

    回傳{"invest_streak", "foreign_streak", "flow"}，"flow"是{"foreign_40d",
    "invest_40d", "foreign_20d", "invest_20d", "foreign_10d", "invest_10d",
    "foreign_5d", "invest_5d"}或None——對應原程式碼「投信、外資兩份資料都是空的才
    整段跳過不寫」，只要其中一份有資料就照樣算(缺的那份自然加總為0，不是跳過)。
    """
    resolved_date = _resolve_as_of_date(conn, stock_id, as_of_date)
    if resolved_date is None:
        invest_net: list[float] = []
        foreign_net: list[float] = []
    else:
        invest_net = _fetch_institutional_net_desc(conn, stock_id, "Investment_Trust", resolved_date, INSTITUTIONAL_LOOKBACK_DAYS)
        foreign_net = _fetch_institutional_net_desc(conn, stock_id, "Foreign_Investor", resolved_date, INSTITUTIONAL_LOOKBACK_DAYS)

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

    return {
        "invest_streak": signals.classify_institutional_streak(invest_net),
        "foreign_streak": signals.classify_institutional_streak(foreign_net),
        "flow": flow,
    }


def load_ma_price_position(conn, stock_id: str, as_of_date: str | None = None) -> dict | None:
    """H欄：均線狀態。需要「今天」跟「上一個實際有daily_indicators紀錄的交易日」的
    MA20/MA60，不是自然日的前一天(daily_indicators本來就只在交易日有紀錄)。"""
    resolved_date = _resolve_as_of_date(conn, stock_id, as_of_date)
    if resolved_date is None:
        return None
    cur = conn.execute(
        "SELECT date, ma20, ma60 FROM daily_indicators WHERE stock_id = ? AND date <= ? ORDER BY date DESC LIMIT 2",
        (stock_id, resolved_date),
    )
    rows = cur.fetchall()
    if len(rows) < 2:
        return None
    (today_date, ma20_today, ma60_today), (_, ma20_yesterday, ma60_yesterday) = rows

    price_row = conn.execute(
        "SELECT open, close FROM stock_prices WHERE stock_id = ? AND date = ?", (stock_id, today_date),
    ).fetchone()
    if price_row is None:
        return None
    open_today, close_today = price_row

    return signals.classify_ma_price_position(ma20_today, ma20_yesterday, ma60_today, ma60_yesterday, close_today, open_today)


def load_weekly_volume_pattern(conn, stock_id: str, as_of_date: str | None = None) -> dict | None:
    """I欄：週K型態(大量K判斷)，取最近370個曆日的日K(對應原程式碼的抓法)。"""
    resolved_date = _resolve_as_of_date(conn, stock_id, as_of_date)
    if resolved_date is None:
        return None
    cur = conn.execute(
        """
        SELECT date, high, low, close, volume FROM stock_prices
        WHERE stock_id = ? AND date <= ? AND date >= date(?, ?)
        ORDER BY date ASC
        """,
        (stock_id, resolved_date, resolved_date, f"-{WEEKLY_PATTERN_LOOKBACK_DAYS} days"),
    )
    rows = [
        {"date": row_date, "high": high, "low": low, "close": close, "volume": volume}
        for row_date, high, low, close, volume in cur.fetchall()
    ]
    return signals.classify_weekly_volume_pattern(rows)


def load_holder_change(conn, stock_id: str) -> dict | None:
    """F/G欄：大戶/散戶持股週變化。查`holder_shares_distribution`表——這張表目前沒有
    排程自動維護，查無資料時回傳None，呼叫端顯示「尚未有資料」。"""
    cur = conn.execute(
        "SELECT date, holding_shares_level, percent FROM holder_shares_distribution WHERE stock_id = ?",
        (stock_id,),
    )
    rows_by_date: dict[str, list[dict]] = {}
    for row_date, level, percent in cur.fetchall():
        rows_by_date.setdefault(row_date, []).append({"holding_shares_level": level, "percent": percent})
    return signals.classify_holder_change(rows_by_date)


def load_huang_chip_row(conn, stock_id: str, as_of_date: str | None = None) -> dict:
    """組合觀察清單一列所需的D~R全部欄位，供UI直接使用。"""
    streak_and_flow = load_institutional_streak_and_flow(conn, stock_id, as_of_date)
    return {
        "invest_streak": streak_and_flow["invest_streak"],
        "foreign_streak": streak_and_flow["foreign_streak"],
        "flow": streak_and_flow["flow"],
        "ma_price_position": load_ma_price_position(conn, stock_id, as_of_date),
        "weekly_volume_pattern": load_weekly_volume_pattern(conn, stock_id, as_of_date),
        "holder_change": load_holder_change(conn, stock_id),
    }
