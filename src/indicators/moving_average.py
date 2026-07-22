"""均線分類（Layer 0）：均線計算公式（R-MA-01）、均線多頭/空頭排列（R-MA-08/09）。

均線一律用「收盤價」計算的簡單移動平均（SMA），不是 OHLC 平均——這是
R-MA-01 特別強調、後續所有均線規則都仰賴的地基，實作時務必保持一致。
"""

from __future__ import annotations

import pandas as pd

from src.rule_registry import implements_rule

DEFAULT_BULLISH_PERIODS = (5, 10, 20)
DEFAULT_BEARISH_PERIODS = (5, 10, 20)
FULL_PERIODS = (5, 10, 20, 60, 120, 240)


@implements_rule("R-MA-01")
def sma(close: pd.Series, n: int) -> pd.Series:
    """N 日簡單移動平均（SMA）。MA(N, t) = SUM(Close[t-N+1 .. t]) / N。"""
    return close.rolling(window=n, min_periods=n).mean()


@implements_rule("R-MA-01")
def compute_ma_set(close: pd.Series, periods: tuple[int, ...] = FULL_PERIODS) -> pd.DataFrame:
    """一次算出多條天期的均線，回傳欄位為 MA{N} 的 DataFrame。"""
    return pd.DataFrame({f"MA{n}": sma(close, n) for n in periods})


@implements_rule("R-MA-08")
def is_bullish_aligned(ma_frame: pd.DataFrame, periods: tuple[int, ...] = DEFAULT_BULLISH_PERIODS) -> pd.Series:
    """均線多頭排列：短天期均線 > 長天期均線，依序由上而下排列（例如 MA5>MA10>MA20）。"""
    cols = [f"MA{n}" for n in periods]
    result = pd.Series(True, index=ma_frame.index)
    for shorter, longer in zip(cols, cols[1:]):
        result &= ma_frame[shorter] > ma_frame[longer]
    return result


@implements_rule("R-MA-08")
def is_bullish_aligned_strict(ma_frame: pd.DataFrame, periods: tuple[int, ...] = DEFAULT_BULLISH_PERIODS) -> pd.Series:
    """加強版多頭排列：排列順序正確，且每條均線方向皆向上。"""
    aligned = is_bullish_aligned(ma_frame, periods)
    direction_up = pd.Series(True, index=ma_frame.index)
    for n in periods:
        col = ma_frame[f"MA{n}"]
        direction_up &= col > col.shift(1)
    return aligned & direction_up


@implements_rule("R-MA-09")
def is_bearish_aligned(ma_frame: pd.DataFrame, periods: tuple[int, ...] = DEFAULT_BEARISH_PERIODS) -> pd.Series:
    """均線空頭排列：短天期均線 < 長天期均線，依序由上而下排列（例如 MA5<MA10<MA20）。"""
    cols = [f"MA{n}" for n in periods]
    result = pd.Series(True, index=ma_frame.index)
    for shorter, longer in zip(cols, cols[1:]):
        result &= ma_frame[shorter] < ma_frame[longer]
    return result


@implements_rule("R-MA-09")
def is_bearish_aligned_strict(ma_frame: pd.DataFrame, periods: tuple[int, ...] = DEFAULT_BEARISH_PERIODS) -> pd.Series:
    """加強版空頭排列：排列順序正確，且每條均線方向皆向下。"""
    aligned = is_bearish_aligned(ma_frame, periods)
    direction_down = pd.Series(True, index=ma_frame.index)
    for n in periods:
        col = ma_frame[f"MA{n}"]
        direction_down &= col < col.shift(1)
    return aligned & direction_down


@implements_rule("R-MA-08", "R-MA-09")
def aligned_line_count(ma_frame: pd.DataFrame, periods: tuple[int, ...] = FULL_PERIODS, direction: str = "bullish") -> pd.Series:
    """排列條數：由短天期開始，連續滿足多頭（或空頭）大小關係的均線條數。"""
    cols = [f"MA{n}" for n in periods]
    count = pd.Series(1, index=ma_frame.index)
    still_aligned = pd.Series(True, index=ma_frame.index)
    for shorter, longer in zip(cols, cols[1:]):
        if direction == "bullish":
            step_ok = ma_frame[shorter] > ma_frame[longer]
        elif direction == "bearish":
            step_ok = ma_frame[shorter] < ma_frame[longer]
        else:
            raise ValueError("direction 必須是 'bullish' 或 'bearish'")
        still_aligned &= step_ok
        count += still_aligned.astype(int)
    return count


@implements_rule("R-MA-02")
def holder_profit_state(close: pd.Series, ma: pd.Series) -> pd.Series:
    """均線代表最近N天買進者的平均持有成本：收盤價高於/低於/等於均線，對應獲利/虧損/損平。"""
    state = pd.Series("損平", index=close.index)
    state = state.where(close == ma, "獲利")
    state = state.mask((close < ma) & close.notna() & ma.notna(), "虧損")
    state = state.mask(close.isna() | ma.isna(), pd.NA)
    return state


@implements_rule("R-MA-03")
def ma_weight(n: int) -> float:
    """N 日均線中，單一交易日漲跌對均線數值的影響權重 = 1/N。天期越短，權重越大、反應越快。"""
    return 1.0 / n


@implements_rule("R-MA-03")
def ma_direction(ma: pd.Series) -> pd.Series:
    """均線逐日方向：與前一日數值比較，回傳「上揚」/「下彎」/「走平」。供葛蘭碧8大法則等後續規則使用。"""
    direction = pd.Series(pd.NA, index=ma.index, dtype="object")
    prev = ma.shift(1)
    both_known = ma.notna() & prev.notna()
    direction = direction.mask(both_known & (ma > prev), "上揚")
    direction = direction.mask(both_known & (ma < prev), "下彎")
    direction = direction.mask(both_known & (ma == prev), "走平")
    return direction


@implements_rule("R-MA-05")
def offset_values(close: pd.Series, n: int, as_of: int, max_k: int) -> dict[int, float]:
    """移動扣抵：對未來第 k 個交易日（k=1..max_k），算出屆時 MA(N) 將被扣除的「扣價」。

    扣價 offset_value(k) = Close[as_of + k - n]，只有當這個來源日期 <= as_of（今天已知）
    時才可提前算出，這正是「移動扣抵」能夠未卜先知的原因：扣除的是歷史已發生的價格。
    """
    result: dict[int, float] = {}
    for k in range(1, max_k + 1):
        source_pos = as_of + k - n
        if 0 <= source_pos <= as_of:
            result[k] = close.iloc[source_pos]
    return result


@implements_rule("R-MA-05")
def predict_ma_turn(assumed_close: float, offset_value: float) -> str:
    """比較「假設/預估的未來收盤價」與該日的扣抵值，預判均線屆時會上彎、下彎或走平。"""
    if assumed_close > offset_value:
        return "上彎"
    if assumed_close < offset_value:
        return "下彎"
    return "走平"


@implements_rule("R-MA-21")
def ma_strategy_stop_loss_long(
    entry_open: float,
    entry_close: float,
    entry_low: float,
    swing_low_after_entry: float | None,
    threshold_pct: float = 5.0,
) -> float:
    """均線戰法做多停損（5%分界法）：進場K棒漲幅>=5%用當根低點；否則用進場後的轉折低點。

    swing_low_after_entry：進場後最新出現的「上漲轉折最低點」，由呼叫端用轉折點偵測
    （見 src.indicators.pivots）提供；漲幅<5%時若尚未走出轉折點，退回進場當根低點。
    """
    gain_pct = (entry_close - entry_open) / entry_open * 100
    if gain_pct >= threshold_pct:
        return entry_low
    return swing_low_after_entry if swing_low_after_entry is not None else entry_low


@implements_rule("R-MA-16")
def is_ma_converged(ma_frame: pd.DataFrame, close: pd.Series, periods: tuple[int, ...] = DEFAULT_BULLISH_PERIODS, threshold: float = 0.03) -> pd.Series:
    """均線糾結：參與均線的最大最小差幅相對收盤價 <= threshold(預設3%)，代表短中長天期均線互相靠攏。"""
    cols = [f"MA{n}" for n in periods]
    spread = (ma_frame[cols].max(axis=1) - ma_frame[cols].min(axis=1)) / close
    return (spread <= threshold).fillna(False)


@implements_rule("R-MA-16")
def ma_convergence_line_count(ma_frame: pd.DataFrame, close: pd.Series, periods: tuple[int, ...] = FULL_PERIODS, threshold: float = 0.03) -> pd.Series:
    """從最短天期開始依序納入更長天期均線，回傳目前仍同時落在threshold範圍內的均線條數(3線糾結~6線糾結)。"""
    cols = [f"MA{n}" for n in periods]
    count = pd.Series(0, index=ma_frame.index)
    still_converged = pd.Series(True, index=ma_frame.index)
    included: list[str] = []
    for col in cols:
        included.append(col)
        spread = (ma_frame[included].max(axis=1) - ma_frame[included].min(axis=1)) / close
        still_converged &= (spread <= threshold).fillna(False)
        count += still_converged.astype(int)
    return count


@implements_rule("R-MA-12")
def is_ma_tangled(ma_frame: pd.DataFrame, periods: tuple[int, ...] = DEFAULT_BULLISH_PERIODS) -> pd.Series:
    """盤整均線交錯：既非多頭排列也非空頭排列，訊號不可靠，應作為其他進出場規則的濾網、此時不進場。"""
    bullish = is_bullish_aligned(ma_frame, periods)
    bearish = is_bearish_aligned(ma_frame, periods)
    return ~(bullish | bearish)


@implements_rule("R-INDICATOR-17")
def bias_ratio(close: pd.Series, ma_n: pd.Series) -> pd.Series:
    """乖離率 = (收盤價-均線)/均線 x 100%，常用N=20(MA20)。書中未給「過大」的統一門檻，可搭配R-INDICATOR-18的12~15%參考。"""
    return (close - ma_n) / ma_n * 100


@implements_rule("R-INDICATOR-17")
def classify_bias(close: pd.Series, ma_n: pd.Series) -> pd.Series:
    """正乖離(收盤>均線，過大代表買超)；負乖離(收盤<均線，過大代表超賣)；貼近均線則無乖離。"""
    state = pd.Series("無乖離（貼近均線）", index=close.index, dtype="object")
    state = state.mask(close > ma_n, "正乖離")
    state = state.mask(close < ma_n, "負乖離")
    return state


@implements_rule("R-INDICATOR-19")
def bias_extreme_warning(bias_pct: pd.Series, extreme_threshold: float = 15.0) -> pd.Series:
    """乖離過大時避免追高殺低：|乖離率|達門檻(借用R-INDICATOR-18的MA通道12~15%上緣)才發出警示，非獨立買賣訊號。"""
    return (bias_pct.abs() >= extreme_threshold).fillna(False)


@implements_rule("R-INDICATOR-19")
def is_bias_unreliable_for_stock(is_long_term_range_bound: bool) -> bool:
    """長期緩漲/緩跌/盤整的個股，乖離率多數時間會失效，不適合單獨依賴乖離率判斷。"""
    return is_long_term_range_bound


@implements_rule("R-MA-21")
def ma_strategy_stop_loss_short(
    entry_open: float,
    entry_close: float,
    entry_high: float,
    swing_high_after_entry: float | None,
    threshold_pct: float = 5.0,
) -> float:
    """均線戰法做空停損（5%分界法），與做多方向完全鏡射。"""
    loss_pct = (entry_open - entry_close) / entry_open * 100
    if loss_pct >= threshold_pct:
        return entry_high
    return swing_high_after_entry if swing_high_after_entry is not None else entry_high


MA_PERIODS_BY_HORIZON = {
    "長期策略": ("週線", (10, 20)),
    "中期策略": ("日線", (20, 60)),
    "短期策略": ("日線", (3, 5, 10)),
    "當沖策略": ("分線", (1, 5, 15, 60)),
}


@implements_rule("R-MA-04")
def select_ma_periods(holding_horizon: str) -> tuple[str, tuple[int, ...]]:
    """依交易策略時間長度選用均線天期：長期用週線10/20週、中期用日線20/60日、短期用日線3/5/10日、當沖用分線。"""
    return MA_PERIODS_BY_HORIZON[holding_horizon]


@implements_rule("R-MA-06")
def ma_support_state(close: pd.Series, ma_n: pd.Series) -> pd.Series:
    """均線上彎時的支撐/助漲判定：僅在均線方向向上才成立，跌破後被拉回站上視為助漲，否則僅提示減弱下殺力道。"""
    direction = ma_direction(ma_n)
    is_up = direction == "上揚"
    was_above_yesterday = close.shift(1) >= ma_n.shift(1)
    supported = is_up & (close >= ma_n)
    reclaimed = is_up & (close < ma_n) & was_above_yesterday
    state = pd.Series("無作用（均線非上揚）", index=close.index, dtype="object")
    state = state.mask(is_up, "上揚可減弱黑K下殺力道")
    state = state.mask(reclaimed, "助漲：跌破後可望被拉回站上均線")
    state = state.mask(supported, "支撐：回檔止跌回升機率較高")
    return state


@implements_rule("R-MA-06")
def ma_influence_strength(period_n: int, slope: float) -> float:
    """均線支撐/助漲強度：天期越長、上彎角度(斜率)越大，作用越強，僅供同組均線相對比較。"""
    return period_n * abs(slope)


@implements_rule("R-MA-07")
def ma_resistance_state(close: pd.Series, ma_n: pd.Series) -> pd.Series:
    """均線下彎時的壓力/助跌判定，與R-MA-06完全對稱。"""
    direction = ma_direction(ma_n)
    is_down = direction == "下彎"
    was_below_yesterday = close.shift(1) <= ma_n.shift(1)
    resisted = is_down & (close <= ma_n)
    pulled_back = is_down & (close > ma_n) & was_below_yesterday
    state = pd.Series("無作用（均線非下彎）", index=close.index, dtype="object")
    state = state.mask(is_down, "下彎可減弱紅K上漲力道")
    state = state.mask(pulled_back, "助跌：突破後可望被拉回均線下方")
    state = state.mask(resisted, "壓力：反彈再度轉跌機率較高")
    return state


@implements_rule("R-MA-10")
def is_short_term_bullish_setup(
    close: pd.Series,
    ma5: pd.Series,
    ma10: pd.Series,
    ma20: pd.Series,
    wave_pattern_bullish: pd.Series,
    ma60: pd.Series | None = None,
) -> pd.Series:
    """短多做多成功條件：波浪頭頭高底底高(外部注入) + 3線多排且方向皆向上 + 收盤站上MA20；若提供MA60則檢查4線多排加強版。"""
    three_line_bullish = (ma5 > ma10) & (ma10 > ma20)
    all_rising = (ma5 > ma5.shift(1)) & (ma10 > ma10.shift(1)) & (ma20 > ma20.shift(1))
    setup = wave_pattern_bullish.astype(bool) & three_line_bullish & all_rising & (close > ma20)
    if ma60 is not None:
        setup = setup & (ma20 > ma60) & (ma60 > ma60.shift(1))
    return setup.fillna(False)


@implements_rule("R-MA-11")
def is_short_term_bearish_setup(
    close: pd.Series,
    ma5: pd.Series,
    ma10: pd.Series,
    ma20: pd.Series,
    wave_pattern_bearish: pd.Series,
    ma60: pd.Series | None = None,
) -> pd.Series:
    """短空做空成功條件：波浪頭頭低底底低(外部注入) + 3線空排且方向皆向下 + 收盤跌破MA20；與R-MA-10完全對稱。"""
    three_line_bearish = (ma5 < ma10) & (ma10 < ma20)
    all_falling = (ma5 < ma5.shift(1)) & (ma10 < ma10.shift(1)) & (ma20 < ma20.shift(1))
    setup = wave_pattern_bearish.astype(bool) & three_line_bearish & all_falling & (close < ma20)
    if ma60 is not None:
        setup = setup & (ma20 < ma60) & (ma60 < ma60.shift(1))
    return setup.fillna(False)
