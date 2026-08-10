"""30種主力特殊量價與K線轉折型態——「三維過濾法」(見`ai/q3-rules/主力型態/`，
R-Q3P-01~30)複製外部工具Antigravity儀表板的型態圖鑑，供「個股資訊」的「三維過濾法」
分頁使用。每條規則的來源、條件描述、跟既有規則庫的對應關係都記錄在對應的
`ai/q3-rules/主力型態/*.md`文件裡，這裡是那份文件「計算公式」欄的正式實作。

能重用既有已接線規則(`scan_golden_tier()`輸出)的型態，直接檢查對應rule_id是否
今天觸發，不重新實作一次判斷邏輯；查無對應或既有規則沒接線的型態，才在這裡用
OHLCV基本資料自行實作一個簡化版偵測——這些簡化版是本檔獨立維護的邏輯，不等於
文件裡引用的既有規則的完整定義(多數既有規則有額外的天期/門檻細節，這裡只抓
PDF原文描述的核心條件)，見各函式docstring個別註明。

每筆觸發結果都附上`basis`欄位(`BASIS_EXISTING_RULE`/`BASIS_SIMPLIFIED`)，
供UI直接標示「簡化版」，不能只寫在`ai/q3-rules/`文件裡要求使用者自己回頭翻
(2026-08-10使用者明確反映過這一點)。
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

from src.indicators.candles import (
    candle_shadows,
    is_black_candle,
    is_doji,
    is_mid_long_black_candle,
    is_mid_long_red_candle,
    is_red_candle,
)
from src.indicators.gaps import detect_gap
from src.indicators.moving_average import ma_direction, sma
from src.indicators.volume_price import basic_volume, is_big_volume_vs_ma5


def _notes_by_rule(golden_tier_results: list[dict]) -> dict[str, list[str]]:
    notes: dict[str, list[str]] = defaultdict(list)
    for r in golden_tier_results:
        notes[r["rule_id"]].append(r["note"])
    return notes


def _any_note_contains(notes_by_rule: dict[str, list[str]], rule_id: str, keyword: str) -> bool:
    return any(keyword in n for n in notes_by_rule.get(rule_id, []))


BASIS_EXISTING_RULE = "existing_rule"  # 直接重用既有已接線規則的判斷結果
BASIS_SIMPLIFIED = "simplified"  # 本檔自行實作的簡化版偵測，非既有規則的完整定義


def scan_q3_patterns(df: pd.DataFrame, golden_tier_results: list[dict], trend_today: str | None = None) -> list[dict]:
    """回傳今天觸發的Q3型態清單[{"rule_id": "R-Q3P-NN", "name": ..., "note": ..., "basis": ...},
    ...]。"basis"是`BASIS_EXISTING_RULE`(直接重用既有已接線規則，判斷邏輯跟系統其他地方
    完全一致)或`BASIS_SIMPLIFIED`(本檔自行實作的簡化版，只抓PDF原文描述的核心條件，
    不是既有規則的完整定義)——2026-08-10使用者反映「簡化版」這件事不能只寫在
    `ai/q3-rules/`文件裡，要直接在畫面上標示，不能靠使用者自己回頭翻文件才知道，
    這個欄位就是給UI層直接顯示用的。

    golden_tier_results：`src.screener.rule_scan.scan_golden_tier(df, trend_df)`的原始
    輸出，用來重用已接線規則的判斷結果，不重算一次。trend_today：短期(日線)多空盤整
    分類('多頭'/'空頭'/'盤整')，部分型態(如26號連續黑K見頂)需要知道目前是否處於高檔
    脈絡才有意義。資料不足(<30天)回傳空清單。
    """
    if len(df) < 30:
        return []
    open_, high, low, close, volume = df["open"], df["high"], df["low"], df["close"], df["volume"]
    notes = _notes_by_rule(golden_tier_results)
    ma5_vol = basic_volume(volume)
    ma20 = sma(close, 20)
    ma20_dir = ma_direction(ma20)
    is_red = is_red_candle(open_, close)
    is_black = is_black_candle(open_, close)
    is_mid_red = is_mid_long_red_candle(open_, close)
    is_mid_black = is_mid_long_black_candle(open_, close)
    upper_shadow, lower_shadow = candle_shadows(open_, high, low, close)
    body = (close - open_).abs()

    t, t1 = -1, -2  # 今天／昨天的.iloc索引
    results: list[dict] = []

    def add(no: str, name: str, note: str, basis: str) -> None:
        results.append({"rule_id": f"R-Q3P-{no}", "name": name, "note": note, "basis": basis})

    # 01 均線糾結帶量長紅——重用R-CLASSIC-30(已接線)
    if "R-CLASSIC-30" in notes:
        add("01", "均線糾結帶量長紅", notes["R-CLASSIC-30"][0], BASIS_EXISTING_RULE)

    # 02 突破前高量價齊揚——重用R-SR-01(已接線)
    if "R-SR-01" in notes:
        add("02", "突破前高量價齊揚", notes["R-SR-01"][0], BASIS_EXISTING_RULE)

    # 03 跳空過壓帶量缺口——簡化版：今天出現向上缺口，且量能較前一日放大
    if len(close) > 1:
        gap = detect_gap(
            prev_high=float(high.iloc[t1]), prev_low=float(low.iloc[t1]),
            curr_high=float(high.iloc[t]), curr_low=float(low.iloc[t]),
        )
        if gap is not None and gap.type == "up_gap" and float(volume.iloc[t]) > float(volume.iloc[t1]):
            add(
                "03", "跳空過壓帶量缺口",
                f"向上跳空缺口({gap.lower_edge:.2f}~{gap.upper_edge:.2f})且成交量較前一日放大",
                BASIS_SIMPLIFIED,
            )

    # 04 階梯橫盤量縮突破——重用R-CANDLE-04(已接線)，只取「突破」方向
    if _any_note_contains(notes, "R-CANDLE-04", "突破盤整區上緣"):
        add("04", "階梯橫盤量縮突破", notes["R-CANDLE-04"][0], BASIS_EXISTING_RULE)

    # 05 均線撐盤拉回量縮——重用R-SR-15(已接線)
    if "R-SR-15" in notes:
        add("05", "均線撐盤拉回量縮", notes["R-SR-15"][0], BASIS_EXISTING_RULE)

    # 06 高檔爆量長下影線(洗盤後續漲)——本專案規則庫缺口，簡化版：高檔＋長下影線K棒＋
    # 爆大量＋收盤站回接近平盤(跟低點有明顯距離)
    if bool(is_big_volume_vs_ma5(volume, ma5_vol).iloc[t]):
        body_t = float(body.iloc[t]) or 0.01
        if float(lower_shadow.iloc[t]) >= 2 * body_t and float(close.iloc[t]) > float(low.iloc[t]) + float(lower_shadow.iloc[t]) * 0.6:
            add(
                "06", "高檔爆量長下影線(洗盤後續漲)",
                "爆大量長下影線，尾盤強拉回，疑似主力洗盤而非出貨", BASIS_SIMPLIFIED,
            )

    # 07 價平量縮凹洞量底——簡化版：近5日股價幾乎持平，今天成交量是近20日最低
    if len(volume) >= 20:
        recent_close = close.iloc[-5:]
        is_flat = (recent_close.max() - recent_close.min()) / recent_close.mean() < 0.03 if recent_close.mean() else False
        if is_flat and volume.iloc[t] == volume.iloc[-20:].min():
            add("07", "價平量縮凹洞量底", "近5日股價幾乎持平，今日成交量創近20日新低(凹洞量)", BASIS_SIMPLIFIED)

    # 08 連黑洗盤價量微增——簡化版：前3-4天連續黑K且量沒放大，今天第一根「價漲量增」紅K
    if len(close) >= 5:
        black_streak = 0
        for j in range(t1, t1 - 4, -1):
            if bool(is_black.iloc[j]) and float(volume.iloc[j]) <= float(ma5_vol.iloc[j] or 0):
                black_streak += 1
            else:
                break
        if black_streak >= 3 and bool(is_red.iloc[t]) and float(close.iloc[t]) > float(close.iloc[t1]) and float(volume.iloc[t]) > float(volume.iloc[t1]):
            add(
                "08", "連黑洗盤價量微增",
                f"連續{black_streak}根量縮黑K後，出現價漲量增紅K，疑似修正結束", BASIS_SIMPLIFIED,
            )

    # 09 高檔爆量長黑倒貨——重用R-CLASSIC-01(已接線)
    if "R-CLASSIC-01" in notes:
        add("09", "高檔爆量長黑倒貨", notes["R-CLASSIC-01"][0], BASIS_EXISTING_RULE)

    # 10 跌破均線帶量長黑——簡化版：收盤跌破下彎的月線，且是帶量長黑K
    if str(ma20_dir.iloc[t]) == "下彎" and float(close.iloc[t]) < float(ma20.iloc[t]) and float(close.iloc[t1]) >= float(ma20.iloc[t1]):
        if bool(is_mid_black.iloc[t]) and bool(is_big_volume_vs_ma5(volume, ma5_vol).iloc[t]):
            add("10", "跌破均線帶量長黑", "收盤跌破下彎的月線(20MA)，且為帶量長黑K", BASIS_SIMPLIFIED)

    # 11 高檔帶量長上影線——重用R-CANDLE-05(已接線，反轉K棒幾何)
    if _any_note_contains(notes, "R-CANDLE-05", "十字線"):
        add("11", "高檔帶量長上影線", notes["R-CANDLE-05"][0], BASIS_EXISTING_RULE)

    # 12 破底無量陰跌不止——本專案規則庫缺口，簡化版：跌破近60日低點，且量能持續萎縮
    if len(close) >= 61:
        prior_low = float(close.iloc[-61:-1].min())
        if float(close.iloc[t]) < prior_low and float(volume.iloc[t]) < float(ma5_vol.iloc[t] or float("inf")):
            add(
                "12", "破底無量陰跌不止",
                "股價跌破近60日低點，但成交量未放大反而萎縮，市場乏人承接", BASIS_SIMPLIFIED,
            )

    # 13 價跌量增多殺多——重用R-TREND-13概念：連續下跌且量增
    if len(close) >= 3:
        down2 = float(close.iloc[t]) < float(close.iloc[t1]) < float(close.iloc[-3])
        vol_rising = float(volume.iloc[t]) > float(volume.iloc[t1]) > float(volume.iloc[-3])
        if down2 and vol_rising and bool(is_black.iloc[t]):
            add("13", "價跌量增多殺多", "連續下跌且成交量一天比一天大，加速趕底", BASIS_SIMPLIFIED)

    # 14 反彈無量長上影線——簡化版：空頭格局中小幅反彈但爆長上影線、量能低迷
    if trend_today == "空頭" and float(close.iloc[t]) > float(close.iloc[t1]):
        body_t = float(body.iloc[t]) or 0.01
        if float(upper_shadow.iloc[t]) >= 2 * body_t and float(volume.iloc[t]) < float(ma5_vol.iloc[t] or float("inf")):
            add("14", "反彈無量長上影線", "空頭格局中無量反彈，收長上影線，追價意願低", BASIS_SIMPLIFIED)

    # 15 反彈觸及均線量縮——重用R-SR-16(已接線)
    if "R-SR-16" in notes:
        add("15", "反彈觸及均線量縮", notes["R-SR-16"][0], BASIS_EXISTING_RULE)

    # 16 低檔無量平台向下——重用R-CANDLE-04(已接線)，只取「跌破」方向
    if _any_note_contains(notes, "R-CANDLE-04", "跌破盤整區下緣"):
        add("16", "低檔無量平台向下", notes["R-CANDLE-04"][0], BASIS_EXISTING_RULE)

    # 17 跌深低檔爆量長紅——重用R-CLASSIC-16(已接線)
    if "R-CLASSIC-16" in notes:
        add("17", "跌深低檔爆量長紅", notes["R-CLASSIC-16"][0], BASIS_EXISTING_RULE)

    # 18 低檔價穩量縮極致——簡化版：低檔+近5日多為十字線/量能持續萎縮
    if len(volume) >= 20:
        doji_ratio = float(is_doji(open_, close).iloc[-5:].mean())
        if doji_ratio >= 0.4 and volume.iloc[t] <= volume.iloc[-20:].quantile(0.1):
            add("18", "低檔價穩量縮極致", "近期多收十字線，成交量縮到窒息量等級", BASIS_SIMPLIFIED)

    # 19 打底完成帶量突破——重用R-CLASSIC-28(已接線，對應W底)
    if "R-CLASSIC-28" in notes:
        add("19", "打底完成帶量突破", notes["R-CLASSIC-28"][0], BASIS_EXISTING_RULE)

    # 20 低檔帶量長下影線——重用R-CLASSIC-26(已接線)
    if "R-CLASSIC-26" in notes:
        add("20", "低檔帶量長下影線", notes["R-CLASSIC-26"][0], BASIS_EXISTING_RULE)

    # 21 底部破底翻(假跌破)——簡化版：今天跌破近20日低點，隔天(即"今天"自己)無法適用
    # 「隔天」語意，改成看「昨天破底、今天帶量長紅收復」
    if len(close) >= 21:
        prior_low_21 = float(close.iloc[-21:-1].min())
        broke_yesterday = float(close.iloc[t1]) < prior_low_21
        if broke_yesterday and bool(is_mid_red.iloc[t]) and float(close.iloc[t]) > prior_low_21 and bool(is_big_volume_vs_ma5(volume, ma5_vol).iloc[t]):
            add(
                "21", "底部破底翻(假跌破)",
                f"昨日跌破近期低點{prior_low_21:.2f}，今日帶量長紅收復並站回支撐之上", BASIS_SIMPLIFIED,
            )

    # 22 底部出水芙蓉紅K——重用R-CLASSIC-30/R-MA-17(已接線)，跟01同一組判斷結果
    if "R-CLASSIC-30" in notes:
        add("22", "底部出水芙蓉紅K", notes["R-CLASSIC-30"][0], BASIS_EXISTING_RULE)

    # 23 利空不跌量大收紅——本專案沒有新聞/消息面資料，只能判斷「開低走高帶量收紅」
    # 這個技術現象，無法真正確認是否為利空消息造成
    if float(open_.iloc[t]) < float(close.iloc[t1]) and bool(is_red.iloc[t]) and bool(is_big_volume_vs_ma5(volume, ma5_vol).iloc[t]):
        add(
            "23", "利空不跌量大收紅",
            "開盤走低但爆量收紅K(僅能判斷技術現象，本專案沒有新聞/消息面資料，無法確認是否對應利空消息)",
            BASIS_SIMPLIFIED,
        )

    # 24 凹洞量後溫和遞增——延續07號的凹洞量偵測，額外看後續量能是否連續遞增
    if len(volume) >= 25:
        window = volume.iloc[-5:]
        if window.is_monotonic_increasing and float(close.iloc[t]) >= float(close.iloc[-5]):
            add("24", "凹洞量後溫和遞增", "近5日成交量連續溫和放大，股價同步緩步墊高", BASIS_SIMPLIFIED)

    # 25 高檔價平爆量換手——簡化版：近5日股價幾乎持平+近5日均量遠高於前20日均量。
    # ⚠️不能像其餘型態一樣逐日跟「自己的」rolling MA5比(連續多日爆量時，MA5會被
    # 這些爆量的日子自己拉高，5天內幾乎不可能每天都還維持2倍差距)，改成用一段
    # 較長的基準期(前20日)比較，避免窗口自我重疊導致條件實質上不可能成立。
    if len(close) >= 25:
        recent_close_25 = close.iloc[-5:]
        is_flat_25 = (recent_close_25.max() - recent_close_25.min()) / recent_close_25.mean() < 0.03 if recent_close_25.mean() else False
        recent_vol_avg = float(volume.iloc[-5:].mean())
        baseline_vol_avg = float(volume.iloc[-25:-5].mean())
        if is_flat_25 and baseline_vol_avg > 0 and recent_vol_avg >= 1.5 * baseline_vol_avg:
            add(
                "25", "高檔價平爆量換手",
                "高檔區間橫盤不漲不跌，但近5日均量遠高於前期水準，籌碼持續換手", BASIS_SIMPLIFIED,
            )

    # 26 高檔連黑K量能遞增——本專案規則庫缺口
    if len(close) >= 3:
        black3 = bool(is_black.iloc[-3:].all())
        vol_increasing = float(volume.iloc[t]) > float(volume.iloc[t1]) > float(volume.iloc[-3])
        if black3 and vol_increasing:
            add(
                "26", "高檔連黑K量能遞增",
                "連續3根以上黑K且成交量逐日遞增，賣方力道持續增強", BASIS_SIMPLIFIED,
            )

    # 27 跳空開高爆量收黑——重用R-CLASSIC-07(已接線，條件較嚴格)
    if "R-CLASSIC-07" in notes:
        add("27", "跳空開高爆量收黑", notes["R-CLASSIC-07"][0], BASIS_EXISTING_RULE)

    # 28 高檔十字線爆巨量——重用R-CANDLE-05(已接線)搭配爆量
    if _any_note_contains(notes, "R-CANDLE-05", "十字線") and bool(is_big_volume_vs_ma5(volume, ma5_vol).iloc[t]):
        add("28", "高檔十字線爆巨量", "高檔十字線反轉K棒且成交量爆出波段新高", BASIS_EXISTING_RULE)

    # 29 高檔量價背離推升——簡化版：股價創近20日新高，成交量卻是近5日均量遞減
    if len(close) >= 20:
        is_new_high = float(close.iloc[t]) >= float(close.iloc[-20:].max())
        vol_shrinking = float(ma5_vol.iloc[t]) < float(ma5_vol.iloc[t1]) if pd.notna(ma5_vol.iloc[t1]) else False
        if is_new_high and vol_shrinking:
            add("29", "高檔量價背離推升", "股價創近20日新高，但5日均量同步遞減，價量背離", BASIS_SIMPLIFIED)

    # 30 高檔假突破外包黑K——重用R-CLASSIC-01(已接線，內部已透過R-CANDLE-10高檔長黑吞噬判斷)
    if _any_note_contains(notes, "R-CLASSIC-01", "吞噬"):
        add("30", "高檔假突破外包黑K", notes["R-CLASSIC-01"][0], BASIS_EXISTING_RULE)

    return results
