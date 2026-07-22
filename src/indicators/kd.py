"""技術指標分類：KD指標（R-INDICATOR-08/09/10/12）。

KD計算公式書中僅給出「5日範例的3日加總簡化式」，與業界常見的「RSV先算出後再取平滑移動
平均」不同，兩者數值會有差異，書中並未區辨——這裡採用書中原文的3日加總簡化式，因為那是
唯一有完整例題可驗證的版本；業界平滑移動平均式屬工程實作補充，此處不重複實作。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.crossovers import is_death_cross, is_golden_cross
from src.rule_registry import implements_rule


@implements_rule("R-INDICATOR-08")
def compute_kd(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9, d_period: int = 3) -> pd.DataFrame:
    """K＝RSV(N日)＝100*(C-Ln)/(Hn-Ln)；D＝最近d_period日「C-Ln」總和 除以 同期間「Hn-Ln」總和 *100（書中3日加總簡化式）。"""
    ln = low.rolling(window=n, min_periods=n).min()
    hn = high.rolling(window=n, min_periods=n).max()
    k = (close - ln) / (hn - ln) * 100
    numerator = (close - ln).rolling(window=d_period, min_periods=d_period).sum()
    denominator = (hn - ln).rolling(window=d_period, min_periods=d_period).sum()
    d = numerator / denominator * 100
    return pd.DataFrame({"K": k, "D": d})


@implements_rule("R-INDICATOR-09")
def kd_cross_signal_by_trend(k: pd.Series, d: pd.Series, trend: pd.Series) -> pd.Series:
    """KD交叉的意義依當下趨勢而定：多頭黃金=買點/死亡=賣點；空頭死亡=空點/黃金=回補點；盤整訊號皆無效。"""
    golden = is_golden_cross(k, d)
    dead = is_death_cross(k, d)
    is_bull, is_bear, is_range = trend == "多頭", trend == "空頭", trend == "盤整"
    signal = pd.Series(pd.NA, index=k.index, dtype="object")
    signal = signal.mask((is_bull & golden).fillna(False), "參考買點")
    signal = signal.mask((is_bull & dead).fillna(False), "多單參考賣出點")
    signal = signal.mask((is_bear & dead).fillna(False), "參考空點")
    signal = signal.mask((is_bear & golden).fillna(False), "空單參考回補點")
    signal = signal.mask((is_range & (golden | dead)).fillna(False), "訊號無效，不宜依KD交叉進出")
    return signal


@implements_rule("R-INDICATOR-10")
def select_kd_timeframe(trading_horizon: str) -> str:
    """依交易期程選用對應週期的K線資料餵入KD計算公式：短線用日線、中期用週線、長期用月線。"""
    mapping = {"短線": "日線", "中期": "週線", "長期": "月線"}
    if trading_horizon not in mapping:
        raise ValueError(f"trading_horizon 必須是 {list(mapping)} 之一，收到：{trading_horizon!r}")
    return mapping[trading_horizon]


@implements_rule("R-INDICATOR-11")
def is_high_dull(k: pd.Series, d: pd.Series, n: int = 3, threshold: float = 80.0) -> pd.Series:
    """高檔鈍化：K、D連續N天(預設3天，外部查證常見起始門檻)皆維持在80以上，KD已失去參考價值。"""
    overbought = (k >= threshold) & (d >= threshold)
    return overbought.rolling(n).apply(lambda window: bool(window.all()), raw=True).fillna(0).astype(bool)


@implements_rule("R-INDICATOR-11")
def is_low_dull(k: pd.Series, d: pd.Series, n: int = 3, threshold: float = 20.0) -> pd.Series:
    """低檔鈍化：K、D連續N天(預設3天)皆維持在20以下，與高檔鈍化完全對稱。"""
    oversold = (k <= threshold) & (d <= threshold)
    return oversold.rolling(n).apply(lambda window: bool(window.all()), raw=True).fillna(0).astype(bool)


@implements_rule("R-INDICATOR-12")
def kd_peak_divergence(heads: list[float], k_peaks: list[float], valid_low: float = 20, valid_high: float = 80) -> str | None:
    """KD峰背離(高檔背離)：股價頭頭高但K值峰值頭頭低。僅在兩峰值皆落在20~80非鈍化區間時訊號可信。"""
    if len(heads) < 2 or len(k_peaks) < 2:
        return None
    if not (heads[-1] > heads[-2] and k_peaks[-1] < k_peaks[-2]):
        return None
    if valid_low <= k_peaks[-1] <= valid_high and valid_low <= k_peaks[-2] <= valid_high:
        return "KD峰背離，趨勢反轉風險升高"
    return "KD雖呈背離型態，但落在鈍化區，訊號可信度低，需回歸股價與價量判斷"


@implements_rule("R-INDICATOR-12")
def kd_trough_divergence(bottoms: list[float], k_troughs: list[float], valid_low: float = 20, valid_high: float = 80) -> str | None:
    """KD底背離(低檔背離)：股價底底低但K值谷值底底高。僅在兩谷值皆落在20~80非鈍化區間時訊號可信。"""
    if len(bottoms) < 2 or len(k_troughs) < 2:
        return None
    if not (bottoms[-1] < bottoms[-2] and k_troughs[-1] > k_troughs[-2]):
        return None
    if valid_low <= k_troughs[-1] <= valid_high and valid_low <= k_troughs[-2] <= valid_high:
        return "KD底背離，股價隨時會反彈或落底"
    return "KD雖呈背離型態，但落在鈍化區，訊號可信度低，需回歸股價與價量判斷"
