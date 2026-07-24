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
from src.indicators.candles import is_hammer_candle, is_inverted_hammer_candle, is_reversal_candle_at_high, is_reversal_candle_at_low
from src.indicators.crossovers import interpret_cross, is_death_cross, is_golden_cross
from src.indicators.kd import compute_kd, is_high_dull, is_low_dull, kd_cross_signal_by_trend
from src.indicators.macd import compute_macd, macd_zero_axis_bear_signal, macd_zero_axis_bull_signal
from src.indicators.moving_average import compute_ma_set, is_bearish_aligned, is_bullish_aligned, is_ma_converged, is_ma_tangled, sma
from src.indicators.rsi import rsi, rsi_overbought_oversold_signal, rsi_short_long_cross_signal
from src.indicators.volume_price import basic_volume, is_accumulation_volume
from src.patterns.trend_state import TREND_BEAR, TREND_BULL, classify_trend_states_multi_horizon

MIN_DAYS = 30  # 均線/MACD/KD/RSI/布林通道都需要暖身天數，資料不足就整批不評估(不逐一判斷各自門檻)


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
    for label, (timeframe, trend) in trend_horizons.items():
        if trend == TREND_BULL:
            add("R-TREND-03", f"{label}({timeframe}轉折波)：頭頭高且底底高，多頭趨勢成立")
        elif trend == TREND_BEAR:
            add("R-TREND-04", f"{label}({timeframe}轉折波)：頭頭低且底底低，空頭趨勢成立")

    # 下面幾條依賴trend的規則(R-MA-15/KD依趨勢判讀/布林通道訊號①②)書中沒有另外要求區分
    # 短中長天期，沿用短線(日線)天期即可，跟本專案其他規則(R-TREND-14等)慣用的短線框架一致；
    # trend_series用「今天」單一分類值填滿整個index，這幾個函式都只會讀.iloc[-1]
    # (見_last_bool/_last_text)，不需要逐日皆準確的趨勢序列。
    trend_today = trend_horizons["短線"][1]

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

    return results
