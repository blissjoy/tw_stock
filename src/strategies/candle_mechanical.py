"""K棒型態分類（Layer 3）：機械化多頭／空頭K線交易規則（R-CANDLE-32/33）。

以「前一日高低點」為唯一比較基準的逐日狀態機，不依賴任何K線型態辨識，是全書最接近可
直接程式化的規則。兩套規則本質上互為鏡射，這裡用一個共用的核心函式＋方向參數統一實作，
另外提供 R-MA-21 統一停損邏輯所沒有的「固定7%停損上限」寫死在此，因為書中原文明訂
「不能超過7%」是本規則獨有的數字，不是均線戰法那套5%分界法。
"""

from __future__ import annotations

import pandas as pd

from src.rule_registry import implements_rule


def _mechanical_state_machine(high: pd.Series, low: pd.Series, close: pd.Series, direction: str, stop_loss_pct: float) -> pd.DataFrame:
    n = len(close)
    state = ["空手"] * n
    entry_price: list[float | None] = [None] * n
    stop_loss: list[float | None] = [None] * n
    action: list[str | None] = [None] * n

    # ⚠️ 效能：改用numpy陣列而非pandas Series的.iloc[]逐格存取——screen_all_stocks()對
    # 每檔股票都各呼叫一次多頭+一次空頭(見daily_screener.py的screen_mechanical_long/
    # short)，2000多檔股票疊加起來，.iloc[]逐格存取的Python層開銷會主導掉「立即重新
    # 篩選」的執行時間。演算法完全不變，只是換一種存取底層資料的方式。
    close_arr = close.to_numpy()
    high_arr = high.to_numpy()
    low_arr = low.to_numpy()

    cur_state = "空手"
    cur_entry: float | None = None
    cur_stop: float | None = None

    for t in range(1, n):
        if cur_state == "空手":
            if direction == "long" and close_arr[t] > high_arr[t - 1]:
                cur_state = "持有多單"
                cur_entry = close_arr[t]
                cur_stop = max(low_arr[t], cur_entry * (1 - stop_loss_pct))
                action[t] = "進場"
            elif direction == "short" and close_arr[t] < low_arr[t - 1]:
                cur_state = "持有空單"
                cur_entry = close_arr[t]
                cur_stop = min(high_arr[t], cur_entry * (1 + stop_loss_pct))
                action[t] = "進場"
        elif cur_state == "持有多單":
            if close_arr[t] < cur_stop:
                cur_state, action[t] = "空手", "停損出場"
                cur_entry = cur_stop = None
            elif close_arr[t] < low_arr[t - 1]:
                cur_state, action[t] = "空手", "跌破前一日低點出場"
                cur_entry = cur_stop = None
        elif cur_state == "持有空單":
            if close_arr[t] > cur_stop:
                cur_state, action[t] = "空手", "停損出場"
                cur_entry = cur_stop = None
            elif close_arr[t] > high_arr[t - 1]:
                cur_state, action[t] = "空手", "突破前一日高點回補"
                cur_entry = cur_stop = None

        state[t] = cur_state
        entry_price[t] = cur_entry
        stop_loss[t] = cur_stop

    return pd.DataFrame({"state": state, "entry_price": entry_price, "stop_loss": stop_loss, "action": action}, index=close.index)


@implements_rule("R-CANDLE-32")
def mechanical_long_trading_rule(high: pd.Series, low: pd.Series, close: pd.Series, stop_loss_pct: float = 0.07) -> pd.DataFrame:
    """機械化多頭K線交易規則：收盤突破前一日高點進場；跌破前一日低點或觸及7%停損出場。"""
    return _mechanical_state_machine(high, low, close, "long", stop_loss_pct)


@implements_rule("R-CANDLE-33")
def mechanical_short_trading_rule(high: pd.Series, low: pd.Series, close: pd.Series, stop_loss_pct: float = 0.07) -> pd.DataFrame:
    """機械化空頭K線交易規則：收盤跌破前一日低點放空；突破前一日高點或觸及7%停損回補，與多頭版鏡射對稱。"""
    return _mechanical_state_machine(high, low, close, "short", stop_loss_pct)
