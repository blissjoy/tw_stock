"""支撐壓力分類：轉折點/均線的支撐壓力角色判定、有效性4法則、關鍵支撐壓力進出場、趨勢強弱回推
（R-SR-01/02/08/14/15/16/17）。

R-SR-14「遇壓遇撐4法則」是這個分類所有取點規則共用的有效性濾網——書中反覆強調支撐壓力
「並非絕對」，光是價格碰到某個價位不足以判定有效，必須「量＋當日K棒反應＋次日方向確認」
三步驟到齊才算數，這裡把這個共用濾網獨立實作，R-SR-01/02/15/16等規則只負責「找出支撐壓力
在哪裡」，實際是否有效要另外呼叫本模組的 confirm_resistance/confirm_support。
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from src.indicators.candles import candle_shadows, is_mid_long_black_candle, is_mid_long_red_candle
from src.indicators.gaps import Gap
from src.rule_registry import implements_rule

MA_STRENGTH_RANK = {60: 4, 20: 3, 10: 2, 5: 1}  # 數字越大代表支撐/壓力力道越強


@implements_rule("R-SR-01")
def classify_head_role(head_price: float, current_price: float, has_broken_above: bool) -> str:
    """轉折高點(頭)：股價尚未突破時是壓力；一旦收盤突破過，回測時反過來變成支撐。"""
    if current_price < head_price:
        return "壓力"
    if has_broken_above:
        return "支撐"
    return "壓力"


@implements_rule("R-SR-02")
def classify_bottom_role(bottom_price: float, current_price: float, has_broken_below: bool) -> str:
    """轉折低點(底)：股價尚未跌破時是支撐；一旦收盤跌破過，反彈測試時反過來變成壓力。與R-SR-01鏡射。"""
    if current_price > bottom_price:
        return "支撐"
    if has_broken_below:
        return "壓力"
    return "支撐"


@implements_rule("R-SR-08")
def ma_support_conversion_long(close: pd.Series, ma: pd.Series, ma_direction: pd.Series, window_days: int = 3) -> pd.Series:
    """多頭跌破月線(或其他天期均線)支撐後，window_days天內收盤站回且均線仍上揚=支撐依然有效；逾期未站回=支撐轉壓力。"""
    n = len(close)
    result = pd.Series(pd.NA, index=close.index, dtype="object")
    breach_x = None
    for t in range(1, n):
        if breach_x is None:
            if close.iloc[t - 1] >= ma.iloc[t - 1] and close.iloc[t] < ma.iloc[t]:
                breach_x = t
        else:
            days_elapsed = t - breach_x
            if close.iloc[t] >= ma.iloc[t] and ma_direction.iloc[t] == "上揚":
                result.iloc[t] = "月線支撐依然有效，多頭趨勢未變" if days_elapsed <= window_days else "逾期站回，仍判定月線支撐已轉弱一次"
                breach_x = None
            elif days_elapsed > window_days:
                result.iloc[t] = "逾期未站回，月線支撐轉為壓力，多頭趨勢轉弱/反轉"
                breach_x = None
    return result


@implements_rule("R-SR-08")
def ma_resistance_conversion_short(close: pd.Series, ma: pd.Series, ma_direction: pd.Series, window_days: int = 3) -> pd.Series:
    """空頭突破月線壓力後，window_days天內收盤跌破且均線仍下彎=壓力依然有效；逾期未跌破=壓力轉支撐。與多頭版鏡射。"""
    n = len(close)
    result = pd.Series(pd.NA, index=close.index, dtype="object")
    breach_x = None
    for t in range(1, n):
        if breach_x is None:
            if close.iloc[t - 1] <= ma.iloc[t - 1] and close.iloc[t] > ma.iloc[t]:
                breach_x = t
        else:
            days_elapsed = t - breach_x
            if close.iloc[t] <= ma.iloc[t] and ma_direction.iloc[t] == "下彎":
                result.iloc[t] = "月線壓力依然有效，空頭趨勢未變" if days_elapsed <= window_days else "逾期再跌破，仍判定月線壓力已轉強一次"
                breach_x = None
            elif days_elapsed > window_days:
                result.iloc[t] = "逾期未再跌破，月線壓力轉為支撐，空頭趨勢轉強/反轉"
                breach_x = None
    return result


CandleType = Literal["中長紅K", "長上影線K", "中長黑K", "長下影線K", "變盤線", "其他"]


def classify_candle_type(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """把K棒分類成遇壓遇撐4法則使用的5種型態，長上/下影線門檻採與R-CANDLE-25槌子線一致的「>=實體2倍」。"""
    upper_shadow, lower_shadow = candle_shadows(open_, high, low, close)
    body = (close - open_).abs()
    mid_long_red = is_mid_long_red_candle(open_, close)
    mid_long_black = is_mid_long_black_candle(open_, close)
    long_upper = (upper_shadow >= 2 * body) & ~mid_long_red & ~mid_long_black
    long_lower = (lower_shadow >= 2 * body) & ~mid_long_red & ~mid_long_black

    result = pd.Series("其他", index=close.index, dtype="object")
    result = result.mask(mid_long_red, "中長紅K")
    result = result.mask(mid_long_black, "中長黑K")
    result = result.mask(long_upper, "長上影線K")
    result = result.mask(long_lower, "長下影線K")
    result = result.mask(~mid_long_red & ~mid_long_black & ~long_upper & ~long_lower & (body <= (upper_shadow + lower_shadow)), "變盤線")
    return result


@implements_rule("R-SR-14")
def is_big_volume(volume: pd.Series, avg_volume: pd.Series, k: float = 1.5) -> pd.Series:
    return volume > k * avg_volume


@implements_rule("R-SR-14")
def confirm_resistance(
    candle_type_today: str, close_today: float, sr_price: float,
    candle_type_next: str, next_is_red: bool, next_is_big_volume: bool,
) -> str:
    """遇壓4法則：大量中長紅收黑次日=遇壓回檔；長上影線/中長黑觸壓未站穩=遇壓；變盤線待次日確認；突破後次日爆量長黑=疑似假突破。"""
    if candle_type_today == "中長紅K" and close_today <= sr_price:
        return "確認遇壓回檔（闖關前爆大量，股價不漲要回檔）" if not next_is_red else "尚未確認，持續觀察"
    if candle_type_today in ("長上影線K", "中長黑K") and close_today <= sr_price:
        return "遇壓，次日容易下跌"
    if candle_type_today == "變盤線":
        return "次日收黑，確認遇壓回檔" if not next_is_red else "尚未確認，持續觀察"
    if candle_type_today == "中長紅K" and close_today > sr_price:
        if next_is_big_volume and candle_type_next == "中長黑K":
            return "高機率假突破，執行多單停損準備"
        return "突破有效，壓力轉支撐"
    return "尚未觸及有效判斷條件"


@implements_rule("R-SR-14")
def confirm_support(
    candle_type_today: str, close_today: float, sr_price: float,
    candle_type_next: str, next_is_red: bool, next_is_big_volume: bool,
) -> str:
    """遇撐4法則：與遇壓4法則方向完全對稱（紅黑互換、漲跌互換）。"""
    if candle_type_today == "中長黑K" and close_today >= sr_price:
        return "確認遇撐反彈（過撐爆大量，股價不跌要反彈）" if next_is_red else "尚未確認，持續觀察"
    if candle_type_today in ("長下影線K", "中長紅K") and close_today >= sr_price:
        return "遇撐，次日容易反彈"
    if candle_type_today == "變盤線":
        return "次日收紅，確認遇撐反彈" if next_is_red else "尚未確認，持續觀察"
    if candle_type_today == "中長黑K" and close_today < sr_price:
        if next_is_big_volume and candle_type_next == "中長紅K":
            return "高機率假跌破，執行空單停損準備"
        return "跌破有效，支撐轉壓力"
    return "尚未觸及有效判斷條件"


@implements_rule("R-SR-15")
def is_bullish_reversal_candle(open_: float, high: float, low: float, close: float, shadow_ratio: float = 1.0) -> bool:
    """止跌訊號K棒：紅K，或下影線長度>=實體的shadow_ratio倍(書中未給精確倍數，預設1倍)。"""
    is_red = close > open_
    lower_shadow = min(open_, close) - low
    body = abs(close - open_)
    return is_red or lower_shadow > body * shadow_ratio


@implements_rule("R-SR-16")
def is_bearish_reversal_candle(open_: float, high: float, low: float, close: float, shadow_ratio: float = 1.0) -> bool:
    """止漲訊號K棒：黑K，或上影線長度>=實體的shadow_ratio倍。與止跌訊號K棒鏡射。"""
    is_black = close < open_
    upper_shadow = high - max(open_, close)
    body = abs(close - open_)
    return is_black or upper_shadow > body * shadow_ratio


@implements_rule("R-SR-15")
def bullish_support_buy_signal(trend: str, touched_support: bool, support_type: str, is_reversal_candle: bool) -> str | None:
    """多頭回檔4大關鍵支撐(月線/季線/上升切線/前低/下方跳空缺口)+止跌K棒，兩者缺一不可才算買點候選。"""
    if trend != "多頭趨勢" or not touched_support or not is_reversal_candle:
        return None
    return f"{support_type}支撐＋止跌訊號K棒 → 次日買進訊號候選"


@implements_rule("R-SR-16")
def bearish_resistance_short_signal(trend: str, touched_resistance: bool, resistance_type: str, is_reversal_candle: bool) -> str | None:
    """空頭反彈4大關鍵壓力(月線/季線/下降切線/前高/上方跳空缺口)+止漲K棒，與R-SR-15鏡射。"""
    if trend != "空頭趨勢" or not touched_resistance or not is_reversal_candle:
        return None
    return f"{resistance_type}壓力＋止漲訊號K棒 → 次日放空訊號候選"


@implements_rule("R-SR-17")
def bull_trend_strength(
    close: float, ma20: float, ma20_direction: str, prior_low: float, prior_high: float,
    stayed_above_ma10_during_pullback: bool, tested_prior_high: bool,
) -> str:
    """用支撐壓力測試結果回推多頭趨勢強弱，5條規則依優先順序檢查(強勢->不變->測前高->改變->轉弱)。"""
    if stayed_above_ma10_during_pullback:
        return "強勢多頭"
    if close > prior_low and close > ma20 and ma20_direction == "上揚":
        return "多頭趨勢不變，可續做多"
    if tested_prior_high:
        return "多頭沒有改變" if close > prior_high else "多頭進入盤整"
    if close < prior_low:
        return "多頭趨勢改變"
    if close < ma20:
        return "多頭趨勢轉弱（需搭配移動平均線支撐壓力與3日站回觀察窗規則判斷是否真轉弱）"
    return "趨勢持續，無明確變化訊號"


@implements_rule("R-SR-17")
def bull_trend_change_escape_wave(rebounded: bool, rebound_close: float, prior_high: float) -> str | None:
    """多頭趨勢改變後，若後續反彈仍無法突破前高壓力，即為多單逃命波賣點。"""
    if rebounded and rebound_close < prior_high:
        return "多單逃命波賣點"
    return None


@implements_rule("R-SR-17")
def bear_trend_strength(
    close: float, ma20: float, ma20_direction: str, prior_low: float, prior_high: float,
    stayed_below_ma10_during_rebound: bool, tested_prior_low: bool,
) -> str:
    """與 bull_trend_strength 方向完全對稱。"""
    if stayed_below_ma10_during_rebound:
        return "弱勢空頭"
    if close < prior_high and close < ma20 and ma20_direction == "下彎":
        return "空頭趨勢不變，可續做空"
    if tested_prior_low:
        return "空頭沒有改變" if close < prior_low else "空頭進入盤整"
    if close > prior_high:
        return "空頭趨勢改變"
    if close > ma20:
        return "空頭趨勢轉強（需搭配移動平均線支撐壓力與3日站回觀察窗規則判斷是否真轉強）"
    return "趨勢持續，無明確變化訊號"


@implements_rule("R-SR-17")
def bear_trend_change_escape_wave(fell: bool, fall_close: float, prior_low: float) -> str | None:
    """空頭趨勢改變後，若後續下跌仍無法跌破前低支撐，即為空單逃命波回補點。"""
    if fell and fall_close > prior_low:
        return "空單逃命波回補點"
    return None


@implements_rule("R-SR-09")
def gap_support_zone_reaction(gap: Gap, close_t: float, already_breached: bool) -> tuple[str, bool]:
    """向上跳空缺口整個區間都具支撐；一旦收盤跌破缺口下緣即永久失效，之後即使回補也不再具支撐作用。"""
    if already_breached:
        return "缺口已失效，不再具支撐作用（即使已回補）", True
    if gap.lower_edge <= close_t <= gap.upper_edge:
        return "股價回檔進入缺口區間，具支撐作用", False
    if close_t < gap.lower_edge:
        return "缺口支撐已被跌破，空方力道轉強，缺口永久失效", True
    return "股價未觸及缺口區間", False


@implements_rule("R-SR-10")
def gap_resistance_zone_reaction(gap: Gap, close_t: float, already_breached: bool) -> tuple[str, bool]:
    """向下跳空缺口整個區間都具壓力；一旦收盤突破缺口上緣即永久失效，與R-SR-09鏡射對稱。"""
    if already_breached:
        return "缺口已失效，不再具壓力作用（即使已回補）", True
    if gap.lower_edge <= close_t <= gap.upper_edge:
        return "股價反彈進入缺口區間，具壓力作用", False
    if close_t > gap.upper_edge:
        return "缺口壓力已被突破，多方力道轉強，缺口永久失效", True
    return "股價未觸及缺口區間", False


@implements_rule("R-SR-18")
def classify_breakout_holds(fell_back: bool, days_held_above: int, hold_threshold: int = 2) -> str:
    """突破後若未站穩(觀察窗內跌回且站上天數不足)，判定假突破，前高角色不變；否則視為真突破，角色互換。"""
    if fell_back and days_held_above < hold_threshold:
        return "假突破（未站穩）— 前高角色不變，仍為壓力"
    if fell_back and days_held_above >= hold_threshold:
        return "已站穩後才小幅拉回，視為真突破後的正常回測，非假突破"
    return "真突破，前高轉支撐"


@implements_rule("R-SR-18")
def classify_breakdown_holds(bounced_back: bool, days_held_below: int, hold_threshold: int = 2) -> str:
    """跌破後若未站穩，判定假跌破，前低角色不變；否則視為真跌破，與classify_breakout_holds鏡射對稱。"""
    if bounced_back and days_held_below < hold_threshold:
        return "假跌破（未站穩）— 前低角色不變，仍為支撐"
    if bounced_back and days_held_below >= hold_threshold:
        return "已站穩後才小幅反彈，視為真跌破後的正常測試，非假跌破"
    return "真跌破，前低轉壓力"


@implements_rule("R-SR-18")
def evaluate_breakout_window(prior_high: float, window_closes: list[float], hold_threshold: int = 2) -> str:
    """便利函式：直接餵觀察窗內逐日收盤價，自動算出fell_back/days_held_above再分類。"""
    fell_back = any(c < prior_high for c in window_closes)
    days_held_above = sum(1 for c in window_closes if c >= prior_high)
    return classify_breakout_holds(fell_back, days_held_above, hold_threshold)


@implements_rule("R-SR-18")
def evaluate_breakdown_window(prior_low: float, window_closes: list[float], hold_threshold: int = 2) -> str:
    """便利函式：evaluate_breakout_window的跌破鏡射版本。"""
    bounced_back = any(c > prior_low for c in window_closes)
    days_held_below = sum(1 for c in window_closes if c <= prior_low)
    return classify_breakdown_holds(bounced_back, days_held_below, hold_threshold)


FIB_RATIOS = (0.236, 0.382, 0.5, 0.618)


@implements_rule("R-SR-11")
def bull_pullback_support_levels(low_a: float, high_b: float, ratios: tuple[float, ...] = FIB_RATIOS) -> dict[float, float]:
    """多頭回檔黃金分割支撐：從波段低點A拉到高點B，支撐價位 = B - (B-A) x r，常見優先看0.382/0.5。"""
    return {r: high_b - (high_b - low_a) * r for r in ratios}


@implements_rule("R-SR-11")
def bear_rebound_resistance_levels(high_a: float, low_b: float, ratios: tuple[float, ...] = FIB_RATIOS) -> dict[float, float]:
    """空頭反彈黃金分割壓力：從波段高點A拉到低點B，壓力價位 = B + (A-B) x r，與回檔支撐鏡射對稱。"""
    return {r: low_b + (high_a - low_b) * r for r in ratios}


@implements_rule("R-SR-03")
def consolidation_zone_role_strength(price_in_zone: float, zone_low: float, zone_high: float, direction: str) -> float:
    """密集盤整區支撐/壓力力道(0~1)：回測支撐越靠上緣越強，回測壓力越靠下緣越強。zone取自R-CANDLE-04的detect_consolidation()輸出。"""
    position_ratio = (price_in_zone - zone_low) / (zone_high - zone_low)
    if direction == "回測支撐":
        return position_ratio
    if direction == "回測壓力":
        return 1 - position_ratio
    raise ValueError("direction 必須是 '回測支撐' 或 '回測壓力'")


@implements_rule("R-SR-03")
def consolidation_zone_role_transition(close_price: float, zone_low: float, zone_high: float) -> str | None:
    """收盤跌破盤整區下沿，整個區塊轉為壓力區；收盤突破上沿，整個區塊轉為支撐區。"""
    if close_price < zone_low:
        return "整個盤整區轉為壓力區"
    if close_price > zone_high:
        return "整個盤整區轉為支撐區"
    return None


@implements_rule("R-SR-04")
def neckline_touch_signal(low_t: float, high_t: float, lower_neckline: float, upper_neckline: float) -> str | None:
    """觸及盤整區下頸線提供支撐、觸及上頸線提供壓力。頸線取自R-CANDLE-04的upper_neckline/lower_neckline。"""
    if low_t <= lower_neckline:
        return "觸及下頸線，具支撐參考"
    if high_t >= upper_neckline:
        return "觸及上頸線，具壓力參考"
    return None


@implements_rule("R-SR-12")
def bullish_pattern_target_price(neckline_price: float, pattern_low: float) -> float:
    """底部型態(頭肩底/圓弧底/N字底/三重底)完成後量出法目標價：頸線價+型態高度，到達後轉為壓力。"""
    pattern_height = neckline_price - pattern_low
    return neckline_price + pattern_height


@implements_rule("R-SR-12")
def bearish_pattern_target_price(neckline_price: float, pattern_high: float) -> float:
    """頭部型態(頭肩頂/圓弧頂/倒N字頭/三重頂)完成後量出法目標價：頸線價-型態高度，到達後轉為支撐。"""
    pattern_height = pattern_high - neckline_price
    return neckline_price - pattern_height


@implements_rule("R-SR-05")
def find_key_volume_level(high: pd.Series, low: pd.Series, volume: pd.Series, zone_type: str, top_n: int = 2) -> float:
    """頭部/底部區間內成交量最大的top_n根K棒，取其最高價(頭部)或最低價(底部)為關鍵支撐壓力關卡。"""
    top_idx = volume.nlargest(top_n).index
    if zone_type == "頭部":
        return high.loc[top_idx].max()
    if zone_type == "底部":
        return low.loc[top_idx].min()
    raise ValueError(f"zone_type 必須是 '頭部' 或 '底部'，收到：{zone_type!r}")


@implements_rule("R-SR-05")
def key_volume_level_breakout_signal(close: float, key_level: float, zone_type: str) -> str | None:
    """突破頭部大量壓力關卡=做多買點、原壓力轉支撐；跌破底部大量支撐關卡=做空賣點、原支撐轉壓力。"""
    if zone_type == "頭部" and close > key_level:
        return "突破頭部大量壓力關卡，做多買點；原壓力轉為支撐"
    if zone_type == "底部" and close < key_level:
        return "跌破底部大量支撐關卡，做空賣點；原支撐轉為壓力"
    return None


@implements_rule("R-SR-13")
def nearest_round_level(price: float, round_unit: float) -> float:
    """最接近的整數關卡：個股常用100(百元)或1000(千元)為單位，大盤常用1000或10000點。"""
    return round(price / round_unit) * round_unit


@implements_rule("R-SR-13")
def is_near_psychological_level(price: float, round_unit: float, distance_pct_threshold: float = 0.02) -> tuple[bool, float | None]:
    """判定股價是否逼近整數心理關卡：距離百分比書中未給精確門檻，工程補充預設2%。"""
    level = nearest_round_level(price, round_unit)
    distance_pct = abs(price - level) / level
    if distance_pct <= distance_pct_threshold:
        return True, level
    return False, None


@implements_rule("R-SR-19")
def equal_move_target(a_price: float, b_price: float, c_price: float, direction: str) -> tuple[float, str]:
    """等幅測量法(D值量測)：取前一波段A→B幅度D，從突破/跌破點C再往同方向投射一次，目標價=C±D。"""
    d = abs(b_price - a_price)
    if direction == "多頭突破":
        return c_price + d, "壓力"
    if direction == "空頭跌破":
        return c_price - d, "支撐"
    raise ValueError(f"direction 必須是 '多頭突破' 或 '空頭跌破'，收到：{direction!r}")


@implements_rule("R-SR-20")
def detect_wash_out_breakdown(
    base_confirmed: bool,
    breakdown_close: float,
    support_price: float,
    breakdown_volume: float,
    ma5_volume_at_breakdown: float,
    has_long_lower_shadow: bool,
    prior_bush_volume_seen: bool,
    rebound_back_above: bool,
    subsequent_breakout_with_volume: bool,
    k_vol: float = 1.0,
) -> str:
    """洗盤假跌破診斷：跌破區間下緣但量縮/收長下影線/打底期已見草叢量，且後續攻擊性突破，才完整驗證主力洗盤進貨意圖。"""
    if not base_confirmed:
        return "不適用洗盤判讀（尚無打底/盤整架構前置條件），依一般跌破處理"
    if breakdown_close >= support_price:
        return "尚未跌破支撐，不適用"
    if not rebound_back_above:
        return "尚未拉回，暫視為真跌破，支撐轉壓力"

    vol_signature = breakdown_volume <= k_vol * ma5_volume_at_breakdown
    if vol_signature or has_long_lower_shadow or prior_bush_volume_seen:
        signal = "疑似洗盤（假跌破誘空）：跌破區間下緣但量縮/收長下影線/打底期已見草叢量，主力可能低接承接"
        if subsequent_breakout_with_volume:
            return signal + "；且拉回後爆量突破前高，洗盤進貨意圖確認"
        return signal + "；尚未出現後續攻擊性突破，洗盤意圖未完全驗證，僅列為觀察"
    return "跌破後雖有拉回，但無明顯主力承接量能特徵，列為一般假跌破，不預設為洗盤"
