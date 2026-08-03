"""「個股明細」分頁的查詢層：交易資訊/法人買賣/資券變化/主力進出/大戶籌碼——純函式
（吃`conn`，回傳`dict`/`None`），不依賴任何UI框架，跟chart_data.py/portfolio_data.py
同一套分工慣例，讓桌面版desktop/main_window.py只負責把這裡的結果組成HTML/表格顯示。

2026-08-02新增。目前只有「交易資訊」「法人買賣總覽」「資券變化總覽」三個區塊有真實
資料來源（分別對應stock_prices/institutional_investors/margin_trading這三張表，
scripts/daily_pipeline.py每天會實際寫入)；「主力進出」需要券商分點籌碼(broker_chips
表，schema已建好但抓取器要FinMind付費方案才能接上)、「大戶籌碼」需要股權分散/大戶
持股資料(目前schema完全沒有對應的表)，這兩個區塊目前沒有資料來源，這裡不提供對應
查詢函式，呼叫端(desktop/main_window.py)直接顯示「尚未串接資料來源」的框架，不是
這裡回傳空dict硬湊。
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

from src.indicators import institutional_flow, margin_trading

# 三大法人5類細項(見src/data/twse_client.py的_INSTITUTIONAL_COLUMN_MAP，欄位命名
# 對應FinMind TaiwanStockInstitutionalInvestorsBuySell)合併成一般常見的3個分類——
# 外資自營商(Foreign_Dealer_Self)併入外資、自營商自行/避險併入自營商，是多數看盤
# 網站(例如XQ/Goodinfo)呈現「三大法人買賣超」時的標準分法。
_INVESTOR_GROUP_MAP = {
    "Foreign_Investor": "外資",
    "Foreign_Dealer_Self": "外資",
    "Investment_Trust": "投信",
    "Dealer_self": "自營商",
    "Dealer_Hedging": "自營商",
}
INSTITUTIONAL_GROUPS = ["外資", "投信", "自營商", "三大法人"]

# 「累計」檢視的天期欄位，key是顯示標籤、value是往前算的交易日數(1個月/3個月/6個月/
# 1年是約略的交易日數換算，不是真的日曆月，跟chart_data.py的TREND_LOOKBACK_DAYS
# 抓法一致，都是「交易日」不是「日曆日」)。
INSTITUTIONAL_PERIODS = {
    "1日": 1, "2日": 2, "3日": 3, "5日": 5, "10日": 10, "20日": 20, "40日": 40,
    "3個月": 60, "6個月": 120, "1年": 240,
}


def load_quote_summary(conn, stock_id: str) -> dict | None:
    """回傳最新交易日的成交資訊：成交(收盤)/開盤/最高/最低/均價/成交金額(億)/昨收/
    漲跌/漲跌幅(%)/總量(張)/昨量(張)/振幅(%)，查無資料回傳None。

    均價=成交金額/成交股數，成交金額(億)=成交金額/1億——trading_money是yfinance
    回補的舊資料可能是None(見schema.sql的欄位說明)，這種情況均價/成交金額(億)
    一併回傳None，不強行算一個不可靠的數字。總量/昨量原始單位是「股」，除以1000
    換算成台股慣用的「張」，四捨五入到整數張。
    """
    rows = conn.execute(
        "SELECT date, open, high, low, close, volume, trading_money FROM stock_prices "
        "WHERE stock_id = ? ORDER BY date DESC LIMIT 2",
        (stock_id,),
    ).fetchall()
    if not rows:
        return None
    today = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    date, open_, high, low, close, volume, trading_money = today
    prev_close = prev[4] if prev else None
    prev_volume = prev[5] if prev else None

    avg_price = trading_money / volume if trading_money and volume else None
    trading_money_billion = trading_money / 1e8 if trading_money is not None else None
    change = close - prev_close if prev_close is not None else None
    change_pct = change / prev_close * 100 if change is not None and prev_close else None
    amplitude_pct = (high - low) / prev_close * 100 if prev_close else None

    return {
        "date": date,
        "close": close,
        "open": open_,
        "high": high,
        "low": low,
        "avg_price": avg_price,
        "trading_money_billion": trading_money_billion,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "volume_lots": round(volume / 1000),
        "prev_volume_lots": round(prev_volume / 1000) if prev_volume is not None else None,
        "amplitude_pct": amplitude_pct,
    }


def _fetch_institutional_by_date(conn, stock_id: str, max_days: int) -> tuple[list[str], dict[str, dict[str, int]]]:
    """回傳(依新到舊排序的日期清單, {日期: {分類: 買賣超股數}})，只取最近max_days個
    「有紀錄」的交易日——法人買賣超跟股價一樣，非交易日本來就不會有紀錄，不需要
    另外處理假日。"""
    dates = [
        row[0] for row in conn.execute(
            "SELECT DISTINCT date FROM institutional_investors WHERE stock_id = ? ORDER BY date DESC LIMIT ?",
            (stock_id, max_days),
        ).fetchall()
    ]
    if not dates:
        return [], {}
    placeholders = ",".join("?" * len(dates))
    rows = conn.execute(
        f"SELECT date, investor_type, buy, sell FROM institutional_investors "
        f"WHERE stock_id = ? AND date IN ({placeholders})",
        (stock_id, *dates),
    ).fetchall()
    net_by_date: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for date, investor_type, buy, sell in rows:
        group = _INVESTOR_GROUP_MAP.get(investor_type)
        if group is None:
            continue
        net = (buy or 0) - (sell or 0)
        net_by_date[date][group] += net
        net_by_date[date]["三大法人"] += net
    return dates, net_by_date


def load_institutional_daily(conn, stock_id: str) -> dict[str, dict[str, int]] | None:
    """回傳最新交易日各法人分類的買進/賣出/買賣超(股數)，查無資料回傳None。"""
    rows = conn.execute(
        "SELECT investor_type, buy, sell FROM institutional_investors "
        "WHERE stock_id = ? AND date = (SELECT MAX(date) FROM institutional_investors WHERE stock_id = ?)",
        (stock_id, stock_id),
    ).fetchall()
    if not rows:
        return None
    result = {g: {"buy": 0, "sell": 0} for g in INSTITUTIONAL_GROUPS}
    for investor_type, buy, sell in rows:
        group = _INVESTOR_GROUP_MAP.get(investor_type)
        if group is None:
            continue
        for g in (group, "三大法人"):
            result[g]["buy"] += buy or 0
            result[g]["sell"] += sell or 0
    for g in INSTITUTIONAL_GROUPS:
        result[g]["net"] = result[g]["buy"] - result[g]["sell"]
    return result


def load_institutional_cumulative(conn, stock_id: str) -> dict[str, dict[str, int]] | None:
    """回傳各法人分類在INSTITUTIONAL_PERIODS各天期的買賣超(股數)加總，查無資料回傳
    None。天數不足時就加總「目前實際有的天數」，不是回傳None——例如剛上市股票
    只有20天資料，「6個月」欄位就是這20天的加總，不強求一定要湊滿240天。
    """
    max_days = max(INSTITUTIONAL_PERIODS.values())
    dates, net_by_date = _fetch_institutional_by_date(conn, stock_id, max_days)
    if not dates:
        return None
    result: dict[str, dict[str, int]] = {g: {} for g in INSTITUTIONAL_GROUPS}
    for label, n in INSTITUTIONAL_PERIODS.items():
        selected = dates[:n]
        for g in INSTITUTIONAL_GROUPS:
            result[g][label] = sum(net_by_date[d].get(g, 0) for d in selected)
    return result


ESTIMATED_COST_GROUPS = ("外資", "投信")


def load_institutional_estimated_cost(conn, stock_id: str) -> dict[str, dict[str, float | None]] | None:
    """回傳外資／投信在INSTITUTIONAL_PERIODS各天期的「預估持股成本價」——用當天均價
    (成交金額/成交量，缺均價時退回收盤價)乘以當天買賣超股數加權平均。該天期買賣超
    合計<=0(淨賣出或淨零，沒有淨累積部位)時回傳None(不適用)，不硬算一個沒有意義
    的數字；查無法人資料回傳最外層None。

    2026-08-03新增：陳家豐書中提到分點層級的「持股成本」概念可以用「每日買賣張數
    ×當日均價加權平均」粗略估算(見ai/ebook-summary-chen/P01-C3-好用籌碼工具哪裡找.md)，
    這裡把同一套邏輯套用在外資/投信這個類別層級(本專案沒有分點資料，書中原文是分點
    層級)——書中沒有給精確演算法，這是工程實作，不是嚴格的移動平均成本會計：賣出
    當天的股數是用「當天均價」加權抵銷，不是用「原始買進價」抵銷，屬於簡化估算，
    使用者已確認接受這個做法與「淨賣出天期標示不適用」的處理方式。
    """
    max_days = max(INSTITUTIONAL_PERIODS.values())
    dates, net_by_date = _fetch_institutional_by_date(conn, stock_id, max_days)
    if not dates:
        return None
    placeholders = ",".join("?" * len(dates))
    price_rows = conn.execute(
        f"SELECT date, close, volume, trading_money FROM stock_prices WHERE stock_id = ? AND date IN ({placeholders})",
        (stock_id, *dates),
    ).fetchall()
    avg_price_by_date: dict[str, float | None] = {}
    for date, close, volume, trading_money in price_rows:
        avg_price_by_date[date] = trading_money / volume if trading_money is not None and volume else close

    result: dict[str, dict[str, float | None]] = {g: {} for g in ESTIMATED_COST_GROUPS}
    for label, n in INSTITUTIONAL_PERIODS.items():
        window = dates[:n]
        for group in ESTIMATED_COST_GROUPS:
            numerator = 0.0
            denominator = 0
            for d in window:
                price = avg_price_by_date.get(d)
                net = net_by_date[d].get(group, 0)
                if price is None or net == 0:
                    continue
                numerator += net * price
                denominator += net
            result[group][label] = numerator / denominator if denominator > 0 else None
    return result


def load_institutional_flow_analysis(conn, stock_id: str, lookback_days: int = 30) -> dict[str, dict] | None:
    """回傳各法人分類「連續買超／賣超幾天」的判讀結果(src.indicators.institutional_
    flow.classify_flow_streak())，查無資料回傳None。lookback_days只是抓資料的
    緩衝天數(連續天數不太可能超過30個交易日，抓太長沒意義)，不是天期欄位。

    2026-08-03新增：供「個股明細」法人買賣總覽表格下方顯示分析文字用，理論依據
    見src/indicators/institutional_flow.py的模組docstring(朱家泓淘汰法R-SCREEN-06
    三大法人連續賣超警示＋陳家豐投信連續買超觀察)。
    """
    dates, net_by_date = _fetch_institutional_by_date(conn, stock_id, lookback_days)
    if not dates:
        return None
    return {
        group: institutional_flow.classify_flow_streak([net_by_date[d].get(group, 0) for d in dates])
        for group in INSTITUTIONAL_GROUPS
    }


_MOMENTUM_PERIODS = (5, 20, 40)


def _classify_institutional_momentum(current: int, prior: int) -> str:
    """比較「近N天合計買賣超」(current)跟「再往前N天合計」(prior)，回傳力道變化
    文字。這不是book裡的規則，是2026-08-03使用者直接指定要加的比較邏輯：跟
    load_institutional_flow_analysis()看的「連續同方向天數」是互補但不同的角度，
    這裡看的是「近期力道有沒有比上一個同長度區間更強/更弱」。"""
    if current > 0 and prior > 0:
        if current > prior:
            return "買超力道增強"
        if current < prior:
            return "買超力道減弱"
        return "買超力道持平"
    if current < 0 and prior < 0:
        if current < prior:
            return "賣壓加重"
        if current > prior:
            return "賣壓減緩"
        return "賣壓持平"
    if current > 0 and prior <= 0:
        return "由賣轉買"
    if current < 0 and prior >= 0:
        return "由買轉賣"
    return "買賣力道持平"


MOMENTUM_GROUPS = ("外資", "投信", "外資+投信")


def load_institutional_momentum_analysis(conn, stock_id: str) -> dict[str, dict[str, dict]] | None:
    """回傳外資／投信各自、以及外資+投信合計，在近5日/20日/40日這三個天期的
    「近N天合計」vs「前N天合計」比較結果——{分類: {天期標籤: {"current":
    近N天合計股數, "prior": 前N天合計股數, "trend": 力道變化文字}}}，某分類
    在所有天期都算不出來就不會出現在結果裡，查無資料回傳None。

    2026-08-03新增：使用者要求在法人買賣總覽的分析文字裡，加上「累積近5日/20日/
    40日是變多還是變少，代表法人持續買進還是賣出」這段判讀。2026-08-03第二次
    改版：使用者要求拆成外資／投信各自分析、再看兩者加總，自營商不計入這裡的
    比較——理由跟_build_institutional_flow_analysis_html()既有的「自營商連續
    買賣超」段落引用的陳家豐書中提醒一致(自營商操作週期短、常忽買忽賣，不適合
    用來判斷趨勢性力道)，所以這裡的「外資+投信」合計是外資淨額加投信淨額，
    不是沿用_INVESTOR_GROUP_MAP彙總出的「三大法人」(那個含自營商)。

    要湊滿一個公平的「近N天」vs「前N天」比較，需要2N天完整資料，天數不足2N天
    的天期就不計算(不強行用不足N天的區間硬湊，避免比較基準不一致而誤導)。
    """
    max_days = max(_MOMENTUM_PERIODS) * 2
    dates, net_by_date = _fetch_institutional_by_date(conn, stock_id, max_days)
    if not dates:
        return None

    def net(date: str, group: str) -> int:
        if group == "外資+投信":
            return net_by_date[date].get("外資", 0) + net_by_date[date].get("投信", 0)
        return net_by_date[date].get(group, 0)

    result: dict[str, dict[str, dict]] = {}
    for group in MOMENTUM_GROUPS:
        group_result: dict[str, dict] = {}
        for n in _MOMENTUM_PERIODS:
            if len(dates) < n * 2:
                continue
            current = sum(net(d, group) for d in dates[:n])
            prior = sum(net(d, group) for d in dates[n:n * 2])
            group_result[f"{n}日"] = {
                "current": current,
                "prior": prior,
                "trend": _classify_institutional_momentum(current, prior),
            }
        if group_result:
            result[group] = group_result
    return result or None


def _balance_streak(balances: list[float]) -> str | None:
    """balances須由新到舊排序(index 0=今天)，回傳「連N增」/「連N減」文字。今天比
    前一天平盤(無變動)或資料不足2天時回傳None——「連續增減」這個概念本來就只在
    方向明確時才有意義。"""
    if len(balances) < 2:
        return None
    diffs = [balances[i] - balances[i + 1] for i in range(len(balances) - 1)]
    if diffs[0] == 0:
        return None
    direction = "增" if diffs[0] > 0 else "減"
    count = 0
    for d in diffs:
        if (d > 0) == (direction == "增") and d != 0:
            count += 1
        else:
            break
    return f"連{count}{direction}"


def load_margin_daily(conn, stock_id: str) -> dict | None:
    """回傳最新交易日的融資/融券變化總覽(當日檢視)：買進/賣出/現價/增減/餘額/使用率
    (%)/連增連減/資券互抵/券資比(%)。查無margin_trading資料回傳None。

    連增連減需要往前多抓幾天餘額算連續增減天數(_balance_streak())，這裡抓最近
    30個交易日，跟股票網站常見顯示的個位數~十幾天連續紀錄相比已經很寬裕；使用率
    =今日餘額/限額，限額為0或None時無從計算，回傳None不是0(避免誤讀成「已用完
    額度」)。"""
    rows = conn.execute(
        """
        SELECT date, margin_purchase_buy, margin_purchase_sell, margin_purchase_today_balance,
               margin_purchase_yesterday_balance, margin_purchase_limit,
               short_sale_buy, short_sale_sell, short_sale_today_balance,
               short_sale_yesterday_balance, short_sale_limit, offset_loan_and_short
        FROM margin_trading WHERE stock_id = ? ORDER BY date DESC LIMIT 30
        """,
        (stock_id,),
    ).fetchall()
    if not rows:
        return None
    latest = rows[0]
    (
        date, m_buy, m_sell, m_today, m_yesterday, m_limit,
        s_buy, s_sell, s_today, s_yesterday, s_limit, offset,
    ) = latest
    close_row = conn.execute("SELECT close FROM stock_prices WHERE stock_id = ? AND date = ?", (stock_id, date)).fetchone()
    close = close_row[0] if close_row else None

    m_balances = [r[3] for r in rows if r[3] is not None]
    s_balances = [r[8] for r in rows if r[8] is not None]

    def _usage_rate(balance, limit):
        return balance / limit * 100 if balance is not None and limit else None

    return {
        "date": date,
        "close": close,
        "margin": {
            "buy": m_buy, "sell": m_sell, "balance": m_today,
            "change": (m_today - m_yesterday) if m_today is not None and m_yesterday is not None else None,
            "usage_rate": _usage_rate(m_today, m_limit),
            "streak": _balance_streak(m_balances),
        },
        "short": {
            "buy": s_buy, "sell": s_sell, "balance": s_today,
            "change": (s_today - s_yesterday) if s_today is not None and s_yesterday is not None else None,
            "usage_rate": _usage_rate(s_today, s_limit),
            "streak": _balance_streak(s_balances),
        },
        "offset_loan_and_short": offset,
        "short_to_margin_ratio_pct": (s_today / m_today * 100) if s_today is not None and m_today else None,
    }


def load_margin_cumulative(conn, stock_id: str, days: int = 20) -> dict | None:
    """回傳融資/融券最近`days`個交易日的買進/賣出/餘額增減加總(累計檢視)，查無資料
    回傳None。天數不足時加總「目前實際有的天數」，跟load_institutional_
    cumulative()的處理方式一致。"""
    rows = conn.execute(
        """
        SELECT margin_purchase_buy, margin_purchase_sell, margin_purchase_today_balance,
               short_sale_buy, short_sale_sell, short_sale_today_balance
        FROM margin_trading WHERE stock_id = ? ORDER BY date DESC LIMIT ?
        """,
        (stock_id, days),
    ).fetchall()
    if not rows:
        return None
    oldest_balance_m = rows[-1][2]
    oldest_balance_s = rows[-1][5]
    latest_balance_m = rows[0][2]
    latest_balance_s = rows[0][5]
    return {
        "days": len(rows),
        "margin": {
            "buy": sum(r[0] or 0 for r in rows),
            "sell": sum(r[1] or 0 for r in rows),
            "change": (latest_balance_m - oldest_balance_m) if None not in (latest_balance_m, oldest_balance_m) else None,
        },
        "short": {
            "buy": sum(r[3] or 0 for r in rows),
            "sell": sum(r[4] or 0 for r in rows),
            "change": (latest_balance_s - oldest_balance_s) if None not in (latest_balance_s, oldest_balance_s) else None,
        },
    }


def load_margin_maintenance_analysis(conn, stock_id: str) -> dict | None:
    """回傳個股目前的融資維持率估算結果：ratio(最新一天估算值)/state(166%初始/135%
    警戒/120%斷頭三態分類)/oversold_rebound_signal(是否符合連續N天低於120%的超跌
    反彈訊號)。查無資料回傳None。

    2026-08-03新增，理論依據見src/indicators/margin_trading.py的模組docstring
    (陳家豐《看懂籌碼 股市賺大錢》第2篇第4章「融資維持率揭秘 勿買斷頭股」)。這裡用
    該股「目前資料庫裡的全部歷史」計算加權平均成本(不是只抓最近N天)，跟margin_
    trading.py docstring提醒的一致：抓的歷史越長，早期資料造成的估算偏誤被稀釋得
    越乾淨。融資成數採用該模組的預設值(DEFAULT_MARGIN_PCT=0.6，書中範例的6成)，
    不是這檔股票實際的融資成數規定(書中也提醒過這點，TWSE公開資料目前查不到逐股
    融資成數，這是已知的估算限制，不是bug)。
    """
    rows = conn.execute(
        """
        SELECT sp.date, sp.close, mt.margin_purchase_buy, mt.margin_purchase_sell,
               mt.margin_purchase_cash_repayment, mt.margin_purchase_today_balance
        FROM margin_trading mt
        JOIN stock_prices sp ON sp.stock_id = mt.stock_id AND sp.date = mt.date
        WHERE mt.stock_id = ?
        ORDER BY mt.date ASC
        """,
        (stock_id,),
    ).fetchall()
    if not rows:
        return None
    dates = [r[0] for r in rows]
    close = pd.Series([r[1] for r in rows], index=dates)
    margin_buy = pd.Series([r[2] for r in rows], index=dates)
    margin_sell = pd.Series([r[3] for r in rows], index=dates)
    margin_repay = pd.Series([r[4] for r in rows], index=dates)
    margin_balance = pd.Series([r[5] for r in rows], index=dates)

    ratio = margin_trading.compute_margin_maintenance_ratio(close, margin_buy, margin_sell, margin_repay, margin_balance)
    latest_ratio = ratio.iloc[-1]
    latest_ratio = None if pd.isna(latest_ratio) else float(latest_ratio)
    rebound_series = margin_trading.margin_oversold_rebound_signal(ratio)
    return {
        "ratio": latest_ratio,
        "state": margin_trading.classify_margin_maintenance_state(latest_ratio),
        "oversold_rebound_signal": bool(rebound_series.iloc[-1]),
    }
