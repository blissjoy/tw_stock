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
from src.indicators.candles import candle_shadows, is_hammer_candle, is_inverted_hammer_candle, is_reversal_candle_at_high, is_reversal_candle_at_low, prev_bar_support_resistance_signal
from src.indicators.consolidation import detect_consolidation_breakout
from src.indicators.crossovers import interpret_cross, is_death_cross, is_golden_cross
from src.indicators.gaps import detect_gap, false_fill_reasons, is_true_fill
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
    bearish_resistance_short_signal,
    bullish_support_buy_signal,
    classify_bottom_role,
    classify_head_role,
    is_bearish_reversal_candle,
    is_bullish_reversal_candle,
    ma_resistance_conversion_short,
    ma_support_conversion_long,
)
from src.indicators.trend import bear_trend_change_warning, bull_trend_change_warning, daily_bear_trend_state, daily_bull_trend_state
from src.indicators.trendlines import check_channel_breakdown, check_channel_breakout
from src.indicators.volume_price import basic_volume, is_accumulation_volume, is_big_volume_vs_prev_day
from src.patterns.chart_overlays import compute_trendlines
from src.patterns.trend_state import TREND_BEAR, TREND_BULL, classify_trend_states_multi_horizon

MIN_DAYS = 30  # 均線/MACD/KD/RSI/布林通道都需要暖身天數，資料不足就整批不評估(不逐一判斷各自門檻)
GAP_FILL_LOOKBACK = 20  # R-GAP-19/20往回搜尋「最近一次缺口」的天數上限，避免抓到太久以前已經沒有參考意義的舊缺口
CANDLE04_CONSOLIDATION_MIN_BARS = 20  # R-CANDLE-04盤整天數門檻，書中無明確數字，跟R-GAP-09/14同一個工程估計值


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
    for label, (timeframe, trend, reason) in trend_horizons.items():
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

    return results
