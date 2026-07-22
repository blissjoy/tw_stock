"""每日選股（Layer 4 應用層）：對每檔股票用「今天」的最新資料，判斷 R-TREND-14
（多頭短線選股與停損停利SOP，信心92/100，已用真實資料回測驗證勝率33.5%）的進場條件是否成立。

刻意只從這一條已被回測證實的規則起步，不在這次一次接上全部246條規則；之後要加其他規則
的每日篩選時，比照這裡的模式各自寫一個獨立的 screen_* 函式（輸入df，輸出候選dict或None），
再由 screen_all_stocks 或 daily_pipeline.py 呼叫端合併多個screen函式的結果即可，不需要
重寫這一層。
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from src.data import storage
from src.indicators.moving_average import sma
from src.indicators.trend import (
    bull_short_term_entry_ready,
    bull_short_term_stop_loss,
    daily_bull_trend_state,
)


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


def screen_all_stocks(stock_frames: dict[str, pd.DataFrame], min_days: int = 60) -> list[dict]:
    """對多檔股票批次跑 screen_bull_short_term_entry，回傳今天所有觸發訊號的候選清單。

    stock_frames: {stock_id: df}，df需已依date排序、index為date、含open/high/low/close/volume欄位。
    """
    candidates: list[dict] = []
    for stock_id, df in stock_frames.items():
        result = screen_bull_short_term_entry(df, min_days=min_days)
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

    這是刻意的設計：抓新資料(TWSE/TPEx)成本很高(TPEx經FinMind約4小時)，跟「用現有資料重算
    訊號」(純本地運算，通常幾秒內)分開，才能讓 dashboard 提供「立即重新篩選」這種不需要等待
    資料抓取的即時操作；scripts/daily_pipeline.py 抓完當天新資料後也呼叫同一份邏輯，避免重複實作。
    """
    if iso_date is None:
        iso_date = date.today().isoformat()

    frames = load_trailing_frames(conn, min_days=min_days)
    candidates = screen_all_stocks(frames, min_days=min_days)

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
