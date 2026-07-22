# 技術線圖17項看圖重點與每日收盤SOP規則

- **Rule ID**: R-SCREEN-19
- **名稱**: 技術線圖17項看圖重點與每日收盤SOP規則
- **分類**: 選股策略
- **原文與頁碼**: `P10-C5-股市交易工作重點.md`（下冊 p.299-301，「技術線圖的看圖重點」）
- **解讀**: 作者選股／操作前逐一檢視技術線圖時的17個重點項目，性質上是一份「看盤SOP檢查表」，用於在 [[做多環境四大前提規則]] 或 [[做空環境四大前提規則]] 確認方向後，對個別候選股逐檔複核，可歸為6類：
  1. **多空架構與型態判斷**（項目1、6、8、9）：看當日波浪型態是否符合多頭或空頭架構；看近3～5日K線型態變化；看均線排列方向是否多頭向上或空頭向下（見 [[均線多頭排列]]、[[均線空頭排列]]）；判斷股價目前處於盤整階段還是已經開始發動。四項一致時型態訊號較可靠。
  2. **股價位置與分時走勢**（項目2）：分三種情境——(1) 開盤即漲停最強；9點10分前漲停為次強；9點30分前漲停仍屬強。(2) 若13點20分之後才漲停，須觀察次日是否能延續強勢，不宜直接視為強勢確認。(3) 開盤即跌停為最弱；對快速下殺、反彈至壓力附近又回落的股票，可考慮做空。
  3. **價量、籌碼與週期線圖**（項目3、4、5）：多頭格局要看價量是否配合；籌碼變化要看三大法人（外資、投信、自營商）買賣超與融資融券增減，藉此確認是否出現洗盤後再上漲；看週線走勢方向與月線圖形變化；看日KD與週KD指標。
  4. **個股屬性分類**（項目7）：多頭時判斷該股是否屬於主流股、題材股、應景股、轉機股、軋空股等類別，可作為選股後設標記欄位。
  5. **風險評估與逆勢交易機會**（項目10、11）：預估交易機會的獲利空間並做好風險控制；強勢股（多頭格局）中出現的「空頭反彈」屬空方短打機會（等反彈結束後再下跌）；弱勢股（空頭格局）中出現的「多頭回檔」屬多方短打機會（等回檔結束後再上漲）——與整體多空前提可並存，屬「順大勢、逆小段」的戰術操作。
  6. **交易前的收尾作業**（項目12-17）：篩選出明日可進場的候選標的（挑選最佳3～5檔），並再檢視一次前述技術分析條件是否符合；做好資金分配，規劃部位配置；擬定具體操作策略；發掘新類股的啟動訊號及類股輪動狀況；掃描當日強勢股，發現原本上漲的主流類股是否出現轉弱訊號；建立並維持「強勢股走勢個股資料庫」（作者稱為「財庫」），持續追蹤觀察名單。
- **可程式化**: 部分（項目2的漲停時間分級、項目1/6/8/9的架構一致性檢查、項目12-17的流程性收尾工作可程式化；項目3的「洗盤後再漲」判斷、項目10的獲利空間估算、項目11的逆勢交易時機屬於較主觀的研判，需搭配其他規則的量化輸出作為代理）
- **所需資料**: 日K線OHLCV、分時走勢與漲跌停時間戳記、均線組、週線資料、日/週KD、三大法人買賣超、融資融券餘額、類股指數、當日漲跌幅排行
- **計算公式**:
```
function daily_chart_review_checklist(stock, t):
    review = {}

    # 4.1 多空架構與型態判斷（項目1,6,8,9）
    review["架構一致"] = (
        wave_pattern_matches_trend(stock, t) and
        recent_k_pattern_supportive(stock, t, days=5) and
        (count_bullish_aligned_MA(stock, t) >= 3 or count_bearish_aligned_MA(stock, t) >= 3) and
        not is_in_consolidation(stock, t)
    )

    # 4.2 漲停時間強度分級（項目2）
    limit_up_time = get_limit_up_time(stock, t)
    if limit_up_time is not None:
        if limit_up_time <= "09:10":
            review["強度分級"] = "最強/次強"
        elif limit_up_time <= "09:30":
            review["強度分級"] = "強"
        elif limit_up_time > "13:20":
            review["強度分級"] = "待次日驗證"
    elif get_limit_down_time(stock, t) == market_open_time(t):
        review["強度分級"] = "最弱"

    # 4.3 價量籌碼與週期線圖（項目3,4,5）
    review["價量配合"] = (Close[t] > Close[t-1]) and (Volume[t] > Volume[t-1])
    review["籌碼安定"] = institutional_net_buy(stock, t) > 0 or margin_short_improving(stock, t)
    review["週線月線方向"] = weekly_trend_state(stock, t)
    review["KD同向"] = kd_direction(stock, t, "日") == kd_direction(stock, t, "週")

    # 4.4 個股屬性分類（項目7）
    review["屬性標籤"] = classify_stock_type(stock, t)  # 主流股/題材股/應景股/轉機股/軋空股

    # 4.5 風險評估與逆勢機會（項目10,11）
    review["風報比"] = estimate_profit_risk_ratio(stock, t)
    if trend_state(stock, t) == "多頭" and detect_bear_rebound_within_bull(stock, t):
        review["逆勢機會"] = "強勢股空頭反彈，等結束後放空"
    elif trend_state(stock, t) == "空頭" and detect_bull_pullback_within_bear(stock, t):
        review["逆勢機會"] = "弱勢股多頭回檔，等結束後做多"

    return review


function daily_closing_sop(candidate_pool, t):
    # 4.6 交易前的收尾作業（項目12-17）
    shortlist = rank_by_strength(candidate_pool, t)[:5]  # 收斂至3-5檔
    shortlist = [s for s in shortlist if daily_chart_review_checklist(s, t)["架構一致"]]  # 複驗

    position_plan = allocate_capital(shortlist)          # 項目13：資金分配
    trade_plan = draft_trade_strategy(shortlist)          # 項目14：操作策略
    sector_rotation = detect_new_sector_momentum(t)       # 項目15：新類股啟動/輪動
    weakening_mainstream = detect_mainstream_sector_weakening(t)  # 項目16：主流類股轉弱
    update_watchlist_database(shortlist, t)               # 項目17：財庫（觀察名單資料庫）更新

    return {
        "候選標的": shortlist,
        "資金分配": position_plan,
        "操作策略": trade_plan,
        "類股輪動": sector_rotation,
        "主流轉弱警訊": weakening_mainstream
    }
```
- **參數**: 漲停時間分級門檻09:10／09:30／13:20（書中明確）；候選標的收斂數量3～5檔（書中明確）；其餘（風報比門檻、籌碼安定的具體判準）書中未給精確數字
- **可回測**: 部分（漲停時間分級、候選收斂數量可回測；架構一致性、逆勢機會判斷等需先量化為具體規則輸出才能完整回測）
- **信心**: 74/100 中高（17項檢視重點書中逐一列出，其中漲停時間分級與收尾候選數量有明確數字；多數項目屬於「看圖時應檢查什麼」的定性SOP，需結合本規則庫其他章節的量化規則才能完全程式化）
