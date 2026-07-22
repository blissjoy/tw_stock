# 底部狹幅盤整大量紅K突破鎖股

- **Rule ID**: R-SCREEN-11
- **名稱**: 底部狹幅盤整大量紅K突破鎖股
- **分類**: 選股策略
- **原文與頁碼**: `P10-C4-底部強勢反轉圖形.md`（下冊 p.291-292，「底部狹幅盤整鎖股位置」）
- **解讀**: 股價在底部區間進行「狹幅盤整」，整理時間須達2個月以上（書中原文：「底部經過2個月以上狹幅盤整」）。取點方式：以盤整區間相對高點連成的水平線作為「突破基準線」（上緣壓力線，見 [[密集盤整區區間支撐壓力規則]]）。當盤整區間出現「大量紅K」向上突破上緣基準線時進場，觸發K棒須為收紅K、收盤價站上盤整區間上緣，且當日成交量較盤整期均量明顯放大（書中稱「爆大量」）。書中4個範例的實際整理期間為3.5個月、2.5個月、4個月、3個月，皆落在「≥2個月」門檻之上，可作為門檻合理性的實務參考區間。
- **可程式化**: 是
- **所需資料**: 日K線OHLCV、成交量、盤整區間高低點與時長
- **計算公式**:
```
function narrow_range_bottom_breakout(stock, t):
    consolidation = detect_range_consolidation(stock, t)  # 回傳區間起訖日、高低點
    if consolidation is None:
        return False

    duration_months = (consolidation.end_date - consolidation.start_date).days / 30
    if duration_months < 2:
        return False  # 未達2個月以上狹幅盤整門檻

    upper_bound = consolidation.high  # 盤整區間上緣（相對高點連線）

    is_red_k = Close[t] > Open[t]
    breakout = Close[t] > upper_bound
    range_avg_vol = average_volume(stock, consolidation.start_date, consolidation.end_date)
    big_volume = Volume[t] >= 2 * range_avg_vol  # 爆大量，沿用成交量分類與倍數門檻定義的2倍基準

    return is_red_k and breakout and big_volume
```
- **參數**: 盤整最短時長2個月（書中明確）；爆大量倍數採 [[成交量分類與倍數門檻定義]] 的2倍基準量作為預設代理（書中本節未再重複給出精確倍數，僅稱「爆大量」）
- **可回測**: 是
- **信心**: 89/100 高（盤整時長門檻與進場觸發邏輯書中文字明確描述，並有4個具體範例的整理月數與盤整區低點價位佐證）
