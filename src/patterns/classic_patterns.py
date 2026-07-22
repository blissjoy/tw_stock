"""經典型態總覽分類（Layer 4：整合層）：全書33張精華圖中「信心=高＋可程式化=是」的23張。

每一張圖本質是把已經實作過的切線／缺口／K棒／量價／支撐壓力／趨勢判定等規則，依書中
描述的固定順序「串」成一個複合訊號，本模組不重新實作任何底層判斷邏輯，只做串接——
每個函式的參數就是各底層規則算出的布林值/數值，呼叫端負責用 src.indicators.* 與
src.strategies.* 裡已經做好的函式算出這些值再傳進來。少數幾張圖（R-CLASSIC-28/30/32）
書中原文本身就是「直接沿用某條既有規則」，未強行拆解成新邏輯，這裡同樣只做直通。
"""

from __future__ import annotations

from src.rule_registry import implements_rule


@implements_rule("R-CLASSIC-01")
def one_day_reversal_at_high(is_top_zone: bool, is_engulfing_signal: bool, is_large_volume: bool, next_close: float | None = None, bar_low: float | None = None) -> str | None:
    """高檔大量長黑一日反轉圖：多頭高檔+爆量+高檔長黑吞噬，先賣一半；隔日跌破黑K低點才全部出清。"""
    if not (is_top_zone and is_engulfing_signal and is_large_volume):
        return None
    if next_close is not None and bar_low is not None and next_close < bar_low:
        return "剩餘部位全數賣出，確認一日反轉"
    return "先賣出持股二分之一"


@implements_rule("R-CLASSIC-02")
def big_black_breaks_uptrend_line(has_lower_high: bool, is_big_black: bool, close_below_line: bool) -> str | None:
    """大量長黑破切反轉圖：頭頭低+大量長黑收盤跌破上升切線，切線角色由支撐轉壓力，空頭確認。"""
    if has_lower_high and is_big_black and close_below_line:
        return "空頭確認：上升切線轉壓力"
    return None


@implements_rule("R-CLASSIC-03")
def double_top_neckline_break(is_lower_high: bool, close_below_neckline: bool, is_large_volume: bool) -> str | None:
    """大量雙頭反轉圖(M頭)：第2頭頭頭低+帶大量跌破兩頭之間的頸線，空頭確認。"""
    if is_lower_high and close_below_neckline and is_large_volume:
        return "M頭頸線跌破，空頭確認"
    return None


@implements_rule("R-CLASSIC-04")
def resistance_big_black_immediate_exit(at_resistance: bool, is_big_black: bool, is_lower_high_confirmed: bool = False) -> str | None:
    """遇壓爆量黑K快跑圖：見壓力位置+爆量長黑即應立即停利，是多頭高檔爆量停利訊號中反應最快的版本。"""
    if not (at_resistance and is_big_black):
        return None
    signal = "遇壓爆量長黑，立即停利，不必等待更多確認"
    if is_lower_high_confirmed:
        signal += "，空頭確認強化"
    return signal


@implements_rule("R-CLASSIC-29")
def double_arc_bottom_breakout(close_above_resistance: bool, is_large_volume: bool) -> str | None:
    """雙弧底大量紅K突破圖：與雙盤底同屬打底家族，底部形狀為圓弧(U型)而非平台，核心突破邏輯相同。"""
    if close_above_resistance and is_large_volume:
        return "雙弧底突破，套用雙盤底大量紅K突破進場核心邏輯（底部形狀參數＝圓弧）"
    return None


@implements_rule("R-CLASSIC-05")
def break_below_two_day_high_volume_low(day1_volume_big: bool, day2_volume_big: bool, day1_low: float, day2_low: float, later_close: float, later_is_black: bool) -> str | None:
    """高檔連2日大量被黑K跌破圖：高檔連2日爆量後，黑K跌破這2日低點，一日反轉停利。"""
    if not (day1_volume_big and day2_volume_big):
        return None
    low_zone = min(day1_low, day2_low)
    if later_close < low_zone and later_is_black:
        return "跌破高檔連2日大量低點，一日反轉停利"
    return None


@implements_rule("R-CLASSIC-07")
def gap_down_black_reversal_at_high(is_gap_down_big_volume: bool, is_break_ma20: bool, is_lower_high: bool, is_lower_low: bool, kd_bearish_cross: bool = False) -> str | None:
    """高檔跳空黑K回檔反轉圖：跳空黑K+跌破MA20+頭頭低+底底低同時成立，空頭確認；KD同步走弱可加強。"""
    if not (is_gap_down_big_volume and is_break_ma20 and is_lower_high and is_lower_low):
        return None
    signal = "高檔跳空黑K回檔反轉，空頭確認"
    if kd_bearish_cross:
        signal += "，KD同步走弱，確認強化"
    return signal


@implements_rule("R-CLASSIC-09")
def chase_short_on_bounce_break(is_downtrend: bool, is_bounce_red_with_volume: bool, bounce_low: float | None = None, next_close: float | None = None) -> str | None:
    """反彈大量紅K跌破追空圖：空頭中帶量反彈紅K，隨後跌破該紅K低點，確認空方力道未變，可加碼追空。"""
    if not (is_downtrend and is_bounce_red_with_volume):
        return None
    if bounce_low is not None and next_close is not None and next_close < bounce_low:
        return "跌破反彈紅K低點，追空點確認"
    return None


@implements_rule("R-CLASSIC-12")
def gap_down_continuation(has_gap: bool, gap_not_covered: bool, close_below_consolidation_low: bool, ma20_broken_before_gap: bool = False, island_reversal_detected: bool = False) -> str | None:
    """缺口之下續空圖：向下缺口未回補+跌破缺口下方盤整區低點，續空/加碼放空點。"""
    if not (has_gap and gap_not_covered and close_below_consolidation_low):
        return None
    signal = "缺口下再破底，續空/加碼放空點"
    if ma20_broken_before_gap:
        signal += "（月線先跌破，加速確認）"
    if island_reversal_detected:
        signal += "，注意反轉循環再次啟動"
    return signal


@implements_rule("R-CLASSIC-13")
def bull_to_bear_break_last_low(close: float, last_bull_low: float, is_large_volume: bool) -> str | None:
    """多轉空破多底大跌圖：跌破多頭最後一個確認低點且帶大量，多頭趨勢終結，快速下跌警訊。"""
    if close < last_bull_low and is_large_volume:
        return "跌破多頭最後低點，多頭趨勢終結，快速下跌警訊"
    return None


@implements_rule("R-CLASSIC-15")
def break_below_down_channel(close: float, channel_value: float, is_big_black: bool) -> str | None:
    """跌破下降軌道線大跌圖：大量長黑跌破下降軌道線(下緣支撐)，支撐轉壓力，跌勢轉急跌。"""
    if close < channel_value and is_big_black:
        return "支撐轉壓力，跌勢由緩降轉為急跌"
    return None


@implements_rule("R-CLASSIC-16")
def low_zone_big_red_confirmation(big_red_count: int, min_count: int = 2) -> str | None:
    """低檔大量長紅K圖：低檔短時間內反覆出現(>=2次)帶爆大量的長紅K，確認打底。"""
    if big_red_count >= min_count:
        return "低檔大量長紅確認打底"
    return None


@implements_rule("R-CLASSIC-17")
def break_downtrend_line_then_new_high(is_break_line: bool, retest_support_ok: bool, close_above_prior_bounce_high: bool, is_large_volume: bool) -> str | None:
    """破切反彈過高大漲圖：突破下降切線(轉支撐)+回測有效+突破前波反彈高點+爆量，多頭確認。"""
    if is_break_line and retest_support_ok and close_above_prior_bounce_high and is_large_volume:
        return "破切＋過高＋爆量，多頭確認"
    return None


@implements_rule("R-CLASSIC-18")
def double_leg_bottom_breakout(leg1_big_volume: bool, leg2_big_volume: bool, close_above_neckline: bool, leg2_low: float | None = None, neckline: float | None = None) -> dict | None:
    """大量雙腳反轉圖(雙腳打底)：兩支腳低點都須大量，第2支腳完成後突破頸線確認買點，D值可等距量測目標價。"""
    if not (leg1_big_volume and leg2_big_volume and close_above_neckline):
        return None
    result = {"signal": "雙腳打底突破頸線，多頭確認"}
    if leg2_low is not None and neckline is not None:
        result["D"] = abs(leg2_low - neckline)
    return result


@implements_rule("R-CLASSIC-19")
def gap_up_continuation(has_gap: bool, gap_not_broken: bool, strong_consolidation_signal: bool, is_large_volume: bool) -> str | None:
    """缺口之上續漲圖：向上缺口不破(缺口為支撐)+缺口之上出現強勢整理訊號+爆量，續漲確認。"""
    if has_gap and gap_not_broken and strong_consolidation_signal and is_large_volume:
        return "缺口之上出現大量買點，續漲確認"
    return None


@implements_rule("R-CLASSIC-20")
def gradual_rally_breakout(is_left_low_right_high: bool, is_above_rising_ma20: bool, is_red_with_volume: bool, close_above_range_high: bool) -> str | None:
    """碎步上漲攻擊圖：左低右高小紅K沿上揚MA20墊高，某日大量長紅突破整理區，攻擊買進點。"""
    if is_left_low_right_high and is_above_rising_ma20 and is_red_with_volume and close_above_range_high:
        return "碎步緩漲後大量長紅突破，攻擊買進點"
    return None


@implements_rule("R-CLASSIC-22")
def bear_to_bull_break_rebound_high(is_bear_reversal_signal: bool, is_bull_confirm: bool, close: float, bear_rebound_high: float, is_large_volume: bool) -> str | None:
    """空轉多過空高大漲圖：空頭止跌反轉+多頭確認(higher low)+突破空頭反彈高點+爆量，趨勢空轉多確認。"""
    if is_bear_reversal_signal and is_bull_confirm and close > bear_rebound_high and is_large_volume:
        return "突破空頭反彈高點，趨勢空轉多確認"
    return None


@implements_rule("R-CLASSIC-24")
def breakout_above_big_black_candle(is_big_black_in_uptrend: bool, close_above_black_high: bool, breakout_volume_big: bool) -> str | None:
    """突破大量黑K買進：多頭中出現大量黑K，但後續帶量突破該黑K高點，非轉空、為續漲買進訊號。"""
    if is_big_black_in_uptrend and close_above_black_high and breakout_volume_big:
        return "突破大量黑K高點，非轉空、為續漲買進訊號"
    return None


@implements_rule("R-CLASSIC-25")
def break_above_two_day_low_volume_high(day1_volume_big: bool, day2_volume_big: bool, day1_high: float, day2_high: float, later_close: float, breakout_volume_big: bool) -> str | None:
    """低檔連2日大量被突破圖：低檔連2日爆量後，帶量突破這2日高點，一日反轉轉強(鏡射R-CLASSIC-05)。"""
    if not (day1_volume_big and day2_volume_big):
        return None
    high_zone = max(day1_high, day2_high)
    if later_close > high_zone and breakout_volume_big:
        return "突破低檔連2日大量高點，一日反轉轉強"
    return None


@implements_rule("R-CLASSIC-26")
def low_zone_big_lower_shadow_reversal(is_hammer_big_volume: bool, is_at_bottom: bool, above_ma20_or_morning_star: bool = False) -> str | None:
    """低檔大量長下影線圖：低檔爆量長下影線(槌子線)，一日反轉買進候選；配合站上MA20或低檔晨星可加強確認。"""
    if not (is_hammer_big_volume and is_at_bottom):
        return None
    signal = "低檔大量長下影線，一日反轉買進候選"
    if above_ma20_or_morning_star:
        signal += "，多重確認強化"
    return signal


@implements_rule("R-CLASSIC-27")
def bear_rebound_consolidate_above_ma20_breakout(stayed_above_ma20: bool, is_breakout: bool) -> str | None:
    """空頭反彈在月線上盤整圖：反彈後站穩月線橫向盤整(月線壓力轉支撐)，帶量突破盤整區即轉強買點。"""
    if stayed_above_ma20 and is_breakout:
        return "反彈站穩月線盤整後突破買點"
    return None


@implements_rule("R-CLASSIC-28")
def double_bottom_platform_breakout(double_bottom_breakout_signal: bool) -> str | None:
    """雙盤底大量紅K突破圖：書中原文直接沿用選股策略分類的「雙盤底大量紅K突破進場」規則，此處只做直通。"""
    return "雙盤底大量紅K突破進場" if double_bottom_breakout_signal else None


@implements_rule("R-CLASSIC-30")
def ma_tangle_breakout(ma_tangle_breakout_signal: bool) -> str | None:
    """均線糾結紅K突破圖：書中原文直接沿用「均線糾結向上突破做多SOP」，此處只做直通。"""
    return "均線糾結轉多頭排列，強力多頭起漲訊號" if ma_tangle_breakout_signal else None


@implements_rule("R-CLASSIC-32")
def island_reversal(island_reversal_signal: bool) -> str | None:
    """島型反轉圖：書中原文直接沿用「低檔島型反轉規則」，此處只做直通。"""
    return "島型反轉，強烈低檔反轉訊號" if island_reversal_signal else None


@implements_rule("R-CLASSIC-33")
def breakout_above_up_channel(close: float, channel_value: float, is_big_red: bool) -> str | None:
    """突破上升軌道線大漲圖：帶大量長紅K收盤突破上升軌道線，漲勢自緩步盤堅轉為加速噴出，全書最強力多頭訊號。"""
    if close > channel_value and is_big_red:
        return "漲勢自緩步盤堅轉為加速噴出，全書最強力多頭訊號"
    return None


@implements_rule("R-CLASSIC-06")
def high_zone_long_upper_shadow_reversal(is_long_upper_shadow_big_volume: bool, is_at_top: bool, next_close_below_bar_low: bool | None = None) -> str | None:
    """高檔大量長上影線反轉圖：高檔爆大量長上影線，先賣二分之一部位；隔日跌破該K棒低點才賣出剩餘，確認一日反轉。"""
    if not (is_long_upper_shadow_big_volume and is_at_top):
        return None
    if next_close_below_bar_low:
        return "賣出剩餘部位，確認一日反轉"
    return "先賣出持股二分之一"


@implements_rule("R-CLASSIC-10")
def breakout_prior_high_then_big_black_fakeout(is_breakout_prior_high: bool, is_big_black_engulf_after: bool, close_back_below_prior_high: bool) -> str | None:
    """突破高檔前高黑K出現大量要下跌圖：突破前高後1~2日內爆量長黑吞噬且收盤跌破前高，前高支撐測試失敗，假突破空頭確認。"""
    if is_breakout_prior_high and is_big_black_engulf_after and close_back_below_prior_high:
        return "假突破：前高支撐測試失敗、反轉為壓力，空頭確認"
    return None


@implements_rule("R-CLASSIC-14")
def break_abc_correction_downtrend(close_below_uptrend_line: bool, close_below_c_low: bool, c_low: float, prior_rally_d: float) -> str | None:
    """跌破ABC下跌圖：收盤跌破上升切線且跌破ABC修正C點低點，空頭確認，等幅測量法(前一段漲幅D)估算下跌目標價。"""
    if not (close_below_uptrend_line and close_below_c_low):
        return None
    target = c_low - prior_rally_d
    return f"跌破ABC修正低點與上升切線，空頭確認，目標價={target}"


@implements_rule("R-CLASSIC-21")
def bottom_wash_out_then_breakout(is_fake_breakdown_prior_low: bool, is_big_volume_breakdown: bool, quick_rebound: bool, close_above_prior_high: bool, is_big_volume_breakout: bool) -> str | None:
    """底部洗盤上攻大漲圖：先假跌破前低爆量洗盤、快速拉回，再爆量突破前高，確認洗盤後攻擊買進。"""
    if not (is_fake_breakdown_prior_low and is_big_volume_breakdown and quick_rebound):
        return None
    if close_above_prior_high and is_big_volume_breakout:
        return "洗盤後突破前高，攻擊買進"
    return "假跌破洗盤（非真跌破），持續觀察是否突破前高"


@implements_rule("R-CLASSIC-08")
def three_day_upper_shadow_distribution(consecutive_3day_upper_shadow_at_resistance_signal: bool, bear_confirmed: bool = False) -> str | None:
    """連3天上漲長上影線大敵當前主力出貨圖：書中原文直接沿用「連3天上漲長上影線」(R-CANDLE-36)的判定，此處只做直通。"""
    if not consecutive_3day_upper_shadow_at_resistance_signal:
        return None
    signal = "連3日長上影，大敵當前，主力出貨警訊"
    return signal + "，空頭確認" if bear_confirmed else signal


@implements_rule("R-CLASSIC-11")
def black_red_black_decline(black_red_black_continuation_signal: bool) -> str | None:
    """黑紅黑續跌圖：書中原文直接沿用「紅黑紅與黑紅黑續勢組合」(R-CANDLE-37)的空方夾擊判定，此處只做直通。"""
    return "黑紅黑空方夾擊，續跌確認" if black_red_black_continuation_signal else None


@implements_rule("R-CLASSIC-23")
def red_black_red_rally(red_black_red_continuation_signal: bool) -> str | None:
    """紅黑紅上漲圖：書中原文直接沿用「紅黑紅與黑紅黑續勢組合」(R-CANDLE-37)的多方夾擊判定，此處只做直通。"""
    return "紅黑紅多方夾擊，續漲訊號（非反轉警訊）" if red_black_red_continuation_signal else None


@implements_rule("R-CLASSIC-31")
def break_abc_correction_uptrend(close_above_downtrend_line: bool, close_above_a_high: bool, a_high: float, ab_range_d: float) -> str | None:
    """突破ABC上漲圖：收盤突破下降切線且站上ABC修正A點高點，多頭續漲確認，等幅測量法(AB段差距D)估算上漲目標價。"""
    if not (close_above_downtrend_line and close_above_a_high):
        return None
    target = a_high + ab_range_d
    return f"突破ABC修正下降切線與A點高點，多頭續漲確認，目標價={target}"
