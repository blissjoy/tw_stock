# K線橫盤突破確認規則

- **Rule ID**: R-CANDLE-04
- **名稱**: K線橫盤突破確認規則
- **分類**: K棒型態
- **原文與頁碼**: P03-C1-K線起源與基本概念.md（上冊 p.154-155；原書3-1節「K線橫盤原則與進出場的確認」）
- **解讀**: 走勢圖中K線橫向走勢，股價一直沒有突破前一根K線的最高點、也沒有跌破前一根K線的最低點，這樣的橫向K線連續超過3根以上，可視為K線的橫盤或盤整。確認向上突破盤整，必須出現1根中長紅K線且收盤價突破橫盤區間的「上頸線」（即橫盤期間的最高點）；確認向下跌破盤整，必須出現1根中長黑K線且收盤價跌破橫盤區間的「下頸線」（即橫盤期間的最低點）。若此時處於多頭趨勢，向上突破即為進場做多的位置；若處於空頭趨勢，向下跌破即為進場放空的位置。
- **可程式化**: 是
- **所需資料**: 每根K棒的開盤價、收盤價、最高價、最低價
- **計算公式**:
```
# 判定橫盤區間：連續 >= 3 根K棒，彼此皆未突破/跌破前一根的高低點
consolidation = []
for i in range(1, n):
    if high[i] <= max(high[j] for j in consolidation ∪ {i-1}) and \
       low[i]  >= min(low[j]  for j in consolidation ∪ {i-1}):
        consolidation.append(i)
if len(consolidation) >= 3:
    upper_neckline = max(high[i] for i in consolidation)
    lower_neckline = min(low[i]  for i in consolidation)

    # 向上突破確認
    if is_mid_long_red_candle(t) and close[t] > upper_neckline:
        breakout_up = True   # 若當下為多頭趨勢 → 做多進場訊號

    # 向下跌破確認
    if is_mid_long_black_candle(t) and close[t] < lower_neckline:
        breakout_down = True # 若當下為空頭趨勢 → 做空進場訊號
```
- **參數**: 盤整最少根數（預設3）、中長K定義沿用漲跌幅門檻（3.5%以上，見R-CANDLE-21／R-CANDLE-22）
- **可回測**: 是
- **信心**: 88/100 高
