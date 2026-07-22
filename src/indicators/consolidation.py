"""K棒型態分類（Layer 1）：K線橫盤突破確認規則（R-CANDLE-04）。

依賴 Layer 0 的 candles.is_mid_long_red_candle / is_mid_long_black_candle。

橫盤定義：連續 >= min_bars 根K棒彼此都沒有突破/跌破這段區間目前的高低點
（區間會隨每根新K棒重新檢查，一旦某根K棒讓區間擴大，就從前一根K棒重新起算
新的橫盤區間）。確認向上突破需要「中長紅K」收盤價站上橫盤區間的上頸線；
確認向下跌破需要「中長黑K」收盤價跌破下頸線。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.candles import is_mid_long_black_candle, is_mid_long_red_candle
from src.rule_registry import implements_rule


@implements_rule("R-CANDLE-04")
def detect_consolidation(high: pd.Series, low: pd.Series, min_bars: int = 3) -> pd.DataFrame:
    """逐日計算橫盤狀態與上下頸線。

    回傳欄位：
    - is_consolidating：當下是否已形成 >= min_bars 根的橫盤區間
    - upper_neckline / lower_neckline：目前橫盤區間的上/下頸線（區間內最高/最低價）
    - group_len：目前橫盤區間累積的K棒根數
    """
    n = len(high)
    group_len = [1] * n
    upper = [float(high.iloc[0])] * n
    lower = [float(low.iloc[0])] * n

    for i in range(1, n):
        h, l = float(high.iloc[i]), float(low.iloc[i])
        if h <= upper[i - 1] and l >= lower[i - 1]:
            # 這根K棒沒有讓區間擴大，延續目前的橫盤區間
            group_len[i] = group_len[i - 1] + 1
            upper[i] = upper[i - 1]
            lower[i] = lower[i - 1]
        else:
            # 區間被擴大，從前一根K棒重新起算新的橫盤區間
            group_len[i] = 2
            upper[i] = max(h, float(high.iloc[i - 1]))
            lower[i] = min(l, float(low.iloc[i - 1]))

    result = pd.DataFrame(
        {
            "group_len": group_len,
            "upper_neckline": upper,
            "lower_neckline": lower,
        },
        index=high.index,
    )
    result["is_consolidating"] = result["group_len"] >= min_bars
    return result


@implements_rule("R-CANDLE-04")
def detect_consolidation_breakout(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, min_bars: int = 3
) -> pd.DataFrame:
    """在橫盤區間之上，判斷是否出現向上突破／向下跌破的確認訊號。

    突破/跌破的比較基準是「前一天已經確立」的橫盤區間（>= min_bars 根、且尚未被
    突破跌破的頸線），而不是把當天自己的高低價也算進區間之後再跟自己比較——
    後者會讓區間自動涵蓋當天，導致永遠測不到突破，是這類演算法最容易誤踩的陷阱。
    """
    box = detect_consolidation(high, low, min_bars=min_bars)
    prior_established = box["is_consolidating"].shift(1).fillna(False)
    prior_upper = box["upper_neckline"].shift(1)
    prior_lower = box["lower_neckline"].shift(1)

    mid_long_red = is_mid_long_red_candle(open_, close)
    mid_long_black = is_mid_long_black_candle(open_, close)

    box["breakout_up"] = prior_established & mid_long_red & (close > prior_upper)
    box["breakout_down"] = prior_established & mid_long_black & (close < prior_lower)
    return box
