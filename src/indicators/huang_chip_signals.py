"""黃豐凱籌碼分析法：完全獨立於本專案既有朱家泓/陳嘉宏規則系統之外的一組籌碼判讀邏輯。

程式碼來源：private(使用者朋友提供的Google Apps Script，非公開出處，不附檔名/連結)。
2026-08-04複刻進本專案，使用者明確要求「一模一樣、不可漏掉任何一個邏輯」——這裡的每個
函式都對應原JS程式碼裡的一個判斷區塊，刻意保留原始邏輯（包含原程式碼裡的行為特性，例如
sum_institutional_flow_lots()在資料筆數不足時仍照樣加總、不報錯、不留白），不做「看起來
更合理」的自行修正。已知兩處使用者確認「先跟原code一致，之後驗證通過再回頭問要不要改」的
地方：
1. 外資只算FinMind的"Foreign_Investor"，不併入"Foreign_Dealer_Self"(跟本專案「個股明細」
   既有的三大法人併計口徑不同，這裡刻意不一致，因為要跟可驗證的來源比對)。
2. classify_weekly_volume_pattern()「大量K」判斷用的52週視窗，是「原始資料抓370個日曆天
   、分週後取前52週」，不是「精確52個完整週K」（這是原code本身的già有落差，來源本人也
   確認過），這裡照抄。

刻意不重用src/indicators/institutional_flow.py等既有模組——即使概念上都是「三大法人連續
買賣超」，這裡的狀態機（見classify_institutional_streak()）跟既有的「單純連續同方向天數」
判讀邏輯不同，維持這組邏輯完全獨立，方便日後整套抽換或單獨除錯，不會互相牽動。

目前只用於「觀察清單」的顯示(desktop/main_window.py)，之後可能擴充到庫存清單／個股資訊——
這裡的函式全部是純函式(吃已經算好的資料，不吃conn/不呼叫API)，方便任何呼叫端重用。
"""

from __future__ import annotations

import math
from datetime import date, timedelta

COLOR_BUY = "#CC0000"    # 紅：買超/增持相關
COLOR_SELL = "#006600"   # 綠：賣超/減持相關
COLOR_GRAY = "#888888"   # 灰：不明確/持平
COLOR_DEFAULT = "#000000"

# 連續買賣超狀態機的方向判定門檻：見classify_institutional_streak()。
_STREAK_CONFIRM_THRESHOLD = 3

# 大戶/散戶持股週變化的級距分類：見原JS的retailLevels/getWhale()。中間5個級距
# (100,001~1,000,000股，約100張~1000張)刻意不算進大戶也不算散戶，只有這裡列出的
# 才會被計入，這是原code的定義範圍，不是「大戶以外都是散戶」的簡化假設。
RETAIL_HOLDING_LEVELS = frozenset({
    "1-999", "1,000-5,000", "5,001-10,000", "10,001-15,000", "15,001-20,000",
    "20,001-30,000", "30,001-40,000", "40,001-50,000", "50,001-100,000",
})
WHALE_HOLDING_LEVEL = "more than 1,000,001"


def _js_round(x: float) -> int:
    """JS的Math.round()等價實作：floor(x+0.5)。Python內建round()是round-half-to-even
    (banker's rounding)，JS則是「.5一律無條件進位到正無窮方向」(Math.round(-2.5)===-2，
    Math.round(2.5)===3)，兩者在.5邊界會給出不同結果，這裡用floor(x+0.5)維持逐位元一致，
    不用Python的round()。"""
    return math.floor(x + 0.5)


def _js_round2(x: float) -> float:
    """對應原JS的Math.round(x*100)/100(四捨五入到小數點後2位)，一樣改用_js_round()。"""
    return _js_round(x * 100) / 100


def classify_institutional_streak(net_values_desc: list[float]) -> dict:
    """投信/外資連續買賣超天數判讀，對應原JS的calcContinuousLabel()——兩者(投信/外資)
    都呼叫同一個函式，差別只在傳入的net_values_desc不同。

    net_values_desc：依日期新到舊排序的每日(買-賣)淨額，index 0是最新一天(day0)。

    判斷邏輯(逐字對應原JS)：
    1. 空清單 → {"text": "", "color": 黑}(原JS: 無資料時回傳空字串，不是「方向未定」)。
    2. day0淨額為0 → {"text": "持平", "color": 灰}(獨立於「方向未定」的另一種狀態)。
    3. 從day0起算，同方向(遇到0視為中斷，不併入計算)連續天數N：
       - N>=3 → 「連買N天」/「連賣N天」，方向夠明確。
       - N<3 且已無更早歷史可比對(i>=len) → 仍顯示「連買N天」/「連賣N天」(資料不足，
         退回顯示簡單版，不強行判斷轉折)。
       - N<3 且緊接著是打平(0) → 「方向未定」。
       - N<3 且緊接著是反方向：再算反方向連續天數M(同樣遇0中斷)：
         - M>=3 → 「連M賣後轉買」/「連M買後轉賣」(顏色跟著「今天」的方向，不是M那段
           反方向的顏色)。
         - M<3 → 「方向未定」。
    回傳{"text": str, "color": str}。
    """
    if not net_values_desc:
        return {"text": "", "color": COLOR_DEFAULT}

    dirs = [1 if v > 0 else (-1 if v < 0 else 0) for v in net_values_desc]

    if dirs[0] == 0:
        return {"text": "持平", "color": COLOR_GRAY}

    today_dir = dirs[0]

    n = 0
    i = 0
    while i < len(dirs) and dirs[i] == today_dir:
        n += 1
        i += 1

    if n >= _STREAK_CONFIRM_THRESHOLD:
        if today_dir == 1:
            return {"text": f"連買{n}天", "color": COLOR_BUY}
        return {"text": f"連賣{n}天", "color": COLOR_SELL}

    if i >= len(dirs):
        if today_dir == 1:
            return {"text": f"連買{n}天", "color": COLOR_BUY}
        return {"text": f"連賣{n}天", "color": COLOR_SELL}

    if dirs[i] == 0:
        return {"text": "方向未定", "color": COLOR_GRAY}

    opp_dir = dirs[i]
    m = 0
    j = i
    while j < len(dirs) and dirs[j] == opp_dir:
        m += 1
        j += 1

    if m >= _STREAK_CONFIRM_THRESHOLD:
        if today_dir == 1:
            return {"text": f"連{m}賣後轉買", "color": COLOR_BUY}
        return {"text": f"連{m}買後轉賣", "color": COLOR_SELL}

    return {"text": "方向未定", "color": COLOR_GRAY}


def sum_institutional_flow_lots(net_values_desc: list[float], n: int) -> int:
    """最近n天(買-賣)加總，換算成「張」(除以1000，四捨五入)，對應原JS的sumDays()+
    呼叫端的Math.round()。

    net_values_desc：依日期新到舊排序的每日(買-賣)淨額(股數)。

    ⚠️ 資料筆數不足n天時，原JS用Array.slice(0,n)，超出範圍時不會報錯、直接用現有多少筆
    就加總多少筆(不是回傳None或拋例外)——這裡故意保留這個寬容行為，跟原code完全一致。
    """
    return _js_round(sum(net_values_desc[:n]) / 1000)


def _format_price_like_js(value: float) -> str:
    """JS的字串串接(number + "")對整數值不會顯示".0"(例如698.0顯示成"698")，Python的
    str(698.0)則會是"698.0"——這裡讓整數值的收盤價顯示格式跟原JS一致。

    ⚠️ 股價從SQLite REAL欄位讀回來，浮點數表示法可能讓88.1變成88.09999999999999
    這種樣子(浮點數無法精確表示大部分小數)，直接str()會把這個表示誤差原樣顯示出來
    (例如"P(88.0999984741211)")——TWSE股價實際最多到小數點後2位，這裡先四捨五入到
    2位再格式化，去除尾端多餘的0，才不會把浮點數誤差當成真正的價格顯示出來。"""
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def classify_ma_price_position(
    ma20_today: float | None, ma20_yesterday: float | None,
    ma60_today: float | None, ma60_yesterday: float | None,
    close_today: float, open_today: float,
) -> dict | None:
    """均線狀態(H欄)，對應原JS的updateMAStatus()——但這裡改吃「今天/昨天的MA20、MA60」
    (呼叫端從本專案自己的daily_indicators查兩天份即可)，不是原JS那樣重新抓K線用
    calcMA()/calcMABefore()從頭算。數學上完全等價(單日均線斜率比較)，但避開了原JS在
    資料筆數剛好卡在60天邊界時的一個小瑕疵(Array.slice()超出範圍不報錯，導致"昨天的
    MA60"其實只用了59筆算——這裡改用本專案daily_indicators既有的min_periods=n語意，
    資料不足時整欄直接視為None，不會有這個邊界誤差)。

    ma20_today>=ma20_yesterday視為「上揚」(原JS用>=，不是嚴格>，相等也算上揚)，MA60同理。
    「P(收盤價)」這一行的顏色依「今天是不是紅K」(close_today>=open_today)決定，不是
    依價格本身漲跌。3行依數值大小由大到小排序決定顯示順序(對應原JS的items.sort())。

    任一MA為None(資料不足)時回傳None，整欄視為無法判斷(對應原JS「資料<60筆直接return，
    這欄留白」)。
    """
    if ma20_today is None or ma20_yesterday is None or ma60_today is None or ma60_yesterday is None:
        return None

    ma20_up = ma20_today >= ma20_yesterday
    ma60_up = ma60_today >= ma60_yesterday
    is_red_k = close_today >= open_today

    items = [
        {"text": f"MA20 {'上揚' if ma20_up else '下彎'}", "value": ma20_today,
         "color": COLOR_BUY if ma20_up else COLOR_SELL},
        {"text": f"MA60 {'上揚' if ma60_up else '下彎'}", "value": ma60_today,
         "color": COLOR_BUY if ma60_up else COLOR_SELL},
        {"text": f"P({_format_price_like_js(close_today)})", "value": close_today,
         "color": COLOR_BUY if is_red_k else COLOR_SELL},
    ]
    items.sort(key=lambda item: item["value"], reverse=True)
    return {"lines": [{"text": item["text"], "color": item["color"]} for item in items]}


def _week_start(d: date) -> date:
    """該日期所屬週的週一，對應原JS的getWeekKey()——但這裡直接用date運算，不透過
    toISOString()的UTC轉換，避免原JS版本潛在的時區位移風險(Python這裡純日期運算，
    沒有時區換算，結果更穩定)。"""
    return d - timedelta(days=d.weekday())


def classify_weekly_volume_pattern(daily_rows_asc: list[dict]) -> dict | None:
    """週K型態(I欄，「大量K」判斷)，對應原JS的updateWeeklyKPattern()。

    daily_rows_asc：依日期由舊到新排序的[{"date": "YYYY-MM-DD", "high", "low",
    "close", "volume"}, ...]。

    邏輯：把日K依週一為起點分組成週K(每週的high/low取當週最大/最小，close取當週最後
    一個交易日的收盤價)，取「最近52週」(⚠️見本模組docstring第2點：這裡的52週是「把
    daily_rows_asc分組後取前52組」，不是「精確52個完整週K」——如果daily_rows_asc本身
    涵蓋的日曆天數不夠精確對齊52個完整週，会跟着不精確，這是原code的既有落差，故意
    照抄)裡，找出成交量總和最大的那一週當「大量K」參考基準；同成交量時(originally用
    reduce的>比較，非>=)，較新的週會贏(排序後在陣列前面的先比對到)。

    用目前最新一週的收盤價，跟「大量K」那一週的高/中值/低比較，分4類：
    大量高之上(>高) / 大量中值之上(>中值) / 大量中值之下(>=低) / 大量低之下(<低)。

    資料筆數<10視為不足，回傳None(對應原JS「資料<10筆直接return，這欄留白」)。
    """
    if len(daily_rows_asc) < 10:
        return None

    week_map: dict[date, dict] = {}
    for row in daily_rows_asc:
        d = date.fromisoformat(row["date"])
        wk = _week_start(d)
        entry = week_map.get(wk)
        if entry is None:
            entry = {"week_start": wk, "total_volume": 0, "high": row["high"], "low": row["low"], "close": row["close"]}
            week_map[wk] = entry
        entry["total_volume"] += row["volume"]
        if row["high"] > entry["high"]:
            entry["high"] = row["high"]
        if row["low"] < entry["low"]:
            entry["low"] = row["low"]
        entry["close"] = row["close"]  # 依日期由舊到新處理，最後一筆自然是該週最後交易日的收盤價

    weeks = sorted(week_map.values(), key=lambda w: w["week_start"], reverse=True)
    recent_52 = weeks[:52]

    max_vol_week = recent_52[0]
    for w in recent_52[1:]:
        if w["total_volume"] > max_vol_week["total_volume"]:
            max_vol_week = w

    high = max_vol_week["high"]
    low = max_vol_week["low"]
    mid = (high + low) / 2
    current_close = weeks[0]["close"]

    if current_close > high:
        pattern = "大量高之上"
    elif current_close > mid:
        pattern = "大量中值之上"
    elif current_close >= low:
        pattern = "大量中值之下"
    else:
        pattern = "大量低之下"

    return {"pattern": pattern, "reference_week_start": max_vol_week["week_start"].isoformat()}


def _whale_percent(rows_for_date: list[dict]) -> float:
    for r in rows_for_date:
        if r["holding_shares_level"] == WHALE_HOLDING_LEVEL:
            return r["percent"]
    return 0.0


def _retail_percent(rows_for_date: list[dict]) -> float:
    total = sum(r["percent"] for r in rows_for_date if r["holding_shares_level"] in RETAIL_HOLDING_LEVELS)
    return _js_round2(total)


def _diff_label(diff: float, subject: str, increase_color: str, decrease_color: str) -> dict:
    """大戶/散戶標籤共用的門檻分級：|diff|>=2%「爆買/爆賣」、>=1%「大增/大減」、
    >=0.5%「增持/減持」、其餘「小增/小減」，對應原JS的getWhaleLabel()/getRetailLabel()。
    兩者門檻/文字模板完全相同，差別只在顏色——大戶增持紅/散戶增持綠(散戶進場常被當
    反指標)，靠increase_color/decrease_color參數區分，呼叫端各自傳入相反的配色。"""
    abs_diff = abs(diff)
    sign = "+" if diff >= 0 else "-"
    color = increase_color if diff >= 0 else decrease_color
    if abs_diff >= 2:
        tier = "爆買" if diff >= 0 else "爆賣"
    elif abs_diff >= 1:
        tier = "大增" if diff >= 0 else "大減"
    elif abs_diff >= 0.5:
        tier = "增持" if diff >= 0 else "減持"
    else:
        tier = "小增" if diff >= 0 else "小減"
    return {"text": f"{subject}{tier} {sign}{abs_diff:.2f}%", "color": color}


def classify_holder_change(rows_by_date: dict[str, list[dict]]) -> dict | None:
    """大戶/散戶持股週變化(F/G欄)，對應原JS的updateHolderData()。

    rows_by_date：{date_str: [{"holding_shares_level", "percent"}, ...]}，同一個
    date_str底下是那一天(集保結算所公告日)全部級距的資料列。

    取資料裡最新的2個日期算差異(至少要有2個不同日期才能算，對應原JS「dates.length<2
    直接return」)——不是「今天」跟「昨天」，是資料裡實際存在的最新兩個公告日期，因為
    這份資料週更新，中間平常日不會有新的一筆。

    「大戶」只看holding_shares_level=="more than 1,000,001"(1000張以上)這一個級距的
    percent；「散戶」加總RETAIL_HOLDING_LEVELS這9個小級距的percent，四捨五入到小數點
    後2位。差異同樣四捨五入到小數點後2位，再依_diff_label()分級——大戶增持紅/減持綠，
    散戶則相反(增持綠/減持紅，因為散戶買進常被當反指標)。

    回傳{"whale": {"text","color"}, "retail": {"text","color"}}，資料不足回傳None。
    """
    dates = sorted(rows_by_date.keys(), reverse=True)
    if len(dates) < 2:
        return None
    latest_date, prev_date = dates[0], dates[1]

    whale_now = _whale_percent(rows_by_date[latest_date])
    whale_prev = _whale_percent(rows_by_date[prev_date])
    retail_now = _retail_percent(rows_by_date[latest_date])
    retail_prev = _retail_percent(rows_by_date[prev_date])

    whale_diff = _js_round2(whale_now - whale_prev)
    retail_diff = _js_round2(retail_now - retail_prev)

    return {
        "whale": _diff_label(whale_diff, "大戶", increase_color=COLOR_BUY, decrease_color=COLOR_SELL),
        "retail": _diff_label(retail_diff, "散戶", increase_color=COLOR_SELL, decrease_color=COLOR_BUY),
    }
