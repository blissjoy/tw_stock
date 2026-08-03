-- 庫存清單／觀察清單 schema：獨立於 schema.sql(市場資料)的另一份DB，只存使用者
-- 自己的持股/觀察名單資料。刻意不存stock_id對應的name欄位——本專案的stocks.name
-- 是唯一權威來源(既有慣例，daily_candidates等表也都不重複存name)，查詢時用
-- stock_id去主DB的stocks表JOIN取得名稱，避免兩份資料不同步。
--
-- 這條DB連線永遠是本機sqlite檔案，不支援Turso(見src/data/connection.py的
-- get_default_portfolio_connection())——庫存/觀察清單是使用者個人資料，這次先不
-- 考慮多裝置雲端同步。

PRAGMA foreign_keys = ON;

-- 庫存清單：使用者實際持有的股票，同一檔股票可以有多筆(分批買入)，每筆各自是
-- 一個獨立的"批次"(lot)，用自動遞增id當主鍵，不是stock_id——2026-08-02改版，
-- 使用者反映實際狀況是分批買入，原本stock_id當主鍵只能存一筆會互相覆蓋。
CREATE TABLE IF NOT EXISTS inventory_stocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id    TEXT NOT NULL,         -- 對應主DB stocks.stock_id
    buy_date    TEXT,                  -- 買入日期("YYYY-MM-DD")，選填
    cost_price  REAL,                  -- 這批的成本價，選填
    shares      INTEGER,               -- 這批的股數，選填
    fee         REAL,                  -- 買進手續費，選填——2026-08-02新增，使用者
                                        -- 直接填券商app顯示的實際金額，不是系統用
                                        -- 固定費率換算(牌告費率+折數+最低手續費的
                                        -- 組合太多種，直接填實際數字最準確)。
    note        TEXT,                  -- 自訂備註
    created_at  TEXT NOT NULL          -- 建立時間(ISO8601字串)
);
CREATE INDEX IF NOT EXISTS idx_inventory_stocks_stock_id ON inventory_stocks(stock_id);

-- 觀察清單分組(例如「半導體」「金融股」)
CREATE TABLE IF NOT EXISTS watchlist_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name  TEXT UNIQUE NOT NULL,
    created_at  TEXT NOT NULL
);

-- 觀察清單股票：同一檔股票可以出現在不同群組，複合主鍵(group_id, stock_id)
CREATE TABLE IF NOT EXISTS watchlist_stocks (
    group_id    INTEGER NOT NULL REFERENCES watchlist_groups(id),
    stock_id    TEXT NOT NULL,
    cost_price  REAL,                  -- 參考成本價，選填
    shares      INTEGER,               -- 參考股數，選填
    note        TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (group_id, stock_id)
);
