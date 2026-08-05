-- 台股分析系統資料庫 schema
--
-- 資料來源規劃：
--   stock_prices / institutional_investors / margin_trading  <- TWSE/TPEx 官方開放資料 或 FinMind
--   broker_chips / securities_traders (分點券商籌碼)          <- FinMind（非官方開放資料，交易所本身不公布，
--                                                               是由第三方依成交回報券商代號彙整而來）
--
-- 目前階段：先建好4張事實表(stock_prices/institutional_investors/margin_trading/broker_chips)
-- 的欄位結構，broker_chips 的抓取器(fetcher)留待 FinMind API token 與方案確認後再接上；
-- 其餘3張表可先用 TWSE/TPEx 官方開放資料實作。

PRAGMA foreign_keys = ON;

-- 股票基本資料
CREATE TABLE IF NOT EXISTS stocks (
    stock_id      TEXT PRIMARY KEY,      -- 例如 "2330"
    name          TEXT NOT NULL,         -- 例如 "台積電"
    market        TEXT NOT NULL,         -- "TWSE" 或 "TPEx"（興櫃併入TPEx，見listing_type）
    industry      TEXT,                  -- 產業別
    listing_type  TEXT,                  -- FinMind TaiwanStockInfo原始市場別："twse"/"tpex"/
                                          -- "emerging"(興櫃)，2026-08-04新增，只給觀察清單
                                          -- 依市場別顯示不同字體顏色用，不影響market欄位既有
                                          -- 的篩選/分類邏輯（見src/data/finmind_client.py
                                          -- fetch_stock_info()的說明）。可能是NULL(來源沒有
                                          -- 提供，例如大盤INDEX這種特殊列)。
    updated_at    TEXT NOT NULL          -- 本筆資料最後更新時間(ISO8601字串)
);

-- 每日股價OHLCV（欄位對應 FinMind TaiwanStockPrice，與TWSE官方開放資料語意相同）
CREATE TABLE IF NOT EXISTS stock_prices (
    stock_id            TEXT NOT NULL REFERENCES stocks(stock_id),
    date                TEXT NOT NULL,     -- "YYYY-MM-DD"
    open                REAL NOT NULL,
    high                REAL NOT NULL,     -- FinMind欄位名為max，此處統一用OHLC慣用命名
    low                 REAL NOT NULL,     -- FinMind欄位名為min
    close               REAL NOT NULL,
    volume              INTEGER NOT NULL,  -- 成交股數，對應FinMind Trading_Volume
    trading_money       INTEGER,           -- 成交金額
    trading_turnover    INTEGER,           -- 成交筆數
    spread              REAL,              -- 漲跌價差
    PRIMARY KEY (stock_id, date)
);
CREATE INDEX IF NOT EXISTS idx_stock_prices_date ON stock_prices(date);

-- 三大法人買賣超（欄位對應 FinMind TaiwanStockInstitutionalInvestorsBuySell）
-- investor_type 可能值：Foreign_Investor(外資) / Investment_Trust(投信) /
--                       Dealer_self(自營商自行買賣) / Dealer_Hedging(自營商避險) /
--                       Foreign_Dealer_Self(外資自營商，較少見)
CREATE TABLE IF NOT EXISTS institutional_investors (
    stock_id        TEXT NOT NULL REFERENCES stocks(stock_id),
    date            TEXT NOT NULL,
    investor_type   TEXT NOT NULL,
    buy             INTEGER NOT NULL,   -- 買進股數
    sell            INTEGER NOT NULL,   -- 賣出股數
    PRIMARY KEY (stock_id, date, investor_type)
);
CREATE INDEX IF NOT EXISTS idx_institutional_investors_date ON institutional_investors(date);

-- 融資融券（欄位對應 FinMind TaiwanStockMarginPurchaseShortSale）
CREATE TABLE IF NOT EXISTS margin_trading (
    stock_id                        TEXT NOT NULL REFERENCES stocks(stock_id),
    date                             TEXT NOT NULL,
    margin_purchase_buy             INTEGER,   -- 融資買進
    margin_purchase_sell            INTEGER,   -- 融資賣出
    margin_purchase_cash_repayment  INTEGER,   -- 融資現金償還
    margin_purchase_yesterday_balance INTEGER, -- 前日融資餘額
    margin_purchase_today_balance   INTEGER,   -- 今日融資餘額
    margin_purchase_limit           INTEGER,   -- 融資限額
    short_sale_buy                  INTEGER,   -- 融券買進(回補)
    short_sale_sell                 INTEGER,   -- 融券賣出
    short_sale_cash_repayment       INTEGER,   -- 融券現金償還
    short_sale_yesterday_balance    INTEGER,   -- 前日融券餘額
    short_sale_today_balance        INTEGER,   -- 今日融券餘額
    short_sale_limit                INTEGER,   -- 融券限額
    offset_loan_and_short           INTEGER,   -- 資券互抵
    PRIMARY KEY (stock_id, date)
);
CREATE INDEX IF NOT EXISTS idx_margin_trading_date ON margin_trading(date);

-- 券商分點基本資料（欄位對應 FinMind TaiwanSecuritiesTraderInfo）
CREATE TABLE IF NOT EXISTS securities_traders (
    securities_trader_id   TEXT PRIMARY KEY,  -- 券商分點代號
    securities_trader      TEXT NOT NULL,     -- 券商分點名稱，例如"合庫"
    address                 TEXT,
    phone                   TEXT,
    updated_at              TEXT NOT NULL
);

-- 分點券商進出籌碼（欄位對應 FinMind TaiwanStockTradingDailyReport，屬於非官方彙整資料）
-- 同一檔股票、同一天、同一分點，依成交價位分別記錄買賣超，粒度比三大法人細很多，
-- 資料量會遠大於其他3張表，之後若效能有問題，可考慮依年份分表或只保留近N年。
CREATE TABLE IF NOT EXISTS broker_chips (
    stock_id                TEXT NOT NULL REFERENCES stocks(stock_id),
    date                     TEXT NOT NULL,
    securities_trader_id    TEXT NOT NULL REFERENCES securities_traders(securities_trader_id),
    price                    REAL NOT NULL,
    buy                      INTEGER NOT NULL,   -- 該分點於該價位的買進股數
    sell                     INTEGER NOT NULL,   -- 該分點於該價位的賣出股數
    PRIMARY KEY (stock_id, date, securities_trader_id, price)
);
CREATE INDEX IF NOT EXISTS idx_broker_chips_date ON broker_chips(date);
CREATE INDEX IF NOT EXISTS idx_broker_chips_trader ON broker_chips(securities_trader_id);

-- 資料抓取進度紀錄：追蹤每個(dataset, stock_id, date)是否已抓取，避免GitHub Actions
-- 每日排程重複呼叫API浪費額度(尤其FinMind免費額度為300~600次/小時)
CREATE TABLE IF NOT EXISTS fetch_log (
    dataset     TEXT NOT NULL,   -- 例如 "TaiwanStockPrice"
    stock_id    TEXT NOT NULL,
    date        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (dataset, stock_id, date)
);

-- 每日選股結果：由 scripts/daily_pipeline.py 每天寫入一次，Streamlit 儀表板直接讀這張表
-- 顯示最新一天的候選清單，不必在儀表板端重算任何指標。
CREATE TABLE IF NOT EXISTS daily_candidates (
    date            TEXT NOT NULL,       -- "YYYY-MM-DD"，訊號成立當天
    stock_id        TEXT NOT NULL REFERENCES stocks(stock_id),
    signal_name     TEXT NOT NULL,       -- 例如 "R-TREND-14多頭短線進場"
    entry_price     REAL NOT NULL,
    stop_loss       REAL NOT NULL,
    note            TEXT,                -- 補充說明(例如命中的規則細節)
    created_at      TEXT NOT NULL,
    PRIMARY KEY (date, stock_id, signal_name)
);
CREATE INDEX IF NOT EXISTS idx_daily_candidates_date ON daily_candidates(date);

-- 每日資料來源狀態：is_intraday=1代表當天資料來自盤中即時價(TWSE官方「每日收盤行情」端點
-- 收盤前查詢會是空的，改用yfinance批次下載的盤中即時價當備援，見scripts/daily_pipeline.py
-- 的fetch_today_twse())，還不是官方最終定案的收盤價；=0代表已經是官方收盤後的最終數字。
-- 兩個前端的候選清單UI依這個flag顯示「尚未收盤」提示，讓使用者知道這天的訊號可能還會隨
-- 收盤價格微調而改變。同一天重新抓取(例如收盤後再按一次「手動抓取」)會依當時實際抓到的
-- 來源覆蓋更新這個flag，是自我修正的，不需要另外清除舊紀錄。
CREATE TABLE IF NOT EXISTS daily_data_status (
    date            TEXT PRIMARY KEY,
    is_intraday     INTEGER NOT NULL,
    updated_at      TEXT NOT NULL
);

-- 每日均線/SAR快取：候選清單「篩選方法」(均線多頭排列/SAR翻轉)原本每次套用篩選都對
-- stock_prices即時重算，改成全市場基礎池後(見chart_data.load_stock_universe_for_date())
-- 這個即時計算的成本變得很高(SAR是逐日累積、無法簡單向量化的指標，全市場~2300檔要
-- 15~35秒)。改成股價更新的同時(src/screener/daily_screener.py的run_screen_and_store())
-- 順便把均線/SAR算好存進這張表，候選清單篩選只需要查表，見
-- src/screener/indicator_precompute.py的計算邏輯、chart_data.py的
-- load_ma_bullish_flags_from_table()/load_sar_flip_flags_from_table()查詢路徑。
--
-- 均線存原始數值(不是布林旗標)：CANDIDATE_FILTERS的幾種均線多排條件都能直接從這幾欄
-- 用SQL比較大小算出來，之後多加其他均線條件也不用改schema。
-- sar_flip_days_ago存「第幾天前翻轉」這個整數(不是日期字串)，跟現有
-- src/indicators/parabolic_sar.py的sar_flip_days_ago()既有語意(以「第幾根K棒」為
-- 單位，不是日曆天數，避免連假造成的落差)完全一致，1代表就是這一天翻轉。
--
-- ⚠️ 這不是「算過一次、永遠不再碰」的快取：TWSE收盤前用yfinance盤中即時價當備援、
-- 收盤後可能重抓一次拿到最終數字，加上這次session才修過的「TAIEX成交量延遲」bug
-- 證實歷史資料確實會事後被回補/修正，因此scripts/daily_pipeline.py每次排程執行時
-- 會額外往回重刷INDICATOR_REFRESH_WINDOW_DAYS個交易日，不是只算當天這一筆。
-- ma200(2026-08-04新增)是獨立於ma5~ma240這組「朱家泓多頭排列」慣例之外的一條均線：
-- 只給SAR翻轉篩選的必備條件(股價需站上/跌破長期均線，見chart_data.py的
-- compute_sar_flip_flags()/load_sar_flip_flags_from_table())專用，刻意用200天而不是
-- 沿用既有的ma240——使用者比對的另一套獨立工具(d:\tw_stock_analyzer)這條件用的是
-- 200天，兩邊要能對得上號，不能為了省一個欄位就借用本專案自己240天的慣例(那是給
-- MA5>10>20>60>120>240多排條件用的、意義不同)。
CREATE TABLE IF NOT EXISTS daily_indicators (
    stock_id            TEXT NOT NULL REFERENCES stocks(stock_id),
    date                TEXT NOT NULL,
    ma5                 REAL,
    ma10                REAL,
    ma20                REAL,
    ma60                REAL,
    ma120               REAL,
    ma200               REAL,
    ma240               REAL,
    sar_value           REAL,
    sar_is_bull         INTEGER,  -- 0/1，NULL代表資料不足(<3天)算不出SAR
    sar_flip_days_ago   INTEGER,  -- 目前方向是第幾天前翻轉進來的；NULL代表算不出SAR
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (stock_id, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_indicators_date ON daily_indicators(date);

-- 集保戶股權分散表(FinMind TaiwanStockHoldingSharesPer)，2026-08-04新增：黃豐凱籌碼
-- 分析法(見src/indicators/huang_chip_signals.py，程式碼來源private)「大戶/散戶持股
-- 週變化」需要的資料，本專案目前唯一一張「非日更新」的事實表。
--
-- ⚠️ TDCC(集保結算所)通常每週五才公布新一批資料，其餘平日呼叫FinMind只會拿到同一批
-- 「上次已知」的資料，不是每個交易日都有新的一筆——跟stock_prices/institutional_
-- investors「每個交易日一定有一筆」的既有假設不同。查詢/判斷「有沒有新資料」時，
-- 要比較「這檔股票DB裡已存的最新一筆date」跟「API這次回傳的最新date」是否相同，
-- 不能用「今天日期」當比對基準(API幾乎不會剛好回傳"今天"這個日期，用今天日期比對
-- 會讓dedup判斷永遠失效)。
--
-- 存原始逐級距資料(不是只存大戶/散戶兩個算好的百分比)，跟本專案「均線存原始數值不是
-- 布林旗標」同一個理由——之後如果需要別的級距組合，不用回頭重新設計schema。
CREATE TABLE IF NOT EXISTS holder_shares_distribution (
    stock_id                TEXT NOT NULL REFERENCES stocks(stock_id),
    date                    TEXT NOT NULL,
    holding_shares_level    TEXT NOT NULL,  -- FinMind HoldingSharesLevel原始值，例如"1-999"、"more than 1,000,001"
    people                  INTEGER,
    unit                    INTEGER,
    percent                 REAL NOT NULL,
    updated_at              TEXT NOT NULL,
    PRIMARY KEY (stock_id, date, holding_shares_level)
);
CREATE INDEX IF NOT EXISTS idx_holder_shares_distribution_date ON holder_shares_distribution(date);

-- 已確認下市/終止交易的股票(2026-08-04新增)：使用者回報大量「下載錯誤」股票代號，
-- 追查發現FinMind的股票清單分類資料落後(甚至落後超過一年)，這些代號其實已經下市/
-- 併購/終止興櫃買賣，Yahoo Finance兩種市場後綴(.TW/.TWO)都查不到——見
-- scripts/daily_pipeline.py的fetch_today_tpex()說明。記下來避免排程每天重複浪費
-- 下載嘗試，也讓UI(候選清單/股票搜尋)不要再把這些股票當成還在交易的標的顯示。
-- stock_id刻意不設REFERENCES stocks(stock_id)外鍵——記錄下市這件事的時候，
-- stocks表裡不一定還有這筆(可能從來沒成功抓過名稱、或本來就查無資料)。
CREATE TABLE IF NOT EXISTS delisted_stocks (
    stock_id        TEXT PRIMARY KEY,
    name            TEXT,
    delisted_date   TEXT,   -- 確認下市的日期(若已知)，不確定時為NULL
    reason          TEXT,   -- 簡短原因，例如"兩種市場後綴皆查無資料"
    noted_at        TEXT NOT NULL  -- 我們自己記錄下這件事的時間(不是真正下市當下)
);

-- web版對主DB Turso帳號有實質額度風險的「高風險動作」(回補資料、手動抓取今日資料)
-- 共用的速率限制紀錄表，見src/data/admin_action_rate_limit.py。2026-08-05新增，
-- 一開始只給回補資料用(表名一度叫backfill_attempts)，後來手動抓取今日資料按鈕
-- 發現是同一類風險(公開網址下任何訪客都能觸發、對Turso寫入+耗FinMind額度+手動
-- 抓取還會觸發真實LINE/Email通知)，改成用kind欄位分開追蹤但共用同一張表/同一組
-- 密碼(ADMIN_ACCESS_CODE)。只有web版有這個表的實際使用(桌面版一律寫本機sqlite，
-- 沒有Turso額度風險，但schema.sql本機/Turso共用，桌面版建表但不使用不影響任何事)。
-- 冷卻計算基準是「這次嘗試開始寫入的時間」(不是完成時間)：即使這次執行途中失敗，
-- Turso額度也已經被消耗掉一部分，不能因為「還沒完成」就允許立刻重試，否則變成
-- 可以無限重試炸額度的漏洞。
CREATE TABLE IF NOT EXISTS admin_action_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,     -- 'backfill' 或 'manual_fetch'，各自獨立算冷卻時間
    started_at   TEXT NOT NULL,     -- ISO8601 UTC，冷卻計算基準
    finished_at  TEXT,              -- 完成/失敗時才填
    status       TEXT NOT NULL,     -- 'running'/'done'/'failed'
    params_json  TEXT NOT NULL,     -- 這次動作的參數(方便事後追查)
    result_json  TEXT               -- 完成後的summary
);
