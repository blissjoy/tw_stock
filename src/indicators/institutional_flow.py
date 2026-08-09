"""三大法人／投信／外資／自營商買賣超連續性判讀（Layer 0）：依「連續買超或賣超天數」
判斷停損觀察或可能的持續進場訊號。

來源①：朱家泓《抓住飆股輕鬆賺》淘汰法選股排除規則第8項（筆記見ai/zhu-rules/選股策略/
淘汰法選股排除規則.md，程式碼見src/screener/screening_rules.py的
institutional_sell_streak_or_black_k_cluster()，R-SCREEN-06）：「三大法人連續賣超要
避開」，既有實作明確門檻是連續3天——那個函式吃的是「已經算好的連續天數」這個int，沒有
從原始逐日買賣超資料算出這個天數的函式，這裡補上，供「個股明細」畫面顯示用。

來源②：陳家豐《看懂籌碼 股市賺大錢》第4篇第2章「風向球 投信動向幫抬轎」（筆記見
ai/ebook-summary-chen/P04-C2-風向球投信動向幫抬轎.md）本章重點第2點：「投信連續加碼
3、5天，且個股剛脫離下跌後的平盤整理，通常是最佳切入點」——這是買超方向、鎖定投信
這個分類的訊號，書中給的門檻是3~5天，這裡採用3天(下限)當觸發門檻。

⚠️ 陳家豐書中對三大法人各分類的可信度評價並不相同(第4篇第1、2章)：
- 自營商：操作週期短、忽買忽賣，書中建議「首先剔除」，不建議用連續性判斷趨勢。
- 外資：只有中小型股(非權值股)的買賣超才有參考價值，權值股/大型股受全球布局/期貨
  套利/指數調整干擾，不宜直接採信。
- 投信：法規限制(10%持股上限+單日買進不得超過成交量10%)造就「分批布局」的規律性，
  是全書認為最值得參考、法規數字最具體的一類。
這裡的函式只負責算出「連續買/賣超幾天」這個中性事實，不判斷可信度，呼叫端(desktop/
main_window.py)顯示結果時要附上對應分類的可信度提醒，不能讓使用者誤以為連續買超訊號
在任何分類、任何股票上都同樣可靠。
"""

from __future__ import annotations

import pandas as pd

INSTITUTIONAL_STREAK_THRESHOLD = 3  # 三大法人連續賣超警示：朱家泓淘汰法明確門檻(R-SCREEN-06)。
# 投信連續買超觀察：陳家豐書中給3~5天，這裡採下限3天，讓買賣兩個方向判斷邏輯一致，也
# 讓外資/自營商套用同一個函式時有一致的比較基準(可信度高低留給呼叫端的文字說明處理，
# 不是這裡用不同天數區分)。


def classify_flow_streak(net_values_desc: list[float]) -> dict:
    """net_values_desc: 由新到舊排序的每日買賣超淨額(買進-賣出)，index 0是今天。

    回傳{"direction": "buy"/"sell"/"flat"/None, "streak_days": int,
    "is_sell_warning": bool, "is_buy_watch": bool}——direction為None代表沒有資料；
    "flat"代表今天淨額剛好是0，streak_days為0；is_sell_warning/is_buy_watch是否
    達到INSTITUTIONAL_STREAK_THRESHOLD天門檻。
    """
    if not net_values_desc:
        return {"direction": None, "streak_days": 0, "is_sell_warning": False, "is_buy_watch": False}
    today = net_values_desc[0]
    if today == 0:
        return {"direction": "flat", "streak_days": 0, "is_sell_warning": False, "is_buy_watch": False}
    direction = "buy" if today > 0 else "sell"
    streak = 0
    for v in net_values_desc:
        if v == 0 or (v > 0) != (direction == "buy"):
            break
        streak += 1
    return {
        "direction": direction,
        "streak_days": streak,
        "is_sell_warning": direction == "sell" and streak >= INSTITUTIONAL_STREAK_THRESHOLD,
        "is_buy_watch": direction == "buy" and streak >= INSTITUTIONAL_STREAK_THRESHOLD,
    }


def flow_streak_series(net_asc: pd.Series) -> pd.Series:
    """net_asc：由舊到新排序的每日買賣超淨額。回傳每一天的「連續同方向天數」整數
    Series，跟classify_flow_streak()對「今天」算的邏輯一致(方向由當天淨額正負決定，
    淨額為0時streak重置為0)，但改用向量化算法一次算出整段歷史每一天的值，避免對長
    歷史逐日呼叫classify_flow_streak()造成O(n^2)開銷。

    2026-08-09新增，供`src/presentation/stock_detail_data.py`的`detect_chip_signal_
    conflict()`(R-CHIP-14訊號矛盾偵測)重建R-SCREEN-06／R-CHIP-01這兩條規則的完整
    歷史觸發日期用。
    """
    sign = net_asc.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    group_id = (sign != sign.shift()).cumsum()
    streak = sign.groupby(group_id).cumcount() + 1
    return streak.where(sign != 0, 0)
