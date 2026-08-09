# 出貨量vs換手量判斷法

- **Rule ID**: R-CHIP-20
- **名稱**: 「出貨量」vs.「換手量」判斷法
- **分類**: 籌碼面
- **原文與頁碼**: `ai/ebook-summary-chen/P07-C3-老牌中石化董監戰抬行情.md`（第7篇第3章「老牌中石化 董監戰抬行情」，p.242-244）
- **解讀**: 書中評為「全書方法論最嚴謹的示範」，用來解決一個常見投資困境：回檔出量時，究竟是「出貨量」（應順勢逢高賣出）還是「換手量」（應趁低檔繼續加碼），兩者做法完全相反。具體偵測方法：比對「上漲期主要買方券商」在「回檔那天」的動作——(1) 找出上漲區間持續買進的高手/贏家/股價維護型券商分點；(2) 檢查回檔破關鍵支撐（如10日線）那天的賣出券商分點是否為同一批；(3) 若賣出的是不同(新)的一批人（尤其是總公司下單，通常代表法人單），且原買方在越跌越買，即為「換手量」（安全，可安心續抱甚至逢低加碼）；若賣出的正是原買方自己，才是真正的「出貨量」（應該逢高減碼）。中石化案例：原6家高手券商持續買進，回檔當天賣壓來自完全不同的4家（研判是投信賣），且原買方在最跌時仍持續加碼，判定為換手量，隔天股價確實創短期新高，證明投信看錯（這也直接呼應R-CHIP-14「股價表態仲裁元原則」）。
- **可程式化**: 否——需要「個股×券商分點×逐日買賣方向」的歷史資料（`broker_chips`表），才能比對「上漲期主要買方」在「回檔當天」是否仍是同一批人，資料源待FinMind付費方案。
- **所需資料**: 個股每日各券商分點買賣張數與方向（`broker_chips`表，待FinMind付費方案）。
- **計算公式**:
```
function turnover_vs_distribution(stock, uptrend_start, uptrend_end, pullback_date):
    main_buyers = top_n_net_buyers(stock, uptrend_start, uptrend_end, n=6)
    pullback_sellers = top_n_net_sellers(stock, pullback_date)
    overlap = set(main_buyers) & set(pullback_sellers)
    main_buyers_still_buying = all(
        broker_chips[stock][tr][pullback_date].buy > broker_chips[stock][tr][pullback_date].sell
        for tr in main_buyers
    )
    if not overlap and main_buyers_still_buying:
        return "換手量，可安心續抱甚至逢低加碼"
    if overlap:
        return "出貨量，原買方自己在賣，應逢高減碼"
```
- **參數**: 「上漲期主要買方」取前N家券商的N值書中沒有明確給出(案例中用了6家)，需自行決定並回測。
- **可回測**: 否——資料源未到位，無法回測。
- **信心**: 30/100 低（方法論邏輯嚴謹、有完整真實案例佐證；但扣分原因是核心資料`broker_chips`未到位，這是本清單裡多數依賴分點資料的規則共同的限制）。
