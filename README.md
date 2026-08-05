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
  浮動提示框，仿TradingView超級圖表的畫法（`src/presentation/chart_render.py`；桌面版跟
  Streamlit版共用，Streamlit版透過`st.components.v1.html()`嵌入原始HTML+JS）。
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
盤中每小時跑一次(10:00~13:00，9點開盤當下還沒有值得抓的資料所以不排)取得即時訊號，
收盤時間點(13:30)與收盤後一小時(14:30)各再加跑一次以盡快拿到官方最終收盤價
(daily_data_status表會記錄某次結果是盤中即時價還是官方收盤價，兩個前端UI會標示
「尚未收盤」)。

✅ **這8個排程工作已經在本機建立好了**（前6個2026-07-24建立，17:00/21:00這2個
2026-08-03新增，見`ai/PLAN.md`同日期章節）。以下指令留著給之後要在其他機器上重新設定、
或排程被誤刪需要重建時參考：

```powershell
schtasks /create /tn "tw_stock_pipeline_1000" /tr "C:\path\to\python.exe D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 10:00
schtasks /create /tn "tw_stock_pipeline_1100" /tr "C:\path\to\python.exe D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 11:00
schtasks /create /tn "tw_stock_pipeline_1200" /tr "C:\path\to\python.exe D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 12:00
schtasks /create /tn "tw_stock_pipeline_1300" /tr "C:\path\to\python.exe D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 13:00
schtasks /create /tn "tw_stock_pipeline_1330" /tr "C:\path\to\python.exe D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 13:30
schtasks /create /tn "tw_stock_pipeline_1430" /tr "C:\path\to\python.exe D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 14:30
schtasks /create /tn "tw_stock_pipeline_1700" /tr "C:\path\to\python.exe D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db --dry-run --skip-tpex" /sc weekly /d MON,TUE,WED,THU,FRI /st 17:00
schtasks /create /tn "tw_stock_pipeline_2100" /tr "C:\path\to\python.exe D:\tw_stock\scripts\daily_pipeline.py --local-db D:\tw_stock\data\tw_stock.db --dry-run --skip-tpex" /sc weekly /d MON,TUE,WED,THU,FRI /st 21:00
```

`C:\path\to\python.exe`要換成`where python`查到的實際直譯器完整路徑（例如
`C:\Users\你的帳號\AppData\Local\Programs\Python\Python314\python.exe`）——排程觸發時
不一定會套用互動式終端機的PATH設定，直接寫死完整路徑比較保險，用純`python`指令可能因為
排程環境解析不到而靜默失敗。

**17:00/21:00這兩個排程的用途**：三大法人買賣超／融資融券資料(`institutional_investors`/
`margin_trading`表，`fetch_today_twse()`裡跟股價同一批抓)有時TWSE公布時間比收盤價晚，
14:30那次不一定抓得齊全，所以晚上再補跑兩次盡量拿到當天最終的法人/資券數字。這兩個
排程刻意加了`--dry-run --skip-tpex`：`--dry-run`是因為候選清單此時通常跟14:30那次
完全一樣(目前選股規則都還沒用到法人/資券資料，見`scripts/daily_pipeline.py`模組
docstring)，不用再發一次重複的LINE/Email通知；`--skip-tpex`是因為法人/資券資料
本來就只有TWSE上市股票才有(TPEx這兩張表完全沒有抓取來源，見同一份docstring)，跳過
TPEx股價重抓可以加快這兩次排程的執行時間。

⚠️ 實際建立時**拿掉了`/rl highest`**（原本以為要有這個旗標，但套用後`schtasks /create`
會要求系統管理員權限、直接回`ERROR: Access is denied.`）——這支腳本只做HTTP請求跟寫自己
目錄下的sqlite檔案，本來就不需要最高權限，不加這個旗標反而更安全(最小權限原則)，也不需要
用系統管理員權限開終端機才能建立。

或用工作排程器GUI（`taskschd.msc`）手動建立/檢視這8個工作，並記得都勾選：
- 「觸發程序」頁籤 → 進階設定 → **「如果錯過排定的啟動時間，儘快執行工作」**（涵蓋電腦當時
  剛好關機的情況，開機後會自動補跑）。
- 「一般」頁籤 → **「不論使用者登入與否均執行」**（背景執行，不需要停留在登入畫面）。

可以用 `schtasks /run /tn "tw_stock_pipeline_1000"` 手動觸發一次驗證是否正常執行，
執行紀錄可以在工作排程器的「記錄」頁籤查看，或直接開啟`data/pipeline_status.json`確認。
之後若要移除，逐一用`schtasks /delete /tn "tw_stock_pipeline_1000" /f`（其餘7個同理）。

**`data/pipeline_run_history.jsonl`**：2026-08-04新增，每次`run_daily_pipeline()`執行
完畢都會累加一行(不覆寫)，記錄執行時間/`is_intraday`/候選檔數，以及當天`sar_flip_days_
ago<=3`(最近翻轉、最容易受資料事後修正影響)的股票完整指標值——起因是同一個交易日內
不同時段排程跑出來的候選清單曾經對不上，但`stock_prices`/`daily_indicators`都是直接
覆寫、事後查不到證據，才補上這份歷史紀錄，供之後比對。純append-only文字檔，會隨時間
持續增長(實測每次執行約100~150KB)，需要時可以自行清空或刪除，不影響pipeline運作。

⚠️ 每小時跑一次全市場批次下載(TWSE+TPEx合計約2000多檔)，實測約需1分鐘內，不會對
TWSE/yfinance造成明顯負擔；但如果之後有更高頻率的需求(例如每15分鐘)，應該重新評估
是否會被來源端限流，這裡先以「使用者本身盤中操作需要的頻率」為準，不做更激進的排程。

**其他重點**：
- **選股邏輯**：目前為 MVP 起點，只接上已用真實資料回測驗證過的 R-TREND-14（多頭短線選股與
  停損停利SOP，信心92/100，見 `src/screener/daily_screener.py`），之後可逐步接上更多規則庫規則。
- **通知**：LINE Messaging API（broadcast，推播給自己）+ Gmail SMTP，跟DB是本機還是雲端無關，
  `run_daily_pipeline()`執行完就會直接發送。

### 第9個排程：本機→Turso同步（`scripts/sync_local_to_turso.py`）

web版部署到雲端後，如果同時也讓web版自己按「▶ 手動抓取今日資料」，同一天的法人/資券
資料會被FinMind抓兩次(本機排程一次、web版一次)——FinMind是**每小時**限額(300~600次/
小時，過去實測撞過402被限流)，不是Turso那種寬裕到「正常用量不到1%」的月額度，重複抓取
是真的會撞到額度的風險。改成本機排程抓完資料後，直接把本機sqlite的資料**推**到Turso
(完全不呼叫FinMind/TWSE)，web版不用自己再抓一次：

```powershell
schtasks /create /tn "tw_stock_sync_to_turso_2115" /tr "C:\path\to\python.exe D:\tw_stock\scripts\sync_local_to_turso.py --local-db D:\tw_stock\data\tw_stock.db" /sc weekly /d MON,TUE,WED,THU,FRI /st 21:15
```

排在21:00那個排程之後15分鐘觸發，確保當天最後一次的本機資料都已經寫完。

⚠️ **這個排程刻意只設一個時段，不要比照上面8個排程各自複製一份**：Turso免費方案每月
1000萬列寫入額度，這支腳本一次同步約15.8萬列(最近10個交易日的股價/法人/資券/指標/
候選清單)，**一天一次**約475萬列/月(占額度47.5%，留了一半以上餘裕給web版自己的手動
抓取/回補資料功能)；如果比照本機8個排程各自觸發一次，一個月會逼近甚至超過額度上限。
建立排程前，先手動跑一次`--dry-run`確認預計同步的列數合理，且不會意外連線到Turso：

```bash
python scripts/sync_local_to_turso.py --local-db data/tw_stock.db --dry-run
```

每次執行(含dry-run)都會在`data/sync_to_turso_log.jsonl`(append-only)記一筆執行紀錄
(各表列數/成功或失敗)，排程是無人值守執行，之後要確認「昨晚同步到底有沒有跑」不用去翻
工作排程器的記錄畫面。

## 需要的憑證（`.env`，可參考 `.env.example`）

本機優先模式下，只有 `FINMIND_API_TOKEN`（取得TPEx股票清單/名稱/產業別）跟LINE/Email那組
（要推播才需要）是必要的；`TURSO_*` 只有之後要恢復雲端部署時才需要設定。

| 變數 | 用途 | 取得方式 |
|---|---|---|
| `FINMIND_API_TOKEN` | TPEx股票基本資料(名稱/產業別)，TPEx股價改用yfinance批次下載(不需要金鑰) | 註冊 [finmindtrade.com](https://finmindtrade.com) |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE推播 | LINE Developers Console 建立 Messaging API 頻道，並用自己帳號加此bot為好友 |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` / `NOTIFY_EMAIL_TO` | Email通知 | Gmail 開啟兩步驟驗證後產生「應用程式密碼」 |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN`（可選） | 之後恢復雲端部署時的雲端資料庫 | 註冊 [turso.tech](https://turso.tech) 建立資料庫 |
| `TURSO_PORTFOLIO_DATABASE_URL` / `TURSO_PORTFOLIO_AUTH_TOKEN`（可選） | 庫存清單/觀察清單雲端持久化，跟上面主DB**分開的另一個**Turso資料庫 | 同樣在 [turso.tech](https://turso.tech) 另外建立一個資料庫 |
| `ADMIN_ACCESS_CODE`（可選，web版佈署到公開網址時建議設定） | web版「回補資料」分頁+「▶ 手動抓取今日資料」按鈕共用的存取密碼——這兩個動作都會對主DB的Turso帳號寫入、消耗FinMind額度，手動抓取還會觸發真實LINE/Email通知，公開網址下任何訪客都能觸發，未設定時這兩個功能會顯示「已停用」 | 自己設一組字串即可，不是向外部服務申請 |

⚠️ 庫存清單/觀察清單(`data/portfolio.db`)刻意用**跟主DB分開的第二個Turso資料庫**，不是
共用同一個——主DB的Turso帳號曾經寫入額度用完被封鎖過(見下方說明)，獨立開一個資料庫讓
「使用者互動觸發的頻繁小額寫入」跟「每日排程的批次寫入」的額度互不影響。本機/桌面版
使用不需要設定這兩個變數(`desktop/main.py`已經固定指向本機檔案)；web版只要不設定
`PORTFOLIO_DB_PATH`，就會自動改連這裡設定的Turso資料庫。第一次接上時，用下面指令把
本機既有的庫存/觀察清單資料搬過去一次：
```bash
python scripts/seed_turso_portfolio_from_local.py --local-db data/portfolio.db
```

### 把憑證加到部署環境（Streamlit Community Cloud / GitHub Actions）

`.env` 只有本機讀得到，部署到雲端後要另外在對應平台各自設定一次，兩邊是**完全獨立**的
密鑰系統、互不相通：

- **Streamlit Community Cloud**（web版實際在跑的地方，`TURSO_PORTFOLIO_DATABASE_URL`/
  `TURSO_PORTFOLIO_AUTH_TOKEN`沒設定會直接讓app crash，因為`get_default_portfolio_
  connection()`在沒有`PORTFOLIO_DB_PATH`時一定會嘗試連Turso）：
  1. 到 [share.streamlit.io](https://share.streamlit.io)，找到已部署的app
  2. 右下角「⋮」（或 App settings）→ **Settings → Secrets**
  3. 用TOML格式貼上（一次貼全部變數，不是只貼缺的那兩個——每次儲存會整份覆蓋）：
     ```toml
     TURSO_DATABASE_URL = "libsql://your-database.turso.io"
     TURSO_AUTH_TOKEN = "your-turso-auth-token"
     TURSO_PORTFOLIO_DATABASE_URL = "libsql://your-portfolio-database.turso.io"
     TURSO_PORTFOLIO_AUTH_TOKEN = "your-turso-portfolio-auth-token"
     ADMIN_ACCESS_CODE = "your-admin-access-code"
     FINMIND_API_TOKEN = "your-finmind-api-token"
     ```
  4. 存檔後app會自動重新啟動套用新的secrets（沒有自動重啟的話，「⋮」→ Reboot app）

- **GitHub Actions**（只有`.github/workflows/daily_pipeline.yml`的排程用得到，這個排程
  目前**還是註解停用狀態**，所以現在還不需要設定這裡——只有之後真的要恢復GitHub Actions
  自動排程時才需要）：
  1. GitHub repo 頁面 → **Settings → Secrets and variables → Actions**
  2. 「New repository secret」，一個一個新增（跟上面同樣的變數名稱、同樣的值）

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

## ⚠️ 已知問題（Known Issues）

### 少數「近期從上櫃/興櫃轉上市」的股票，均線/SAR可能不準確

**現象**：極少數股票的均線多頭排列/SAR翻轉判斷結果，會跟只用yfinance資料獨立計算的
結果（例如`ref-project/`那套工具）不一致，且用TWSE官方端點重新查詢那檔股票「很早以前」
的日期，會查不到資料——即使我們資料庫裡明明存有那個日期的價格。

**根因**：TWSE歷史回補（`scripts/backfill_history.py`的`backfill_twse()`）只走
`twse_client`（TWSE官方「每日收盤行情」端點），這個端點只會回傳「當時已經是上市股」
的資料。但如果一檔股票是「先在上櫃/興櫃交易一段時間、之後才轉上市」，我們的
`stocks`表只記錄它**目前**的市場別（`market='TWSE'`），歷史回補時如果誤把它當成
「一直都是上市股」去查詢，TWSE官方端點對「轉上市之前」的日期會查無資料而略過——
但如果`stock_prices`裡那些「轉上市前」的資料其實是透過別的路徑（例如上櫃股票用
FinMind抓）進來、又沿用同一個`stock_id`寫入，就會出現「均線/SAR的歷史計算，把
興櫃/上櫃時期（流動性、造市機制都跟上市不同）的價格，跟轉上市後的價格接成同一條
連續序列」的情況——SAR是路徑相關的遞迴指標，對這種「兩個市場拼接」特別敏感，容易
算出偏離的結果。

**已知受影響股票**（2026-08-02用即時查詢TWSE官方端點逐檔驗證過，只有這4檔）：

| 代號 | 名稱 | 我們最早資料日期 | 備註 |
|---|---|---|---|
| 5236 | 凌陽創新 | 2024-07-22 | |
| 6947 | 台鎔科技 | 2024-07-22 | 已確認上市掛牌日為2026-07-23 |
| 7827 | 漢康-KY創 | 2025-06-23 | |
| 7689 | 大鵬科CLMX | 2025-10-07 | |

檢查範圍：所有`market='TWSE'`且最早資料日期晚於2023-01-10（回補起點）的125檔股票，
逐一用TWSE官方端點即時查詢其最早資料日期當天是否真的查得到——**只有這4檔查不到**，
其餘121檔都是乾淨的新股上市資料，不受影響。範圍很小、不是系統性問題。

**目前狀態**：只記錄，尚未修正。如果要修正，做法是把這4檔「轉上市之前」的
`stock_prices`歷史刪掉，只保留轉上市後的資料，並重新跑一次
`scripts/backfill_daily_indicators.py --stock-id <代號>`讓`daily_indicators`
快取跟著更新。詳細查證過程見`ai/PLAN.md`同標題章節。

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
- `desktop/`：PySide6桌面版（本機優先架構下的主要前端）
- `dashboard/`：Streamlit 儀表板（可選，本機或之後上雲皆可）；跟桌面版共用
  `src/presentation/chart_render.py`疊加滑鼠十字線＋左上角動態資訊框效果(仿TradingView)
- `ai/`：電子書逐章精讀筆記（`ebook-summary/`）、規則庫（`zhu-rules/`）、規劃文件（`PLAN.md`）
