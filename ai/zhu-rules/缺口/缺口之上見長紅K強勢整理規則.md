# 缺口之上見長紅K強勢整理規則

- **Rule ID**: R-GAP-17
- **名稱**: 缺口之上見長紅K強勢整理規則
- **分類**: 缺口
- **原文與頁碼**: P09-C5-缺口的進階應用.md（下冊 p.241，圖表9-5-1）
- **解讀**: 向上跳空缺口出現後，緊接著出現一根長紅K棒，是缺口力量疊加K線力量的多頭強力表態，比單純長紅K更強。核心持有條件：股價只要回跌**不破缺口「上沿價」**（即缺口上邊界，向上缺口=跳空當日最低價，見[[缺口基本定義與偵測規則]]），就維持「拉回做多」方向。橫盤強弱兩型態（引用[[大量長紅K的二分之一價支撐規則]]的二分之一價概念）：型態A——後續股價在「長紅K二分之一價」之上、「長紅K收盤價」之下的高處橫盤整理，判定為強勢整理，隨時可能再度上漲；型態B——後續股價直接在「長紅K收盤價」之上橫盤整理，判定為更強勢的整理型態，發動上漲時可以追價買進。時間參考：強勢橫盤整理時，最容易發動上漲的時間點落在橫盤第1、3、5、8、13日的**次日**（費波那契數列天數規律）。
- **可程式化**: 是
- **所需資料**: 缺口事件（向上）、緊接的長紅K棒OHLC（含二分之一價）、後續逐日收盤價與橫盤天數計數
- **計算公式**:
```
function detect_gap_with_long_red(gap, long_red_K, bars, i):
    if gap.type != "up_gap":
        return None
    if not is_long_red_candle(long_red_K):
        return None

    half_price = (long_red_K.High + long_red_K.Low) / 2
    result = {
        "category": "缺口之上見長紅K",
        "hold_condition": gap.upper_edge   # 缺口上沿價，不破則續多
    }

    consolidation_days = 0
    for j in range(i+1, len(bars)):
        if bars[j].Close < gap.upper_edge:
            result["signal"] = "跌破缺口上沿，拉回做多方向失效"
            break
        consolidation_days += 1
        if half_price <= bars[j].Close < long_red_K.Close:
            result["consolidation_type"] = "強勢整理型態A（二分之一價之上、收盤價之下）"
        elif bars[j].Close >= long_red_K.Close:
            result["consolidation_type"] = "強勢整理型態B（收盤價之上，可追價）"

        if consolidation_days in FIBONACCI_DAYS:   # {1,3,5,8,13}
            result["breakout_watch_day"] = f"第{consolidation_days+1}日（次日）為潛在發動時間點"

    return result
```
- **參數**: FIBONACCI_DAYS = {1, 3, 5, 8, 13}（書中明確給出）；「橫盤」的價格波動容許範圍書中未給精確門檻
- **可回測**: 是
- **信心**: 76/100 中高（持有條件、二分之一價分級、費波那契時間規律皆有明確文字與圖例；橫盤區間的精確波動容許度未給精確數值）
