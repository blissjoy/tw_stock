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
    conn, stock_ids: list[str], periods: tuple[int, ...] = DEFAULT_BULLISH_PERIODS, lookback_days: int | None = None
) -> dict[str, bool]:
    """對每檔股票算出「均線多頭排列」(依`periods`由短到長排列，例如MA5>MA10>MA20，或延伸到
    書中「多線多排」的MA120/MA240)是否成立，取最新一筆已知資料為準。只在候選清單篩選器實際
    勾選這個條件時才呼叫(見CANDIDATE_FILTERS)，不是每次載入候選清單都算，避免候選股數量
    變多時拖慢清單載入速度。

    lookback_days未指定時採`max(periods)`——延伸到MA120/MA240的篩選需要對應天數的收盤價
    才能算出最長那條均線，不能沿用MA5/10/20版本的60天預設值。
    資料不足以算出最長均線時該檔股票視為不成立(False)，不是拋例外或跳過。
    """
    if not stock_ids:
        return {}
    if lookback_days is None:
        lookback_days = max(periods)
    closes_by_stock = _fetch_recent_columns_batched(conn, stock_ids, ["close"], lookback_days)
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
    conn, stock_ids: list[str], columns: list[str], lookback_days: int,
) -> dict[str, dict[str, list[float]]]:
    """批次撈取多檔股票最近`lookback_days`個交易日的指定欄位，取代逐檔各自查詢一次
    (N+1查詢)——候選清單股數多、又同時勾選多個篩選條件時，逐檔查詢的往返成本會疊加成
    明顯的等待時間(2026-08-01效能調校：使用者回報篩選花的時間很多)。改成一次查全部、
    用stock_id分組，SQLite/Turso執行單一大查詢的成本遠低於N次小查詢的往返總和。

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
    """
    placeholders = ",".join("?" * len(stock_ids))
    column_list = ", ".join(columns)
    cur = conn.execute(
        f"""
        SELECT stock_id, {column_list} FROM (
            SELECT stock_id, {column_list},
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
            FROM stock_prices
            WHERE stock_id IN ({placeholders})
        )
        WHERE rn <= ?
        ORDER BY stock_id, rn DESC
        """,
        [*stock_ids, lookback_days],
    )
    result: dict[str, dict[str, list[float]]] = {}
    for row in cur.fetchall():
        stock_id = row[0]
        per_stock = result.setdefault(stock_id, {col: [] for col in columns})
        for col, value in zip(columns, row[1:]):
            per_stock[col].append(value)
    return result


def _ma_bullish_filter(periods: tuple[int, ...]) -> Callable[[object, list[str]], dict[str, bool]]:
    return lambda conn, stock_ids: compute_ma_bullish_flags(conn, stock_ids, periods=periods)


# 候選清單篩選器registry：{顯示標籤: 計算函式(conn, stock_ids) -> {stock_id: bool}}。
# 之後要加其他篩選條件(例如量能/型態)時，在這裡多加一組標籤/函式即可，兩個前端
# (dashboard/app.py、desktop/main_window.py)都是迴圈讀取這個registry動態產生勾選框、
# 不用另外改UI程式碼。
CANDIDATE_FILTERS: dict[str, Callable[[object, list[str]], dict[str, bool]]] = {
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


def compute_sar_flip_flags(
    conn, stock_ids: list[str], direction: str = "多頭", within_days: int = 1,
    lookback_days: int = SAR_FLIP_LOOKBACK_DAYS,
) -> dict[str, bool]:
    """對每檔股票算出「SAR是否翻轉為`direction`(多頭/空頭)、且發生在最近`within_days`天以內」
    (見`src.indicators.parabolic_sar.sar_flipped_within`，含引用來源說明)。只在候選清單篩選器
    實際勾選SAR翻轉條件時才呼叫(見`apply_candidate_filters`的`sar_flip_option`參數)。歷史資料
    不足3天(compute_sar至少需要2天以上才有意義)的股票視為不成立(False)，不拋例外。
    """
    if not stock_ids:
        return {}
    rows_by_stock = _fetch_recent_columns_batched(conn, stock_ids, ["high", "low", "close"], lookback_days)
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


def apply_candidate_filters(
    conn, candidates_df: pd.DataFrame, active_filter_labels: list[str],
    sar_flip_option: dict | None = None,
) -> pd.DataFrame:
    """依勾選的篩選標籤(CANDIDATE_FILTERS的key)逐一AND套用，回傳過濾後的候選清單。
    未勾選任何篩選(active_filter_labels為空且sar_flip_option為None)時原樣回傳，不做任何運算。

    sar_flip_option：SAR翻轉篩選的參數，格式{"direction": "多頭"|"空頭", "within_days": int}，
    傳None代表沒有勾選這個條件。這個篩選條件的UI是「勾選框+方向下拉+天數輸入」三個元件綁在
    一起，不是單純的勾選框，不適合塞進`CANDIDATE_FILTERS`那種「label -> 純checkbox」的
    registry，因此用獨立參數傳入，不是加進`active_filter_labels`清單裡。
    """
    if candidates_df.empty:
        return candidates_df
    if not active_filter_labels and sar_flip_option is None:
        return candidates_df
    stock_ids = candidates_df["stock_id"].tolist()
    mask = pd.Series(True, index=candidates_df.index)
    for label in active_filter_labels or []:
        flags = CANDIDATE_FILTERS[label](conn, stock_ids)
        mask &= candidates_df["stock_id"].map(flags).fillna(False)
    if sar_flip_option is not None:
        flags = compute_sar_flip_flags(
            conn, stock_ids,
            direction=sar_flip_option.get("direction", "多頭"),
            within_days=sar_flip_option.get("within_days", 1),
        )
        mask &= candidates_df["stock_id"].map(flags).fillna(False)
    return candidates_df[mask].reset_index(drop=True)


def list_candidate_dates(conn) -> list[str]:
    """回傳daily_candidates裡所有有紀錄的日期，由新到舊排序，供候選清單的日期選單使用。"""
    cur = conn.execute("SELECT DISTINCT date FROM daily_candidates ORDER BY date DESC")
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


def load_candidates_for_date(conn, target_date: str | None = None) -> tuple[pd.DataFrame, str | None, bool]:
    """回傳 (指定日期的候選清單DataFrame, 該日期字串, is_intraday)；target_date為None時
    取最新一天。尚無任何紀錄(或指定日期查無資料)時回傳(空DataFrame, 對應日期字串或None, False)。

    is_intraday：這天的資料是否來自yfinance盤中即時價備援(True)而非TWSE官方最終收盤價
    (False)，讀daily_data_status表(見schema.sql與scripts/daily_pipeline.py的
    fetch_today_twse())；查無紀錄(例如這個功能上線前就有的歷史資料)一律視為False，不
    特別標示。呼叫端(兩個前端)依此顯示「尚未收盤」提示，讓使用者知道這天的訊號可能還會
    隨收盤價格微調而改變。

    同一檔股票如果同時符合多條規則(daily_candidates裡有多筆同stock_id、不同signal_name
    的紀錄，例如同時觸發R-TREND-14跟R-SCREEN-15)，這裡會合併成一列顯示，不是一條規則
    一列——signal_name欄位用「\n」換行字元分隔多條規則的內容(而不是逗號/頓號這類同一行內
    的分隔符)，讓桌面版(desktop/main_window.py開了word wrap + resizeRowsToContents)
    能在同一格內分成好幾行顯示，一條規則一行，比擠在同一行裡好讀。⚠️ Streamlit的
    `st.dataframe`不支援儲存格內換行(實測\n會被吃成空白、HTML的<br>則會顯示成字面文字)，
    這是該元件本身的限制；用\n分隔在那邊會退化成單行、規則之間用空白隔開，不是bug，是
    目前Streamlit這個次要前端能接受的降級效果。
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

    cur = conn.execute(
        """
        SELECT dc.stock_id, s.name, s.industry, dc.signal_name, dc.entry_price, dc.stop_loss,
               sp.close AS today_close, sp.volume AS today_volume,
               (SELECT sp2.close FROM stock_prices sp2
                WHERE sp2.stock_id = dc.stock_id AND sp2.date < dc.date
                ORDER BY sp2.date DESC LIMIT 1) AS prev_close
        FROM daily_candidates dc
        LEFT JOIN stocks s ON dc.stock_id = s.stock_id
        LEFT JOIN stock_prices sp ON sp.stock_id = dc.stock_id AND sp.date = dc.date
        WHERE dc.date = ? ORDER BY dc.stock_id, dc.created_at
        """,
        (target_date,),
    )
    columns = [d[0] for d in cur.description]
    raw_df = pd.DataFrame(cur.fetchall(), columns=columns)
    if raw_df.empty:
        return raw_df, target_date, is_intraday

    raw_df["pct_change"] = (raw_df["today_close"] - raw_df["prev_close"]) / raw_df["prev_close"] * 100

    merged_rows = []
    for stock_id, group in raw_df.groupby("stock_id", sort=False):
        first = group.iloc[0]
        signal_name = "\n".join(group["signal_name"])
        merged_rows.append({
            "stock_id": stock_id,
            "name": first["name"],
            "industry": first["industry"],
            "signal_name": signal_name,
            "entry_price": first["entry_price"],
            "stop_loss": first["stop_loss"],
            "pct_change": first["pct_change"],
            "volume": first["today_volume"],
            # 排序用：這檔股票當天符合的所有規則信心分數加總，觸發越多條規則、信心分數
            # 越高的股票排越前面——不是最終顯示欄位，排序完就丟棄，不影響回傳的欄位結構。
            "_confidence_sum": sum(int(m) for m in _CONFIDENCE_PATTERN.findall(signal_name)),
        })
    merged_df = pd.DataFrame(
        merged_rows,
        columns=["stock_id", "name", "industry", "signal_name", "entry_price", "stop_loss", "pct_change", "volume", "_confidence_sum"],
    )
    # 預設排序：信心分數加總由高到低；同分時退回股票代號排序，確保結果穩定、可重現。
    merged_df = merged_df.sort_values(
        ["_confidence_sum", "stock_id"], ascending=[False, True]
    ).drop(columns="_confidence_sum").reset_index(drop=True)
    return merged_df, target_date, is_intraday


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
    """回傳(該圖表資料範圍內的休市日清單, 是否成功抓取)。TWSE假日曆這一步是畫圖路徑上
    新增的網路依賴，抓取失敗時回傳([], False)而不拋例外，呼叫端可以決定要不要提示使用者
    「假日清單暫時無法取得」，圖表仍能正常畫出來(退回只套用週末rangebreak)。
    """
    if df.empty:
        return [], True
    try:
        return trading_calendar.holidays_between(df.index.min().year, df.index.max().year), True
    except Exception:  # noqa: BLE001 - 不應該讓TWSE暫時打不通就讓整張圖表壞掉
        return [], False


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
    並可選擇在最下方再疊加MACD/KD子圖。漲用紅、跌用黑，比照書中與規則庫(candles.py)一貫的
    紅K/黑K命名慣例(台股K線圖傳統配色，紅漲黑跌，與美股常見的綠漲紅跌相反)；成交量長條比照
    同一套配色，當天收紅用紅色、收黑用黑色。

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
    綠點、空頭(SAR在K棒上方)用紅點——這是SAR圖表的通用畫法慣例，跟本專案K棒本身「紅漲黑跌」
    的配色是兩套獨立慣例，不會互相衝突混淆。欄位不存在時直接跳過不畫，不會crash。
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
        decreasing_line_color="#1a1a1a", decreasing_fillcolor="#1a1a1a",
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

    volume_colors = ["#c0392b" if c >= o else "#1a1a1a" for o, c in zip(df["open"], df["close"])]
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
        # OSC正值紅柱(多方動能)/負值綠柱(空方動能)是書中原文定義的顏色，跟K棒紅漲黑跌是
        # 兩套獨立配色慣例，不要混用(見src/indicators/macd.py docstring)。
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
    fig.update_yaxes(title_text="價格", dtick=_price_axis_dtick(df), row=1, col=1)
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
