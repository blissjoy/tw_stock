# tw_stock

台股技術分析選股系統：以朱家泓技術分析方法論（`ai/zhu-rules/` 246條規則庫）為主軸，
每日收盤後自動抓取 TWSE/TPEx 資料、計算指標、跑選股，並透過 LINE 與 Email 推播結果，
儀表板可隨時在電腦或手機上查看。

## 本機開發

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入下方「需要的憑證」
pytest tests/ -q
```

### 本機跑儀表板（不連 Turso）

開發階段不需要 Turso 帳號，設定 `LOCAL_DB_PATH` 環境變數指向任一本機 sqlite 檔案即可：

```bash
LOCAL_DB_PATH=data/tw_stock_dev.db streamlit run dashboard/app.py
```

若這個檔案是全新的，儀表板會自動建表但候選清單是空的；可以先跑
`scripts/seed_turso_from_local.py`（把 `--local-db` 換成任一本機 sqlite 檔案，
目標端也用同一支腳本本機測試即可，不一定要接 Turso）灌一些歷史資料進去，
或是打開儀表板後直接按「🔄 立即重新篩選」，只要 `stock_prices` 表已有資料就會即時算出候選清單。

**做法確認**：先在本機把資料層、選股邏輯、儀表板互動都跑過、畫面看起來對，再串
GitHub Actions + Streamlit Community Cloud + Turso 上線，比一開始就對著雲端服務除錯有效率很多。

### 補特定一天的資料

`scripts/daily_pipeline.py` 預設抓「今天」，但可以用 `--date` 補跑任意一天：

```bash
python scripts/daily_pipeline.py --date 20260722
```

流程：抓該天 TWSE 全市場批次 + TPEx（透過FinMind逐股，約需4小時）資料 → 寫入Turso →
跑選股 → 寫入 daily_candidates → 發送LINE/Email通知。常用組合：

| 情境 | 指令 |
|---|---|
| 只想補資料，不要真的發通知 | 加 `--dry-run` |
| 先只補 TWSE，跳過耗時的 TPEx | 加 `--skip-tpex` |
| 快速驗證（幾分鐘內完成） | `python scripts/daily_pipeline.py --date 20260722 --dry-run --skip-tpex` |
| 補進本機資料庫而不是 Turso | 加 `--local-db data/tw_stock.db` |

## 每日自動化架構（上線後）

```
GitHub Actions（每日排程） → 抓TWSE+TPEx當日資料 → 寫入Turso → 跑選股 → 寫入Turso → 發LINE/Email通知
                                                                              ↓
                                                        Streamlit 儀表板（讀Turso顯示最新候選清單）
```

- **資料持久化**：[Turso](https://turso.tech)（免費方案，SQLite相容的雲端資料庫），取代本機
  531MB+ 的研究用歷史庫直接 commit 回 git 的方案（會超過GitHub單檔100MB限制且讓git history
  永久膨脹）。本機的 `data/tw_stock.db` 保留給離線回測研究使用，不會搬到雲端。
- **排程**：GitHub Actions，`.github/workflows/daily_pipeline.yml`，收盤後（台灣時間19:30）
  自動抓資料、選股、通知。本 repo 為 public，Actions 標準 Linux runner 免費無分鐘數上限，
  可負擔 TPEx 透過 FinMind 逐股回補所需的約4小時執行時間。
- **選股邏輯**：目前為 MVP 起點，只接上已用真實資料回測驗證過的 R-TREND-14（多頭短線選股與
  停損停利SOP，信心92/100，見 `src/screener/daily_screener.py`），之後可逐步接上更多規則庫規則。
- **通知**：LINE Messaging API（broadcast，推播給自己）+ Gmail SMTP。
- **儀表板**：Streamlit Community Cloud（`dashboard/app.py`），限邀請的檢視者可開啟；候選清單
  可直接點選任一列，下方即顯示該檔股票的K線圖（可疊加均線/切線軌道線/支撐壓力，皆可個別切換
  顯示）、成交量子圖，以及最新交易日的K棒型態與量價訊號分析；也支援按鈕「立即重新篩選」用
  資料庫現有資料重算訊號，不需要等待每日排程。

## 需要的憑證（`.env`，可參考 `.env.example`）

| 變數 | 用途 | 取得方式 |
|---|---|---|
| `FINMIND_API_TOKEN` | TPEx股價/三大法人/融資融券資料 | 註冊 [finmindtrade.com](https://finmindtrade.com) |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` | 每日pipeline的雲端資料庫 | 註冊 [turso.tech](https://turso.tech) 建立資料庫 |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE推播 | LINE Developers Console 建立 Messaging API 頻道，並用自己帳號加此bot為好友 |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` / `NOTIFY_EMAIL_TO` | Email通知 | Gmail 開啟兩步驟驗證後產生「應用程式密碼」 |

## 上線部署步驟（本機驗證沒問題後才做）

1. 依上表申請/設定好所有憑證，本機用 `.env`，正式環境用下方兩處：
   - GitHub repo → Settings → Secrets and variables → Actions，新增同名 secrets
   - Streamlit Community Cloud → App settings → Secrets，貼上同一組 `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN`
2. 跑一次性種子腳本，把本機歷史資料的近期滾動窗口灌進 Turso（否則第一天Turso資料不足以算MA240等指標）：
   ```bash
   python scripts/seed_turso_from_local.py --local-db data/tw_stock.db --days 400
   ```
3. 本機先用 `--dry-run` 驗證整條管線邏輯正確（不會真的發送通知）：
   ```bash
   python scripts/daily_pipeline.py --dry-run
   ```
4. 確認無誤後，到 GitHub repo 的 Actions 頁面手動觸發一次 `daily_pipeline` workflow
   （`workflow_dispatch`），確認排程正確跑完，再放心讓 cron 排程接手。
5. 到 [Streamlit Community Cloud](https://share.streamlit.io) 部署 `dashboard/app.py`，
   設定僅限受邀者可檢視。

## 更新雲端

「雲端」分兩塊，各自更新方式不一樣：

**程式碼**（GitHub Actions / Streamlit Cloud 讀的是 repo 內容）
```bash
git add <改動的檔案> && git commit -m "..." && git push origin master
```
push 之後：GitHub Actions 下次排程執行（或手動 `workflow_dispatch`）就會用到最新程式碼；
Streamlit Community Cloud 偵測到 push 會自動重新部署，通常 1~2 分鐘內完成，不需要額外操作。

**資料**（Turso 是獨立的雲端資料庫，不會自動跟本機同步）
| 情境 | 指令 |
|---|---|
| 每天/補某一天的當日資料 | `python scripts/daily_pipeline.py [--date YYYYMMDD]`（見上方「補特定一天的資料」） |
| 把本機歷史資料庫整批重新灌進Turso | `python scripts/seed_turso_from_local.py --local-db data/tw_stock.db --days 400` |
| 修正Turso裡已經寫錯的資料（例如股票名稱） | 寫一支像 `scripts/fix_stock_names.py` 這樣的一次性修正腳本，跑一次即可 |

⚠️ **同一時間只能有一個行程對Turso寫入**：`daily_pipeline.py`/`seed_turso_from_local.py`/一次性修正腳本
如果同時執行（例如背景已經在跑一個，又手動另外開一個一樣的指令），可能會在 `ensure_schema()`
建表時互相卡到（`libsql-client` 套件對這種情況的錯誤處理不完善，實測會直接crash，
已在 `src/data/turso_client.py` 修好讓「物件已存在」這種情況不再crash，但重複寫入本身
沒有必要，還是應該避免同時跑兩個）。跑之前可以先確認終端機/背景工作有沒有已經在跑的行程。

## 目錄結構

- `src/indicators/` `src/strategies/` `src/patterns/` `src/risk/`：246條朱家泓規則庫的程式實作
  （`src/patterns/chart_overlays.py`、`latest_day_summary.py` 是給儀表板用的整合層）
- `src/screener/`：選股邏輯（`screening_rules.py`為規則庫函式，`daily_screener.py`為每日選股組裝）
- `src/data/`：TWSE/TPEx/FinMind抓取器、SQLite/Turso儲存層、交易日曆(`trading_calendar.py`)
- `src/notify/`：LINE/Email通知
- `src/backtest/`：回測引擎
- `scripts/`：一次性/排程用的進入點腳本
- `dashboard/`：Streamlit 儀表板
- `ai/`：電子書逐章精讀筆記（`ebook-summary/`）、規則庫（`zhu-rules/`）、規劃文件（`PLAN.md`）
