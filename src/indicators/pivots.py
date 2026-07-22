"""趨勢判定分類（Layer 0）：轉折波定義與取點演算法（R-TREND-01）。

核心邏輯：用「收盤價相對均線的位置」把K線切成正價區段（收盤在均線上）與
負價區段（收盤在均線下）；每次收盤價由正轉負（收盤跌破均線）時，取「前面
正價區段＋當天跌破的K線」區間內的最高點（含上影線）為轉折高點（頭）；每次
收盤價由負轉正（收盤突破均線）時，取「前面負價區段＋當天突破的K線」區間內
的最低點（含下影線）為轉折低點（底）。5日/10日/20日轉折波只是均線天期不同，
演算法完全一致。

這是後續「頭頭高底底高」等趨勢判定規則的必要前置步驟，之後 Layer 1/2 會直接
拿這裡輸出的轉折點序列去判斷多空趨勢，因此輸出格式（依時間交替排列的頭/底）
是規則庫裡最常被引用的中介資料結構。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.indicators.moving_average import sma
from src.rule_registry import implements_rule

TurningType = Literal["head", "bottom"]


@dataclass(frozen=True)
class TurningPoint:
    type: TurningType   # "head"（轉折高點／頭）或 "bottom"（轉折低點／底）
    price: float
    index: object        # 對應 bars 的 index label（例如日期）


@implements_rule("R-TREND-01")
def compute_turning_points(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 5) -> list[TurningPoint]:
    """依收盤價相對 N 日均線的位置，算出交替排列的轉折高低點序列（頭1,底1,頭2,底2,...）。

    N=5 為短線轉折波、N=10 為中線轉折波、N=20 為長線轉折波，書中口訣：
    「突破5日均取低點，跌破5均取高點」。
    """
    ma = sma(close, n)
    turning_points: list[TurningPoint] = []

    state: str | None = None      # "positive"（收盤在均線上）或 "negative"（收盤在均線下）
    group_idx: list[int] = []      # 目前累積中的同狀態K線區段（存位置索引）

    valid_start = ma.first_valid_index()
    if valid_start is None:
        return turning_points

    positions = close.index.get_indexer([valid_start])[0]

    for i in range(positions, len(close)):
        if close.iloc[i] > ma.iloc[i]:
            cur = "positive"
        elif close.iloc[i] < ma.iloc[i]:
            cur = "negative"
        else:
            cur = state  # 收盤剛好等於均線：書中未定義，預設沿用前一狀態

        if state is None:
            state = cur
            group_idx = [i]
            continue

        if cur == state:
            group_idx.append(i)
            continue

        # 狀態切換：把當天K線併入取值區間（跌破/突破當天也算在內）
        group_idx.append(i)
        if state == "positive" and cur == "negative":
            head_pos = max(group_idx, key=lambda j: high.iloc[j])
            turning_points.append(TurningPoint("head", float(high.iloc[head_pos]), high.index[head_pos]))
        elif state == "negative" and cur == "positive":
            bottom_pos = min(group_idx, key=lambda j: low.iloc[j])
            turning_points.append(TurningPoint("bottom", float(low.iloc[bottom_pos]), low.index[bottom_pos]))
        state = cur
        group_idx = [i]

    return turning_points
