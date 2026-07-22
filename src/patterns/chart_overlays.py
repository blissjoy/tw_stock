"""切線/軌道線與支撐壓力的圖表疊圖資料組裝（Layer 4 應用層）：直接重用已實作的轉折點取點
演算法(indicators/pivots.py)與切線/軌道線畫法(indicators/trendlines.py)、支撐壓力角色判定
(indicators/support_resistance.py)，不重新發明演算法，只是把這些函式串起來、轉成圖表需要
的座標資料。

trendlines.py 的 LinePoint.x 是「K棒的整數位置」，但傳進來的df是以日期為index，這裡負責
兩者之間的轉換：算線時用reset_index後的positional Series，畫線時再把整數位置換回實際日期。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.pivots import TurningPoint, compute_turning_points
from src.indicators.support_resistance import classify_bottom_role, classify_head_role
from src.indicators.trendlines import (
    LinePoint,
    TrendLine,
    draw_down_channel_line,
    draw_up_channel_line,
    find_down_tangent_line,
    find_up_tangent_line,
)


def compute_trendlines(df: pd.DataFrame, ma_window: int = 5) -> dict[str, TrendLine]:
    """從OHLC資料算出目前的上升/下降切線，以及對應的上升/下降軌道線(若兩點間有足夠K棒可取中間點)。

    回傳dict，key可能包含："up_tangent"/"down_tangent"/"up_channel"/"down_channel"，
    找不到符合條件的切線時，對應key就不會出現(而不是回傳None佔位)。
    """
    if len(df) < ma_window + 2:
        return {}

    high_pos = df["high"].reset_index(drop=True)
    low_pos = df["low"].reset_index(drop=True)
    close_pos = df["close"].reset_index(drop=True)

    turning_points = compute_turning_points(df["high"], df["low"], df["close"], n=ma_window)
    position_of = {label: pos for pos, label in enumerate(df.index)}

    bottoms = [
        LinePoint(x=position_of[tp.index], y=tp.price) for tp in turning_points if tp.type == "bottom"
    ]
    tops = [
        LinePoint(x=position_of[tp.index], y=tp.price) for tp in turning_points if tp.type == "head"
    ]

    lines: dict[str, TrendLine] = {}

    up_tangent = find_up_tangent_line(bottoms, high_pos, low_pos)
    if up_tangent is not None:
        lines["up_tangent"] = up_tangent
        if up_tangent.b.x - up_tangent.a.x > 1:
            lines["up_channel"] = draw_up_channel_line(up_tangent, high_pos)

    down_tangent = find_down_tangent_line(tops, high_pos, low_pos)
    if down_tangent is not None:
        lines["down_tangent"] = down_tangent
        if down_tangent.b.x - down_tangent.a.x > 1:
            lines["down_channel"] = draw_down_channel_line(down_tangent, low_pos)

    return lines


def trendline_to_xy(line: TrendLine, df: pd.DataFrame) -> tuple[list, list[float]]:
    """把一條TrendLine換算成圖表要畫的(日期陣列, 價格陣列)，從起點畫到資料最後一天(切線畫法
    慣例是延伸到最新一天，不是只畫在原本取點的兩點之間)。"""
    start_x = max(line.a.x, 0)
    end_x = len(df) - 1
    xs = list(range(start_x, end_x + 1))
    dates = [df.index[x] for x in xs]
    prices = [line.at(x) for x in xs]
    return dates, prices


def compute_support_resistance_levels(df: pd.DataFrame, ma_window: int = 5, max_levels: int = 3) -> list[dict]:
    """取最近幾個轉折高/低點當支撐壓力參考線，用R-SR-01/02判斷目前的角色(支撐/壓力)。
    回傳依時間新到舊排序、最多 max_levels*2 筆(頭/底各自最多max_levels筆)的清單，
    每筆為 {"price": 價位, "type": "head"或"bottom", "role": "支撐"或"壓力", "date": 該轉折點日期}。
    """
    if df.empty:
        return []

    turning_points: list[TurningPoint] = compute_turning_points(df["high"], df["low"], df["close"], n=ma_window)
    if not turning_points:
        return []

    current_price = float(df["close"].iloc[-1])
    heads = [tp for tp in turning_points if tp.type == "head"][-max_levels:]
    bottoms = [tp for tp in turning_points if tp.type == "bottom"][-max_levels:]

    levels: list[dict] = []
    for tp in heads:
        role = classify_head_role(tp.price, current_price, has_broken_above=current_price > tp.price)
        levels.append({"price": tp.price, "type": "head", "role": role, "date": tp.index})
    for tp in bottoms:
        role = classify_bottom_role(tp.price, current_price, has_broken_below=current_price < tp.price)
        levels.append({"price": tp.price, "type": "bottom", "role": role, "date": tp.index})

    levels.sort(key=lambda lv: lv["date"])
    return levels
