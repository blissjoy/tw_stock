"""量價分類：R-VOLPRICE-01/03/05/06/07/08/09/10/11。

書中有兩套「大量」基準且明文不可混用：R-VOLPRICE-01用「相對5日均量MA5」(攻擊量1.2~1.3倍、
爆大量2倍以上)；R-VOLPRICE-03起的高檔量能分類改用「相對前一日量」(2倍以上)。這裡用不同
函式名稱清楚區分(`is_big_volume_vs_ma5` vs `is_big_volume_vs_prev_day`)，不共用同一個
「大量」判斷式，避免呼叫端誤用錯的基準。

書中這幾條規則本質是「當天K棒＋次日(或後續固定天數)反應」的逐一事件判斷，不是逐列向量化
計算，這裡沿用 R-SR-14 confirm_resistance/confirm_support 的設計：以純函式接收「當天」與
「次日/後續」的已知資料(由呼叫端從時間序列中取出對應那幾天的值)，回傳判讀結果字串，方便
單元測試逐條驗證，也符合書中原文本身就是逐日敘事的寫法。
"""

from __future__ import annotations

import pandas as pd

from src.rule_registry import implements_rule

# ---------------------------------------------------------------------------
# R-VOLPRICE-01：成交量分類與倍數門檻定義
# ---------------------------------------------------------------------------


@implements_rule("R-VOLPRICE-01")
def basic_volume(volume: pd.Series, n: int = 5) -> pd.Series:
    """基本量：N日平均成交量(預設MA5)，全書量價分類的比較原點。"""
    return volume.rolling(window=n, min_periods=n).mean()


@implements_rule("R-VOLPRICE-01")
def is_attack_volume(volume: pd.Series, ma5_volume: pd.Series, close: pd.Series, lower: float = 1.2, upper: float = 1.3) -> pd.Series:
    """攻擊量：當日量約為基本量1.2~1.3倍，且股價上漲。"""
    ratio = volume / ma5_volume
    return ((ratio >= lower) & (ratio <= upper) & (close > close.shift(1))).fillna(False)


@implements_rule("R-VOLPRICE-01")
def is_big_volume_vs_ma5(volume: pd.Series, ma5_volume: pd.Series, multiple: float = 2.0) -> pd.Series:
    """爆大量(相對MA5基準)：當日量>=基本量2倍以上，方向意涵需搭配位置判斷。"""
    return (volume >= multiple * ma5_volume).fillna(False)


@implements_rule("R-VOLPRICE-01")
def is_stop_fall_volume(volume: pd.Series, ma5_volume: pd.Series, no_new_low_after: pd.Series, multiple: float = 0.5) -> pd.Series:
    """止跌量：下跌走勢中量急縮至基本量0.5倍以下，且後續不再創新低(由呼叫端判斷後傳入布林Series)。"""
    shrink = volume <= multiple * ma5_volume
    return (shrink & no_new_low_after.astype(bool)).fillna(False)


@implements_rule("R-VOLPRICE-01")
def is_accumulation_volume(volume: pd.Series, ma5_volume: pd.Series, close: pd.Series, lower: float = 1.2, upper: float = 1.3, big_multiple: float = 2.0) -> pd.Series:
    """進貨量：出現攻擊量或爆大量，且股價同步上漲，視為主力吸籌訊號。"""
    attack = is_attack_volume(volume, ma5_volume, close, lower, upper)
    big = is_big_volume_vs_ma5(volume, ma5_volume, big_multiple)
    return ((attack | big) & (close > close.shift(1))).fillna(False)


# ---------------------------------------------------------------------------
# R-VOLPRICE-03：高檔爆量三分類判讀（調節量／換手量／出貨量）
# ---------------------------------------------------------------------------


@implements_rule("R-VOLPRICE-03")
def is_big_volume_vs_prev_day(volume: pd.Series, multiple: float = 2.0) -> pd.Series:
    """高檔量能分類的「大量」基準：相對前一日量，與R-VOLPRICE-01相對MA5的基準不同，不可混用。"""
    return (volume >= multiple * volume.shift(1)).fillna(False)


@implements_rule("R-VOLPRICE-03")
def classify_high_volume_bar(
    is_bull_early_or_main_stage: bool,
    pullback_volume_shrunk: bool,
    pullback_near_support_with_reversal_candle: bool,
    pullback_holds_above_prior_low_and_ma20: bool,
    is_black_candle: bool,
    next_day_small_move: bool,
    breaks_above_bar_high_later: bool,
    late_stage_range_broken_down: bool = False,
    surges_then_reverses_within_1_day: bool = False,
) -> str:
    """高檔爆量(相對前一日2倍)三分類：調節量(洗盤)/換手量(偏多延續)/出貨量(空頭風險，二擇一情境)。"""
    if is_bull_early_or_main_stage and pullback_volume_shrunk and pullback_near_support_with_reversal_candle and pullback_holds_above_prior_low_and_ma20:
        return "調節量（主力洗盤，非真出貨）"
    if is_black_candle and next_day_small_move and breaks_above_bar_high_later:
        return "換手量（偏多延續）"
    if late_stage_range_broken_down or surges_then_reverses_within_1_day:
        return "出貨量（空頭風險訊號）"
    return "尚無法判定，持續觀察"


# ---------------------------------------------------------------------------
# R-VOLPRICE-05：多頭起漲量能判讀（5個判讀重點）
# ---------------------------------------------------------------------------


@implements_rule("R-VOLPRICE-05")
def rally_start_attack_signal(is_big_red_start: bool, next_close_up: bool) -> str | None:
    if is_big_red_start and next_close_up:
        return "起漲攻擊量，偏多"
    return None


@implements_rule("R-VOLPRICE-05")
def rally_start_bull_trap_signal(is_big_red_start: bool, next_close_down: bool, next_low_below_start_low: bool, is_at_high_or_late_stage: bool = False) -> str | None:
    if is_big_red_start and next_close_down and next_low_below_start_low:
        return "誘多出貨量" + ("（高檔/末升段起漲更容易出現此情形）" if is_at_high_or_late_stage else "")
    return None


@implements_rule("R-VOLPRICE-05")
def rally_start_low_volume_healthy_signal(is_red_start_without_volume: bool, next_volume_up: bool, next_close_up: bool) -> str | None:
    if is_red_start_without_volume and next_volume_up and next_close_up:
        return "量縮起漲、次日補量續漲，健康多頭延續"
    return None


@implements_rule("R-VOLPRICE-05")
def rally_start_washout_signal(is_big_red_start: bool, subsequent_decline_days: bool, low_held_above_start_low: bool, later_volume_up_and_close_up: bool) -> str | None:
    if is_big_red_start and subsequent_decline_days and low_held_above_start_low and later_volume_up_and_close_up:
        return "前段下跌回溯認定為主力洗盤，非出貨"
    return None


@implements_rule("R-VOLPRICE-05")
def rally_start_fake_breakout_distribution_signal(broke_consolidation_with_big_red: bool, next_is_black_big_volume: bool, next_low_below_start_low: bool) -> str | None:
    if broke_consolidation_with_big_red and next_is_black_big_volume and next_low_below_start_low:
        return "假突破，兩根K線合計大量標記為出貨量"
    return None


# ---------------------------------------------------------------------------
# R-VOLPRICE-06：空頭下跌大量支撐壓力轉化與換手失敗判定
# ---------------------------------------------------------------------------


@implements_rule("R-VOLPRICE-06")
def bear_decline_big_black_role(broke_above_high: bool, broke_below_low: bool) -> str | None:
    """下跌大量長黑K的高點是壓力、低點是支撐：突破高點=多方轉強反彈確認；跌破低點=空方換手失敗。"""
    if broke_above_high:
        return "多方力量轉強，反彈確認"
    if broke_below_low:
        return "空方換手失敗，持續下跌"
    return None


@implements_rule("R-VOLPRICE-06")
def bear_decline_retro_accumulation_label(rebounded_with_big_red: bool, rebound_ended_then_declined: bool, pullback_low_above_support: bool) -> str | None:
    """回溯標記：反彈後再跌不破前低(底底高)，前面的下跌大量黑K可回溯視為主力進貨量(打底第1支腳)。"""
    if rebounded_with_big_red and rebound_ended_then_declined and pullback_low_above_support:
        return "主力進貨量（打底第1支腳）"
    return None


# ---------------------------------------------------------------------------
# R-VOLPRICE-07：窒息量與凹洞量（空頭末跌段力竭反轉訊號）
# ---------------------------------------------------------------------------


@implements_rule("R-VOLPRICE-07")
def is_suffocation_volume(is_big_black_candle: bool, next_close_down: bool, next_volume: float, big_black_volume: float, multiple: float = 0.5) -> bool:
    """窒息量：下跌大量長黑K次日續跌，且量縮小到該大量的一半以下。"""
    return is_big_black_candle and next_close_down and next_volume <= multiple * big_black_volume


@implements_rule("R-VOLPRICE-07")
def is_pothole_volume_pattern(is_suffocation: bool, day_after_is_red: bool, day_after_volume_up: bool, day_after_close_above_open: bool) -> bool:
    """凹洞量：窒息量次日出現放量紅K反彈(大量長黑→窒息量→放量紅K，近似晨星組合)，強力反彈訊號。"""
    return is_suffocation and day_after_is_red and day_after_volume_up and day_after_close_above_open


# ---------------------------------------------------------------------------
# R-VOLPRICE-08：大量K線支撐壓力回溯標記通用規則
# ---------------------------------------------------------------------------


_RETRO_LABEL_MAP = {
    "多頭": {"突破": "攻擊進貨量／未來支撐", "跌破": "出貨量／未來壓力"},
    "空頭": {"突破": "搶反彈進貨量／未來支撐", "跌破": "恐慌出貨量／未來壓力"},
    "盤整": {"突破": "換手成功進貨量／未來支撐", "跌破": "換手失敗出貨量／未來壓力"},
}


@implements_rule("R-VOLPRICE-08")
def classify_big_volume_bar(trend_state: str, bar_high: float, bar_low: float, broke_out_above: bool, broke_down_below: bool) -> tuple[str, float | None]:
    """任一大量K線依後續價格突破/跌破，回溯標記為「進貨量+未來支撐」或「出貨量+未來壓力」，通用於多空盤整三態。"""
    if broke_out_above and not broke_down_below:
        return _RETRO_LABEL_MAP[trend_state]["突破"], bar_low
    if broke_down_below and not broke_out_above:
        return _RETRO_LABEL_MAP[trend_state]["跌破"], bar_high
    return "待突破/跌破結果確認", None


# ---------------------------------------------------------------------------
# R-VOLPRICE-09：多頭轉折關鍵大量K線操作對應（第3點，其餘4點引用其他規則）
# ---------------------------------------------------------------------------


@implements_rule("R-VOLPRICE-09")
def bull_high_key_point_pullback_signal(
    is_high_position: bool, candle_color: str, is_big_volume: bool,
    next_close: float | None = None, bar_low: float | None = None,
    next_open: float | None = None, bar_close: float | None = None,
) -> str | None:
    """高檔大量紅K：跌破紅K最低點則回檔。高檔大量黑K：次日開低走低或開高走低則回檔。"""
    if not (is_high_position and is_big_volume):
        return None
    if candle_color == "紅" and next_close is not None and bar_low is not None and next_close < bar_low:
        return "跌破紅K最低點，回檔"
    if candle_color == "黑" and next_open is not None and bar_close is not None and next_close is not None:
        opened_lower_closed_lower = next_open < bar_close and next_close < next_open
        opened_higher_closed_lower = next_open > bar_close and next_close < next_open
        if opened_lower_closed_lower or opened_higher_closed_lower:
            return "回檔"
    return None


# ---------------------------------------------------------------------------
# R-VOLPRICE-10：空頭轉折關鍵大量K線操作對應（書中對空頭起跌量能的唯一完整段落，完整收錄5條）
# ---------------------------------------------------------------------------


@implements_rule("R-VOLPRICE-10")
def bear_start_decline_stop_loss_signal(is_start_of_decline: bool, is_big_black: bool, next_close_above_high: bool) -> str | None:
    if is_start_of_decline and is_big_black and next_close_above_high:
        return "做空者停損出場"
    return None


@implements_rule("R-VOLPRICE-10")
def bear_start_decline_key_point_signal(
    is_start_big_black: bool, day2_is_red: bool, day2_big_volume: bool,
    day3_close: float | None, day2_low: float | None, day1_high: float | None,
) -> str | None:
    if not (is_start_big_black and day2_is_red and day2_big_volume):
        return None
    if day3_close is not None and day2_low is not None and day3_close < day2_low:
        return "續跌，紅K視為誘多出貨/殺低洗盤騙線"
    if day3_close is not None and day1_high is not None and day3_close > day1_high:
        return "突破黑K高點，停損出場"
    return None


@implements_rule("R-VOLPRICE-10")
def bear_low_key_point_rebound_signal(
    is_low_position: bool, is_big_black: bool,
    next_close: float | None = None, bar_high: float | None = None,
    next_open: float | None = None, bar_close: float | None = None,
) -> str | None:
    if not (is_low_position and is_big_black):
        return None
    if next_close is not None and bar_high is not None and next_close > bar_high:
        return "反彈"
    if next_open is not None and bar_close is not None and next_close is not None:
        opened_higher_closed_higher = next_open > bar_close and next_close > next_open
        opened_lower_closed_higher = next_open < bar_close and next_close > next_open
        if opened_higher_closed_higher or opened_lower_closed_higher:
            return "反彈"
    return None


@implements_rule("R-VOLPRICE-10")
def bear_exhaustion_reversal_signal(is_low_continuous_decline: bool, is_huge_volume: bool, is_red_candle: bool, price_up: bool) -> str | None:
    if is_low_continuous_decline and is_huge_volume and is_red_candle and price_up:
        return "末跌段暴大量出現紅K，容易急漲或反轉趨勢"
    return None


@implements_rule("R-VOLPRICE-10")
def bear_low_divergence_signal(made_new_low: bool, volume_increasing: bool, next_is_red: bool, next_not_falling_further: bool) -> str | None:
    if not (made_new_low and volume_increasing):
        return None
    if next_is_red and next_not_falling_further:
        return "容易反彈或打底"
    return "量價背離（低檔）"


# ---------------------------------------------------------------------------
# R-VOLPRICE-11：壓力支撐區大量K線隔日應對規則
# ---------------------------------------------------------------------------


@implements_rule("R-VOLPRICE-11")
def resistance_zone_big_volume_next_day_response(touched_resistance_no_breakout: bool, next_is_black: bool, next_close_le_today: bool) -> str | None:
    """上漲遇壓力大量K未突破，次日黑K不漲，要回檔。"""
    if touched_resistance_no_breakout and next_is_black and next_close_le_today:
        return "回檔"
    return None


@implements_rule("R-VOLPRICE-11")
def support_zone_big_volume_next_day_response(touched_support_no_breakdown: bool, next_is_red: bool, next_close_ge_today: bool) -> str | None:
    """下跌遇支撐大量K未跌破，次日紅K不跌，要反彈。"""
    if touched_support_no_breakdown and next_is_red and next_close_ge_today:
        return "反彈"
    return None


# ---------------------------------------------------------------------------
# R-VOLPRICE-04：多頭上漲量價背離三型態（價漲量縮／價平量增／價漲量平）
# ---------------------------------------------------------------------------


@implements_rule("R-VOLPRICE-04")
def bull_price_up_volume_shrink_divergence(price_new_high: bool, volume_shrink: bool) -> bool:
    """型態1價漲量縮：股價續創高但量反而縮小，追價買盤轉弱，是量價背離。"""
    return price_new_high and volume_shrink


@implements_rule("R-VOLPRICE-04")
def bull_price_flat_volume_expand_signal(price_flat: bool, volume_expand: bool, position: str) -> str | None:
    """型態2價平量增：位置決定意義完全相反——底部視為進貨訊號(偏多)，高檔/末升段視為潛在出貨訊號(偏空)。"""
    if not (price_flat and volume_expand):
        return None
    if position == "底部":
        return "主力進貨訊號（偏多）"
    if position in ("高檔", "末升段"):
        return "潛在出貨訊號（偏空），待後續下跌後價量關係確認"
    return None


@implements_rule("R-VOLPRICE-04")
def bull_price_up_volume_flat_stall_signal(price_new_high: bool, volume_flat: bool) -> bool:
    """型態3價漲量平：股價續創高但量能未隨之放大，是止漲徵兆，反映主力拉抬與追買意願降低。"""
    return price_new_high and volume_flat


@implements_rule("R-VOLPRICE-02")
def evaluate_volume_signal(
    trend_direction: str | None, position: str | None,
    price_change_today_or_next_day: float | None, volume_change_pct: float | None,
) -> str:
    """量價4維度綜合判讀骨架：任何量能規則觸發前應同時檢查趨勢方向/位置/股價漲跌/量能增減幅度，缺一則訊號可信度下降。"""
    if trend_direction is None or position is None or price_change_today_or_next_day is None or volume_change_pct is None:
        return "資訊不足，暫緩判定"
    return f"趨勢={trend_direction}, 位置={position}, 價格變化={price_change_today_or_next_day}, 量能變化={volume_change_pct}"


@implements_rule("R-VOLPRICE-02")
def wash_trading_risk_flag(is_bottom_zone: bool, is_breakout_with_big_volume: bool) -> str | None:
    """主力對敲風險提示：底部區爆量突破時僅作警示旗標，非否決訊號，須留待後續股價是否延續上漲驗證。"""
    if is_bottom_zone and is_breakout_with_big_volume:
        return "留意主力對敲可能，等待後續股價是否延續上漲驗證"
    return None
