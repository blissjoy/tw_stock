"""每日選股（Layer 4 應用層）：對每檔股票用「今天」的最新資料，判斷已接上的規則的進場
條件是否成立。

⚠️ 2026-07-23前只接了R-TREND-14（多頭短線選股與停損停利SOP，信心92/100，已用真實資料
回測驗證勝率33.5%），刻意先從這一條已被回測證實的規則起步。之後追加R-SCREEN-11（底部
狹幅盤整大量紅K突破鎖股，信心89/100）、R-SCREEN-15（緩漲上升軌道線突破大量長紅K做多，
信心88/100）、R-CLASSIC-24（突破大量黑K買進，信心87/100）——246條規則庫其實已經100%
都有程式實作(`scripts/check_rule_coverage.py`可查)，差別只在於這裡有沒有把它「接進每日
自動選股」這一層；這幾條都是清楚的做多進場訊號、只需要OHLCV資料(不像R-SCREEN-05需要
股本/營收/三大法人等本專案還沒抓取的基本面資料)，且各自能重用既有的building block
(`src/indicators/consolidation.py`的橫盤突破偵測、`src/patterns/chart_overlays.py`的
上升軌道線、`src/indicators/moving_average.py`的均線多頭排列、`src/indicators/
volume_price.py`的大量判斷)，不需要另外新寫底層演算法。依使用者指示，這次先接上觀察
實際選股表現，不像R-TREND-14那樣要求先個別回測驗證勝率。

⚠️ 2026-07-23追加R-GAP-09（打底完成向上突破缺口，信心90/100）時，一開始誤判這條規則
「需要缺口隔天的成交量確認、跟只評估今天的做法衝突」而排除掉——這個判斷是錯的：實際已
實作的`src.indicators.gaps.detect_breakaway_gap_up()`裡，「3天內是否回補」只是事後才能
加註的warning欄位，從來不是回傳訊號與否的必要條件(gap_filled_within_3_days=False時一樣
正常回傳訊號)，所以評估「缺口發生當天」完全不需要用到未來資料，跟其他4條規則的模式
一致，只是最初排除得太草率，之後補上了。

之後要加其他規則的每日篩選時，比照這裡的模式各自寫一個獨立的 screen_* 函式（輸入df，
輸出候選dict或None），再由 screen_all_stocks 或 daily_pipeline.py 呼叫端合併多個screen
函式的結果即可，不需要重寫這一層。
"""

from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd

from src.data import storage
from src.indicators.candles import is_mid_long_red_candle
from src.indicators.consolidation import detect_consolidation, detect_consolidation_breakout
from src.indicators.gaps import detect_breakaway_gap_up, detect_gap
from src.indicators.moving_average import compute_ma_set, is_bullish_aligned, sma
from src.indicators.trend import (
    bull_short_term_entry_ready,
    bull_short_term_stop_loss,
    daily_bull_trend_state,
)
from src.indicators.volume_price import is_big_volume_vs_prev_day
from src.patterns import chart_overlays
from src.screener.screening_rules import narrow_range_bottom_breakout, slow_rally_channel_breakout

# R-GAP-09判斷「打底完成」的盤整天數門檻：書中這條規則本身沒有給出明確的天數(只引用
# 「盤整區上下頸線支撐壓力規則」等其他章節)，這裡用比R-SCREEN-11(2個月/42天)略短的
# 20個交易日(約1個月)當工程估計值，不是書中明文數字。
GAP_CONSOLIDATION_MIN_BARS = 20

# R-CLASSIC-24往回搜尋「多頭排列期間的大量黑K」的天數上限，避免抓到太久以前、
# 已經沒有參考意義的舊黑K高點當作watch_high。
BIG_BLACK_BREAKOUT_LOOKBACK = 20

# 約2個月交易日，比照R-SCREEN-11「盤整須達2個月以上」的門檻換算(21個交易日/月概估)
CONSOLIDATION_MIN_BARS = 42


def screen_bull_short_term_entry(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料(依date遞增排序、index為date)判斷「今天」(最後一列)是否觸發
    R-TREND-14多頭短線進場訊號。資料不足min_days天則回傳None(不足以計算MA20等指標)。
    """
    if len(df) < min_days:
        return None

    close, high, low, open_, volume = df["close"], df["high"], df["low"], df["open"], df["volume"]
    ma10 = sma(close, 10)
    ma20 = sma(close, 20)
    ma10_slope = ma10.diff()
    ma20_slope = ma20.diff()
    volume_prev = volume.shift(1)
    bull_trend = daily_bull_trend_state(high, low, close, n=5)

    t = len(close) - 1
    if pd.isna(ma20_slope.iloc[t]) or pd.isna(volume_prev.iloc[t]) or pd.isna(ma10.iloc[t]):
        return None

    ready = bull_short_term_entry_ready(
        is_bull_trend=bool(bull_trend.iloc[t]),
        ma10=ma10.iloc[t], ma20=ma20.iloc[t],
        ma10_slope=ma10_slope.iloc[t], ma20_slope=ma20_slope.iloc[t],
        close_t=close.iloc[t], open_t=open_.iloc[t],
        volume_t=volume.iloc[t], volume_prev=volume_prev.iloc[t],
    )
    if not ready:
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bull_short_term_stop_loss(entry_bar_low=float(low.iloc[t]))
    return {
        "signal_name": "R-TREND-14多頭短線進場（92%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": "多頭架構＋MA10/MA20多排向上＋攻擊量(前日1.3倍以上)＋紅K實體漲幅>2%",
    }


def screen_narrow_range_bottom_breakout(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-SCREEN-11底部狹幅盤整大量紅K突破鎖股訊號。

    重用`src/indicators/consolidation.py`的橫盤偵測(min_bars設成約2個月交易日，比照書中
    「盤整須達2個月以上」的門檻)；該函式已經確認過「中長紅K收盤站上頸線」，這裡只需要額外
    算出區間均量、交給`screening_rules.narrow_range_bottom_breakout()`檢查量能是否達
    區間均量2倍以上(這是`detect_consolidation_breakout`本身不檢查的部分)。
    """
    if len(df) < min_days:
        return None
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]

    box = detect_consolidation_breakout(open_, high, low, close, min_bars=CONSOLIDATION_MIN_BARS)
    t = len(close) - 1
    if t < 1 or not bool(box["breakout_up"].iloc[t]):
        return None

    prior_group_len = int(box["group_len"].iloc[t - 1])
    range_start = max(0, t - prior_group_len)
    range_avg_volume = float(volume.iloc[range_start:t].mean())
    consolidation_upper = float(box["upper_neckline"].iloc[t - 1])

    triggered = narrow_range_bottom_breakout(
        duration_months=prior_group_len / 21.0,
        is_red_k=bool(is_mid_long_red_candle(open_, close).iloc[t]),
        close=float(close.iloc[t]), consolidation_upper=consolidation_upper,
        volume=float(volume.iloc[t]), range_avg_volume=range_avg_volume,
    )
    if not triggered:
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bull_short_term_stop_loss(entry_bar_low=float(low.iloc[t]))
    return {
        "signal_name": "R-SCREEN-11底部盤整突破鎖股（89%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": f"底部狹幅盤整{prior_group_len}天以上大量紅K突破＋量能達區間均量2倍以上",
    }


def screen_slow_rally_channel_breakout(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-SCREEN-15緩漲上升軌道線突破大量長紅K
    做多訊號。重用`src/patterns/chart_overlays.compute_trendlines()`已經算好的上升軌道線
    (`up_channel`，跟K線圖疊圖用的是同一套邏輯，不重新發明取點演算法)。
    """
    if len(df) < min_days:
        return None
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]

    trendlines = chart_overlays.compute_trendlines(df)
    up_channel = trendlines.get("up_channel")
    if up_channel is None:
        return None

    t = len(close) - 1
    channel_value = up_channel.at(t)
    avg_volume_20 = float(volume.iloc[max(0, t - 20):t].mean())

    triggered = slow_rally_channel_breakout(
        close=float(close.iloc[t]), channel_upper_value=channel_value,
        is_long_red_k=bool(is_mid_long_red_candle(open_, close).iloc[t]),
        volume=float(volume.iloc[t]), avg_volume_20=avg_volume_20,
    )
    if not triggered:
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bull_short_term_stop_loss(entry_bar_low=float(low.iloc[t]))
    return {
        "signal_name": "R-SCREEN-15緩漲軌道突破做多（88%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": "緩漲上升軌道線大量長紅K突破＋量能達20日均量2倍以上",
    }


def screen_breakout_above_big_black_candle(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-CLASSIC-24突破大量黑K買進訊號。

    書中觀念：多頭排列(均線多頭)期間出現的大量黑K，表面上是賣壓K棒，但只要後續股價收盤
    突破該黑K高點且放量，黑K反而不是轉空訊號、而是續漲買進訊號。往回搜尋最近
    `BIG_BLACK_BREAKOUT_LOOKBACK`天內、最近一根「均線多頭排列期間出現的大量黑K」當作
    突破基準(watch_high)，只取最近一根而不是任一根，避免抓到已經沒有參考意義的舊黑K。
    """
    if len(df) < min_days:
        return None
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]
    t = len(close) - 1

    ma_frame = compute_ma_set(close, periods=(5, 10, 20))
    bullish = is_bullish_aligned(ma_frame)
    big_volume = is_big_volume_vs_prev_day(volume, multiple=2.0)
    is_black = close < open_

    watch_high = None
    search_start = t - 1
    search_end = max(search_start - BIG_BLACK_BREAKOUT_LOOKBACK, -1)
    for j in range(search_start, search_end, -1):
        if bool(is_black.iloc[j]) and bool(big_volume.iloc[j]) and bool(bullish.iloc[j]):
            watch_high = float(high.iloc[j])
            break
    if watch_high is None:
        return None

    if not (close.iloc[t] > watch_high and bool(big_volume.iloc[t])):
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bull_short_term_stop_loss(entry_bar_low=float(low.iloc[t]))
    return {
        "signal_name": "R-CLASSIC-24突破大量黑K買進（87%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": f"多頭排列期間出現大量黑K(高點{watch_high:.2f})，今日收盤突破且放量，非轉空為續漲買進訊號",
    }


def screen_breakaway_gap_up(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-GAP-09打底完成向上突破缺口訊號。

    書中評為訊號等級最高的型態之一：底部盤整完成後，股價向上跳空且缺口下緣不低於盤整區
    上緣(真正突破、不是普通缺口)，屬強力買進訊號，原本的壓力線也轉為支撐。「3天內回補
    視為假突破」是事後才能確認的警示，不是觸發訊號的前提(見`detect_breakaway_gap_up()`
    的docstring)，這裡評估「缺口發生當天」時傳入`gap_filled_within_3_days=False`
    (當下還不知道未來3天會不會回補，不代表訊號無效，只是還沒有這個額外警示可以標註)。
    """
    if len(df) < min_days:
        return None
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]
    t = len(close) - 1
    if t < 1:
        return None

    gap = detect_gap(
        prev_high=float(high.iloc[t - 1]), prev_low=float(low.iloc[t - 1]),
        curr_high=float(high.iloc[t]), curr_low=float(low.iloc[t]),
    )
    if gap is None or gap.type != "up_gap":
        return None

    box = detect_consolidation(high.iloc[:t], low.iloc[:t], min_bars=GAP_CONSOLIDATION_MIN_BARS)
    if not bool(box["is_consolidating"].iloc[-1]):
        return None
    consolidation_upper = float(box["upper_neckline"].iloc[-1])

    avg_volume_20 = float(volume.iloc[max(0, t - 20):t].mean())
    is_large_volume = bool(volume.iloc[t] >= 2.0 * avg_volume_20)

    result = detect_breakaway_gap_up(
        gap=gap, consolidation_upper=consolidation_upper,
        is_large_volume=is_large_volume, gap_filled_within_3_days=False,
    )
    if result is None:
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bull_short_term_stop_loss(entry_bar_low=float(low.iloc[t]))
    return {
        "signal_name": "R-GAP-09打底完成向上突破缺口（90%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": f"{result['signal']}，缺口下緣{result['support']:.2f}(原壓力轉支撐)",
    }


_SCREEN_FUNCTIONS = (
    screen_bull_short_term_entry,
    screen_narrow_range_bottom_breakout,
    screen_slow_rally_channel_breakout,
    screen_breakout_above_big_black_candle,
    screen_breakaway_gap_up,
)


_SIGNAL_NAME_PATTERN = re.compile(r"^(R-[A-Z]+-\d+)(.*)（(\d+)%）$")
_CONFIDENCE_PREFIX_PATTERN = re.compile(r"^(\d+)/100")


def analyze_stock_signals(df: pd.DataFrame, min_days: int = 60, trend_df: pd.DataFrame | None = None) -> list[dict]:
    """對「單一股票」的OHLCV資料，跑過①目前已接上的所有screen_*規則(整套進場SOP，含
    進場價/停損建議)、②`src.screener.rule_scan`的「黃金層」單點技術訊號(不含進場/停損
    建議)，回傳「今天」(資料最後一列)符合的訊號清單，依信心分數由高到低排序，每筆附上
    從ai/zhu-rules/查出的規則完整說明——供UI的「個股分析」面板使用，不同於
    screen_all_stocks/run_screen_and_store是批次跑「所有股票」寫回daily_candidates
    資料表，這裡是針對使用者當下正在看的單一股票即時運算，不寫入資料庫。

    目前只涵蓋這兩類已接上的規則（不是全部246條規則庫，範圍界定見rule_scan.py開頭的
    說明），範圍會隨之後接上更多規則自動擴大，這裡的程式碼不用跟著改。

    trend_df：轉傳給`scan_golden_tier()`專門供短/中/長(日/週/月)趨勢分類器使用的長歷史
    資料，見那裡的說明；不傳時退回用`df`自己的歷史。
    """
    from src.rule_docs import load_rule_doc
    from src.screener.rule_scan import scan_golden_tier

    matches: list[dict] = []
    for screen_fn in _SCREEN_FUNCTIONS:
        result = screen_fn(df, min_days=min_days)
        if result is None:
            continue
        name_match = _SIGNAL_NAME_PATTERN.match(result["signal_name"])
        if not name_match:
            continue
        rule_id, title, confidence = name_match.group(1), name_match.group(2), int(name_match.group(3))
        doc = load_rule_doc(rule_id)
        matches.append({
            "rule_id": rule_id,
            "title": title,
            "confidence": confidence,
            "note": result.get("note"),
            "description": doc.get("解讀") if doc else None,
            "reference": doc.get("原文與頁碼") if doc else None,
        })

    for item in scan_golden_tier(df, trend_df=trend_df):
        doc = load_rule_doc(item["rule_id"])
        confidence_match = _CONFIDENCE_PREFIX_PATTERN.match(doc["信心"]) if doc and "信心" in doc else None
        if confidence_match is None:
            continue  # 理論上不會發生(rule_docs涵蓋全部246條)，查無信心分數就不列入
        matches.append({
            "rule_id": item["rule_id"],
            "title": doc.get("名稱", item["rule_id"]),
            "confidence": int(confidence_match.group(1)),
            "note": item["note"],
            "description": doc.get("解讀"),
            "reference": doc.get("原文與頁碼"),
        })

    matches.sort(key=lambda m: m["confidence"], reverse=True)
    return matches


def screen_all_stocks(stock_frames: dict[str, pd.DataFrame], min_days: int = 60) -> list[dict]:
    """對多檔股票批次跑目前已接上的所有screen_*規則，回傳今天所有觸發訊號的候選清單。
    同一檔股票若同時觸發多條規則，會分別各出現一筆(不同signal_name)，不互相排擠。

    stock_frames: {stock_id: df}，df需已依date排序、index為date、含open/high/low/close/volume欄位。
    """
    candidates: list[dict] = []
    for stock_id, df in stock_frames.items():
        for screen_fn in _SCREEN_FUNCTIONS:
            result = screen_fn(df, min_days=min_days)
            if result is not None:
                candidates.append({"stock_id": stock_id, **result})
    return candidates


def load_trailing_frames(conn, min_days: int = 60) -> dict[str, pd.DataFrame]:
    """讀出每檔股票至今的全部OHLCV歷史(不設上限，由screen_all_stocks自己判斷天數夠不夠算指標)。
    純讀取，跟資料是Turso還是本機sqlite無關，供 scripts/daily_pipeline.py 與 dashboard/app.py 共用。
    """
    stock_ids = [r[0] for r in conn.execute("SELECT stock_id FROM stocks ORDER BY stock_id").fetchall()]

    frames: dict[str, pd.DataFrame] = {}
    for stock_id in stock_ids:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM stock_prices WHERE stock_id = ? ORDER BY date",
            (stock_id,),
        ).fetchall()
        if len(rows) < min_days:
            continue
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        frames[stock_id] = df.set_index("date")
    return frames


def run_screen_and_store(conn, iso_date: str | None = None, min_days: int = 60) -> list[dict]:
    """只用資料庫裡『目前已有』的資料重新跑一次選股並寫回daily_candidates，不對外抓取任何新資料。

    這是刻意的設計：抓新資料(TWSE/TPEx)成本較高(TPEx經yfinance批次下載，實測約1~2分鐘)，跟
    「用現有資料重算訊號」(純本地運算，通常幾秒內)分開，才能讓 dashboard 提供「立即重新篩選」
    這種不需要等待資料抓取的即時操作；scripts/daily_pipeline.py 抓完當天新資料後也呼叫同一份
    邏輯，避免重複實作。

    ⚠️ 同一天可能重跑選股不只一次(手動按「立即重新篩選」按很多次、或補資料後重算)，每次都是
    從資料庫現有資料重新算出『完整』的候選清單，不是增量疊加——所以寫入前一定要先清掉這個
    日期的舊紀錄(見storage.delete_daily_candidates_for_date)，否則「這次已經不再符合條件」
    的股票會繼續卡在表裡，讓候選清單顯示過時的結果。即使這次重算出0檔候選，也要清掉舊紀錄
    (代表『今天正確答案就是沒有候選股』)，不能因為candidates是空的就跳過清除這一步。
    """
    if iso_date is None:
        iso_date = date.today().isoformat()

    frames = load_trailing_frames(conn, min_days=min_days)
    candidates = screen_all_stocks(frames, min_days=min_days)

    storage.delete_daily_candidates_for_date(conn, iso_date)
    if candidates:
        storage.upsert_daily_candidates(conn, [
            {
                "date": iso_date, "stock_id": c["stock_id"], "signal_name": c["signal_name"],
                "entry_price": c["entry_price"], "stop_loss": c["stop_loss"], "note": c.get("note"),
                "created_at": datetime.now().isoformat(),
            }
            for c in candidates
        ])
    return candidates
