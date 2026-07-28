"""SAR（Parabolic SAR，停損反轉點）：判斷多空翻轉，供「SAR翻轉」候選清單篩選器使用。

來源：`ref-project/tw_stock_analyzer/src/core/stock_scanner.py`的`calculate_sar()`函式
（該檔案內部把這個函式標註為「SAR - 通過驗證」）。這是該專案自行調整過的版本，跟教科書上
Wilder原始SAR演算法有三個明確差異，這裡照抄同一份邏輯（含差異），不是重新推導教科書版本：

1. 加速因子(AF)預設值改成start=0.03/inc=0.03/max_af=0.6（教科書常見預設是0.02/0.02/0.2），
   反轉更靈敏、但也更容易在盤整時來回翻轉——ref-project的`zhu_analysis.py`另有
   `sar_reliability_note()`提醒盤整區SAR訊號可信度較低，本模組不含這個提示，只搬移SAR
   數值/翻轉判斷本身。
2. 移除教科書版本「多頭SAR不得高於前兩日最低價／空頭SAR不得低於前兩日最高價」的箝制
   （ref-project原始碼裡這段用❌註解標記為刻意移除，不是漏寫）。
3. 每日最終「多頭/空頭」判斷不是迴圈內部追蹤的bull變數，而是事後用
   `sar當日數值 <= 當日最低價`重新逐日判斷（見`compute_sar`回傳的`sar_bull`），
   使用<=（含等於）而非嚴格小於。

這個模組跟`ai/zhu-rules/`（朱家泓書）、`ai/ebook-summary-chen/`（陳家豐書）兩份既有筆記都
無關，是第三個獨立來源，因此沒有掛`@implements_rule`（那個裝飾器只對應朱老師書的246條
規則庫，見`src/rule_registry.py`與`ai/zhu-rules/_manifest.json`），做法跟`margin_trading.py`／
`volume_washout.py`（陳家豐書的建構區塊）一致：只在docstring註明引用來源，不硬套規則ID。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_AF_START = 0.03
DEFAULT_AF_INC = 0.03
DEFAULT_AF_MAX = 0.6


def compute_sar(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    start: float = DEFAULT_AF_START,
    inc: float = DEFAULT_AF_INC,
    max_af: float = DEFAULT_AF_MAX,
) -> tuple[pd.Series, pd.Series]:
    """回傳(sar_bull, sar_values)：sar_bull是每天多頭(True)/空頭(False)的判斷、sar_values是
    SAR數值本身，index跟輸入的high/low一致。逐字對照`ref-project`的`calculate_sar()`演算法
    （見本檔案docstring列出的三點差異）。close參數目前實際上沒被用到——跟來源函式的參數列
    一致，只是保留簽章方便日後比對來源。

    資料筆數(n)少於2天時回傳兩個空Series；剛好2天時用第2天是否創高判斷多空、SAR數值留NaN
    (第一天SAR還沒有計算基礎)。
    """
    n = len(high)
    if n < 2:
        return pd.Series(dtype=bool), pd.Series(dtype=float, index=high.index)
    if n == 2:
        bull = bool(high.iloc[1] >= high.iloc[0])
        sar_values = pd.Series([np.nan, np.nan], index=high.index)
        return pd.Series([bull, bull], index=high.index), sar_values

    sar = np.zeros(n)
    hp = float(max(high.iloc[0], high.iloc[1]))
    lp = float(min(low.iloc[0], low.iloc[1]))
    bull = bool(high.iloc[1] >= high.iloc[0])
    if bull:
        sar[0] = sar[1] = lp
    else:
        sar[0] = sar[1] = hp
    af = start

    for i in range(2, n):
        current_high = float(high.iloc[i])
        current_low = float(low.iloc[i])
        prev_sar = sar[i - 1]
        if bull:
            sar_i = prev_sar + af * (hp - prev_sar)
            if current_low < sar_i:
                bull = False
                af = start
                sar[i] = max(hp, current_high)
                lp = current_low
            else:
                sar[i] = sar_i
                if current_high > hp:
                    hp = current_high
                    af = min(af + inc, max_af)
        else:
            sar_i = prev_sar + af * (lp - prev_sar)
            if current_high > sar_i:
                bull = True
                af = start
                sar[i] = min(lp, current_low)
                hp = current_high
            else:
                sar[i] = sar_i
                if current_low < lp:
                    lp = current_low
                    af = min(af + inc, max_af)

    sar_values = pd.Series(sar, index=high.index)
    sar_bull_list = [bool(sar_values.loc[idx] <= float(low.loc[idx])) for idx in sar_values.index]
    return pd.Series(sar_bull_list, index=high.index, dtype=bool), sar_values


def sar_flip_days_ago(sar_bull: pd.Series) -> int | None:
    """回傳「目前的多空狀態」是從幾天前翻轉過來的：1代表翻轉發生在最新一根K棒(也就是使用者
    說的「當天翻轉」)，2代表發生在前一根K棒，以此類推。找不到翻轉點(例如整段歷史都是同一個
    方向，或資料筆數不夠)回傳None。邏輯對照`ref-project`的`calculate_history_metrics()`裡
    `flip_day`那段迴圈：從最新一天往回找，第一個「跟目前狀態相同、但前一天不同」的位置。

    ⚠️ ref-project原始碼的中文註解寫「flip_day=1(昨天)」，但實際迴圈比對的是
    `sar_bull.iloc[-1]`(最新一天)本身是否剛好翻轉，也就是flip_day=1實際代表「翻轉發生在
    最新這一天」，不是字面上的「昨天」——這裡沿用原始碼的實際計算方式，不沿用可能誤植的
    中文註解字面意思。
    """
    n = len(sar_bull)
    if n < 2:
        return None
    current_is_bull = bool(sar_bull.iloc[-1])
    for i in range(1, n):
        if bool(sar_bull.iloc[-i]) == current_is_bull and bool(sar_bull.iloc[-i - 1]) != current_is_bull:
            return i
    return None


def sar_flipped_within(sar_bull: pd.Series, direction: str, within_days: int = 1) -> bool:
    """判斷目前是否處於`direction`("多頭"或"空頭")狀態、且翻轉進入這個狀態發生在最近
    `within_days`天以內(含)。對照ref-project `ui/widgets/dashboard.py`的篩選邏輯：
    `flip_days is not None and flip_days <= params.SAR_FLIP_DAYS`，`sar_status == 'SAR 多頭'`
    (或空頭)。目前狀態跟`direction`不符、或翻轉天數超過`within_days`都回傳False。
    """
    if sar_bull.empty:
        return False
    wants_bull = direction == "多頭"
    if bool(sar_bull.iloc[-1]) != wants_bull:
        return False
    flip_days = sar_flip_days_ago(sar_bull)
    return flip_days is not None and flip_days <= within_days
