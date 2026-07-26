"""黃金層規則掃描：不需要「趨勢狀態/轉折點」中間輸入、能直接從OHLCV+基礎技術指標(均線/
MACD/KD/RSI/布林通道/量能/K棒幾何)判斷「今天有沒有觸發」的規則庫訊號，供
`daily_screener.analyze_stock_signals()`的「個股分析」面板使用。

跟`daily_screener.py`裡`_SCREEN_FUNCTIONS`(整套進場SOP，含進場價/停損建議)不同，這裡每
一條規則只回答「這個技術現象今天有沒有發生」的單點判斷(例如「今天是不是均線多頭排列」)，
不含進場/停損建議，UI呈現時是「今天符合的訊號」清單的一部分，不是可下單的候選股。

規則庫共246條，這裡刻意只掃描一個有明確範圍的子集，排除的類別：
- 需要「趨勢位置(高檔/低檔/起漲/主升段/末升段)」中間輸入的規則(例如candle_patterns_2.py
  一大批`is_at_high`/`is_at_low`參數的函式、`wave_pattern_bullish`等)——本專案目前沒有
  一套自動判斷「現在處於趨勢的哪個階段」的共用邏輯，這類規則留待之後建好更細緻的「趨勢
  位置」分類器再接(比現有的多頭/空頭/盤整判斷更進一步)。
- 需要三大法人籌碼/基本面(股本、營收)/新聞面/當日盤中tick資料的規則——本專案完全沒有
  抓這些資料。
- 需要「未來資料才能確認」的規則(例如`is_stop_fall_volume`的`no_new_low_after`，要事後
  才知道後續有沒有創新低)——維持本專案「用今天以前的資料判斷今天」的即時評估原則，不
  用未來資料才能下的結論(同R-GAP-09當初的修正經驗)。
- 純數值/永遠有值的計算函式(例如`sma`、`bias_ratio`的數值本身、`ma_weight`)——這些不是
  「有沒有觸發」的訊號，是訊號賴以計算的中繼值，不適合當成獨立的「符合訊號」項目列出。

2026-07-24第一階段(「黃金層」)先接了完全不需要趨勢狀態的規則。第二階段接上
`src/patterns/trend_state.py`的簡易多頭/空頭/盤整分類器(串接R-TREND-01轉折點取點+
R-TREND-03/04頭頭高底底高/頭頭低底底低判定)後，解鎖了一批「只需要`trend`這個單一字串
輸入」的規則(R-TREND-03/04本身、R-MA-15黃金死亡交叉配合主趨勢判讀、R-INDICATOR-09
KD依趨勢判讀、R-INDICATOR-22/23布林通道訊號①②)——這些規則不需要「趨勢位置」那種更細緻
的資訊，所以能在這一階段一起接上；「趨勢位置」仍然是尚未解決的下一塊。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.bollinger import (
    bollinger_bands,
    bollinger_buy_signal_1,
    bollinger_buy_signal_2,
    bollinger_buy_signal_3,
    bollinger_sell_signal_1,
    bollinger_sell_signal_2,
    bollinger_sell_signal_3,
)
from src.indicators.candle_patterns_2 import bearish_engulfing_at_high
from src.indicators.candles import big_red_candle_entry_filter, candle_shadows, is_black_candle, is_doji, is_gravestone_line, is_hammer_candle, is_inverted_hammer_candle, is_long_t_line, is_mid_long_black_candle, is_mid_long_red_candle, is_red_candle, is_reversal_candle_at_high, is_reversal_candle_at_low, prev_bar_support_resistance_signal
from src.indicators.consolidation import detect_consolidation_breakout
from src.indicators.crossovers import interpret_cross, is_death_cross, is_golden_cross
from src.indicators.gaps import detect_gap, detect_island_reversal_bottom, false_fill_reasons, is_true_fill
from src.indicators.granville import (
    granville_buy_signal_1,
    granville_buy_signal_2,
    granville_buy_signal_3,
    granville_buy_signal_4,
    granville_sell_signal_1,
    granville_sell_signal_2,
    granville_sell_signal_3,
    granville_sell_signal_4,
)
from src.indicators.kd import compute_kd, is_high_dull, is_low_dull, kd_cross_signal_by_trend, kd_peak_divergence, kd_trough_divergence
from src.indicators.ma_channel import ma_channel_bands, ma_channel_breakout_signal
from src.indicators.macd import (
    compute_macd,
    macd_trend_level_bearish_divergence,
    macd_trend_level_bullish_divergence,
    macd_zero_axis_bear_signal,
    macd_zero_axis_bull_signal,
)
from src.indicators.moving_average import compute_ma_set, is_bearish_aligned, is_bullish_aligned, is_ma_converged, is_ma_tangled, ma_direction, sma
from src.indicators.pivots import compute_turning_points
from src.indicators.rsi import rsi, rsi_overbought_oversold_signal, rsi_short_long_cross_signal
from src.indicators.support_resistance import (
    bear_trend_strength,
    bearish_resistance_short_signal,
    bull_trend_strength,
    bullish_support_buy_signal,
    classify_bottom_role,
    classify_head_role,
    confirm_resistance,
    confirm_support,
    is_bearish_reversal_candle,
    is_bullish_reversal_candle,
    ma_resistance_conversion_short,
    ma_support_conversion_long,
)
from src.indicators.trend import bear_trend_change_warning, bull_high_volume_exhaustion_signal, bull_pullback_buy_signal, bull_trend_change_warning, daily_bear_trend_state, daily_bull_trend_state
from src.indicators.trend_position import compute_trend_position
from src.indicators.trendlines import check_channel_breakdown, check_channel_breakout
from src.indicators.volume_price import basic_volume, bear_decline_big_black_role, bear_low_divergence_signal, bear_low_key_point_rebound_signal, bull_high_key_point_pullback_signal, classify_big_volume_bar, is_accumulation_volume, is_big_volume_vs_ma5, is_big_volume_vs_prev_day, resistance_zone_big_volume_next_day_response, support_zone_big_volume_next_day_response
from src.patterns.chart_overlays import compute_trendlines
from src.patterns.classic_patterns import (
    bear_rebound_consolidate_above_ma20_breakout,
    bear_to_bull_break_rebound_high,
    big_black_breaks_uptrend_line,
    break_above_two_day_low_volume_high,
    break_below_down_channel,
    break_below_two_day_high_volume_low,
    breakout_above_up_channel,
    bull_to_bear_break_last_low,
    chase_short_on_bounce_break,
    double_bottom_platform_breakout,
    double_top_neckline_break,
    gap_down_black_reversal_at_high,
    gap_down_continuation,
    island_reversal,
    low_zone_big_lower_shadow_reversal,
    low_zone_big_red_confirmation,
    ma_tangle_breakout,
    one_day_reversal_at_high,
)
from src.patterns.trend_state import TREND_BEAR, TREND_BULL, TREND_RANGE, classify_trend_states_multi_horizon
from src.screener.screening_rules import double_bottom_breakout_signal
from src.strategies.ma_strategies import ma_tangle_breakout_long_entry
from src.strategies.operation_sop import bear_to_bull_reversal_signal, bull_to_bear_reversal_signal, short_swing_entry_ready

MIN_DAYS = 30  # 均線/MACD/KD/RSI/布林通道都需要暖身天數，資料不足就整批不評估(不逐一判斷各自門檻)
GAP_FILL_LOOKBACK = 20  # R-GAP-19/20往回搜尋「最近一次缺口」的天數上限，避免抓到太久以前已經沒有參考意義的舊缺口
CANDLE04_CONSOLIDATION_MIN_BARS = 20  # R-CANDLE-04盤整天數門檻，書中無明確數字，跟R-GAP-09/14同一個工程估計值
CLASSIC_TWO_DAY_VOLUME_LOOKBACK = 20  # R-CLASSIC-05/25往回搜尋「連續2日大量交易日」的天數上限，書中無明確數字
CLASSIC_BOUNCE_LOOKBACK = 10  # R-CLASSIC-09往回搜尋「反彈紅K」的天數上限，書中無明確數字
CLASSIC_ISLAND_LOOKBACK = 30  # R-CLASSIC-32往回搜尋孤島反轉「前一個向下缺口」的天數上限，書中無明確數字
CLASSIC16_LOOKBACK = 20  # R-CLASSIC-16「低檔短時間內反覆出現大量長紅K」的計數窗口，書中無明確數字
VOLPRICE08_LOOKBACK = 20  # R-VOLPRICE-08往回搜尋「任一大量K棒」的天數上限，書中無明確數字
VOLPRICE06_LOOKBACK = 20  # R-VOLPRICE-06往回搜尋「下跌大量長黑K」的天數上限，書中無明確數字
VOLPRICE10_NEW_LOW_LOOKBACK = 20  # R-VOLPRICE-10「低檔創新低」的比較窗口，書中無明確數字
CANDLE23_LONG_BODY_PCT = 0.065  # R-CANDLE-23「大量長紅K」的長紅門檻，跟R-CANDLE-21「長紅K」定義一致(書中原文數字)


def _last_bool(series: pd.Series) -> bool:
    value = series.iloc[-1]
    return bool(value) if pd.notna(value) else False


def _last_text(series: pd.Series) -> str | None:
    value = series.iloc[-1]
    return str(value) if pd.notna(value) else None


def scan_golden_tier(df: pd.DataFrame, trend_df: pd.DataFrame | None = None) -> list[dict]:
    """對單一股票的OHLCV資料，回傳「今天」實際觸發的黃金層訊號清單，每筆為
    {"rule_id": ..., "note": ...}；資料不足或都沒觸發時回傳空清單。

    trend_df：專門供短/中/長(日/週/月)趨勢分類器使用的、涵蓋更長歷史的OHLCV資料(見
    `src/presentation/chart_data.py`的`TREND_LOOKBACK_DAYS`)——週線/月線重新取樣需要
    足夠長的日線歷史才能取樣出夠多根K棒，若沿用`df`原本（可能只有~120天顯示窗口）的
    資料，週線/月線幾乎必然因為資料不足被誤判成「盤整」。不傳時退回用`df`自己的
    high/low/close(維持向下相容，多數測試/舊呼叫端不需要這麼長的歷史)。
    """
    if len(df) < MIN_DAYS:
        return []
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]
    prev_close = close.shift(1)

    results: list[dict] = []

    def add(rule_id: str, note: str) -> None:
        results.append({"rule_id": rule_id, "note": note})

    # --- 均線 ---
    ma_frame = compute_ma_set(close, periods=(5, 10, 20))
    if _last_bool(is_bullish_aligned(ma_frame)):
        add("R-MA-08", "MA5>MA10>MA20，均線多頭排列")
    if _last_bool(is_bearish_aligned(ma_frame)):
        add("R-MA-09", "MA5<MA10<MA20，均線空頭排列")
    if _last_bool(is_ma_tangled(ma_frame)):
        add("R-MA-12", "均線非多頭亦非空頭排列，處於盤整交錯")
    if _last_bool(is_ma_converged(ma_frame, close)):
        add("R-MA-16", "MA5/MA10/MA20互相靠攏(乖離幅度<=3%)，均線糾結")

    ma5, ma10 = sma(close, 5), sma(close, 10)
    golden_today = _last_bool(is_golden_cross(ma5, ma10))
    death_today = _last_bool(is_death_cross(ma5, ma10))
    if golden_today:
        add("R-MA-13", "MA5上穿MA10，黃金交叉")
    if death_today:
        add("R-MA-14", "MA5下穿MA10，死亡交叉")

    # --- MACD ---
    macd_df = compute_macd(close)
    bull_sig = _last_text(macd_zero_axis_bull_signal(macd_df["DIF"], macd_df["MACD"]))
    if bull_sig:
        add("R-INDICATOR-02", bull_sig)
    bear_sig = _last_text(macd_zero_axis_bear_signal(macd_df["DIF"], macd_df["MACD"]))
    if bear_sig:
        add("R-INDICATOR-03", bear_sig)

    # --- KD ---
    kd_df = compute_kd(high, low, close)
    if _last_bool(is_high_dull(kd_df["K"], kd_df["D"])):
        add("R-INDICATOR-11", "K、D連續3天在80以上，KD高檔鈍化")
    if _last_bool(is_low_dull(kd_df["K"], kd_df["D"])):
        add("R-INDICATOR-11", "K、D連續3天在20以下，KD低檔鈍化")

    # --- 轉折點(供R-INDICATOR-07 MACD趨勢級背離／R-INDICATOR-12 KD背離共用) ---
    # 兩條背離規則都需要「股價轉折頭/底」對應「同一波段某指標的峰/谷值」——這裡用轉折點
    # 當天的指標數值當作那個波段的代表值(工程近似，不是逐波段另外找指標自己的局部峰谷，
    # 書中也沒有規定要多精確比對，抓轉折點當天的數值是最直接、可重現的做法)。
    turning_points = compute_turning_points(high, low, close, n=5)
    tp_heads = [tp for tp in turning_points if tp.type == "head"]
    tp_bottoms = [tp for tp in turning_points if tp.type == "bottom"]
    head_prices = [tp.price for tp in tp_heads]
    bottom_prices = [tp.price for tp in tp_bottoms]

    if len(tp_heads) >= 2:
        osc_peaks = [float(macd_df["OSC"].loc[tp.index]) for tp in tp_heads]
        if macd_trend_level_bullish_divergence(head_prices, osc_peaks):
            add("R-INDICATOR-07", "股價頭頭高但OSC紅柱峰值頭頭低，趨勢級高檔背離，提示多轉空")
        k_peaks = [float(kd_df["K"].loc[tp.index]) for tp in tp_heads]
        peak_divergence = kd_peak_divergence(head_prices, k_peaks)
        if peak_divergence:  # 函式本身回傳文字已經區分「真背離」跟「背離但落在鈍化區、可信度低」兩種情境
            add("R-INDICATOR-12", peak_divergence)
    if len(tp_bottoms) >= 2:
        osc_troughs = [float(macd_df["OSC"].loc[tp.index]) for tp in tp_bottoms]
        if macd_trend_level_bearish_divergence(bottom_prices, osc_troughs):
            add("R-INDICATOR-07", "股價底底低但OSC綠柱谷值(絕對值)底底高，趨勢級低檔背離，提示空轉多")
        k_troughs = [float(kd_df["K"].loc[tp.index]) for tp in tp_bottoms]
        trough_divergence = kd_trough_divergence(bottom_prices, k_troughs)
        if trough_divergence:
            add("R-INDICATOR-12", trough_divergence)

    # --- 趨勢判定(R-TREND-08多頭改變先知先覺/R-TREND-09空頭改變先知先覺) ---
    # 「先知先覺」的重點是比對「今天最新一組頭/底」是否已經出現裂痕，但前提是「昨天為止」
    # 原本的主趨勢已經確立——用daily_bull_trend_state()/daily_bear_trend_state()(逐日、
    # 不用未來資料的狀態機，R-TREND-14已經在用同一套)取「昨天」的確認狀態當前提，不能用
    # 「今天」的多頭/空頭判斷當前提，因為is_bull_trend本身要求頭跟底同時創高，跟這裡「頭
    # 或底任一個出現裂痕」互斥，用今天的判斷結果當前提會讓這條規則永遠不可能觸發。
    bull_trend_yesterday = bool(daily_bull_trend_state(high, low, close, n=5).iloc[-2])
    bull_warning = bull_trend_change_warning(head_prices, bottom_prices, bull_trend_yesterday)
    if bull_warning:
        add("R-TREND-08", bull_warning)
    bear_trend_yesterday = bool(daily_bear_trend_state(high, low, close, n=5).iloc[-2])
    bear_warning = bear_trend_change_warning(head_prices, bottom_prices, bear_trend_yesterday)
    if bear_warning:
        add("R-TREND-09", bear_warning)

    # --- 支撐壓力(R-SR-01/02轉折高低點角色互換) ---
    # classify_head_role/classify_bottom_role本身是「隨時可查詢」的角色分類函式(壓力或
    # 支撐)，不是「今天有沒有觸發」的訊號——這裡收斂成「今天剛好突破/跌破最近一個轉折
    # 高/低點」這個明確的今日事件才回報，避免每天都因為「查詢角色」而重複列出。
    if len(tp_heads) >= 1:
        recent_head = tp_heads[-1]
        if close.iloc[-1] > recent_head.price and close.iloc[-2] <= recent_head.price:
            role = classify_head_role(recent_head.price, float(close.iloc[-1]), has_broken_above=True)
            add("R-SR-01", f"收盤突破前波段轉折高點{recent_head.price:.2f}，原壓力角色轉為{role}")
    if len(tp_bottoms) >= 1:
        recent_bottom = tp_bottoms[-1]
        if close.iloc[-1] < recent_bottom.price and close.iloc[-2] >= recent_bottom.price:
            role = classify_bottom_role(recent_bottom.price, float(close.iloc[-1]), has_broken_below=True)
            add("R-SR-02", f"收盤跌破前波段轉折低點{recent_bottom.price:.2f}，原支撐角色轉為{role}")

    # --- 支撐壓力(R-SR-08月線支撐壓力轉換+3日站回觀察窗) ---
    ma20_direction = ma_direction(ma_frame["MA20"])
    support_conv = _last_text(ma_support_conversion_long(close, ma_frame["MA20"], ma20_direction))
    if support_conv:
        add("R-SR-08", support_conv)
    resistance_conv = _last_text(ma_resistance_conversion_short(close, ma_frame["MA20"], ma20_direction))
    if resistance_conv:
        add("R-SR-08", resistance_conv)

    # --- MA通道(R-INDICATOR-18正負乖離軌道突破) ---
    ma20_for_channel = ma_frame["MA20"]
    channel = ma_channel_bands(ma20_for_channel)
    channel_sig = _last_text(ma_channel_breakout_signal(
        close, channel["upper"], channel["lower"], is_big_volume_vs_prev_day(volume),
    ))
    if channel_sig and "常態" not in channel_sig:
        add("R-INDICATOR-18", channel_sig)

    # --- RSI ---
    rsi9 = rsi(close, n=9)
    rsi_sig = _last_text(rsi_overbought_oversold_signal(rsi9))
    if rsi_sig:
        add("R-INDICATOR-14", rsi_sig)

    rsi6, rsi12 = rsi(close, n=6), rsi(close, n=12)
    rsi_cross_sig = _last_text(rsi_short_long_cross_signal(rsi6, rsi12))
    if rsi_cross_sig:
        add("R-INDICATOR-15", rsi_cross_sig)

    # --- 布林通道 ---
    bb = bollinger_bands(close)
    if _last_bool(bollinger_buy_signal_3(close, bb["mid"], bb["upper"])):
        add("R-INDICATOR-22", "股價在中軌與上軌間向上運行，多頭市場持續做多")
    if _last_bool(bollinger_sell_signal_3(close, bb["mid"], bb["lower"])):
        add("R-INDICATOR-23", "股價在中軌與下軌間向下運行，空頭市場持續做空")

    # --- 趨勢狀態(第二階段：接上src/patterns/trend_state.py的多頭/空頭/盤整分類器) ---
    # 依R-INDICATOR-10書中定義的短(日線)/中(週線)/長(月線)三種天期分別判斷、分別回報——用
    # 單一天期代表「大趨勢」太草率(例如日線走空、週線仍是多頭很常見)，三者可能不一致，UI要
    # 讓使用者看到全部三種，不是只挑一種。
    if trend_df is not None and not trend_df.empty:
        trend_high, trend_low, trend_close = trend_df["high"], trend_df["low"], trend_df["close"]
    else:
        trend_high, trend_low, trend_close = high, low, close
    trend_horizons = classify_trend_states_multi_horizon(trend_high, trend_low, trend_close)
    for label, (timeframe, trend, reason, *_freshness) in trend_horizons.items():
        if trend == TREND_BULL:
            add("R-TREND-03", f"{label}({timeframe}轉折波)：頭頭高且底底高，多頭趨勢成立（依據：{reason}）")
        elif trend == TREND_BEAR:
            add("R-TREND-04", f"{label}({timeframe}轉折波)：頭頭低且底底低，空頭趨勢成立（依據：{reason}）")

    # 下面幾條依賴trend的規則(R-MA-15/KD依趨勢判讀/布林通道訊號①②)書中沒有另外要求區分
    # 短中長天期，沿用短期(日線)天期即可，跟本專案其他規則(R-TREND-14等)慣用的短線框架一致；
    # trend_series用「今天」單一分類值填滿整個index，這幾個函式都只會讀.iloc[-1]
    # (見_last_bool/_last_text)，不需要逐日皆準確的趨勢序列。
    trend_today = trend_horizons["短期"][1]

    if golden_today or death_today:
        cross_event = "黃金交叉" if golden_today else "死亡交叉"
        interpretation = interpret_cross(trend_today, cross_event)
        if interpretation != "無明確訊號":
            add("R-MA-15", f"{cross_event}配合{trend_today}趨勢：{interpretation}")

    trend_series = pd.Series(trend_today, index=close.index)
    kd_trend_sig = _last_text(kd_cross_signal_by_trend(kd_df["K"], kd_df["D"], trend_series))
    if kd_trend_sig:
        add("R-INDICATOR-09", kd_trend_sig)

    if _last_bool(bollinger_buy_signal_1(close, bb["lower"], trend_series)):
        add("R-INDICATOR-22", "空頭下跌至低檔，價格由下往上穿越下軌，搶空頭反彈買進")
    if _last_bool(bollinger_buy_signal_2(close, bb["mid"], trend_series)):
        add("R-INDICATOR-22", "多頭回檔跌破中軌後，近期再站上中軌，買進")
    if _last_bool(bollinger_sell_signal_1(close, bb["upper"], trend_series)):
        add("R-INDICATOR-23", "多頭高檔，價格由上往下穿越上軌，搶多頭回檔賣出/放空")
    if _last_bool(bollinger_sell_signal_2(close, bb["mid"], trend_series)):
        add("R-INDICATOR-23", "空頭反彈突破中軌後，近期再跌回中軌下方，賣出/放空")

    # --- 均線(葛蘭碧8大法則，R-MA-19買點①②③④/R-MA-20賣點①②③④) ---
    # 全部以股價與MA20的相對關係+方向判斷；買點④/賣點④需要is_bull_trend/is_bear_trend，
    # 沿用上面已經算好的trend_series(短期/日線)，跟R-MA-15等既有規則同一套依據。
    ma20 = ma_frame["MA20"]
    is_bull_trend_series = trend_series == TREND_BULL
    is_bear_trend_series = trend_series == TREND_BEAR
    if _last_bool(granville_buy_signal_1(close, ma20)):
        add("R-MA-19", "買點①落底反轉：MA20止跌走平或上揚，股價由均線下方收盤突破")
    if _last_bool(granville_buy_signal_2(close, low, ma20)):
        add("R-MA-19", "買點②多頭回測支撐：MA20上揚，股價回檔貼近但未跌破，隨後轉漲")
    if _last_bool(granville_buy_signal_3(close, ma20)):
        add("R-MA-19", "買點③短暫跌破快速收復：MA20持續上揚，近期短暫跌破後已收復站上")
    if _last_bool(granville_buy_signal_4(close, ma20, is_bear_trend_series)):
        add("R-MA-19", "買點④乖離過大搶反彈：空頭連跌且負乖離過大，股價開始反彈(僅短打)")
    if _last_bool(granville_sell_signal_1(close, ma20)):
        add("R-MA-20", "賣點①盤頭反轉：MA20止漲走平或下彎，股價由均線上方收盤跌破")
    if _last_bool(granville_sell_signal_2(close, high, ma20)):
        add("R-MA-20", "賣點②空頭反彈遇壓：MA20下彎，股價反彈貼近但未突破，隨後轉跌")
    if _last_bool(granville_sell_signal_3(close, ma20)):
        add("R-MA-20", "賣點③短暫突破快速回跌：MA20持續下彎，近期短暫突破後已回跌至下方")
    if _last_bool(granville_sell_signal_4(close, ma20, is_bull_trend_series)):
        add("R-MA-20", "賣點④乖離過大搶回檔：多頭連漲且正乖離過大，股價開始回檔(僅短打)")

    # --- 支撐壓力(R-SR-15多頭回檔關鍵支撐買進/R-SR-16空頭反彈關鍵壓力放空) ---
    # 書中列出4種支撐/壓力來源(月線/季線/上升切線/前低/跳空缺口)，這裡先只接資料需求
    # 最少、最單純的月線(MA20)版本；貼近但未跌破/突破的容忍度比照granville_buy_signal_2
    # 既有的2%門檻，保持同一套工程估計值。其餘3種來源之後有需要可以再擴充。
    ma20_now = float(ma20.iloc[-1])
    touched_support_ma20 = float(low.iloc[-1]) <= ma20_now * 1.02 and float(close.iloc[-1]) >= ma20_now * 0.98
    if touched_support_ma20:
        reversal_up = is_bullish_reversal_candle(
            float(open_.iloc[-1]), float(high.iloc[-1]), float(low.iloc[-1]), float(close.iloc[-1]),
        )
        trend_label = "多頭趨勢" if trend_today == TREND_BULL else "非多頭"
        buy_sig = bullish_support_buy_signal(trend_label, touched_support_ma20, "月線", reversal_up)
        if buy_sig:
            add("R-SR-15", buy_sig)

    touched_resistance_ma20 = float(high.iloc[-1]) >= ma20_now * 0.98 and float(close.iloc[-1]) <= ma20_now * 1.02
    if touched_resistance_ma20:
        reversal_down = is_bearish_reversal_candle(
            float(open_.iloc[-1]), float(high.iloc[-1]), float(low.iloc[-1]), float(close.iloc[-1]),
        )
        trend_label_bear = "空頭趨勢" if trend_today == TREND_BEAR else "非空頭"
        short_sig = bearish_resistance_short_signal(trend_label_bear, touched_resistance_ma20, "月線", reversal_down)
        if short_sig:
            add("R-SR-16", short_sig)

    # --- 缺口(R-GAP-01基本定義與偵測) ---
    today_gap = detect_gap(
        prev_high=float(high.iloc[-2]), prev_low=float(low.iloc[-2]),
        curr_high=float(high.iloc[-1]), curr_low=float(low.iloc[-1]),
    )
    if today_gap is not None:
        gap_label = "向上跳空缺口" if today_gap.type == "up_gap" else "向下跳空缺口"
        add("R-GAP-01", f"今天出現{gap_label}，缺口區間{today_gap.lower_edge:.2f}~{today_gap.upper_edge:.2f}")

    # --- 缺口(R-GAP-19真封口/R-GAP-20假封口) ---
    # 往回找GAP_FILL_LOOKBACK天內最近一次缺口，用「今天」的K棒(量/紅黑/影線)評估是否
    # 構成真封口(大量+實體反向K+收盤確實越界，三者缺一即為假封口)——這是R-CLASSIC-24
    # 已經用過的「往回搜尋最近一根參考K棒」模式，不是新的搜尋邏輯形狀。
    recent_gap = None
    search_end = max(len(close) - 1 - GAP_FILL_LOOKBACK, 1)
    for j in range(len(close) - 2, search_end - 1, -1):
        g = detect_gap(
            prev_high=float(high.iloc[j - 1]), prev_low=float(low.iloc[j - 1]),
            curr_high=float(high.iloc[j]), curr_low=float(low.iloc[j]),
        )
        if g is not None:
            recent_gap = g
            break
    if recent_gap is not None:
        upper_shadow_series, lower_shadow_series = candle_shadows(open_, high, low, close)
        body_today = float(abs(close.iloc[-1] - open_.iloc[-1]))
        has_long_shadow_today = (
            max(float(upper_shadow_series.iloc[-1]), float(lower_shadow_series.iloc[-1])) >= 2 * body_today
            if body_today > 0 else True
        )
        is_black_today = bool(close.iloc[-1] < open_.iloc[-1])
        is_red_today = bool(close.iloc[-1] > open_.iloc[-1])
        avg_volume_20 = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else float(volume.iloc[:-1].mean())
        gap_range = f"{recent_gap.lower_edge:.2f}~{recent_gap.upper_edge:.2f}"
        if is_true_fill(
            recent_gap, is_black_today, is_red_today, has_long_shadow_today,
            float(volume.iloc[-1]), avg_volume_20, float(close.iloc[-1]),
        ):
            add("R-GAP-19", f"大量實體反向K收盤確實越過缺口邊界，真封口，缺口({gap_range})失效")
        else:
            reasons = false_fill_reasons(
                recent_gap, is_black_today, is_red_today, has_long_shadow_today,
                float(volume.iloc[-1]), avg_volume_20, float(close.iloc[-1]),
                float(low.iloc[-1]), float(high.iloc[-1]),
            )
            if reasons:
                add("R-GAP-20", f"假封口（{'；'.join(reasons)}），缺口({gap_range})支撐/壓力仍然有效")

    # --- 切線軌道線(R-LINE-11/12跌破突破分級與角色互換、R-LINE-14/15軌道線突破) ---
    # 重用src/patterns/chart_overlays.py已經在圖表疊圖路徑用的compute_trendlines()——
    # 裡面已經內建R-LINE-01~06(切線畫法+動態更新)+R-LINE-11/12(角色互換)，這裡不重新
    # 實作取點/畫線邏輯，只是多做「今天」跟「昨天」的比較，抓出「剛好是今天發生」的事件
    # (角色互換/軌道突破都是「一旦發生就維持」的狀態，不比較昨天會每天都重複回報)。
    trendlines_today = compute_trendlines(df)
    trendlines_yesterday = compute_trendlines(df.iloc[:-1]) if len(df) > 1 else {}
    x_today, x_yesterday = len(df) - 1, len(df) - 2

    up_tangent = trendlines_today.get("up_tangent")
    up_tangent_prev = trendlines_yesterday.get("up_tangent")
    if up_tangent is not None and up_tangent.role == "resistance":
        if up_tangent_prev is None or up_tangent_prev.role != "resistance":
            add("R-LINE-11", f"上升切線遭收盤跌破，原支撐角色轉為壓力（切線約{up_tangent.at(x_today):.2f}）")

    down_tangent = trendlines_today.get("down_tangent")
    down_tangent_prev = trendlines_yesterday.get("down_tangent")
    if down_tangent is not None and down_tangent.role == "support":
        if down_tangent_prev is None or down_tangent_prev.role != "support":
            add("R-LINE-12", f"下降切線遭收盤突破，原壓力角色轉為支撐（切線約{down_tangent.at(x_today):.2f}）")

    up_channel = trendlines_today.get("up_channel")
    if up_channel is not None and check_channel_breakout(up_channel, x_today, close):
        if not check_channel_breakout(up_channel, x_yesterday, close):
            add("R-LINE-14", f"股價突破上升軌道線上緣（約{up_channel.at(x_today):.2f}），偏多轉強")

    down_channel = trendlines_today.get("down_channel")
    if down_channel is not None and check_channel_breakdown(down_channel, x_today, close):
        if not check_channel_breakdown(down_channel, x_yesterday, close):
            add("R-LINE-15", f"股價跌破下降軌道線下緣（約{down_channel.at(x_today):.2f}），偏空轉強")

    # --- 量能 ---
    ma5_volume = basic_volume(volume)
    if _last_bool(is_accumulation_volume(volume, ma5_volume, close)):
        add("R-VOLPRICE-01", "出現攻擊量或爆大量，且股價同步上漲，主力吸籌訊號")

    # --- K棒幾何 ---
    if _last_bool(is_reversal_candle_at_high(open_, high, low, close, prev_close)):
        add("R-CANDLE-05", "十字線/墓碑線/長T線/紡錘線/槌子/倒槌/跌停/長黑等反轉K棒幾何成立")
    if _last_bool(is_reversal_candle_at_low(open_, high, low, close, prev_close)):
        add("R-CANDLE-13", "十字線/墓碑線/長T線/紡錘線/槌子/倒槌/漲停/長紅等反轉K棒幾何成立")
    if _last_bool(is_hammer_candle(open_, high, low, close)):
        add("R-CANDLE-25", "下影線>=實體2倍、上影線短，槌子線")
    if _last_bool(is_inverted_hammer_candle(open_, high, low, close)):
        add("R-CANDLE-25", "上影線>=實體2倍、下影線短，倒槌K線")

    # --- K棒型態(R-CANDLE-01前一日高低點支撐壓力) ---
    # prev_bar_support_resistance_signal()永遠有值(多空未表態/買方力量轉強/賣方力量轉強)，
    # 只在不是「多空未表態」這個中性狀態時才回報，避免每天都列出。
    prev_bar_sig = _last_text(prev_bar_support_resistance_signal(close, high, low))
    if prev_bar_sig and prev_bar_sig != "多空未表態":
        add("R-CANDLE-01", prev_bar_sig)

    # --- K棒型態(R-CANDLE-04橫盤突破確認) ---
    # 重用daily_screener.py已經在用的detect_consolidation_breakout()，只是這裡評估的是
    # 「今天」單點觸發、不含進場價/停損建議(黃金層訊號的一貫定位)，不是候選清單性質的
    # screen_*函式；CANDLE04_CONSOLIDATION_MIN_BARS取20天(約1個月)，跟R-GAP-09/14的
    # 盤整天數門檻同一個工程估計值，書中這條規則本身沒有另外給出明確天數。
    consolidation_box = detect_consolidation_breakout(open_, high, low, close, min_bars=CANDLE04_CONSOLIDATION_MIN_BARS)
    if _last_bool(consolidation_box["breakout_up"]):
        add("R-CANDLE-04", f"中長紅K收盤突破盤整區上緣{float(consolidation_box['upper_neckline'].iloc[-1]):.2f}，橫盤突破確認")
    if _last_bool(consolidation_box["breakout_down"]):
        add("R-CANDLE-04", f"中長黑K收盤跌破盤整區下緣{float(consolidation_box['lower_neckline'].iloc[-1]):.2f}，橫盤跌破確認")

    # --- 經典型態總覽：本模組不重新實作任何底層判斷邏輯，只把上面已經算好的切線/轉折點/
    # 缺口/均線糾結/雙頂雙底等building block「組」成書中33張精華圖描述的複合訊號，餵給
    # src/patterns/classic_patterns.py裡對應的組合函式。today_idx指向「今天」那一列。
    today_idx = len(close) - 1
    avg_vol_5d = float(volume.iloc[-6:-1].mean()) if len(volume) >= 6 else float(volume.iloc[:-1].mean())
    is_big_black_today = bool(is_mid_long_black_candle(open_, close).iloc[-1]) and float(volume.iloc[-1]) >= 2 * avg_vol_5d
    is_big_red_today = bool(is_mid_long_red_candle(open_, close).iloc[-1]) and float(volume.iloc[-1]) >= 2 * avg_vol_5d
    has_lower_high = len(tp_heads) >= 2 and tp_heads[-1].price <= tp_heads[-2].price

    # R-CLASSIC-02大量長黑破切反轉圖：頭頭低+大量長黑+今天剛好跌破上升切線(重用R-LINE-11
    # 已經算好的「今天角色變成壓力、昨天還不是」判斷，同一個跌破事件，不重新畫線)。
    close_below_uptrend_line = (
        up_tangent is not None and up_tangent.role == "resistance"
        and (up_tangent_prev is None or up_tangent_prev.role != "resistance")
    )
    classic02_note = big_black_breaks_uptrend_line(has_lower_high, is_big_black_today, close_below_uptrend_line)
    if classic02_note:
        add("R-CLASSIC-02", classic02_note)

    # R-CLASSIC-03大量雙頭反轉圖(M頭)：用最近2個轉折高點當雙頭，中間夾的最低轉折低點當頸線。
    if len(tp_heads) >= 2:
        head1, head2 = tp_heads[-2], tp_heads[-1]
        between_bottoms_03 = [tp for tp in tp_bottoms if head1.index < tp.index < head2.index]
        if between_bottoms_03:
            neckline_03 = min(tp.price for tp in between_bottoms_03)
            classic03_note = double_top_neckline_break(
                head2.price <= head1.price, float(close.iloc[-1]) < neckline_03,
                float(volume.iloc[-1]) >= 2 * avg_vol_5d,
            )
            if classic03_note:
                add("R-CLASSIC-03", f"{classic03_note}（頸線約{neckline_03:.2f}）")

    # R-CLASSIC-05高檔連2日大量被黑K跌破/R-CLASSIC-25低檔連2日大量被突破：書中的「高檔」/
    # 「低檔」情境沒有另外的判定式，這裡不額外加趨勢位置門檻(跟R-SR-15/16同一個簡化原則)，
    # 只往回找最近一次「連續2日大量」，多空各自檢查今天是否剛好跌破/突破這個2日大量區間
    # (「今天」vs「昨天」比較，避免同一次事件連續多天重複回報)。
    big_vol_days = is_big_volume_vs_ma5(volume, basic_volume(volume))
    is_black_series = is_black_candle(open_, close)
    cluster_low_05, cluster_high_05 = None, None
    search_start_05 = max(today_idx - CLASSIC_TWO_DAY_VOLUME_LOOKBACK, 1)
    for j in range(today_idx - 1, search_start_05, -1):
        if bool(big_vol_days.iloc[j]) and bool(big_vol_days.iloc[j - 1]):
            cluster_low_05 = min(float(low.iloc[j]), float(low.iloc[j - 1]))
            cluster_high_05 = max(float(high.iloc[j]), float(high.iloc[j - 1]))
            break

    if cluster_low_05 is not None:
        broke_low_today = float(close.iloc[today_idx]) < cluster_low_05 and bool(is_black_series.iloc[today_idx])
        broke_low_yesterday = float(close.iloc[today_idx - 1]) < cluster_low_05
        if broke_low_today and not broke_low_yesterday:
            classic05_note = break_below_two_day_high_volume_low(
                True, True, cluster_low_05, cluster_low_05, float(close.iloc[today_idx]), True,
            )
            if classic05_note:
                add("R-CLASSIC-05", classic05_note)

        broke_high_today = float(close.iloc[today_idx]) > cluster_high_05 and bool(big_vol_days.iloc[today_idx])
        broke_high_yesterday = float(close.iloc[today_idx - 1]) > cluster_high_05
        if broke_high_today and not broke_high_yesterday:
            classic25_note = break_above_two_day_low_volume_high(
                True, True, cluster_high_05, cluster_high_05, float(close.iloc[today_idx]), True,
            )
            if classic25_note:
                add("R-CLASSIC-25", classic25_note)

    # R-CLASSIC-07高檔跳空黑K回檔反轉圖：跳空黑K(重用today_gap)+跌破月線(月線下彎)+頭頭低+
    # 底底低同時成立；KD死亡交叉(K/D今天剛好黃金/死亡交叉)可加強，非必要條件。
    is_lower_low_07 = len(tp_bottoms) >= 2 and tp_bottoms[-1].price < tp_bottoms[-2].price
    gap_down_big_volume_07 = (
        today_gap is not None and today_gap.type == "down_gap" and float(volume.iloc[-1]) >= 2 * avg_vol_5d
    )
    is_break_ma20_07 = float(close.iloc[-1]) < ma20_now and str(ma20_direction.iloc[-1]) == "下彎"
    kd_bearish_cross_07 = bool(
        kd_df["K"].iloc[-1] < kd_df["D"].iloc[-1] and kd_df["K"].iloc[-2] >= kd_df["D"].iloc[-2]
    )
    classic07_note = gap_down_black_reversal_at_high(
        gap_down_big_volume_07, is_break_ma20_07, has_lower_high, is_lower_low_07, kd_bearish_cross_07,
    )
    if classic07_note:
        add("R-CLASSIC-07", classic07_note)

    # R-CLASSIC-09反彈大量紅K跌破追空圖：空頭中(重用短期日線的頭頭低底底低分類trend_today)
    # 往回找最近一根帶量反彈紅K，今天剛好跌破它的低點才回報。
    if trend_today == TREND_BEAR:
        is_red_series_09 = is_red_candle(open_, close)
        search_start_09 = max(today_idx - CLASSIC_BOUNCE_LOOKBACK, 1)
        for j in range(today_idx - 1, search_start_09, -1):
            if bool(is_red_series_09.iloc[j]) and float(volume.iloc[j]) >= avg_vol_5d:
                bounce_low_09 = float(low.iloc[j])
                broke_today_09 = float(close.iloc[today_idx]) < bounce_low_09
                broke_yesterday_09 = float(close.iloc[today_idx - 1]) < bounce_low_09
                if broke_today_09 and not broke_yesterday_09:
                    classic09_note = chase_short_on_bounce_break(True, True, bounce_low_09, float(close.iloc[today_idx]))
                    if classic09_note:
                        add("R-CLASSIC-09", classic09_note)
                break

    # R-CLASSIC-12缺口之下續空圖：重用R-GAP-19/20已經找到的最近一次缺口(recent_gap)，篩選
    # 向下缺口且尚未真封口，今天收盤跌破盤整區下緣(重用R-CANDLE-04的consolidation_box)。
    if recent_gap is not None and recent_gap.type == "down_gap":
        gap_not_covered_12 = not is_true_fill(
            recent_gap, is_black_today, is_red_today, has_long_shadow_today,
            float(volume.iloc[-1]), avg_volume_20, float(close.iloc[-1]),
        )
        consolidation_low_12 = float(consolidation_box["lower_neckline"].iloc[-1])
        ma20_broken_before_gap_12 = str(ma20_direction.iloc[-1]) == "下彎"
        classic12_note = gap_down_continuation(
            True, gap_not_covered_12, float(close.iloc[-1]) < consolidation_low_12, ma20_broken_before_gap_12,
        )
        if classic12_note:
            add("R-CLASSIC-12", classic12_note)

    # R-CLASSIC-13多轉空破多底大跌圖：往回找最近一個「當時仍在多頭趨勢中」確認的轉折低點
    # (重用daily_bull_trend_state逐日狀態機)，今天剛好跌破才回報。
    bull_state_series_13 = daily_bull_trend_state(high, low, close, n=5)
    last_bull_low_13 = None
    for tp in reversed(tp_bottoms):
        idx_pos = close.index.get_loc(tp.index)
        if idx_pos < len(bull_state_series_13) and bool(bull_state_series_13.iloc[idx_pos]):
            last_bull_low_13 = tp.price
            break
    if last_bull_low_13 is not None:
        broke_today_13 = float(close.iloc[-1]) < last_bull_low_13
        broke_yesterday_13 = float(close.iloc[-2]) < last_bull_low_13
        if broke_today_13 and not broke_yesterday_13:
            classic13_note = bull_to_bear_break_last_low(
                float(close.iloc[-1]), last_bull_low_13, float(volume.iloc[-1]) >= 2 * avg_vol_5d,
            )
            if classic13_note:
                add("R-CLASSIC-13", classic13_note)

    # R-CLASSIC-15跌破下降軌道線大跌圖/R-CLASSIC-33突破上升軌道線大漲圖：重用R-LINE-14/15
    # 已經算好的軌道線(up_channel/down_channel)，多加一個「今天是大量長紅/長黑K」的門檻。
    if down_channel is not None:
        classic15_note = break_below_down_channel(float(close.iloc[-1]), float(down_channel.at(x_today)), is_big_black_today)
        if classic15_note:
            add("R-CLASSIC-15", classic15_note)
    if up_channel is not None:
        classic33_note = breakout_above_up_channel(float(close.iloc[-1]), float(up_channel.at(x_today)), is_big_red_today)
        if classic33_note:
            add("R-CLASSIC-33", classic33_note)

    # R-CLASSIC-27空頭反彈在月線上盤整圖：重用R-CANDLE-04的consolidation_box，檢查「昨天
    # 為止已經確立」的盤整區間裡每一天都站在月線之上，今天剛好帶量突破盤整區上緣。
    prior_is_consolidating_27 = bool(consolidation_box["is_consolidating"].shift(1).iloc[-1])
    if prior_is_consolidating_27:
        group_len_27 = int(consolidation_box["group_len"].shift(1).iloc[-1])
        window_low_27 = low.iloc[-1 - group_len_27:-1]
        window_ma20_27 = ma20.iloc[-1 - group_len_27:-1]
        stayed_above_ma20_27 = len(window_low_27) > 0 and bool((window_low_27 >= window_ma20_27).all())
        is_breakout_27 = bool(consolidation_box["breakout_up"].iloc[-1])
        classic27_note = bear_rebound_consolidate_above_ma20_breakout(stayed_above_ma20_27, is_breakout_27)
        if classic27_note:
            add("R-CLASSIC-27", classic27_note)

    # R-CLASSIC-28雙盤底大量紅K突破圖：跟R-CLASSIC-03(雙頭)鏡射，用最近2個轉折低點當雙底，
    # 中間夾的最高轉折高點當共同壓力線，直接呼叫書中原文指定沿用的雙盤底突破規則(R-SCREEN-13)。
    if len(tp_bottoms) >= 2:
        bottom1, bottom2 = tp_bottoms[-2], tp_bottoms[-1]
        between_heads_28 = [tp for tp in tp_heads if bottom1.index < tp.index < bottom2.index]
        if between_heads_28:
            resistance_28 = max(tp.price for tp in between_heads_28)
            breakout_signal_28 = double_bottom_breakout_signal(
                bottom2.price >= bottom1.price, bool(is_red_candle(open_, close).iloc[-1]),
                float(close.iloc[-1]), resistance_28, float(volume.iloc[-1]), avg_vol_5d,
            )
            classic28_note = double_bottom_platform_breakout(breakout_signal_28)
            if classic28_note:
                add("R-CLASSIC-28", f"{classic28_note}（壓力約{resistance_28:.2f}）")

    # R-CLASSIC-30均線糾結紅K突破圖：書中原文直接沿用「均線糾結向上突破做多SOP」(R-MA-17)，
    # 這裡只做直通，was_converged_yesterday重用R-MA-12已經在用的is_ma_tangled()。
    was_converged_yesterday_30 = is_ma_tangled(ma_frame).shift(1).fillna(False)
    convergence_high_30 = pd.concat(
        [ma_frame["MA5"], ma_frame["MA10"], ma_frame["MA20"]], axis=1,
    ).max(axis=1).shift(1)
    ma_tangle_signal_30 = ma_tangle_breakout_long_entry(
        close, volume, was_converged_yesterday_30, convergence_high_30, is_mid_long_red_candle(open_, close),
    )
    classic30_note = ma_tangle_breakout(bool(ma_tangle_signal_30.iloc[-1]))
    if classic30_note:
        add("R-CLASSIC-30", classic30_note)

    # R-CLASSIC-32島型反轉圖：書中原文直接沿用「低檔島型反轉規則」，只在「今天」剛好出現
    # 向上缺口(重用today_gap)時，往回找中間盤整、再更早的向下缺口，兩缺口不重疊才成立孤島。
    if today_gap is not None and today_gap.type == "up_gap":
        search_start_32 = max(today_idx - 1 - CLASSIC_ISLAND_LOOKBACK, 1)
        for j in range(today_idx - 1, search_start_32 - 1, -1):
            down_gap_32 = detect_gap(
                prev_high=float(high.iloc[j - 1]), prev_low=float(low.iloc[j - 1]),
                curr_high=float(high.iloc[j]), curr_low=float(low.iloc[j]),
            )
            if down_gap_32 is not None and down_gap_32.type == "down_gap":
                consolidation_days_32 = today_idx - j - 1
                island_dict = detect_island_reversal_bottom(down_gap_32, today_gap, consolidation_days_32)
                if island_dict is not None:
                    classic32_note = island_reversal(True)
                    if classic32_note:
                        add("R-CLASSIC-32", classic32_note)
                break

    # --- 綜合操作SOP(R-STRATEGY-01短線波段進場守則第1條) ---
    # 20條守則裡只有第1條(進場)不需要「已持有部位」的profit_pct/停損價，是純粹的新候選
    # 訊號；第2-19條(停損/停利/加碼)都依賴持倉狀態，性質上跟停損停利資金管理同一類，
    # 這裡先不接，等使用者確認顯示位置後再處理。
    entry_ready_01 = short_swing_entry_ready(
        trend_today == TREND_BULL, float(close.iloc[-1]), float(open_.iloc[-1]), float(ma_frame["MA5"].iloc[-1]),
        float(high.iloc[-2]), 1.0 if str(ma20_direction.iloc[-1]) == "上揚" else -1.0,
        float(kd_df["K"].iloc[-1] - kd_df["K"].iloc[-2]),
        float(volume.iloc[-1]), float(ma5_volume.iloc[-1]), float(volume.iloc[-2]),
    )
    if entry_ready_01:
        add("R-STRATEGY-01", "短線波段進場：多頭架構+站上MA5及前高+紅K實體漲幅≥2%+MA20/KD_K向上+量增或量平")

    # --- 綜合操作SOP(R-STRATEGY-07多空完成反轉後續強力走勢口訣) ---
    # 直接複用已經算好的短期(日線)趨勢分類，比較「今天」跟「昨天」(少一天資料重算一次)
    # 是否剛好發生多頭→空頭或空頭→多頭的狀態切換。
    if len(df) > 1:
        trend_yesterday_horizons = classify_trend_states_multi_horizon(
            trend_high.iloc[:-1], trend_low.iloc[:-1], trend_close.iloc[:-1],
        )
        trend_yesterday_07 = trend_yesterday_horizons["短期"][1]
        trend_state_map_07 = {TREND_BULL: "多頭確認", TREND_BEAR: "空頭確認", TREND_RANGE: "盤整"}
        prev_state_07 = trend_state_map_07.get(trend_yesterday_07, "盤整")
        curr_state_07 = trend_state_map_07.get(trend_today, "盤整")
        bull_to_bear_07 = bull_to_bear_reversal_signal(prev_state_07, curr_state_07)
        if bull_to_bear_07:
            add("R-STRATEGY-07", bull_to_bear_07)
        bear_to_bull_07 = bear_to_bull_reversal_signal(prev_state_07, curr_state_07)
        if bear_to_bull_07:
            add("R-STRATEGY-07", bear_to_bull_07)

    # --- 趨勢位置模組(2026-07-26新增)解鎖的規則：is_at_high/is_at_low不再是全True佔位符，
    # 重新檢視先前因「趨勢位置未實作」被排除的規則，把參數需求剛好就是is_at_high/is_at_low
    # (不需要更細的初升/主升/末升等子階段)的部分接上；其餘(is_bull_early_or_main_stage、
    # is_start_of_decline、is_late_stage_rally等更細緻的子階段判斷)本模組尚未提供，維持排除。
    trend_position = compute_trend_position(high, low, close)
    is_at_high, is_at_low = trend_position["is_at_high"], trend_position["is_at_low"]

    # R-TREND-12多頭高檔爆量停利訊號：is_at_bull_high直接對應is_at_high。
    exhaustion_signal_12 = bull_high_volume_exhaustion_signal(volume, open_, close, ma5_volume, is_at_high)
    if bool(exhaustion_signal_12.iloc[-1]):
        add("R-TREND-12", "多頭高檔爆量停利訊號：單日爆量(前一日3倍以上)長黑K，或近3天內至少2天爆量(5日均量2倍以上)且股價不漲，建議停利")

    # R-VOLPRICE-09多頭轉折關鍵大量K線操作對應/R-VOLPRICE-10空頭轉折關鍵大量K線操作對應(鏡射
    # 部分)：兩者都是「昨天在高檔/低檔出現大量K棒，今天評估後續反應」的隔日確認模式，跟
    # R-VOLPRICE-07凹洞量、R-GAP-19/20同一套「日落後確認」慣例。R-VOLPRICE-10另外3個子函式
    # (第1/2/4點，需要is_start_of_decline/is_low_continuous_decline等更細緻的階段判斷)
    # 這裡不接；第5點(量價背離)見下方獨立區塊。
    candle_color_09 = "紅" if close.iloc[-2] > open_.iloc[-2] else "黑"
    pullback_signal_09 = bull_high_key_point_pullback_signal(
        bool(is_at_high.iloc[-2]), candle_color_09, bool(is_big_volume_vs_prev_day(volume).iloc[-2]),
        float(close.iloc[-1]), float(low.iloc[-2]), float(open_.iloc[-1]), float(close.iloc[-2]),
    )
    if pullback_signal_09:
        add("R-VOLPRICE-09", pullback_signal_09)

    rebound_signal_10 = bear_low_key_point_rebound_signal(
        bool(is_at_low.iloc[-2]), bool(is_mid_long_black_candle(open_, close).iloc[-2]),
        float(close.iloc[-1]), float(high.iloc[-2]), float(open_.iloc[-1]), float(close.iloc[-2]),
    )
    if rebound_signal_10:
        add("R-VOLPRICE-10", rebound_signal_10)

    # R-VOLPRICE-10第5點(低檔量價背離)：昨天收盤創新低(近VOLPRICE10_NEW_LOW_LOOKBACK天，
    # 書中無明確天數)且量增，今天收紅且沒有續跌才回報——跟上面的bear_low_key_point_rebound_
    # signal是同一個rule_id底下互相獨立的另一個訊號來源。
    if len(close) > VOLPRICE10_NEW_LOW_LOOKBACK + 1:
        made_new_low_10 = float(close.iloc[-2]) < float(close.iloc[-VOLPRICE10_NEW_LOW_LOOKBACK - 2:-2].min())
        volume_increasing_10 = float(volume.iloc[-2]) > float(ma5_volume.iloc[-2])
        divergence_signal_10 = bear_low_divergence_signal(
            made_new_low_10, volume_increasing_10,
            bool(is_red_candle(open_, close).iloc[-1]), float(close.iloc[-1]) >= float(close.iloc[-2]),
        )
        if divergence_signal_10:
            add("R-VOLPRICE-10", divergence_signal_10)

    # R-VOLPRICE-08大量K線支撐壓力回溯標記通用規則：往回搜尋最近一根大量K棒(VOLPRICE08_
    # LOOKBACK=20天，書中無明確數字)，今天收盤突破其高點或跌破其低點(且昨天還沒突破/跌破)
    # 才回報，跟R-CLASSIC-05/25的「往回搜尋+今天vs昨天」慣例同一個形狀；trend_state直接
    # 用trend_today(已經算好的短期日線多空盤整標籤，剛好就是這條規則要的三態字串)。
    volprice08_note = None
    search_start_08 = max(today_idx - VOLPRICE08_LOOKBACK, 1)
    for j in range(today_idx - 1, search_start_08, -1):
        if bool(big_vol_days.iloc[j]):
            bar_high_08, bar_low_08 = float(high.iloc[j]), float(low.iloc[j])
            broke_out_08 = float(close.iloc[today_idx]) > bar_high_08
            broke_out_yesterday_08 = float(close.iloc[today_idx - 1]) > bar_high_08
            broke_down_08 = float(close.iloc[today_idx]) < bar_low_08
            broke_down_yesterday_08 = float(close.iloc[today_idx - 1]) < bar_low_08
            if (broke_out_08 and not broke_out_yesterday_08) or (broke_down_08 and not broke_down_yesterday_08):
                label_08, price_08 = classify_big_volume_bar(trend_today, bar_high_08, bar_low_08, broke_out_08, broke_down_08)
                if price_08 is not None:
                    volprice08_note = f"{label_08}(價位約{price_08:.2f})"
            break
    if volprice08_note:
        add("R-VOLPRICE-08", volprice08_note)

    # R-VOLPRICE-06空頭下跌大量支撐壓力轉化：往回搜尋最近一根「下跌中的大量長黑K」，今天
    # 突破其高點(多方轉強反彈確認)或跌破其低點(空方換手失敗)才回報，跟R-VOLPRICE-08同一個
    # 「往回搜尋+今天vs昨天」形狀；書中第3點(`bear_decline_retro_accumulation_label`，需要
    # 「反彈後再跌不破前低」3事件序列追蹤)不接，超出這批範圍。
    volprice06_note = None
    search_start_06 = max(today_idx - VOLPRICE06_LOOKBACK, 1)
    for j in range(today_idx - 1, search_start_06, -1):
        if bool(big_vol_days.iloc[j]) and bool(is_mid_long_black_candle(open_, close).iloc[j]):
            bar_high_06, bar_low_06 = float(high.iloc[j]), float(low.iloc[j])
            broke_out_06 = float(close.iloc[today_idx]) > bar_high_06
            broke_out_yesterday_06 = float(close.iloc[today_idx - 1]) > bar_high_06
            broke_down_06 = float(close.iloc[today_idx]) < bar_low_06
            broke_down_yesterday_06 = float(close.iloc[today_idx - 1]) < bar_low_06
            if (broke_out_06 and not broke_out_yesterday_06) or (broke_down_06 and not broke_down_yesterday_06):
                volprice06_note = bear_decline_big_black_role(broke_out_06, broke_down_06)
            break
    if volprice06_note:
        add("R-VOLPRICE-06", volprice06_note)

    # R-VOLPRICE-11壓力支撐區大量K線隔日應對規則：重用R-SR-15/16已經在用的MA20支撐壓力
    # 觸價定義(2%容忍帶)，這裡看的是「昨天」觸價但收盤未突破/跌破，「今天」評估後續反應。
    ma20_yesterday_11 = float(ma20.iloc[-2])
    touched_resistance_yesterday_11 = (
        float(high.iloc[-2]) >= ma20_yesterday_11 * 0.98 and float(close.iloc[-2]) <= ma20_yesterday_11 * 1.02
    )
    touched_support_yesterday_11 = (
        float(low.iloc[-2]) <= ma20_yesterday_11 * 1.02 and float(close.iloc[-2]) >= ma20_yesterday_11 * 0.98
    )
    is_big_volume_yesterday_11 = bool(big_vol_days.iloc[-2])
    if touched_resistance_yesterday_11 and is_big_volume_yesterday_11:
        signal_11a = resistance_zone_big_volume_next_day_response(
            True, bool(is_black_candle(open_, close).iloc[-1]), float(close.iloc[-1]) <= float(close.iloc[-2]),
        )
        if signal_11a:
            add("R-VOLPRICE-11", signal_11a)
    if touched_support_yesterday_11 and is_big_volume_yesterday_11:
        signal_11b = support_zone_big_volume_next_day_response(
            True, bool(is_red_candle(open_, close).iloc[-1]), float(close.iloc[-1]) >= float(close.iloc[-2]),
        )
        if signal_11b:
            add("R-VOLPRICE-11", signal_11b)

    # R-CLASSIC-01高檔大量長黑一日反轉圖：is_top_zone對應is_at_high，重用candle_patterns_2.py
    # 已經在latest_day_summary.py用過的bearish_engulfing_at_high()當「高檔長黑吞噬」判斷，
    # 沿用同一套「昨天大量吞噬K、今天評估是否全部出清」的隔日確認模式。
    engulfing_at_high_series = bearish_engulfing_at_high(open_, low, close, is_at_high)
    classic01_note = one_day_reversal_at_high(
        bool(is_at_high.iloc[-2]), bool(engulfing_at_high_series.iloc[-2]),
        bool(is_big_volume_vs_prev_day(volume).iloc[-2]),
        float(close.iloc[-1]), float(low.iloc[-2]),
    )
    if classic01_note:
        add("R-CLASSIC-01", classic01_note)

    # R-CLASSIC-26低檔大量長下影線圖：is_at_bottom對應is_at_low，是「當天」反轉K棒本身的
    # 幾何+位置+量能條件，不像R-CLASSIC-01/R-VOLPRICE-09/10是隔日確認模式。
    hammer_big_volume_26 = bool(is_hammer_candle(open_, high, low, close).iloc[-1]) and bool(
        is_big_volume_vs_ma5(volume, ma5_volume).iloc[-1]
    )
    classic26_note = low_zone_big_lower_shadow_reversal(
        hammer_big_volume_26, bool(is_at_low.iloc[-1]), float(close.iloc[-1]) > float(ma20.iloc[-1]),
    )
    if classic26_note:
        add("R-CLASSIC-26", classic26_note)

    # R-CLASSIC-16低檔大量長紅K圖：「低檔短時間內反覆出現(>=2次)帶爆大量的長紅K」，用
    # is_at_low往回搭配大量長紅K計數，窗口取CLASSIC16_LOOKBACK(書中無明確數字，工程估計值)。
    big_red_at_low_16 = (
        is_mid_long_red_candle(open_, close) & is_big_volume_vs_ma5(volume, ma5_volume) & is_at_low
    )
    big_red_count_16 = int(big_red_at_low_16.iloc[-CLASSIC16_LOOKBACK:].sum())
    classic16_note = low_zone_big_red_confirmation(big_red_count_16)
    if classic16_note and bool(is_at_low.iloc[-1]):
        add("R-CLASSIC-16", classic16_note)

    # R-CLASSIC-22空轉多過空高大漲圖：往回找「最近一個在空頭趨勢中確認」的轉折底部(重用
    # daily_bear_trend_state逐日狀態機，跟R-CLASSIC-13「往回找確認時仍在該趨勢中的轉折點」
    # 同一個模式)，這個底部之後的走勢視為一段「空頭反彈」：bear_rebound_high是這段反彈期間
    # (不含今天，避免今天自己的高點把自己算進門檻裡)至今的最高價；is_bull_confirm是「這個
    # 底部之後有沒有出現更高的下一個底部」(首次底底高)；今天收盤突破bear_rebound_high、
    # 昨天還沒突破才回報。
    bear_state_series_22 = daily_bear_trend_state(high, low, close, n=5)
    anchor_bottom_22, anchor_list_pos_22 = None, None
    for pos in range(len(tp_bottoms) - 1, -1, -1):
        tp = tp_bottoms[pos]
        idx_pos_22 = close.index.get_loc(tp.index)
        if idx_pos_22 < len(bear_state_series_22) and bool(bear_state_series_22.iloc[idx_pos_22]):
            anchor_bottom_22, anchor_list_pos_22 = tp, pos
            break
    if anchor_bottom_22 is not None:
        anchor_date_pos_22 = close.index.get_loc(anchor_bottom_22.index)
        is_bear_reversal_22 = bool(big_vol_days.iloc[anchor_date_pos_22])
        later_bottoms_22 = tp_bottoms[anchor_list_pos_22 + 1:]
        is_bull_confirm_22 = any(tp.price > anchor_bottom_22.price for tp in later_bottoms_22)
        rebound_window_22 = high.iloc[anchor_date_pos_22:-1]
        bear_rebound_high_22 = float(rebound_window_22.max()) if len(rebound_window_22) > 0 else anchor_bottom_22.price
        broke_today_22 = float(close.iloc[-1]) > bear_rebound_high_22
        broke_yesterday_22 = len(close) > 1 and float(close.iloc[-2]) > bear_rebound_high_22
        if broke_today_22 and not broke_yesterday_22:
            classic22_note = bear_to_bull_break_rebound_high(
                is_bear_reversal_22, is_bull_confirm_22, float(close.iloc[-1]),
                bear_rebound_high_22, bool(big_vol_days.iloc[-1]),
            )
            if classic22_note:
                add("R-CLASSIC-22", classic22_note)

    # --- R-SR-14遇壓遇撐4法則支撐壓力有效性確認 ---
    # sr_price統一用MA20(跟R-SR-15/16/R-VOLPRICE-11同一個簡化原則，書中列出的4種支撐壓力
    # 來源裡最單純的一種，不是重新設計R-SR-01/02/15/16既有wiring，是獨立接一次)。書中的
    # candle_type是"中長紅K"/"長上影線K"/"中長黑K"/"變盤線"4類，書中沒有另外定義這4個字串
    # 怎麼從幾何條件對照，這裡採用最直覺的映射："變盤線"=十字線/墓碑線/長T字線任一成立；
    # 長上影線=上影線>=2倍實體(跟R-CANDLE-25槌子線的2倍門檻同一個工程估計值)且非變盤線。
    # 昨天(index -2)觸及月線的K棒決定候選法則、今天(index -1)是確認日，是day-lag確認模式。
    is_doji_like_14 = is_doji(open_, close) | is_gravestone_line(open_, high, low, close) | is_long_t_line(open_, high, low, close)
    upper_shadow_14, _lower_shadow_14 = candle_shadows(open_, high, low, close)
    body_14 = (close - open_).abs()
    is_long_upper_shadow_14 = (upper_shadow_14 >= 2 * body_14) & ~is_doji_like_14
    is_mid_long_red_14 = is_mid_long_red_candle(open_, close)
    is_mid_long_black_14 = is_mid_long_black_candle(open_, close)

    def _sr14_candle_type(i: int) -> str:
        if bool(is_doji_like_14.iloc[i]):
            return "變盤線"
        if bool(is_mid_long_red_14.iloc[i]):
            return "中長紅K"
        if bool(is_mid_long_black_14.iloc[i]):
            return "中長黑K"
        if bool(is_long_upper_shadow_14.iloc[i]):
            return "長上影線K"
        return ""

    candle_type_yesterday_14 = _sr14_candle_type(-2)
    if candle_type_yesterday_14:
        ma20_yesterday_14 = float(ma20.iloc[-2])
        candle_type_today_14 = _sr14_candle_type(-1)
        is_red_today_14 = bool(is_red_candle(open_, close).iloc[-1])
        is_big_volume_today_14 = bool(big_vol_days.iloc[-1])
        skip_labels_14 = ("尚未觸及有效判斷條件", "尚未確認，持續觀察")
        resistance_result_14 = confirm_resistance(
            candle_type_yesterday_14, float(close.iloc[-2]), ma20_yesterday_14,
            candle_type_today_14, is_red_today_14, is_big_volume_today_14,
        )
        if resistance_result_14 not in skip_labels_14:
            add("R-SR-14", f"遇壓：{resistance_result_14}")
        support_result_14 = confirm_support(
            candle_type_yesterday_14, float(close.iloc[-2]), ma20_yesterday_14,
            candle_type_today_14, is_red_today_14, is_big_volume_today_14,
        )
        if support_result_14 not in skip_labels_14:
            add("R-SR-14", f"遇撐：{support_result_14}")

    # --- R-SR-17支撐壓力測試結果回推多空趨勢強弱 ---
    # 「回檔期間」＝從最近一個轉折頭部到現在(前提是這個頭部確實是目前最新的轉折點，不是
    # 更早、已經被後續底部取代的舊頭部)；「反彈期間」則鏡射，從最近一個轉折底部到現在。
    # tested_prior_high/low的容忍帶2%，跟本檔案其他觸價判斷(R-SR-15/16、R-VOLPRICE-11)
    # 同一個工程估計值。書中另外2個子函式(bull/bear_trend_change_escape_wave)要在「趨勢
    # 已經改變」之後再找「有沒有出現過反彈」，是在這個狀態機之上再疊一層錨點搜尋，這批不接。
    #
    # ⚠️ 這裡刻意加了「今天vs昨天」比較才回報(跟R-LINE-11/12/14/15、R-STRATEGY-07同一個
    # 慣例)：一開始沒加這個比較，對本機真實db驗證時發現R-SR-17在600檔快照裡高達579檔
    # 觸發——追查發現`tested_prior_high`(`.any()`掃整段回檔期間至今)一旦在某一天測試過
    # 前高，之後每天都會持續回傳True，導致「多頭進入盤整」這個結果一旦觸發就會每天重複
    # 回報，佔了全部觸發次數的74%，不是真正「今天發生了什麼變化」的事件。改成額外算一份
    # 「昨天」的版本(少一天資料)，只在今天算出的結果跟昨天不同時才回報，才是「狀態剛好
    # 在今天改變」的真正意義。
    ma10_series_17 = ma_frame["MA10"]

    def _sr17_bull_strength_at(t: int) -> str | None:
        if not (tp_heads and tp_bottoms and tp_heads[-1].index > tp_bottoms[-1].index):
            return None
        prior_high = tp_heads[-1].price
        prior_low = tp_bottoms[-1].price
        anchor_pos = close.index.get_loc(tp_heads[-1].index)
        if anchor_pos > t:
            return None
        stayed_above_ma10 = bool((close.iloc[anchor_pos:t + 1] > ma10_series_17.iloc[anchor_pos:t + 1]).all())
        tested_prior_high = bool((high.iloc[anchor_pos:t + 1] >= prior_high * 0.98).any())
        return bull_trend_strength(
            float(close.iloc[t]), float(ma20.iloc[t]), str(ma20_direction.iloc[t]),
            prior_low, prior_high, stayed_above_ma10, tested_prior_high,
        )

    def _sr17_bear_strength_at(t: int) -> str | None:
        if not (tp_heads and tp_bottoms and tp_bottoms[-1].index > tp_heads[-1].index):
            return None
        prior_high = tp_heads[-1].price
        prior_low = tp_bottoms[-1].price
        anchor_pos = close.index.get_loc(tp_bottoms[-1].index)
        if anchor_pos > t:
            return None
        stayed_below_ma10 = bool((close.iloc[anchor_pos:t + 1] < ma10_series_17.iloc[anchor_pos:t + 1]).all())
        tested_prior_low = bool((low.iloc[anchor_pos:t + 1] <= prior_low * 1.02).any())
        return bear_trend_strength(
            float(close.iloc[t]), float(ma20.iloc[t]), str(ma20_direction.iloc[t]),
            prior_low, prior_high, stayed_below_ma10, tested_prior_low,
        )

    today_pos_17 = len(close) - 1
    bull_strength_17 = _sr17_bull_strength_at(today_pos_17)
    if bull_strength_17 is not None and bull_strength_17 != "趨勢持續，無明確變化訊號":
        bull_strength_yesterday_17 = _sr17_bull_strength_at(today_pos_17 - 1) if today_pos_17 > 0 else None
        if bull_strength_17 != bull_strength_yesterday_17:
            add("R-SR-17", f"多頭：{bull_strength_17}")
    bear_strength_17 = _sr17_bear_strength_at(today_pos_17)
    if bear_strength_17 is not None and bear_strength_17 != "趨勢持續，無明確變化訊號":
        bear_strength_yesterday_17 = _sr17_bear_strength_at(today_pos_17 - 1) if today_pos_17 > 0 else None
        if bear_strength_17 != bear_strength_yesterday_17:
            add("R-SR-17", f"空頭：{bear_strength_17}")

    # --- R-CANDLE-23大量長紅K進場位置篩選規則 ---
    # 10個avoid/buy布林參數裡9個都能用這批已經建立的「錨點+滾動視窗」手法(跟R-CLASSIC-13/
    # 22/R-SR-17同一套)算出來，只有pattern_confirmed_breakout(書中沒有指定是哪一種型態，
    # 33種經典型態圖裡任何一種突破都可能算，猜一個等於發明書中沒有的規則)固定給False——
    # 這是buy_ok清單裡用or連接的其中一條，給False只會少算一種本來也能買的情境，不會像
    # avoid_buy清單漏算那樣有製造假「可以買」訊號的風險，安全。
    is_big_red_23 = bool(
        float(close.iloc[-1]) > float(open_.iloc[-1])
        and (float(close.iloc[-1]) - float(open_.iloc[-1])) / float(open_.iloc[-1]) > CANDLE23_LONG_BODY_PCT
        and bool(is_big_volume_vs_ma5(volume, ma5_volume).iloc[-1])
    )
    if is_big_red_23:
        ma_triple_bullish_23 = bool(is_bullish_aligned(ma_frame).iloc[-1])
        consolidation_breakout_23 = bool(consolidation_box["breakout_up"].iloc[-1])
        below_ma20_23 = float(close.iloc[-1]) < float(ma20.iloc[-1])
        # 空轉多第1次過前高：重用R-CLASSIC-22已經算好的「空頭趨勢中確認的底部當錨點+今天
        # 突破反彈高點」判斷，兩條規則描述的本質是同一個「空頭止穩反轉+突破前波高點」情境。
        bear_to_bull_first_break_23 = anchor_bottom_22 is not None and broke_today_22

        # 高檔連續上漲>=3天：重用is_at_high往回數連續上漲天數。
        is_up_day_23 = (close > close.shift(1)).fillna(False)
        consecutive_up_streak_23 = 0
        for k in range(len(close) - 1, -1, -1):
            if bool(is_up_day_23.iloc[k]):
                consecutive_up_streak_23 += 1
            else:
                break
        consecutive_up_days_ge_3_at_high_23 = consecutive_up_streak_23 >= 3 and bool(is_at_high.iloc[-1])

        # 上漲前貼近壓力：重用R-VOLPRICE-11已經算好的「昨天觸及月線壓力」判斷。
        near_resistance_before_rise_23 = touched_resistance_yesterday_11

        # 空頭反彈中：短期日線趨勢仍是空頭，但今天貼近波段高點。
        is_bear_rebound_23 = trend_today == TREND_BEAR and bool(is_at_high.iloc[-1])

        # 回檔止跌回升／回檔跌破前低：兩者互為鏡射，共用同一段「回檔期間」視窗(從最近一個
        # 轉折頭部到現在，前提是這個頭部確實比最近一個轉折底部更新)。
        bull_pullback_reversal_23 = False
        broke_prior_low_in_pullback_23 = False
        if tp_heads and tp_bottoms and tp_heads[-1].index > tp_bottoms[-1].index:
            pullback_pos_23 = close.index.get_loc(tp_heads[-1].index)
            pullback_holds_prior_low_23 = bool((low.iloc[pullback_pos_23:] >= tp_bottoms[-1].price).all())
            broke_prior_low_in_pullback_23 = not pullback_holds_prior_low_23
            bull_pullback_reversal_23 = bull_pullback_buy_signal(
                trend_today == TREND_BULL, pullback_holds_prior_low_23,
                float(open_.iloc[-1]), float(close.iloc[-1]), float(ma_frame["MA5"].iloc[-1]), float(high.iloc[-2]),
                float(volume.iloc[-1]), float(volume.iloc[-2]),
            )

        candle23_note = big_red_candle_entry_filter(
            is_big_red_23,
            bear_to_bull_first_break_23, ma_triple_bullish_23, bull_pullback_reversal_23,
            consolidation_breakout_23, False,
            consecutive_up_days_ge_3_at_high_23, near_resistance_before_rise_23, is_bear_rebound_23,
            below_ma20_23, broke_prior_low_in_pullback_23,
        )
        if candle23_note not in ("非大量長紅K，不適用本規則", "不在明列的可買清單內，保守觀望"):
            add("R-CANDLE-23", candle23_note)

    return results
