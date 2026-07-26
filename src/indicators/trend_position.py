"""趨勢位置模組（Layer 1）：逐日判斷「目前是否處於本波段的高檔／低檔」(is_at_high/is_at_low)。

書中沒有給出「現在處於波段的哪個百分位」這種量化公式——打底/初升段/主升段/末升段(及鏡射
的做頭/初跌段/主跌段/末跌段，見R-SCREEN-04「六六大順選股法」的「②位置」構面)在書中都是
純文字定性描述，書中原文自己也承認需要「簡化為有限狀態集合」才能程式化。這裡採用的操作型
定義：從最近一次「已確認」的轉折點(頭或底)算起，目前這一段走勢的漲跌幅是否達到R-TREND-18
書中明訂的「波段須達10%~15%以上才算有效波段」門檻(取下限10%)，且今天的收盤是否貼近這段
走勢目前為止的極值(容忍帶5%，書中未給精確數字，工程估計值)。

跟`src/indicators/trend.py`的`_daily_trend_state()`一樣，刻意不呼叫`pivots.compute_
turning_points()`把整段序列一次算完再事後比對日期，而是重現同一套「以SMA(n)區分正/負
波段、翻轉時取波段極值確認頭/底」的狀態機，逐日就地判斷——原因也完全相同：一次算完再
比對日期會讓「轉折點被確認的時間點」跟「頭/底本身發生的日期」混淆，造成look-ahead。

有一個刻意的設計決定：翻轉當天(例如波段高檔出現一根長黑K，讓SMA(n)由正轉負)仍然用
「翻轉前」的狀態判斷今天的高低檔位置，不是翻轉後的新狀態——書中的「高檔反轉K棒」型態
(R-CANDLE-06/08/09/10/11等)本質上就是「在高檔當天出現的反轉K」，如果用翻轉後的狀態
判斷，反轉當天自己反而會被判定成「已經不在高檔」，這樣任何高檔反轉型態都不可能被抓到，
邏輯上自相矛盾。
"""

from __future__ import annotations

import pandas as pd

from src.indicators.moving_average import sma

MIN_SWING_PCT = 0.10  # R-TREND-18書中「波段漲跌幅須達10%~15%以上才算有效波段」，取下限10%
ZONE_TOLERANCE = 0.10  # 「貼近本波段極值」的容忍帶，書中未給精確數字，工程估計值；跟
# MIN_SWING_PCT取同一個10%數字，理由：多根K線反轉型態(如夜星/晨星)的最後一根確認K棒
# 本身可能就是長黑/長紅(實體跌漲幅可達6.5%以上，見candles.py的LONG_BODY_PCT)，容忍帶
# 至少要能涵蓋這種完成反轉當天自然產生的價格波動，否則反轉型態的完成日反而會被誤判成
# 「已經不在高檔/低檔」，跟is_at_high/is_at_low原本要服務的「反轉發生在高檔」判斷矛盾。


def compute_trend_position(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 5) -> pd.DataFrame:
    """逐日計算是否處於「本波段高檔」(is_at_high)／「本波段低檔」(is_at_low)。

    回傳欄位：
    - is_at_high／is_at_low：布林，兩者互斥(同一天只會處於「上升中的一段」或「下降中的
      一段」其中之一，不會同時成立)。
    - swing_pct：目前這段走勢從波段起點算起的漲跌幅(絕對值)，未達門檻或資料不足時為0。
    """
    ma = sma(close, n)
    is_at_high = pd.Series(False, index=close.index)
    is_at_low = pd.Series(False, index=close.index)
    swing_pct = pd.Series(0.0, index=close.index)

    heads: list[float] = []
    bottoms: list[float] = []
    state: str | None = None
    running_extreme: float | None = None

    valid_start = ma.first_valid_index()
    if valid_start is None:
        return pd.DataFrame({"is_at_high": is_at_high, "is_at_low": is_at_low, "swing_pct": swing_pct})
    start_pos = close.index.get_indexer([valid_start])[0]

    for i in range(start_pos, len(close)):
        if close.iloc[i] > ma.iloc[i]:
            cur = "positive"
        elif close.iloc[i] < ma.iloc[i]:
            cur = "negative"
        else:
            cur = state

        if state is None:
            state = cur
            running_extreme = float(high.iloc[i]) if cur == "positive" else float(low.iloc[i])
            check_state, extreme_for_check = state, running_extreme
        elif cur == state:
            if cur == "positive":
                running_extreme = max(running_extreme, float(high.iloc[i]))
            else:
                running_extreme = min(running_extreme, float(low.iloc[i]))
            check_state, extreme_for_check = state, running_extreme
        else:
            # 翻轉當天的K棒仍算進「剛結束的那一段」一起比較極值(跟_daily_trend_state()同一個
            # 慣例：翻轉當天可能就是那一段走勢的最高/最低點，例如長黑外側反轉)，且今天的
            # 高低檔位置判斷沿用「翻轉前」的狀態(check_state)，理由見模組docstring。
            check_state = state
            if state == "positive" and cur == "negative":
                extreme_for_check = max(running_extreme, float(high.iloc[i]))
                heads.append(extreme_for_check)
            else:
                extreme_for_check = min(running_extreme, float(low.iloc[i]))
                bottoms.append(extreme_for_check)
            state = cur
            running_extreme = float(high.iloc[i]) if cur == "positive" else float(low.iloc[i])

        if check_state == "positive" and bottoms:
            anchor = bottoms[-1]
            pct = (extreme_for_check - anchor) / anchor if anchor else 0.0
            if pct >= MIN_SWING_PCT and close.iloc[i] >= extreme_for_check * (1 - ZONE_TOLERANCE):
                is_at_high.iloc[i] = True
                swing_pct.iloc[i] = pct
        elif check_state == "negative" and heads:
            anchor = heads[-1]
            pct = (anchor - extreme_for_check) / anchor if anchor else 0.0
            if pct >= MIN_SWING_PCT and close.iloc[i] <= extreme_for_check * (1 + ZONE_TOLERANCE):
                is_at_low.iloc[i] = True
                swing_pct.iloc[i] = pct

    return pd.DataFrame({"is_at_high": is_at_high, "is_at_low": is_at_low, "swing_pct": swing_pct})
