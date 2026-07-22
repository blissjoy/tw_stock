"""K棒型態分類（Layer 0）：紅K線／黑K線分類（R-CANDLE-21/22）。

書中用「開盤到收盤的漲跌幅%」分類K線實體大小，**不是**實體占最高減最低全距的比例，
這是最容易被誤植成西方K線慣例的地方，實作時務必保持這個精確定義。

「位置判讀」（在打底/高檔/回檔等不同趨勢位置的意涵）屬於「可程式化：部分」——
需要外部的趨勢位置模組（尚未實作）才能完整判斷，這裡先把可客觀計算的部分
（顏色、實體大小分類）做成純函式，位置判讀則保留成純資料字典，等趨勢位置
模組就緒後由呼叫端自行查表，不在此處假裝自動完成。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.rule_registry import implements_rule

LONG_BODY_PCT = 0.065   # 長紅/長黑K門檻：漲跌幅 > 6.5%
MID_BODY_PCT = 0.035    # 中紅/中黑K門檻：漲跌幅介於 3.5%~6.5%


@implements_rule("R-CANDLE-21")
def red_candle_body_pct(open_: pd.Series, close: pd.Series) -> pd.Series:
    """紅K的實體漲幅%（(收盤-開盤)/開盤），黑K或平盤時為 NaN。"""
    is_red = close > open_
    pct = (close - open_) / open_
    return pct.where(is_red)


@implements_rule("R-CANDLE-21")
def classify_red_candle_size(open_: pd.Series, close: pd.Series) -> pd.Series:
    """紅K實體大小分類：長紅K(>6.5%)／中紅K(3.5%~6.5%)／小紅K(<3.5%)／(非紅K為 NaN)。"""
    body_pct = red_candle_body_pct(open_, close)
    size = pd.Series(np.where(body_pct > LONG_BODY_PCT, "長紅K",
                      np.where(body_pct >= MID_BODY_PCT, "中紅K", "小紅K")),
                      index=open_.index)
    return size.where(body_pct.notna())


@implements_rule("R-CANDLE-22")
def black_candle_body_pct(open_: pd.Series, close: pd.Series) -> pd.Series:
    """黑K的實體跌幅%（(開盤-收盤)/開盤），紅K或平盤時為 NaN。"""
    is_black = close < open_
    pct = (open_ - close) / open_
    return pct.where(is_black)


@implements_rule("R-CANDLE-22")
def classify_black_candle_size(open_: pd.Series, close: pd.Series) -> pd.Series:
    """黑K實體大小分類：長黑K(>6.5%)／中黑K(3.5%~6.5%)／小黑K(<3.5%)／(非黑K為 NaN)。"""
    body_pct = black_candle_body_pct(open_, close)
    size = pd.Series(np.where(body_pct > LONG_BODY_PCT, "長黑K",
                      np.where(body_pct >= MID_BODY_PCT, "中黑K", "小黑K")),
                      index=open_.index)
    return size.where(body_pct.notna())


@implements_rule("R-CANDLE-04")
def is_mid_long_red_candle(open_: pd.Series, close: pd.Series) -> pd.Series:
    """中長紅K：紅K且實體漲幅 >= 3.5%（供橫盤突破確認等規則引用的共用判斷）。"""
    body_pct = red_candle_body_pct(open_, close)
    return (body_pct >= MID_BODY_PCT).fillna(False)


@implements_rule("R-CANDLE-04")
def is_mid_long_black_candle(open_: pd.Series, close: pd.Series) -> pd.Series:
    """中長黑K：黑K且實體跌幅 >= 3.5%（供橫盤跌破確認等規則引用的共用判斷）。"""
    body_pct = black_candle_body_pct(open_, close)
    return (body_pct >= MID_BODY_PCT).fillna(False)


def is_red_candle(open_: pd.Series, close: pd.Series) -> pd.Series:
    """紅K：收盤>開盤（不分大小，供組合K線型態規則共用的最基本判斷）。"""
    return close > open_


def is_black_candle(open_: pd.Series, close: pd.Series) -> pd.Series:
    """黑K：收盤<開盤（不分大小）。"""
    return close < open_


def is_small_red_candle(open_: pd.Series, close: pd.Series) -> pd.Series:
    """小紅K：紅K且實體漲幅 < 3.5%（供上升三法等中繼組合型態的「中段小K」判斷共用）。"""
    body_pct = red_candle_body_pct(open_, close)
    return (body_pct < MID_BODY_PCT).fillna(False)


def is_small_black_candle(open_: pd.Series, close: pd.Series) -> pd.Series:
    """小黑K：黑K且實體跌幅 < 3.5%。"""
    body_pct = black_candle_body_pct(open_, close)
    return (body_pct < MID_BODY_PCT).fillna(False)


def candle_shadows(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """回傳 (上影線長度, 下影線長度)，供紡錘K線、槌子/倒槌等型態共用。"""
    body_top = pd.concat([open_, close], axis=1).max(axis=1)
    body_bottom = pd.concat([open_, close], axis=1).min(axis=1)
    upper_shadow = high - body_top
    lower_shadow = body_bottom - low
    return upper_shadow, lower_shadow


@implements_rule("R-CANDLE-01")
def prev_bar_support_resistance_signal(close: pd.Series, high: pd.Series, low: pd.Series, lookback: int = 1) -> pd.Series:
    """前一根K棒高低點支撐壓力：收盤突破前高＝買方轉強；收盤跌破前低＝賣方轉強；否則多空未表態。"""
    prev_high = high.shift(lookback)
    prev_low = low.shift(lookback)
    signal = pd.Series("多空未表態", index=close.index, dtype="object")
    signal = signal.mask(close > prev_high, "買方力量轉強")
    signal = signal.mask(close < prev_low, "賣方力量轉強")
    signal = signal.mask(prev_high.isna() | prev_low.isna(), pd.NA)
    return signal


@implements_rule("R-CANDLE-24")
def is_spindle_candle(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """紡錘K線：上下影線都很短，長度都不超過實體的二分之一（本書定義與西方Spinning Top方向相反）。"""
    body = (close - open_).abs()
    upper_shadow, lower_shadow = candle_shadows(open_, high, low, close)
    return (upper_shadow <= body / 2) & (lower_shadow <= body / 2)


@implements_rule("R-CANDLE-25")
def is_hammer_candle(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """槌子線：下影線長(>=實體2倍)、上影線短，顏色不拘（不分紅黑，意義取決於出現的價格位階）。"""
    body = (close - open_).abs()
    upper_shadow, lower_shadow = candle_shadows(open_, high, low, close)
    return (lower_shadow >= 2 * body) & (upper_shadow < lower_shadow)


@implements_rule("R-CANDLE-25")
def is_inverted_hammer_candle(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """倒槌K線：上影線長(>=實體2倍)、下影線短，顏色不拘。書中槌子/倒槌判斷邏輯完全相同、皆是「高檔易下跌、低檔易反彈」。"""
    body = (close - open_).abs()
    upper_shadow, lower_shadow = candle_shadows(open_, high, low, close)
    return (upper_shadow >= 2 * body) & (lower_shadow < upper_shadow)


DOJI_BODY_PCT = 0.005  # 十字線：實體漲跌幅門檻。查證外部資料(doji定義)僅有「開盤收盤價趨近於0」的定性描述，
# 無公認統一數字門檻，此為工程估計值，非外部佐證數字（見 ai/ebook-summary/P03-C2 補充備註）。
TW_DAILY_LIMIT_PCT = 0.10  # 台股現行漲跌幅限制：金管會自2015-06-01起由7%放寬為10%，此為法規事實非TA慣例。


@implements_rule("R-CANDLE-05")
def is_doji(open_: pd.Series, close: pd.Series, threshold: float = DOJI_BODY_PCT) -> pd.Series:
    """十字線：開盤收盤價趨近相同(實體漲跌幅<threshold)。"""
    return ((close - open_).abs() / open_ < threshold).fillna(False)


@implements_rule("R-CANDLE-05")
def is_gravestone_line(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, threshold: float = DOJI_BODY_PCT) -> pd.Series:
    """墓碑線：十字線變體，上影線長、下影線趨近於0(收盤/開盤貼近當日最低點)。"""
    upper_shadow, lower_shadow = candle_shadows(open_, high, low, close)
    body = (close - open_).abs()
    return (is_doji(open_, close, threshold) & (upper_shadow > lower_shadow) & (lower_shadow <= body)).fillna(False)


@implements_rule("R-CANDLE-05")
def is_long_t_line(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, threshold: float = DOJI_BODY_PCT) -> pd.Series:
    """長T線：十字線變體，下影線長、上影線趨近於0(收盤/開盤貼近當日最高點)，與墓碑線鏡射。"""
    upper_shadow, lower_shadow = candle_shadows(open_, high, low, close)
    body = (close - open_).abs()
    return (is_doji(open_, close, threshold) & (lower_shadow > upper_shadow) & (upper_shadow <= body)).fillna(False)


@implements_rule("R-CANDLE-05")
def is_limit_move(close: pd.Series, prev_close: pd.Series, direction: str, limit_pct: float = TW_DAILY_LIMIT_PCT) -> pd.Series:
    """漲跌停線：台股現行漲跌幅限制10%(2015-06-01起金管會由7%放寬至10%，法規事實)。"""
    change_pct = (close - prev_close) / prev_close
    tolerance = 0.001  # 容許極小數值誤差，避免浮點數比較剛好卡在10%邊界漏判
    if direction == "up":
        return (change_pct >= limit_pct - tolerance).fillna(False)
    if direction == "down":
        return (change_pct <= -(limit_pct - tolerance)).fillna(False)
    raise ValueError("direction 必須是 'up' 或 'down'")


@implements_rule("R-CANDLE-05")
def is_reversal_candle_at_high(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, prev_close: pd.Series,
) -> pd.Series:
    """高檔變盤線8種外觀之組合判斷：十字線/墓碑線/長T線/紡錘線/槌子/倒槌/跌停線/長黑線，任一成立即為止漲訊號候選。"""
    long_black = classify_black_candle_size(open_, close) == "長黑K"
    return (
        is_doji(open_, close)
        | is_gravestone_line(open_, high, low, close)
        | is_long_t_line(open_, high, low, close)
        | is_spindle_candle(open_, high, low, close)
        | is_hammer_candle(open_, high, low, close)
        | is_inverted_hammer_candle(open_, high, low, close)
        | is_limit_move(close, prev_close, "down")
        | long_black
    ).fillna(False)


@implements_rule("R-CANDLE-13")
def is_reversal_candle_at_low(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, prev_close: pd.Series,
) -> pd.Series:
    """低檔變盤線8種外觀組合(與高檔版鏡射)：十字線/墓碑線/長T線/紡錘線/槌子/倒槌/漲停線/長紅線。"""
    long_red = classify_red_candle_size(open_, close) == "長紅K"
    return (
        is_doji(open_, close)
        | is_gravestone_line(open_, high, low, close)
        | is_long_t_line(open_, high, low, close)
        | is_spindle_candle(open_, high, low, close)
        | is_hammer_candle(open_, high, low, close)
        | is_inverted_hammer_candle(open_, high, low, close)
        | is_limit_move(close, prev_close, "up")
        | long_red
    ).fillna(False)


NEAR_CLOSE_PCT = 0.01  # 「一日封口」收盤接近前日收盤的容許誤差。查證外部資料未找到此書中特定用語的公認數字門檻，工程估計值。


@implements_rule("R-CANDLE-07")
def high_black_meeting_pattern(
    open_1: float, close_1: float, open_2: float, close_2: float, near_pct: float = NEAR_CLOSE_PCT,
) -> bool:
    """高檔長黑遭遇(一日封口)：前一日中長紅，次日開高走黑，收盤封回接近前日收盤。6型態中最弱的止漲訊號。"""
    is_open_higher = open_2 > close_1
    is_black_day2 = close_2 < open_2
    is_close_near = abs(close_2 - close_1) <= near_pct * close_1
    return is_open_higher and is_black_day2 and is_close_near


@implements_rule("R-CANDLE-15")
def low_red_meeting_pattern(
    open_1: float, close_1: float, open_2: float, close_2: float, near_pct: float = NEAR_CLOSE_PCT,
) -> bool:
    """低檔長紅遭遇(一日封口)：前一日中長黑，次日開低走紅，收盤封回接近前日收盤，與高檔長黑遭遇鏡射。"""
    is_open_lower = open_2 < close_1
    is_red_day2 = close_2 > open_2
    is_close_near = abs(close_2 - close_1) <= near_pct * close_1
    return is_open_lower and is_red_day2 and is_close_near


@implements_rule("R-CANDLE-12")
def evening_star_pattern(is_mid_long_red_left: bool, all_middle_are_reversal_candles: bool, is_mid_long_black_right: bool, is_at_high: bool) -> bool:
    """高檔夜星：左中長紅+中段1根以上變盤線+右中長黑，型態成立觸發點=右邊黑K收盤。"""
    return is_mid_long_red_left and all_middle_are_reversal_candles and is_mid_long_black_right and is_at_high


@implements_rule("R-CANDLE-12")
def evening_star_invalidated(right_black_high: float, subsequent_highs: list[float]) -> bool:
    """夜星型態失效：黑K高點被後續紅K突破，結構轉為多方主控(常見於多頭回檔中的夜星)。"""
    return any(h > right_black_high for h in subsequent_highs)


@implements_rule("R-CANDLE-20")
def morning_star_pattern(is_mid_long_black_left: bool, all_middle_are_reversal_candles: bool, is_mid_long_red_right: bool, is_at_low: bool) -> bool:
    """低檔晨星：左中長黑+中段1根以上變盤線+右中長紅，與高檔夜星鏡射。"""
    return is_mid_long_black_left and all_middle_are_reversal_candles and is_mid_long_red_right and is_at_low


@implements_rule("R-CANDLE-20")
def morning_star_invalidated(right_red_low: float, subsequent_lows: list[float]) -> bool:
    """晨星型態失效：紅K低點被後續黑K跌破，結構轉為空方主控(常見於空頭反彈中的晨星)，書中2個範例明確佐證此失效條件。"""
    return any(low_ < right_red_low for low_ in subsequent_lows)


@implements_rule("R-CANDLE-34")
def channel_breakout_strength_score(gap_up_open: bool, is_big_volume: bool, is_long_red: bool) -> int:
    """突破上升軌道線壓力的長紅K力道評分：跳空上漲、大量、長紅實體各+1分，分數越高力道越強。"""
    return int(gap_up_open) + int(is_big_volume) + int(is_long_red)


@implements_rule("R-CANDLE-34")
def channel_pre_breakout_short_signal(near_channel_no_breakout: bool, then_pulls_back: bool) -> bool:
    """軌道線未突破前的逆勢做空訊號：股價接近上升軌道線但未突破就回落，是書中少數明確允許的逆勢交易情境。"""
    return near_channel_no_breakout and then_pulls_back


@implements_rule("R-CANDLE-35")
def channel_breakdown_strength_score(gap_down_open: bool, is_big_volume: bool, is_long_black: bool) -> int:
    """跌破下降軌道線支撐的長黑K力道評分，與突破上升軌道線版鏡射。"""
    return int(gap_down_open) + int(is_big_volume) + int(is_long_black)


@implements_rule("R-CANDLE-35")
def channel_pre_breakdown_long_signal(near_channel_no_breakdown: bool, then_rebounds: bool) -> bool:
    """軌道線未跌破前的逆勢做多訊號：股價接近下降軌道線但未跌破就反彈。"""
    return near_channel_no_breakdown and then_rebounds


@implements_rule("R-CANDLE-36")
def long_upper_shadow_at_high(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """高檔長上影線：實體小、上影線長(>=實體2倍，比照R-CANDLE-25槌子/倒槌的倍數定義)，顏色不拘。"""
    body = (close - open_).abs()
    upper_shadow, lower_shadow = candle_shadows(open_, high, low, close)
    return ((upper_shadow >= 2 * body) & (upper_shadow > lower_shadow)).fillna(False)


@implements_rule("R-CANDLE-36")
def consecutive_3day_upper_shadow_at_resistance(upper_shadow_ge_2x_body: list[bool], near_resistance: list[bool]) -> bool:
    """連3天上漲長上影線大敵當前：同一壓力區連續3天都出現長上影線(每天上攻都失敗)，主力連續調節出貨警訊。"""
    return len(upper_shadow_ge_2x_body) == 3 and all(upper_shadow_ge_2x_body) and all(near_resistance)


@implements_rule("R-CANDLE-37", "R-CLASSIC-23")
def red_black_red_continuation(is_red_1: bool, is_black_2: bool, is_red_3: bool, black_holds_above_red1_open: bool, ma_bullish_aligned: bool) -> bool:
    """紅黑紅續勢(多方夾擊)：中間黑K未跌破第1根紅K開盤價，且處於均線多頭排列格局，才視為續漲而非反轉。"""
    return is_red_1 and is_black_2 and is_red_3 and black_holds_above_red1_open and ma_bullish_aligned


@implements_rule("R-CANDLE-37")
def black_red_black_continuation(is_black_1: bool, is_red_2: bool, is_black_3: bool, red_fails_below_black1_open: bool, is_bear_trend: bool) -> bool:
    """黑紅黑續勢(空方夾擊)：中間紅K未收復第1根黑K開盤價，且處於空頭趨勢格局，與紅黑紅鏡射對稱。"""
    return is_black_1 and is_red_2 and is_black_3 and red_fails_below_black1_open and is_bear_trend


@implements_rule("R-CANDLE-02")
def big_red_candle_half_price_tiers(high: float, low: float) -> dict:
    """大量長紅K的3層支撐(由強至弱)：最高價 > 二分之一價(=當日多空平均成本) > 最低價。"""
    half_price = (high + low) / 2
    return {"最強支撐": high, "平均成本支撐": half_price, "最弱支撐": low}


@implements_rule("R-CANDLE-02")
def classify_big_red_candle_support_test(tiers: dict, close_test: float, next_close: float | None = None, is_at_high: bool = False) -> str:
    """後續K棒測試這3層支撐的反應：跌破最高價力道減弱、跌破二分之一價轉弱、跌破最低價多空易位(除非次日假跌破)。"""
    tier1, tier2, tier3 = tiers["最強支撐"], tiers["平均成本支撐"], tiers["最弱支撐"]
    if tier2 <= close_test < tier1:
        return "攻擊力道減弱，須3~5個交易日內站回最高點之上，否則注意轉折向下"
    if tier3 <= close_test < tier2:
        status = "跌破平均成本，容易產生大量賣壓，多方氣勢轉弱"
        if is_at_high:
            status += "；高檔做頭機率大增，須小心反轉"
        return status
    if close_test < tier3:
        if next_close is not None and next_close > tier3:
            return "假跌破，不算真正轉弱（次日已收復）"
        return "跌破最低點，多空易位，該長紅K轉為日後壓力"
    return "支撐未破，多方氣勢維持"


@implements_rule("R-CANDLE-03")
def big_black_candle_half_price_tiers(high: float, low: float) -> dict:
    """大量長黑K的3層壓力(由強至弱)：最低價 > 二分之一價(=當日多空平均成本) > 最高價，與長紅K版鏡射。"""
    half_price = (high + low) / 2
    return {"最強壓力": low, "平均成本壓力": half_price, "最弱壓力": high}


@implements_rule("R-CANDLE-03")
def classify_big_black_candle_resistance_test(tiers: dict, close_test: float) -> str:
    """後續K棒測試這3層壓力的反應：突破最低價力道減弱、突破二分之一價轉弱、突破最高價多空易位。"""
    tier1, tier2, tier3 = tiers["最強壓力"], tiers["平均成本壓力"], tiers["最弱壓力"]
    if tier1 < close_test <= tier2:
        return "向下力道減弱，注意是否轉折向上反彈"
    if tier2 < close_test <= tier3:
        return "突破放空平均成本，容易產生大量回補買單，空方氣勢轉弱"
    if close_test > tier3:
        return "突破最高點，多空易位，該長黑K轉為日後重要支撐"
    return "壓力未破，空方氣勢維持"


@implements_rule("R-CANDLE-03")
def is_short_rebound_signal(is_big_volume_or_suffocation: bool, today_close: float, prev_high: float) -> bool:
    """連續長黑K後若爆量或窒息量，且今日收盤站過前一日高點，可短線搶反彈。"""
    return is_big_volume_or_suffocation and today_close > prev_high


@implements_rule("R-CANDLE-23")
def big_red_candle_entry_filter(
    is_big_red: bool,
    bear_to_bull_first_break_prior_high: bool = False,
    ma_triple_bullish: bool = False,
    bull_pullback_reversal: bool = False,
    consolidation_breakout: bool = False,
    pattern_confirmed_breakout: bool = False,
    consecutive_up_days_ge_3_at_high: bool = False,
    near_resistance_before_rise: bool = False,
    is_bear_rebound: bool = False,
    below_ma20: bool = False,
    broke_prior_low_in_pullback: bool = False,
) -> str:
    """大量長紅K進場位置篩選：書中明列4種可買清單、5種不可買清單，避免清單同時命中時，不可買清單優先。"""
    if not is_big_red:
        return "非大量長紅K，不適用本規則"

    avoid_buy = (
        consecutive_up_days_ge_3_at_high
        or near_resistance_before_rise
        or is_bear_rebound
        or below_ma20
        or broke_prior_low_in_pullback
    )
    if avoid_buy:
        return "不建議進場的大量長紅K位置"

    buy_ok = (
        (bear_to_bull_first_break_prior_high and ma_triple_bullish)
        or bull_pullback_reversal
        or consolidation_breakout
        or pattern_confirmed_breakout
    )
    if buy_ok:
        return "符合進場條件的大量長紅K"
    return "不在明列的可買清單內，保守觀望"


# 「位置判讀」對照表：純資料，不是自動判斷。呼叫端需先透過趨勢位置模組
# （尚未實作，屬於 Layer 2/3 的範疇）判定出目前所在位置字串，再查這個表取得
# 書中對應的解讀文字。刻意不做成函式，避免誤導成「已完整程式化」。
RED_CANDLE_POSITION_NOTES = {
    "底部打底期間": "主力接籌碼，量增但尚不會立刻續攻",
    "底部突破盤整": "配合攻擊量，多頭確認訊號",
    "多頭上漲行進中": "多方氣勢不墜，常見惜售量縮",
    "回檔修正完成(未破前低)": "修正結束，把握買進機會",
    "多頭盤整末端大量突破": "攻擊訊號，把握買進",
    "多頭高檔或連續急漲": "注意價量背離，警惕主力誘多出貨",
    "多頭高檔盤頭區間大量": "主力分批出貨訊號",
    "空頭下跌反彈": "主力誘多，不可搶進；若無量更不易反彈",
    "空頭低檔或急跌後止跌": "反彈信號",
}

BLACK_CANDLE_POSITION_NOTES = {
    "空頭低檔打底盤整": "底部尚未確認，可能主力再洗盤，不可視為止跌做多",
    "多頭上漲行進中": "漲多賣壓，趨勢未變則屬正常回檔",
    "多頭高點": "止漲訊號，若大量/爆天量須警惕主力出貨，密切注意次日",
    "高檔頭頭低第2個頭長黑": "容易轉成空頭，有量或無量皆可能續跌",
    "空頭初跌段": "通常出量，行情繼續下跌",
    "空頭下跌行進中無止跌訊號": "不論是否有量，都不能低接",
    "連續長黑後爆量或窒息量+隔日紅K收盤過前高": "短線搶反彈訊號",
    "空頭下跌盤整跌破": "做空機會",
}
