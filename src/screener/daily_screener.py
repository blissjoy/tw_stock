"""每日選股（Layer 4 應用層）：對每檔股票用「今天」的最新資料，判斷已接上的規則的進場
條件是否成立。

⚠️ 2026-07-23前只接了R-TREND-14（多頭短線選股與停損停利SOP，信心92/100，已用真實資料
回測驗證勝率33.5%），刻意先從這一條已被回測證實的規則起步。之後追加R-SCREEN-11（底部
狹幅盤整大量紅K突破鎖股，信心89/100）、R-SCREEN-15（緩漲上升軌道線突破大量長紅K做多，
信心88/100）、R-CLASSIC-24（突破大量黑K買進，信心87/100）——246條規則庫其實已經100%
都有程式實作(`scripts/check_rule_coverage.py`可查)，差別只在於這裡有沒有把它「接進每日
自動選股」這一層；這幾條都是清楚的做多進場訊號、只需要OHLCV資料(不像R-SCREEN-05需要
股本/營收/三大法人等本專案還沒抓取的基本面資料)，且各自能重用既有的building block
(`src/indicators/consolidation.py`的橫盤突破偵測、`src/patterns/chart_overlays.py`的
上升軌道線、`src/indicators/moving_average.py`的均線多頭排列、`src/indicators/
volume_price.py`的大量判斷)，不需要另外新寫底層演算法。依使用者指示，這次先接上觀察
實際選股表現，不像R-TREND-14那樣要求先個別回測驗證勝率。

⚠️ 2026-07-23追加R-GAP-09（打底完成向上突破缺口，信心90/100）時，一開始誤判這條規則
「需要缺口隔天的成交量確認、跟只評估今天的做法衝突」而排除掉——這個判斷是錯的：實際已
實作的`src.indicators.gaps.detect_breakaway_gap_up()`裡，「3天內是否回補」只是事後才能
加註的warning欄位，從來不是回傳訊號與否的必要條件(gap_filled_within_3_days=False時一樣
正常回傳訊號)，所以評估「缺口發生當天」完全不需要用到未來資料，跟其他4條規則的模式
一致，只是最初排除得太草率，之後補上了。

之後要加其他規則的每日篩選時，比照這裡的模式各自寫一個獨立的 screen_* 函式（輸入df，
輸出候選dict或None），再由 screen_all_stocks 或 daily_pipeline.py 呼叫端合併多個screen
函式的結果即可，不需要重寫這一層。
"""

from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd

from src.data import storage
from src.indicators.candles import is_mid_long_red_candle
from src.indicators.consolidation import detect_consolidation, detect_consolidation_breakout
from src.indicators.gaps import detect_breakaway_gap_down, detect_breakaway_gap_up, detect_gap
from src.indicators.moving_average import compute_ma_set, is_bullish_aligned, ma_strategy_stop_loss_long, ma_strategy_stop_loss_short, sma
from src.indicators.trend import (
    bear_short_term_entry_ready,
    bear_short_term_stop_loss,
    bull_short_term_entry_ready,
    bull_short_term_stop_loss,
    daily_bear_trend_state,
    daily_bull_trend_state,
)
from src.indicators.volume_price import is_big_volume_vs_prev_day
from src.patterns import chart_overlays
from src.screener.indicator_precompute import LIVE_UPDATE_LOOKBACK_DAYS, compute_indicator_rows
from src.screener.screening_rules import narrow_range_bottom_breakout, slow_rally_channel_breakout
from src.strategies.candle_mechanical import mechanical_long_trading_rule, mechanical_short_trading_rule
from src.strategies.ma_strategies import (
    dual_ma_long_term_long_strategy,
    dual_ma_long_term_short_strategy,
    single_ma_mid_term_long_strategy,
    single_ma_mid_term_short_strategy,
    single_ma_short_term_long_strategy,
    single_ma_short_term_short_strategy,
)

# R-GAP-09判斷「打底完成」的盤整天數門檻：書中這條規則本身沒有給出明確的天數(只引用
# 「盤整區上下頸線支撐壓力規則」等其他章節)，這裡用比R-SCREEN-11(2個月/42天)略短的
# 20個交易日(約1個月)當工程估計值，不是書中明文數字。
GAP_CONSOLIDATION_MIN_BARS = 20

# R-CLASSIC-24往回搜尋「多頭排列期間的大量黑K」的天數上限，避免抓到太久以前、
# 已經沒有參考意義的舊黑K高點當作watch_high。
BIG_BLACK_BREAKOUT_LOOKBACK = 20

# 約2個月交易日，比照R-SCREEN-11「盤整須達2個月以上」的門檻換算(21個交易日/月概估)
CONSOLIDATION_MIN_BARS = 42


def screen_bull_short_term_entry(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料(依date遞增排序、index為date)判斷「今天」(最後一列)是否觸發
    R-TREND-14多頭短線進場訊號。資料不足min_days天則回傳None(不足以計算MA20等指標)。
    """
    if len(df) < min_days:
        return None

    close, high, low, open_, volume = df["close"], df["high"], df["low"], df["open"], df["volume"]
    ma10 = sma(close, 10)
    ma20 = sma(close, 20)
    ma10_slope = ma10.diff()
    ma20_slope = ma20.diff()
    volume_prev = volume.shift(1)
    bull_trend = daily_bull_trend_state(high, low, close, n=5)

    t = len(close) - 1
    if pd.isna(ma20_slope.iloc[t]) or pd.isna(volume_prev.iloc[t]) or pd.isna(ma10.iloc[t]):
        return None

    ready = bull_short_term_entry_ready(
        is_bull_trend=bool(bull_trend.iloc[t]),
        ma10=ma10.iloc[t], ma20=ma20.iloc[t],
        ma10_slope=ma10_slope.iloc[t], ma20_slope=ma20_slope.iloc[t],
        close_t=close.iloc[t], open_t=open_.iloc[t],
        volume_t=volume.iloc[t], volume_prev=volume_prev.iloc[t],
    )
    if not ready:
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bull_short_term_stop_loss(entry_bar_low=float(low.iloc[t]))
    return {
        "signal_name": "R-TREND-14多頭短線進場（92%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": "多頭架構＋MA10/MA20多排向上＋攻擊量(前日1.3倍以上)＋紅K實體漲幅>2%",
    }


def screen_bear_short_term_entry(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-TREND-15空頭短線選股訊號——
    R-TREND-14多頭短線進場的鏡射對稱版本，用`daily_bear_trend_state()`(見trend.py)。
    """
    if len(df) < min_days:
        return None

    close, high, low, open_, volume = df["close"], df["high"], df["low"], df["open"], df["volume"]
    ma5 = sma(close, 5)
    ma10 = sma(close, 10)
    ma20 = sma(close, 20)
    ma10_slope = ma10.diff()
    ma20_slope = ma20.diff()
    volume_prev = volume.shift(1)
    low_prev = low.shift(1)
    bear_trend = daily_bear_trend_state(high, low, close, n=5)

    t = len(close) - 1
    if pd.isna(ma20_slope.iloc[t]) or pd.isna(volume_prev.iloc[t]) or pd.isna(ma5.iloc[t]) or pd.isna(low_prev.iloc[t]):
        return None

    ready = bear_short_term_entry_ready(
        is_bear_trend=bool(bear_trend.iloc[t]),
        ma10=ma10.iloc[t], ma20=ma20.iloc[t],
        ma10_slope=ma10_slope.iloc[t], ma20_slope=ma20_slope.iloc[t],
        close_t=close.iloc[t], open_t=open_.iloc[t],
        volume_t=volume.iloc[t], volume_prev=volume_prev.iloc[t],
        ma5_t=ma5.iloc[t], low_prev=low_prev.iloc[t],
    )
    if not ready:
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bear_short_term_stop_loss(entry_bar_high=float(high.iloc[t]))
    return {
        "signal_name": "R-TREND-15空頭短線進場（92%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": "空頭架構＋MA10/MA20空排向下＋攻擊量(前日1.3倍以上)＋黑K實體跌幅>2%＋跌破MA5與前一日低點",
    }


def _single_ma_strategy_screen(
    df: pd.DataFrame, min_days: int, strategy_fn, is_long: bool, hold_ma_label: str, signal_name: str,
) -> dict | None:
    """R-MA-22/23/24/25(單一均線短/中線做多做空戰法)共用的wiring邏輯：4個戰法共用同一套
    骨架(見src/strategies/ma_strategies.py開頭說明)，差別只在傳入的策略函式、多空方向、
    停損守哪一條均線(短線守MA5/中線守MA10)——抽成共用函式避免4份幾乎相同的程式碼。
    """
    if len(df) < min_days:
        return None
    close, high, low, open_ = df["close"], df["high"], df["low"], df["open"]
    ma5, ma10, ma20 = sma(close, 5), sma(close, 10), sma(close, 20)
    t = len(close) - 1
    if pd.isna(ma20.iloc[t]) or pd.isna(ma10.iloc[t]):
        return None

    trend = daily_bull_trend_state(high, low, close, n=5) if is_long else daily_bear_trend_state(high, low, close, n=5)
    if is_long:
        result = strategy_fn(close, high, ma5, ma10, ma20, trend) if hold_ma_label == "MA10" else strategy_fn(close, high, ma5, ma20, trend)
    else:
        result = strategy_fn(close, low, ma5, ma10, ma20, trend) if hold_ma_label == "MA10" else strategy_fn(close, low, ma5, ma20, trend)

    if not bool(result["entry_signal"].iloc[t]):
        return None

    entry_price = float(close.iloc[t])
    if is_long:
        stop_loss = ma_strategy_stop_loss_long(
            entry_open=float(open_.iloc[t]), entry_close=float(close.iloc[t]),
            entry_low=float(low.iloc[t]), swing_low_after_entry=None,
        )
    else:
        stop_loss = ma_strategy_stop_loss_short(
            entry_open=float(open_.iloc[t]), entry_close=float(close.iloc[t]),
            entry_high=float(high.iloc[t]), swing_high_after_entry=None,
        )
    direction = "多頭回後買上漲(收盤突破MA5且突破前一日高點)" if is_long else "空頭彈後空下跌(收盤跌破MA5且跌破前一日低點)"
    return {
        "signal_name": signal_name,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": f"主趨勢確認+站{'上' if is_long else '下'}MA20，{direction}，持股依據守{hold_ma_label}",
    }


def screen_single_ma_short_term_long(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """R-MA-22單一均線短線做多戰法：進出場皆守MA5。"""
    return _single_ma_strategy_screen(
        df, min_days, single_ma_short_term_long_strategy, is_long=True, hold_ma_label="MA5",
        signal_name="R-MA-22單一均線短線做多（88%）",
    )


def screen_single_ma_short_term_short(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """R-MA-23單一均線短線做空戰法，與R-MA-22鏡射對稱。"""
    return _single_ma_strategy_screen(
        df, min_days, single_ma_short_term_short_strategy, is_long=False, hold_ma_label="MA5",
        signal_name="R-MA-23單一均線短線做空（88%）",
    )


def screen_single_ma_mid_term_long(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """R-MA-24單一均線中線做多戰法：進場訊號仍用MA5判斷回檔結束，持股/停利改守MA10。"""
    return _single_ma_strategy_screen(
        df, min_days, single_ma_mid_term_long_strategy, is_long=True, hold_ma_label="MA10",
        signal_name="R-MA-24單一均線中線做多（88%）",
    )


def screen_single_ma_mid_term_short(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """R-MA-25單一均線中線做空戰法，與R-MA-24鏡射對稱。"""
    return _single_ma_strategy_screen(
        df, min_days, single_ma_mid_term_short_strategy, is_long=False, hold_ma_label="MA10",
        signal_name="R-MA-25單一均線中線做空（88%）",
    )


def screen_dual_ma_long_term_long(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """R-MA-28兩條均線長線做多戰法：MA10/MA20黃金交叉且多排向上進場。"""
    if len(df) < min_days:
        return None
    close, open_, low = df["close"], df["open"], df["low"]
    ma5, ma10, ma20 = sma(close, 5), sma(close, 10), sma(close, 20)
    t = len(close) - 1
    if pd.isna(ma20.iloc[t]):
        return None
    result = dual_ma_long_term_long_strategy(close, ma5, ma10, ma20)
    if not bool(result["entry_signal"].iloc[t]):
        return None
    entry_price = float(close.iloc[t])
    stop_loss = ma_strategy_stop_loss_long(
        entry_open=float(open_.iloc[t]), entry_close=float(close.iloc[t]),
        entry_low=float(low.iloc[t]), swing_low_after_entry=None,
    )
    return {
        "signal_name": "R-MA-28兩條均線長線做多（89%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": "MA10上穿MA20且多排向上，黃金交叉進場",
    }


def screen_dual_ma_long_term_short(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """R-MA-29兩條均線長線做空戰法，與R-MA-28鏡射對稱。"""
    if len(df) < min_days:
        return None
    close, open_, high = df["close"], df["open"], df["high"]
    ma5, ma10, ma20 = sma(close, 5), sma(close, 10), sma(close, 20)
    t = len(close) - 1
    if pd.isna(ma20.iloc[t]):
        return None
    result = dual_ma_long_term_short_strategy(close, ma5, ma10, ma20)
    if not bool(result["entry_signal"].iloc[t]):
        return None
    entry_price = float(close.iloc[t])
    stop_loss = ma_strategy_stop_loss_short(
        entry_open=float(open_.iloc[t]), entry_close=float(close.iloc[t]),
        entry_high=float(high.iloc[t]), swing_high_after_entry=None,
    )
    return {
        "signal_name": "R-MA-29兩條均線長線做空（89%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": "MA10下穿MA20且空排向下，死亡交叉進場",
    }


def screen_narrow_range_bottom_breakout(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-SCREEN-11底部狹幅盤整大量紅K突破鎖股訊號。

    重用`src/indicators/consolidation.py`的橫盤偵測(min_bars設成約2個月交易日，比照書中
    「盤整須達2個月以上」的門檻)；該函式已經確認過「中長紅K收盤站上頸線」，這裡只需要額外
    算出區間均量、交給`screening_rules.narrow_range_bottom_breakout()`檢查量能是否達
    區間均量2倍以上(這是`detect_consolidation_breakout`本身不檢查的部分)。
    """
    if len(df) < min_days:
        return None
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]

    box = detect_consolidation_breakout(open_, high, low, close, min_bars=CONSOLIDATION_MIN_BARS)
    t = len(close) - 1
    if t < 1 or not bool(box["breakout_up"].iloc[t]):
        return None

    prior_group_len = int(box["group_len"].iloc[t - 1])
    range_start = max(0, t - prior_group_len)
    range_avg_volume = float(volume.iloc[range_start:t].mean())
    consolidation_upper = float(box["upper_neckline"].iloc[t - 1])

    triggered = narrow_range_bottom_breakout(
        duration_months=prior_group_len / 21.0,
        is_red_k=bool(is_mid_long_red_candle(open_, close).iloc[t]),
        close=float(close.iloc[t]), consolidation_upper=consolidation_upper,
        volume=float(volume.iloc[t]), range_avg_volume=range_avg_volume,
    )
    if not triggered:
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bull_short_term_stop_loss(entry_bar_low=float(low.iloc[t]))
    return {
        "signal_name": "R-SCREEN-11底部盤整突破鎖股（89%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": f"底部狹幅盤整{prior_group_len}天以上大量紅K突破＋量能達區間均量2倍以上",
    }


def screen_slow_rally_channel_breakout(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-SCREEN-15緩漲上升軌道線突破大量長紅K
    做多訊號。重用`src/patterns/chart_overlays.compute_trendlines()`已經算好的上升軌道線
    (`up_channel`，跟K線圖疊圖用的是同一套邏輯，不重新發明取點演算法)。
    """
    if len(df) < min_days:
        return None
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]

    trendlines = chart_overlays.compute_trendlines(df)
    up_channel = trendlines.get("up_channel")
    if up_channel is None:
        return None

    t = len(close) - 1
    channel_value = up_channel.at(t)
    avg_volume_20 = float(volume.iloc[max(0, t - 20):t].mean())

    triggered = slow_rally_channel_breakout(
        close=float(close.iloc[t]), channel_upper_value=channel_value,
        is_long_red_k=bool(is_mid_long_red_candle(open_, close).iloc[t]),
        volume=float(volume.iloc[t]), avg_volume_20=avg_volume_20,
    )
    if not triggered:
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bull_short_term_stop_loss(entry_bar_low=float(low.iloc[t]))
    return {
        "signal_name": "R-SCREEN-15緩漲軌道突破做多（88%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": "緩漲上升軌道線大量長紅K突破＋量能達20日均量2倍以上",
    }


def screen_breakout_above_big_black_candle(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-CLASSIC-24突破大量黑K買進訊號。

    書中觀念：多頭排列(均線多頭)期間出現的大量黑K，表面上是賣壓K棒，但只要後續股價收盤
    突破該黑K高點且放量，黑K反而不是轉空訊號、而是續漲買進訊號。往回搜尋最近
    `BIG_BLACK_BREAKOUT_LOOKBACK`天內、最近一根「均線多頭排列期間出現的大量黑K」當作
    突破基準(watch_high)，只取最近一根而不是任一根，避免抓到已經沒有參考意義的舊黑K。
    """
    if len(df) < min_days:
        return None
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]
    t = len(close) - 1

    ma_frame = compute_ma_set(close, periods=(5, 10, 20))
    bullish = is_bullish_aligned(ma_frame)
    big_volume = is_big_volume_vs_prev_day(volume, multiple=2.0)
    is_black = close < open_

    watch_high = None
    search_start = t - 1
    search_end = max(search_start - BIG_BLACK_BREAKOUT_LOOKBACK, -1)
    for j in range(search_start, search_end, -1):
        if bool(is_black.iloc[j]) and bool(big_volume.iloc[j]) and bool(bullish.iloc[j]):
            watch_high = float(high.iloc[j])
            break
    if watch_high is None:
        return None

    if not (close.iloc[t] > watch_high and bool(big_volume.iloc[t])):
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bull_short_term_stop_loss(entry_bar_low=float(low.iloc[t]))
    return {
        "signal_name": "R-CLASSIC-24突破大量黑K買進（87%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": f"多頭排列期間出現大量黑K(高點{watch_high:.2f})，今日收盤突破且放量，非轉空為續漲買進訊號",
    }


def screen_breakaway_gap_up(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-GAP-09打底完成向上突破缺口訊號。

    書中評為訊號等級最高的型態之一：底部盤整完成後，股價向上跳空且缺口下緣不低於盤整區
    上緣(真正突破、不是普通缺口)，屬強力買進訊號，原本的壓力線也轉為支撐。「3天內回補
    視為假突破」是事後才能確認的警示，不是觸發訊號的前提(見`detect_breakaway_gap_up()`
    的docstring)，這裡評估「缺口發生當天」時傳入`gap_filled_within_3_days=False`
    (當下還不知道未來3天會不會回補，不代表訊號無效，只是還沒有這個額外警示可以標註)。
    """
    if len(df) < min_days:
        return None
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]
    t = len(close) - 1
    if t < 1:
        return None

    gap = detect_gap(
        prev_high=float(high.iloc[t - 1]), prev_low=float(low.iloc[t - 1]),
        curr_high=float(high.iloc[t]), curr_low=float(low.iloc[t]),
    )
    if gap is None or gap.type != "up_gap":
        return None

    box = detect_consolidation(high.iloc[:t], low.iloc[:t], min_bars=GAP_CONSOLIDATION_MIN_BARS)
    if not bool(box["is_consolidating"].iloc[-1]):
        return None
    consolidation_upper = float(box["upper_neckline"].iloc[-1])

    avg_volume_20 = float(volume.iloc[max(0, t - 20):t].mean())
    is_large_volume = bool(volume.iloc[t] >= 2.0 * avg_volume_20)

    result = detect_breakaway_gap_up(
        gap=gap, consolidation_upper=consolidation_upper,
        is_large_volume=is_large_volume, gap_filled_within_3_days=False,
    )
    if result is None:
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bull_short_term_stop_loss(entry_bar_low=float(low.iloc[t]))
    return {
        "signal_name": "R-GAP-09打底完成向上突破缺口（90%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": f"{result['signal']}，缺口下緣{result['support']:.2f}(原壓力轉支撐)",
    }


def screen_breakaway_gap_down(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-GAP-14做頭完成向下跌破缺口訊號，是
    `screen_breakaway_gap_up()`(R-GAP-09)的鏡射版本。書中明文的關鍵不對稱：這條不需要
    大量配合(`detect_breakaway_gap_down()`本身沒有量能檢查)，跟R-GAP-09不同。
    `topping_pattern_confirmed`用「缺口上緣是否清楚跌出已盤整區間下緣」判斷，不是還在
    盤整區內部的普通缺口，跟R-GAP-09判斷「缺口下緣不低於盤整區上緣」同一種思路的鏡射。
    """
    if len(df) < min_days:
        return None
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]
    t = len(close) - 1
    if t < 1:
        return None

    gap = detect_gap(
        prev_high=float(high.iloc[t - 1]), prev_low=float(low.iloc[t - 1]),
        curr_high=float(high.iloc[t]), curr_low=float(low.iloc[t]),
    )
    if gap is None or gap.type != "down_gap":
        return None

    box = detect_consolidation(high.iloc[:t], low.iloc[:t], min_bars=GAP_CONSOLIDATION_MIN_BARS)
    if not bool(box["is_consolidating"].iloc[-1]):
        return None
    consolidation_lower = float(box["lower_neckline"].iloc[-1])
    topping_pattern_confirmed = gap.upper_edge <= consolidation_lower

    result = detect_breakaway_gap_down(
        gap=gap, topping_pattern_confirmed=topping_pattern_confirmed, gap_filled_within_3_days=False,
    )
    if result is None:
        return None

    entry_price = float(close.iloc[t])
    stop_loss = bear_short_term_stop_loss(entry_bar_high=float(high.iloc[t]))
    return {
        "signal_name": "R-GAP-14做頭完成向下跌破缺口（91%）",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "note": f"{result['signal']}，缺口上緣{result['resistance']:.2f}(原支撐轉壓力)",
    }


def screen_mechanical_long(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-CANDLE-32機械化多頭K線交易規則進場。

    `mechanical_long_trading_rule()`本身就是完整的逐日狀態機(全書docstring明言是最接近
    可直接程式化的規則：只用「前一日高低點」為唯一比較基準，不依賴任何K線型態辨識)，
    這裡只需要跑一次整段序列、檢查「今天」的action是不是「進場」，entry_price/stop_loss
    直接讀狀態機自己算出來的值，不需要另外計算。
    """
    if len(df) < min_days:
        return None
    high, low, close = df["high"], df["low"], df["close"]
    result = mechanical_long_trading_rule(high, low, close)
    t = len(close) - 1
    if result["action"].iloc[t] != "進場":
        return None
    return {
        "signal_name": "R-CANDLE-32機械化多頭K線交易規則（89%）",
        "entry_price": float(result["entry_price"].iloc[t]),
        "stop_loss": float(result["stop_loss"].iloc[t]),
        "note": "收盤突破前一日高點進場；跌破前一日低點或觸及7%停損出場",
    }


def screen_mechanical_short(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-CANDLE-33機械化空頭K線交易規則進場，
    是`screen_mechanical_long()`(R-CANDLE-32)的鏡射版本。
    """
    if len(df) < min_days:
        return None
    high, low, close = df["high"], df["low"], df["close"]
    result = mechanical_short_trading_rule(high, low, close)
    t = len(close) - 1
    if result["action"].iloc[t] != "進場":
        return None
    return {
        "signal_name": "R-CANDLE-33機械化空頭K線交易規則（89%）",
        "entry_price": float(result["entry_price"].iloc[t]),
        "stop_loss": float(result["stop_loss"].iloc[t]),
        "note": "收盤跌破前一日低點放空；突破前一日高點或觸及7%停損回補",
    }


_SCREEN_FUNCTIONS = (
    screen_bull_short_term_entry,
    screen_bear_short_term_entry,
    screen_narrow_range_bottom_breakout,
    screen_slow_rally_channel_breakout,
    screen_breakout_above_big_black_candle,
    screen_breakaway_gap_up,
    screen_breakaway_gap_down,
    screen_mechanical_long,
    screen_mechanical_short,
    screen_single_ma_short_term_long,
    screen_single_ma_short_term_short,
    screen_single_ma_mid_term_long,
    screen_single_ma_mid_term_short,
    screen_dual_ma_long_term_long,
    screen_dual_ma_long_term_short,
)


_SIGNAL_NAME_PATTERN = re.compile(r"^(R-[A-Z]+-\d+)(.*)（(\d+)%）$")


def analyze_stock_signals(df: pd.DataFrame, min_days: int = 60, trend_df: pd.DataFrame | None = None) -> list[dict]:
    """對「單一股票」的OHLCV資料，跑過①目前已接上的所有screen_*規則(整套進場SOP，含
    進場價/停損建議)、②`src.screener.rule_scan`的「黃金層」單點技術訊號(不含進場/停損
    建議)，回傳「今天」(資料最後一列)符合的訊號清單，依信心分數由高到低排序，每筆附上
    從ai/zhu-rules/查出的規則完整說明——供UI的「個股分析」面板使用，不同於
    screen_all_stocks/run_screen_and_store是批次跑「所有股票」寫回daily_candidates
    資料表，這裡是針對使用者當下正在看的單一股票即時運算，不寫入資料庫。

    目前只涵蓋這兩類已接上的規則（不是全部246條規則庫，範圍界定見rule_scan.py開頭的
    說明），範圍會隨之後接上更多規則自動擴大，這裡的程式碼不用跟著改。

    trend_df：轉傳給`scan_golden_tier()`專門供短/中/長(日/週/月)趨勢分類器使用的長歷史
    資料，見那裡的說明；不傳時退回用`df`自己的歷史。

    ⚠️ 回傳清單裡每個rule_id只會出現一次：`scan_golden_tier()`裡有些規則(例如R-TREND-03/
    04，短/中/長三種天期各自獨立判斷)可能對同一個rule_id呼叫多次add()、每次note文字不同
    (天期不同)，這裡合併成一筆，`note`欄位可能是用換行接起來的多行文字，呼叫端(UI)顯示
    時要自行處理多行(不能假設`note`永遠是單行字串)。跟「候選清單」(daily_candidates，
    只顯示`_SCREEN_FUNCTIONS`這組更精簡的「新進場機會」規則)範圍不同、通常會比候選清單
    列出更多規則，是刻意設計成這樣：「個股分析」是「這檔股票今天符合規則庫裡哪些訊號」
    的完整清單，「候選清單」只是其中一個精選子集(附進場價/停損建議的SOP型規則)，兩者
    定位不同，不是bug。
    """
    from src.rule_docs import load_rule_doc, parse_confidence
    from src.screener.rule_scan import scan_golden_tier

    matches: list[dict] = []
    for screen_fn in _SCREEN_FUNCTIONS:
        result = screen_fn(df, min_days=min_days)
        if result is None:
            continue
        name_match = _SIGNAL_NAME_PATTERN.match(result["signal_name"])
        if not name_match:
            continue
        rule_id, title, confidence = name_match.group(1), name_match.group(2), int(name_match.group(3))
        doc = load_rule_doc(rule_id)
        matches.append({
            "rule_id": rule_id,
            "title": title,
            "confidence": confidence,
            "note": result.get("note"),
            "description": doc.get("解讀") if doc else None,
            "reference": doc.get("原文與頁碼") if doc else None,
        })

    for item in scan_golden_tier(df, trend_df=trend_df):
        doc = load_rule_doc(item["rule_id"])
        confidence = parse_confidence(item["rule_id"])
        if confidence is None:
            continue  # 理論上不會發生(rule_docs涵蓋全部246條)，查無信心分數就不列入
        matches.append({
            "rule_id": item["rule_id"],
            "title": doc.get("名稱", item["rule_id"]),
            "confidence": confidence,
            "note": item["note"],
            "description": doc.get("解讀"),
            "reference": doc.get("原文與頁碼"),
        })

    # ⚠️ 同一個rule_id可能在scan_golden_tier()裡被add()呼叫超過一次，最常見的情況是
    # R-TREND-03/04(短/中/長三種天期各自獨立判斷多空，見trend_state.py)——如果例如短期
    # 跟中期剛好都是多頭，會各自產生一筆rule_id="R-TREND-03"、但note文字不同(天期不同)
    # 的match。不合併的話，UI畫面上同一條規則名稱會重複出現兩次、只有下面的「目前狀態」
    # 文字不一樣，使用者容易誤以為是重複的bug。這裡合併成同一個rule_id只留一筆，把
    # 多筆note文字用換行接起來，呼叫端(dashboard/desktop)看到的「目前狀態」可能是
    # 多行文字，需要各自處理換行顯示。
    merged: dict[str, dict] = {}
    order: list[str] = []
    for m in matches:
        rid = m["rule_id"]
        if rid not in merged:
            merged[rid] = dict(m)
            merged[rid]["note"] = [m["note"]] if m.get("note") else []
            order.append(rid)
        elif m.get("note"):
            merged[rid]["note"].append(m["note"])
    result = [merged[rid] for rid in order]
    for m in result:
        m["note"] = "\n".join(m["note"]) if m["note"] else None

    result.sort(key=lambda m: m["confidence"], reverse=True)
    return result


def summarize_signal_matches(matches: list[dict]) -> dict:
    """對`analyze_stock_signals()`回傳的清單算出一段簡短總結，供UI在列完所有符合規則後
    再附加一段「總結分析」，不用讓使用者自己從一長串規則清單裡歸納重點。

    多頭/空頭傾向是依規則「標題」文字裡有沒有出現「多」或「空」字概略分類的，不是精確的
    多空判定——書中不少規則(例如K棒型態、缺口規則)的標題本來就不含這兩個字(如「低檔晨星」
    「向上跳空缺口支撐規則」)，這類規則會被歸進"other"，不會被迫湊進多頭或空頭；標題同時
    含「多」「空」兩字或兩者都沒有，也歸進"other"。這只是「大致上偏多的訊號比較多還是偏空
    的訊號比較多」的粗略統計，用來讓總結一眼看出整體風向，不是取代逐條規則判讀。

    matches已經依confidence由高到低排序(見analyze_stock_signals())，回傳的"top_match"
    直接取第一筆即可，不用重新排序。matches為空時回傳total=0、top_match=None。
    """
    if not matches:
        return {"total": 0, "bullish": 0, "bearish": 0, "other": 0, "top_match": None}

    bullish = bearish = other = 0
    for m in matches:
        title = m["title"]
        has_bull = "多" in title
        has_bear = "空" in title
        if has_bull and not has_bear:
            bullish += 1
        elif has_bear and not has_bull:
            bearish += 1
        else:
            other += 1

    return {
        "total": len(matches),
        "bullish": bullish,
        "bearish": bearish,
        "other": other,
        "top_match": matches[0],
    }


def screen_all_stocks(stock_frames: dict[str, pd.DataFrame], min_days: int = 60) -> list[dict]:
    """對多檔股票批次跑目前已接上的所有screen_*規則，回傳今天所有觸發訊號的候選清單。
    同一檔股票若同時觸發多條規則，會分別各出現一筆(不同signal_name)，不互相排擠。

    stock_frames: {stock_id: df}，df需已依date排序、index為date、含open/high/low/close/volume欄位。
    """
    candidates: list[dict] = []
    for stock_id, df in stock_frames.items():
        for screen_fn in _SCREEN_FUNCTIONS:
            result = screen_fn(df, min_days=min_days)
            if result is not None:
                candidates.append({"stock_id": stock_id, **result})
    return candidates


def load_trailing_frames(conn, min_days: int = 60) -> dict[str, pd.DataFrame]:
    """讀出每檔股票至今的全部OHLCV歷史(不設上限，由screen_all_stocks自己判斷天數夠不夠算指標)。
    純讀取，跟資料是Turso還是本機sqlite無關，供 scripts/daily_pipeline.py 與 dashboard/app.py 共用。

    排除market='INDEX'的列(目前只有大盤`^TWII`，見src/data/yfinance_client.py的
    fetch_taiex_prices())——大盤不是一檔可以交易的股票，不該被個股適用的screen_*規則
    (進場價/停損建議等)誤判成候選標的、混進daily_candidates候選清單。

    2026-08-04新增：一併排除`delisted_stocks`表裡已確認下市/併購/終止興櫃買賣的股票
    (見scripts/daily_pipeline.py的fetch_today_tpex()說明)——這些股票不會再有新的
    股價資料，但歷史資料還留在stock_prices裡，不明確排除的話，screen_*規則仍然會
    對它們「最後一天」的舊資料重新評估，可能產生一個實際上已經買不到的假候選標的。
    """
    stock_ids = [
        r[0] for r in conn.execute(
            """
            SELECT stock_id FROM stocks
            WHERE market != 'INDEX' AND stock_id NOT IN (SELECT stock_id FROM delisted_stocks)
            ORDER BY stock_id
            """
        ).fetchall()
    ]

    frames: dict[str, pd.DataFrame] = {}
    for stock_id in stock_ids:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM stock_prices WHERE stock_id = ? ORDER BY date",
            (stock_id,),
        ).fetchall()
        if len(rows) < min_days:
            continue
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        frames[stock_id] = df.set_index("date")
    return frames


def run_screen_and_store(conn, iso_date: str | None = None, min_days: int = 60) -> list[dict]:
    """只用資料庫裡『目前已有』的資料重新跑一次選股並寫回daily_candidates，不對外抓取任何新資料。

    這是刻意的設計：抓新資料(TWSE/TPEx)成本較高(TPEx經yfinance批次下載，實測約1~2分鐘)，跟
    「用現有資料重算訊號」(純本地運算，通常幾秒內)分開，才能讓 dashboard 提供「立即重新篩選」
    這種不需要等待資料抓取的即時操作；scripts/daily_pipeline.py 抓完當天新資料後也呼叫同一份
    邏輯，避免重複實作。

    ⚠️ 同一天可能重跑選股不只一次(手動按「立即重新篩選」按很多次、或補資料後重算)，每次都是
    從資料庫現有資料重新算出『完整』的候選清單，不是增量疊加——所以寫入前一定要先清掉這個
    日期的舊紀錄(見storage.delete_daily_candidates_for_date)，否則「這次已經不再符合條件」
    的股票會繼續卡在表裡，讓候選清單顯示過時的結果。即使這次重算出0檔候選，也要清掉舊紀錄
    (代表『今天正確答案就是沒有候選股』)，不能因為candidates是空的就跳過清除這一步。

    ⚠️ iso_date為None時，「今天」指的是『資料庫裡實際有價格資料的最新交易日』(MAX(date)
    FROM stock_prices)，不是`date.today()`字面上的日曆日期——兩者在非交易日(週末/國定
    假日)按「立即重新篩選」時會不同：`scripts/daily_pipeline.py`本身已經有「TWSE官方+
    yfinance盤中備援都查無資料就判定非交易日、跳過選股」的檢查，但「立即重新篩選」是
    純本地重算、不會觸發那段檢查，如果沿用`date.today()`，週六按下去會把『用上週五資料
    算出的結果』寫成『週六的候選清單』，候選清單日期下拉選單因此會冒出一個實際上沒有任何
    交易發生的日期，使用者會誤以為系統在非交易日也有跑選股。改成用價格資料本身的最新日期，
    不管哪一天按都會正確地寫回『資料實際對應的那個交易日』。
    """
    if iso_date is None:
        latest_price_date = conn.execute("SELECT MAX(date) FROM stock_prices").fetchone()[0]
        iso_date = latest_price_date or date.today().isoformat()

    frames = load_trailing_frames(conn, min_days=min_days)
    candidates = screen_all_stocks(frames, min_days=min_days)

    storage.delete_daily_candidates_for_date(conn, iso_date)
    if candidates:
        storage.upsert_daily_candidates(conn, [
            {
                "date": iso_date, "stock_id": c["stock_id"], "signal_name": c["signal_name"],
                "entry_price": c["entry_price"], "stop_loss": c["stop_loss"], "note": c.get("note"),
                "created_at": datetime.now().isoformat(),
            }
            for c in candidates
        ])

    # 均線/SAR快取：候選清單「篩選方法」原本每次套用篩選都對stock_prices即時重算，改成
    # 查daily_indicators表(見chart_data.py的load_ma_bullish_flags_from_table()/
    # load_sar_flip_flags_from_table())，這裡順便把iso_date這一天的均線/SAR算好存進去，
    # 沿用上面已經讀出來的frames(不用另外查一次DB)。涵蓋①每天第一次算出當天指標②盤中價
    # →收盤價修正(重新按一次這個函式，今天的指標會跟著重算覆蓋)。歷史資料事後被修正
    # (例如這次session修過的TAIEX成交量延遲bug)的風險，由scripts/daily_pipeline.py
    # 排程時額外往回刷新一段窗口涵蓋，不在這裡處理，避免使用者連續手動按「立即重新篩選」
    # 時重複付出往回刷新的成本。
    #
    # ⚠️ 只傳入df.tail(LIVE_UPDATE_LOOKBACK_DAYS)，不是frames裡的整段歷史：
    # compute_indicator_rows()對整個df只算一次SAR/均線，但這個「一次」的成本是O(df的
    # 總天數)，不是O(target_dates的數量)——2026-08-02實測發現，傳整段歷史(當時已累積
    # ~860天)會讓這裡多花69秒，拖慢使用者按「立即重新篩選」的體感速度，且會隨DB歷史
    # 持續累積而越來越慢。裁切成最近LIVE_UPDATE_LOOKBACK_DAYS天，效能才會是固定成本，
    # 見indicator_precompute.py模組docstring的詳細說明。
    indicator_rows: list[dict] = []
    for stock_id, df in frames.items():
        indicator_rows.extend(compute_indicator_rows(stock_id, df.tail(LIVE_UPDATE_LOOKBACK_DAYS), {iso_date}))
    if indicator_rows:
        storage.upsert_daily_indicators(conn, indicator_rows)

    return candidates


def refresh_indicator_window(conn, end_date: str, window_days: int, min_days: int = 60) -> int:
    """往回刷新最近`window_days`個交易日的均線/SAR快取(`daily_indicators`)，不只
    `end_date`當天一天——供`scripts/daily_pipeline.py`排程執行時呼叫，吸收股價資料
    事後被修正的風險(例如TWSE盤中價→收盤價、或yfinance歷史資料事後回補，這次session
    修過的TAIEX成交量延遲bug就是活生生的例子，見`src/data/schema.sql`的
    `daily_indicators`表說明)。只在排程/完整pipeline呼叫，不在`run_screen_and_store()`
    (手動「立即重新篩選」按鈕)裡做，避免使用者連續手動觸發時重複付出這筆額外成本。

    每檔股票各自往回抓自己最近`window_days`筆(不是用單一交易日曆列表)，避免不同股票
    資料涵蓋範圍略有落差(例如新上市、或個別股票資料缺漏)時互相影響。

    回傳實際寫入的列數，供呼叫端記錄/印出。
    """
    frames = load_trailing_frames(conn, min_days=min_days)
    end_ts = pd.Timestamp(end_date)
    indicator_rows: list[dict] = []
    for stock_id, df in frames.items():
        bounded = df[df.index <= end_ts].tail(LIVE_UPDATE_LOOKBACK_DAYS)
        recent = bounded.tail(window_days)
        if recent.empty:
            continue
        target_dates = set(recent.index.strftime("%Y-%m-%d"))
        # 跟run_screen_and_store()同樣的效能陷阱：傳入bounded(裁切過)而不是整段df，
        # 避免每檔股票的SAR/均線計算成本隨DB歷史持續累積而越來越慢，見
        # indicator_precompute.py模組docstring的說明。
        indicator_rows.extend(compute_indicator_rows(stock_id, bounded, target_dates))
    if indicator_rows:
        storage.upsert_daily_indicators(conn, indicator_rows)
    return len(indicator_rows)
