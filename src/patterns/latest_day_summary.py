"""最新交易日K棒/量價綜合分析（Layer 4 應用層）：串接已實作的K棒型態
(indicators/candles.py、candle_patterns_2.py、candle_patterns_3.py)與量價
(indicators/volume_price.py)規則，對資料的「最新一天」給出白話摘要，供儀表板個股圖表
下方顯示用。不重新實作任何底層判斷邏輯，只做組裝與整理。

⚠️ 範圍說明：candle_patterns_2.py 開頭已註明，「位於高檔/低檔」需要外部的趨勢位置模組
（尚未實作，指的是is_at_high/is_at_low這種「趨勢的哪個階段」的細緻判斷），呼叫端可先傳
全True的Series只看幾何型態——這裡沿用同樣做法。也就是說，這裡判斷的是「型態的幾何條件是否
成立」，不是「確認真的發生在高檔或低檔」，摘要文字會清楚標註這一點，避免看起來像是完整的
高低檔位置判斷。

也刻意只挑選書中最常被提及、且不需要「趨勢位置」就能單純從OHLCV算出的規則子集，不是246條
規則的全自動化。趨勢方向(多頭/空頭/盤整，跟「趨勢位置」是不同層次的判斷)2026-07-24已經
接上`src.patterns.trend_state.classify_trend_states_multi_horizon()`(串接R-TREND-01
轉折點取點+R-TREND-03/04頭頭高底底高/頭頭低底底低判定)，摘要的`trend`欄位就是這裡算出
來的——⚠️ 依使用者要求分成短(MA5)/中(MA10)/長(MA20)三種天期分別判斷，不是單一數字，因為
用單一天期代表「大趨勢」太草率(例如短線走空、長線仍是多頭這種常見情境，只看一種天期會
誤導)。
"""

from __future__ import annotations

import pandas as pd

from src.patterns.trend_state import classify_trend_states_multi_horizon

from src.indicators.candle_patterns_2 import (
    basic_reversal_at_high,
    basic_reversal_at_low,
    bearish_engulfing_at_high,
    bearish_harami_at_high,
    bearish_piercing_at_high,
    bullish_cover_low,
    bullish_engulfing_at_low,
    bullish_harami_at_low,
    bullish_piercing_at_low,
    dark_cloud_cover,
)
from src.indicators.candle_patterns_3 import (
    falling_three_black_candles,
    falling_three_methods,
    one_star_two_yang,
    one_star_two_yin,
    rising_three_methods,
    rising_three_red_candles,
)
from src.indicators.candles import (
    classify_black_candle_size,
    classify_red_candle_size,
    evening_star_pattern,
    is_doji,
    is_gravestone_line,
    is_hammer_candle,
    is_inverted_hammer_candle,
    is_long_t_line,
    is_mid_long_black_candle,
    is_mid_long_red_candle,
    is_small_black_candle,
    is_small_red_candle,
    is_spindle_candle,
    morning_star_pattern,
)
from src.indicators.volume_price import (
    basic_volume,
    bull_price_up_volume_shrink_divergence,
    is_attack_volume,
    is_big_volume_vs_ma5,
    is_big_volume_vs_prev_day,
    is_suffocation_volume,
)

# 位置條件(是否位於高檔/低檔)的趨勢位置模組尚未實作，比照 candle_patterns_2.py
# 開頭註明的做法：呼叫端傳全True的Series，只判斷型態本身的幾何條件。
_MIN_ROWS_FOR_MULTI_CANDLE = 3


def classify_latest_candle_name(df: pd.DataFrame) -> str:
    """對最新一天(df最後一列)判斷單根K棒名稱。十字線變體優先判斷(較特定)，
    再槌子/倒槌，再紡錘，最後才退回大小紅黑K實體分類(較籠統)。"""
    open_, high, low, close = df["open"], df["high"], df["low"], df["close"]

    if bool(is_gravestone_line(open_, high, low, close).iloc[-1]):
        return "墓碑線"
    if bool(is_long_t_line(open_, high, low, close).iloc[-1]):
        return "長T字線"
    if bool(is_doji(open_, close).iloc[-1]):
        return "十字線"
    if bool(is_hammer_candle(open_, high, low, close).iloc[-1]):
        return "槌子線"
    if bool(is_inverted_hammer_candle(open_, high, low, close).iloc[-1]):
        return "倒槌子線"
    if bool(is_spindle_candle(open_, high, low, close).iloc[-1]):
        return "紡錘線"

    red_size = classify_red_candle_size(open_, close).iloc[-1]
    if pd.notna(red_size):
        return str(red_size)
    black_size = classify_black_candle_size(open_, close).iloc[-1]
    if pd.notna(black_size):
        return str(black_size)
    return "平盤（開盤收盤同價）"


def detect_latest_day_candle_patterns(df: pd.DataFrame) -> list[str]:
    """回傳最新一天觸發的多根K線組合型態名稱清單(可能多個或空)。"""
    if len(df) < 2:
        return []

    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]
    all_true = pd.Series(True, index=close.index)
    hits: list[str] = []

    two_candle_checks = {
        "基本反轉（高檔）": basic_reversal_at_high(open_, close, all_true),
        "基本反轉（低檔）": basic_reversal_at_low(open_, close, all_true),
        "烏雲罩頂": dark_cloud_cover(open_, high, close, all_true),
        "貫穿線（低檔）": bullish_cover_low(open_, low, close, all_true),
        "空頭母子懷抱": bearish_harami_at_high(open_, high, low, close, all_true),
        "多頭母子懷抱（光明在望）": bullish_harami_at_low(open_, high, low, close, all_true),
        "空頭長黑吞噬": bearish_engulfing_at_high(open_, low, close, all_true),
        "多頭長紅吞噬": bullish_engulfing_at_low(open_, high, close, all_true),
        "空頭刺透": bearish_piercing_at_high(open_, low, close, all_true),
        "多頭刺透": bullish_piercing_at_low(open_, high, close, all_true),
    }
    for name, series in two_candle_checks.items():
        if bool(series.iloc[-1]):
            hits.append(name)

    ma5_volume = basic_volume(volume, n=5)
    three_candle_checks = {
        "上升三法": rising_three_methods(open_, high, low, close),
        "下降三法": falling_three_methods(open_, high, low, close),
        "上漲連3紅": rising_three_red_candles(open_, close, volume, ma5_volume),
        "下跌連3黑": falling_three_black_candles(open_, close),
        "一星二陽": one_star_two_yang(open_, high, low, close),
        "一星二陰": one_star_two_yin(open_, high, low, close),
    }
    for name, series in three_candle_checks.items():
        if bool(series.iloc[-1]):
            hits.append(name)

    if len(df) >= _MIN_ROWS_FOR_MULTI_CANDLE:
        star_shape = (
            is_doji(open_, close) | is_spindle_candle(open_, high, low, close)
            | is_small_red_candle(open_, close) | is_small_black_candle(open_, close)
        )
        mid_long_red = is_mid_long_red_candle(open_, close)
        mid_long_black = is_mid_long_black_candle(open_, close)

        if evening_star_pattern(
            is_mid_long_red_left=bool(mid_long_red.iloc[-3]),
            all_middle_are_reversal_candles=bool(star_shape.iloc[-2]),
            is_mid_long_black_right=bool(mid_long_black.iloc[-1]),
            is_at_high=True,
        ):
            hits.append("夜星")

        if morning_star_pattern(
            is_mid_long_black_left=bool(mid_long_black.iloc[-3]),
            all_middle_are_reversal_candles=bool(star_shape.iloc[-2]),
            is_mid_long_red_right=bool(mid_long_red.iloc[-1]),
            is_at_low=True,
        ):
            hits.append("晨星")

    return hits


def detect_latest_day_volume_signals(df: pd.DataFrame) -> list[str]:
    """回傳最新一天觸發的量價訊號名稱清單(可能多個或空)：攻擊量、爆量、量縮創高背離、窒息量。"""
    if len(df) < 2:
        return []

    open_, close, volume = df["open"], df["close"], df["volume"]
    ma5_volume = basic_volume(volume, n=5)
    hits: list[str] = []

    if bool(is_attack_volume(volume, ma5_volume, close).iloc[-1]):
        hits.append("攻擊量（達前5日均量1.2~1.3倍以上）")
    if bool(is_big_volume_vs_prev_day(volume).iloc[-1]):
        hits.append("爆量（達前一日量2倍以上）")

    rolling_high_20 = close.rolling(20, min_periods=20).max()
    price_new_high = bool((close.iloc[-1] >= rolling_high_20.iloc[-1])) if pd.notna(rolling_high_20.iloc[-1]) else False
    volume_shrink = bool(volume.iloc[-1] < ma5_volume.iloc[-1]) if pd.notna(ma5_volume.iloc[-1]) else False
    if price_new_high and bull_price_up_volume_shrink_divergence(price_new_high, volume_shrink):
        hits.append("量縮創高背離（價創新高但量能萎縮，留意假突破）")

    if len(df) >= 2:
        is_big_black_prev = bool(is_mid_long_black_candle(open_, close).iloc[-2]) and bool(
            is_big_volume_vs_ma5(volume, ma5_volume).iloc[-2]
        )
        next_close_down = bool(close.iloc[-1] < close.iloc[-2])
        if is_suffocation_volume(
            is_big_black_candle=is_big_black_prev, next_close_down=next_close_down,
            next_volume=float(volume.iloc[-1]), big_black_volume=float(volume.iloc[-2]),
        ):
            hits.append("窒息量（前一日大量長黑後，今日量縮續跌，留意止跌訊號）")

    return hits


def summarize_latest_day(df: pd.DataFrame) -> dict:
    """整理成儀表板要顯示的摘要dict：candle_name(單根K棒名稱)、patterns(型態清單)、
    volume_signals(量價訊號清單)、trend(今天短/中/長三種天期各自的多頭/空頭/盤整趨勢
    狀態，見trend_state.classify_trend_states_multi_horizon()——不是單一數字，短/中/長
    可能不一致，例如短線走空但長線仍是多頭)。df為空時回傳全空結果，不拋例外。
    """
    if df.empty:
        return {"candle_name": None, "patterns": [], "volume_signals": [], "trend": None}
    return {
        "candle_name": classify_latest_candle_name(df),
        "patterns": detect_latest_day_candle_patterns(df),
        "volume_signals": detect_latest_day_volume_signals(df),
        "trend": classify_trend_states_multi_horizon(df["high"], df["low"], df["close"]),
    }
