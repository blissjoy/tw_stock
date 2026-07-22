"""均線分類（Layer 3）：9種均線戰法中「信心=高、可程式化=是」的7種（單一均線短/中線做多做空、
2條均線長線做多做空、3條均線綜合戰法）。

這7個戰法共用同一套骨架：前提濾網(主趨勢+站上/站下MA20) → 進場訊號 → 持股/出場依據某條均線
→ 停損(R-MA-21統一邏輯，見 src.indicators.moving_average) → 停利(波段獲利門檻+跌破/突破持股均線)。
書中進場訊號有部分子條件（多頭盤整突破確認、底部型態確認、急跌反彈確認、頭部型態確認）依賴尚未
實作的K棒型態/經典型態總覽分類規則，這裡用 extra_entry_confirmed 參數開放外部注入，待那些規則
完成後再接上，不在此處重新實作型態辨識邏輯。同理，急漲末端爆量長黑/長上影線/吞噬等出場加速訊號
用 exhaustion_signal 參數注入，本模組只實作「訊號成立後賣半/隔日再跌賣清」這段有明確規則的部分。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.candles import MID_BODY_PCT
from src.indicators.crossovers import is_death_cross, is_golden_cross
from src.rule_registry import implements_rule


def _long_prerequisite(close: pd.Series, ma20: pd.Series, is_bull_trend: pd.Series) -> pd.Series:
    return (is_bull_trend.astype(bool) & (close > ma20)).fillna(False)


def _short_prerequisite(close: pd.Series, ma20: pd.Series, is_bear_trend: pd.Series) -> pd.Series:
    return (is_bear_trend.astype(bool) & (close < ma20)).fillna(False)


def single_ma_long_entry_signal(
    close: pd.Series,
    high: pd.Series,
    ma5: pd.Series,
    ma20: pd.Series,
    is_bull_trend: pd.Series,
    extra_entry_confirmed: pd.Series | None = None,
) -> pd.Series:
    """單一均線做多共用進場邏輯：多頭回後買上漲確認點（收盤突破MA5且突破前一日高點），
    OR 由外部注入的其他確認點（盤整突破/底部型態），前提是主趨勢多頭且站上MA20。"""
    prerequisite = _long_prerequisite(close, ma20, is_bull_trend)
    pullback_confirmed = (close > ma5) & (close > high.shift(1))
    entry_confirmed = pullback_confirmed if extra_entry_confirmed is None else (pullback_confirmed | extra_entry_confirmed.astype(bool))
    return (prerequisite & entry_confirmed).fillna(False)


def single_ma_short_entry_signal(
    close: pd.Series,
    low: pd.Series,
    ma5: pd.Series,
    ma20: pd.Series,
    is_bear_trend: pd.Series,
    extra_entry_confirmed: pd.Series | None = None,
) -> pd.Series:
    """單一均線做空共用進場邏輯：空頭彈後空下跌確認點（收盤跌破MA5且跌破前一日低點）。"""
    prerequisite = _short_prerequisite(close, ma20, is_bear_trend)
    pullback_confirmed = (close < ma5) & (close < low.shift(1))
    entry_confirmed = pullback_confirmed if extra_entry_confirmed is None else (pullback_confirmed | extra_entry_confirmed.astype(bool))
    return (prerequisite & entry_confirmed).fillna(False)


def ma_hold_exit_signal(close: pd.Series, ma_hold: pd.Series) -> pd.Series:
    """收盤跌破持股依據均線＝轉折下跌確認，出場（做多方向）。"""
    return (close < ma_hold).fillna(False)


def ma_cover_exit_signal(close: pd.Series, ma_hold: pd.Series) -> pd.Series:
    """收盤突破持股依據均線＝轉折上漲確認，回補（做空方向）。"""
    return (close > ma_hold).fillna(False)


def profit_take_exit_long(close: pd.Series, ma_hold: pd.Series, entry_price: float, target_pct: float = 10.0) -> pd.Series:
    """波段獲利達門檻後，才會因跌破持股均線而停利；未達門檻則續抱不因跌破而出場。"""
    reached_target = close.cummax() >= entry_price * (1 + target_pct / 100)
    return (reached_target & (close < ma_hold)).fillna(False)


def profit_take_exit_short(close: pd.Series, ma_hold: pd.Series, entry_price: float, target_pct: float = 10.0) -> pd.Series:
    """做空鏡射版：波段獲利（下跌）達門檻後，才因突破持股均線而回補停利。"""
    reached_target = close.cummin() <= entry_price * (1 - target_pct / 100)
    return (reached_target & (close > ma_hold)).fillna(False)


def swing_profit_guard_long(close: pd.Series, ma5: pd.Series, entry_price: float, guard_pct: float = 20.0) -> pd.Series:
    """波段獲利超過門檻(預設20%)時，不再分批/不看持股均線，收盤跌破MA5即直接全部停利。"""
    reached_guard = close.cummax() >= entry_price * (1 + guard_pct / 100)
    return (reached_guard & (close < ma5)).fillna(False)


def swing_profit_guard_short(close: pd.Series, ma5: pd.Series, entry_price: float, guard_pct: float = 20.0) -> pd.Series:
    reached_guard = close.cummin() <= entry_price * (1 - guard_pct / 100)
    return (reached_guard & (close > ma5)).fillna(False)


def exhaustion_scaled_exit(exhaustion_signal: pd.Series) -> pd.Series:
    """急漲/急跌末端爆量或吞噬型態出現當天賣一半，若隔日續朝不利方向即賣出剩餘全部。

    exhaustion_signal 由外部（K棒型態規則）判斷後注入；回傳兩個欄位：today_fraction（今天要
    出清的比例，0.5）、tomorrow_full_if_confirmed（隔日若確認訊號持續則出清剩餘部位的比例，0.5）。
    """
    today_half = exhaustion_signal.fillna(False)
    tomorrow_half = exhaustion_signal.shift(1).fillna(False) & exhaustion_signal.fillna(False)
    return pd.DataFrame({"today_fraction": today_half.map({True: 0.5, False: 0.0}),
                          "tomorrow_fraction": tomorrow_half.map({True: 0.5, False: 0.0})})


@implements_rule("R-MA-22")
def single_ma_short_term_long_strategy(
    close: pd.Series, high: pd.Series, ma5: pd.Series, ma20: pd.Series,
    is_bull_trend: pd.Series, entry_price: float | None = None,
    extra_entry_confirmed: pd.Series | None = None,
) -> pd.DataFrame:
    """戰法1：單一均線短線做多。進出場皆守MA5，MA20為大方向濾網。"""
    entry = single_ma_long_entry_signal(close, high, ma5, ma20, is_bull_trend, extra_entry_confirmed)
    exit_ = ma_hold_exit_signal(close, ma5)
    result = pd.DataFrame({"entry_signal": entry, "exit_signal": exit_})
    if entry_price is not None:
        result["profit_take_exit"] = profit_take_exit_long(close, ma5, entry_price)
        result["swing_profit_guard"] = swing_profit_guard_long(close, ma5, entry_price)
    return result


@implements_rule("R-MA-23")
def single_ma_short_term_short_strategy(
    close: pd.Series, low: pd.Series, ma5: pd.Series, ma20: pd.Series,
    is_bear_trend: pd.Series, entry_price: float | None = None,
    extra_entry_confirmed: pd.Series | None = None,
) -> pd.DataFrame:
    """戰法2：單一均線短線做空，與戰法1鏡射對稱。"""
    entry = single_ma_short_entry_signal(close, low, ma5, ma20, is_bear_trend, extra_entry_confirmed)
    exit_ = ma_cover_exit_signal(close, ma5)
    result = pd.DataFrame({"entry_signal": entry, "exit_signal": exit_})
    if entry_price is not None:
        result["profit_take_exit"] = profit_take_exit_short(close, ma5, entry_price)
        result["swing_profit_guard"] = swing_profit_guard_short(close, ma5, entry_price)
    return result


@implements_rule("R-MA-24")
def single_ma_mid_term_long_strategy(
    close: pd.Series, high: pd.Series, ma5: pd.Series, ma10: pd.Series, ma20: pd.Series,
    is_bull_trend: pd.Series, entry_price: float | None = None,
    extra_entry_confirmed: pd.Series | None = None,
) -> pd.DataFrame:
    """戰法3：單一均線中線做多。進場訊號仍用MA5判斷回檔結束，但持股/停利改守MA10。"""
    entry = single_ma_long_entry_signal(close, high, ma5, ma20, is_bull_trend, extra_entry_confirmed)
    exit_ = ma_hold_exit_signal(close, ma10)
    result = pd.DataFrame({"entry_signal": entry, "exit_signal": exit_})
    if entry_price is not None:
        result["profit_take_exit"] = profit_take_exit_long(close, ma10, entry_price)
        result["swing_profit_guard"] = swing_profit_guard_long(close, ma5, entry_price)
    return result


@implements_rule("R-MA-25")
def single_ma_mid_term_short_strategy(
    close: pd.Series, low: pd.Series, ma5: pd.Series, ma10: pd.Series, ma20: pd.Series,
    is_bear_trend: pd.Series, entry_price: float | None = None,
    extra_entry_confirmed: pd.Series | None = None,
) -> pd.DataFrame:
    """戰法4：單一均線中線做空，與戰法3鏡射對稱。"""
    entry = single_ma_short_entry_signal(close, low, ma5, ma20, is_bear_trend, extra_entry_confirmed)
    exit_ = ma_cover_exit_signal(close, ma10)
    result = pd.DataFrame({"entry_signal": entry, "exit_signal": exit_})
    if entry_price is not None:
        result["profit_take_exit"] = profit_take_exit_short(close, ma10, entry_price)
        result["swing_profit_guard"] = swing_profit_guard_short(close, ma5, entry_price)
    return result


@implements_rule("R-MA-28")
def dual_ma_long_term_long_strategy(
    close: pd.Series, ma5: pd.Series, ma10: pd.Series, ma20: pd.Series, entry_price: float | None = None,
) -> pd.DataFrame:
    """戰法7：2條均線長線做多。MA10/MA20黃金交叉且多排向上進場；死亡交叉且空排向下出場（主要訊號）。"""
    entry = (is_golden_cross(ma10, ma20) & (ma10 > ma20)).fillna(False)
    exit_ = (is_death_cross(ma10, ma20) & (ma10 < ma20)).fillna(False)
    result = pd.DataFrame({"entry_signal": entry, "exit_signal": exit_})
    if entry_price is not None:
        result["swing_profit_guard"] = swing_profit_guard_long(close, ma5, entry_price)
    return result


@implements_rule("R-MA-29")
def dual_ma_long_term_short_strategy(
    close: pd.Series, ma5: pd.Series, ma10: pd.Series, ma20: pd.Series, entry_price: float | None = None,
) -> pd.DataFrame:
    """戰法8：2條均線長線做空，與戰法7鏡射對稱。"""
    entry = (is_death_cross(ma10, ma20) & (ma10 < ma20)).fillna(False)
    exit_ = (is_golden_cross(ma10, ma20) & (ma10 > ma20)).fillna(False)
    result = pd.DataFrame({"entry_signal": entry, "exit_signal": exit_})
    if entry_price is not None:
        result["swing_profit_guard"] = swing_profit_guard_short(close, ma5, entry_price)
    return result


@implements_rule("R-MA-30")
def triple_ma_batch_exit_thirds_long(close: pd.Series, ma5: pd.Series, ma10: pd.Series, ma20: pd.Series) -> pd.DataFrame:
    """戰法9a多頭：3線分批停利。跌破MA5賣1/3，續跌破MA10再賣1/3，續跌破MA20賣最後1/3出清。

    回傳每日「本應已出清的部位比例」(0~1)，由呼叫端對照自己的實際已出清比例決定今天還要再賣多少。
    """
    fraction_sold = pd.Series(0.0, index=close.index)
    fraction_sold = fraction_sold.mask(close < ma5, fraction_sold + 1 / 3)
    fraction_sold = fraction_sold.mask(close < ma10, fraction_sold + 1 / 3)
    fraction_sold = fraction_sold.mask(close < ma20, fraction_sold + 1 / 3)
    return fraction_sold.clip(upper=1.0)


@implements_rule("R-MA-30")
def triple_ma_batch_reentry_thirds_long(close: pd.Series, ma5: pd.Series, ma10: pd.Series, ma20: pd.Series, is_bull_trend: pd.Series) -> pd.Series:
    """戰法9a多頭：多頭趨勢不變時，逐一突破MA5→MA10→MA20分批買回，恢復原部位比例(0~1)。"""
    fraction_back = pd.Series(0.0, index=close.index)
    trend_intact = is_bull_trend.astype(bool)
    fraction_back = fraction_back.mask(trend_intact & (close > ma5), fraction_back + 1 / 3)
    fraction_back = fraction_back.mask(trend_intact & (close > ma10), fraction_back + 1 / 3)
    fraction_back = fraction_back.mask(trend_intact & (close > ma20), fraction_back + 1 / 3)
    return fraction_back.clip(upper=1.0)


@implements_rule("R-MA-30")
def triple_ma_batch_exit_thirds_short(close: pd.Series, ma5: pd.Series, ma10: pd.Series, ma20: pd.Series) -> pd.Series:
    """戰法9b空頭：3線分批回補，與9a多頭鏡射對稱。"""
    fraction_covered = pd.Series(0.0, index=close.index)
    fraction_covered = fraction_covered.mask(close > ma5, fraction_covered + 1 / 3)
    fraction_covered = fraction_covered.mask(close > ma10, fraction_covered + 1 / 3)
    fraction_covered = fraction_covered.mask(close > ma20, fraction_covered + 1 / 3)
    return fraction_covered.clip(upper=1.0)


@implements_rule("R-MA-17")
def ma_tangle_breakout_long_entry(close: pd.Series, volume: pd.Series, was_converged_yesterday: pd.Series, convergence_high: pd.Series, is_long_red: pd.Series, volume_multiplier: float = 1.5) -> pd.Series:
    """均線糾結向上突破做多SOP進場：前一日仍糾結+收盤突破糾結區間高點+放量(相對前一日)+中長紅K。"""
    breakout = close > convergence_high
    big_volume = volume > volume.shift(1) * volume_multiplier
    return (was_converged_yesterday.astype(bool) & breakout & big_volume & is_long_red.astype(bool)).fillna(False)


@implements_rule("R-MA-17")
def ma_tangle_breakout_stop_loss_long(entry_open: float, entry_close: float, entry_low: float) -> float:
    """停損：進場K線最低點；若進場為小紅K(漲幅<3.5%，沿用R-CANDLE-21門檻)，改用收盤下跌5%為停損。"""
    gain_pct = (entry_close - entry_open) / entry_open
    if gain_pct < MID_BODY_PCT:
        return entry_close * 0.95
    return entry_low


@implements_rule("R-MA-17")
def ma_tangle_breakout_hold_exit_signal(close: pd.Series, low: pd.Series) -> pd.Series:
    """智慧K線交易法(多頭)：每日收盤未跌破前一日K棒最低點就續抱，跌破才出場(對應每日13:20檢視時點)。"""
    return (close < low.shift(1)).fillna(False)


@implements_rule("R-MA-17")
def ma_tangle_breakout_reentry_signal(close: pd.Series, high: pd.Series) -> pd.Series:
    """出場後若仍維持多頭趨勢，再次收盤突破前一天K線最高點的上漲紅K，可重新進場。"""
    return (close > high.shift(1)).fillna(False)


@implements_rule("R-MA-18")
def ma_tangle_breakdown_short_entry(close: pd.Series, volume: pd.Series, was_converged_yesterday: pd.Series, convergence_low: pd.Series, is_long_black: pd.Series, volume_multiplier: float = 1.5) -> pd.Series:
    """均線糾結向下跌破做空SOP進場：與向上突破做多SOP完全鏡射。"""
    breakdown = close < convergence_low
    big_volume = volume > volume.shift(1) * volume_multiplier
    return (was_converged_yesterday.astype(bool) & breakdown & big_volume & is_long_black.astype(bool)).fillna(False)


@implements_rule("R-MA-18")
def ma_tangle_breakdown_stop_loss_short(entry_open: float, entry_close: float, entry_high: float) -> float:
    """停損：進場K線最高點；若進場為小黑K(跌幅<3.5%)，改用收盤反彈5%為停損。"""
    loss_pct = (entry_open - entry_close) / entry_open
    if loss_pct < MID_BODY_PCT:
        return entry_close * 1.05
    return entry_high


@implements_rule("R-MA-18")
def ma_tangle_breakdown_hold_exit_signal(close: pd.Series, high: pd.Series) -> pd.Series:
    """智慧K線交易法(空頭)：每日收盤未突破前一日K棒最高點就續抱空單，突破才回補。"""
    return (close > high.shift(1)).fillna(False)


@implements_rule("R-MA-18")
def ma_tangle_breakdown_reentry_signal(close: pd.Series, low: pd.Series) -> pd.Series:
    """回補後若仍維持空頭趨勢，再次收盤跌破前一天K線最低點的下跌黑K，可重新進場做空。"""
    return (close < low.shift(1)).fillna(False)


@implements_rule("R-MA-30")
def triple_ma_batch_reentry_thirds_short(close: pd.Series, ma5: pd.Series, ma10: pd.Series, ma20: pd.Series, is_bear_trend: pd.Series) -> pd.Series:
    """戰法9b空頭：空頭趨勢不變時，逐一跌破MA5→MA10→MA20分批加空，恢復原空單部位比例(0~1)。"""
    fraction_back = pd.Series(0.0, index=close.index)
    trend_intact = is_bear_trend.astype(bool)
    fraction_back = fraction_back.mask(trend_intact & (close < ma5), fraction_back + 1 / 3)
    fraction_back = fraction_back.mask(trend_intact & (close < ma10), fraction_back + 1 / 3)
    fraction_back = fraction_back.mask(trend_intact & (close < ma20), fraction_back + 1 / 3)
    return fraction_back.clip(upper=1.0)


@implements_rule("R-MA-26")
def long_term_long_entry_signal(close: pd.Series, ma20: pd.Series, ma60: pd.Series, golden_cross_10_20_bullish: pd.Series) -> pd.Series:
    """戰法5：股價站上MA20且在MA60之上，且MA10/MA20已黃金交叉多排上揚，才買進多單。"""
    return (golden_cross_10_20_bullish.astype(bool) & (close > ma20) & (close > ma60)).fillna(False)


@implements_rule("R-MA-26")
def long_term_long_watch_exit_signal(close: pd.Series, ma20: pd.Series, ma60: pd.Series) -> pd.Series:
    """跌破MA20但仍在MA60之上：賣出多單、空手觀望（非停損，是趨勢轉弱的中性出場）。"""
    return ((close < ma20) & (close > ma60)).fillna(False)


@implements_rule("R-MA-26")
def should_upgrade_to_long_term_stage(
    four_line_bullish_aligned: bool, has_unrealized_profit: bool, weekly_bull_trend: bool, weekly_ma_3line_bullish: bool
) -> bool:
    """短線轉長線：均線4線多排+已獲利+週線多頭確認+週均線3線多排同時成立，才改守MA20操作。"""
    return bool(four_line_bullish_aligned and has_unrealized_profit and weekly_bull_trend and weekly_ma_3line_bullish)


@implements_rule("R-MA-26")
def swing_profit_take_exit_via_ma10_long(
    close: pd.Series, ma10: pd.Series, entry_price: float, bias_over_threshold: pd.Series,
    profit_pct_threshold: float = 20.0,
) -> pd.Series:
    """波段獲利>20%且股價與MA20乖離>20%(由呼叫端用R-INDICATOR-17的bias_ratio算出注入)時，改守跌破MA10停利。"""
    reached_profit = close.cummax() >= entry_price * (1 + profit_pct_threshold / 100)
    return (reached_profit & bias_over_threshold.astype(bool) & (close < ma10)).fillna(False)


@implements_rule("R-MA-26")
def should_downgrade_stop_basis_to_ma5(price_doubled_from_base: bool, in_late_stage_rally: bool) -> bool:
    """股價已上漲達底部起漲幅度1倍，或進入末升段高檔：改守MA5，只做短線。"""
    return bool(price_doubled_from_base or in_late_stage_rally)


@implements_rule("R-MA-27")
def long_term_short_entry_signal(close: pd.Series, ma20: pd.Series, ma60: pd.Series, death_cross_10_20_bearish: pd.Series) -> pd.Series:
    """戰法6：股價跌破MA20且在MA60之下，且MA10/MA20已死亡交叉空排下彎，才做空空單。與R-MA-26鏡射對稱。"""
    return (death_cross_10_20_bearish.astype(bool) & (close < ma20) & (close < ma60)).fillna(False)


@implements_rule("R-MA-27")
def long_term_short_watch_exit_signal(close: pd.Series, ma20: pd.Series, ma60: pd.Series) -> pd.Series:
    """站上MA20但仍在MA60之下：回補空單、空手觀望。"""
    return ((close > ma20) & (close < ma60)).fillna(False)


@implements_rule("R-MA-27")
def should_upgrade_to_long_term_stage_short(
    four_line_bearish_aligned: bool, has_unrealized_profit: bool, weekly_bear_trend: bool, weekly_ma_3line_bearish: bool
) -> bool:
    """短線轉長線（做空版）：均線4線空排+已獲利+週線空頭確認+週均線3線空排同時成立，才改守MA20操作。"""
    return bool(four_line_bearish_aligned and has_unrealized_profit and weekly_bear_trend and weekly_ma_3line_bearish)


@implements_rule("R-MA-27")
def swing_profit_take_exit_via_ma5_short(
    close: pd.Series, ma5: pd.Series, entry_price: float, profit_pct_threshold: float = 20.0,
) -> pd.Series:
    """波段獲利(下跌)超過20%，收盤突破MA5即回補停利（做空版無乖離條件，書中僅載明獲利門檻）。"""
    reached_profit = close.cummin() <= entry_price * (1 - profit_pct_threshold / 100)
    return (reached_profit & (close > ma5)).fillna(False)


@implements_rule("R-MA-27")
def should_downgrade_stop_basis_to_short_term(price_halved_from_top: bool, in_late_stage_decline: bool) -> bool:
    """股價已下跌達頭部最高點的二分之一價，或進入末跌段低檔：此位置只做短空，須提高警覺爆量容易落底。"""
    return bool(price_halved_from_top or in_late_stage_decline)
