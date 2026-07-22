"""趨勢判定分類（Layer 1）：依賴 pivots.py 輸出的轉折點序列，判斷多空趨勢。

R-TREND-03/04（頭頭高底底高／頭頭低底底低）是全書被引用最多次的核心判斷，
R-TREND-08/09（趨勢改變先知先覺）則是在趨勢已確認的前提下，比對最新一組
轉折點是否已經破壞原架構，比空頭/多頭「完整反轉確認」更早一步的預警訊號。
"""

from __future__ import annotations

from src.indicators.pivots import TurningPoint
from src.rule_registry import implements_rule


def heads_and_bottoms(turning_points: list[TurningPoint]) -> tuple[list[float], list[float]]:
    """把交替排列的轉折點序列拆成頭部價格序列與底部價格序列，依時間先後排列。"""
    heads = [tp.price for tp in turning_points if tp.type == "head"]
    bottoms = [tp.price for tp in turning_points if tp.type == "bottom"]
    return heads, bottoms


@implements_rule("R-TREND-03")
def is_bull_trend(heads: list[float], bottoms: list[float]) -> bool:
    """頭頭高底底高：最近一個頭比前一個頭高，且最近一個底比前一個底高，兩者缺一不可。"""
    if len(heads) < 2 or len(bottoms) < 2:
        return False
    return heads[-1] > heads[-2] and bottoms[-1] > bottoms[-2]


@implements_rule("R-TREND-04")
def is_bear_trend(heads: list[float], bottoms: list[float]) -> bool:
    """頭頭低底底低：最近一個頭比前一個頭低，且最近一個底比前一個底低，兩者缺一不可。"""
    if len(heads) < 2 or len(bottoms) < 2:
        return False
    return heads[-1] < heads[-2] and bottoms[-1] < bottoms[-2]


@implements_rule("R-TREND-08")
def bull_trend_change_warning(
    heads: list[float], bottoms: list[float], bull_trend_previously_confirmed: bool
) -> str | None:
    """多頭趨勢改變先知先覺：出現「底底低」或「頭頭低」任一種即為早期預警，比完整空頭確認更早一步。"""
    if not bull_trend_previously_confirmed:
        return None
    if len(bottoms) >= 2 and bottoms[-1] < bottoms[-2]:
        return "多頭出現改變（底底低），趨勢轉入盤整，短線多單出場"
    if len(heads) >= 2 and heads[-1] < heads[-2]:
        return "多頭出現改變（頭頭低），趨勢轉入盤整，短線多單出場"
    return None


@implements_rule("R-TREND-09")
def bear_trend_change_warning(
    heads: list[float], bottoms: list[float], bear_trend_previously_confirmed: bool
) -> str | None:
    """空頭趨勢改變先知先覺：出現「頭頭高」或「底底高」任一種即為早期預警，與多頭版鏡射對稱。"""
    if not bear_trend_previously_confirmed:
        return None
    if len(heads) >= 2 and heads[-1] > heads[-2]:
        return "空頭出現改變（頭頭高），趨勢轉入盤整，短線空單回補"
    if len(bottoms) >= 2 and bottoms[-1] > bottoms[-2]:
        return "空頭出現改變（底底高），趨勢轉入盤整，短線空單回補"
    return None


@implements_rule("R-TREND-12")
def bull_high_volume_exhaustion_signal(
    volume,  # pd.Series
    open_,   # pd.Series
    close,   # pd.Series
    volume_ma5,  # pd.Series
    is_at_bull_high,  # pd.Series[bool]：由趨勢位置模組（尚未實作）供應，此處為外部輸入
    day_volume_multiple: float = 3.0,
    consecutive_volume_multiple: float = 2.0,
    consecutive_days: int = 3,
    min_hits: int = 2,
):
    """多頭高檔爆量停利訊號：兩種情境皆為書中明確數字門檻。

    情境①：單日量 >= 前一日量的3~5倍（預設取下限3倍）且當天收長黑K。
    情境②：近N天(預設3天)內至少min_hits(預設2)天量 >= 5日均量的2倍，且股價不漲或下跌。
    回傳布林 Series：True 代表當天觸發停利訊號（僅在 is_at_bull_high 為 True 時才有效）。
    """
    import pandas as pd

    scenario1 = (volume >= day_volume_multiple * volume.shift(1)) & (close < open_)

    hit_big_volume = volume >= consecutive_volume_multiple * volume_ma5
    rolling_hits = hit_big_volume.rolling(window=consecutive_days, min_periods=consecutive_days).sum()
    flat_or_down = close <= close.shift(1)
    scenario2 = (rolling_hits >= min_hits) & flat_or_down

    signal = (scenario1 | scenario2) & is_at_bull_high
    return signal.fillna(False) if isinstance(signal, pd.Series) else signal


@implements_rule("R-TREND-06")
def bull_pullback_buy_signal(
    is_bull_trend: bool, pullback_holds_prior_low: bool,
    open_t: float, close_t: float, ma5_t: float, high_prev: float,
    volume_t: float, volume_prev: float, mid_long_red_threshold: float = 0.02,
) -> bool:
    """回後買上漲：多頭趨勢中回檔未跌破前低，帶量中長紅K收盤同時突破MA5與前一日高點。"""
    if not (is_bull_trend and pullback_holds_prior_low):
        return False
    gain_pct = (close_t - open_t) / open_t
    return close_t > ma5_t and close_t > high_prev and volume_t > volume_prev and gain_pct >= mid_long_red_threshold


@implements_rule("R-TREND-06")
def bull_consolidation_breakout_signal(
    prev_trend_bull: bool, is_consolidation: bool,
    open_t: float, close_t: float, upper_neckline: float,
    volume_t: float, avg_volume_prev: float,
    mid_long_red_threshold: float = 0.02, volume_multiple: float = 1.3,
) -> bool:
    """盤整的突破：前一趨勢為多頭的盤整區，帶量(前均量1.3倍以上)中長紅K收盤突破上頸線。"""
    if not (prev_trend_bull and is_consolidation):
        return False
    gain_pct = (close_t - open_t) / open_t
    return close_t > upper_neckline and volume_t > avg_volume_prev * volume_multiple and gain_pct >= mid_long_red_threshold


@implements_rule("R-TREND-07")
def bear_rebound_short_signal(
    is_bear_trend: bool, rebound_fails_prior_high: bool,
    open_t: float, close_t: float, ma5_t: float, low_prev: float,
    volume_t: float, volume_prev: float, long_black_threshold: float = 0.02,
) -> bool:
    """彈後空下跌：空頭趨勢中反彈未突破前高，帶量長黑K收盤同時跌破MA5與前一日低點。"""
    if not (is_bear_trend and rebound_fails_prior_high):
        return False
    loss_pct = (open_t - close_t) / open_t
    return close_t < ma5_t and close_t < low_prev and volume_t > volume_prev and loss_pct >= long_black_threshold


@implements_rule("R-TREND-05")
def is_range_worth_trading(upper_neckline: float, lower_neckline: float, min_width_pct: float = 0.15) -> bool:
    """盤整區間寬度須達15%(書中明確數字)才適合在區間內進出，否則風險報酬比太差。"""
    range_pct = (upper_neckline - lower_neckline) / lower_neckline
    return range_pct >= min_width_pct


@implements_rule("R-TREND-05")
def consolidation_trading_direction(prev_trend: str) -> str | None:
    """盤整期間操作依循前一趨勢方向的順勢原則：前多頭先買後賣，前空頭先賣後買。"""
    if prev_trend == "多頭":
        return "先買（近下頸線）後賣（近上頸線）"
    if prev_trend == "空頭":
        return "先賣（近上頸線）後買（近下頸線）"
    return None


@implements_rule("R-TREND-05")
def classify_consolidation_shape(upper_slope: float, lower_slope: float, price_level: float, flat_threshold: float = 0.001) -> str:
    """盤整4型態分類：三角收斂(頭降底升)/矩形(皆走平)/上升直角三角(頭走平底升)/下降直角三角(頭降底走平)。

    「走平」容許誤差(flat_threshold)書中僅以示意圖呈現、查證外部資料無公認統一慣例，
    此參數為工程估計值(相對股價的斜率千分位)，如實標註非外部佐證數字。
    """
    upper_flat = abs(upper_slope) / price_level < flat_threshold
    lower_flat = abs(lower_slope) / price_level < flat_threshold
    if not upper_flat and not lower_flat and upper_slope < 0 and lower_slope > 0:
        return "三角收斂"
    if upper_flat and lower_flat:
        return "矩形"
    if upper_flat and not lower_flat and lower_slope > 0:
        return "上升直角三角"
    if not upper_flat and upper_slope < 0 and lower_flat:
        return "下降直角三角"
    return "型態未明"


@implements_rule("R-TREND-13")
def bear_confirm_short_timing(is_bear_trend: bool, is_big_volume: bool, is_long_black: bool, close_t: float, ma20_t: float, ma20_slope: float, ma60_t: float) -> str | None:
    """空頭確認爆大量：持有多單應立刻賣出；已跌破下彎月線但未跌破季線可做短空，否則等下次彈後空下跌訊號。"""
    if not (is_bear_trend and is_big_volume and is_long_black):
        return None
    if close_t < ma20_t and ma20_slope < 0 and close_t > ma60_t:
        return "立刻賣出多單；可做短空"
    return "立刻賣出多單；尚未跌破月線，等待下一次彈後空下跌訊號再進場"


@implements_rule("R-TREND-07")
def bear_consolidation_breakdown_signal(
    prev_trend_bear: bool, is_consolidation: bool,
    open_t: float, close_t: float, lower_neckline: float,
    volume_t: float, avg_volume_prev: float,
    long_black_threshold: float = 0.02, volume_multiple: float = 1.3,
) -> bool:
    """盤整的跌破：前一趨勢為空頭的盤整區，帶量長黑K收盤跌破下頸線，與多頭版鏡射對稱。"""
    if not (prev_trend_bear and is_consolidation):
        return False
    loss_pct = (open_t - close_t) / open_t
    return close_t < lower_neckline and volume_t > avg_volume_prev * volume_multiple and loss_pct >= long_black_threshold


@implements_rule("R-TREND-02")
def mid_wave_simplify(zone_high: float, zone_low: float, reversed_from_prior_trend: bool, prior_trend: str) -> list[float]:
    """中波簡化：盤整反轉只取代表性1點（前多頭取區間最高點、前空頭取區間最低點）；續勢盤整取高低點各1組。"""
    if reversed_from_prior_trend:
        return [zone_high] if prior_trend == "多頭" else [zone_low]
    return [zone_high, zone_low]


@implements_rule("R-TREND-02")
def k_line_range_simplify(is_whipsaw_around_ma5: bool, reversed_upward: bool, representative_high: float, representative_low: float) -> float | None:
    """K線橫盤簡化：股價在MA5上下頻繁穿梭、無明顯方向時，只取1個代表性轉折點，避免鋸齒雜訊。"""
    if not is_whipsaw_around_ma5:
        return None
    return representative_low if reversed_upward else representative_high


@implements_rule("R-TREND-02")
def trend_wave_pivot(direction: str, close_t: float, prior_confirmed_low: float, prior_confirmed_high: float) -> str | None:
    """趨勢波簡化：只在多頭回檔跌破前低、或空頭反彈突破前高的關鍵事件才取轉折點，其餘盤整/假突破一律忽略。"""
    if direction == "多頭" and close_t < prior_confirmed_low:
        return "多頭回檔跌破前低，取前一個轉折高點為頭部轉折點"
    if direction == "空頭" and close_t > prior_confirmed_high:
        return "空頭反彈突破前高，取前一個轉折低點為底部轉折點"
    return None


@implements_rule("R-TREND-10")
def base_building_leg1_signal(leg1_low: float, pullback_low: float, rebound_broke_ma20_and_prior_high: bool) -> str | None:
    """打底第1支腳：反彈拉回不破第1支腳低點(底底高)即回補空單；反彈突破月線與前高(頭頭高)則趨勢改變不宜做空。"""
    if pullback_low > leg1_low:
        return "立刻回補空單，留意反轉"
    if rebound_broke_ma20_and_prior_high:
        return "趨勢改變，不宜再做空"
    return None


@implements_rule("R-TREND-10")
def base_building_leg2_confirmed(leg1_low: float, leg2_low: float, close_t: float, resistance: float) -> bool:
    """打底第2支腳（黃金右腳）：低點不破第1支腳(底底高)，且收盤突破兩腳之間反彈高點，反轉確認多頭。"""
    return leg2_low > leg1_low and close_t > resistance


@implements_rule("R-TREND-10")
def base_building_ma_lock_signal(ma10: float, ma10_slope: float, ma20: float, ma20_slope: float, close_t: float, ma60_t: float) -> str | None:
    """打底期間均線排列鎖股：10日均與20日均轉多頭排列即可鎖股做短多；股價進一步站上MA60可鎖股做短中長多。"""
    if close_t > ma60_t:
        return "鎖股準備做短中長多"
    if ma10 > ma20 and ma10_slope > 0 and ma20_slope > 0:
        return "打底接近完成，鎖股準備做短多"
    return None


@implements_rule("R-TREND-11")
def topping_leg1_signal(leg1_high: float, rebound_high: float, breakdown_ma20_and_prior_low: bool) -> str | None:
    """做頭第1個頭：回檔反彈不過第1個頭高點(頭頭低)即立刻出場；回檔跌破月線與前低(底底低)則趨勢改變不宜做多。"""
    if rebound_high < leg1_high:
        return "立刻出場，留意反轉"
    if breakdown_ma20_and_prior_low:
        return "趨勢改變，不宜再做多"
    return None


@implements_rule("R-TREND-11")
def topping_leg2_confirmed(leg1_high: float, leg2_high: float, close_t: float, support: float) -> bool:
    """做頭第2個頭：高點不過第1個頭(頭頭低)，且收盤跌破兩頭之間回檔低點，反轉確認空頭。"""
    return leg2_high < leg1_high and close_t < support


@implements_rule("R-TREND-11")
def topping_ma_lock_signal(ma10: float, ma10_slope: float, ma20: float, ma20_slope: float, close_t: float, ma60_t: float) -> str | None:
    """做頭期間均線排列鎖股：10日均與20日均轉空頭排列即可鎖股做短空；股價進一步跌破MA60可鎖股做短中長空。"""
    if close_t < ma60_t:
        return "均線4線空排，鎖股準備做短中長空"
    if ma10 < ma20 and ma10_slope < 0 and ma20_slope < 0:
        return "做頭接近完成，鎖股準備做短空"
    return None


@implements_rule("R-TREND-11")
def one_day_reversal_high(day_low: float, next_day_low: float) -> bool:
    """一日反轉：高檔爆量長黑K或爆量長上影線出現當天，次日股價跌破當天最低點，多單應立刻出場。"""
    return next_day_low < day_low


def daily_bull_trend_state(high, low, close, n: int = 5):
    """逐日計算「以當下已confirmed的轉折點為準」的多頭趨勢狀態(R-TREND-01+03的逐日版本)。

    刻意不用 pivots.compute_turning_points 一次算完整個序列再事後比對日期，因為那樣會讓
    每個轉折點的「確認時點」與「頭/底本身的日期」混淆，造成look-ahead；這裡直接重現同一套
    狀態機演算法，但在每一天當下就決定是否已經知道最新的多頭趨勢狀態。回傳布林Series。

    這是 scripts/run_backtest_trend14.py 抽取出來的共用邏輯，daily_screener.py 也用同一份，
    避免回測與每日選股各自維護一套一樣的狀態機。
    """
    import pandas as pd

    from src.indicators.moving_average import sma

    ma = sma(close, n)
    result = pd.Series(False, index=close.index)
    heads: list[float] = []
    bottoms: list[float] = []
    state: str | None = None
    group_idx: list[int] = []

    valid_start = ma.first_valid_index()
    if valid_start is None:
        return result
    start_pos = close.index.get_indexer([valid_start])[0]

    for i in range(start_pos, len(close)):
        if close.iloc[i] > ma.iloc[i]:
            cur = "positive"
        elif close.iloc[i] < ma.iloc[i]:
            cur = "negative"
        else:
            cur = state

        if state is None:
            state = cur
            group_idx = [i]
        elif cur == state:
            group_idx.append(i)
        else:
            group_idx.append(i)
            if state == "positive" and cur == "negative":
                head_pos = max(group_idx, key=lambda j: high.iloc[j])
                heads.append(float(high.iloc[head_pos]))
            elif state == "negative" and cur == "positive":
                bottom_pos = min(group_idx, key=lambda j: low.iloc[j])
                bottoms.append(float(low.iloc[bottom_pos]))
            state = cur
            group_idx = [i]

        result.iloc[i] = is_bull_trend(heads, bottoms)

    return result


@implements_rule("R-TREND-14")
def bull_short_term_entry_ready(
    is_bull_trend: bool, ma10: float, ma20: float, ma10_slope: float, ma20_slope: float,
    close_t: float, open_t: float, volume_t: float, volume_prev: float,
    attack_volume_multiple: float = 1.3, body_gain_threshold: float = 0.02,
) -> bool:
    """多頭短線選股6要件：多頭架構+MA10/MA20多排向上+站上兩線+攻擊量(前日1.3倍以上)+紅K實體漲幅>2%。"""
    body_gain_pct = (close_t - open_t) / open_t
    return (
        is_bull_trend
        and ma10 > ma20 and ma10_slope > 0 and ma20_slope > 0
        and close_t > ma10 and close_t > ma20
        and volume_t >= attack_volume_multiple * volume_prev
        and body_gain_pct > body_gain_threshold
    )


@implements_rule("R-TREND-14")
def bull_short_term_stop_loss(entry_bar_low: float, stop_pct: float = 0.05) -> float:
    """多頭短線停損：進場中長紅K最低點下方5%~7%(書中明確區間，超出範圍自動夾回邊界)。"""
    stop_pct = max(0.05, min(stop_pct, 0.07))
    return entry_bar_low * (1 - stop_pct)


@implements_rule("R-TREND-14")
def bull_short_term_exit_action(
    close_t: float, stop_loss: float, has_lower_high: bool, profit_pct: float, ma5_t: float,
    is_rapid_rally_exhaustion: bool = False,
) -> str:
    """多頭短線出場：跌破停損／出現頭頭低優先；獲利>20%或急漲爆量長黑當天出場；獲利>10%且跌破MA5出場；否則續抱。"""
    if close_t < stop_loss:
        return "跌破停損，出場"
    if has_lower_high:
        return "收盤出現頭頭低，出場"
    if profit_pct > 0.20 or is_rapid_rally_exhaustion:
        return "獲利超過20%或連續急漲後大量長黑K強覆蓋/吞噬，當天出場"
    if profit_pct > 0.10 and close_t < ma5_t:
        return "獲利超過10%且跌破MA5，出場"
    return "續抱"


@implements_rule("R-TREND-15")
def bear_short_term_entry_ready(
    is_bear_trend: bool, ma10: float, ma20: float, ma10_slope: float, ma20_slope: float,
    close_t: float, open_t: float, volume_t: float, volume_prev: float,
    ma5_t: float, low_prev: float,
    attack_volume_multiple: float = 1.3, body_loss_threshold: float = 0.02,
) -> bool:
    """空頭短線選股6要件：與R-TREND-14鏡射對稱，另加收盤跌破MA5且跌破前一日低點作為進場觸發。"""
    body_loss_pct = (open_t - close_t) / open_t
    return (
        is_bear_trend
        and ma10 < ma20 and ma10_slope < 0 and ma20_slope < 0
        and close_t < ma10 and close_t < ma20
        and volume_t >= attack_volume_multiple * volume_prev
        and body_loss_pct > body_loss_threshold
        and close_t < ma5_t and close_t < low_prev
    )


@implements_rule("R-TREND-15")
def bear_short_term_stop_loss(entry_bar_high: float, stop_pct: float = 0.05) -> float:
    """空頭短線停損：進場中長黑K最高點上方5%~7%，與多頭版鏡射對稱。"""
    stop_pct = max(0.05, min(stop_pct, 0.07))
    return entry_bar_high * (1 + stop_pct)


@implements_rule("R-TREND-15")
def bear_short_term_exit_action(
    close_t: float, stop_loss: float, has_higher_low: bool, profit_pct: float, ma5_t: float,
    is_rapid_decline_exhaustion: bool = False,
) -> str:
    """空頭短線回補：突破停損／出現底底高優先；獲利>20%或急跌爆量長紅當天回補；獲利>10%且突破MA5回補；否則續抱。"""
    if close_t > stop_loss:
        return "突破停損，回補"
    if has_higher_low:
        return "收盤出現底底高，回補"
    if profit_pct > 0.20 or is_rapid_decline_exhaustion:
        return "獲利超過20%或連續急跌後大量長紅K強覆蓋/吞噬，當天回補"
    if profit_pct > 0.10 and close_t > ma5_t:
        return "獲利超過10%且突破MA5，回補"
    return "續抱"


@implements_rule("R-TREND-16")
def long_entry_taboo_check(
    base_not_above_ma60: bool,
    third_or_later_up_bar: bool,
    divergence_overheated: bool,
    weekly_resistance_nearby: bool,
    pulled_back_below_ma20_not_reclaimed: bool,
    broke_prior_low_then_rallied: bool,
    is_consolidation: bool,
    is_bear_rebound: bool,
    rapid_rally_high_volume_at_high: bool,
    price_up_but_black_candle: bool,
) -> tuple[bool, list[str]]:
    """做多進場10大戒律：反向過濾清單，命中任一條即不應進場，回傳(是否可進場, 命中原因清單)。"""
    checks = {
        "盤底未過月線": base_not_above_ma60,
        "追高風險（上漲第3根以上）": third_or_later_up_bar,
        "量價背離＋指標過熱": divergence_overheated,
        "週線壓力": weekly_resistance_nearby,
        "回檔跌破月線未站回": pulled_back_below_ma20_not_reclaimed,
        "跌破前低再上漲，架構已破壞": broke_prior_low_then_rallied,
        "盤整區間不進場": is_consolidation,
        "一般空頭反彈不做多": is_bear_rebound,
        "連續急漲大量高檔": rapid_rally_high_volume_at_high,
        "進場位置是價漲的黑K": price_up_but_black_candle,
    }
    reasons = [name for name, hit in checks.items() if hit]
    return (len(reasons) == 0, reasons)


@implements_rule("R-TREND-17")
def short_entry_taboo_check(
    top_not_below_ma60: bool,
    third_or_later_down_bar: bool,
    divergence_overheated: bool,
    weekly_support_nearby: bool,
    rebounded_above_ma20_not_broken: bool,
    broke_prior_high_then_declined: bool,
    is_consolidation: bool,
    is_bull_pullback: bool,
    rapid_decline_high_volume_at_low: bool,
    price_down_but_red_candle: bool,
) -> tuple[bool, list[str]]:
    """做空進場10大戒律：與R-TREND-16鏡射對稱，命中任一條即不應進場。"""
    checks = {
        "盤頭未跌破月線": top_not_below_ma60,
        "殺低風險（下跌第3根以上）": third_or_later_down_bar,
        "量價背離＋指標過熱": divergence_overheated,
        "週線支撐": weekly_support_nearby,
        "反彈突破月線未跌破": rebounded_above_ma20_not_broken,
        "突破前高再下跌，架構已破壞": broke_prior_high_then_declined,
        "盤整區間不進場": is_consolidation,
        "一般多頭回檔不做空": is_bull_pullback,
        "連續急跌大量低檔": rapid_decline_high_volume_at_low,
        "進場位置是價跌的紅K": price_down_but_red_candle,
    }
    reasons = [name for name, hit in checks.items() if hit]
    return (len(reasons) == 0, reasons)


@implements_rule("R-TREND-18")
def is_correction_classification_applicable(cumulative_move_pct: float, threshold: float = 0.10) -> bool:
    """修正分類僅在波段漲跌幅達10%~15%以上才適用(書中明確數字，取下限10%為預設門檻)。"""
    return abs(cumulative_move_pct) >= threshold


@implements_rule("R-TREND-18")
def classify_pullback_correction(
    pullback_pct: float, broke_ma20_or_prior_extreme: bool,
    is_consolidation_breakout: bool, is_abc_correction_resolved: bool,
) -> str:
    """修正4種情況：弱勢回檔(<1/2、未破月線前低)延續原趨勢；強勢回檔(>=1/2、破月線前低)轉盤整；盤整突破；ABC修正結束。"""
    if pullback_pct < 0.5 and not broke_ma20_or_prior_extreme:
        return "情況1 弱勢回檔：回後買上漲/彈後空下跌，原趨勢繼續"
    if pullback_pct >= 0.5 and broke_ma20_or_prior_extreme:
        return "情況2 強勢回檔：容易進入頭頭低/底底高盤整，需等重新符合原趨勢架構再進場"
    if is_consolidation_breakout:
        return "情況3 盤整突破：原趨勢繼續"
    if is_abc_correction_resolved:
        return "情況4 ABC修正結束：原趨勢繼續"
    return "修正型態未明"
