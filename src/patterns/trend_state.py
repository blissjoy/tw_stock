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
"""

from __future__ import annotations

import pandas as pd

from src.indicators.pivots import compute_turning_points
from src.indicators.trend import is_bear_trend, is_bull_trend

TREND_BULL = "多頭"
TREND_BEAR = "空頭"
TREND_RANGE = "盤整"


def classify_trend_state(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 5) -> str:
    """回傳「今天」(傳入資料的最後一列)的趨勢狀態：多頭(頭頭高底底高)／空頭(頭頭低底底低)／
    盤整(兩者皆不成立，含轉折點不足2組頭與2組底的情況)。

    n對應R-TREND-01的均線天期(5=短線轉折波/10=中線/20=長線，書中口訣「突破5日均取低點，
    跌破5均取高點」)，預設5日短線轉折波，跟本專案其他規則(R-TREND-14等)慣用的短線框架
    一致。要評估「某一天」的趨勢狀態，呼叫端要自行把high/low/close截到那一天為止——跟
    daily_screener.py裡各screen_*函式「今天=資料最後一列」的既有慣例相同。
    """
    turning_points = compute_turning_points(high, low, close, n=n)
    heads = [tp.price for tp in turning_points if tp.type == "head"]
    bottoms = [tp.price for tp in turning_points if tp.type == "bottom"]

    if is_bull_trend(heads, bottoms):
        return TREND_BULL
    if is_bear_trend(heads, bottoms):
        return TREND_BEAR
    return TREND_RANGE
