# MACD零軸下黃金死亡交叉空頭格局判讀

- **Rule ID**: R-INDICATOR-03
- **名稱**: MACD零軸下黃金死亡交叉空頭格局判讀
- **分類**: 技術指標
- **原文與頁碼**: P08-C1-MACD指標中期走勢的研判.md，下冊 p.154
- **解讀**: 適用情境為DIF與MACD兩線皆位於0軸之下（空頭格局），與「[[MACD零軸上黃金死亡交叉多頭格局判讀]]」對稱。①黃金交叉之區分：兩線在0軸之下時，若DIF與MACD黃金交叉，視為空頭格局中的低檔訊號，可作空單回補；但反彈若未突破0軸，只能視為空頭趨勢中的反彈，不可視為轉多。②死亡交叉賣訊：兩線在0軸之下時，若DIF與MACD死亡交叉，為空頭賣出訊號，代表行情再次下跌。
- **可程式化**: 是
- **所需資料**: DIF序列、MACD（訊號線）序列
- **計算公式**:
```
zero_axis_bear = DIF(t) < 0 and MACD(t) < 0    # 兩線皆在0軸之下

golden_cross(t) = DIF(t-1) <= MACD(t-1) and DIF(t) > MACD(t)
dead_cross(t)   = DIF(t-1) >= MACD(t-1) and DIF(t) < MACD(t)

# 空頭格局黃金交叉：空單回補訊號（非轉多）
if zero_axis_bear and golden_cross(t):
    signal = "空單回補訊號"
    if DIF(t) < 0 and MACD(t) < 0:   # 反彈未站上0軸
        trend_status = "僅屬空頭反彈，空頭格局不變"
    else:
        trend_status = "站上0軸，格局轉為多頭，非單純反彈"

# 空頭格局死亡交叉：賣出／做空訊號
if zero_axis_bear and dead_cross(t):
    signal = "空方賣出（做空）訊號"
```
- **參數**: 無額外可調參數（依賴R-INDICATOR-01計算出的DIF與MACD序列）
- **可回測**: 是
- **信心**: 91/100 高（書中原文明確區分「0軸下黃金交叉但未站上0軸＝反彈非轉多」與「0軸下死亡交叉＝賣出訊號」，並附範例圖表佐證）
