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
    stock_id    TEXT PRIMARY KEY,      -- 例如 "2330"
    name        TEXT NOT NULL,         -- 例如 "台積電"
    market      TEXT NOT NULL,         -- "TWSE" 或 "TPEx"
    industry    TEXT,                  -- 產業別
    updated_at  TEXT NOT NULL          -- 本筆資料最後更新時間(ISO8601字串)
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
CREATE TABLE IF NOT EXISTS daily_indicators (
    stock_id            TEXT NOT NULL REFERENCES stocks(stock_id),
    date                TEXT NOT NULL,
    ma5                 REAL,
    ma10                REAL,
    ma20                REAL,
    ma60                REAL,
    ma120               REAL,
    ma240               REAL,
    sar_value           REAL,
    sar_is_bull         INTEGER,  -- 0/1，NULL代表資料不足(<3天)算不出SAR
    sar_flip_days_ago   INTEGER,  -- 目前方向是第幾天前翻轉進來的；NULL代表算不出SAR
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (stock_id, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_indicators_date ON daily_indicators(date);
