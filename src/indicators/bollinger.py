"""技術指標分類：布林通道（R-INDICATOR-20/21/22/23）。

「布林通道的8個使用原則」書中共8條（4個買進訊號＋4個做空訊號），全部完整轉譯，一條不省略。
第④條「三軌走平」的斜率容許誤差、「盤整區」邊界，書中未量化，斜率門檻屬工程補充；盤整區
邊界則直接重用 `src.indicators.consolidation` 已算出的箱型上下緣（由呼叫端傳入），不在此
重新定義一套盤整判斷邏輯。
"""

from __future__ import annotations

import pandas as pd

from src.rule_registry import implements_rule


@implements_rule("R-INDICATOR-20")
def bollinger_bands(close: pd.Series, n: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """中軌＝MA20；上軌＝中軌+2倍20日標準差；下軌＝中軌-2倍20日標準差。"""
    mid = close.rolling(window=n, min_periods=n).mean()
    std = close.rolling(window=n, min_periods=n).std()
    return pd.DataFrame({"mid": mid, "upper": mid + num_std * std, "lower": mid - num_std * std})


@implements_rule("R-INDICATOR-21")
def bollinger_touch_exit_signal(close: pd.Series, upper: pd.Series, lower: pd.Series, holding: pd.Series) -> pd.Series:
    """持倉了結：做多碰上軌即出場；做空碰下軌即回補。holding為每日持倉方向("多"/"空"/其他)。"""
    signal = pd.Series(pd.NA, index=close.index, dtype="object")
    signal = signal.mask((holding == "多") & (close >= upper), "短線出場訊號（做多出場）")
    signal = signal.mask((holding == "空") & (close <= lower), "短線回補訊號（做空回補）")
    return signal


def bollinger_bands_flat(upper: pd.Series, mid: pd.Series, lower: pd.Series, slope_threshold: float = 0.01) -> pd.Series:
    """三軌走平（盤整）：三條軌道線的單日變動幅度百分比皆小於門檻。書中未量化門檻，屬工程補充。"""
    flat_upper = upper.pct_change().abs() < slope_threshold
    flat_mid = mid.pct_change().abs() < slope_threshold
    flat_lower = lower.pct_change().abs() < slope_threshold
    return (flat_upper & flat_mid & flat_lower).fillna(False)


@implements_rule("R-INDICATOR-22")
def bollinger_buy_signal_1(close: pd.Series, lower: pd.Series, trend: pd.Series) -> pd.Series:
    """買訊①：空頭下跌至低檔，價格由下往上穿越下軌，搶空頭反彈買進。"""
    cross_up_lower = (close.shift(1) <= lower.shift(1)) & (close > lower)
    return ((trend == "空頭") & cross_up_lower).fillna(False)


@implements_rule("R-INDICATOR-22")
def bollinger_buy_signal_2(close: pd.Series, mid: pd.Series, trend: pd.Series, lookback: int = 5) -> pd.Series:
    """買訊②：多頭回檔跌破中軌後，近期(lookback天內)再站上中軌，買進。"""
    was_below_mid = pd.Series(False, index=close.index)
    for d in range(1, lookback + 1):
        was_below_mid |= close.shift(d) < mid.shift(d)
    cross_up_mid = (close.shift(1) <= mid.shift(1)) & (close > mid)
    return ((trend == "多頭") & was_below_mid & cross_up_mid).fillna(False)


@implements_rule("R-INDICATOR-22")
def bollinger_buy_signal_3(close: pd.Series, mid: pd.Series, upper: pd.Series) -> pd.Series:
    """買訊③：價格在中軌與上軌之間向上運行，多頭市場，持續做多/長線抱股。"""
    in_range = (close >= mid) & (close <= upper)
    rising = close > close.shift(1)
    return (in_range & rising).fillna(False)


@implements_rule("R-INDICATOR-22")
def bollinger_buy_signal_4(close: pd.Series, is_bands_flat: pd.Series, consolidation_upper: pd.Series) -> pd.Series:
    """買訊④：三軌走平(盤整)時，股價突破盤整區上緣，做多買進。盤整區上緣沿用 consolidation.py 的箱型輸出。"""
    breakout = close > consolidation_upper
    return (is_bands_flat.astype(bool) & breakout).fillna(False)


@implements_rule("R-INDICATOR-23")
def bollinger_sell_signal_1(close: pd.Series, upper: pd.Series, trend: pd.Series) -> pd.Series:
    """做空訊①：多頭高檔，價格由上往下穿越上軌，搶多頭回檔賣出/放空。"""
    cross_down_upper = (close.shift(1) >= upper.shift(1)) & (close < upper)
    return ((trend == "多頭") & cross_down_upper).fillna(False)


@implements_rule("R-INDICATOR-23")
def bollinger_sell_signal_2(close: pd.Series, mid: pd.Series, trend: pd.Series, lookback: int = 5) -> pd.Series:
    """做空訊②：空頭反彈突破中軌後，近期(lookback天內)再跌回中軌下方，賣出/放空。"""
    was_above_mid = pd.Series(False, index=close.index)
    for d in range(1, lookback + 1):
        was_above_mid |= close.shift(d) > mid.shift(d)
    cross_down_mid = (close.shift(1) >= mid.shift(1)) & (close < mid)
    return ((trend == "空頭") & was_above_mid & cross_down_mid).fillna(False)


@implements_rule("R-INDICATOR-23")
def bollinger_sell_signal_3(close: pd.Series, mid: pd.Series, lower: pd.Series) -> pd.Series:
    """做空訊③：價格在中軌與下軌之間向下運行，空頭市場，持續做空。"""
    in_range = (close >= lower) & (close <= mid)
    falling = close < close.shift(1)
    return (in_range & falling).fillna(False)


@implements_rule("R-INDICATOR-23")
def bollinger_sell_signal_4(close: pd.Series, is_bands_flat: pd.Series, consolidation_lower: pd.Series) -> pd.Series:
    """做空訊④：三軌走平(盤整)時，股價跌破盤整區下緣，放空賣出。"""
    breakdown = close < consolidation_lower
    return (is_bands_flat.astype(bool) & breakdown).fillna(False)


@implements_rule("R-INDICATOR-24")
def band_slope_direction(band: pd.Series, threshold: float = 0.01) -> pd.Series:
    """軌道線逐日方向：單日變動百分比超過門檻(工程補充，同R-INDICATOR-21三軌走平門檻)判為上揚/下彎，否則走平。"""
    change = band.pct_change()
    direction = pd.Series("走平", index=band.index, dtype="object")
    direction = direction.mask(change > threshold, "上揚")
    direction = direction.mask(change < -threshold, "下彎")
    return direction


@implements_rule("R-INDICATOR-24")
def classify_three_band_shape(upper_dir: pd.Series, mid_dir: pd.Series, lower_dir: pd.Series) -> pd.Series:
    """三軌方向組合判讀（要點⑥⑦⑧⑨⑩）：同向延續強弱勢、罕見組合、通道縮小整理、走平觀望。"""
    state = pd.Series("方向不一致，個別判讀", index=upper_dir.index, dtype="object")
    all_up = (upper_dir == "上揚") & (mid_dir == "上揚") & (lower_dir == "上揚")
    all_down = (upper_dir == "下彎") & (mid_dir == "下彎") & (lower_dir == "下彎")
    all_flat = (upper_dir == "走平") & (mid_dir == "走平") & (lower_dir == "走平")
    rare_combo = (upper_dir == "上揚") & (mid_dir == "下彎") & (lower_dir == "下彎")
    squeeze = (upper_dir == "下彎") & (mid_dir == "上揚") & (lower_dir == "上揚")
    state = state.mask(all_up, "三軌同時向上，強勢多頭延續")
    state = state.mask(all_down, "三軌同時向下，強勢空頭延續")
    state = state.mask(all_flat, "三軌走平，方向不明，建議觀望等待通道開口突破")
    state = state.mask(rare_combo, "罕見組合（上軌揚升、中下軌下彎）")
    state = state.mask(squeeze, "上軌轉下、中下軌仍向上，通道縮小，回檔或漸趨整理")
    return state


@implements_rule("R-INDICATOR-24")
def channel_squeeze_guidance(three_band_shape: str, long_term_trend: str) -> str:
    """要點⑨：通道縮小整理需依長期趨勢分別判讀，多頭屬強勢整理、空頭屬弱勢整理；其餘型態原樣傳回。"""
    if three_band_shape != "上軌轉下、中下軌仍向上，通道縮小，回檔或漸趨整理":
        return three_band_shape
    if long_term_trend == "多頭":
        return "強勢整理，可持股觀望或逢回短多"
    if long_term_trend == "空頭":
        return "弱勢整理，宜持空單續抱或反彈續空"
    return three_band_shape


@implements_rule("R-INDICATOR-24")
def channel_pullback_warning(upper_dir: str, close_below_mid: bool) -> str | None:
    """要點③：上軌走平或下彎，代表多頭進入盤整或急速回檔；若再跌破中軌，容易進一步觸及下軌。"""
    if upper_dir == "上揚":
        return None
    warning = "多頭進入盤整或急速回檔階段"
    if close_below_mid:
        warning += "；跌破中軌，容易進一步觸及下軌"
    return warning


@implements_rule("R-INDICATOR-24")
def profit_take_exit_below_ma5_black_candle(close: pd.Series, ma5: pd.Series, is_black_candle: pd.Series) -> pd.Series:
    """要點①：多頭沿上軌上漲時持股續抱，直到出現一根跌破MA5的黑K才停利出場。"""
    return ((close < ma5) & is_black_candle.astype(bool)).fillna(False)
