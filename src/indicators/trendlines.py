"""切線軌道線分類：切線與軌道線的畫法演算法（R-LINE-01/02/03/04/05/06/09/11/14/15）。

使用者特別交代要寫清楚「底底高、頭頭低、切線」到底怎麼畫，這裡把書中步驟逐條轉譯成可
重現的演算法，不是抽象概念描述。所有切線/軌道線都用兩點 (x1,y1)-(x2,y2) 表示的線性
函式，x是K棒在時間序列中的整數位置(bar index)，y是價格；轉折低點的y必須是「含下影線
的最低價」，轉折高點的y必須是「含上影線的最高價」，不可用收盤價或實體價代替——這是
書中反覆強調、最容易被誤植的細節，呼叫端組出 LinePoint 時務必用 high/low 而非 close。

「線不蓋線」是判斷畫線對錯的唯一書面標準：A、B兩個取點之間的所有K棒（含影線）都必須
落在切線上方(上升切線)或下方(下降切線)，否則此線畫錯，須換一組更新的點重新畫。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import pandas as pd

from src.rule_registry import implements_rule


class LinePoint(NamedTuple):
    """一個取線用的轉折點：x為K棒整數位置，y為含影線的最高/最低價。"""

    x: int
    y: float


@dataclass
class TrendLine:
    """由兩點決定的直線(切線/軌道線)。role可在跌破/突破後被改寫(支撐壓力角色互換)。"""

    a: LinePoint
    b: LinePoint
    role: str = "support"

    @property
    def slope(self) -> float:
        return (self.b.y - self.a.y) / (self.b.x - self.a.x)

    def at(self, x: int) -> float:
        return self.a.y + self.slope * (x - self.a.x)

    @classmethod
    def through_point_with_slope(cls, point: LinePoint, slope: float, role: str = "support") -> "TrendLine":
        """建構一條「過指定點、斜率與既有切線相同」的平行線（軌道線畫法核心）。"""
        other = LinePoint(x=point.x + 1, y=point.y + slope)
        return cls(a=point, b=other, role=role)


def fit_line_through(a: LinePoint, b: LinePoint, role: str = "support") -> TrendLine:
    return TrendLine(a=a, b=b, role=role)


def passes_line_not_covering_check(a: LinePoint, b: LinePoint, high: pd.Series, low: pd.Series, is_up: bool) -> bool:
    """線不蓋線檢查：上升切線要求A、B之間所有K棒(含影線)不可跌破切線；下降切線方向相反(不可突破)。"""
    line = fit_line_through(a, b)
    for x in range(a.x + 1, b.x):
        if is_up:
            if low.iloc[x] < line.at(x):
                return False
        else:
            if high.iloc[x] > line.at(x):
                return False
    return True


@implements_rule("R-LINE-01")
def find_up_tangent_line(bottoms: list[LinePoint], high: pd.Series, low: pd.Series) -> TrendLine | None:
    """上升切線：連接兩個「底底高」轉折低點。從最新一組往回找，取第一組通過線不蓋線檢查的配對。"""
    for i in range(len(bottoms) - 2, -1, -1):
        a, b = bottoms[i], bottoms[i + 1]
        if b.y <= a.y:
            continue
        if passes_line_not_covering_check(a, b, high, low, is_up=True):
            return fit_line_through(a, b, role="support")
    return None


@implements_rule("R-LINE-02")
def find_down_tangent_line(tops: list[LinePoint], high: pd.Series, low: pd.Series) -> TrendLine | None:
    """下降切線：連接兩個「頭頭低」轉折高點，與上升切線完全對稱。"""
    for i in range(len(tops) - 2, -1, -1):
        a, b = tops[i], tops[i + 1]
        if b.y >= a.y:
            continue
        if passes_line_not_covering_check(a, b, high, low, is_up=False):
            return fit_line_through(a, b, role="resistance")
    return None


@implements_rule("R-LINE-03")
def identify_original_up_tangent(
    bottoms: list[LinePoint], high: pd.Series, low: pd.Series, close: pd.Series, trend_state: str
) -> dict | None:
    """原始上升切線：空頭確認後第一組底底高低點畫出的切線，作為後續盤整區的下頸線，追蹤突破/跌破/反轉失敗。"""
    if trend_state != "空頭":
        return None

    original_line, confirm_b_x = None, None
    for i in range(len(bottoms) - 1):
        a, b = bottoms[i], bottoms[i + 1]
        if b.y > a.y and passes_line_not_covering_check(a, b, high, low, is_up=True):
            original_line = fit_line_through(a, b, role="support")
            confirm_b_x = b.x
            break
    if original_line is None:
        return None

    status, confirmed_x = "觀察中", None
    for x in range(confirm_b_x + 1, len(close)):
        if close.iloc[x] > original_line.at(x):
            status, confirmed_x = "多頭確認：盤整向上突破原始上升切線", x
            break
        if close.iloc[x] < original_line.at(x):
            status = "空頭續跌：盤整向下跌破原始上升切線（下頸線失守）"
            break

    if status.startswith("多頭確認"):
        for x in range(confirmed_x + 1, len(close)):
            if close.iloc[x] < original_line.at(x):
                status = "反轉失敗：空頭ABC反彈修正結束，空頭續跌"
                break

    return {"line": original_line, "status": status}


@implements_rule("R-LINE-04")
def identify_original_down_tangent(
    tops: list[LinePoint], high: pd.Series, low: pd.Series, close: pd.Series, trend_state: str
) -> dict | None:
    """原始下降切線：多頭確認後第一組頭頭低高點畫出的切線，作為後續盤整區的上頸線，與R-LINE-03完全對稱。"""
    if trend_state != "多頭":
        return None

    original_line, confirm_b_x = None, None
    for i in range(len(tops) - 1):
        a, b = tops[i], tops[i + 1]
        if b.y < a.y and passes_line_not_covering_check(a, b, high, low, is_up=False):
            original_line = fit_line_through(a, b, role="resistance")
            confirm_b_x = b.x
            break
    if original_line is None:
        return None

    status, confirmed_x = "觀察中", None
    for x in range(confirm_b_x + 1, len(close)):
        if close.iloc[x] < original_line.at(x):
            status, confirmed_x = "空頭確認：盤整向下跌破原始下降切線（上頸線失守）", x
            break
        if close.iloc[x] > original_line.at(x):
            status = "多頭續漲：盤整向上突破原始下降切線"
            break

    if status.startswith("空頭確認"):
        for x in range(confirmed_x + 1, len(close)):
            if close.iloc[x] > original_line.at(x):
                status = "反轉失敗：多頭ABC回檔修正結束，多頭續漲"
                break

    return {"line": original_line, "status": status}


@implements_rule("R-LINE-05")
def update_up_sub_tangent_lines(bottoms: list[LinePoint], high: pd.Series, low: pd.Series, existing_lines: list[TrendLine]) -> list[TrendLine]:
    """隨機上升次切線：每出現一組新的底底高低點，就用最新2點重畫一條，動態累加進線的歷史清單。"""
    if len(bottoms) < 2:
        return existing_lines
    a, b = bottoms[-2], bottoms[-1]
    if b.y <= a.y or not passes_line_not_covering_check(a, b, high, low, is_up=True):
        return existing_lines
    existing_lines.append(fit_line_through(a, b, role="support"))
    return existing_lines


@implements_rule("R-LINE-06")
def update_down_sub_tangent_lines(tops: list[LinePoint], high: pd.Series, low: pd.Series, existing_lines: list[TrendLine]) -> list[TrendLine]:
    """隨機下降次切線：與隨機上升次切線完全對稱，每出現新的頭頭低高點就重畫一條。"""
    if len(tops) < 2:
        return existing_lines
    a, b = tops[-2], tops[-1]
    if b.y >= a.y or not passes_line_not_covering_check(a, b, high, low, is_up=False):
        return existing_lines
    existing_lines.append(fit_line_through(a, b, role="resistance"))
    return existing_lines


@implements_rule("R-LINE-09", "R-SR-06")
def support_strength_by_touch_count(touch_count: int) -> str:
    """上升切線支撐力道遞減：第1次觸線支撐最強，第2次約50%成功率，第3次(含)以後容易被跌破。"""
    if touch_count <= 1:
        return "支撐最強"
    if touch_count == 2:
        return "支撐約50%成功率"
    return "容易被跌破"


@implements_rule("R-LINE-09")
def angle_strength(line_history: list[TrendLine]) -> list[str]:
    """比較相鄰兩條(原始/次)切線的斜率：角度變陡(斜率增)=走勢轉強；角度變平(斜率減)=走勢轉弱。"""
    results = []
    for prev_line, curr_line in zip(line_history, line_history[1:]):
        if curr_line.slope > prev_line.slope:
            results.append("走勢轉強（角度變陡）")
        elif curr_line.slope < prev_line.slope:
            results.append("走勢轉弱（角度變平）")
        else:
            results.append("走勢持平")
    return results


@implements_rule("R-LINE-10", "R-SR-07")
def resistance_strength_by_touch_count(touch_count: int) -> str:
    """下降切線壓力力道遞減：與上升切線支撐遞減完全對稱，第1次反彈觸線壓力最強，第3次以後容易被突破。"""
    if touch_count <= 1:
        return "壓力最強"
    if touch_count == 2:
        return "壓力約50%成功率"
    return "容易被突破"


@implements_rule("R-LINE-10")
def angle_strength_down(line_history: list[TrendLine]) -> list[str]:
    """下降切線角度強弱：方向性與上升切線非鏡射對稱——斜率(絕對值)變陡反而判定空頭轉弱(接近竭盡)，趨緩判定轉強(延續力道更強)，按書中原文字面實作。"""
    results = []
    for prev_line, curr_line in zip(line_history, line_history[1:]):
        abs_prev, abs_curr = abs(prev_line.slope), abs(curr_line.slope)
        if abs_curr > abs_prev:
            results.append("走勢轉弱（下降角度變陡，按書中原文字面）")
        elif abs_curr < abs_prev:
            results.append("走勢轉強（下降角度趨緩，按書中原文字面）")
        else:
            results.append("走勢持平")
    return results


@implements_rule("R-LINE-12", "R-SR-07")
def classify_down_line_breakout_and_role_swap(line_history: list[TrendLine], x: int, close: pd.Series) -> dict:
    """突破下降切線分級：突破最新一條=反彈修正訊號；連續突破更早的線=警示等級升高。被突破的線角色互換為支撐。"""
    broken_lines = []
    for line in reversed(line_history):
        if close.iloc[x] > line.at(x):
            broken_lines.append(line)
        else:
            break

    if not broken_lines:
        status = "未突破，空頭結構維持"
    elif len(broken_lines) == 1:
        status = "突破最新一條下降切線：反彈修正訊號（書中標題用詞：空頭轉強，待覆核）"
    else:
        status = f"連續突破{len(broken_lines)}條下降切線（含更早的切線）：趨勢反轉訊號，警示等級隨突破條數升高"

    for line in broken_lines:
        line.role = "support"

    return {"status": status, "broken_lines": broken_lines}


@implements_rule("R-LINE-11", "R-SR-06")
def classify_break_and_role_swap(line_history: list[TrendLine], x: int, close: pd.Series) -> dict:
    """跌破分級：跌破最新一條=回檔修正警訊；連續跌破更早的線=警示等級升高。被跌破的線角色互換為壓力。"""
    broken_lines = []
    for line in reversed(line_history):
        if close.iloc[x] < line.at(x):
            broken_lines.append(line)
        else:
            break

    if not broken_lines:
        status = "未跌破，多頭結構維持"
    elif len(broken_lines) == 1:
        status = "跌破最新一條上升切線：回檔修正警訊（多頭轉弱）"
    else:
        status = f"連續跌破{len(broken_lines)}條上升切線（含更早的切線）：趨勢反轉警訊，警示等級隨跌破條數升高"

    for line in broken_lines:
        line.role = "resistance"

    return {"status": status, "broken_lines": broken_lines}


@implements_rule("R-LINE-13")
def exit_observation_period_state(
    resumes_original_direction: bool = False,
    enters_consolidation: bool = False,
    reverses_trend_confirmed: bool = False,
) -> str:
    """切線失守後只有3種可能方向：原方向確認優先判定，其次反轉確認，再其次盤整；否則維持退出觀察期不進場。"""
    if resumes_original_direction:
        return "原方向確認，依原方向進場"
    if reverses_trend_confirmed:
        return "反轉確認，依新方向進場"
    if enters_consolidation:
        return "轉入盤整，退出觀察期延續"
    return "退出觀察期，尚無確認訊號，不可進場"


@implements_rule("R-LINE-13")
def consolidation_watch_resolution(breaks_upward: bool = False, breaks_downward: bool = False) -> str | None:
    """切線失守後轉入盤整，持續監控直到盤整被突破(依多頭方向進場)或跌破(依空頭方向進場)。"""
    if breaks_upward:
        return "盤整向上突破，依多頭方向進場"
    if breaks_downward:
        return "盤整向下跌破，依空頭方向進場"
    return None


@implements_rule("R-LINE-14")
def draw_up_channel_line(tangent_line: TrendLine, high: pd.Series) -> TrendLine:
    """上升軌道線：取上升切線A、B兩低點之間的相對最高點M(含上影線)，過M畫一條與切線平行的壓力線。"""
    between = range(tangent_line.a.x + 1, tangent_line.b.x)
    m_x = max(between, key=lambda x: high.iloc[x])
    m = LinePoint(x=m_x, y=high.iloc[m_x])
    return TrendLine.through_point_with_slope(m, tangent_line.slope, role="resistance")


@implements_rule("R-LINE-14")
def check_channel_breakout(channel_line: TrendLine, x: int, close: pd.Series) -> bool:
    return close.iloc[x] > channel_line.at(x)


@implements_rule("R-LINE-15")
def draw_down_channel_line(tangent_line: TrendLine, low: pd.Series) -> TrendLine:
    """下降軌道線：取下降切線A、B兩高點之間的相對最低點M(含下影線)，過M畫一條與切線平行的支撐線。"""
    between = range(tangent_line.a.x + 1, tangent_line.b.x)
    m_x = min(between, key=lambda x: low.iloc[x])
    m = LinePoint(x=m_x, y=low.iloc[m_x])
    return TrendLine.through_point_with_slope(m, tangent_line.slope, role="support")


@implements_rule("R-LINE-15")
def check_channel_breakdown(channel_line: TrendLine, x: int, close: pd.Series) -> bool:
    return close.iloc[x] < channel_line.at(x)


@implements_rule("R-LINE-07")
def draw_up_rapid_tangent_line(bottoms: list[LinePoint], high: pd.Series, low: pd.Series) -> TrendLine | None:
    """上升急切線：急漲區間內連接第一個與最新低點，取樣跨度遠短於一般切線，仍須通過線不蓋線檢查。"""
    if len(bottoms) < 2:
        return None
    a, b = bottoms[0], bottoms[-1]
    if not passes_line_not_covering_check(a, b, high, low, is_up=True):
        return None
    return fit_line_through(a, b, role="support")


@implements_rule("R-LINE-07")
def check_up_rapid_line_exit(line: TrendLine, x: int, close: pd.Series) -> bool:
    """跌破上升急切線：短線停利/出場訊號。"""
    return close.iloc[x] < line.at(x)


@implements_rule("R-LINE-08")
def draw_down_rapid_tangent_line(tops: list[LinePoint], high: pd.Series, low: pd.Series) -> TrendLine | None:
    """下降急切線：急跌區間內連接第一個與最新高點，與上升急切線完全對稱。"""
    if len(tops) < 2:
        return None
    a, b = tops[0], tops[-1]
    if not passes_line_not_covering_check(a, b, high, low, is_up=False):
        return None
    return fit_line_through(a, b, role="resistance")


@implements_rule("R-LINE-08")
def check_down_rapid_line_cover(line: TrendLine, x: int, close: pd.Series) -> bool:
    """突破下降急切線：空單短線回補停利訊號。"""
    return close.iloc[x] > line.at(x)
