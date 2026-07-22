"""技術指標分類：MACD計算公式與其3個判讀規則（R-INDICATOR-01/02/03/07）。

書中僅稱「12日均線」「26日均線」「9日平均值」，未明確標註是SMA或EMA；業界慣例（含
大多數看盤軟體）皆以指數移動平均（EMA）計算，這裡採用EMA以符合市場慣例顯示的數值，
此點屬工程慣例補充、非書中明文（見R-INDICATOR-01信心欄位說明）。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.crossovers import is_death_cross, is_golden_cross
from src.rule_registry import implements_rule


def ema(series: pd.Series, n: int) -> pd.Series:
    """N期指數移動平均（EMA）。"""
    return series.ewm(span=n, adjust=False).mean()


@implements_rule("R-INDICATOR-01")
def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """DIF＝快線(EMA12)減慢線(EMA26)；MACD＝DIF的9日EMA(訊號線)；OSC＝DIF減MACD(柱狀體)。"""
    dif = ema(close, fast) - ema(close, slow)
    macd_line = ema(dif, signal)
    osc = dif - macd_line
    return pd.DataFrame({"DIF": dif, "MACD": macd_line, "OSC": osc})


@implements_rule("R-INDICATOR-02")
def macd_zero_axis_bull_signal(dif: pd.Series, macd_line: pd.Series) -> pd.Series:
    """0軸上黃金交叉＝多方買進訊號；0軸上死亡交叉＝短線獲利了結（仍屬回檔，除非後續跌破0軸）。"""
    zero_bull = (dif > 0) & (macd_line > 0)
    golden = is_golden_cross(dif, macd_line)
    dead = is_death_cross(dif, macd_line)
    signal = pd.Series(pd.NA, index=dif.index, dtype="object")
    signal = signal.mask((zero_bull & golden).fillna(False), "多方買進訊號")
    signal = signal.mask((zero_bull & dead).fillna(False), "短線獲利了結訊號（回檔，多頭格局不變，除非後續跌破0軸）")
    return signal


@implements_rule("R-INDICATOR-03")
def macd_zero_axis_bear_signal(dif: pd.Series, macd_line: pd.Series) -> pd.Series:
    """0軸下死亡交叉＝空方賣出（做空）訊號；0軸下黃金交叉＝空單回補訊號（僅反彈，除非後續站上0軸）。"""
    zero_bear = (dif < 0) & (macd_line < 0)
    golden = is_golden_cross(dif, macd_line)
    dead = is_death_cross(dif, macd_line)
    signal = pd.Series(pd.NA, index=dif.index, dtype="object")
    signal = signal.mask((zero_bear & golden).fillna(False), "空單回補訊號（僅屬空頭反彈，除非後續站上0軸）")
    signal = signal.mask((zero_bear & dead).fillna(False), "空方賣出（做空）訊號")
    return signal


@implements_rule("R-INDICATOR-07")
def macd_trend_level_bullish_divergence(heads: list[float], osc_peaks: list[float]) -> bool:
    """趨勢級高檔背離：股價頭頭高，但對應波段的OSC紅柱峰值卻頭頭低 -> 提示多轉空。"""
    if len(heads) < 2 or len(osc_peaks) < 2:
        return False
    return heads[-1] > heads[-2] and osc_peaks[-1] < osc_peaks[-2]


@implements_rule("R-INDICATOR-07")
def macd_trend_level_bearish_divergence(bottoms: list[float], osc_troughs: list[float]) -> bool:
    """趨勢級低檔背離：股價底底低，但對應波段的OSC綠柱谷值(絕對值)卻底底高 -> 提示空轉多。"""
    if len(bottoms) < 2 or len(osc_troughs) < 2:
        return False
    return bottoms[-1] < bottoms[-2] and abs(osc_troughs[-1]) < abs(osc_troughs[-2])


@implements_rule("R-INDICATOR-04")
def bull_osc_shrinking_pullback(osc_t: float, osc_prev: float) -> bool:
    """①紅柱縮短 -> 多頭回檔訊號。"""
    return osc_t > 0 and osc_t < osc_prev


@implements_rule("R-INDICATOR-04")
def bull_osc_growing_continuation(osc_t: float, osc_prev: float) -> bool:
    """②紅柱漸長 -> 漲勢持續，續抱多單。"""
    return osc_t > 0 and osc_t > osc_prev


@implements_rule("R-INDICATOR-04")
def bull_osc_momentum_divergence(osc_t: float, osc_prev: float, close_t: float, recent_high: float) -> bool:
    """③紅柱漸長但股價未同步創新高 -> 動能與價格背離，多單準備賣出。"""
    return osc_t > 0 and osc_t > osc_prev and close_t <= recent_high


@implements_rule("R-INDICATOR-04")
def bull_osc_re_growth_buy_signal(was_shrinking_before: bool, osc_t: float, osc_prev: float) -> bool:
    """④紅柱由縮短再轉增長 -> 漲勢再起，可回檔後買上漲。"""
    return was_shrinking_before and osc_t > osc_prev and osc_t > 0


@implements_rule("R-INDICATOR-04")
def green_to_red_bullish_signal(osc_prev: float, green_was_shrinking: bool, osc_t: float) -> bool:
    """⑤綠柱縮短接近0軸、之後轉為紅柱 -> 偏多訊號，股價容易再漲。"""
    return osc_prev < 0 and green_was_shrinking and osc_t > 0


@implements_rule("R-INDICATOR-04")
def red_to_green_bearish_signal(osc_prev: float, red_was_shrinking: bool, osc_t: float) -> bool:
    """⑥紅柱縮短接近0軸、之後轉為綠柱 -> 偏空訊號，股價容易續回檔。"""
    return osc_prev > 0 and red_was_shrinking and osc_t < 0


@implements_rule("R-INDICATOR-04")
def bull_high_divergence(made_new_high_recently: bool, osc_peak_not_new_high: bool) -> bool:
    """⑦高檔背離：多頭高檔股價創新高(1~3次)但紅柱未同步創新高 -> 做頭機會大增。"""
    return made_new_high_recently and osc_peak_not_new_high


@implements_rule("R-INDICATOR-05")
def bear_osc_shrinking_rebound(osc_t: float, osc_prev: float) -> bool:
    """①綠柱縮短(絕對值變小) -> 空頭反彈訊號。"""
    return osc_t < 0 and abs(osc_t) < abs(osc_prev)


@implements_rule("R-INDICATOR-05")
def bear_osc_growing_continuation(osc_t: float, osc_prev: float) -> bool:
    """②綠柱漸長(絕對值變大) -> 跌勢持續，續抱空單。"""
    return osc_t < 0 and abs(osc_t) > abs(osc_prev)


@implements_rule("R-INDICATOR-05")
def bear_osc_momentum_divergence(osc_t: float, osc_prev: float, close_t: float, recent_low: float) -> bool:
    """③綠柱漸長但股價未同步創新低 -> 動能背離，空單準備回補。"""
    return osc_t < 0 and abs(osc_t) > abs(osc_prev) and close_t >= recent_low


@implements_rule("R-INDICATOR-05")
def bear_osc_re_growth_short_signal(was_shrinking_before: bool, osc_t: float, osc_prev: float) -> bool:
    """④綠柱由縮短再轉增長 -> 跌勢再起，反彈後可再放空。"""
    return was_shrinking_before and abs(osc_t) > abs(osc_prev) and osc_t < 0


@implements_rule("R-INDICATOR-05")
def red_to_green_bearish_signal_low(osc_prev: float, red_was_shrinking: bool, osc_t: float) -> bool:
    """⑤紅柱縮短接近0軸、之後轉為綠柱 -> 偏空訊號，股價容易再跌。"""
    return osc_prev > 0 and red_was_shrinking and osc_t < 0


@implements_rule("R-INDICATOR-05")
def green_to_red_bullish_signal_low(osc_prev: float, green_was_shrinking: bool, osc_t: float) -> bool:
    """⑥綠柱縮短接近0軸、之後轉為紅柱 -> 偏多訊號，股價容易續反彈。"""
    return osc_prev < 0 and green_was_shrinking and osc_t > 0


@implements_rule("R-INDICATOR-05")
def bear_low_divergence(made_new_low_recently: bool, osc_trough_not_new_low: bool) -> bool:
    """⑦低檔背離：空頭低檔股價創新低(1~3次)但綠柱未同步創新低 -> 打底機會大增。"""
    return made_new_low_recently and osc_trough_not_new_low


@implements_rule("R-INDICATOR-06")
def macd_swing_high_divergence(making_new_swing_high: bool, osc_t: float, osc_at_prior_swing_high: float) -> bool:
    """波段高檔背離(單一上漲波段內)：股價續創高但OSC紅柱高度未同步創高 -> 提示回檔。"""
    return making_new_swing_high and osc_t < osc_at_prior_swing_high


@implements_rule("R-INDICATOR-06")
def macd_swing_low_divergence(making_new_swing_low: bool, osc_t: float, osc_at_prior_swing_low: float) -> bool:
    """波段低檔背離(單一下跌波段內)：股價續創低但OSC綠柱高度(絕對值)未同步創低 -> 提示反彈。"""
    return making_new_swing_low and abs(osc_t) < abs(osc_at_prior_swing_low)
