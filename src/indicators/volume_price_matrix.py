"""12種多空量價終極矩陣與三維過濾法——「三維過濾法」(見`ai/q3-rules/量價矩陣/`，
R-Q3M-00~19)複製外部工具Antigravity儀表板的量價分類框架，把每天的Q1(價格方向)／
Q2(成交量方向)／Q3(基期位置)三個維度組合成19種具名矩陣格，供「個股資訊」的「三維
過濾法」分頁使用。詳細條件與對應既有規則庫見`ai/q3-rules/量價矩陣/00-三維過濾法
方法論與三個維度定義.md`，這裡是那份文件「計算公式」欄的正式實作，欄位命名/門檻
需跟文件保持一致，改動時要同步更新文件。

Q2門檻直接沿用R-VOLPRICE-01(成交量分類與倍數門檻定義)既有的1.2倍/0.5倍MA5門檻，
不是本檔新訂；Q3沿用`src.indicators.trend_position.compute_trend_position()`既有的
is_at_high/is_at_low二元判斷，本專案沒有「初升/主升/末升/末跌/初跌」等更細子階段
分類器，矩陣裡標註這些子階段的格子(01/02/03/04/06/07)只能程式化到「高檔/低檔」
這一層，見對應文件的「可程式化」欄。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

Q1_UP = "上漲"
Q1_DOWN = "下跌"
Q1_RANGE = "盤整"
Q1_BREAKOUT_UP = "關鍵點向上突破"
Q1_BREAKOUT_DOWN = "關鍵點向下跌破"

Q2_UP = "量增"
Q2_DOWN = "量縮"
Q2_FLAT = "量平"

Q3_HIGH = "高檔"
Q3_LOW = "低檔"
Q3_MID = "全基期"


def classify_q1_price(
    close: pd.Series, breakout_up: pd.Series | None = None, breakout_down: pd.Series | None = None,
) -> pd.Series:
    """Q1：價格方向，供12種矩陣分類用。breakout_up/breakout_down是「今天是否觸及一個
    明確技術關鍵價位」的既有旗標(例如R-CANDLE-04的detect_consolidation_breakout()、
    R-SR-01/02突破轉折高低點、R-LINE-11/12/14/15切線軌道突破)，呼叫端依情境自行傳入；
    傳入時「今天同時上漲又剛好突破關鍵價位」會被歸類成「關鍵點突破」而不是「上漲」，
    這是矩陣11-15格(「關鍵點+量增/量縮/量平」)需要的分類方式。

    ⚠️這個「突破優先於漲跌」的分類方式不適合拿來組「今日量價關係」這句話(見
    format_volume_price_relation())——2026-08-10使用者拿合晶(6182)實測發現：
    當天股價確實下跌，但同時觸發了盤整跌破，這裡回傳「關鍵點向下跌破」而不是
    「下跌」，導致format_volume_price_relation()因為Q1不是純粹的上漲/下跌而回傳
    None，跟PDF原文「價跌量增(背離)」的顯示對不上——PDF的「今日基礎量價關係」
    看起來是獨立於「12矩陣」計算的，不受是否同時發生突破事件影響。「今日量價
    關係」要用classify_price_direction_basic()，不要沿用這裡的結果。"""
    prev_close = close.shift(1)
    result = classify_price_direction_basic(close, prev_close)
    if breakout_up is not None:
        result[breakout_up.fillna(False)] = Q1_BREAKOUT_UP
    if breakout_down is not None:
        result[breakout_down.fillna(False)] = Q1_BREAKOUT_DOWN
    return result


def classify_price_direction_basic(close: pd.Series, prev_close: pd.Series | None = None) -> pd.Series:
    """單純的漲/跌/平三態，不考慮是否同時觸及關鍵價位——供「今日量價關係」
    (format_volume_price_relation())使用，跟classify_q1_price()是兩種不同用途的
    分類，不要混用(見classify_q1_price()的說明)。"""
    if prev_close is None:
        prev_close = close.shift(1)
    result = pd.Series(Q1_RANGE, index=close.index)
    result[close > prev_close] = Q1_UP
    result[close < prev_close] = Q1_DOWN
    return result


def classify_q2_volume(volume: pd.Series, ma5_volume: pd.Series) -> pd.Series:
    """Q2：成交量方向。門檻沿用R-VOLPRICE-01既有的1.2倍(量增)／0.5倍(量縮)MA5門檻。"""
    ratio = volume / ma5_volume
    result = pd.Series(Q2_FLAT, index=volume.index)
    result[ratio >= 1.2] = Q2_UP
    result[ratio <= 0.5] = Q2_DOWN
    result[ma5_volume.isna()] = None
    return result


def is_volume_price_divergence(q1: str | None, q2: str | None) -> bool:
    """今日量價背離：價格方向跟成交量方向「不一致」——價漲量縮或價跌量增。這是PDF
    原文「今日基礎量價關係」欄位的標記依據(例如「價漲量縮(背離)」)，2026-08-10
    使用者拿合晶(6182)的「價跌量增(背離)」實例發現我們系統雖然已經算出Q1/Q2(見
    classify_q1_price()/classify_q2_volume())，但顯示的「今日量價關係」欄位沒有
    附上這個「(背離)」標記，容易讓人誤以為系統判斷不出來——其實只是沒有把已經
    算好的Q1/Q2組合成這句話而已。價漲量增/價跌量縮視為「量價配合」，不標記；
    Q1是盤整/關鍵點突破或Q2是量平時，不適用「背離」這個二元對照，一律不標記。
    """
    return (q1 == Q1_UP and q2 == Q2_DOWN) or (q1 == Q1_DOWN and q2 == Q2_UP)


def format_volume_price_relation(q1: str | None, q2: str | None) -> str | None:
    """把Q1/Q2組成PDF原文「今日基礎量價關係」欄位同樣格式的一句話(例如「價漲量縮
    (背離)」)。Q1是盤整/關鍵點突破，或Q1/Q2任一為None(資料不足)時回傳None，
    這種情況PDF原文的「價漲/價跌」措辭本身就不適用。"""
    if q1 not in (Q1_UP, Q1_DOWN) or q2 is None:
        return None
    price_word = "價漲" if q1 == Q1_UP else "價跌"
    volume_word = {Q2_UP: "量增", Q2_DOWN: "量縮", Q2_FLAT: "量平"}.get(q2)
    if volume_word is None:
        return None
    label = f"{price_word}{volume_word}"
    if is_volume_price_divergence(q1, q2):
        label += "(背離)"
    return label


def classify_q3_position(is_at_high: pd.Series, is_at_low: pd.Series) -> pd.Series:
    """Q3：基期位置。沿用compute_trend_position()既有的is_at_high/is_at_low，只到
    「高檔/低檔/全基期」三態，不含PDF原文提到的初升/主升/末升/末跌/初跌等子階段。"""
    result = pd.Series(Q3_MID, index=is_at_high.index)
    result[is_at_high.fillna(False)] = Q3_HIGH
    result[is_at_low.fillna(False)] = Q3_LOW
    return result


@dataclass(frozen=True)
class MatrixRow:
    rule_id: str
    label: str
    interpretation: str
    # 2026-08-10新增：使用者反映「這格的判斷有簡化/侷限」不能只寫在ai/q3-rules/文件裡，
    # 要能直接顯示在畫面上——大多數矩陣格是None(Q1/Q2/Q3三軸都能完整程式化，沒有
    # 簡化問題)，只有PDF原文用到「初升/主升/末升/末跌/初跌段」等比「高檔/低檔」更細
    # 的子階段描述的格子才有值，說明本專案目前只能判斷到「高檔/低檔」這一層。
    caveat: str | None = None


_SUB_STAGE_CAVEAT = "PDF原文描述的是更細的子階段，本專案目前只有「高檔/低檔」二元判斷，程式化到這一層為止。"

# (Q1, Q2, Q3) -> 矩陣格；Q3為None代表該格不分高檔/低檔/全基期(關鍵點/量平系列)。
# 08/09(價跌量縮·多頭回檔期/空頭主跌段)不是用Q3(高檔/低檔)區分、是用當下多空趨勢
# 區分，另外用_resolve_pullback_rows()處理，不放進這個查表字典。
_MATRIX_TABLE: dict[tuple[str, str, str | None], MatrixRow] = {
    (Q1_UP, Q2_UP, Q3_LOW): MatrixRow("R-Q3M-01", "價漲量增·低檔初升段", "絕對續漲：主力進場吃貨，波段起漲訊號", _SUB_STAGE_CAVEAT),
    (Q1_UP, Q2_UP, Q3_HIGH): MatrixRow("R-Q3M-02", "價漲量增·高檔噴出段", "極度危險：可能是集體瘋狂末行情或主力最後誘多", _SUB_STAGE_CAVEAT),
    (Q1_UP, Q2_DOWN, Q3_LOW): MatrixRow("R-Q3M-03", "價漲量縮·低檔反彈段", "反彈無力：缺乏資金追價，隨時會再破底", _SUB_STAGE_CAVEAT),
    (Q1_UP, Q2_DOWN, Q3_HIGH): MatrixRow(
        "R-Q3M-04", "價漲量縮·高檔主升段", "主力鎖籌：籌碼在大戶手上，驚驚漲格局",
        _SUB_STAGE_CAVEAT + "⚠️另外這個解讀方向跟R-VOLPRICE-04(多頭上漲量價背離三型態)的警訊解讀可能相反，不能只看這一格就直接視為多頭訊號。",
    ),
    (Q1_UP, Q2_FLAT, Q3_MID): MatrixRow("R-Q3M-05", "價漲量平·全基期", "常態推進：多頭依循慣性緩步墊高"),
    (Q1_DOWN, Q2_UP, Q3_LOW): MatrixRow("R-Q3M-06", "價跌量增·低檔末跌段", "趕底止跌：恐慌盤爆發，大資金進場接盤", _SUB_STAGE_CAVEAT),
    (Q1_DOWN, Q2_UP, Q3_HIGH): MatrixRow("R-Q3M-07", "價跌量增·高檔初跌段", "轉空崩跌：主力不計成本帶頭逃跑", _SUB_STAGE_CAVEAT),
    (Q1_DOWN, Q2_FLAT, Q3_MID): MatrixRow("R-Q3M-10", "價跌量平·全基期", "慣性走弱：賣壓穩定大於買盤"),
    (Q1_BREAKOUT_UP, Q2_UP, Q3_MID): MatrixRow("R-Q3M-11", "關鍵點+量增·向上突破", "真突破：真金白銀突破壓力，後續大漲"),
    (Q1_BREAKOUT_DOWN, Q2_UP, Q3_MID): MatrixRow("R-Q3M-12", "關鍵點+量增·向下跌破", "真跌破：防線崩潰，後續大跌"),
    (Q1_BREAKOUT_UP, Q2_DOWN, Q3_MID): MatrixRow("R-Q3M-13", "關鍵點+量縮·向上突破", "假突破：虛胖突破，易形成穿頭破腳反轉"),
    (Q1_BREAKOUT_DOWN, Q2_DOWN, Q3_MID): MatrixRow("R-Q3M-14", "關鍵點+量縮·向下跌破", "假跌破：主力誘空洗盤，隨後常報復性上漲"),
    (Q1_BREAKOUT_UP, Q2_FLAT, Q3_MID): MatrixRow("R-Q3M-15", "關鍵點+量平·全基期", "需要時間確認：多空試探，需再觀察1-2根K棒"),
    (Q1_BREAKOUT_DOWN, Q2_FLAT, Q3_MID): MatrixRow("R-Q3M-15", "關鍵點+量平·全基期", "需要時間確認：多空試探，需再觀察1-2根K棒"),
    (Q1_RANGE, Q2_UP, Q3_HIGH): MatrixRow("R-Q3M-16", "盤整量增·高檔區", "高檔出貨：主力不斷轉手籌碼給散戶"),
    (Q1_RANGE, Q2_UP, Q3_LOW): MatrixRow("R-Q3M-17", "盤整量增·低檔區", "主力吸籌：大戶正在暗中吃貨"),
    (Q1_RANGE, Q2_DOWN, Q3_MID): MatrixRow("R-Q3M-18", "盤整量縮·全基期", "變盤前夕：波動度極低，隨後常有大缺口或大K棒破局"),
    (Q1_RANGE, Q2_FLAT, Q3_MID): MatrixRow("R-Q3M-19", "盤整量平·全基期", "冷門垃圾時間：缺乏催化劑，暫無操作價值"),
}

_PULLBACK_CAVEAT = "「多頭回檔期/空頭主跌段」不是「高檔/低檔」二元判斷，是靠當下的多空趨勢分類(短期日線)間接推定，不是精細的波段階段判斷。"
_MATRIX_08 = MatrixRow("R-Q3M-08", "價跌量縮·多頭回檔期", "良性修正：主力沒出貨只是洗盤，回測均線後通常再攻", _PULLBACK_CAVEAT)
_MATRIX_09 = MatrixRow("R-Q3M-09", "價跌量縮·空頭主跌段", "無量陰跌：市場失去人氣，股價將漫長探底", _PULLBACK_CAVEAT)


def classify_matrix_row(q1: str | None, q2: str | None, q3: str | None, trend_today: str | None = None) -> MatrixRow | None:
    """依單一天的Q1/Q2/Q3(+可選的trend_today，用來解「價跌量縮」這一格屬於多頭回檔還是
    空頭主跌)查出對應矩陣格，查無對應(理論上不該發生，19格應已覆蓋Q1×Q2的所有組合，除了
    「盤整+關鍵點」這種PDF原文本來就沒有定義的無意義組合)回傳None。"""
    if q1 is None or q2 is None:
        return None
    if q1 == Q1_DOWN and q2 == Q2_DOWN:
        if trend_today == "多頭":
            return _MATRIX_08
        if trend_today == "空頭":
            return _MATRIX_09
        return None
    key_with_q3 = (q1, q2, q3)
    if key_with_q3 in _MATRIX_TABLE:
        return _MATRIX_TABLE[key_with_q3]
    key_mid = (q1, q2, Q3_MID)
    return _MATRIX_TABLE.get(key_mid)
