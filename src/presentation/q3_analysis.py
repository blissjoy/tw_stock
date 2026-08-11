"""三維過濾法——「個股資訊」新分頁，複製外部工具Antigravity儀表板(見`ai/q3-rules/`)的
個股健檢面板：技術指標總覽＋12種量價矩陣＋30種主力型態＋規則版AI判定。彙整既有指標/
規則計算結果，不重複造輪子；只有ATR/OBV(`src.indicators.atr`/`src.indicators.obv`)、
量價矩陣分類(`src.indicators.volume_price_matrix`)、30型態偵測(`src.screener.q3_
patterns`)、以及本檔的規則版AI判定是這個功能新增的邏輯，其餘全部沿用既有指標/規則。

AI判定明確走「規則版」(不接LLM API)，使用者2026-08-10已確認——把矩陣格/型態群組/
均線排列/KD/MACD各自的多空傾向加權加總，超過門檻給出強勢/轉弱結論，介於中間給中性，
跟本專案「訊號都要能講出依據」的一貫風格一致，AI判定的每一條理由(bullets)都對應到
具體算出來的數字，不是黑箱文字。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.atr import average_true_range
from src.indicators.bollinger import bollinger_bands
from src.indicators.consolidation import detect_consolidation_breakout
from src.indicators.crossovers import is_death_cross, is_golden_cross
from src.indicators.kd import compute_kd
from src.indicators.macd import compute_macd
from src.indicators.moving_average import (
    bias_ratio,
    is_bearish_aligned,
    is_bullish_aligned,
    ma_direction,
    sma,
)
from src.indicators.obv import on_balance_volume
from src.indicators.rsi import rsi
from src.indicators.trend_position import compute_trend_position
from src.indicators.volume_price import basic_volume, is_big_volume_vs_ma5
from src.indicators.volume_price_matrix import (
    MatrixRow,
    classify_matrix_row,
    classify_price_direction_basic,
    classify_q1_price,
    classify_q2_volume,
    classify_q3_position,
    format_volume_price_relation,
)
from src.patterns.trend_state import classify_trend_states_multi_horizon
from src.screener.q3_patterns import scan_q3_patterns
from src.screener.rule_scan import scan_golden_tier

MIN_DAYS = 30

# 矩陣格多空傾向配分——見ai/q3-rules/量價矩陣/*.md各檔「解讀」欄，正負號代表偏多/偏空、
# 數字大小代表訊號強度(2=強烈方向性訊號，1=較弱或有但書的方向性訊號，0=中性/待確認)。
_MATRIX_SCORE: dict[str, int] = {
    "R-Q3M-01": 2, "R-Q3M-02": -2, "R-Q3M-03": -1, "R-Q3M-04": 1, "R-Q3M-05": 1,
    "R-Q3M-06": 1, "R-Q3M-07": -2, "R-Q3M-08": 1, "R-Q3M-09": -1, "R-Q3M-10": -1,
    "R-Q3M-11": 2, "R-Q3M-12": -2, "R-Q3M-13": -1, "R-Q3M-14": 1, "R-Q3M-15": 0,
    "R-Q3M-16": -2, "R-Q3M-17": 1, "R-Q3M-18": 0, "R-Q3M-19": 0,
}

# 30型態依編號分組對應多空傾向：01-08多頭結構(+1)／09-16空頭結構(-1)／17-24趨勢轉折
# 築底(+1)／25-30高檔反轉多翻空(-1)，跟ai/q3-rules/主力型態/的4組分類一致。
def _pattern_score(rule_id: str) -> int:
    no = int(rule_id.rsplit("-", 1)[-1])
    if no <= 8 or 17 <= no <= 24:
        return 1
    return -1


STRONG_THRESHOLD = 4
WEAK_THRESHOLD = -4


def load_q3_analysis(price_df: pd.DataFrame, trend_df: pd.DataFrame | None = None) -> dict | None:
    """price_df：至少`MIN_DAYS`天的OHLCV(比照`chart_data.load_price_history()`輸出，
    含MA5/10/20/60等欄位)；trend_df：`scan_golden_tier()`用的長歷史(見`chart_data.
    TREND_LOOKBACK_DAYS`)，不傳則退回用price_df自己的歷史(週/月線可能因資料不足被
    誤判成盤整，跟scan_golden_tier()本身的限制一致)。資料不足回傳None。
    """
    if price_df is None or len(price_df) < MIN_DAYS:
        return None
    open_, high, low, close, volume = (
        price_df["open"], price_df["high"], price_df["low"], price_df["close"], price_df["volume"],
    )

    ma8 = sma(close, 8)
    ma20 = price_df["MA20"] if "MA20" in price_df.columns else sma(close, 20)
    ma60 = price_df["MA60"] if "MA60" in price_df.columns else sma(close, 60)
    ma5 = price_df["MA5"] if "MA5" in price_df.columns else sma(close, 5)
    ma10 = price_df["MA10"] if "MA10" in price_df.columns else sma(close, 10)
    ma20_dir = ma_direction(ma20)

    kd = compute_kd(high, low, close)
    rsi9 = rsi(close, n=9)
    macd_df = compute_macd(close)
    bb = bollinger_bands(close)
    bias = bias_ratio(close, ma20)
    atr14 = average_true_range(high, low, close, n=14)
    obv = on_balance_volume(close, volume)
    ma5_vol = basic_volume(volume)

    trend_position = compute_trend_position(high, low, close)
    is_at_high, is_at_low = trend_position["is_at_high"], trend_position["is_at_low"]

    if trend_df is not None and not trend_df.empty:
        t_high, t_low, t_close = trend_df["high"], trend_df["low"], trend_df["close"]
    else:
        t_high, t_low, t_close = high, low, close
    trend_horizons = classify_trend_states_multi_horizon(t_high, t_low, t_close)
    trend_today = trend_horizons["短期"][1]

    consolidation = detect_consolidation_breakout(open_, high, low, close)
    q1 = classify_q1_price(close, consolidation["breakout_up"], consolidation["breakout_down"])
    q2 = classify_q2_volume(volume, ma5_vol)
    q3 = classify_q3_position(is_at_high, is_at_low)
    matrix_row = classify_matrix_row(q1.iloc[-1], q2.iloc[-1], q3.iloc[-1], trend_today)

    golden_tier_results = scan_golden_tier(price_df, trend_df=trend_df)
    patterns = scan_q3_patterns(price_df, golden_tier_results, trend_today)

    # 布林連續站上上軌天數(0代表今天沒站上)
    above_upper = close > bb["upper"]
    bollinger_streak = 0
    for above in reversed(above_upper.tolist()):
        if bool(above):
            bollinger_streak += 1
        else:
            break

    golden_today = bool(is_golden_cross(ma5, ma10).iloc[-1])
    death_today = bool(is_death_cross(ma5, ma10).iloc[-1])
    ma_cross_label = "黃金交叉" if golden_today else ("死亡交叉" if death_today else "無")

    is_big_volume_today = bool(is_big_volume_vs_ma5(volume, ma5_vol).iloc[-1])

    indicators = {
        "ma8": float(ma8.iloc[-1]) if pd.notna(ma8.iloc[-1]) else None,
        "ma20": float(ma20.iloc[-1]) if pd.notna(ma20.iloc[-1]) else None,
        "ma60": float(ma60.iloc[-1]) if pd.notna(ma60.iloc[-1]) else None,
        "k": float(kd["K"].iloc[-1]) if pd.notna(kd["K"].iloc[-1]) else None,
        "d": float(kd["D"].iloc[-1]) if pd.notna(kd["D"].iloc[-1]) else None,
        "rsi": float(rsi9.iloc[-1]) if pd.notna(rsi9.iloc[-1]) else None,
        "macd_osc": float(macd_df["OSC"].iloc[-1]) if pd.notna(macd_df["OSC"].iloc[-1]) else None,
        "obv": float(obv.iloc[-1]) if pd.notna(obv.iloc[-1]) else None,
        "atr": float(atr14.iloc[-1]) if pd.notna(atr14.iloc[-1]) else None,
        "bias_pct": float(bias.iloc[-1]) if pd.notna(bias.iloc[-1]) else None,
        "bollinger_streak_days": bollinger_streak,
        "ma_cross": ma_cross_label,
        "big_volume_today": is_big_volume_today,
        "q1_price": q1.iloc[-1],
        "q2_volume": q2.iloc[-1],
        "q3_position": q3.iloc[-1],
        # 今日量/5日均量的實際比例——2026-08-10使用者拿中光電(5371)實測發現「量平」
        # 判斷結果跟PDF原文「量縮」對不上，追查是外部工具用的量能判斷基準不明(黑盒子，
        # 猜測可能是跟「昨日量」比而非跟「5日均量」比，門檻也可能更窄)，不是bug或資料
        # 過期。使用者確認要把這個比例數字直接顯示出來，之後遇到類似落差可以直接看
        # 數字自己判斷，不用每次都回頭問。
        "volume_ratio_vs_ma5_pct": (
            float(volume.iloc[-1] / ma5_vol.iloc[-1] * 100) if pd.notna(ma5_vol.iloc[-1]) and ma5_vol.iloc[-1] else None
        ),
        # 用classify_price_direction_basic()(單純漲跌，不管是否同時觸及關鍵價位)，
        # 不是上面矩陣用的q1(可能被判成「關鍵點突破」而蓋掉單純的漲跌)——見
        # classify_q1_price()的說明，這是2026-08-10合晶(6182)實測發現的落差。
        "volume_price_relation": format_volume_price_relation(
            classify_price_direction_basic(close).iloc[-1], q2.iloc[-1],
        ),
    }

    matrix_result = (
        {
            "rule_id": matrix_row.rule_id, "label": matrix_row.label,
            "interpretation": matrix_row.interpretation, "caveat": matrix_row.caveat,
        }
        if matrix_row is not None
        else None
    )

    verdict = _build_verdict(
        matrix_row=matrix_row,
        patterns=patterns,
        is_bullish=bool(is_bullish_aligned(pd.DataFrame({"MA5": ma5, "MA10": ma10, "MA20": ma20})).iloc[-1]),
        is_bearish=bool(is_bearish_aligned(pd.DataFrame({"MA5": ma5, "MA10": ma10, "MA20": ma20})).iloc[-1]),
        k=indicators["k"], d=indicators["d"], macd_osc=indicators["macd_osc"],
        support_price=indicators["ma8"],
    )

    return {"indicators": indicators, "matrix": matrix_result, "patterns": patterns, "verdict": verdict}


def _build_verdict(
    matrix_row: MatrixRow | None, patterns: list[dict], is_bullish: bool, is_bearish: bool,
    k: float | None, d: float | None, macd_osc: float | None, support_price: float | None,
) -> dict:
    score = 0
    bullets: list[str] = []

    if matrix_row is not None:
        score += _MATRIX_SCORE.get(matrix_row.rule_id, 0)
        bullets.append(f"量價矩陣「{matrix_row.label}」：{matrix_row.interpretation}")

    for p in patterns:
        score += _pattern_score(p["rule_id"])

    if is_bullish:
        score += 2
        bullets.append("均線呈多頭排列(MA5>MA10>MA20)，短線動能偏強")
    elif is_bearish:
        score -= 2
        bullets.append("均線呈空頭排列(MA5<MA10<MA20)，短線動能偏弱")

    if k is not None and d is not None:
        if k > d:
            score += 1
            bullets.append(f"KD指標偏多(K:{k:.2f}>D:{d:.2f})，動能轉強")
        else:
            score -= 1
            bullets.append(f"KD指標偏空(K:{k:.2f}<D:{d:.2f})，動能轉弱")

    if macd_osc is not None:
        if macd_osc > 0:
            score += 1
            bullets.append("MACD柱狀翻紅，多方動能增強")
        else:
            score -= 1
            bullets.append("MACD柱狀翻綠，空方動能增強")

    if score >= STRONG_THRESHOLD:
        tier, text = "強勢", "強勢股，建議積極持有或加碼"
    elif score <= WEAK_THRESHOLD:
        tier, text = "轉弱", "偏弱，建議退場／減碼"
    else:
        tier, text = "中性", "訊號多空互見，建議觀望為宜"

    return {
        "tier": tier,
        "text": text,
        "score": score,
        "support_price": support_price,
        "bullets": bullets[:3],
    }
