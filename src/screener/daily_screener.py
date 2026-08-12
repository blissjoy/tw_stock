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

⚠️ 2026-08-08新增陳家豐籌碼面building block（融資維持率超跌反彈/投信連續買超，程式碼
分別在`src.indicators.margin_trading`/`institutional_flow`）進`daily_candidates`
候選清單，是本檔案第一批「非純OHLCV」的訊號來源；量縮止跌(`volume_washout.py`)也已
實作對應的`screen_volume_washout()`，但實測全市場觸發率高達47%(見`screen_all_
stocks()`docstring)，經使用者確認暫不接進候選清單，只留函式本身：
- 這3條在此之前只接進`src.presentation.stock_detail_data.scan_chip_tier()`／
  `analyze_chip_signals()`，供「個股分析」／「大盤分析」面板顯示「這檔股票今天符合哪些
  規則」，但那條路徑是使用者正在看某一檔股票時才即時運算、不寫入`daily_candidates`，
  跟這裡「批次跑全市場、寫回候選清單」是兩件事——之前只做了前者，這次補上後者。
- 刻意重用`margin_trading.py`／`institutional_flow.py`的底層純函式(`compute_margin_
  maintenance_ratio()`／`margin_oversold_rebound_signal()`／`classify_flow_streak()`)
  而不是重用`scan_chip_tier(conn, stock_id)`本身——`scan_chip_tier()`是「每次呼叫查一次
  DB」的單股函式，全市場(~2000檔)批次呼叫會變成N+1查詢，正是本專案N+1查詢曾經在Turso上
  拖慢觀察清單頁面到8.9秒才修好的同一種問題(見`src/presentation/huang_chip_data.py`的
  批次化教訓)。這裡改成`load_trailing_margin_frames()`／`load_trailing_institutional_
  trust_net()`各自一次SQL把「全部股票」的融資/法人資料讀出來，再用Python依stock_id分組，
  避免重蹈覆轍。
- 只挑「買進方向」的訊號進候選清單(融資超跌反彈/投信連續買超)，三大法人連續
  賣超(R-SCREEN-06)這種「排除型」訊號刻意不放進來——`daily_candidates`的既有慣例是
  「都是清楚的做多進場訊號」，混進「應該避開」的警示會讓候選清單語意不一致，這條警示
  已經在`scan_chip_tier()`／「個股分析」面板顯示，那裡才是它該出現的地方。
- entry_price/stop_loss：這幾條規則書中都沒有給明確的停損公式(不像R-TREND-14有書中
  明確的5%~7%數字)，這裡一律用「當日最低點」當停損參考(不額外打折扣)，是工程預設值，
  不是引用自書中——跟`bull_short_term_stop_loss()`那種「書中明確區間」的函式不同，
  刻意不重用它，避免誤讓使用者以為這個停損數字也是書中的明確規則。
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Callable

import pandas as pd

from src.data import storage
from src.indicators import institutional_flow, margin_trading, volume_washout
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


def screen_bull_short_term_entry(df: pd.DataFrame, min_days: int = 60, bull_trend: pd.Series | None = None) -> dict | None:
    """對單一股票的OHLCV資料(依date遞增排序、index為date)判斷「今天」(最後一列)是否觸發
    R-TREND-14多頭短線進場訊號。資料不足min_days天則回傳None(不足以計算MA20等指標)。

    bull_trend：可選的預先算好的`daily_bull_trend_state(high, low, close, n=5)`結果——
    這條規則、`screen_single_ma_short_term_long()`、`screen_single_ma_mid_term_long()`
    三條都需要同一個(n=5)多頭趨勢判斷，2026-08-08前是各自呼叫`daily_bull_trend_state()`
    重算一次，實測全市場批次掃描時這6條(含鏡射的空頭3條)重複計算佔了23%的運算時間；
    改成由呼叫端(`screen_all_stocks()`／`analyze_stock_signals()`)每檔股票只算一次、
    傳進來共用。不傳(None，例如測試或單獨呼叫時)則照舊在函式內部自行計算，行為完全不變。
    """
    if len(df) < min_days:
        return None

    close, high, low, open_, volume = df["close"], df["high"], df["low"], df["open"], df["volume"]
    ma10 = sma(close, 10)
    ma20 = sma(close, 20)
    ma10_slope = ma10.diff()
    ma20_slope = ma20.diff()
    volume_prev = volume.shift(1)
    if bull_trend is None:
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


def screen_bear_short_term_entry(df: pd.DataFrame, min_days: int = 60, bear_trend: pd.Series | None = None) -> dict | None:
    """對單一股票的OHLCV資料判斷「今天」是否觸發R-TREND-15空頭短線選股訊號——
    R-TREND-14多頭短線進場的鏡射對稱版本，用`daily_bear_trend_state()`(見trend.py)。

    bear_trend：可選的預先算好結果，理由跟`screen_bull_short_term_entry()`的
    `bull_trend`參數說明一致(2026-08-08效能優化)。
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
    if bear_trend is None:
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
    trend: pd.Series | None = None,
) -> dict | None:
    """R-MA-22/23/24/25(單一均線短/中線做多做空戰法)共用的wiring邏輯：4個戰法共用同一套
    骨架(見src/strategies/ma_strategies.py開頭說明)，差別只在傳入的策略函式、多空方向、
    停損守哪一條均線(短線守MA5/中線守MA10)——抽成共用函式避免4份幾乎相同的程式碼。

    trend：可選的預先算好的`daily_bull_trend_state()`／`daily_bear_trend_state()`(視
    is_long而定，n=5)結果，理由跟`screen_bull_short_term_entry()`的`bull_trend`參數
    說明一致(2026-08-08效能優化，供4個戰法跟短線多空進場規則共用同一份計算)。不傳
    (None)則照舊在函式內部自行計算。
    """
    if len(df) < min_days:
        return None
    close, high, low, open_ = df["close"], df["high"], df["low"], df["open"]
    ma5, ma10, ma20 = sma(close, 5), sma(close, 10), sma(close, 20)
    t = len(close) - 1
    if pd.isna(ma20.iloc[t]) or pd.isna(ma10.iloc[t]):
        return None

    if trend is None:
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


def screen_single_ma_short_term_long(df: pd.DataFrame, min_days: int = 60, bull_trend: pd.Series | None = None) -> dict | None:
    """R-MA-22單一均線短線做多戰法：進出場皆守MA5。"""
    return _single_ma_strategy_screen(
        df, min_days, single_ma_short_term_long_strategy, is_long=True, hold_ma_label="MA5",
        signal_name="R-MA-22單一均線短線做多（88%）", trend=bull_trend,
    )


def screen_single_ma_short_term_short(df: pd.DataFrame, min_days: int = 60, bear_trend: pd.Series | None = None) -> dict | None:
    """R-MA-23單一均線短線做空戰法，與R-MA-22鏡射對稱。"""
    return _single_ma_strategy_screen(
        df, min_days, single_ma_short_term_short_strategy, is_long=False, hold_ma_label="MA5",
        signal_name="R-MA-23單一均線短線做空（88%）", trend=bear_trend,
    )


def screen_single_ma_mid_term_long(df: pd.DataFrame, min_days: int = 60, bull_trend: pd.Series | None = None) -> dict | None:
    """R-MA-24單一均線中線做多戰法：進場訊號仍用MA5判斷回檔結束，持股/停利改守MA10。"""
    return _single_ma_strategy_screen(
        df, min_days, single_ma_mid_term_long_strategy, is_long=True, hold_ma_label="MA10",
        signal_name="R-MA-24單一均線中線做多（88%）", trend=bull_trend,
    )


def screen_single_ma_mid_term_short(df: pd.DataFrame, min_days: int = 60, bear_trend: pd.Series | None = None) -> dict | None:
    """R-MA-25單一均線中線做空戰法，與R-MA-24鏡射對稱。"""
    return _single_ma_strategy_screen(
        df, min_days, single_ma_mid_term_short_strategy, is_long=False, hold_ma_label="MA10",
        signal_name="R-MA-25單一均線中線做空（88%）", trend=bear_trend,
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

    ⚠️ 2026-08-08效能優化：只傳入`df.tail(LIVE_UPDATE_LOOKBACK_DAYS)`給
    `compute_trendlines()`，不是整段歷史——跟`indicator_precompute.py`裡SAR快取
    同一個道理(該模組docstring有詳細的效能陷阱說明)：這裡的轉折點/切線演算法同樣是
    「狀態隨資料逐步累積」的類型，只要有足夠的暖身天數(SAR驗證過400天已足夠讓初始
    種子收斂)，結果就會跟餵全部歷史一致。2026-08-08用本機真實DB(全市場~2368檔，
    最長累積約867天歷史)實測驗證：改用400天窗口後，11檔真實觸發的R-SCREEN-15候選
    逐筆比對(signal_name/entry_price/stop_loss)完全一致，這個函式的耗時從16.8秒
    降到10.9秒(降35%)。刻意只在這個函式裡截斷，不改`compute_trendlines()`／
    `compute_turning_points()`本身——這兩個是共用函式，同時供圖表疊圖(dashboard/
    desktop的K線圖切線)、`rule_scan.py`(個股分析/大盤分析面板)、`trend_state.py`
    (多時間框架趨勢判斷)使用，動到共用函式本身風險與驗證範圍大得多，這次刻意不做。
    """
    if len(df) < min_days:
        return None
    df = df.tail(LIVE_UPDATE_LOOKBACK_DAYS)
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


def screen_margin_oversold_rebound(df: pd.DataFrame, margin_df: pd.DataFrame | None, min_days: int = 60) -> dict | None:
    """對單一股票判斷「今天」是否觸發R-CHIP-02融資維持率超跌反彈訊號(陳家豐書中P02-C4)。

    margin_df須為`load_trailing_margin_frames()`批次讀出、該股票對應的融資歷史(欄位
    close/margin_buy/margin_sell/margin_cash_repayment/margin_today_balance，依date
    遞增排序)，查無融資資料(該股從未有人融資買賣)回傳None——跟`stock_detail_data.
    load_margin_maintenance_analysis()`用同一套底層計算(`compute_margin_maintenance_
    ratio()`)，數字會跟「個股明細」分頁顯示的一致。
    """
    if len(df) < min_days or margin_df is None or margin_df.empty:
        return None
    ratio = margin_trading.compute_margin_maintenance_ratio(
        margin_df["close"], margin_df["margin_buy"], margin_df["margin_sell"],
        margin_df["margin_cash_repayment"], margin_df["margin_today_balance"],
    )
    if not bool(margin_trading.margin_oversold_rebound_signal(ratio).iloc[-1]):
        return None

    from src.rule_docs import parse_confidence
    confidence = parse_confidence("R-CHIP-02")
    latest_ratio = ratio.iloc[-1]
    ratio_text = f"約{latest_ratio * 100:.1f}%" if pd.notna(latest_ratio) else ""
    return {
        "signal_name": f"R-CHIP-02融資維持率超跌反彈（{confidence}%）",
        "entry_price": float(df["close"].iloc[-1]),
        "stop_loss": float(df["low"].iloc[-1]),
        "note": f"融資維持率{ratio_text}已連續{margin_trading.OVERSOLD_MIN_CONSECUTIVE_DAYS}天低於120%斷頭線，符合超跌反彈搶短觀察條件(僅適合能嚴設停利的短線操作)",
    }


def screen_institutional_trust_buy_streak(df: pd.DataFrame, trust_net_desc: list[float] | None, min_days: int = 60) -> dict | None:
    """對單一股票判斷「今天」是否觸發R-CHIP-01投信連續買超觀察訊號(陳家豐書中P04-C2)。

    trust_net_desc須為`load_trailing_institutional_trust_net()`批次讀出、該股票投信
    每日買賣超淨額、由新到舊排序(index 0=今天)的清單，查無投信資料回傳None。跟
    `stock_detail_data.load_institutional_flow_analysis()`用同一個`classify_flow_
    streak()`底層函式，數字會跟「個股明細」分頁顯示的一致。
    """
    if len(df) < min_days or not trust_net_desc:
        return None
    streak = institutional_flow.classify_flow_streak(trust_net_desc)
    if not streak["is_buy_watch"]:
        return None

    from src.rule_docs import parse_confidence
    confidence = parse_confidence("R-CHIP-01")
    return {
        "signal_name": f"R-CHIP-01投信連續買超觀察（{confidence}%）",
        "entry_price": float(df["close"].iloc[-1]),
        "stop_loss": float(df["low"].iloc[-1]),
        "note": f"投信已連續買超{streak['streak_days']}天，達最佳切入點觀察門檻(書中給連續3~5天)",
    }


# 「排除型」候選訊號的signal_name標記前綴：跟screen_institutional_trust_buy_streak()
# 這種「買進候選」共用daily_candidates同一張表(2026-08-09使用者決定，不另建新表)，
# 但語意是「這檔股票今天有賣超警訊，應排除/謹慎」而非「買進機會」。UI端(chart_data.py
# 的split_candidate_and_warning_signals())依這個前綴字串切分成候選/警示兩組顯示，
# 不能混在一起呈現成同一份「進場建議」清單，否則會誤導使用者。
WARNING_SIGNAL_PREFIX = "⚠️排除："


def screen_institutional_trust_sell_streak(df: pd.DataFrame, trust_net_desc: list[float] | None, min_days: int = 60) -> dict | None:
    """對單一股票判斷「今天」是否觸發投信連續賣超警訊——跟screen_institutional_trust_
    buy_streak()同一份`trust_net_desc`資料、同一個`classify_flow_streak()`底層函式，
    只是改看`is_sell_warning`而非`is_buy_watch`。

    引用朱家泓淘汰法R-SCREEN-06「三大法人連續賣超要避開」(見`ai/zhu-rules/選股策略/
    淘汰法選股排除規則.md`)的信心分數，這裡把範圍縮小到投信這一個分類單獨判斷(R-
    SCREEN-06原文是三大法人合計)，跟`stock_detail_data.scan_chip_tier()`目前對三大
    法人合計判斷R-SCREEN-06是不同顆粒度，但沿用同一套「連續賣超要避開」的方法論。
    signal_name加上`WARNING_SIGNAL_PREFIX`前綴，`run_screen_and_store()`一樣寫進
    `daily_candidates`，但呼叫端要用該前綴區分「候選」和「警示」，不能混著當進場建議。
    """
    if len(df) < min_days or not trust_net_desc:
        return None
    streak = institutional_flow.classify_flow_streak(trust_net_desc)
    if not streak["is_sell_warning"]:
        return None

    from src.rule_docs import parse_confidence
    confidence = parse_confidence("R-SCREEN-06")
    return {
        "signal_name": f"{WARNING_SIGNAL_PREFIX}R-SCREEN-06投信連續賣超（{confidence}%）",
        "entry_price": float(df["close"].iloc[-1]),
        "stop_loss": float(df["low"].iloc[-1]),
        "note": f"投信已連續賣超{streak['streak_days']}天，達停損觀察門檻，非買進訊號",
    }


def screen_foreign_investor_buy_streak(df: pd.DataFrame, foreign_net_desc: list[float] | None, min_days: int = 60) -> dict | None:
    """對單一股票判斷「今天」是否觸發外資連續買超觀察訊號——跟screen_institutional_
    trust_buy_streak()同一套`classify_flow_streak()`邏輯，資料來源改用`load_trailing_
    foreign_investor_net()`。

    引用R-CHIP-10「外資訊號有效性條件」(見`ai/chen-rules/籌碼面/外資訊號有效性條件.md`)
    的信心分數——書中明確提醒外資買賣超訊號只有中小型/非權值股才有參考價值，權值股/
    大型股受全球布局、期貨套利、指數調整干擾不宜採信；本專案目前沒有市值/是否為權值股
    的分類資料(股本/市值尚未串接，見該規則檔「可程式化」欄位說明)，這裡**沒有**先過濾
    掉大型權值股，note文字明確附上這個限制提醒，避免使用者對台積電這類權值股的外資
    連續買超訊號照單全收。
    """
    if len(df) < min_days or not foreign_net_desc:
        return None
    streak = institutional_flow.classify_flow_streak(foreign_net_desc)
    if not streak["is_buy_watch"]:
        return None

    from src.rule_docs import parse_confidence
    confidence = parse_confidence("R-CHIP-10")
    return {
        "signal_name": f"R-CHIP-10外資連續買超觀察（{confidence}%）",
        "entry_price": float(df["close"].iloc[-1]),
        "stop_loss": float(df["low"].iloc[-1]),
        "note": (
            f"外資已連續買超{streak['streak_days']}天；⚠️書中提醒外資訊號只有中小型/"
            "非權值股才有參考價值，本專案目前無法自動判斷是否為權值股，請自行留意"
        ),
    }


def screen_foreign_investor_sell_streak(df: pd.DataFrame, foreign_net_desc: list[float] | None, min_days: int = 60) -> dict | None:
    """對單一股票判斷「今天」是否觸發外資連續賣超警訊——跟screen_institutional_trust_
    sell_streak()同一套邏輯與R-SCREEN-06信心引用，資料來源改用`load_trailing_foreign_
    investor_net()`。signal_name同樣加上`WARNING_SIGNAL_PREFIX`前綴。
    """
    if len(df) < min_days or not foreign_net_desc:
        return None
    streak = institutional_flow.classify_flow_streak(foreign_net_desc)
    if not streak["is_sell_warning"]:
        return None

    from src.rule_docs import parse_confidence
    confidence = parse_confidence("R-SCREEN-06")
    return {
        "signal_name": f"{WARNING_SIGNAL_PREFIX}R-SCREEN-06外資連續賣超（{confidence}%）",
        "entry_price": float(df["close"].iloc[-1]),
        "stop_loss": float(df["low"].iloc[-1]),
        "note": f"外資已連續賣超{streak['streak_days']}天，達停損觀察門檻，非買進訊號",
    }


def screen_volume_washout(df: pd.DataFrame, min_days: int = 60) -> dict | None:
    """對單一股票判斷「今天」是否觸發R-CHIP-03低檔量縮止跌觀察訊號(陳家豐書中P07-C4)。

    只需要df本身的volume欄位(不需要額外的融資/法人資料)，資料不足
    `volume_washout.VOLUME_WASHOUT_LOOKBACK`(240)天時，`volume_washout_signal()`
    內部rolling(min_periods=lookback)本來就會回傳NaN/False，這裡不用另外判斷天數。

    ⚠️ 2026-08-08：這個函式本身可正常運作、也有測試涵蓋，但`screen_all_stocks()`
    刻意沒有呼叫它——實測對本機真實DB全市場掃描，觸發率高達47%，遠比R-CHIP-01/02
    雜訊多很多，會讓daily_candidates候選清單失去篩選意義(理由見screen_all_stocks()
    docstring)。保留這個函式供未來有更好的鑑別方法(例如搭配is_at_low或其他訊號一起
    判讀)時直接接回screen_all_stocks()，不需要重寫。
    """
    if len(df) < min_days:
        return None
    signal_series = volume_washout.volume_washout_signal(df["volume"])
    if not bool(signal_series.iloc[-1]):
        return None

    from src.rule_docs import parse_confidence
    confidence = parse_confidence("R-CHIP-03")
    return {
        "signal_name": f"R-CHIP-03低檔量縮止跌觀察（{confidence}%）",
        "entry_price": float(df["close"].iloc[-1]),
        "stop_loss": float(df["low"].iloc[-1]),
        "note": "近期均量已萎縮到近1年峰值均量的10分之1以下，符合籌碼洗清、主力再進場觀察條件(書中提醒不宜單獨當高信心買進理由，建議搭配其他訊號一起判讀)",
    }


# 2026-08-08效能優化：screen_bull_short_term_entry/screen_bear_short_term_entry/4個
# 單一均線戰法(R-MA-22/23/24/25)這6條規則，刻意不放進_SCREEN_FUNCTIONS這個統一迴圈，
# 拆成_BULL_TREND_SCREEN_FUNCTIONS/_BEAR_TREND_SCREEN_FUNCTIONS兩組——這6條裡有3條
# 要用同一份`daily_bull_trend_state(n=5)`、3條要用同一份`daily_bear_trend_state(n=5)`，
# 如果留在同一個迴圈裡個別呼叫、不傳入預先算好的trend，每條規則會各自重算一次，對
# 同一檔股票重複算6次——實測全市場批次掃描時這部分佔了23%的運算時間(見ai/PLAN.md
# 2026-08-08該日期章節)。拆成獨立的兩組，讓screen_all_stocks()／analyze_stock_
# signals()能對每檔股票只算一次bull_trend/bear_trend、傳給整組規則共用。
_SCREEN_FUNCTIONS = (
    screen_narrow_range_bottom_breakout,
    screen_slow_rally_channel_breakout,
    screen_breakout_above_big_black_candle,
    screen_breakaway_gap_up,
    screen_breakaway_gap_down,
    screen_mechanical_long,
    screen_mechanical_short,
    screen_dual_ma_long_term_long,
    screen_dual_ma_long_term_short,
)

_BULL_TREND_SCREEN_FUNCTIONS = (
    screen_bull_short_term_entry,
    screen_single_ma_short_term_long,
    screen_single_ma_mid_term_long,
)

_BEAR_TREND_SCREEN_FUNCTIONS = (
    screen_bear_short_term_entry,
    screen_single_ma_short_term_short,
    screen_single_ma_mid_term_short,
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
    from src.screener import escape_signals
    from src.screener.rule_scan import scan_golden_tier

    matches: list[dict] = []

    def _add_screen_result(result: dict | None) -> None:
        if result is None:
            return
        name_match = _SIGNAL_NAME_PATTERN.match(result["signal_name"])
        if not name_match:
            return
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

    for screen_fn in _SCREEN_FUNCTIONS:
        _add_screen_result(screen_fn(df, min_days=min_days))

    # _BULL_TREND_SCREEN_FUNCTIONS/_BEAR_TREND_SCREEN_FUNCTIONS這6條規則跟
    # screen_all_stocks()共用同一套「trend只算一次」的效能優化(見那兩個常數的說明)，
    # 這裡雖然只處理單一股票、效能差異不明顯，但仍然沿用同一份計算避免維護兩套邏輯。
    if len(df) >= min_days:
        bull_trend = daily_bull_trend_state(df["high"], df["low"], df["close"], n=5)
        bear_trend = daily_bear_trend_state(df["high"], df["low"], df["close"], n=5)
        for screen_fn in _BULL_TREND_SCREEN_FUNCTIONS:
            _add_screen_result(screen_fn(df, min_days=min_days, bull_trend=bull_trend))
        for screen_fn in _BEAR_TREND_SCREEN_FUNCTIONS:
            _add_screen_result(screen_fn(df, min_days=min_days, bear_trend=bear_trend))

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

    # 2026-08-11新增「逃命示警」標記：使用者反映一長串規則列表裡混著買賣訊號，看不出
    # 哪些是該注意快逃的——`escape_signals.is_escape_signal()`從既有已接線規則裡挑出
    # 偏向「示警/賣出」性質的一批，UI可以依`is_escape`把這些提到最上方另外標示，不用
    # 新增規則、不影響原本的排序清單。
    for m in result:
        m["is_escape"] = escape_signals.is_escape_signal(m["rule_id"], m.get("note"))

    # KD死亡交叉(不受趨勢限制)：書中R-INDICATOR-09要求死亡交叉要搭配趨勢才觸發，這裡
    # 額外獨立判斷，見escape_signals.detect_kd_death_cross()的docstring。confidence
    # 借用R-INDICATOR-09既有信心分數(同一個底層概念，只是拿掉趨勢前提)，查無分數時
    # 給60分中等信心的保守預設值。
    if escape_signals.detect_kd_death_cross(df):
        result.append({
            "rule_id": escape_signals.ESCAPE_KD_DEATH_CROSS_RULE_ID,
            "title": "KD死亡交叉(不分趨勢)",
            "confidence": parse_confidence("R-INDICATOR-09") or 60,
            "note": "K值由上往下穿越D值，動能轉弱——不受R-INDICATOR-09「依趨勢判讀」的前提限制，只要死亡交叉發生就示警",
            "description": "本專案「逃命示警」面板新增的補充判斷，不是ai/zhu-rules/裡有書籍頁碼佐證的正式規則，底層跟R-INDICATOR-09同樣是KD死亡交叉，差別只在這裡不要求搭配趨勢。",
            "reference": None,
            "is_escape": True,
        })

    # 2026-08-12新增：每筆訊號附上「這是依據哪一天的資料判斷出來的」日期，用df最後一列
    # (「今天」，見本函式docstring)的日期，不是呼叫當下的系統日期——如果這檔股票的資料
    # 更新有延遲(例如停牌/資料抓取中斷)，df最後一列可能不是真正的今天，這裡如實反映
    # 資料本身的日期，不假裝是最新的。使用者反映「逃命示警」列表看不出訊號是哪天出現的，
    # 容易誤以為全部都是今天才發生——多數規則(死亡交叉等)確實是當天才會觸發的單日事件，
    # 但R-TREND-04這類「趨勢狀態」規則只要條件持續成立就會每天重複出現在清單裡，不是
    # 「今天才開始」，這裡只能如實提供「這筆訊號對應的資料日期」，不是「這個現象從
    # 哪一天開始」(後者需要逐日回溯，不在這裡的能力範圍內，見ai/PLAN.md「探索多空綜合
    # 摘要」那次判定不可行的記錄)。
    if df.empty:
        as_of_date = None
    elif isinstance(df.index, pd.DatetimeIndex):
        as_of_date = str(df.index[-1].date())
    else:
        # 測試用的合成資料常常直接給RangeIndex(0,1,2...)而非真正的日期索引，真實呼叫端
        # (chart_data.load_price_history()的輸出)一律是DatetimeIndex，這裡防呆不crash。
        as_of_date = str(df.index[-1])
    for m in result:
        m["date"] = as_of_date

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


def screen_all_stocks(
    stock_frames: dict[str, pd.DataFrame],
    min_days: int = 60,
    margin_frames: dict[str, pd.DataFrame] | None = None,
    institutional_trust_net: dict[str, list[float]] | None = None,
    foreign_investor_net: dict[str, list[float]] | None = None,
) -> list[dict]:
    """對多檔股票批次跑目前已接上的所有screen_*規則，回傳今天所有觸發訊號的候選清單。
    同一檔股票若同時觸發多條規則，會分別各出現一筆(不同signal_name)，不互相排擠。

    stock_frames: {stock_id: df}，df需已依date排序、index為date、含open/high/low/close/volume欄位。

    margin_frames/institutional_trust_net/foreign_investor_net：分別對應`load_
    trailing_margin_frames()`／`load_trailing_institutional_trust_net()`／`load_
    trailing_foreign_investor_net()`的批次讀取結果，供R-CHIP-02(融資超跌反彈)／
    R-CHIP-01(投信連續買超)／R-CHIP-10(外資連續買超)＋投信/外資連續賣超警訊使用；
    不傳(None)時這些籌碼面規則一律回傳None，只有技術面`_SCREEN_FUNCTIONS`會產生
    候選(向下相容既有呼叫端與測試)。

    ⚠️ 2026-08-09新增的投信/外資連續賣超這兩條，signal_name會帶`WARNING_SIGNAL_
    PREFIX`前綴，一樣寫進回傳清單、一樣會被`run_screen_and_store()`存進`daily_
    candidates`，但語意是「排除警示」不是「買進候選」——呼叫端(UI)要用這個前綴分開
    顯示，不能當成一般候選股列出，理由見`WARNING_SIGNAL_PREFIX`常數說明。

    ⚠️ R-CHIP-03(量縮止跌，`screen_volume_washout()`)刻意不放進這裡：2026-08-08實測
    對本機真實DB全市場~2368檔掃描，觸發率高達47%(1106檔)，遠比R-CHIP-01(39檔)／
    R-CHIP-02(64檔)雜訊多很多——雖然書中原文本來就說量縮是「市場常態」不是罕見訊號(見
    volume_washout.py模組docstring)，但把接近半個市場都標成候選股會讓daily_candidates
    這張表失去篩選意義，經使用者確認先不接進候選清單，只留函式本身(已測試、可隨時
    接回)，等有更好的鑑別方法(例如搭配`trend_position.py`的is_at_low、或集中度等其他
    訊號一起判讀)再考慮加回來。
    """
    margin_frames = margin_frames or {}
    institutional_trust_net = institutional_trust_net or {}
    foreign_investor_net = foreign_investor_net or {}
    candidates: list[dict] = []
    for stock_id, df in stock_frames.items():
        for screen_fn in _SCREEN_FUNCTIONS:
            result = screen_fn(df, min_days=min_days)
            if result is not None:
                candidates.append({"stock_id": stock_id, **result})

        # 2026-08-08效能優化：bull_trend/bear_trend每檔股票只算一次，供_BULL_TREND_
        # SCREEN_FUNCTIONS/_BEAR_TREND_SCREEN_FUNCTIONS共6條規則共用(見這兩個常數
        # 的說明)，取代原本各自在函式內部重算的寫法。df長度不足min_days時這6條規則
        # 反正一開始就會回傳None，跳過運算，不用白算一次trend。
        if len(df) >= min_days:
            bull_trend = daily_bull_trend_state(df["high"], df["low"], df["close"], n=5)
            bear_trend = daily_bear_trend_state(df["high"], df["low"], df["close"], n=5)
            for screen_fn in _BULL_TREND_SCREEN_FUNCTIONS:
                result = screen_fn(df, min_days=min_days, bull_trend=bull_trend)
                if result is not None:
                    candidates.append({"stock_id": stock_id, **result})
            for screen_fn in _BEAR_TREND_SCREEN_FUNCTIONS:
                result = screen_fn(df, min_days=min_days, bear_trend=bear_trend)
                if result is not None:
                    candidates.append({"stock_id": stock_id, **result})

        margin_result = screen_margin_oversold_rebound(df, margin_frames.get(stock_id), min_days=min_days)
        if margin_result is not None:
            candidates.append({"stock_id": stock_id, **margin_result})

        trust_result = screen_institutional_trust_buy_streak(df, institutional_trust_net.get(stock_id), min_days=min_days)
        if trust_result is not None:
            candidates.append({"stock_id": stock_id, **trust_result})

        trust_sell_result = screen_institutional_trust_sell_streak(df, institutional_trust_net.get(stock_id), min_days=min_days)
        if trust_sell_result is not None:
            candidates.append({"stock_id": stock_id, **trust_sell_result})

        foreign_buy_result = screen_foreign_investor_buy_streak(df, foreign_investor_net.get(stock_id), min_days=min_days)
        if foreign_buy_result is not None:
            candidates.append({"stock_id": stock_id, **foreign_buy_result})

        foreign_sell_result = screen_foreign_investor_sell_streak(df, foreign_investor_net.get(stock_id), min_days=min_days)
        if foreign_sell_result is not None:
            candidates.append({"stock_id": stock_id, **foreign_sell_result})
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


# 融資維持率批次篩選的回看窗口(交易日)：跟volume_washout.py的VOLUME_WASHOUT_LOOKBACK
# (240，約1年)同一個量級的工程估計值——約1年的融資歷史，足夠讓compute_margin_
# maintenance_ratio()「歷史起點當天餘額視為當天買進」的早期誤差充分收斂(見該函式
# docstring)，不需要真的讀「全部歷史」。2026-08-08實測：本機DB累積約3.5年融資歷史時，
# 不設窗口的全量批次查詢(全部~1500檔)要價16秒，改成只抓最近250個交易日後降到約6秒，
# 差距會隨資料庫歷史持續累積而越拉越大，早晚必須設窗口，不如一開始就設好。
# `stock_detail_data.load_margin_maintenance_analysis()`(個股明細分頁，一次只查1檔)
# 仍維持不設窗口的全歷史算法，兩處數字理論上可能有些微差異，但差異只出現在資料庫歷史
# 起點那段已經收斂掉的早期誤差範圍內，不影響「今天是否超跌反彈」的判斷。
MARGIN_SCREENING_LOOKBACK_DAYS = 250

# 投信連續買超批次篩選的回看窗口(交易日)：連續買超天數門檻只有3天(見institutional_
# flow.py的INSTITUTIONAL_STREAK_THRESHOLD)，抓太長沒意義，跟stock_detail_data.
# load_institutional_flow_analysis()預設的lookback_days=30同一個量級(該函式docstring：
# 「連續天數不太可能超過30個交易日，抓太長沒意義」)。
INSTITUTIONAL_TRUST_SCREENING_LOOKBACK_DAYS = 30


def _trading_day_cutoff(conn, lookback_days: int) -> str | None:
    """回傳『倒數第lookback_days個有股價資料的交易日』的日期字串，供load_trailing_
    margin_frames()／load_trailing_institutional_trust_net()把批次查詢限定在『最近
    N個交易日』，避免隨資料庫歷史持續累積、批次查詢時間跟著無上限變慢(見上方兩個
    LOOKBACK常數的說明)。用stock_prices的實際交易日清單而不是`date.today()`往回推算
    N個日曆天，理由跟run_screen_and_store()的iso_date預設值一樣：週末/國定假日不是
    交易日，用日曆天回推會抓到過多不需要的天數或抓不夠。stock_prices歷史天數不足
    lookback_days時，回傳最早的那一天(等同於沒有窗口限制)，不會出錯或漏資料。
    """
    row = conn.execute(
        "SELECT date FROM (SELECT DISTINCT date FROM stock_prices ORDER BY date DESC LIMIT ?) ORDER BY date LIMIT 1",
        (lookback_days,),
    ).fetchone()
    return row[0] if row else None


def load_trailing_margin_frames(conn) -> dict[str, pd.DataFrame]:
    """批次讀出『全部股票』最近`MARGIN_SCREENING_LOOKBACK_DAYS`個交易日的融資交易歷史
    (margin_trading JOIN stock_prices的收盤價)，供screen_margin_oversold_rebound()
    計算融資維持率用。

    刻意用單一SQL一次讀出全部股票、再用Python依stock_id分組，不是逐檔股票各自查一次——
    後者對~2000檔股票會變成N+1查詢，在Turso上曾經把觀察清單頁面拖慢到8.9秒才修好(見
    `src/presentation/huang_chip_data.py`的批次化教訓)，這裡從一開始就採用批次寫法，
    不重蹈覆轍。回傳{stock_id: df}，df欄位為close/margin_buy/margin_sell/
    margin_cash_repayment/margin_today_balance，依date遞增排序，缺融資資料的股票不會
    出現在回傳結果裡。
    """
    cutoff = _trading_day_cutoff(conn, MARGIN_SCREENING_LOOKBACK_DAYS)
    rows = conn.execute(
        # CROSS JOIN(而非JOIN)是刻意的：SQLite的CROSS JOIN語意等同INNER JOIN，但會強制
        # 查詢規劃器不要重新排序表的處理順序(一般JOIN在這裡容易被規劃器誤判成先掃
        # stock_prices(同期間列數較多)再逐列查margin_trading，反而更慢)——2026-08-08
        # 實測：改成CROSS JOIN強制先用margin_trading的date索引，把這段查詢從~5.7秒
        # 降到~4.2秒。
        """
        SELECT mt.stock_id, mt.date, sp.close, mt.margin_purchase_buy, mt.margin_purchase_sell,
               mt.margin_purchase_cash_repayment, mt.margin_purchase_today_balance
        FROM margin_trading mt
        CROSS JOIN stock_prices sp ON sp.stock_id = mt.stock_id AND sp.date = mt.date
        WHERE mt.date >= ?
        ORDER BY mt.stock_id, mt.date
        """,
        (cutoff or "",),
    ).fetchall()
    by_stock: dict[str, list[tuple]] = defaultdict(list)
    for stock_id, mdate, close, buy, sell, repay, balance in rows:
        by_stock[stock_id].append((mdate, close, buy, sell, repay, balance))

    frames: dict[str, pd.DataFrame] = {}
    for stock_id, stock_rows in by_stock.items():
        frames[stock_id] = pd.DataFrame(
            {
                "close": [r[1] for r in stock_rows],
                "margin_buy": [r[2] for r in stock_rows],
                "margin_sell": [r[3] for r in stock_rows],
                "margin_cash_repayment": [r[4] for r in stock_rows],
                "margin_today_balance": [r[5] for r in stock_rows],
            },
            index=pd.to_datetime([r[0] for r in stock_rows]),
        )
    return frames


def load_trailing_institutional_trust_net(conn) -> dict[str, list[float]]:
    """批次讀出『全部股票』最近`INSTITUTIONAL_TRUST_SCREENING_LOOKBACK_DAYS`個交易日
    投信(Investment_Trust)每日買賣超淨額，依date遞增排序，供screen_institutional_
    trust_buy_streak()判斷連續買超天數用——只抓投信這一個分類(不是三大法人合計)，因為
    這裡只接「投信連續買超」這個買進方向的候選訊號(理由見本模組docstring)，
    `institutional_investors`表的`investor_type='Investment_Trust'`剛好跟
    `stock_detail_data._INVESTOR_GROUP_MAP`的「投信」一對一對應，不需要額外分類。

    跟`load_trailing_margin_frames()`同樣的N+1顧慮與批次化理由，單一SQL一次讀出全部
    股票。回傳{stock_id: [由新到舊排序的淨額]}，直接是`classify_flow_streak()`要求的
    輸入格式，缺投信資料的股票不會出現在回傳結果裡。
    """
    cutoff = _trading_day_cutoff(conn, INSTITUTIONAL_TRUST_SCREENING_LOOKBACK_DAYS)
    rows = conn.execute(
        "SELECT stock_id, date, buy, sell FROM institutional_investors "
        "WHERE investor_type = 'Investment_Trust' AND date >= ? ORDER BY stock_id, date",
        (cutoff or "",),
    ).fetchall()
    by_stock: dict[str, list[float]] = defaultdict(list)
    for stock_id, _date, buy, sell in rows:
        by_stock[stock_id].append(buy - sell)
    return {stock_id: list(reversed(values)) for stock_id, values in by_stock.items()}


def load_trailing_foreign_investor_net(conn) -> dict[str, list[float]]:
    """批次讀出『全部股票』最近`INSTITUTIONAL_TRUST_SCREENING_LOOKBACK_DAYS`個交易日
    外資每日買賣超淨額，依date遞增排序，供screen_foreign_investor_buy_streak()／
    screen_foreign_investor_sell_streak()判斷連續買/賣超天數用——沿用跟`stock_
    detail_data._INVESTOR_GROUP_MAP`一致的「外資」定義：`Foreign_Investor`(外資本身)
    +`Foreign_Dealer_Self`(外資自營商，較少見)兩個investor_type合計，不是只算前者。
    跟`load_trailing_institutional_trust_net()`共用同一個回看窗口常數(門檻同樣只有
    3天，抓太長沒意義)。

    回傳{stock_id: [由新到舊排序的淨額]}，格式跟`load_trailing_institutional_trust_
    net()`一致，直接是`classify_flow_streak()`要求的輸入格式。
    """
    cutoff = _trading_day_cutoff(conn, INSTITUTIONAL_TRUST_SCREENING_LOOKBACK_DAYS)
    rows = conn.execute(
        "SELECT stock_id, date, buy, sell FROM institutional_investors "
        "WHERE investor_type IN ('Foreign_Investor', 'Foreign_Dealer_Self') AND date >= ? "
        "ORDER BY stock_id, date",
        (cutoff or "",),
    ).fetchall()
    net_by_stock_date: dict[tuple[str, str], float] = defaultdict(float)
    for stock_id, date_, buy, sell in rows:
        net_by_stock_date[(stock_id, date_)] += buy - sell
    by_stock: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (stock_id, date_), net in net_by_stock_date.items():
        by_stock[stock_id].append((date_, net))
    return {
        stock_id: [net for _date, net in sorted(values, key=lambda x: x[0], reverse=True)]
        for stock_id, values in by_stock.items()
    }


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
    margin_frames = load_trailing_margin_frames(conn)
    institutional_trust_net = load_trailing_institutional_trust_net(conn)
    foreign_investor_net = load_trailing_foreign_investor_net(conn)
    candidates = screen_all_stocks(
        frames, min_days=min_days,
        margin_frames=margin_frames, institutional_trust_net=institutional_trust_net,
        foreign_investor_net=foreign_investor_net,
    )

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


def recompute_indicators_for_range(conn, stock_ids: list[str] | None, start_date: str, end_date: str) -> int:
    """「回補資料」桌面分頁專用：股價回補完成後，只針對受影響的股票子集＋回補的日期範圍
    重算daily_indicators(均線/SAR快取)，不像scripts/backfill_daily_indicators.py那樣對
    全部歷史重算一次——回補通常只動到一小段範圍，成本可以低很多。

    跟refresh_indicator_window()的差異：這裡的範圍由呼叫端明確指定(回補的start_date~
    end_date)，不是「往回抓最近window_days筆」；stock_ids為None時處理load_trailing_
    frames()讀到的全部股票(對應「全市場」回補情境)，非None時只處理指定子集。
    """
    frames = load_trailing_frames(conn, min_days=1)
    if stock_ids is not None:
        wanted = set(stock_ids)
        frames = {sid: df for sid, df in frames.items() if sid in wanted}

    start_ts, end_ts = pd.Timestamp(start_date), pd.Timestamp(end_date)
    indicator_rows: list[dict] = []
    for stock_id, df in frames.items():
        bounded = df[df.index <= end_ts]
        target_dates = set(bounded[bounded.index >= start_ts].index.strftime("%Y-%m-%d"))
        if not target_dates:
            continue
        indicator_rows.extend(compute_indicator_rows(stock_id, bounded, target_dates))
    if indicator_rows:
        storage.upsert_daily_indicators(conn, indicator_rows)
    return len(indicator_rows)


def run_screen_and_store_for_range(
    conn, stock_ids: list[str] | None, start_date: str, end_date: str, min_days: int = 60,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """「回補資料」桌面分頁「同時回補歷史候選清單訊號」勾選框專用：針對回補範圍內每一天，
    重新算出『當時』(只用<=該日期的資料)符合哪些screen_*規則，寫回daily_candidates。

    ⚠️ 不能直接對每個歷史日期各呼叫一次run_screen_and_store()：那個函式永遠評估『資料庫
    現有資料的最後一列』，在已經有更新資料的正式DB上對歷史iso_date呼叫，實際算出來的會是
    『今天』的訊號，只是被貼上歷史日期標籤寫進DB——是錯的。這裡改成把每檔股票的frame明確
    截到`df.index <= 該日期`才餵給screen_all_stocks()，才是真正『回到當時』重算。純本地
    運算，不呼叫任何API，但日期範圍/股票數越多，CPU時間越久，所以桌面版UI上這個選項預設
    不勾選。

    stock_ids為None時處理全部股票(對應「全市場」回補情境)。回傳寫入的候選總數(可能同一檔
    股票同一天觸發多條規則，各算一筆)。
    """
    frames = load_trailing_frames(conn, min_days=1)
    if stock_ids is not None:
        wanted = set(stock_ids)
        frames = {sid: df for sid, df in frames.items() if sid in wanted}

    start_ts, end_ts = pd.Timestamp(start_date), pd.Timestamp(end_date)
    trading_dates: set = set()
    for df in frames.values():
        trading_dates.update(df.index[(df.index >= start_ts) & (df.index <= end_ts)])
    dates = sorted(trading_dates)

    total_candidates = 0
    for i, d in enumerate(dates, 1):
        frames_as_of = {}
        for sid, df in frames.items():
            trimmed = df[df.index <= d]
            if len(trimmed) >= min_days:
                frames_as_of[sid] = trimmed
        candidates = screen_all_stocks(frames_as_of, min_days=min_days)
        iso_date = d.strftime("%Y-%m-%d")
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
        total_candidates += len(candidates)
        if on_progress is not None:
            on_progress(i, len(dates))
    return total_candidates
