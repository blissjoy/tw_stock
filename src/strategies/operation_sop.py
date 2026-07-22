"""綜合操作SOP分類（Layer 4：整合層）：短線/長線波段守則、飆股操作守則、12個操作口訣(R-STRATEGY-01~09)。

本模組把書中「操作口訣」與「進出場SOP」轉譯為可執行邏輯，多數口訣本身依賴其他分類
(趨勢判定/K棒型態/支撐壓力/成交量)已完成的規則作為輸入，此處只做SOP層級的串接，不
重新定義底層判斷邏輯。書中未給精確數字的口訣觸發門檻(如「不漲/不跌」「久盤」天數、
「該回/該彈」容忍期)一律開放為可調參數，預設值為工程估計值，不假裝有精確書中數字。
"""

from __future__ import annotations

from src.rule_registry import implements_rule


@implements_rule("R-STRATEGY-01")
def short_swing_entry_ready(
    is_bull_trend: bool, close_t: float, open_t: float, ma5_t: float, high_prev: float,
    ma20_slope: float, kd_k_slope: float, volume_t: float, volume_avg: float, volume_prev: float,
    body_gain_threshold: float = 0.02,
) -> bool:
    """短線波段20條守則第1條：多頭架構+突破MA5與前高+紅K實體漲幅≥2%+MA20/KD_K向上+量增或量平。"""
    body_gain_pct = (close_t - open_t) / open_t
    volume_ok = volume_t >= volume_avg or volume_t > volume_prev
    return (
        is_bull_trend
        and close_t > ma5_t and close_t > high_prev
        and body_gain_pct >= body_gain_threshold
        and ma20_slope > 0 and kd_k_slope > 0
        and volume_ok
    )


@implements_rule("R-STRATEGY-01")
def short_swing_exit_action(
    close_t: float, stop_loss: float, has_lower_high: bool, profit_pct: float, ma5_t: float,
    big_black_signal: bool = False, close_below_prior_low: bool = False, next_day_gap_down_continue: bool = False,
) -> str:
    """短線波段20條守則第2-8條：停損/頭頭低優先出場；獲利≥20%大量長黑分批出清；獲利≥10%跌破MA5停利；否則續抱。"""
    if close_t < stop_loss:
        return "跌破停損，出場"
    if has_lower_high:
        return "頭頭低，趨勢改變出場"
    if profit_pct >= 0.20 and big_black_signal:
        if close_below_prior_low:
            return "跌破前一日低點，全部出場"
        return "出清剩餘部位" if next_day_gap_down_continue else "減碼二分之一"
    if profit_pct >= 0.10 and close_t < ma5_t:
        return "獲利≥10%跌破MA5，停利出場"
    return "續抱"


@implements_rule("R-STRATEGY-01")
def short_swing_small_pullback_tolerance(close_t: float, ma5_t: float, is_volume_shrinking: bool, ma20_slope: float) -> str | None:
    """短線波段20條守則第15條：跌破MA5但跌幅<1%、量縮、MA20仍向上時當日續抱觀察次日。"""
    pullback_pct = (ma5_t - close_t) / ma5_t
    if pullback_pct < 0.01 and is_volume_shrinking and ma20_slope > 0:
        return "跌幅<1%且量縮，續抱觀察次日"
    return None


@implements_rule("R-STRATEGY-01")
def can_add_position(profit_pct: float, is_range_bound_or_small_dip: bool, is_volume_up: bool, is_red_k: bool, add_position_cutoff: float = 0.10) -> bool:
    """短線波段20條守則第17-19條：波段漲幅未達10%、處於橫盤或小跌、量增紅K，才可加碼。"""
    return profit_pct < add_position_cutoff and is_range_bound_or_small_dip and is_volume_up and is_red_k


@implements_rule("R-STRATEGY-02")
def long_swing_exit_action(
    close_t: float, stop_loss: float, has_lower_high: bool, profit_pct: float, ma20_t: float,
    big_black_signal: bool = False, close_below_prior_low: bool = False,
) -> str:
    """長線波段操作SOP：與短線波段(R-STRATEGY-01)結構相同，差異在停利改守MA20而非MA5。"""
    if close_t < stop_loss:
        return "跌破停損，出場"
    if has_lower_high:
        return "頭頭低，出場"
    if profit_pct >= 0.20 and big_black_signal and close_below_prior_low:
        return "全部出場"
    if profit_pct >= 0.10 and close_t < ma20_t:
        return "獲利≥10%跌破MA20，停利出場"
    return "續抱"


@implements_rule("R-STRATEGY-02")
def long_swing_second_wave_check(cumulative_gain_from_launch_pct: float, entry_ready: bool) -> str | None:
    """長線波段第2波與操作上限：出場後反彈超過50%且再符合進場條件可做第2波；累計漲幅達1倍後不再新進場。"""
    if cumulative_gain_from_launch_pct >= 1.0:
        return "累計漲幅達1倍，不再進行長線新進場/加碼"
    if cumulative_gain_from_launch_pct > 0.50 and entry_ready:
        return "重新進場做長線第2波，重複第2～7條邏輯"
    return None


@implements_rule("R-STRATEGY-03")
def surge_stock_hold_conditions(not_broke_uptrend_line: bool, close_t: float, low_prev: float, low_prev2: float, ma3_t: float, is_red_or_flat: bool) -> bool:
    """飆股續抱四要件：未跌破上升趨勢線、未跌破前2日低點、未跌破3日均線、未出現黑K，四者須同時成立。"""
    cond2 = close_t >= min(low_prev, low_prev2)
    cond3 = close_t >= ma3_t
    return not_broke_uptrend_line and cond2 and cond3 and is_red_or_flat


SURGE_VOLUME_TIER_ACTIONS = {
    "無量飆漲": "續抱",
    "量稍放大股價仍穩": "續抱",
    "量放大價格大幅震盪": "出脫二分之一",
    "量大增仍收紅": "保守者先出場，或觀察次日",
    "量大增開高走低收黑K": "迅速賣出",
}


@implements_rule("R-STRATEGY-03")
def surge_stock_volume_tier_action(volume_tier: str) -> str:
    """飆股量能5級距：依當日量能型態決定續抱/減碼/出清，五級距為書中明確分類。"""
    if volume_tier not in SURGE_VOLUME_TIER_ACTIONS:
        raise ValueError(f"volume_tier 必須是 {list(SURGE_VOLUME_TIER_ACTIONS)} 之一，收到：{volume_tier!r}")
    return SURGE_VOLUME_TIER_ACTIONS[volume_tier]


@implements_rule("R-STRATEGY-04")
def bull_high_volume_no_rise(is_bull_trend: bool, is_big_volume: bool, price_change_pct: float, is_black_candle: bool, no_rise_threshold: float = 0.0) -> str | None:
    """口訣1：多頭大量不漲，股價要回檔（當日或後數日）。「不漲」門檻書中未給精確數字，預設漲幅<0視為不漲。"""
    if not is_bull_trend:
        return None
    is_no_rise = price_change_pct < no_rise_threshold or is_black_candle
    if is_big_volume and is_no_rise:
        return "大量不漲，預期當日或後數日回檔"
    return None


@implements_rule("R-STRATEGY-04")
def bear_high_volume_no_fall(is_bear_trend: bool, is_big_volume: bool, price_change_pct: float, is_red_candle: bool, no_fall_threshold: float = 0.0) -> str | None:
    """口訣2：空頭大量不跌，股價要反彈，與口訣1鏡射對稱。"""
    if not is_bear_trend:
        return None
    is_no_fall = price_change_pct > -no_fall_threshold or is_red_candle
    if is_big_volume and is_no_fall:
        return "大量不跌，預期當日或後數日反彈"
    return None


@implements_rule("R-STRATEGY-05")
def good_news_no_rise_distribution(is_at_bull_high: bool, news_is_good: bool, close_t: float, close_prev: float) -> str | None:
    """口訣3：多頭高檔利多不漲，疑似主力出貨做頭。須外部接入消息面資料判定news_is_good。"""
    if not (is_at_bull_high and news_is_good):
        return None
    if close_t <= close_prev:
        return "利多不漲，疑似主力出貨"
    return None


@implements_rule("R-STRATEGY-05")
def bad_news_no_fall_accumulation(is_at_bear_low: bool, news_is_bad: bool, close_t: float, close_prev: float) -> str | None:
    """口訣4：空頭低檔利空不跌，疑似主力進場築底（書中無獨立圖例，為口訣3的鏡射推論）。"""
    if not (is_at_bear_low and news_is_bad):
        return None
    if close_t >= close_prev:
        return "利空不跌，疑似主力進場築底（鏡射推論，書中無獨立圖例）"
    return None


@implements_rule("R-STRATEGY-06")
def bull_no_pullback_strength(is_bull_trend: bool, should_pullback: bool, no_pullback_observed: bool, close_after_window: float, prior_high: float) -> str | None:
    """口訣5：多頭該回不回，過高要大漲，可續抱或加碼（非停利訊號）。"""
    if not is_bull_trend or not should_pullback:
        return None
    if no_pullback_observed and close_after_window > prior_high:
        return "該回不回，過高要大漲，可續抱或加碼（非停利訊號）"
    return None


@implements_rule("R-STRATEGY-06")
def bear_no_rebound_strength(is_bear_trend: bool, should_rebound: bool, no_rebound_observed: bool, close_after_window: float, prior_low: float) -> str | None:
    """口訣6：空頭該彈不彈，破低要大跌，與口訣5鏡射對稱。"""
    if not is_bear_trend or not should_rebound:
        return None
    if no_rebound_observed and close_after_window < prior_low:
        return "該彈不彈，破低要大跌"
    return None


@implements_rule("R-STRATEGY-07")
def bull_to_bear_reversal_signal(prev_trend_state: str, curr_trend_state: str) -> str | None:
    """口訣7：多頭完成反轉，要大跌。直接複用趨勢判定的狀態切換事件(多頭確認→空頭確認)。"""
    if prev_trend_state == "多頭確認" and curr_trend_state == "空頭確認":
        return "多頭完成反轉，預期初期出現大跌"
    return None


@implements_rule("R-STRATEGY-07")
def bear_to_bull_reversal_signal(prev_trend_state: str, curr_trend_state: str) -> str | None:
    """口訣8：空頭完成反轉，會大漲，與口訣7鏡射對稱。"""
    if prev_trend_state == "空頭確認" and curr_trend_state == "多頭確認":
        return "空頭完成反轉，預期初期出現大漲"
    return None


@implements_rule("R-STRATEGY-08")
def star_dominance_signal(is_morning_star: bool, is_evening_star: bool) -> str | None:
    """口訣9：晨星多方主控，夜星空方主控。直接複用R-CANDLE-20/12的既有型態辨識結果。"""
    if is_morning_star:
        return "晨星反彈，多方主控"
    if is_evening_star:
        return "夜星下跌，空方主控"
    return None


@implements_rule("R-STRATEGY-08")
def one_star_two_bull_fakeout(is_one_star_two_bull_pattern: bool, third_candle_is_red: bool, third_candle_close: float, prior_support: float) -> str | None:
    """口訣10上半：一星二陽後長紅表面收紅卻實際跌破支撐＝騙線，近日易大跌。"""
    if not is_one_star_two_bull_pattern:
        return None
    if third_candle_is_red and third_candle_close < prior_support:
        return "一星二陽後跌破長紅，空頭確認，近日易大跌（騙線）"
    return None


@implements_rule("R-STRATEGY-08")
def one_star_two_bear_fakeout(is_one_star_two_bear_pattern: bool, third_candle_is_black: bool, third_candle_close: float, prior_resistance: float) -> str | None:
    """口訣10下半：一星二陰後長黑表面收黑卻實際突破壓力＝騙線，近日易大漲。"""
    if not is_one_star_two_bear_pattern:
        return None
    if third_candle_is_black and third_candle_close > prior_resistance:
        return "一星二陰後長黑突破壓力，近日易大漲（騙線）"
    return None


@implements_rule("R-STRATEGY-09")
def resistance_high_volume_no_rise(close_t: float, prior_high: float, is_big_volume: bool, near_resistance_pct_threshold: float = 0.02) -> str | None:
    """口訣11：關前放大量，股價不漲要回檔。「關前」距離門檻書中未給，沿用R-SR-13已查證的2%心理關卡容許誤差為工程估計值。"""
    near_prior_high = abs(close_t - prior_high) / prior_high <= near_resistance_pct_threshold
    is_no_rise = close_t < prior_high
    if near_prior_high and is_big_volume and is_no_rise:
        return "關前爆量不漲，先回檔"
    return None


@implements_rule("R-STRATEGY-09")
def long_consolidation_breakout_signal(consolidation_days: int, position: str, close_t: float, zone_upper: float, zone_lower: float, min_days: int = 40) -> str | None:
    """口訣12：高檔久盤必跌，低檔久盤必漲。「久盤」天數書中未給，沿用R-SCREEN-09已查證的2個月(約40交易日)均線糾結門檻為工程估計值。"""
    if consolidation_days < min_days:
        return None
    if position == "高檔" and close_t < zone_lower:
        return "跌破久盤，預期大跌"
    if position == "低檔" and close_t > zone_upper:
        return "突破久盤，預期大漲"
    return None
