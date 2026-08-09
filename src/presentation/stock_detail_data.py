"""「個股明細」分頁的查詢層：交易資訊/法人買賣/資券變化/主力進出/大戶籌碼——純函式
（吃`conn`，回傳`dict`/`None`），不依賴任何UI框架，跟chart_data.py/portfolio_data.py
同一套分工慣例，讓桌面版desktop/main_window.py只負責把這裡的結果組成HTML/表格顯示。

2026-08-02新增。「交易資訊」「法人買賣總覽」「資券變化總覽」三個區塊有真實資料來源
（分別對應stock_prices/institutional_investors/margin_trading這三張表，scripts/
daily_pipeline.py每天會實際寫入)；「主力進出」需要券商分點籌碼(broker_chips表，
schema已建好但抓取器要FinMind付費方案才能接上)，這個區塊目前沒有資料來源，這裡不
提供對應查詢函式，呼叫端(desktop/main_window.py)直接顯示「尚未串接資料來源」的框架，
不是這裡回傳空dict硬湊。「大戶籌碼」2026-08-09起有資料來源(見load_ownership_
distribution_analysis()，但只涵蓋「觀察清單」裡的股票，見holder_shares_distribution
表的既有範圍限制)。
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

from src.indicators import institutional_flow, margin_trading, ownership_distribution, volume_washout

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

    均價=成交金額/成交股數，成交金額(億)=成交金額/1億——trading_money缺值時(盤中
    即時價備援沒有這個欄位，或yfinance回補的舊資料本來就沒有，見schema.sql的欄位
    說明)，2026-08-03改版：不再直接回傳None，改用當天OHLC湊出的「典型價格」
    (最高+最低+收盤)/3估算均價，再乘以成交量回推一個估算成交金額——不是真正逐筆
    成交算出來的官方數字，只是近似值，`avg_price_is_estimated`標記這種情況，UI
    據此顯示「(估)」，不能跟官方數字混著看。成交量本身不受trading_money是否缺值
    影響，一律是DB裡實際紀錄的股數(盤中即時價備援也有累計成交量，不是缺值)。
    總量/昨量原始單位是「股」，除以1000換算成台股慣用的「張」，四捨五入到整數張。
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

    avg_price_is_estimated = False
    if trading_money and volume:
        avg_price = trading_money / volume
    elif volume:
        avg_price = (high + low + close) / 3
        trading_money = avg_price * volume
        avg_price_is_estimated = True
    else:
        avg_price = None
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
        "avg_price_is_estimated": avg_price_is_estimated,
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
    """回傳外資／投信在INSTITUTIONAL_PERIODS各天期的「預估持股成本價」——只用「淨買超」
    的交易日(當天均價=成交金額/成交量，缺均價時退回收盤價)加權平均；該天期買賣超合計
    <=0(淨賣出或淨零，沒有淨累積部位)時回傳None(不適用)，不硬算一個沒有意義的數字；
    查無法人資料回傳最外層None。

    2026-08-03新增：陳家豐書中提到分點層級的「持股成本」概念可以用「每日買賣張數×當日
    均價加權平均」粗略估算(見ai/ebook-summary-chen/P01-C3-好用籌碼工具哪裡找.md)，這裡
    把同一套邏輯套用在外資/投信這個類別層級(本專案沒有分點資料，書中原文是分點層級)——
    書中沒有給精確演算法，這是工程實作，不是嚴格的移動平均成本會計。

    2026-08-04修正一個數學上的缺陷：一開始的版本把賣出天的股數也計入分母(用當天均價
    「加權抵銷」)，結果使用者以緯創為例回報算出來的成本價(291.78/508.68)遠高於這段
    期間股價實際出現過的任何一天(最高186)，查證後發現是「加權平均」用負權重(賣出淨額
    是負數)時，只要買超總和跟賣超總和剛好相減後只剩一個很小的正數(分母很小)，就會被
    分子的規模放大成一個脫離實際價格區間的離群值——這不是特定資料錯誤，是這個公式本身
    的數學缺陷(負權重的「加權平均」不再是真正的凸組合，理論上就是可能超出原始數值範圍)。
    改成只累加「淨買超」的交易日(net>0)，賣出天完全不參與加權平均——這也更貼近會計上
    「移動平均成本」的定義(賣出只減少持有數量，不會改變剩餘部位的每股成本)，同時保證
    算出來的數字必然落在窗口內實際成交價格的範圍內，不會再出現這種脫離現實的離群值。
    「該天期整體是否為淨賣出」的判斷(是否顯示不適用)仍然看全部交易日(含賣出)的合計，
    跟加權平均本身用哪些交易日是兩個獨立的判斷，不能只看買超天數合計來決定適用與否
    (那樣的話賣超力道再大也不會被排除，跟「淨賣出天期不適用」的原意矛盾)。
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
            total_net = sum(net_by_date[d].get(group, 0) for d in window)
            numerator = 0.0
            denominator = 0
            for d in window:
                price = avg_price_by_date.get(d)
                net = net_by_date[d].get(group, 0)
                if price is None or net <= 0:
                    continue
                numerator += net * price
                denominator += net
            result[group][label] = numerator / denominator if total_net > 0 and denominator > 0 else None
    return result


def pick_longest_available_cost(cost_by_period: dict[str, float | None]) -> float | None:
    """從INSTITUTIONAL_PERIODS裡最長的天期往最短的天期找，回傳第一個「適用」(非
    None)的預估持股成本，全部天期都不適用時回傳None。

    2026-08-03新增：供「交易資訊」區塊顯示單一「最新持有成本(預估)」數字使用——
    天期越長，越接近「這段時間內真正累積的部位」，優先採用；剛好那個天期是淨賣出
    (不適用)才退到較短天期，盡量給出一個有意義的數字，不是固定用某一個天期、
    動不動就顯示不適用。
    """
    for label in reversed(list(INSTITUTIONAL_PERIODS.keys())):
        value = cost_by_period.get(label)
        if value is not None:
            return value
    return None


def load_latest_institutional_cost_summary(conn, stock_id: str) -> dict[str, float | None] | None:
    """回傳外資／投信「最新持有成本(預估)」單一數字(見pick_longest_available_cost()
    的天期選擇邏輯)——{"外資": 數字或None, "投信": 數字或None}，查無法人資料回傳
    最外層None。2026-08-03新增，供「交易資訊」區塊使用，是load_institutional_
    estimated_cost()完整矩陣的精簡摘要，不是另一套計算邏輯。
    """
    cost = load_institutional_estimated_cost(conn, stock_id)
    if cost is None:
        return None
    return {group: pick_longest_available_cost(cost[group]) for group in ESTIMATED_COST_GROUPS}


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


def load_volume_washout_signal(conn, stock_id: str) -> bool | None:
    """R-CHIP-03低檔量縮止跌觀察(見src/indicators/volume_washout.py)，供scan_chip_
    tier()判斷「今天」是否觸發。只需要stock_prices的成交量歷史，不設查詢天數上限
    (跟本模組load_margin_maintenance_analysis()等其他單股查詢函式一致，這裡只查
    1檔股票，全部歷史的查詢成本可忽略，不需要像daily_screener.py批次跑全市場那樣
    刻意截斷)——⚠️若改成只抓`VOLUME_WASHOUT_LOOKBACK`(240)天剛好夠用，
    volume_washout_signal()內部「近5日均量」的平滑步驟會先吃掉前4天當暖身，
    導致rolling(240)峰值窗口在剛好240筆資料時反而湊不齊240個有效值、算出NaN，
    查全部歷史沒有這個邊界問題。

    資料筆數不足`VOLUME_WASHOUT_LOOKBACK`天(240)時回傳None(該股歷史太短，無法
    判斷)，不是False——None代表「查不出來」，False代表「查得出來、但沒有觸發」，
    兩者語意不同，呼叫端(scan_chip_tier())只在True時才加進結果清單，None/False
    都不加，所以目前呼叫端看不出這個區分，但保留這個語意供之後有需要區分「無法
    判斷」跟「未觸發」的呼叫端使用。
    """
    rows = conn.execute(
        "SELECT volume FROM stock_prices WHERE stock_id = ? ORDER BY date ASC",
        (stock_id,),
    ).fetchall()
    if len(rows) < volume_washout.VOLUME_WASHOUT_LOOKBACK:
        return None
    volume_series = pd.Series([r[0] for r in rows])
    return bool(volume_washout.volume_washout_signal(volume_series).iloc[-1])


def load_ownership_distribution_analysis(conn, stock_id: str) -> dict | None:
    """R-CHIP-07集保股權分散判讀規則(見src/indicators/ownership_distribution.py)，
    供scan_chip_tier()判斷籌碼流向方向。⚠️`holder_shares_distribution`表只對「觀察
    清單」裡的股票有資料(見src/data/holder_shares_sync.py)，不是全市場——查詢清單外
    的股票會回傳None，這是既有的資料範圍限制，不是bug。資料筆數不足2個公告日期時
    (該股不在觀察清單、或剛加入還沒抓過第2次)，底層chip_flow_direction()同樣回傳
    None，這裡原樣往上傳遞。
    """
    rows = conn.execute(
        "SELECT date, holding_shares_level, percent FROM holder_shares_distribution WHERE stock_id = ? ORDER BY date DESC",
        (stock_id,),
    ).fetchall()
    if not rows:
        return None
    rows_by_date: dict[str, list[dict]] = defaultdict(list)
    for date_, level, percent in rows:
        rows_by_date[date_].append({"holding_shares_level": level, "percent": percent})
    return ownership_distribution.chip_flow_direction(rows_by_date)


def scan_chip_tier(conn, stock_id: str) -> list[dict]:
    """回傳籌碼面「今天」實際觸發的規則清單，每筆為{"rule_id": ..., "note": ...}——
    跟`src.screener.rule_scan.scan_golden_tier()`同一種「今天有沒有觸發」語意，但這裡
    涵蓋法人籌碼/融資融券/量能規則(R-SCREEN-06／R-CHIP-*)，需要直接查DB(institutional_
    investors/margin_trading/stock_prices表)，跟scan_golden_tier()純吃OHLCV DataFrame
    的架構不同，所以放在這裡(跟本模組其餘函式一樣是DB查詢層)而不是rule_scan.py。查無
    資料回傳空list，不是None，跟scan_golden_tier()的慣例一致。

    2026-08-04新增，供「個股分析」/「大盤分析」面板的「籌碼面」區塊使用，目前接了5條
    規則，都是本專案已經在「個股明細」分頁用過的既有判讀邏輯，這裡只是重新包裝成
    「今天有沒有觸發」的訊號格式：
    - R-SCREEN-06(朱家泓)：三大法人連續賣超達INSTITUTIONAL_STREAK_THRESHOLD天。
    - R-CHIP-01(陳家豐，見ai/chen-rules/籌碼面/投信連續買超觀察.md)：投信連續買超
      達同一個天數門檻。
    - R-CHIP-02(陳家豐，見ai/chen-rules/籌碼面/融資維持率規則.md)：融資維持率跌破
      120%斷頭線，或連續N天低於120%的超跌反彈訊號——兩者可能同時成立，各自成一筆
      (呼叫端analyze_chip_signals()會合併成同一個rule_id、note用換行接起來)。
    - R-CHIP-03(陳家豐，見ai/chen-rules/籌碼面/量縮止跌觀察.md)：近期均量萎縮到近
      1年峰值均量的10分之1以下。2026-08-08曾評估接進`daily_screener.py`的
      `daily_candidates`候選清單，但實測全市場觸發率高達47%，會讓候選清單失去
      篩選意義而作罷；這裡(個股分析面板，只顯示「當前正在看的這一檔股票」)不受
      同樣的顧慮影響——顯示「這檔股票今天量縮」是單純陳述事實，不是從全市場篩出
      一份「值得注意」的候選名單，兩者定位不同，所以R-CHIP-03只接這裡、不接候選
      清單。
    - R-CHIP-07(陳家豐，見ai/chen-rules/籌碼面/集保股權分散判讀規則.md，2026-08-09
      新增)：集保股權分散表最新2個公告日期比較，大股東(>1,000張)持股增加+散戶
      (<=20張)持股減少(或反向)，判定為有明確方向的籌碼流向。⚠️`holder_shares_
      distribution`只對「觀察清單」裡的股票有資料，非清單內股票這裡不會觸發，是
      既有資料範圍限制、不是bug；只接這裡，不接`daily_screener.py`候選清單(全市場
      掃描沒有意義，因為多數股票根本沒有這份資料)。
    """
    results: list[dict] = []
    flow = load_institutional_flow_analysis(conn, stock_id)
    if flow is not None:
        sanhua = flow["三大法人"]
        if sanhua["is_sell_warning"]:
            results.append({
                "rule_id": "R-SCREEN-06",
                "note": f"三大法人已連續賣超{sanhua['streak_days']}天，達到停損觀察門檻",
            })
        trust = flow["投信"]
        if trust["is_buy_watch"]:
            results.append({
                "rule_id": "R-CHIP-01",
                "note": f"投信已連續買超{trust['streak_days']}天，達最佳切入點觀察門檻(連續3~5天)",
            })

    margin = load_margin_maintenance_analysis(conn, stock_id)
    if margin is not None:
        if margin["state"] == "已跌破斷頭線":
            ratio_pct = margin["ratio"] * 100 if margin["ratio"] is not None else None
            ratio_text = f"約{ratio_pct:.1f}%" if ratio_pct is not None else ""
            results.append({
                "rule_id": "R-CHIP-02",
                "note": f"融資維持率{ratio_text}已跌破斷頭線(120%)，留意斷頭賣壓",
            })
        if margin["oversold_rebound_signal"]:
            results.append({
                "rule_id": "R-CHIP-02",
                "note": "融資維持率連續低於120%(超跌)，符合搶短線反彈觀察條件",
            })

    if load_volume_washout_signal(conn, stock_id):
        results.append({
            "rule_id": "R-CHIP-03",
            "note": "近期均量已萎縮到近1年峰值均量的10分之1以下，符合籌碼洗清、主力再進場觀察條件(書中提醒不宜單獨當高信心買進理由，建議搭配其他訊號一起判讀)",
        })

    ownership = load_ownership_distribution_analysis(conn, stock_id)
    if ownership is not None and ownership["direction"] != "無明確方向":
        results.append({
            "rule_id": "R-CHIP-07",
            "note": (
                f"集保股權分散({ownership['prev_date']}→{ownership['latest_date']})："
                f"大股東(>1,000張)持股{ownership['whale_pct']:.2f}%"
                f"({ownership['whale_diff']:+.2f}%)，散戶(<=20張)持股{ownership['retail_pct']:.2f}%"
                f"({ownership['retail_diff']:+.2f}%)，{ownership['direction']}"
            ),
        })
    return results


def analyze_chip_signals(conn, stock_id: str) -> list[dict]:
    """對scan_chip_tier()的結果逐筆查`src.rule_docs.load_rule_doc()`補上title/
    confidence/description/reference，回傳跟`src.screener.daily_screener.
    analyze_stock_signals()`完全相同的dict結構([{"rule_id","title","confidence",
    "note","description","reference"}, ...])，方便UI端重用同一套規則清單渲染邏輯。
    依confidence由高到低排序。

    同一個rule_id出現多次(R-CHIP-02可能同時觸發斷頭警示+超跌反彈)合併成一筆，note用
    換行接起來——跟analyze_stock_signals()處理R-TREND-03/04多筆note合併同一個模式。
    查無規則文件(理論上不會發生，R-SCREEN-06/R-CHIP-01/R-CHIP-02都已經正式形式化)
    的項目不列入。
    """
    from src.rule_docs import load_rule_doc, parse_confidence

    merged: dict[str, dict] = {}
    order: list[str] = []
    for item in scan_chip_tier(conn, stock_id):
        rule_id = item["rule_id"]
        confidence = parse_confidence(rule_id)
        if confidence is None:
            continue
        if rule_id not in merged:
            doc = load_rule_doc(rule_id)
            merged[rule_id] = {
                "rule_id": rule_id,
                "title": doc.get("名稱", rule_id) if doc else rule_id,
                "confidence": confidence,
                "note": [item["note"]] if item.get("note") else [],
                "description": doc.get("解讀") if doc else None,
                "reference": doc.get("原文與頁碼") if doc else None,
            }
            order.append(rule_id)
        elif item.get("note"):
            merged[rule_id]["note"].append(item["note"])

    result = [merged[rid] for rid in order]
    for m in result:
        m["note"] = "\n".join(m["note"]) if m["note"] else None
    result.sort(key=lambda m: m["confidence"], reverse=True)
    return result


def load_mops_capital_changes(conn, stock_id: str) -> list[dict]:
    """MOPS「公司增減資表」(IRB160)裡跟這檔股票相關的紀錄，依year_month新到舊排序，
    見src/data/mops_client.py模組docstring的完整背景。回傳[{"year_month","market",
    "fetched_at"}, ...]，只是「哪個年月有增減資公告」這個中性事實——⚠️沒有增資/減資
    方向、金額、恢復交易日期，呼叫端(desktop/main_window.py／dashboard/app.py)顯示
    時不能誤植成「已經幫忙判斷是增資還是減資」。查無資料(該股這段期間沒有公告，或
    scripts/fetch_mops_capital_changes.py還沒手動執行過)回傳空list。
    """
    rows = conn.execute(
        "SELECT year_month, market, fetched_at FROM mops_capital_changes WHERE stock_id = ? ORDER BY year_month DESC",
        (stock_id,),
    ).fetchall()
    return [{"year_month": r[0], "market": r[1], "fetched_at": r[2]} for r in rows]
