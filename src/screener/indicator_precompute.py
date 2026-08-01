"""每日均線/SAR快取的計算邏輯：對「傳入的這段df」只算一次(不是逐個目標日期各自重算一次
回看窗口)，供`src/screener/daily_screener.py`的`run_screen_and_store()`/
`refresh_indicator_window()`、`scripts/backfill_daily_indicators.py`一次性回補共用。

計算本身完全重用已驗證過的純函式(不重新定義均線/SAR的計算方式)：
`src/indicators/moving_average.py`的`compute_ma_set()`、
`src/indicators/parabolic_sar.py`的`compute_sar()`。見`src/data/schema.sql`的
`daily_indicators`表說明。

⚠️ 2026-08-02實測發現的效能陷阱：`compute_sar()`/`compute_ma_set()`對整個df只算一次，
但這個「一次」的成本是O(df的總天數)，不是O(target_dates的數量)——一開始誤以為
`run_screen_and_store()`每次只算iso_date一天「成本很低」，實測卻要對~2300檔股票的
`load_trailing_frames()`全部歷史(當時已累積到~860天)各走一遍SAR遞迴，耗時69秒，
拖慢了使用者按「立即重新篩選」的體感速度，且會隨著DB歷史持續累積而越來越慢，不是
一次性的固定成本。日常更新(不是backfill)時，呼叫端應該只傳入`LIVE_UPDATE_LOOKBACK_
DAYS`天的尾端資料(`df.tail(LIVE_UPDATE_LOOKBACK_DAYS)`)，不是整段歷史——這個天數已經
足夠讓SAR的初始種子影響收斂穩定(2026-08-01驗證SAR翻轉bug時，250天窗口算出的結果已經
跟ref-project的獨立驗證數字完全吻合，這裡抓400天留更寬裕的緩衝)，效能上跟target_dates
數量無關，只跟傳入的df長度成正比。`scripts/backfill_daily_indicators.py`的一次性回補
不受這個限制、故意傳入全部歷史(見該檔案說明)，只有「日常增量更新」的呼叫端需要自己
先裁切。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.indicators.moving_average import FULL_PERIODS, compute_ma_set
from src.indicators.parabolic_sar import compute_sar

# 見上方模組docstring的效能陷阱說明：日常增量更新(不是backfill)呼叫compute_indicator_
# rows()前，應該先把df裁切成只留最近這麼多天，成本才不會隨DB歷史持續累積而越來越慢。
LIVE_UPDATE_LOOKBACK_DAYS = 400


def _sar_flip_days_ago_series(sar_bull: pd.Series) -> list[int | None]:
    """對序列裡每一天算「目前(以該天為準)的方向是第幾天前翻轉進來的」，1代表當天就是
    翻轉日——跟`src.indicators.parabolic_sar.sar_flip_days_ago()`對「整段序列最後一天」
    算出來的結果語意一致(見`tests/test_indicator_precompute.py`驗證兩者在同一天算出的
    值相同)，差別是這裡一次算出序列裡每一天的對應值，不是逐天各自重新掃描一次序列——
    對全部歷史回補是O(n)，不是O(n²)。

    第一天(index 0)視為「起始」，flip_days_ago=1：`compute_sar()`對前兩天固定給同一個
    初始方向(見該函式docstring)，沒有更早的資料可以比較，用1當作測量起點是合理的預設值，
    不是真正意義上的「翻轉」。
    """
    n = len(sar_bull)
    result: list[int | None] = [None] * n
    if n == 0:
        return result
    days_since_flip = 1
    result[0] = days_since_flip
    for i in range(1, n):
        if bool(sar_bull.iloc[i]) != bool(sar_bull.iloc[i - 1]):
            days_since_flip = 1
        else:
            days_since_flip += 1
        result[i] = days_since_flip
    return result


def _safe_float(value: object) -> float | None:
    return None if pd.isna(value) else float(value)


def compute_indicator_rows(stock_id: str, df: pd.DataFrame, target_dates: set[str]) -> list[dict]:
    """`df`是這檔股票依date排序、DatetimeIndex的OHLCV(跟`daily_screener.
    load_trailing_frames()`回傳格式一致)，只回傳`target_dates`("YYYY-MM-DD"字串集合)
    這些日期需要的列，不是整段歷史都回傳(呼叫端視情境決定要哪幾天：日常只要今天一天，
    排程往回刷新窗口，backfill要全部歷史)。

    均線/SAR都是對整個`df`只算一次(而不是每個target_date各自重算一次回看窗口)，效能上
    遠比「逐日期獨立重算」快，尤其backfill全部歷史時差異更明顯。
    """
    if df.empty:
        return []
    ma_frame = compute_ma_set(df["close"], periods=FULL_PERIODS)
    sar_bull, sar_values = compute_sar(df["high"], df["low"], df["close"])
    flip_days_ago_list = _sar_flip_days_ago_series(sar_bull)
    sar_len = len(sar_bull)

    date_strs = df.index.strftime("%Y-%m-%d")
    updated_at = datetime.now().isoformat()
    rows: list[dict] = []
    for i, date_str in enumerate(date_strs):
        if date_str not in target_dates:
            continue
        has_sar = i < sar_len
        rows.append({
            "stock_id": stock_id,
            "date": date_str,
            "ma5": _safe_float(ma_frame["MA5"].iloc[i]),
            "ma10": _safe_float(ma_frame["MA10"].iloc[i]),
            "ma20": _safe_float(ma_frame["MA20"].iloc[i]),
            "ma60": _safe_float(ma_frame["MA60"].iloc[i]),
            "ma120": _safe_float(ma_frame["MA120"].iloc[i]),
            "ma240": _safe_float(ma_frame["MA240"].iloc[i]),
            "sar_value": _safe_float(sar_values.iloc[i]) if has_sar else None,
            "sar_is_bull": int(bool(sar_bull.iloc[i])) if has_sar else None,
            "sar_flip_days_ago": flip_days_ago_list[i] if has_sar else None,
            "updated_at": updated_at,
        })
    return rows
