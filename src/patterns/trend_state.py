"""趨勢狀態分類（Layer 1/2組裝）：串接R-TREND-01(轉折點取點演算法)＋R-TREND-03/04(頭頭高
底底高/頭頭低底底低多空趨勢判定)，算出「今天」屬於多頭／空頭／盤整的哪一種。

這是本專案至今唯一「逐日判斷現在算什麼趨勢」的地方——`src/indicators/`裡有大量函式的
docstring寫著需要「趨勢位置模組（尚未實作）」或「外部注入」的`trend`/`is_bull_trend`/
`is_bear_trend`參數，指的就是這裡；建好之後，`src/screener/rule_scan.py`才能進一步接上
一批原本被這個前置需求卡住的規則庫規則（KD依趨勢判讀、布林通道買訊①②/做空訊①②、
黃金死亡交叉配合主趨勢判讀等）。

⚠️ 這裡只解決「現在算多頭還是空頭」，不解決「現在處於多頭的哪個階段(起漲/主升段/末升段/
高檔)」這個更細的「趨勢位置」問題——後者是另一批規則(R-CANDLE-06/08/09/10/11等candle_
patterns_2.py的函式、R-VOLPRICE-03/04/09/10等)需要的`is_at_high`/`is_at_low`/
`wave_pattern_bullish`類參數，本模組不提供，維持排除在外。

⚠️ 2026-07-24修正：一開始只用單一N=5(短線)判斷「目前趨勢」，被使用者指出「用幾天資料
判斷大趨勢太草率」——書中R-TREND-01原文其實明確定義了短/中/長三種天期(5日/10日/20日)，
同一套轉折點演算法只是套用不同均線天期，天期越短轉折點越多越敏感(適合短線)、越長轉折點
越少越平滑(適合中長期)，三者本來就該分開判斷、不能只看其中一種代表「大趨勢」。這裡改成
`classify_trend_states_multi_horizon()`一次算出短/中/長三種天期各自的趨勢狀態，呼叫端
(UI顯示/rule_scan.py)不應該再只挑一種當作唯一的「目前趨勢」。
"""

from __future__ import annotations

from typing import NamedTuple

import pandas as pd

from src.indicators.pivots import compute_turning_points
from src.indicators.trend import is_bear_trend, is_bull_trend

TREND_BULL = "多頭"
TREND_BEAR = "空頭"
TREND_RANGE = "盤整"

# R-TREND-01書中原文明確定義的三種轉折波天期：「5日、10日、20日均線只是套用同一套演算法
# 時的參數不同，均線週期越短轉折點越多越敏感(適合短線操作)，週期越長轉折點越少越平滑
# (適合判斷中長期趨勢及支撐壓力)」。key的順序即為顯示順序(短→中→長)。
TREND_HORIZONS: dict[str, int] = {"短線": 5, "中線": 10, "長線": 20}


class TrendHorizonResult(NamedTuple):
    n: int          # 轉折波取點所用的均線天期(對應下面的MA{n})
    trend: str       # "多頭"/"空頭"/"盤整"


def classify_trend_state(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 5) -> str:
    """回傳「今天」(傳入資料的最後一列)在「單一天期」下的趨勢狀態：多頭(頭頭高底底高)／
    空頭(頭頭低底底低)／盤整(兩者皆不成立，含轉折點不足2組頭與2組底的情況)。

    n對應R-TREND-01的均線天期(5=短線轉折波/10=中線/20=長線，書中口訣「突破5日均取低點，
    跌破5均取高點」)。多數呼叫端應該改用下面的`classify_trend_states_multi_horizon()`
    一次拿到短/中/長三種天期的結果，不要只挑單一天期代表「目前趨勢」；這個單一天期版本
    保留給明確只需要某一種天期的呼叫端使用(例如`rule_scan.py`裡KD依趨勢判讀等既有規則，
    書中沒有另外要求區分短中長天期)。要評估「某一天」的趨勢狀態，呼叫端要自行把
    high/low/close截到那一天為止——跟daily_screener.py裡各screen_*函式「今天=資料
    最後一列」的既有慣例相同。
    """
    turning_points = compute_turning_points(high, low, close, n=n)
    heads = [tp.price for tp in turning_points if tp.type == "head"]
    bottoms = [tp.price for tp in turning_points if tp.type == "bottom"]

    if is_bull_trend(heads, bottoms):
        return TREND_BULL
    if is_bear_trend(heads, bottoms):
        return TREND_BEAR
    return TREND_RANGE


def classify_trend_states_multi_horizon(
    high: pd.Series, low: pd.Series, close: pd.Series,
) -> dict[str, TrendHorizonResult]:
    """回傳{"短線": TrendHorizonResult(n=5, trend=...), "中線": TrendHorizonResult(n=10, ...),
    "長線": TrendHorizonResult(n=20, ...)}，依R-TREND-01書中定義的短/中/長三種轉折波天期
    分別判斷。三者可能不一致(例如短線走空、長線仍是多頭)，這正是分開判斷的意義所在——
    呼叫端(UI)應該三個都顯示，不要合併成一個籠統的「目前趨勢」。
    """
    return {
        label: TrendHorizonResult(n=n, trend=classify_trend_state(high, low, close, n=n))
        for label, n in TREND_HORIZONS.items()
    }
