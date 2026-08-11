"""逃命示警：使用者2026-08-11要求，把「個股分析」裡偏向「示警/賣出」性質的既有規則
另外挑出來，能在面板最上方用醒目樣式(前端決定紅色粗體)獨立標示，不用等使用者自己從
一長串規則列表裡找。這是「重新分類既有已接線規則＋改顯示方式」的curation層，不是
新規則——`ESCAPE_RULE_IDS`/`ESCAPE_RULE_KEYWORD_FILTER`裡列的每一條，都已經在
`src.screener.rule_scan.scan_golden_tier()`裡實際判斷過、有書籍依據，這裡只是額外
標記哪些屬於「逃命」性質，供UI從`analyze_stock_signals()`的既有輸出裡挑出來。

唯一例外：`detect_kd_death_cross()`。書中R-INDICATOR-09(KD黃金死亡交叉依趨勢判讀，
`kd_cross_signal_by_trend()`)要求死亡交叉要搭配當下趨勢才觸發，不是任何時候死亡
交叉都會被列出來；使用者2026-08-11明確要求另外獨立判斷「不管趨勢、只要死亡交叉就
示警」，這裡直接用`is_death_cross(K, D)`，不透過R-INDICATOR-09那個「依趨勢」的
wrapping邏輯。`ESCAPE_KD_DEATH_CROSS_RULE_ID`不是`ai/zhu-rules/`裡有書籍頁碼佐證
的正式Rule ID，是本專案「逃命示警」面板自己新增的補充判斷，呼叫端顯示時不應該假裝
它跟其他規則一樣有精確頁碼可查。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.crossovers import is_death_cross
from src.indicators.kd import compute_kd

ESCAPE_KD_DEATH_CROSS_RULE_ID = "R-ESCAPE-KD-DEATH-CROSS"

# 這些rule_id不管note文字內容是什麼，只要今天觸發過就一定是「示警/賣出」方向——各自
# 都只有單一方向的add()呼叫(跟R-CANDLE-13/R-INDICATOR-02/R-INDICATOR-22等對應的
# 「買進」版本是分開的rule_id)，可以直接用rule_id比對，不用檢查note文字。
ESCAPE_RULE_IDS: frozenset[str] = frozenset({
    "R-MA-14",        # 死亡交叉
    "R-TREND-04",     # 頭頭低底底低，空頭趨勢成立
    "R-TREND-09",     # 空頭趨勢改變先知先覺(多頭出現裂痕)
    "R-TREND-12",     # 多頭高檔爆量停利訊號
    "R-CANDLE-05",    # 高檔反轉K棒幾何(含長黑/長上引線/十字線等)
    "R-CLASSIC-01",   # 高檔大量長黑一日反轉
    "R-CLASSIC-02",   # 大量長黑破切反轉
    "R-CLASSIC-07",   # 高檔跳空黑K回檔反轉
    "R-CLASSIC-09",   # 反彈大量紅K跌破追空
    "R-CLASSIC-13",   # 多轉空破多底大跌
    "R-CLASSIC-15",   # 跌破下降軌道線大跌
    "R-INDICATOR-03", # MACD零軸下死亡交叉空頭格局判讀
    "R-INDICATOR-23", # 布林通道賣出訊號
    "R-SR-02",        # 跌破轉折低點，支撐轉壓力
    "R-SR-16",        # 空頭反彈四大關鍵壓力放空
    "R-LINE-11",      # 上升切線遭跌破，支撐轉壓力
    "R-LINE-15",      # 跌破下降軌道線
})

# 這些rule_id同時被拿來標記兩個相反方向(例如R-STRATEGY-07的口訣7"多轉空"/口訣8
# "空轉多"共用同一個rule_id，只有note文字不同)，要用note裡的關鍵字才能分辨是不是
# 「示警」方向，不能只看rule_id——關鍵字選詞見各自來源函式(src/strategies/
# operation_sop.py的bull_to_bear_reversal_signal()/src/indicators/macd.py的
# macd_trend_level_bullish_divergence()/src/indicators/volume_price.py的
# bear_decline_big_black_role())的回傳文字。
ESCAPE_RULE_KEYWORD_FILTER: dict[str, str] = {
    "R-STRATEGY-07": "大跌",   # "多頭完成反轉，預期初期出現大跌" vs "空頭完成反轉...大漲"
    "R-INDICATOR-07": "轉空",  # "...提示多轉空" vs "...提示空轉多"
    "R-VOLPRICE-06": "空方",   # "空方換手失敗，持續下跌" vs "多方力量轉強，反彈確認"
}


def is_escape_signal(rule_id: str, note: str | None) -> bool:
    """判斷`analyze_stock_signals()`輸出裡的一筆訊號是否屬於「逃命示警」方向。"""
    if rule_id in ESCAPE_RULE_IDS:
        return True
    keyword = ESCAPE_RULE_KEYWORD_FILTER.get(rule_id)
    if keyword is not None and note:
        return keyword in note
    return False


def detect_kd_death_cross(df: pd.DataFrame) -> bool:
    """今天是否發生KD死亡交叉(K下穿D)，不受趨勢限制。df需至少有high/low/close三欄，
    資料不足(<9天，KD最短暖身天數)回傳False。"""
    if len(df) < 9:
        return False
    kd_df = compute_kd(df["high"], df["low"], df["close"])
    return bool(is_death_cross(kd_df["K"], kd_df["D"]).iloc[-1])
