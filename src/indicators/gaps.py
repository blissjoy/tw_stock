"""缺口分類：R-GAP-01/03/04/05/09/11/14/16/19/20。

缺口偵測(R-GAP-01)以「當日最高/最低價」對比「前一日最高/最低價」為準（整根K棒含影線都
不能與前一日重疊），這是「標準版」定義，與9-5章「隱形缺口」只比較開盤價與前一日收盤價
明確不同，兩者不可混用。

真封口(R-GAP-19)／假封口(R-GAP-20)是本分類判斷「缺口是否真的失效」最精確的判別式，
三要素「大量＋實體反向K＋收盤價確實越界」須同時成立才算真封口；只要缺一項，就是假封口，
缺口的支撐/壓力仍然有效——這是本模組所有「缺口是否已失效」判斷的共用依據。
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from src.rule_registry import implements_rule

GapType = Literal["up_gap", "down_gap"]


class Gap(NamedTuple):
    type: GapType
    lower_edge: float
    upper_edge: float
    size: float


@implements_rule("R-GAP-01")
def detect_gap(prev_high: float, prev_low: float, curr_high: float, curr_low: float) -> Gap | None:
    """向上跳空：當日最低價>前一日最高價；向下跳空：當日最高價<前一日最低價。缺口大小恆取正值。"""
    if curr_low > prev_high:
        return Gap("up_gap", prev_high, curr_low, curr_low - prev_high)
    if curr_high < prev_low:
        return Gap("down_gap", curr_high, prev_low, prev_low - curr_high)
    return None


@implements_rule("R-GAP-19")
def is_true_fill(
    gap: Gap, candidate_is_black: bool, candidate_is_red: bool, candidate_has_long_shadow: bool,
    candidate_volume: float, avg_volume: float, candidate_close: float, k_fill: float = 1.5,
) -> bool:
    """真封口三要素(須同時成立)：大量＋實體反向K(非長影線假動作)＋收盤價確實越過缺口對側邊界。"""
    is_large_volume = candidate_volume > k_fill * avg_volume
    if gap.type == "up_gap":
        is_reverse_real_body = candidate_is_black and not candidate_has_long_shadow
        close_crosses_boundary = candidate_close <= gap.lower_edge
    else:
        is_reverse_real_body = candidate_is_red and not candidate_has_long_shadow
        close_crosses_boundary = candidate_close >= gap.upper_edge
    return is_large_volume and is_reverse_real_body and close_crosses_boundary


@implements_rule("R-GAP-20")
def false_fill_reasons(
    gap: Gap, candidate_is_black: bool, candidate_is_red: bool, candidate_has_long_shadow: bool,
    candidate_volume: float, avg_volume: float, candidate_close: float,
    candidate_low: float, candidate_high: float, k_fill: float = 1.5,
) -> list[str]:
    """假封口：真封口三要素任一項不成立即算，回傳原因清單(非空=假封口，缺口支撐/壓力仍然有效)。"""
    if is_true_fill(gap, candidate_is_black, candidate_is_red, candidate_has_long_shadow, candidate_volume, avg_volume, candidate_close, k_fill):
        return []

    if gap.type == "up_gap":
        touched = candidate_low <= gap.lower_edge
        reasons = []
        if candidate_is_black and candidate_has_long_shadow:
            reasons.append("黑K留長下影線，收盤未真正棄守")
        if candidate_is_red and candidate_volume > k_fill * avg_volume:
            reasons.append("大量紅K，反向K棒，買盤主導")
        if candidate_volume < avg_volume:
            reasons.append("量縮，跌破力道不足")
    else:
        touched = candidate_high >= gap.upper_edge
        reasons = []
        if candidate_is_red and candidate_has_long_shadow:
            reasons.append("紅K留長上影線，收盤未真正突破")
        if candidate_is_black and candidate_volume > k_fill * avg_volume:
            reasons.append("大量黑K，反向K棒，賣壓主導")
        if candidate_volume < avg_volume:
            reasons.append("量縮，突破力道不足")

    return reasons if (touched and reasons) else []


class UpGapTiers(NamedTuple):
    上高: float
    上沿: float
    下沿: float
    下底: float


@implements_rule("R-GAP-03")
def build_up_gap_tiers(k_before_high: float, k_before_low: float, k_after_high: float, k_after_low: float, k_after_volume: float, avg_volume: float, k_vol: float = 1.5) -> UpGapTiers | None:
    """向上跳空缺口四層分級(僅適用放量缺口)：上高(最強,缺口後紅K高點)>上沿>下沿>下底(最弱,缺口前紅K低點)。"""
    if k_after_volume < k_vol * avg_volume:
        return None
    return UpGapTiers(上高=k_after_high, 上沿=k_after_low, 下沿=k_before_high, 下底=k_before_low)


@implements_rule("R-GAP-03")
def update_up_gap_state(tiers: UpGapTiers, close_t: float, is_true_filled: bool) -> str:
    """支撐效力階梯式衰減：跌破上高->轉弱警訊；跌破上沿->缺口內震盪(真封口則降級非翻空)；跌破下沿->完全轉弱；跌破下底->多空易位。"""
    if close_t < tiers.下底:
        return "多空易位（正式翻空）"
    if close_t < tiers.下沿:
        return "氣勢完全轉弱（提防下底不可再破）"
    if close_t < tiers.上沿:
        return "降級為一般多頭（缺口已真封口，非強力多頭，但未翻空）" if is_true_filled else "缺口內拉鋸震盪"
    if close_t < tiers.上高:
        return "多頭轉弱警訊（仍屬多頭）"
    return "強力多頭（四層皆未破）"


class DownGapTiers(NamedTuple):
    下底: float
    下沿: float
    上沿: float
    上高: float


@implements_rule("R-GAP-04")
def build_down_gap_tiers(k_before_high: float, k_before_low: float, k_after_high: float, k_after_low: float, k_after_volume: float, avg_volume: float, k_vol: float = 1.5) -> DownGapTiers | None:
    """向下跳空缺口四層分級：與向上版完全鏡射，下底(最強,缺口後黑K低點)>下沿>上沿>上高(最弱,缺口前黑K高點)。"""
    if k_after_volume < k_vol * avg_volume:
        return None
    return DownGapTiers(下底=k_after_low, 下沿=k_after_high, 上沿=k_before_low, 上高=k_before_high)


@implements_rule("R-GAP-04")
def update_down_gap_state(tiers: DownGapTiers, close_t: float, is_true_filled: bool) -> str:
    """壓力效力階梯式衰減，與向上版鏡射：突破上高->空多易位；突破上沿->完全轉弱；突破下沿->缺口內震盪(真封口則降級)；突破下底->轉弱警訊。"""
    if close_t > tiers.上高:
        return "空多易位（正式翻多）"
    if close_t > tiers.上沿:
        return "氣勢完全轉弱（提防上高不可再破）"
    if close_t > tiers.下沿:
        return "降級為一般空頭（缺口已真封口，非強力空頭，但未翻多）" if is_true_filled else "缺口內拉鋸震盪"
    if close_t > tiers.下底:
        return "空頭轉弱警訊（仍屬空頭）"
    return "強力空頭（四層皆未破）"


@implements_rule("R-GAP-05")
def gap_volume_strength(gap_volume: float, avg_volume: float, k_vol: float = 1.5) -> str:
    """量能決定支撐壓力強弱：無量缺口力道弱不建議套用四層分級法；放量缺口力道強可套用。"""
    ratio = gap_volume / avg_volume
    if ratio < 1.0:
        return "無量缺口：支撐/壓力力道弱，不建議套用四層分級法；反轉時風險較高"
    if ratio < k_vol:
        return "普通量能：支撐/壓力力道中等"
    return "放量缺口：支撐/壓力力道強，可套用四層分級法"


@implements_rule("R-GAP-05")
def role_flip_on_breach(gap_type: GapType, breach_direction: Literal["up", "down"]) -> str | None:
    """支撐壓力角色互換：向上缺口跌破後轉為未來反彈壓力(缺口過壓)；向下缺口突破後轉為未來回檔支撐。"""
    if gap_type == "up_gap" and breach_direction == "down":
        return "轉為未來反彈的壓力（缺口過壓）"
    if gap_type == "down_gap" and breach_direction == "up":
        return "轉為未來回檔的支撐"
    return None


@implements_rule("R-GAP-05")
def is_battleground_zone(price_t: float, gap: Gap, was_formed_with_large_volume: bool) -> str | None:
    """大量缺口區＝多空交戰區：股價再次進入當初以大量形成的缺口區間，預期震盪加大，不宜預設單邊方向。"""
    if was_formed_with_large_volume and gap.lower_edge <= price_t <= gap.upper_edge:
        return "大量缺口區多空交戰：預期震盪加大，不宜預設單邊方向"
    return None


@implements_rule("R-GAP-09")
def detect_breakaway_gap_up(gap: Gap, consolidation_upper: float, is_large_volume: bool, gap_filled_within_3_days: bool) -> dict | None:
    """打底完成向上突破缺口：向上跳空且缺口下緣不低於盤整區上緣(真正突破)，強力買進訊號，原壓力轉支撐。"""
    if gap.type != "up_gap" or gap.lower_edge < consolidation_upper:
        return None
    result = {
        "category": "突破缺口（打底完成）",
        "signal": "強力買進訊號" if is_large_volume else "缺乏大量配合，訊號強度降低",
        "support": gap.lower_edge,
    }
    if gap_filled_within_3_days:
        result["warning"] = "3天內回補，視為假突破，提高警覺"
    return result


@implements_rule("R-GAP-14")
def detect_breakaway_gap_down(gap: Gap, topping_pattern_confirmed: bool, gap_filled_within_3_days: bool) -> dict | None:
    """做頭完成向下跌破缺口：與突破缺口鏡射，但不需要大量配合(書中明文的關鍵不對稱)，原支撐轉壓力。"""
    if gap.type != "down_gap" or not topping_pattern_confirmed:
        return None
    result = {
        "category": "做頭完成向下跌破缺口",
        "signal": "空頭確認，持有多單應立刻出場",
        "volume_requirement": "不需要大量配合（與突破缺口不同，無量亦成立）",
        "resistance": gap.upper_edge,
    }
    if gap_filled_within_3_days:
        result["warning"] = "3天內回補，視為假跌破，提高警覺"
    return result


@implements_rule("R-GAP-11")
def detect_exhaustion_gap_up(gap: Gap, is_late_stage_rally: bool, had_huge_volume: bool, volume_shrinking_after: bool, price_stalling: bool, is_filled_within_3_days: bool) -> dict | None:
    """高檔末升段向上竭盡缺口：位置+爆量後量縮+回補三者同時成立才確認；缺口下緣不是可靠支撐(與突破缺口不同)。"""
    if gap.type != "up_gap" or not is_late_stage_rally:
        return None
    if not (had_huge_volume and volume_shrinking_after and price_stalling):
        return {"category": "疑似竭盡缺口（條件不完整，需持續觀察）"}
    result = {"category": "向上竭盡缺口", "support_reliable": False, "confirmed": is_filled_within_3_days}
    result["signal"] = "缺口2-3天內回補，趨勢反轉確認，戒備頭部反轉" if is_filled_within_3_days else "尚未回補，竭盡缺口暫未確認，持續觀察"
    return result


@implements_rule("R-GAP-16")
def detect_exhaustion_gap_down(gap: Gap, is_late_stage_decline: bool, had_huge_volume: bool, volume_shrinking_after: bool, price_stalling: bool, is_filled_within_3_days: bool) -> dict | None:
    """低檔末跌段向下竭盡缺口：與向上竭盡缺口逐字鏡射對稱，缺口上緣不是可靠壓力，回補即視為止跌翻多的潛在機會。"""
    if gap.type != "down_gap" or not is_late_stage_decline:
        return None
    if not (had_huge_volume and volume_shrinking_after and price_stalling):
        return {"category": "疑似竭盡缺口（條件不完整，需持續觀察）"}
    result = {"category": "向下竭盡缺口", "resistance_reliable": False, "confirmed": is_filled_within_3_days}
    result["signal"] = "缺口2-3天內回補，趨勢反轉確認，視為止跌/翻多的潛在機會，非追空時機" if is_filled_within_3_days else "尚未回補，竭盡缺口暫未確認，持續觀察"
    return result


@implements_rule("R-GAP-07")
def detect_island_reversal_bottom(down_gap: Gap, up_gap: Gap, consolidation_days: int) -> dict | None:
    """低檔島型反轉：向下跳空→低檔止跌/盤整→向上跳空，兩缺口區間不重疊(架空)才成立孤島。空單立刻回補。"""
    if down_gap.type != "down_gap" or up_gap.type != "up_gap":
        return None
    if not (down_gap.upper_edge < up_gap.lower_edge):
        return None
    return {
        "category": "低檔島型反轉",
        "consolidation_days": consolidation_days,
        "expected_rally": "盤整天數越多，日後漲幅越大",
        "support": up_gap.lower_edge,
        "action": "空單立刻回補；後續觀察是否轉為低檔反彈型/碎步上漲型/趨勢反轉型",
    }


@implements_rule("R-GAP-07")
def classify_island_reversal_bottom_subtype(
    ma20_turning_up: bool, is_large_volume: bool,
    tests_bottom_high: bool = False, is_gradual_climb: bool = False, is_bull_confirmed: bool = False,
) -> str | None:
    """低檔島型反轉的3種強勢子型態，需先滿足MA20上彎+爆量前提，趨勢反轉型(多頭確認)最強。"""
    if not (ma20_turning_up and is_large_volume):
        return None
    if is_bull_confirmed:
        return "趨勢反轉型（型態最強）"
    if is_gradual_climb:
        return "碎步上漲型"
    if tests_bottom_high:
        return "低檔反彈型"
    return None


@implements_rule("R-GAP-13")
def detect_island_reversal_top(up_gap: Gap, down_gap: Gap, topping_days: int) -> dict | None:
    """高檔島型反轉：與低檔版鏡射，向上跳空→高檔無力盤頭→向下跳空，兩缺口不重疊才成立孤島，積極布空機會。"""
    if up_gap.type != "up_gap" or down_gap.type != "down_gap":
        return None
    if not (up_gap.upper_edge > down_gap.lower_edge):
        return None
    return {
        "category": "高檔島型反轉",
        "topping_days": topping_days,
        "expected_decline": "盤整天數越多，日後跌幅越大",
        "resistance": down_gap.upper_edge,
        "action": "積極布空的好機會（必殺做空）",
    }


@implements_rule("R-GAP-22")
def detect_3day_2gap_up(gap1: Gap, gap2: Gap, all_three_red: bool, position: str, is_huge_volume: bool = False, next_is_hanging_man: bool = False) -> dict | None:
    """向上3日2缺口要大漲：連續3根紅K中出現2次同向上缺口，多空意義依位置而定(打底/起漲/行進/高檔警訊)。"""
    if gap1.type != "up_gap" or gap2.type != "up_gap" or not all_three_red:
        return None
    result: dict = {"category": "向上3日2缺口"}
    if position in ("打底", "起漲"):
        result["signal"] = "強力續漲訊號，可鎖股做多；只要缺口不被回補，股價容易大漲"
    elif position == "行進":
        result["signal"] = "若高檔爆量後不下跌，容易再漲一波"
    elif position == "高檔" and is_huge_volume:
        warning = "高檔爆量3日2缺口，注意股價不漲或下跌、缺口是否遭回補，可能轉為竭盡缺口式反轉警訊"
        if next_is_hanging_man:
            warning += "；次日出現吊人線，變盤警訊加強"
        result["warning"] = warning
    return result


@implements_rule("R-GAP-23")
def detect_3day_2gap_down(gap1: Gap, gap2: Gap, all_three_black: bool, position: str, is_huge_volume: bool = False) -> dict | None:
    """向下3日2缺口要大跌：與向上版完全鏡射，連續3根黑K中出現2次同向下缺口。"""
    if gap1.type != "down_gap" or gap2.type != "down_gap" or not all_three_black:
        return None
    result: dict = {"category": "向下3日2缺口"}
    if position in ("做頭", "起跌"):
        result["signal"] = "強力續跌訊號，可鎖股做空；只要缺口不被回補，股價容易大跌"
    elif position == "行進":
        result["signal"] = "若低檔爆量後不再上漲，容易再跌一波"
    elif position == "低檔" and is_huge_volume:
        result["warning"] = "低檔爆量3日2缺口，股價容易反彈，可能轉為竭盡缺口式最後殺盤訊號，非續跌訊號"
    return result


FIBONACCI_DAYS = {1, 3, 5, 8, 13}


@implements_rule("R-GAP-17")
def gap_long_red_hold_condition(close_t: float, gap: Gap) -> bool:
    """缺口之上見長紅K：拉回做多方向是否仍有效，收盤只要不跌破缺口上沿就維持多方。"""
    return close_t >= gap.upper_edge


@implements_rule("R-GAP-17")
def classify_gap_long_red_consolidation(close_t: float, long_red_high: float, long_red_low: float, long_red_close: float) -> str | None:
    """型態A：二分之一價之上、收盤價之下的強勢整理；型態B：收盤價之上的更強勢整理(可追價)。"""
    half_price = (long_red_high + long_red_low) / 2
    if close_t >= long_red_close:
        return "強勢整理型態B（收盤價之上，可追價）"
    if half_price <= close_t < long_red_close:
        return "強勢整理型態A（二分之一價之上、收盤價之下）"
    return None


@implements_rule("R-GAP-17")
def is_breakout_watch_day(consolidation_days: int) -> bool:
    """橫盤第1/3/5/8/13天的次日，是費波那契數列規律下的潛在發動時間點。"""
    return consolidation_days in FIBONACCI_DAYS


@implements_rule("R-GAP-18")
def gap_long_black_hold_condition(close_t: float, gap: Gap) -> bool:
    """缺口之下見長黑K：反彈做空方向是否仍有效，收盤只要不突破缺口下沿就維持空方。"""
    return close_t <= gap.lower_edge


@implements_rule("R-GAP-10")
def upward_runaway_gap_signal(gap: Gap, trend_is_bull_established: bool, is_filled_within_window: bool, bull_structure_unchanged: bool = True) -> str | None:
    """上漲行進缺口：主升段內部的缺口，不回補=續漲機率高；回補但架構未變=趨勢轉弱非反轉；架構已變=需重新評估。"""
    if gap.type != "up_gap" or not trend_is_bull_established:
        return None
    if not is_filled_within_window:
        return "缺口未回補，強勢上漲持續，續漲機率高"
    if bull_structure_unchanged:
        return "缺口已回補，攻擊功能喪失，趨勢轉弱但未反轉"
    return "缺口已回補，且多頭架構已改變，需重新評估趨勢"


@implements_rule("R-GAP-10")
def measure_escape_gap_up_target(gap: Gap, prior_leg_start_price: float) -> float:
    """逃逸缺口(量測缺口)經驗量測法則：後續漲幅約等於缺口前那段漲幅，僅為經驗值非精確保證。"""
    prior_leg_gain = gap.lower_edge - prior_leg_start_price
    return gap.upper_edge + prior_leg_gain


@implements_rule("R-GAP-15")
def downward_runaway_gap_signal(gap: Gap, trend_is_bear_established: bool, is_filled_within_window: bool, bear_structure_unchanged: bool = True) -> str | None:
    """下跌行進缺口：與上漲行進缺口完全鏡射。"""
    if gap.type != "down_gap" or not trend_is_bear_established:
        return None
    if not is_filled_within_window:
        return "缺口未回補，強勢下跌持續，續跌機率高"
    if bear_structure_unchanged:
        return "缺口已回補，下跌功能喪失，強度轉弱但未翻多"
    return "缺口已回補，且空頭架構已改變，需重新評估趨勢"


@implements_rule("R-GAP-15")
def measure_escape_gap_down_target(gap: Gap, prior_leg_start_price: float) -> float:
    """逃逸缺口向下量測法則：後續跌幅約等於缺口前那段跌幅，經驗值非精確保證。"""
    prior_leg_loss = prior_leg_start_price - gap.upper_edge
    return gap.lower_edge - prior_leg_loss


@implements_rule("R-GAP-08")
def classify_common_gap_in_range(gap: Gap, consolidation_upper: float, filled_within_2_days: bool) -> dict | None:
    """底部盤整區普通缺口：缺口仍在盤整區內部(未突破上緣)，通常1~2天內即封閉，無明顯支撐意義，不宜作為進出場依據。"""
    if gap.type != "up_gap" or gap.upper_edge >= consolidation_upper:
        return None
    return {
        "category": "區域缺口／普通缺口",
        "significance": "低，不宜作為進出場依據",
        "support_significance": "無",
        "filled_within_2_days": filled_within_2_days,
    }


@implements_rule("R-GAP-12")
def pullback_gap_down_signal(gap: Gap, is_bull_trend: bool, close_t: float, swing_low: float, rebound_fails_prior_high: bool = False) -> str | None:
    """漲多回檔向下跳空缺口：多頭中的向下跳空第一時間視為正常回檔，須先看是否跌破前低，不可貿然做空。"""
    if gap.type != "down_gap" or not is_bull_trend:
        return None
    if close_t >= swing_low:
        return "未跌破前低，視為多頭正常回檔，不可做空"
    if rebound_fails_prior_high:
        return "底底低+頭頭低同時成立，空頭反轉確認"
    return "跌破前低，形成底底低"


@implements_rule("R-GAP-12")
def pullback_short_scalp_signal(consecutive_red_or_limitup_days: int, had_huge_volume: bool, close_t: float, prev_low: float, min_days: int = 3) -> str | None:
    """急漲後爆量跳空跌破前一日低點，可少量搶「急漲後回檔」空單，停損設於當天黑K高點。"""
    if consecutive_red_or_limitup_days >= min_days and had_huge_volume and close_t < prev_low:
        return "可少量搶空單，停損設於當天黑K高點"
    return None


@implements_rule("R-GAP-06")
def rebound_gap_up_signal(gap: Gap, is_bear_trend: bool, close_t: float, swing_high: float) -> str | None:
    """跌深反彈向上跳空缺口：空頭中的向上跳空第一時間視為反彈，未過前高不可做多，突破月線/前高則列入打底觀察股。"""
    if gap.type != "up_gap" or not is_bear_trend:
        return None
    if close_t <= swing_high:
        return "未過前高，僅視為空頭反彈，不可做多"
    return "突破月線或前高，列入打底觀察股名單"


@implements_rule("R-GAP-06")
def rebound_short_scalp_signal(consecutive_black_or_limitdown_days: int, had_huge_volume: bool, close_t: float, prev_high: float, min_days: int = 3) -> str | None:
    """急跌後爆量跳空突破前一日高點，可少量搶反彈，須設停損停利，屬短線操作。"""
    if consecutive_black_or_limitdown_days >= min_days and had_huge_volume and close_t > prev_high:
        return "可少量搶反彈（需設停損停利，短線操作）"
    return None


@implements_rule("R-GAP-21")
def detect_crossfire_zone(upper_gap: Gap, lower_gap: Gap, upper_already_true_filled: bool, lower_already_true_filled: bool, price_t: float) -> dict | None:
    """缺口多空交鋒：上方未回補缺口為壓力、下方未回補缺口為支撐，股價夾在中間區間震盪，任一方真封口前不宜單邊表態。"""
    if upper_already_true_filled or lower_already_true_filled:
        return None
    if lower_gap.upper_edge <= price_t <= upper_gap.lower_edge:
        return {
            "category": "多空交鋒（雙缺口區間震盪）",
            "resistance": upper_gap.lower_edge,
            "support": lower_gap.upper_edge,
            "signal": "區間震盪，不宜貿然單邊表態，等待一方真封口",
        }
    return None


@implements_rule("R-GAP-24")
def detect_invisible_gap(prev_close: float, open_t: float, close_t: float) -> dict | None:
    """隱形缺口：以「開盤價 vs 前一日收盤價」為基準(非標準缺口的高低價定義)，且收盤未回補此跳空才成立。"""
    if open_t > prev_close and close_t >= prev_close:
        return {"type": "up_invisible_gap", "boundary": prev_close}
    if open_t < prev_close and close_t <= prev_close:
        return {"type": "down_invisible_gap", "boundary": prev_close}
    return None


@implements_rule("R-GAP-18")
def classify_gap_long_black_consolidation(close_t: float, long_black_high: float, long_black_low: float, long_black_close: float) -> str | None:
    """型態A：二分之一價之下、收盤價之上的弱勢整理；型態B：收盤價之下的更弱勢整理(可追價放空)。"""
    half_price = (long_black_high + long_black_low) / 2
    if close_t <= long_black_close:
        return "弱勢整理型態B（收盤價之下，可追價放空）"
    if long_black_close < close_t <= half_price:
        return "弱勢整理型態A（二分之一價之下、收盤價之上）"
    return None


@implements_rule("R-GAP-02")
def classify_gap_cause(
    is_ex_dividend_or_capital_change: bool,
    gap_day_volume: float,
    avg_volume_n: float,
    main_force_volume_multiple: float = 2.0,
) -> str:
    """缺口成因三分類：除權息/增減資缺口(排除於型態統計樣本)優先判定；其餘以量能是否顯著放大代理區分主力發動缺口與消息面缺口。"""
    if is_ex_dividend_or_capital_change:
        return "除權息或增減資缺口（技術性，意義不大，建議排除於型態規則統計樣本）"
    if gap_day_volume > main_force_volume_multiple * avg_volume_n:
        return "疑似主力發動缺口（訊號權重最高，優先關注）"
    return "疑似消息面缺口（預期偏短期震盪；若可比對新聞為經濟政策面消息，則放寬持續觀察天數）"
