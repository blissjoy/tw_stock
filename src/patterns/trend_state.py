"""趨勢狀態分類（Layer 1/2組裝）：串接R-TREND-01(轉折點取點演算法)＋R-TREND-03/04(頭頭高
底底高/頭頭低底底低多空趨勢判定)，算出「今天」屬於多頭／空頭／盤整的哪一種。

這是本專案至今唯一「逐日判斷現在算什麼趨勢」的地方——`src/indicators/`裡有大量函式的
docstring寫著需要「趨勢位置模組（尚未實作）」或「外部注入」的`trend`/`is_bull_trend`/
`is_bear_trend`參數，指的就是這裡；建好之後，`src/screener/rule_scan.py`才能進一步接上
一批原本被這個前置需求卡住的規則庫規則（KD依趨勢判讀、布林通道買訊①②/做空訊①②、
黃金死亡交叉配合主趨勢判讀等）。

⚠️ 這裡只解決「現在算多頭還是空頭」，不解決「現在處於多頭的哪個階段(起漲/主升段/末升段/
高檔)」這個更細的「趨勢位置」問題——後者是另一批規則(R-CANDLE-06/08/09/10/11等candle_
patterns_2.py的函式、R-VOLPRICE-03/04/09/10等)需要的`is_at_high`/`is_at_low`/
`wave_pattern_bullish`類參數，本模組不提供，維持排除在外。

⚠️ 2026-07-24第一次修正：一開始只用單一N=5(短線)判斷「目前趨勢」，被使用者指出「用幾天
資料判斷大趨勢太草率」——書中R-TREND-01原文明確定義了短/中/長三種天期(5日/10日/20日)，
同一套轉折點演算法只是套用不同均線天期，改成一次算出三種天期各自的趨勢狀態。

⚠️ 2026-07-24第二次修正：使用者馬上又指出「短/中/長是以日線/週線/月線來看MA，不是5/10/20
日」——查證後發現兩種定義都真的在書裡：R-TREND-01講的5/10/20日是「轉折波取點演算法」
本身的參數(同一張日線圖上，均線週期不同、轉折點的敏感度不同)；使用者記得的日/週/月線
則是R-INDICATOR-10「KD不同週期依交易期程選用」的定義(「做短線進出看日線、中期看週線、
長期看月線」，且書中舉的範例就是2330)——原文特別強調「此規則本身不改變KD計算公式…僅是
將同一套公式套用在不同時間週期(日/週/月)的K線資料上」。這裡改成套用R-INDICATOR-10的
精神：轉折波演算法本身的參數(N=5)不變，只把輸入資料換成日/週/月三種週期重新取樣
(resample)後的K棒——不是「換算法參數」，是「換輸入的K棒週期」，這樣短/中/長三個天期才
真正對應書中「日線/週線/月線」的原意，也符合使用者拿2330實測驗證過的直覺(2330用週線看
確實是多頭，用日線的5日轉折點看是空頭，兩者不衝突、只是天期不同)。

⚠️ 週線/月線需要足夠長的日線歷史才能重新取樣出夠多根K棒(例如只給120天日線資料，重新取樣
成月線只有4~5根，轉折點演算法(N=5)連基本的「找到2組頭與2組底」都不夠)，呼叫端(dashboard/
desktop的個股分析面板)因此改成額外抓一份更長期(見`src/presentation/chart_data.py`的
`TREND_LOOKBACK_DAYS`)的日線資料專門餵給這裡，不是沿用畫K線圖用的120天窗口——兩者用途
不同，不應該共用同一份裁切過的資料。

⚠️ 2026-07-24第三次修正：使用者拿2330實際結果反問「短線(日線)顯示空頭的依據是什麼？中線/
長線顯示盤整的依據又是什麼？我看起來中長線都是上升趨勢」——這正凸顯只顯示「多頭/空頭/
盤整」這個結論、不附任何依據，使用者沒有辦法自行核對演算法的判斷是否合理。改成
`TrendHorizonResult`新增`reason`欄位，把「最近兩個頭部/底部的實際價格與日期、以及頭頭高
低/底底高低的判讀」直接組成文字回傳，呼叫端(UI)可以原文顯示，讓使用者自己核對，不用只
相信一個結論字串。也把顯示標籤從「短線/中線/長線」改成使用者要求的「短期/中期/長期」
(避免跟R-TREND-01原本「短線轉折波=5日」的舊用法混淆——那是另一個已經不再使用的定義)。
"""

from __future__ import annotations

from typing import NamedTuple

import pandas as pd

from src.indicators.pivots import TurningPoint, compute_turning_points
from src.indicators.trend import is_bear_trend, is_bull_trend

TREND_BULL = "多頭"
TREND_BEAR = "空頭"
TREND_RANGE = "盤整"

# 轉折波取點演算法本身固定用N=5(R-TREND-01的基準參數，不隨天期改變——比照R-INDICATOR-10
# 「不改變公式本身，只換輸入的K棒週期」的做法)；短/中/長三種天期改成分別對應日/週/月線，
# 用pandas resample規則把日線OHLC重新取樣成週線/月線後再套用同一套演算法。key的順序即為
# 顯示順序(短→中→長)；tuple為(timeframe顯示標籤, resample規則)，resample規則為None代表
# 不需要重新取樣(短期本來就是日線)。
_HORIZONS: dict[str, tuple[str, str | None]] = {
    "短期": ("日線", None),
    "中期": ("週線", "W-FRI"),
    "長期": ("月線", "ME"),
}
TREND_TURNING_POINT_N = 5


class TrendHorizonResult(NamedTuple):
    timeframe: str   # 這個天期對應的K棒週期："日線"/"週線"/"月線"
    trend: str       # "多頭"/"空頭"/"盤整"
    reason: str      # 判斷依據：最近兩個頭部/底部的價格、日期與頭頭高低/底底高低的比較結果


def classify_trend_state(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 5) -> str:
    """回傳「今天」(傳入資料的最後一列)在「單一天期」下的趨勢狀態：多頭(頭頭高底底高)／
    空頭(頭頭低底底低)／盤整(兩者皆不成立，含轉折點不足2組頭與2組底的情況)。

    n是R-TREND-01轉折波取點演算法本身的參數(書中口訣「突破5日均取低點，跌破5均取高點」，
    預設5)，不是天期切換的機制——切換短/中/長天期改成由呼叫端傳入不同週期(日/週/月)的
    high/low/close資料，n維持不變(見`classify_trend_states_multi_horizon()`)。多數
    呼叫端應該改用那個函式一次拿到短/中/長三種天期的結果；這個單一天期版本保留給明確
    只需要某一種天期的呼叫端使用(例如`rule_scan.py`裡KD依趨勢判讀等既有規則，書中沒有
    另外要求區分短中長天期，固定用日線n=5即可)。要評估「某一天」的趨勢狀態，呼叫端要
    自行把high/low/close截到那一天為止——跟daily_screener.py裡各screen_*函式「今天=
    資料最後一列」的既有慣例相同。
    """
    turning_points = compute_turning_points(high, low, close, n=n)
    heads = [tp.price for tp in turning_points if tp.type == "head"]
    bottoms = [tp.price for tp in turning_points if tp.type == "bottom"]

    if is_bull_trend(heads, bottoms):
        return TREND_BULL
    if is_bear_trend(heads, bottoms):
        return TREND_BEAR
    return TREND_RANGE


def _fmt_turning_point(tp: TurningPoint) -> str:
    """把單一轉折點格式化成「價格@日期」，供判斷依據文字使用。"""
    try:
        date_str = pd.Timestamp(tp.index).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        date_str = str(tp.index)
    return f"{tp.price:.2f}@{date_str}"


def _describe_trend_basis(turning_points: list[TurningPoint]) -> str:
    """把is_bull_trend/is_bear_trend實際比較的「最近兩個頭部/最近兩個底部」組成使用者能自行
    核對的文字(價格＋日期＋頭頭高低/底底高低的判讀)，不是額外的判斷邏輯——只是把
    R-TREND-03/04已經在用的同一份比較結果格式化成看得懂的說明，讓UI能附上依據，不是只
    顯示一個「多頭/空頭/盤整」結論字串。轉折點不足2組頭與2組底時(is_bull_trend/
    is_bear_trend本身也會因此回傳False，判定成「盤整」)，直接說明資料不足，不是真的判斷
    出「盤整」這個技術含義。
    """
    heads = [tp for tp in turning_points if tp.type == "head"]
    bottoms = [tp for tp in turning_points if tp.type == "bottom"]
    if len(heads) < 2 or len(bottoms) < 2:
        return f"轉折點不足(頭部{len(heads)}組、底部{len(bottoms)}組，各需至少2組才能判斷)"

    head_prev, head_last = heads[-2], heads[-1]
    bottom_prev, bottom_last = bottoms[-2], bottoms[-1]
    if head_last.price > head_prev.price:
        head_word = "頭頭高"
    elif head_last.price < head_prev.price:
        head_word = "頭頭低"
    else:
        head_word = "頭頭平"
    if bottom_last.price > bottom_prev.price:
        bottom_word = "底底高"
    elif bottom_last.price < bottom_prev.price:
        bottom_word = "底底低"
    else:
        bottom_word = "底底平"
    return (
        f"最近兩個頭：{_fmt_turning_point(head_prev)}→{_fmt_turning_point(head_last)}（{head_word}）；"
        f"最近兩個底：{_fmt_turning_point(bottom_prev)}→{_fmt_turning_point(bottom_last)}（{bottom_word}）"
    )


def _resample_ohlc(high: pd.Series, low: pd.Series, close: pd.Series, rule: str | None) -> tuple[pd.Series, pd.Series, pd.Series]:
    """把日線high/low/close重新取樣成`rule`週期的K棒(high取區間最高、low取區間最低、
    close取區間最後一筆收盤)；rule為None時代表本來就是日線，原樣傳回。要求high/low/close
    有DatetimeIndex(`src/presentation/chart_data.py`的`load_price_history()`已經是
    這個慣例)。"""
    if rule is None:
        return high, low, close
    agg_high = high.resample(rule).max().dropna()
    agg_low = low.resample(rule).min().dropna()
    agg_close = close.resample(rule).last().dropna()
    return agg_high, agg_low, agg_close


def classify_trend_states_multi_horizon(
    high: pd.Series, low: pd.Series, close: pd.Series,
) -> dict[str, TrendHorizonResult]:
    """回傳{"短期": TrendHorizonResult(timeframe="日線", trend=..., reason=...),
    "中期": TrendHorizonResult(timeframe="週線", ...), "長期": TrendHorizonResult(timeframe=
    "月線", ...)}——依R-INDICATOR-10「做短線看日線、中期看週線、長期看月線」的定義，把同一套
    N=5轉折波演算法分別套用在日/週/月三種週期重新取樣後的K棒上。三者可能不一致(例如日線走
    短空、週線仍是多頭)，這正是分開判斷的意義所在——呼叫端(UI)應該三個都顯示，不要合併成
    一個籠統的「目前趨勢」。`reason`是給使用者核對用的判斷依據文字，見`_describe_trend_
    basis()`。

    ⚠️ 週線/月線需要足夠長的日線歷史才能取樣出夠多根K棒，資料不足時該天期會偏向回傳「盤整」
    (轉折點不足2組頭與2組底)，不代表真的盤整，呼叫端若用短窗口(例如只抓120天日線)資料
    餵進來要留意這點——見`chart_data.py`的`TREND_LOOKBACK_DAYS`。
    """
    result = {}
    for label, (timeframe, rule) in _HORIZONS.items():
        h, l, c = _resample_ohlc(high, low, close, rule)
        if len(c) > 0:
            trend = classify_trend_state(h, l, c, n=TREND_TURNING_POINT_N)
            turning_points = compute_turning_points(h, l, c, n=TREND_TURNING_POINT_N)
            reason = _describe_trend_basis(turning_points)
        else:
            trend, reason = TREND_RANGE, "資料不足，無法重新取樣出任何K棒"
        result[label] = TrendHorizonResult(timeframe=timeframe, trend=trend, reason=reason)
    return result
