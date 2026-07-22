"""均線分類：黃金交叉／死亡交叉（R-MA-13/14）與配合主趨勢判讀（R-MA-15）。

交叉一定是短天期均線往長天期均線方向穿越（因為短均線反應速度較快，見
R-MA-03），交叉本身只是「事件」，其多空意義必須配合當時的主趨勢解讀。
"""

from __future__ import annotations

import pandas as pd

from src.rule_registry import implements_rule


@implements_rule("R-MA-13")
def is_golden_cross(ma_short: pd.Series, ma_long: pd.Series) -> pd.Series:
    """黃金交叉：前一日短均線<=長均線，當日短均線已上穿長均線。"""
    was_below_or_equal = ma_short.shift(1) <= ma_long.shift(1)
    now_above = ma_short > ma_long
    return (was_below_or_equal & now_above).fillna(False)


@implements_rule("R-MA-14")
def is_death_cross(ma_short: pd.Series, ma_long: pd.Series) -> pd.Series:
    """死亡交叉：前一日短均線>=長均線，當日短均線已下穿長均線。"""
    was_above_or_equal = ma_short.shift(1) >= ma_long.shift(1)
    now_below = ma_short < ma_long
    return (was_above_or_equal & now_below).fillna(False)


@implements_rule("R-MA-15")
def interpret_cross(main_trend: str, cross_event: str) -> str:
    """同一個交叉事件在不同主趨勢下的操作意涵完全不同，這是判斷時不可省略的前置條件。

    main_trend: "多頭" 或 "空頭"
    cross_event: "黃金交叉" 或 "死亡交叉"
    """
    if main_trend == "多頭":
        if cross_event == "黃金交叉":
            return "買進參考位置（回檔結束、反轉上漲）"
        if cross_event == "死亡交叉":
            return "獲利賣出參考位置（出多單，回檔修正）"
    elif main_trend == "空頭":
        if cross_event == "死亡交叉":
            return "做空參考位置（反彈結束、續跌）"
        if cross_event == "黃金交叉":
            return "獲利回補參考位置（回補空單，反彈修正）"
    return "無明確訊號"
