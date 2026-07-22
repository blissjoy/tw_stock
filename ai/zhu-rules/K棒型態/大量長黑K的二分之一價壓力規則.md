# 大量長黑K的二分之一價壓力規則

- **Rule ID**: R-CANDLE-03
- **名稱**: 大量長黑K的二分之一價壓力規則
- **分類**: K棒型態
- **原文與頁碼**: P03-C1-K線起源與基本概念.md（上冊 p.150-153；原書3-1節「大量長黑K線日後的支撐與壓力」）
- **解讀**: 與R-CANDLE-02完全鏡射對稱的邏輯。空頭下跌行進中出現長黑K，股價繼續下跌且無止跌訊號時不能低接。若出現「帶大量」的長黑K且位於相對高檔或低檔，該長黑K的最低價、二分之一價、最高價會成為日後3個由強到弱的壓力價位。股價反彈測試時：突破最低價代表向下力道減弱、向上力道轉強，須注意是否轉折向上反彈；突破二分之一價代表放空平均成本已被突破，容易引發大量回補買單，空方氣勢可能被扭轉；突破最高價則代表多空易位，該長黑K反而變成日後上漲的重要支撐觀察點。連續出現下跌長黑K後若出現爆量或窒息量，容易產生反彈，當上漲紅K收盤價站過前一日高點時可短線搶反彈。
- **可程式化**: 是
- **所需資料**: 該長黑K棒的開盤價、收盤價、最高價、最低價、成交量、近N日均量、後續K棒的收盤價、股價相對高低檔位置
- **計算公式**:
```
half_price = (high_bigblack + low_bigblack) / 2
is_big_volume = volume_bigblack > K * avg_volume(N)
is_relative_high_or_low = 位於近期相對高檔或低檔

if is_long_black_candle(bigblack) and is_big_volume and is_relative_high_or_low:
    壓力分3層（由強至弱）:
        Tier1_最強壓力 = low_bigblack
        Tier2_平均成本壓力 = half_price
        Tier3_最弱壓力 = high_bigblack

    後續紅K測試反應（逐日檢查）:
        if close_test > Tier1 and close_test <= Tier2:
            status = "向下力道減弱，注意是否轉折向上反彈"
        elif close_test > Tier2 and close_test <= Tier3:
            status = "突破放空平均成本，容易產生大量回補買單，空方氣勢轉弱"
        elif close_test > Tier3:
            status = "突破最高點，多空易位，該長黑K轉為日後重要支撐"

    連續長黑K後判斷:
        if (爆量 or 窒息量) and close[隔日] > high[前一日]:
            status = "短線搶反彈訊號"
```
- **參數**: K=大量倍數門檻（建議預設1.5～2倍N日均量）、N=均量計算天數（預設5或20日）、「相對高檔/低檔」判斷方式
- **可回測**: 是
- **信心**: 60/100 中（3層價位定義與反應邏輯清楚明確；「大量」與「相對高檔/低檔」門檻書中未給精確數字，需自行定義）
