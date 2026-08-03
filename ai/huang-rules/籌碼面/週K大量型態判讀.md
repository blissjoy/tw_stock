# 週K大量型態判讀

- **Rule ID**: R-HUANG-04
- **名稱**: 週K大量型態判讀
- **分類**: 籌碼面
- **程式碼來源**: private
- **解讀**: 把日K依週一為起點分組成週K(每週的高/低取當週最大/最小，收盤價取當週最後一個交易日的收盤價)，取最近52週裡成交量總和最大的那一週當「大量K」參考基準；同成交量時較新的一週優先。用目前最新一週的收盤價，跟這個大量參考週的高點/中值((高+低)/2)/低點比較，分類為「大量高之上」(>高)、「大量中值之上」(>中值)、「大量中值之下」(>=低)、「大量低之下」(<低)四種。
- **可程式化**: 是
- **所需資料**: 每日高/低/收盤價/成交量(涵蓋約370個曆日的歷史)
- **計算公式**:
```
function classify_weekly_volume_pattern(daily_rows_asc):
    weeks = group_by_week_start(daily_rows_asc)  # 每週：high/low取極值、close取最後一天
    recent_52 = most_recent(weeks, 52)
    max_vol_week = max(recent_52, key=total_volume)  # 同量時較新的優先
    high, low = max_vol_week.high, max_vol_week.low
    mid = (high + low) / 2
    current_close = weeks[0].close
    if current_close > high: return "大量高之上"
    if current_close > mid: return "大量中值之上"
    if current_close >= low: return "大量中值之下"
    return "大量低之下"
```
- **參數**: 週數固定最近52週；⚠️這52週是「原始資料抓370個曆日、分組後取前52組」，不是精確對齊的52個完整週K——來源本人已確認這是程式碼本身既有的落差，非本次複刻引入的誤差，日後可能會改成精確52週版本。
- **可回測**: 未評估
- **信心**: private
