# 空頭強勢反彈月線上橫盤大量紅K突破鎖股

- **Rule ID**: R-SCREEN-12
- **名稱**: 空頭強勢反彈月線上橫盤大量紅K突破鎖股
- **分類**: 選股策略
- **原文與頁碼**: `P10-C4-底部強勢反轉圖形.md`（下冊 p.293-294，「空頭強勢反彈鎖股位置」）
- **解讀**: 前提為股票原處於空頭（下跌）趨勢（見 [[頭頭低底底低空頭趨勢判定]]）。股價出現「強勢反彈」，且反彈後未再破底，而是站上月線（MA20）之上進行「橫盤」整理——即反彈力道足以讓股價脫離月線之下的空頭排列，轉為在月線上方區間震盪。取點方式：以「反彈後在月線之上」的橫盤整理區間上緣作為突破基準線。書中原文：「空頭出現強勢反彈，股價在月線上橫盤，鎖大量紅K突破橫盤時進場」。進場觸發條件：當月線上方的橫盤整理區出現大量紅K向上突破時進場。
- **可程式化**: 是
- **所需資料**: 日K線OHLCV、成交量、MA20（月線）
- **計算公式**:
```
function bear_rebound_above_ma20_breakout(stock, t):
    if not was_in_downtrend(stock, t, lookback=60):
        return False

    # 反彈後站上月線且未再破底
    above_ma20_since = first_day_close_above_ma20(stock, t)
    if above_ma20_since is None:
        return False
    if broke_prior_low(stock, above_ma20_since, t):
        return False

    # 月線之上橫盤整理
    consolidation = detect_range_consolidation(stock, above_ma20_since, t, above_line=MA20)
    if consolidation is None:
        return False

    upper_bound = consolidation.high
    is_red_k = Close[t] > Open[t]
    breakout = Close[t] > upper_bound
    range_avg_vol = average_volume(stock, consolidation.start_date, consolidation.end_date)
    big_volume = Volume[t] >= 2 * range_avg_vol  # 爆大量，沿用成交量分類與倍數門檻定義

    return is_red_k and breakout and big_volume
```
- **參數**: 爆大量倍數採 [[成交量分類與倍數門檻定義]] 的2倍基準量作為預設代理（書中本節未給精確倍數，僅稱「大量紅K」）；橫盤整理時長書中未量化
- **可回測**: 是（核心邏輯可回測，橫盤整理時長門檻需自訂）
- **信心**: 74/100 中高（辨識邏輯與進場觸發書中文字明確，2個範例佐證型態；但橫盤整理所需最短時長、大量的精確倍數書中未給出）
