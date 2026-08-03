"""前端無關的圖表資料組裝層：把「從DB撈資料」跟「畫成Plotly Figure」這兩件事，從Streamlit
(`dashboard/app.py`)搬到這裡，讓PySide6桌面版(`desktop/`)也能呼叫同一套函式、算出完全相同
的`plotly.graph_objects.Figure`，不用在兩個UI框架裡各寫一份K線/均線/切線/支撐壓力的畫圖邏輯。

這裡的函式全部是純函式（吃`conn`/`DataFrame`，回傳`DataFrame`/`Figure`），完全沒有任何UI
框架呼叫——這正是2026-07-23架構調整（改回本機優先、PySide6桌面版）時，從`dashboard/app.py`
搬出來的部分（搬移前它們本來就已經不依賴`st.*`，只是定義位置卡在Streamlit檔案裡，其他前端
沒辦法import）。
"""

from __future__ import annotations

import re
from typing import Callable

import pandas as pd

from src.data import trading_calendar
from src.indicators.kd import compute_kd
from src.indicators.macd import compute_macd
from src.indicators.moving_average import DEFAULT_BULLISH_PERIODS, FULL_PERIODS, compute_ma_set, is_bullish_aligned
from src.indicators.parabolic_sar import compute_sar, sar_flipped_within
from src.patterns import chart_overlays

_CONFIDENCE_PATTERN = re.compile(r"（(\d+)%）")

MA_COLORS = {
    5: "#2e86de", 10: "#e67e22", 20: "#8e44ad",
    60: "#16a085", 120: "#7f8c8d", 240: "#b8860b",
}

# 短/中/長(日/週/月)趨勢分類器(見src/patterns/trend_state.py)專用的歷史長度——跟畫K線圖
# 用的顯示窗口(load_price_history預設120天)分開，因為週線/月線要重新取樣(resample)出夠多
# 根K棒才能讓轉折點演算法找到2組頭與2組底，120天日線只夠取樣出4~5根月線K棒，遠遠不夠。
#
# ⚠️ 2026-07-25教訓：這個值原本設750(約3年)，本機DB回補到約860個交易日(約3.4年)後，
# `load_price_history()`的`.tail(days)`會從「現有全部歷史」的尾端裁掉最舊的部分只留最近
# 750天——裁掉的那段剛好包含月線轉折點演算法需要的暖身期資料，反而讓2330的長期(月線)
# 從「多頭」退化回「轉折點不足」。這裡改成1000天(約4年)，暫時比本機DB實際累積的歷史
# (860天)多留一截緩衝，之後DB隨每日排程持續增長、遲早還是會超過1000天再次觸發裁切——
# 這是這個「trailing window」設計本來就有的效果(固定天數窗口，不是無限累積)，不是bug，
# 只是要留意窗口大小要抓得比「目前實際歷史」寬裕，不能抓得太剛好。
TREND_LOOKBACK_DAYS = 1000

TRENDLINE_LABELS = {
    "up_tangent": "上升切線", "down_tangent": "下降切線",
    "up_channel": "上升軌道線", "down_channel": "下降軌道線",
}
TRENDLINE_STYLES = {
    "up_tangent": {"color": "#1565c0", "dash": "solid"},
    "down_tangent": {"color": "#d84315", "dash": "solid"},
    "up_channel": {"color": "#64b5f6", "dash": "dash"},
    "down_channel": {"color": "#ffab40", "dash": "dash"},
}
# 每種切線預設的role(未被跌破/突破時)，用來偵測R-LINE-11/12的角色互換是否發生過
# （up_channel/down_channel目前的實作沒有另外套用跌破/突破檢查，role固定不變）。
TRENDLINE_DEFAULT_ROLE = {
    "up_tangent": "support", "down_tangent": "resistance",
    "up_channel": "resistance", "down_channel": "support",
}
SR_ROLE_COLORS = {"支撐": "#16a085", "壓力": "#c0392b"}


def compute_ma_bullish_flags(
    conn, stock_ids: list[str], periods: tuple[int, ...] = DEFAULT_BULLISH_PERIODS, lookback_days: int | None = None,
    as_of_date: str | None = None,
) -> dict[str, bool]:
    """對每檔股票算出「均線多頭排列」(依`periods`由短到長排列，例如MA5>MA10>MA20，或延伸到
    書中「多線多排」的MA120/MA240)是否成立，取`as_of_date`(或None時取最新一筆已知資料)為準。
    只在候選清單篩選器實際勾選這個條件時才呼叫(見CANDIDATE_FILTERS)，不是每次載入候選清單
    都算，避免候選股數量變多時拖慢清單載入速度。

    lookback_days未指定時採`max(periods)`——延伸到MA120/MA240的篩選需要對應天數的收盤價
    才能算出最長那條均線，不能沿用MA5/10/20版本的60天預設值。
    資料不足以算出最長均線時該檔股票視為不成立(False)，不是拋例外或跳過。

    as_of_date：見`_fetch_recent_columns_batched`的說明——候選清單可以瀏覽「過去某一天」
    (不是DB目前最新一天)的紀錄，這裡一定要傳入該候選清單的日期，不能讓均線用「DB目前最新
    資料」算，否則瀏覽舊日期候選清單時會混入該日期之後才發生的價格變化。
    """
    if not stock_ids:
        return {}
    if lookback_days is None:
        lookback_days = max(periods)
    closes_by_stock = _fetch_recent_columns_batched(conn, stock_ids, ["close"], lookback_days, as_of_date=as_of_date)
    flags: dict[str, bool] = {}
    for stock_id in stock_ids:
        closes = closes_by_stock.get(stock_id, {}).get("close", [])
        if len(closes) < max(periods):
            flags[stock_id] = False
            continue
        close_series = pd.Series(closes)
        ma_frame = compute_ma_set(close_series, periods=periods)
        flags[stock_id] = bool(is_bullish_aligned(ma_frame, periods=periods).iloc[-1])
    return flags


def _fetch_recent_columns_batched(
    conn, stock_ids: list[str], columns: list[str], lookback_days: int, as_of_date: str | None = None,
) -> dict[str, dict[str, list[float]]]:
    """批次撈取多檔股票「截至`as_of_date`為止」最近`lookback_days`個交易日的指定欄位，
    取代逐檔各自查詢一次(N+1查詢)——候選清單股數多、又同時勾選多個篩選條件時，逐檔查詢的
    往返成本會疊加成明顯的等待時間(2026-08-01效能調校：使用者回報篩選花的時間很多)。改成
    一次查全部、用stock_id分組，SQLite/Turso執行單一大查詢的成本遠低於N次小查詢的往返總和。

    回傳{stock_id: {column: [值...]}}，每檔股票的清單依日期由舊到新排序(呼叫端原本
    逐檔查詢時就是這個順序，這裡維持一致，不用另外反轉)；查無資料的股票不會出現在
    回傳dict裡(呼叫端用.get(stock_id, {})取用，天然對應「資料不足視為不成立」)。

    ⚠️ 2026-08-01第一版曾經改用「用date字串概略限制查詢範圍」(以`date.today()`往前
    推算日曆天數當WHERE條件)，結果讓部分回測/測試用的歷史資料(日期本來就跟「現在」
    無關，例如整批補在2025年的合成測試資料)被這個跟真實現在時間綁死的WHERE條件誤
    篩掉，算出跟舊版逐檔查詢不一致的結果——量測「最近N筆」不該用「現在的日曆時間」
    當基準，跟資料本身實際涵蓋的時間範圍無關。改用SQL window function在資料庫端
    直接算「每檔股票依日期新到舊的第幾筆」，只取前lookback_days筆，正確且不依賴
    「現在」是哪一天。

    ⚠️ 2026-08-01第二個bug：上面那版拿掉了日期WHERE條件之後，變成永遠抓「DB目前
    最新的lookback_days筆」，完全沒有考慮候選清單本身可能是在瀏覽「過去某一天」
    (例如DB已經累積到8/1，但使用者切候選清單日期選單看7/30那天)。SAR這種路徑相關
    (path-dependent)指標的翻轉判斷因此會用到「7/30之後才發生」的價格資料，算出跟
    真正「以7/30為基準」不一致的結果(使用者拿ref-project的7/30計算結果比對，5檔
    應該偵測到SAR多頭翻轉的股票只有1檔對得上，其餘因為多算了7/31那天而把翻轉點
    往後推移了一天，被「1天內翻轉」的篩選條件排除)。新增`as_of_date`參數，有傳入時
    在WHERE子句加上`date <= as_of_date`，讓「篩選依據的最新一筆資料」跟「候選清單
    正在瀏覽的日期」一致，不是跟「呼叫當下DB實際累積到哪一天」綁在一起。
    """
    placeholders = ",".join("?" * len(stock_ids))
    column_list = ", ".join(columns)
    date_clause = "AND date <= ?" if as_of_date is not None else ""
    params: list = [*stock_ids]
    if as_of_date is not None:
        params.append(as_of_date)
    params.append(lookback_days)
    cur = conn.execute(
        f"""
        SELECT stock_id, {column_list} FROM (
            SELECT stock_id, {column_list},
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
            FROM stock_prices
            WHERE stock_id IN ({placeholders}) {date_clause}
        )
        WHERE rn <= ?
        ORDER BY stock_id, rn DESC
        """,
        params,
    )
    result: dict[str, dict[str, list[float]]] = {}
    for row in cur.fetchall():
        stock_id = row[0]
        per_stock = result.setdefault(stock_id, {col: [] for col in columns})
        for col, value in zip(columns, row[1:]):
            per_stock[col].append(value)
    return result


def _ma_bullish_filter(periods: tuple[int, ...]) -> Callable[[object, list[str], str | None], dict[str, bool]]:
    return lambda conn, stock_ids, as_of_date: load_ma_bullish_flags_from_table(
        conn, stock_ids, periods=periods, as_of_date=as_of_date
    )


# 候選清單篩選器registry：{顯示標籤: 計算函式(conn, stock_ids, as_of_date) -> {stock_id: bool}}。
# 之後要加其他篩選條件(例如量能/型態)時，在這裡多加一組標籤/函式即可，兩個前端
# (dashboard/app.py、desktop/main_window.py)都是迴圈讀取這個registry動態產生勾選框、
# 不用另外改UI程式碼。
CANDIDATE_FILTERS: dict[str, Callable[[object, list[str], str | None], dict[str, bool]]] = {
    "均線多頭排列（MA5>MA10>MA20）": _ma_bullish_filter((5, 10, 20)),
    "均線多頭排列（...>MA120）": _ma_bullish_filter((5, 10, 20, 120)),
    "均線多頭排列（...>MA240）": _ma_bullish_filter((5, 10, 20, 120, 240)),
}

# 各篩選器預設勾選狀態：MA5>MA10>MA20是書中做多的基本地基條件，預設打勾；延伸到
# MA120/MA240的「多線多排」條件較嚴格(篩到的股票會少很多)，預設不勾，避免使用者
# 一開啟候選清單就發現空空如也、誤以為系統壞掉。未列在這裡的篩選標籤視為預設不勾。
CANDIDATE_FILTER_DEFAULTS: dict[str, bool] = {
    "均線多頭排列（MA5>MA10>MA20）": True,
    "均線多頭排列（...>MA120）": False,
    "均線多頭排列（...>MA240）": False,
}


# SAR翻轉篩選(勾選框+多頭/空頭下拉+翻轉天數輸入)的歷史回看天數：SAR是逐日累積、跟路徑
# 相關的指標(加速因子隨趨勢延續而增加)，需要足夠長的暖身期讓狀態收斂穩定，抓太短會讓早期
# 任意的初始多空種子影響到「目前」的判斷。抓250個交易日(約1年)，跟`compute_ma_bullish_flags`
# 用「勾選才查」的精神一致，不是每次載入候選清單都算。
SAR_FLIP_LOOKBACK_DAYS = 250

# 候選清單「篩選方法」(SAR翻轉/朱家泓技術分析)預設是否啟用、SAR翻轉的預設方向/天數：
# 跟CANDIDATE_FILTER_DEFAULTS同樣的精神，集中定義成常數，desktop/main_window.py的
# UI初始狀態、scripts/daily_pipeline.py的LINE/Email通知內容都讀同一份，確保「畫面
# 預設顯示的候選清單」跟「主動推播的內容」是同一個集合。⚠️ 2026-08-03修正：先前
# 兩處分別各自維護一份預設值(UI用widget初始狀態、通知用run_screen_and_store()完全
# 沒套用這些篩選的原始daily_candidates全部內容)，導致使用者收到的LINE通知涵蓋的
# 股票數比打開UI看到的候選清單多很多(通知端沒有另外要求MA5>10>20+SAR翻轉)，使用者
# 回報「發LINE通知的清單與候選清單列出來的沒有對齊」後才發現這個落差。
CANDIDATE_SAR_FLIP_ENABLED_DEFAULT = True
CANDIDATE_SAR_FLIP_OPTION_DEFAULT: dict = {"direction": "多頭", "within_days": 1}
CANDIDATE_ZHU_RULE_ONLY_DEFAULT = True


def compute_sar_flip_flags(
    conn, stock_ids: list[str], direction: str = "多頭", within_days: int = 1,
    lookback_days: int = SAR_FLIP_LOOKBACK_DAYS, as_of_date: str | None = None,
) -> dict[str, bool]:
    """對每檔股票算出「SAR是否翻轉為`direction`(多頭/空頭)、且發生在最近`within_days`天以內」
    (見`src.indicators.parabolic_sar.sar_flipped_within`，含引用來源說明)。只在候選清單篩選器
    實際勾選SAR翻轉條件時才呼叫(見`apply_candidate_filters`的`sar_flip_option`參數)。歷史資料
    不足3天(compute_sar至少需要2天以上才有意義)的股票視為不成立(False)，不拋例外。

    as_of_date：見`_fetch_recent_columns_batched`的說明——SAR是路徑相關(path-dependent)
    指標，多算或少算一天都可能讓翻轉判斷的日期整個往後推移，候選清單瀏覽「過去某一天」時
    一定要把這個日期傳進來，不能讓SAR用DB目前最新資料算。
    """
    if not stock_ids:
        return {}
    rows_by_stock = _fetch_recent_columns_batched(
        conn, stock_ids, ["high", "low", "close"], lookback_days, as_of_date=as_of_date
    )
    flags: dict[str, bool] = {}
    for stock_id in stock_ids:
        per_stock = rows_by_stock.get(stock_id, {})
        high_vals = per_stock.get("high", [])
        if len(high_vals) < 3:
            flags[stock_id] = False
            continue
        high = pd.Series(high_vals)
        low = pd.Series(per_stock["low"])
        close = pd.Series(per_stock["close"])
        sar_bull, _ = compute_sar(high, low, close)
        flags[stock_id] = sar_flipped_within(sar_bull, direction=direction, within_days=within_days)
    return flags


_MA_COLUMN_BY_PERIOD = {5: "ma5", 10: "ma10", 20: "ma20", 60: "ma60", 120: "ma120", 240: "ma240"}


def load_ma_bullish_flags_from_table(
    conn, stock_ids: list[str], periods: tuple[int, ...], as_of_date: str,
) -> dict[str, bool]:
    """`compute_ma_bullish_flags()`的查表版本：候選清單「篩選條件」原本每次套用篩選都
    對`stock_prices`即時重算均線，改成查`daily_indicators`(見`src/screener/
    indicator_precompute.py`跟`src/data/schema.sql`的說明)——2026-08-02新增，取代
    `CANDIDATE_FILTERS`裡原本呼叫`compute_ma_bullish_flags()`的路徑。

    `compute_ma_bullish_flags()`本身不變、不刪除：它是`indicator_precompute.py`背後
    真正的計算邏輯來源(用來產生要寫進`daily_indicators`的值)，也保留給既有測試使用。

    查無紀錄的股票(還沒回補、或當天沒有價格資料)視為不成立(False)，跟即時計算版本
    「資料不足視為不成立」的既有語意一致，不拋例外。
    """
    if not stock_ids:
        return {}
    placeholders = ",".join("?" * len(stock_ids))
    columns = [_MA_COLUMN_BY_PERIOD[n] for n in periods]
    column_list = ", ".join(columns)
    cur = conn.execute(
        f"SELECT stock_id, {column_list} FROM daily_indicators WHERE stock_id IN ({placeholders}) AND date = ?",
        [*stock_ids, as_of_date],
    )
    flags: dict[str, bool] = {stock_id: False for stock_id in stock_ids}
    for row in cur.fetchall():
        stock_id = row[0]
        values = row[1:]
        if any(v is None for v in values):
            continue
        flags[stock_id] = all(shorter > longer for shorter, longer in zip(values, values[1:]))
    return flags


def load_sar_flip_flags_from_table(
    conn, stock_ids: list[str], direction: str, within_days: int, as_of_date: str,
) -> dict[str, bool]:
    """`compute_sar_flip_flags()`的查表版本：候選清單「篩選方法」的SAR翻轉原本每次套用
    篩選都對`stock_prices`即時重算(SAR是逐日累積、無法簡單向量化的指標，全市場~2300檔
    要15~35秒)，改成查`daily_indicators`(見`src/screener/indicator_precompute.py`跟
    `src/data/schema.sql`的說明)——2026-08-02新增，取代`apply_candidate_filters()`裡
    原本呼叫`compute_sar_flip_flags()`的路徑。

    `compute_sar_flip_flags()`本身不變、不刪除，理由同`load_ma_bullish_flags_from_
    table()`。查無紀錄的股票(還沒回補、或資料不足<3天算不出SAR)視為不成立(False)。
    """
    if not stock_ids:
        return {}
    placeholders = ",".join("?" * len(stock_ids))
    cur = conn.execute(
        f"""
        SELECT stock_id, sar_is_bull, sar_flip_days_ago FROM daily_indicators
        WHERE stock_id IN ({placeholders}) AND date = ?
        """,
        [*stock_ids, as_of_date],
    )
    wants_bull = direction == "多頭"
    flags: dict[str, bool] = {stock_id: False for stock_id in stock_ids}
    for stock_id, sar_is_bull, sar_flip_days_ago in cur.fetchall():
        if sar_is_bull is None or sar_flip_days_ago is None:
            continue
        flags[stock_id] = bool(sar_is_bull) == wants_bull and sar_flip_days_ago <= within_days
    return flags


def apply_candidate_filters(
    conn, candidates_df: pd.DataFrame, active_filter_labels: list[str],
    sar_flip_option: dict | None = None, zhu_rule_only: bool = False, as_of_date: str | None = None,
) -> pd.DataFrame:
    """依勾選的篩選標籤(CANDIDATE_FILTERS的key)逐一AND套用，回傳過濾後的候選清單。
    未勾選任何篩選(active_filter_labels為空、sar_flip_option為None、zhu_rule_only為False)時
    原樣回傳，不做任何運算。

    2026-08-02使用者釐清語意後改版：`candidates_df`現在應該傳入`load_stock_universe_
    for_date()`回傳的「全市場」DataFrame(不是只有daily_candidates裡已經觸發規則的股票)。
    「篩選條件」(CANDIDATE_FILTERS的均線多排)、「篩選方法」的SAR翻轉、朱家泓技術分析
    三者是彼此獨立、可任意組合的AND條件，不是「候選清單本來就限定在這個範圍」的基礎池：
        - 只勾均線多排 → 全市場均線掃描(不要求當天有觸發任何朱家泓規則)。
        - 均線多排+SAR翻轉 → 全市場「均線多排 且 SAR翻轉」掃描。
        - 均線多排+SAR翻轉+朱家泓技術分析 → 在上面的基礎上，再要求當天有出現在
          daily_candidates(等同於過去舊版「候選清單」的範圍)。
    因此zhu_rule_only不再是判斷signal_name字串內容(那個規則ID比對法在候選清單100%
    來自朱家泓規則的舊架構下恆為True，其實沒有篩選到任何東西)，改成單純檢查
    `candidates_df["signal_name"]`是否非空(是否出現在當天的daily_candidates)。

    sar_flip_option：SAR翻轉篩選的參數，格式{"direction": "多頭"|"空頭", "within_days": int}，
    傳None代表沒有勾選這個條件。這個篩選條件的UI是「勾選框+方向下拉+天數輸入」三個元件綁在
    一起，不是單純的勾選框，不適合塞進`CANDIDATE_FILTERS`那種「label -> 純checkbox」的
    registry，因此用獨立參數傳入，不是加進`active_filter_labels`清單裡。

    as_of_date：呼叫端(兩個前端)應該一律傳入`load_stock_universe_for_date()`回傳的候選
    清單日期(不是None)，確保均線/SAR這些依`stock_prices`重新計算的篩選條件是「以候選清單
    正在瀏覽的那一天為準」，不是「以DB目前實際累積到哪一天為準」。⚠️ 2026-08-01發現：
    沒有這個參數時，瀏覽過去日期的候選清單(DB隨每日排程持續往後累積資料後)會混入該日期
    之後才發生的價格變化，SAR這種路徑相關指標尤其明顯——同一天的翻轉判斷會因為多算了
    之後幾天的資料，被誤判成「已經是幾天前翻轉的」而被「N天內翻轉」篩選條件排除掉。見
    `_fetch_recent_columns_batched`的詳細說明。

    回傳結果裡，靠均線/SAR條件篩出來、但當天沒有觸發任何朱家泓規則(signal_name原本是
    None)的股票，「訊號」欄位會補上「符合條件本身」的描述文字(例如「均線多頭排列
    （MA5>MA10>MA20）」)，不是留空——使用者要求這樣才知道這檔股票是「為什麼」出現在
    清單裡，不是無中生有；已經有真正規則訊號的股票維持原樣，不會被覆蓋。
    """
    if candidates_df.empty:
        return candidates_df
    if not active_filter_labels and sar_flip_option is None and not zhu_rule_only:
        return candidates_df
    mask = pd.Series(True, index=candidates_df.index)
    matched_condition_labels: list[str] = []

    # ⚠️ 2026-08-02效能修正(第一版)：候選清單基礎池改成全市場(~2000+檔)後，均線/SAR
    # 這類條件如果無條件對candidates_df裡「當下的全部stock_id」算一次，即使最後
    # zhu_rule_only會篩掉九成結果，還是得先付出對全市場算一次的成本——SAR尤其明顯
    # (逐日累積、沒辦法簡單向量化)，當時實測連預設(勾朱家泓技術分析、等同舊版「候選
    # 清單=已觸發規則的股票」)都要30幾秒。改成zhu_rule_only(純欄位比對，不查DB、幾乎
    # 零成本)優先套用、把stock_ids先縮小，後面才把縮小後的stock_ids交給CANDIDATE_
    # FILTERS/SAR條件計算——最後篩出的結果集合不變(AND滿足交換律)，只是運算量減少。
    #
    # 2026-08-02第二版(當天稍晚)：均線/SAR進一步改成查`daily_indicators`表(見
    # `load_ma_bullish_flags_from_table()`/`load_sar_flip_flags_from_table()`)，
    # 不再即時對`stock_prices`重算，即使對全市場~2300檔查詢也只是單一次索引查詢，
    # 效能問題已經從根本解決；這裡保留先套用zhu_rule_only縮小stock_ids的做法，
    # 是因為多一個縮小的IN子句仍然是免費的效能提升，且不影響正確性，沒有理由拿掉。
    if zhu_rule_only:
        mask &= candidates_df["signal_name"].notna()

    stock_ids = candidates_df.loc[mask, "stock_id"].tolist()
    for label in active_filter_labels or []:
        flags = CANDIDATE_FILTERS[label](conn, stock_ids, as_of_date)
        mask &= candidates_df["stock_id"].map(flags).fillna(False)
        matched_condition_labels.append(label)
        stock_ids = candidates_df.loc[mask, "stock_id"].tolist()
    if sar_flip_option is not None:
        direction = sar_flip_option.get("direction", "多頭")
        within_days = sar_flip_option.get("within_days", 1)
        flags = load_sar_flip_flags_from_table(
            conn, stock_ids, direction=direction, within_days=within_days, as_of_date=as_of_date,
        )
        mask &= candidates_df["stock_id"].map(flags).fillna(False)
        matched_condition_labels.append(f"SAR翻轉（{direction}，{within_days}天內）")

    result = candidates_df[mask].reset_index(drop=True)
    if matched_condition_labels and "signal_name" in result.columns:
        blank_signal = result["signal_name"].isna()
        if blank_signal.any():
            result.loc[blank_signal, "signal_name"] = "\n".join(matched_condition_labels)
    return result


def list_candidate_dates(conn) -> list[str]:
    """回傳daily_candidates裡所有有紀錄的日期，由新到舊排序，供候選清單的日期選單使用。"""
    cur = conn.execute("SELECT DISTINCT date FROM daily_candidates ORDER BY date DESC")
    return [row[0] for row in cur.fetchall()]


def list_price_dates(conn) -> list[str]:
    """回傳stock_prices裡所有有紀錄的日期，由新到舊排序——跟list_candidate_dates()不同，
    這裡不受daily_candidates(是否觸發過朱家泓規則)限制，供「產業輪動」分頁的日期選單
    使用(產業輪動只要有股價資料就能算，不需要當天有觸發任何規則)。
    """
    cur = conn.execute("SELECT DISTINCT date FROM stock_prices ORDER BY date DESC")
    return [row[0] for row in cur.fetchall()]


def list_industries(conn) -> list[str]:
    """回傳stocks表裡所有相異的產業別分類，由字串排序，供「選股」分頁的產業別篩選下拉
    選單、「產業輪動」分頁使用。industry為NULL的股票不列入(篩選下拉選單只需要真正存在
    的分類值)。
    """
    cur = conn.execute("SELECT DISTINCT industry FROM stocks WHERE industry IS NOT NULL ORDER BY industry")
    return [row[0] for row in cur.fetchall()]


def get_latest_update_time(conn) -> str | None:
    """回傳stocks表裡最新的updated_at時間戳(ISO8601字串)，代表DB目前最新一次成功寫入
    股價資料的時間——TWSE/TPEx兩條抓取路徑(scripts/daily_pipeline.py的fetch_today_twse/
    fetch_today_tpex)每次成功抓到資料都會upsert_stocks()更新這個欄位，不管是盤中即時價
    備援還是官方收盤價，都算數。查無任何股票資料回傳None。供兩個前端畫面右上角顯示
    「資料更新至：...」用。
    """
    row = conn.execute("SELECT MAX(updated_at) FROM stocks").fetchone()
    return row[0] if row is not None else None


def get_latest_candidate_update_time(conn) -> str | None:
    """回傳daily_candidates表裡最新一筆的created_at時間戳(ISO8601字串)，代表「立即重新篩選」
    或每日排程最近一次成功寫入候選清單的時間——跟get_latest_update_time()的股價DB更新時間
    是兩件事：股價可能已經更新到今天，但候選清單是幾分鐘前手動重篩才產生的，兩個時間點不會
    永遠一致，因此兩個前端的「資料更新至」要分開顯示，不能只顯示其中一個。查無任何候選紀錄
    時回傳None。
    """
    row = conn.execute("SELECT MAX(created_at) FROM daily_candidates").fetchone()
    return row[0] if row is not None else None


def load_stock_universe_for_date(
    conn, target_date: str | None = None, market: str | None = None,
) -> tuple[pd.DataFrame, str | None, bool]:
    """回傳 (指定日期「全市場」股票的DataFrame, 該日期字串, is_intraday)；target_date為
    None時取daily_candidates裡最新一天。尚無任何daily_candidates紀錄、或指定日期當天
    查無任何股票價格資料時回傳(空DataFrame, 對應日期字串或None, False)。

    market：可選的市場篩選("TWSE"/"TPEx")，None代表不限制(排除大盤本身即可)——
    2026-08-02新增，供桌面版「選股」分頁的「市場：全部/上市/上櫃」篩選使用。

    DataFrame欄位除了原本的stock_id/name/industry/signal_name/entry_price/stop_loss/
    pct_change/volume，2026-08-02新增：
    - close：當天收盤價。
    - sar_value/sar_status/sar_distance_pct：SAR相關欄位，查`daily_indicators`表
      (LEFT JOIN，還沒回補到這張表的股票這幾欄是None，不影響整列被排除)。sar_status
      是"多頭"/"空頭"文字(對應sar_is_bull)；sar_distance_pct是SAR值與收盤價的百分比
      距離`(sar_value - close) / close * 100`，數值越接近0代表股價越接近翻轉點，公式
      沿用`ref-project/tw_stock_analyzer/src/core/stock_scanner.py`既有的「SAR距離%」
      定義，不是這裡另外發明的指標。

    2026-08-02使用者釐清語意後改版(舊名`load_candidates_for_date`)：候選清單的篩選
    列(「篩選條件」的均線多排、「篩選方法」的SAR翻轉／朱家泓技術分析)彼此是獨立的AND
    條件，不是「候選清單一開始就限定在daily_candidates(已經觸發某條朱家泓規則的股票)
    範圍內」——使用者要求勾MA5>MA10>MA20+SAR、但不勾朱家泓技術分析時，等同對全市場做
    「均線多排+SAR翻轉」掃描，不受「當天有沒有觸發朱家泓規則」限制；只有勾選朱家泓
    技術分析才會額外要求「當天有出現在daily_candidates」(見`apply_candidate_filters`)。
    因此這裡改成以`stocks`(排除market='INDEX'的大盤)為主表，用INNER JOIN當天的
    `stock_prices`(當天沒有價格資料的股票，MA/SAR/漲跌幅本來就無從算起，直接排除，不是
    LEFT JOIN留著全部NaN)，再合併當天`daily_candidates`裡「這檔股票有沒有觸發規則、
    觸發了哪些」的資訊(沒觸發的股票signal_name/entry_price/stop_loss是None，由
    `apply_candidate_filters`視情況補上「符合條件本身」的描述文字)。

    is_intraday：這天的資料是否來自yfinance盤中即時價備援(True)而非TWSE官方最終收盤價
    (False)，讀daily_data_status表(見schema.sql與scripts/daily_pipeline.py的
    fetch_today_twse())；查無紀錄(例如這個功能上線前就有的歷史資料)一律視為False，不
    特別標示。呼叫端(兩個前端)依此顯示「尚未收盤」提示，讓使用者知道這天的訊號可能還會
    隨收盤價格微調而改變。

    同一檔股票如果當天同時符合多條規則(daily_candidates裡有多筆同stock_id、不同
    signal_name的紀錄，例如同時觸發R-TREND-14跟R-SCREEN-15)，這裡會合併成一列顯示，
    不是一條規則一列——signal_name欄位用「\n」換行字元分隔多條規則的內容(而不是逗號/
    頓號這類同一行內的分隔符)，讓桌面版(desktop/main_window.py開了word wrap +
    resizeRowsToContents)能在同一格內分成好幾行顯示，一條規則一行，比擠在同一行裡好讀。
    ⚠️ Streamlit的`st.dataframe`不支援儲存格內換行(實測\n會被吃成空白、HTML的<br>則會
    顯示成字面文字)，這是該元件本身的限制；用\n分隔在那邊會退化成單行、規則之間用空白
    隔開，不是bug，是目前Streamlit這個次要前端能接受的降級效果。
    entry_price/stop_loss取合併前第一筆的值：目前已接上的規則都是用同一天的收盤價/同一套
    停損公式(bull_short_term_stop_loss)，理論上同一檔股票不管觸發幾條規則，算出來的值
    本來就會相同；之後如果加入用不同公式的規則導致同一天算出不同的進場價/停損價，這裡
    仍然只顯示第一筆，不特別提示「多個不同數值」，避免為了目前用不到的情境過度設計UI。

    漲跌幅/成交量：跟訊號本身無關、是「當天這檔股票實際的價量表現」，用stock_prices表
    當天的收盤價與前一個交易日(依date字串排序取最近一筆更早的資料，非日曆天)的收盤價
    算出百分比，不用stock_prices.spread欄位——spread是否有值取決於資料來源(yfinance
    補的historical資料一律是NULL，見src/data/yfinance_client.py)，不可靠，直接用兩天
    收盤價自己算才能保證每一列都有值。
    """
    if target_date is None:
        target_date = conn.execute("SELECT MAX(date) FROM daily_candidates").fetchone()[0]
        if target_date is None:
            return pd.DataFrame(), None, False

    status_row = conn.execute("SELECT is_intraday FROM daily_data_status WHERE date = ?", (target_date,)).fetchone()
    is_intraday = bool(status_row[0]) if status_row is not None else False

    candidate_rows = conn.execute(
        "SELECT stock_id, signal_name, entry_price, stop_loss FROM daily_candidates WHERE date = ? ORDER BY stock_id, created_at",
        (target_date,),
    ).fetchall()
    candidates_by_stock: dict[str, dict] = {}
    for stock_id, signal_name, entry_price, stop_loss in candidate_rows:
        entry = candidates_by_stock.setdefault(
            stock_id, {"signal_names": [], "entry_price": entry_price, "stop_loss": stop_loss}
        )
        entry["signal_names"].append(signal_name)

    market_clause = " AND s.market = ?" if market else ""
    params: list = [target_date, target_date]
    if market:
        params.append(market)
    cur = conn.execute(
        f"""
        SELECT s.stock_id, s.name, s.industry, sp.close AS today_close, sp.volume AS today_volume,
               (SELECT sp2.close FROM stock_prices sp2
                WHERE sp2.stock_id = s.stock_id AND sp2.date < ?
                ORDER BY sp2.date DESC LIMIT 1) AS prev_close,
               di.sar_value AS sar_value, di.sar_is_bull AS sar_is_bull
        FROM stocks s
        JOIN stock_prices sp ON sp.stock_id = s.stock_id AND sp.date = ?
        LEFT JOIN daily_indicators di ON di.stock_id = s.stock_id AND di.date = sp.date
        WHERE s.market != 'INDEX'{market_clause}
        """,
        params,
    )
    columns = [d[0] for d in cur.description]
    raw_df = pd.DataFrame(cur.fetchall(), columns=columns)
    if raw_df.empty:
        return raw_df, target_date, is_intraday

    raw_df["pct_change"] = (raw_df["today_close"] - raw_df["prev_close"]) / raw_df["prev_close"] * 100

    rows = []
    for _, row in raw_df.iterrows():
        stock_id = row["stock_id"]
        cand = candidates_by_stock.get(stock_id)
        signal_name = "\n".join(cand["signal_names"]) if cand else None
        sar_value = row["sar_value"] if pd.notna(row["sar_value"]) else None
        sar_is_bull = row["sar_is_bull"] if pd.notna(row["sar_is_bull"]) else None
        sar_status = ("多頭" if sar_is_bull else "空頭") if sar_is_bull is not None else None
        today_close = row["today_close"]
        sar_distance_pct = (
            (sar_value - today_close) / today_close * 100
            if sar_value is not None and pd.notna(today_close) and today_close != 0
            else None
        )
        rows.append({
            "stock_id": stock_id,
            "name": row["name"],
            "industry": row["industry"],
            "signal_name": signal_name,
            "close": today_close,
            "entry_price": cand["entry_price"] if cand else None,
            "stop_loss": cand["stop_loss"] if cand else None,
            "pct_change": row["pct_change"],
            "volume": row["today_volume"],
            "sar_value": sar_value,
            "sar_status": sar_status,
            "sar_distance_pct": sar_distance_pct,
            # 排序用：這檔股票當天符合的所有規則信心分數加總，觸發越多條規則、信心分數
            # 越高的股票排越前面——不是最終顯示欄位，排序完就丟棄，不影響回傳的欄位結構。
            # 沒有觸發任何規則(全市場掃描補進來)的股票signal_name是None，加總視為0。
            "_confidence_sum": sum(int(m) for m in _CONFIDENCE_PATTERN.findall(signal_name)) if signal_name else 0,
        })
    universe_df = pd.DataFrame(
        rows,
        columns=[
            "stock_id", "name", "industry", "signal_name", "close", "entry_price", "stop_loss",
            "pct_change", "volume", "sar_value", "sar_status", "sar_distance_pct", "_confidence_sum",
        ],
    )
    # 預設排序：信心分數加總由高到低；同分時退回股票代號排序，確保結果穩定、可重現。
    universe_df = universe_df.sort_values(
        ["_confidence_sum", "stock_id"], ascending=[False, True]
    ).drop(columns="_confidence_sum").reset_index(drop=True)
    return universe_df, target_date, is_intraday


def load_industry_rotation(conn, target_date: str | None = None) -> tuple[pd.DataFrame, str | None]:
    """回傳(指定日期各產業別的成交量加總/平均漲跌幅/股票數DataFrame, 該日期字串)，供
    「產業輪動」分頁使用——想看某一天資金比較集中往哪個產業移動，用成交量加總判斷資金
    集中度、平均漲跌幅判斷該產業當天整體強弱。target_date為None時取stock_prices裡
    最新一天(不是daily_candidates最新一天——產業輪動只要有股價資料就能算，跟當天有沒有
    觸發朱家泓規則無關)。查無股價資料時回傳(空DataFrame, None)。

    JOIN寫法沿用load_stock_universe_for_date()同一套(stocks INNER JOIN當天
    stock_prices + 前一日收盤價的相關子查詢算漲跌幅)，差別是這裡最後依industry分組
    加總，不是逐股列出。平均漲跌幅用簡單平均，不是市值加權——這個專案目前沒有市值資料。

    ⚠️ 已知資料品質限制(這裡先如實呈現原始industry分組，不做正規化)：`stocks.industry`
    裡有ETF/ETN/存託憑證/創新板股票等約8種非個股分類，以及少數同義產業因TWSE/TPEx
    命名差異拆成兩個分類(例如「數位雲端」vs「數位雲端類」)，會讓「加總」的產業邊界
    不夠精確，之後有需要再處理，不在這裡用不成熟的規則硬湊。
    """
    if target_date is None:
        target_date = conn.execute("SELECT MAX(date) FROM stock_prices").fetchone()[0]
        if target_date is None:
            return pd.DataFrame(), None

    cur = conn.execute(
        """
        SELECT s.industry AS industry, sp.volume AS volume,
               (sp.close - (SELECT sp2.close FROM stock_prices sp2
                             WHERE sp2.stock_id = s.stock_id AND sp2.date < ?
                             ORDER BY sp2.date DESC LIMIT 1))
               / (SELECT sp2.close FROM stock_prices sp2
                  WHERE sp2.stock_id = s.stock_id AND sp2.date < ?
                  ORDER BY sp2.date DESC LIMIT 1) * 100 AS pct_change
        FROM stocks s
        JOIN stock_prices sp ON sp.stock_id = s.stock_id AND sp.date = ?
        WHERE s.market != 'INDEX' AND s.industry IS NOT NULL
        """,
        (target_date, target_date, target_date),
    )
    columns = [d[0] for d in cur.description]
    raw_df = pd.DataFrame(cur.fetchall(), columns=columns)
    if raw_df.empty:
        return raw_df, target_date

    rotation_df = raw_df.groupby("industry").agg(
        total_volume=("volume", "sum"),
        avg_pct_change=("pct_change", "mean"),
        stock_count=("industry", "count"),
    ).reset_index()
    rotation_df = rotation_df.sort_values("total_volume", ascending=False).reset_index(drop=True)
    return rotation_df, target_date


def resolve_stock_id(conn, query: str) -> str | None:
    """依使用者輸入(可能是股票代號、完整名稱、或名稱片段，例如"2330"／"台積電"／"台積")
    找出對應的stock_id，供「個股查詢」欄位使用。查詢優先順序：①股票代號完全相符
    ②名稱完全相符 ③名稱片段(LIKE)相符，取第一筆(依stock_id排序)。都找不到回傳None，
    呼叫端可以退回用原始輸入當stock_id(維持既有「查無股票代號 X 的價格資料」錯誤訊息
    路徑，不特別區分「代號打錯」還是「這代號真的不存在」)。
    """
    query = query.strip()
    if not query:
        return None
    row = conn.execute("SELECT stock_id FROM stocks WHERE stock_id = ?", (query,)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT stock_id FROM stocks WHERE name = ?", (query,)).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "SELECT stock_id FROM stocks WHERE name LIKE ? ORDER BY stock_id LIMIT 1", (f"%{query}%",)
    ).fetchone()
    return row[0] if row else None


def get_stock_name(conn, stock_id: str) -> str | None:
    """回傳股票代號對應的名稱，查無資料回傳None。供圖表標題顯示「代號+名稱」用
    (例如"2330 台積電")，不是只顯示代號——兩個前端都要呼叫，圖表本身的
    build_candlestick_figure()只負責畫圖，不知道conn，名稱查詢留在這裡。
    """
    row = conn.execute("SELECT name FROM stocks WHERE stock_id = ?", (stock_id,)).fetchone()
    return row[0] if row else None


def load_price_history(conn, stock_id: str, days: int = 120) -> pd.DataFrame:
    """回傳指定股票最近days天的OHLCV+均線(MA5/10/20/60/120/240，欄位名MA{n})，依date遞增
    排序、index為date；查無資料回傳空DataFrame。

    多抓 max(FULL_PERIODS) 天的歷史資料當計算緩衝，讓均線在整個顯示範圍內都有值
    （而不是從顯示視窗的第一天才開始算、前面一大段是NaN），抓完才裁切回實際要顯示的days天。
    """
    lookback_days = days + max(FULL_PERIODS)
    cur = conn.execute(
        "SELECT date, open, high, low, close, volume FROM stock_prices WHERE stock_id = ? ORDER BY date DESC LIMIT ?",
        (stock_id, lookback_days),
    )
    columns = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=columns).iloc[::-1].reset_index(drop=True)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df = df.join(compute_ma_set(df["close"], periods=FULL_PERIODS))
    df = df.join(compute_macd(df["close"]))  # 欄位DIF/MACD/OSC，朱老師書中固定參數12/26/9(EMA)
    df = df.join(compute_kd(df["high"], df["low"], df["close"]))  # 欄位K/D，常用KD(9,3,3)參數
    # 欄位SAR/SAR_BULL，用join前(尚未.tail(days)裁切)的完整緩衝歷史計算，讓SAR的加速因子
    # 有跟MA/MACD/KD warm-up同等級的暖身期(見src/indicators/parabolic_sar.py，引用來源
    # 說明)，不是只用最後顯示窗口(days)那一小段重新起算，避免顯示窗口越窄、SAR初始種子
    # 誤差佔比越高的問題。
    sar_bull, sar_values = compute_sar(df["high"], df["low"], df["close"])
    df["SAR"] = sar_values
    df["SAR_BULL"] = sar_bull
    return df.tail(days)


def load_holidays_for_chart(df: pd.DataFrame) -> tuple[list[str], bool]:
    """回傳(該圖表資料範圍內的休市日清單, 是否成功抓取)。休市日清單來自兩個來源合併：
    ①TWSE官方公告的年度假日曆(trading_calendar.holidays_between())，只涵蓋預先公告
    的固定國定假日；②資料本身的「平日缺口」——`df`資料範圍內任何一個週一~週五、但
    `df`裡完全沒有那天資料的日期，也一併視為休市日。

    ⚠️ 2026-08-03新增②：使用者回報K線圖某個日期還是有斷點(即使先前已經修過rangebreaks
    的connectgaps問題)，查證發現2026-07-10(週五)TWSE官方端點對那天回傳0筆資料，證實
    是真正的休市日(研判是颱風假這類臨時公告、當天才決定的休市，不在年度預先公告的
    假日曆裡)。①單獨抓不到這種「臨時性」休市日，導致rangebreaks沒有把這天壓縮掉，
    在圖上留了一格空白斷點(K棒/成交量/均線在這個x位置全部中斷一格，不是connectgaps
    能解決的問題——那次修的是「rangebreaks有正確壓縮、但Plotly.js對平緩線段的繪圖
    瑕疵」，這次是「rangebreaks根本不知道要壓縮這天」，成因不同)。

    直接從`df`本身的資料缺口反推，不管背後原因是國定假日、臨時休市、還是資料抓取
    失敗，只要這天沒有資料就一律壓縮掉，不會在圖上留白——這是比「只信任官方年度
    假日曆」更穩健的做法，之後遇到同類型的臨時休市日也能自動處理，不需要每次都手動
    加清單。

    TWSE假日曆①這一步是畫圖路徑上的網路依賴，抓取失敗時只退回用②(資料缺口)這個
    來源，不會完全沒有假日清單，並回傳False提示呼叫端「官方假日清單暫時無法取得」。
    """
    if df.empty:
        return [], True
    implied_holidays = [
        d.strftime("%Y-%m-%d")
        for d in pd.bdate_range(df.index.min(), df.index.max()).difference(df.index)
    ]
    try:
        official_holidays = trading_calendar.holidays_between(df.index.min().year, df.index.max().year)
        return sorted(set(official_holidays) | set(implied_holidays)), True
    except Exception:  # noqa: BLE001 - 不應該讓TWSE暫時打不通就讓整張圖表壞掉
        return implied_holidays, False


# 台灣證交所公告的股票升降單位（價格級距，越高價股票的最小跳動點越大）：
# <10元:0.01／10~50:0.05／50~100:0.1／100~500:0.5／500~1000:1／>=1000:5。
_TWSE_TICK_SIZE_TIERS: list[tuple[float, float]] = [
    (10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1), (float("inf"), 5),
]


def _twse_tick_size(price: float) -> float:
    """依股價所在的證交所價格級距，回傳該價位實際可成交的最小跳動點（升降單位）。"""
    for upper_bound, tick in _TWSE_TICK_SIZE_TIERS:
        if price < upper_bound:
            return tick
    return _TWSE_TICK_SIZE_TIERS[-1][1]


def _price_axis_range(df: pd.DataFrame, padding_ratio: float = 0.05) -> tuple[float, float]:
    """算出價格Y軸應該顯示的(下限, 上限)，只根據K棒本身的高低價決定，不受切線/軌道線/
    支撐壓力這類疊圖trace影響。

    ⚠️ 2026-08-04修正「K線圖縮成一小條」bug：使用者回報3231(緯創)的K線圖被壓縮在
    畫面中間一小段，上下留了大片空白。查證發現Plotly預設的Y軸autorange是「畫面上
    所有trace的y值範圍聯集」，不是只看K棒——切線/軌道線(`chart_overlays.
    trendline_to_xy()`)刻意延伸畫到資料最後一天(見該函式docstring)，如果原本取點
    的兩個轉折點時間點很近、價差卻不小，算出來的斜率延伸一大段K棒後會遠遠超出
    合理價格範圍(3231這個案例：K棒實際價格122.5~201.0，但下降切線/軌道線外推到
    最新一天時已經跌到21.6、甚至負數-2.6)，這幾個離譜的極端值把Y軸硬拉開一大截，
    K棒本身反而被壓縮在中間一小段。

    改成明確指定Y軸range(不依賴Plotly autorange)，只用K棒高低價(+`padding_ratio`
    留白，預設5%)決定範圍——切線/軌道線超出這個範圍的部分還是會畫出來，只是被
    裁掉看不到超出範圍那一段(這才是正確行為：那些超出K棒實際成交價很遠的延伸段，
    本來就不是「股價可能到達的地方」，是切線公式外推的產物，不需要為了它們犧牲
    K棒本身的可讀性)。
    """
    low = float(df["low"].min())
    high = float(df["high"].max())
    span = high - low
    if span <= 0:
        padding = high * padding_ratio if high else 1.0
        return low - padding, high + padding
    padding = span * padding_ratio
    return low - padding, high + padding


def _price_axis_dtick(df: pd.DataFrame, target_gridlines: int = 10) -> float:
    """算出價格Y軸的格線間距：取該股票實際升降單位的整數倍，讓格線都落在真正可能成交的
    價位上，不是像Plotly預設那樣依數值大小自動抓一個跟股票本身無關的間距(例如2330股價在
    1700~2500區間、Plotly預設抓500元一格，太粗)。倍數選擇讓可視範圍內大約有
    target_gridlines(預設10)條格線，兼顧「格線落在有意義的價位」與「不會多到擠成一團」。
    """
    last_price = float(df["close"].iloc[-1])
    tick = _twse_tick_size(last_price)
    price_range = float(df["high"].max() - df["low"].min())
    if price_range <= 0:
        return tick
    multiple = max(1, round(price_range / target_gridlines / tick))
    return tick * multiple


def _axis_ref_suffix(row: int) -> str:
    """Plotly subplot的軸參照命名規則：第1個subplot的軸是"x"/"y"(不加數字)，第2個以後才是
    "x2"/"y2"、"x3"/"y3"……annotation的xref/yref要用這個suffix才能正確對應到指定的子圖。
    """
    return "" if row == 1 else str(row)


def build_candlestick_figure(
    df: pd.DataFrame, title: str = "", holidays: list[str] | None = None, ma_periods: tuple[int, ...] = (),
    trendlines: dict | None = None, show_trendline_keys: tuple[str, ...] = (),
    sr_levels: list[dict] | None = None, show_support_resistance: bool = False,
    show_macd: bool = False, show_kd: bool = False, show_sar: bool = False,
):
    """把OHLC資料畫成K線圖(非線圖)+下方成交量子圖，可疊加均線/切線軌道線/支撐壓力/SAR點位，
    並可選擇在最下方再疊加MACD/KD子圖。漲用紅，書中與規則庫(candles.py)裡「黑K」這個命名
    是台股K線圖傳統的「陰線」術語(不是實際渲染顏色)；2026-08-02使用者要求把陰線的實際
    渲染色從黑色改成跟MACD負值同一種綠色(#27ae60)，方便一眼分辨漲跌，避免跟成交量/K棒的黑
    混在一起不容易分辨——「黑K」這個規則命名維持不變，只是圖表上這個概念改用綠色呈現。
    成交量長條比照同一套配色，當天收紅用紅色、收黑(陰線)用綠色。

    holidays: 該資料範圍內的休市日期清單("YYYY-MM-DD")，連同週末一起設成x軸的
    rangebreaks，避免非交易日在圖上留白間斷(維持真正的日期型x軸，不是改用category型)。
    ma_periods: 要疊加顯示的均線天期(例如(5,20,60))，對應df裡由load_price_history算好的
    MA{n}欄位；書中預設核心3線是MA5/10/20，可擴充至MA60(季線)/MA120(半年線)/MA240(年線)
    做4~6線多空排列判斷（不是MA200，書裡沒有這個天期）。
    trendlines/show_trendline_keys: src.patterns.chart_overlays.compute_trendlines()算出的
    切線/軌道線字典，與要實際畫出的key清單(例如("up_tangent","up_channel"))。
    sr_levels/show_support_resistance: src.patterns.chart_overlays.compute_support_resistance_levels()
    算出的支撐壓力清單，與是否要畫出來。
    show_macd/show_kd: 是否在成交量子圖下方各自再加一列MACD(DIF/MACD訊號線/OSC柱狀體)、
    KD(K/D線，含80/20參考線)子圖，對應df裡由load_price_history算好的DIF/MACD/OSC/K/D欄位；
    欄位不存在時(例如舊呼叫端傳入的df沒有這些欄位)直接跳過不畫，不會crash。
    show_sar: 是否在價格子圖疊加每根K棒的SAR點位(對應df裡由load_price_history()算好的
    SAR/SAR_BULL欄位，見src.indicators.parabolic_sar引用來源說明)，多頭(SAR在K棒下方)用
    綠點、空頭(SAR在K棒上方)用紅點——這是SAR圖表的通用畫法慣例，跟K棒本身「漲紅跌綠」的
    配色是兩套獨立慣例(SAR的綠代表「多頭」、K棒的綠代表「收黑/陰線」，語意不同，只是剛好
    都用了綠色，不是同一套規則)，不會互相衝突混淆。欄位不存在時直接跳過不畫，不會crash。
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    extra_rows = int(show_macd) + int(show_kd)
    total_rows = 2 + extra_rows
    row_heights = {0: [0.72, 0.28], 1: [0.52, 0.2, 0.28], 2: [0.42, 0.16, 0.21, 0.21]}[extra_rows]

    macd_row = 3 if show_macd else None
    kd_row = (4 if show_macd else 3) if show_kd else None

    fig = make_subplots(
        rows=total_rows, cols=1, shared_xaxes=True, row_heights=row_heights, vertical_spacing=0.03,
    )
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        increasing_line_color="#c0392b", increasing_fillcolor="#c0392b",
        decreasing_line_color="#27ae60", decreasing_fillcolor="#27ae60",
        name="", showlegend=False,
    ), row=1, col=1)

    for n in ma_periods:
        col = f"MA{n}"
        if col not in df.columns:
            continue
        fig.add_trace(go.Scatter(
            x=df.index, y=df[col], mode="lines", name=col,
            line=dict(color=MA_COLORS.get(n, "#999999"), width=1.3),
        ), row=1, col=1)

    if show_sar and {"SAR", "SAR_BULL"}.issubset(df.columns):
        sar_colors = [
            "#27ae60" if pd.notna(bull) and bull else "#c0392b"
            for bull in df["SAR_BULL"]
        ]
        fig.add_trace(go.Scatter(
            x=df.index, y=df["SAR"], mode="markers", name="SAR",
            marker=dict(size=4, color=sar_colors, symbol="circle"),
        ), row=1, col=1)

    for key in show_trendline_keys:
        if not trendlines or key not in trendlines:
            continue
        line = trendlines[key]
        dates, prices = chart_overlays.trendline_to_xy(line, df)
        style = TRENDLINE_STYLES.get(key, {"color": "#999999", "dash": "solid"})
        label = TRENDLINE_LABELS.get(key, key)
        color = style["color"]
        # R-LINE-11/12：這條線如果已經被跌破(上升切線)或突破(下降切線)，role會被
        # compute_trendlines()就地互換過，不再是預設角色——用支撐/壓力的顏色改標示，
        # 不要讓使用者誤以為它還在發揮原本的作用(這正是使用者回報的問題：舊切線畫得
        # 好像還在支撐現在的股價，但其實早就跌破、對「現在」已經沒有意義)。
        if line.role != TRENDLINE_DEFAULT_ROLE.get(key, line.role):
            swapped_to = "壓力" if line.role == "resistance" else "支撐"
            label = f"{label}（已{'跌破' if swapped_to == '壓力' else '突破'}，轉{swapped_to}）"
            color = SR_ROLE_COLORS.get(swapped_to, color)
        fig.add_trace(go.Scatter(
            x=dates, y=prices, mode="lines", name=label,
            line=dict(color=color, dash=style["dash"], width=1.5),
        ), row=1, col=1)

    if show_support_resistance and sr_levels:
        for level in sr_levels:
            color = SR_ROLE_COLORS.get(level["role"], "#999999")
            fig.add_trace(go.Scatter(
                x=[df.index[0], df.index[-1]], y=[level["price"], level["price"]], mode="lines",
                name=f"{level['role']} {level['price']:.2f}",
                line=dict(color=color, dash="dot", width=1),
            ), row=1, col=1)

    volume_colors = ["#c0392b" if c >= o else "#27ae60" for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=df.index, y=df["volume"], marker_color=volume_colors, name="成交量", showlegend=False), row=2, col=1)

    # MACD/KD子圖各自右上角標示目前使用的參數(固定值，直接寫死跟load_price_history()
    # 呼叫compute_macd()/compute_kd()時完全沒有覆寫參數的事實一致)，左上角顯示「最新一天」
    # 的實際數值(不hover時的預設狀態)——desktop/chart_render.py會在滑鼠hover時透過JS動態
    # 覆寫這個文字改顯示「當天」數值，這裡先給的是靜態的初始/預設內容，Streamlit版沒有
    # hover機制，這組數字就是它唯一、也足夠的呈現方式。annotation用name標記
    # ("macd-hover-value"/"kd-hover-value")，讓chart_render.py的JS能在不知道實際
    # annotations清單順序的情況下，用name找到正確的index更新文字。
    annotations = list(fig.layout.annotations or ())

    if macd_row is not None and {"DIF", "MACD", "OSC"}.issubset(df.columns):
        # OSC正值紅柱(多方動能)/負值綠柱(空方動能)是書中原文定義的顏色，跟K棒是兩套獨立
        # 配色慣例(見src/indicators/macd.py docstring)——2026-08-02K棒陰線改成同一種
        # 綠色(#27ae60)後兩者剛好用同一組色碼，純屬巧合，語意上仍是各自獨立判斷。
        osc_colors = ["#c0392b" if v >= 0 else "#27ae60" for v in df["OSC"].fillna(0)]
        fig.add_trace(go.Bar(x=df.index, y=df["OSC"], marker_color=osc_colors, name="OSC", showlegend=False), row=macd_row, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["DIF"], mode="lines", name="DIF", line=dict(color="#e74c3c", width=1.2)), row=macd_row, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["MACD"], mode="lines", name="MACD訊號線", line=dict(color="#2980b9", width=1.2)), row=macd_row, col=1)

        macd_suffix = _axis_ref_suffix(macd_row)
        last = df.iloc[-1]
        annotations.append(dict(
            xref=f"x{macd_suffix} domain", x=1, yref=f"y{macd_suffix} domain", y=1,
            xanchor="right", yanchor="top", showarrow=False, font=dict(size=11, color="#666666"),
            text="MACD(12,26,9)",
        ))
        annotations.append(dict(
            xref=f"x{macd_suffix} domain", x=0, yref=f"y{macd_suffix} domain", y=1,
            xanchor="left", yanchor="top", showarrow=False, font=dict(size=11, color="#333333"),
            text=f"DIF {last['DIF']:.2f}　MACD {last['MACD']:.2f}　OSC {last['OSC']:.2f}",
            name="macd-hover-value",
        ))

    if kd_row is not None and {"K", "D"}.issubset(df.columns):
        fig.add_trace(go.Scatter(x=df.index, y=df["K"], mode="lines", name="K", line=dict(color="#8e44ad", width=1.3)), row=kd_row, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["D"], mode="lines", name="D", line=dict(color="#f39c12", width=1.3)), row=kd_row, col=1)
        fig.add_hline(y=80, line=dict(color="#999999", width=1, dash="dot"), row=kd_row, col=1)
        fig.add_hline(y=20, line=dict(color="#999999", width=1, dash="dot"), row=kd_row, col=1)

        kd_suffix = _axis_ref_suffix(kd_row)
        last = df.iloc[-1]
        annotations.append(dict(
            xref=f"x{kd_suffix} domain", x=1, yref=f"y{kd_suffix} domain", y=1,
            xanchor="right", yanchor="top", showarrow=False, font=dict(size=11, color="#666666"),
            text="KD(N=9,D=3)",
        ))
        annotations.append(dict(
            xref=f"x{kd_suffix} domain", x=0, yref=f"y{kd_suffix} domain", y=1,
            xanchor="left", yanchor="top", showarrow=False, font=dict(size=11, color="#333333"),
            text=f"K {last['K']:.1f}　D {last['D']:.1f}",
            name="kd-hover-value",
        ))

    # ⚠️ 2026-08-02修正「K線圖斷點」bug：使用者回報MA60/MA120/MA240這幾條線在特定日期
    # (查證是農曆春節這種連續多天的休市假期，rangebreaks把整段假期從x軸壓縮掉的位置)
    # 會出現一段線段完全消失的視覺斷點，MA5/10/20跟K棒本身則不受影響。查證方式：直接把
    # 這裡產生的Figure分別用純Plotly(不透過desktop/chart_render.py的自訂JS)畫成HTML用
    # headless瀏覽器截圖，同樣看得到斷點，排除是desktop那層JS造成的；而底層DataFrame
    # (chart_data.load_price_history()算出的MA60/120/240欄位)在整段期間完全沒有NaN，
    # 排除是資料本身的問題。純粹是Plotly.js的已知限制：Scatter線段trace預設
    # connectgaps=False，rangebreaks壓縮掉大段日期後，斜率越平緩(長天期均線)的線段
    # 越容易被Plotly.js的線段簡化/斷點判斷誤判成「資料缺口」而不畫連接線，MA5/10/20
    # 波動較大反而不會觸發這個誤判——跟rangebreaks本身是否正確(有沒有正確涵蓋假日)無關，
    # 這裡的holiday清單(trading_calendar.py)已經查證過是正確的。修法是對所有線段類
    # (mode="lines")trace明確設定connectgaps=True，強制跨越rangebreak的視覺缺口把線
    # 畫連續——K棒(candlestick)/長條圖(volume/OSC)/SAR(markers-only)不支援也不需要
    # 這個屬性，用selector限定type="scatter"精準只套用到有問題的線段類trace，不會
    # 意外影響到其他trace。
    fig.update_traces(connectgaps=True, selector=dict(type="scatter"))

    # 標題(股票代號+名稱)跟上方橫式legend(均線/切線/MACD/KD項目)搶同一塊頂端空間，原本
    # 兩者都用Plotly預設位置、margin也不夠高，會疊在一起看不清楚——改成title釘在最上緣
    # (yanchor="top", y=1)、legend的底部貼齊繪圖區頂部往上長(yanchor="bottom", y=1.01)，
    # 兩者用不同錨點各自往「圖表外」的方向延伸，加高margin.t留出足夠空間，兩者才不會疊到。
    fig.update_layout(
        title=dict(text=title, x=0.01, xanchor="left", y=1, yanchor="top", font=dict(size=14)) if title else dict(text=""),
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=70 if title else 10, b=10),
        height=560 + 140 * extra_rows,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        annotations=annotations,
    )
    fig.update_yaxes(title_text="價格", dtick=_price_axis_dtick(df), range=_price_axis_range(df), row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)
    if macd_row is not None:
        fig.update_yaxes(title_text="MACD", row=macd_row, col=1)
    if kd_row is not None:
        fig.update_yaxes(title_text="KD", range=[0, 100], row=kd_row, col=1)

    rangebreaks = [dict(bounds=["sat", "mon"])]
    if holidays:
        rangebreaks.append(dict(values=holidays))
    fig.update_xaxes(rangebreaks=rangebreaks)

    # 淡灰色十字線(hover時顯示滑鼠對應的X/Y位置)，仿TradingView的畫法。這裡只設定Plotly
    # 原生的spike line，兩個前端(Streamlit/PySide6)都能用；但實測發現原生x軸spike的
    # "across"模式在上下堆疊子圖(價格/成交量)的情況下，垂直線只會畫在滑鼠所在那一格，
    # 不會真的貫穿到另一個子圖——這是PySide6桌面版才需要的「垂直線貫穿兩個子圖」效果，
    # desktop/chart_render.py會在桌面版另外用自訂JS覆蓋掉這裡的x軸spike設定來達成；
    # Streamlit版沒有這個機制，維持這裡的原生效果(垂直線只在單一子圖內顯示)已經是不錯的
    # 折衷，不需要為了它額外做什麼。
    spike_style = dict(
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikecolor="rgba(120,120,120,0.6)", spikethickness=1, spikedash="solid",
    )
    fig.update_xaxes(**spike_style)
    fig.update_yaxes(**spike_style)
    fig.update_layout(hovermode="x")
    return fig
