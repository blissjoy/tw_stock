# tw_stock

台股技術分析選股系統：以朱家泓技術分析方法論（`ai/zhu-rules/` 246條規則庫）為主軸，
收盤後抓取 TWSE/TPEx 資料、計算指標、跑選股，並透過 LINE 與 Email 推播結果。

⚠️ **目前是本機優先架構**（2026-07-23調整）：主要透過PySide6桌面版(`desktop/`)在本機執行，
不強制依賴任何雲端服務（起因：Turso免費方案帳號寫入額度用完、寫入被直接封鎖，見下方
「（可選）之後恢復雲端部署」章節的說明）。Streamlit網頁版(`dashboard/app.py`)仍然保留、
可以本機跑，程式邏輯跟桌面版共用同一套底層(`src/`)，之後要不要接回雲端是獨立的後續決定。

## 本機開發

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入下方「需要的憑證」（本機優先模式下，只有FINMIND_API_TOKEN是必要的）
pytest tests/ -q
```

## 本機執行方式

### PySide6桌面版（主要日常使用方式）

```bash
python desktop/main.py
```

預設直接讀寫本機 `data/tw_stock.db`（不需要另外設定環境變數）。畫面上：
- 候選清單表格（股票代號/名稱/產業別/訊號(信心%)/進場價/停損價/漲跌幅(%)/成交量）：可用
  日期下拉選單切換查看歷史候選清單；點選任一列即在下方載入該檔股票的K線圖；儲存格內容較長
  被截斷時，滑鼠移過去會懸浮顯示完整文字。若當天資料是盤中抓的(TWSE官方收盤資料還沒公布，
  改用yfinance即時價備援，見下方排程說明)，表格上方會有紅色粗體「尚未收盤」提示。
- 個股查詢欄：輸入股票代號或名稱皆可查詢。
- K線圖可疊加均線(MA5/10/20/60/120/240)/切線軌道線/支撐壓力/MACD/KD，皆可用勾選框個別
  切換顯示，搭配下方最新交易日的K棒型態與量價訊號分析，以及「📊 個股分析」面板(顯示這檔
  股票目前符合規則庫中哪些訊號，依信心分數排序)。滑鼠移到圖表上會顯示淡灰色十字線（貫穿
  價格圖與成交量圖），左上角動態顯示滑鼠對應K棒的日期/OHLC/成交量，取代預設會跟著滑鼠跑的
  浮動提示框，仿TradingView超級圖表的畫法（`desktop/chart_render.py`；這個完整效果只有
  桌面版才有，Streamlit版因為渲染架構限制沒有對應機制）。
- 「🔄 立即重新篩選」：只用資料庫現有資料重算候選清單，幾秒內完成。
- 「▶ 手動抓取今日資料」：背景執行緒抓取當天TWSE/TPEx資料並重新選股（跟下面的排程共用
  同一份`run_daily_pipeline()`），下載過程中畫面右上角會顯示進度(例如「TPEx 500/1980檔」)，
  不會卡住畫面。
- 畫面右上角平時顯示「資料更新至：{DB裡最新一次成功寫入股價的時間}」；排程或手動抓取正在
  執行時（不論是本視窗自己觸發、還是Windows工作排程器在背景觸發），會改顯示「🔄 更新中...」。

### Streamlit網頁版（可選）

```bash
LOCAL_DB_PATH=data/tw_stock_dev.db streamlit run dashboard/app.py
```

功能跟桌面版相同（兩者共用`src/presentation/chart_data.py`的圖表資料組裝邏輯），差別只在
UI框架——也有「▶ 手動抓取今日資料」按鈕(同步阻塞呼叫，按下去要等抓取跑完，用進度條顯示
下載進度)跟右上角的「資料更新至/更新中」提示，本機開發時可以用來快速驗證UI改動；沒有的
只有桌面版才有意義的「自動抓取正在背景執行、不卡住視窗」這個體驗差異(Streamlit本來就是
每次互動整個腳本重跑一次的架構，沒有真正的「背景執行緒」概念)。

若指向的 sqlite 檔案是全新的，畫面會自動建表但候選清單是空的；可以先跑
`scripts/seed_turso_from_local.py`（把 `--local-db` 換成任一本機 sqlite 檔案，
目標端也用同一支腳本本機測試即可，不一定要接 Turso）灌一些歷史資料進去，
或是直接按「🔄 立即重新篩選」，只要 `stock_prices` 表已有資料就會即時算出候選清單。

### 補特定一天的資料

`scripts/daily_pipeline.py` 預設抓「今天」，但可以用 `--date` 補跑任意一天：

```bash
python scripts/daily_pipeline.py --date 20260722 --local-db data/tw_stock.db
```

流程：抓該天 TWSE 全市場批次 + TPEx（透過yfinance批次下載股價，實測約1~2分鐘）資料 → 寫入
資料庫 → 跑選股 → 寫入 daily_candidates → 發送LINE/Email通知（同時更新
`data/pipeline_status.json`供桌面版狀態列顯示，見上方「PySide6桌面版」）。常用組合：

| 情境 | 指令 |
|---|---|
| 只想補資料，不要真的發通知 | 加 `--dry-run` |
| 先只補 TWSE，跳過耗時的 TPEx | 加 `--skip-tpex` |
| 快速驗證（幾分鐘內完成） | `python scripts/daily_pipeline.py --date 20260722 --local-db data/tw_stock.db --dry-run --skip-tpex` |
| 不加 `--local-db`（改連線Turso） | 見下方「（可選）之後恢復雲端部署」 |

## 本機每日排程（Windows工作排程器）

不依賴GitHub Actions，改用Windows工作排程器在本機固定時間自動執行，跟兩個前端的
「▶ 手動抓取今日資料」按鈕共用同一份`run_daily_pipeline()`，三者都會更新
`data/pipeline_status.json`，桌面版開著的話右上角會顯示「🔄 更新中...」。

2026-07-24起`fetch_today_twse()`改成官方「每日收盤行情」端點優先、查無資料(收盤前查詢
一律如此)時退回yfinance批次下載盤中即時價當備援(見`scripts/daily_pipeline.py`)，所以
盤中排程也能正常拿到資料、算出即時訊號，不會像之前一樣被誤判成「非交易日」。因此排程改成
盤中每小時跑一次(9:00~13:00)取得即時訊號，收盤時間點(13:30)與收盤後一小時(14:30)各再
加跑一次以盡快拿到官方最終收盤價(daily_data_status表會記錄某次結果是盤中即時價還是官方
收盤價，兩個前端UI會標示「尚未收盤」)。

用系統管理員權限開啟終端機，依序執行（台灣時間，週一到週五）：

```powershell
schtasks /create /tn "tw_stock_pipeline_0900" /tr "python D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:00 /rl highest
schtasks /create /tn "tw_stock_pipeline_1000" /tr "python D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 10:00 /rl highest
schtasks /create /tn "tw_stock_pipeline_1100" /tr "python D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 11:00 /rl highest
schtasks /create /tn "tw_stock_pipeline_1200" /tr "python D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 12:00 /rl highest
schtasks /create /tn "tw_stock_pipeline_1300" /tr "python D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 13:00 /rl highest
schtasks /create /tn "tw_stock_pipeline_1330" /tr "python D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 13:30 /rl highest
schtasks /create /tn "tw_stock_pipeline_1430" /tr "python D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 14:30 /rl highest
```

或用工作排程器GUI（`taskschd.msc`）手動建立7個工作，時間點跟上面一致，並記得每個都勾選：
- 「觸發程序」頁籤 → 進階設定 → **「如果錯過排定的啟動時間，儘快執行工作」**（涵蓋電腦當時
  剛好關機的情況，開機後會自動補跑）。
- 「一般」頁籤 → **「不論使用者登入與否均執行」**（背景執行，不需要停留在登入畫面）。

建立後可以先用 `schtasks /run /tn "tw_stock_pipeline_0900"` 手動觸發一次驗證是否正常執行，
執行紀錄可以在工作排程器的「記錄」頁籤查看，或直接開啟`data/pipeline_status.json`確認。
之後若要移除，逐一用`schtasks /delete /tn "tw_stock_pipeline_0900" /f`（其餘6個同理）。

⚠️ 每小時跑一次全市場批次下載(TWSE+TPEx合計約2000多檔)，實測約需1分鐘內，不會對
TWSE/yfinance造成明顯負擔；但如果之後有更高頻率的需求(例如每15分鐘)，應該重新評估
是否會被來源端限流，這裡先以「使用者本身盤中操作需要的頻率」為準，不做更激進的排程。

**其他重點**：
- **選股邏輯**：目前為 MVP 起點，只接上已用真實資料回測驗證過的 R-TREND-14（多頭短線選股與
  停損停利SOP，信心92/100，見 `src/screener/daily_screener.py`），之後可逐步接上更多規則庫規則。
- **通知**：LINE Messaging API（broadcast，推播給自己）+ Gmail SMTP，跟DB是本機還是雲端無關，
  `run_daily_pipeline()`執行完就會直接發送。

## 需要的憑證（`.env`，可參考 `.env.example`）

本機優先模式下，只有 `FINMIND_API_TOKEN`（取得TPEx股票清單/名稱/產業別）跟LINE/Email那組
（要推播才需要）是必要的；`TURSO_*` 只有之後要恢復雲端部署時才需要設定。

| 變數 | 用途 | 取得方式 |
|---|---|---|
| `FINMIND_API_TOKEN` | TPEx股票基本資料(名稱/產業別)，TPEx股價改用yfinance批次下載(不需要金鑰) | 註冊 [finmindtrade.com](https://finmindtrade.com) |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE推播 | LINE Developers Console 建立 Messaging API 頻道，並用自己帳號加此bot為好友 |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` / `NOTIFY_EMAIL_TO` | Email通知 | Gmail 開啟兩步驟驗證後產生「應用程式密碼」 |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN`（可選） | 之後恢復雲端部署時的雲端資料庫 | 註冊 [turso.tech](https://turso.tech) 建立資料庫 |

## （可選）之後恢復雲端部署

⚠️ 這一節目前是**暫停狀態**：2026-07-23實測發現Turso免費方案的帳號寫入額度用完，會直接在
協定層封鎖所有寫入（HTTP狀態碼仍是200，但回應JSON是`{"code": "BLOCKED", "message": "...do
you need to upgrade your plan?"}`，不是一般的HTTP錯誤狀態碼；`libsql-client 0.3.1`遇到這種
回應形狀時會丟出不含任何上下文的裸`KeyError('result')`，第一次遇到時容易誤判成「多個process
併發寫入互相卡到」的套件bug，實測用繞過套件、直接印出原始HTTP回應JSON的方式才找到真正原因）。
因此改為本機優先架構（見上方章節），`.github/workflows/daily_pipeline.yml`的排程已註解停用
（保留`workflow_dispatch`可手動觸發）。之後如果要恢復：

1. 依上表申請/設定好 `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN`，本機用 `.env`，正式環境用：
   - GitHub repo → Settings → Secrets and variables → Actions，新增同名 secrets
   - Streamlit Community Cloud → App settings → Secrets，貼上同一組
2. 到Turso Dashboard確認額度狀況（升級方案或確認額度已重置）
3. 取消 `.github/workflows/daily_pipeline.yml` 裡 `schedule` 那幾行的註解
4. 跑一次性種子腳本，把本機歷史資料的近期滾動窗口灌進 Turso（否則第一天Turso資料不足以算MA240等指標）：
   ```bash
   python scripts/seed_turso_from_local.py --local-db data/tw_stock.db --days 400
   ```
5. 本機先用 `--dry-run` 驗證整條管線邏輯正確（不會真的發送通知）：
   ```bash
   python scripts/daily_pipeline.py --dry-run
   ```
6. 確認無誤後，到 GitHub repo 的 Actions 頁面手動觸發一次 `daily_pipeline` workflow
   （`workflow_dispatch`），確認排程正確跑完，再放心讓 cron 排程接手。
7. 到 [Streamlit Community Cloud](https://share.streamlit.io) 部署 `dashboard/app.py`
   （設定Secrets、不要設定`LOCAL_DB_PATH`），設定僅限受邀者可檢視。

### 更新雲端

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
如果同時執行（例如背景已經在跑一個，又手動另外開一個一樣的指令），沒有必要地重複寫入，還是應該
避免同時跑兩個。跑之前可以先確認終端機/背景工作有沒有已經在跑的行程。

⚠️ **Turso免費方案的寫入額度用完時，會直接在協定層封鎖所有寫入**（HTTP狀態碼仍是200，但回應
JSON是`{"code": "BLOCKED", "message": "...do you need to upgrade your plan?"}`，不是一般的
HTTP錯誤狀態碼）。`libsql-client 0.3.1`（已停止維護）遇到這種回應形狀時，會直接丟出不含任何
上下文的裸`KeyError('result')`，而不是正常表達「寫入被拒絕」的例外——第一次遇到時容易誤判成
「多個process併發寫入互相卡到」的套件bug（`src/data/turso_client.py`的`executescript()`因此
補了重試機制），但實測用繞過套件、直接印出原始HTTP回應JSON的方式才找到真正原因是**帳號寫入
額度用完**，跟併發與否無關；短暫重試對這種持續性狀態沒有用。**儀表板**（`dashboard/app.py`）
已經把`ensure_schema()`失敗改成不中斷讀取（顯示警告、資料表通常早就存在，讀取不受影響），但
**`daily_pipeline.py`/`seed_turso_from_local.py`等真的需要寫入的流程仍會照常失敗**——遇到時
應該先去 [Turso Dashboard](https://turso.tech) 檢查用量/方案，而不是當成程式bug繼續除錯。

## 目錄結構

- `src/indicators/` `src/strategies/` `src/patterns/` `src/risk/`：246條朱家泓規則庫的程式實作
  （`src/patterns/chart_overlays.py`、`latest_day_summary.py` 是給前端用的整合層）
- `src/screener/`：選股邏輯（`screening_rules.py`為規則庫函式，`daily_screener.py`為每日選股組裝）
- `src/data/`：TWSE官方API/TPEx(`yfinance_client.py`，批次下載)/FinMind(股票基本資料)抓取器、
  SQLite/Turso儲存層、交易日曆(`trading_calendar.py`)、`connection.py`（依LOCAL_DB_PATH
  選擇本機/Turso連線，Streamlit/PySide6前端共用）
- `src/presentation/`：前端無關的圖表資料組裝層（`chart_data.py`：從DB撈資料+畫成Plotly
  Figure；`pipeline_status.py`：每日pipeline執行狀態，供桌面版UI輪詢顯示），Streamlit
  (`dashboard/`)與PySide6(`desktop/`)兩個前端共用同一份，行為保證一致
- `src/notify/`：LINE/Email通知
- `src/backtest/`：回測引擎
- `scripts/`：一次性/排程用的進入點腳本
- `desktop/`：PySide6桌面版（本機優先架構下的主要前端）；`chart_render.py`疊加桌面版專用的
  滑鼠十字線＋左上角動態資訊框效果(仿TradingView)，只有桌面版能用
- `dashboard/`：Streamlit 儀表板（可選，本機或之後上雲皆可）
- `ai/`：電子書逐章精讀筆記（`ebook-summary/`）、規則庫（`zhu-rules/`）、規劃文件（`PLAN.md`）
