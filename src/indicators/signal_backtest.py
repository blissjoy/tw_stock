"""通用訊號回測工具：給定一組觸發訊號的日期與對應股價序列，計算「訊號觸發後N天的
遠期報酬統計」——操作化陳家豐「股價表態仲裁元原則」(R-CHIP-14)：訊號的可信度應該用
「歷史上跟隨這個訊號的勝率」衡量，而不是預設某類法人/主力/規則天生更可信。

來源：陳家豐《看懂籌碼 股市賺大錢》第5篇第3章「破解主力手法 狡兔三窟操控」
(筆記見`ai/ebook-summary-chen/P05-C3-破解主力手法狡兔三窟操控.md`，規則檔見
`ai/chen-rules/籌碼面/股價表態仲裁元原則.md`，R-CHIP-14)。書中原文核心：「當籌碼訊號
矛盾時，不糾結誰更可信，看事後股價實際走向，跟隨贏的一方」。

這裡把這個原則抽象成一個不限於籌碼訊號、本專案任何規則(R-CHIP-*/R-SCREEN-*/
R-TREND-*等)都能套用的通用回測工具：只要有「哪些日期觸發了訊號」跟「股價序列」，
就能算出「觸發後N天平均報酬率/勝率」，用實證數字補強書中「信心」欄位目前偏主觀的
評分。`forward_return_stats()`是這批工作裡2026-08-09第一個實際使用它的地方——
`scripts/validate_limit_up_next_day_stats.py`拿它驗證R-CHIP-15。

⚠️`arbitrate()`是「兩個矛盾訊號當下哪個對」的仲裁函式，但目前本專案的訊號彼此獨立
顯示、沒有明確的「訊號衝突偵測」機制（例如同一檔股票今天同時觸發多頭跟空頭訊號才
需要仲裁），這部分還停留在概念層次，需要先決定要仲裁哪些既有訊號組合才能真正接上
即時UI，見規則檔的「已知限制」。
"""

from __future__ import annotations

import pandas as pd


def forward_return_stats(trigger_dates: list, close: pd.Series, horizon_days: int) -> dict:
    """trigger_dates：訊號觸發的日期清單(需能用來索引close.index)。close：以日期為
    index的收盤價序列(需已由舊到新排序)。horizon_days：觸發後往前看幾個交易日算報酬率。

    回傳{"n": 有效樣本數, "win_rate": float|None, "avg_return": float|None,
    "returns": [每筆的報酬率]}。觸發日在close序列末端horizon_days天內的樣本(沒有
    足夠未來資料可算報酬率)會被排除，不計入統計；trigger_date不在close.index裡的
    也會被排除。n=0時win_rate/avg_return為None——這代表「樣本不足以下結論」，不是
    「勝率0%」，呼叫端不應該把None當0處理。
    """
    dates = list(close.index)
    date_position = {d: i for i, d in enumerate(dates)}
    returns: list[float] = []
    for trigger_date in trigger_dates:
        pos = date_position.get(trigger_date)
        if pos is None or pos + horizon_days >= len(dates):
            continue
        start_price = close.iloc[pos]
        end_price = close.iloc[pos + horizon_days]
        if start_price == 0:
            continue
        returns.append((end_price - start_price) / start_price)

    if not returns:
        return {"n": 0, "win_rate": None, "avg_return": None, "returns": []}
    win_rate = sum(1 for r in returns if r > 0) / len(returns)
    avg_return = sum(returns) / len(returns)
    return {"n": len(returns), "win_rate": win_rate, "avg_return": avg_return, "returns": returns}


def arbitrate(direction_a: str, direction_b: str, future_return: float) -> str | None:
    """兩個方向相反的訊號("buy"/"sell")矛盾時，依事後報酬率判定哪一方"贏了"，回傳
    "buy"或"sell"。方向相同(不矛盾，不需要仲裁)回傳None。"""
    if direction_a == direction_b:
        return None
    winner_direction = "buy" if future_return > 0 else "sell"
    return direction_a if direction_a == winner_direction else direction_b
