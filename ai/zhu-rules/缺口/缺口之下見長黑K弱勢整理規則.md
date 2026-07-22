# 缺口之下見長黑K弱勢整理規則

- **Rule ID**: R-GAP-18
- **名稱**: 缺口之下見長黑K弱勢整理規則
- **分類**: 缺口
- **原文與頁碼**: P09-C5-缺口的進階應用.md（下冊 p.242，圖表9-5-2）
- **解讀**: 與[[缺口之上見長紅K強勢整理規則]]完全鏡射對稱。向下跳空缺口出現後，緊接著出現一根長黑K棒，是空頭強力表態的訊號。核心持有條件：股價只要反彈**不破缺口「下沿價」**（即缺口下邊界，向下缺口=跳空當日最高價），就維持「反彈做空」方向。橫盤弱勢兩型態（引用[[大量長黑K的二分之一價壓力規則]]的二分之一價概念）：型態A——後續股價在「長黑K二分之一價」之下、「長黑K收盤價」之上的低處橫盤整理，判定為弱勢整理，隨時可能再度下跌；型態B——後續股價直接在「長黑K收盤價」之下橫盤整理，判定為更弱勢的整理型態，發動下跌時可以追價放空。時間參考：弱勢橫盤整理時，最容易發動下跌的時間點同樣落在橫盤第1、3、5、8、13日的次日。書中範例顯示橫盤天數不影響「跌破後續跌」的有效性，僅影響蓄勢時間長短。
- **可程式化**: 是
- **所需資料**: 缺口事件（向下）、緊接的長黑K棒OHLC（含二分之一價）、後續逐日收盤價與橫盤天數計數
- **計算公式**:
```
function detect_gap_with_long_black(gap, long_black_K, bars, i):
    if gap.type != "down_gap":
        return None
    if not is_long_black_candle(long_black_K):
        return None

    half_price = (long_black_K.High + long_black_K.Low) / 2
    result = {
        "category": "缺口之下見長黑K",
        "hold_condition": gap.lower_edge   # 缺口下沿價，不破則續空
    }

    consolidation_days = 0
    for j in range(i+1, len(bars)):
        if bars[j].Close > gap.lower_edge:
            result["signal"] = "突破缺口下沿，反彈做空方向失效"
            break
        consolidation_days += 1
        if long_black_K.Close < bars[j].Close <= half_price:
            result["consolidation_type"] = "弱勢整理型態A（二分之一價之下、收盤價之上）"
        elif bars[j].Close <= long_black_K.Close:
            result["consolidation_type"] = "弱勢整理型態B（收盤價之下，可追價放空）"

        if consolidation_days in FIBONACCI_DAYS:   # {1,3,5,8,13}
            result["breakdown_watch_day"] = f"第{consolidation_days+1}日（次日）為潛在發動時間點"

    return result
```
- **參數**: FIBONACCI_DAYS = {1, 3, 5, 8, 13}（書中明確給出）；「橫盤」的價格波動容許範圍書中未給精確門檻
- **可回測**: 是
- **信心**: 76/100 中高（與長紅K版完全對稱，書中並以兩段對照範例驗證「橫盤天數不影響跌破後續跌有效性」；橫盤區間的精確波動容許度未給）
