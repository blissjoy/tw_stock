"""每日選股（Layer 4 應用層）：對每檔股票用「今天」的最新資料，判斷已接上的規則的進場
條件是否成立。

⚠️ 2026-07-23前只接了R-TREND-14（多頭短線選股與停損停利SOP，信心92/100，已用真實資料
回測驗證勝率33.5%），刻意先從這一條已被回測證實的規則起步。這次追加R-SCREEN-11（底部
狹幅盤整大量紅K突破鎖股，信心89/100）與R-SCREEN-15（緩漲上升軌道線突破大量長紅K做多，
信心88/100）——246條規則庫其實已經100%都有程式實作(`scripts/check_rule_coverage.py`
可查)，差別只在於這裡有沒有把它「接進每日自動選股」這一層；這兩條都是清楚的做多進場
訊號、只需要OHLCV資料(不像R-SCREEN-05需要股本/營收/三大法人等本專案還沒抓取的基本面
資料)，且各自能重用既有的building block(`src/indicators/consolidation.py`的橫盤突破
偵測、`src/patterns/chart_overlays.py`的上升軌道線)，不需要另外新寫底層演算法。依使用者
指示，這次先接上觀察實際選股表現，不像R-TREND-14那樣要求先個別回測驗證勝率。

之後要加其他規則的每日篩選時，比照這裡的模式各自寫一個獨立的 screen_* 函式（輸入df，
輸出候選dict或None），再由 screen_all_stocks 或 daily_pipeline.py 呼叫端合併多個screen
函式的結果即可，不需要重寫這一層。
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from src.data import storage
from src.indicators.candles import is_mid_long_red_candle
from src.indicators.consolidation import detect_consolidation_breakout
from src.indicators.moving_average import sma
from src.indicators.trend import (
    bull_short_term_entry_ready,
    bull_short_term_stop_loss,
    daily_bull_trend_state,
)
from src.patterns import chart_overlays
from src.screener.screening_rules import narrow_range_bottom_breakout, slow_rally_channel_breakout

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
        "signal_name": "R-TREND-14多頭短線進場",
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
        "signal_name": "R-SCREEN-11底部盤整突破鎖股",
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
        "signal_name": "R-SCREEN-15緩漲軌道突破做多",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": "緩漲上升軌道線大量長紅K突破＋量能達20日均量2倍以上",
    }


_SCREEN_FUNCTIONS = (
    screen_bull_short_term_entry,
    screen_narrow_range_bottom_breakout,
    screen_slow_rally_channel_breakout,
)


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
