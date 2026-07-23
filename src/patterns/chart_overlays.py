"""切線/軌道線與支撐壓力的圖表疊圖資料組裝（Layer 4 應用層）：直接重用已實作的轉折點取點
演算法(indicators/pivots.py)與切線/軌道線畫法(indicators/trendlines.py)、支撐壓力角色判定
(indicators/support_resistance.py)，不重新發明演算法，只是把這些函式串起來、轉成圖表需要
的座標資料。

trendlines.py 的 LinePoint.x 是「K棒的整數位置」，但傳進來的df是以日期為index，這裡負責
兩者之間的轉換：算線時用reset_index後的positional Series，畫線時再把整數位置換回實際日期。

⚠️ **切線要用「動態更新」邏輯，不是「回頭搜尋任一組合法舊配對」**（2026-07-23修正，見
ai/PLAN.md）：R-LINE-01/02(`find_up_tangent_line`/`find_down_tangent_line`)是「原始切線」
的畫法——從最新往回搜尋，只要找到第一組通過線不蓋線檢查的配對就回傳，即使那組配對是很久
以前的舊轉折點。這對「畫出趨勢一開始的第一條切線」是對的，但對「現在(最新一天)這條切線
還算不算數」是錯的：如果最近的轉折點配對都不合法，往回搜尋可能找到一條對「今天」早已經
沒有意義的舊切線，卻誤導使用者以為它還在發揮支撐/壓力作用。

書中真正的做法是 R-LINE-05/06「隨機次切線動態更新」：只在每次出現新的一組「底底高/頭頭低」
時才重畫，若最新一組不合法就沿用「前一條已經畫出的」切線，不會往更早的歷史回頭找替代方案。
這裡改用 `update_up_sub_tangent_lines`/`update_down_sub_tangent_lines` 模擬「隨著資料逐日
推進、每次新轉折點出現時都檢查一次」的過程，取得依時間排序的切線歷史，`history[-1]` 就是
「以最新一天為準，當下仍在使用的那條切線」。

同時套用 R-LINE-11/12「跌破/突破分級與角色互換」：用最新一天的收盤價檢查這條線是否已經
被跌破(上升切線)或突破(下降切線)，若是，代表這條線的支撐/壓力身份已經互換，且對應的
`role`會被R-LINE-11/12的函式就地更新——呼叫端(dashboard)可以用這個role判斷該用「還在
發揮支撐作用」還是「已跌破轉壓力」的樣式顯示，不會像修正前那樣把一條早就無效的舊切線
畫得像還在支撐現在的股價一樣。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.pivots import TurningPoint, compute_turning_points
from src.indicators.support_resistance import classify_bottom_role, classify_head_role
from src.indicators.trendlines import (
    LinePoint,
    TrendLine,
    classify_break_and_role_swap,
    classify_down_line_breakout_and_role_swap,
    draw_down_channel_line,
    draw_up_channel_line,
    update_down_sub_tangent_lines,
    update_up_sub_tangent_lines,
)


def _build_up_tangent_history(bottoms: list[LinePoint], high: pd.Series, low: pd.Series) -> list[TrendLine]:
    """依R-LINE-05逐步模擬「每新增一個底部轉折點就檢查一次是否要重畫」的動態更新過程，
    回傳依時間排序的切線歷史(可能為空)。"""
    history: list[TrendLine] = []
    for i in range(2, len(bottoms) + 1):
        history = update_up_sub_tangent_lines(bottoms[:i], high, low, history)
    return history


def _build_down_tangent_history(tops: list[LinePoint], high: pd.Series, low: pd.Series) -> list[TrendLine]:
    """依R-LINE-06逐步模擬下降切線的動態更新過程，回傳依時間排序的切線歷史(可能為空)。"""
    history: list[TrendLine] = []
    for i in range(2, len(tops) + 1):
        history = update_down_sub_tangent_lines(tops[:i], high, low, history)
    return history


def compute_trendlines(df: pd.DataFrame, ma_window: int = 5) -> dict[str, TrendLine]:
    """從OHLC資料算出「以最新一天為準」目前仍在使用的上升/下降切線，以及對應的軌道線
    (若兩點間有足夠K棒可取中間點)。

    回傳dict，key可能包含："up_tangent"/"down_tangent"/"up_channel"/"down_channel"，
    找不到符合條件的切線時，對應key就不會出現(而不是回傳None佔位)。up_tangent/down_tangent
    的 `.role` 已依R-LINE-11/12套用最新收盤價的跌破/突破檢查更新過——role仍是原本的
    "support"/"resistance" 代表這條線目前仍在發揮作用；role已經互換(up_tangent變成
    "resistance"、down_tangent變成"support")代表這條線已經失效、角色反轉，呼叫端應該用
    不同樣式呈現，不能當成還在生效的原始意義畫。
    """
    if len(df) < ma_window + 2:
        return {}

    high_pos = df["high"].reset_index(drop=True)
    low_pos = df["low"].reset_index(drop=True)
    close_pos = df["close"].reset_index(drop=True)
    last_x = len(df) - 1

    turning_points = compute_turning_points(df["high"], df["low"], df["close"], n=ma_window)
    position_of = {label: pos for pos, label in enumerate(df.index)}

    bottoms = [
        LinePoint(x=position_of[tp.index], y=tp.price) for tp in turning_points if tp.type == "bottom"
    ]
    tops = [
        LinePoint(x=position_of[tp.index], y=tp.price) for tp in turning_points if tp.type == "head"
    ]

    lines: dict[str, TrendLine] = {}

    up_history = _build_up_tangent_history(bottoms, high_pos, low_pos)
    if up_history:
        classify_break_and_role_swap(up_history, last_x, close_pos)  # 就地更新role
        latest_up = up_history[-1]
        lines["up_tangent"] = latest_up
        if latest_up.b.x - latest_up.a.x > 1:
            lines["up_channel"] = draw_up_channel_line(latest_up, high_pos)

    down_history = _build_down_tangent_history(tops, high_pos, low_pos)
    if down_history:
        classify_down_line_breakout_and_role_swap(down_history, last_x, close_pos)  # 就地更新role
        latest_down = down_history[-1]
        lines["down_tangent"] = latest_down
        if latest_down.b.x - latest_down.a.x > 1:
            lines["down_channel"] = draw_down_channel_line(latest_down, low_pos)

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


def nearest_support_resistance(levels: list[dict], current_price: float) -> list[dict]:
    """從支撐壓力清單中，只取目前價格上下「各自最接近」的一條，不是全部一次疊上去——
    全部畫出來(可能有到6條)在圖上會太雜亂，使用者只需要離現價最近、最直接有參考意義的
    那兩條。以價位跟現價的相對位置篩選(不是用role標籤篩選)，因為角色互換後同一個
    role標籤未必仍對應原本「上/下」的相對位置。"""
    below = [lv for lv in levels if lv["price"] <= current_price]
    above = [lv for lv in levels if lv["price"] > current_price]

    nearest: list[dict] = []
    if below:
        nearest.append(max(below, key=lambda lv: lv["price"]))
    if above:
        nearest.append(min(above, key=lambda lv: lv["price"]))
    return nearest
