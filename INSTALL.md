# Windows 11 安裝與搬遷指南

本指南說明如何在一台全新的 Windows 11 電腦上安裝 `tw_stock`，以及如何從舊電腦搬入既有資料。主要使用方式是 PySide6 桌面版；本機執行不需要設定 Turso 或部署雲端服務。

## 1. 安裝必要工具

### 自動安裝（建議）

先下載或複製本專案，然後在專案根目錄執行 `setup_windows.bat`（可在檔案總管雙擊）。它會在需要時安裝 Git 與 Python 3.12，建立 `.venv`、安裝 `requirements.txt` 中的套件，並在尚未有 `.env` 時複製 `.env.example`。安裝 Python 時即使 `py` 指令無法辨識，腳本也會嘗試直接找到 Python 執行檔。

此方式需要 Windows 的 `winget` 與網路連線；套件安裝可能會顯示系統確認視窗。若程式碼尚未下載，請先依「下載程式碼」取得專案，再執行腳本。

### 手動安裝

### Git

用來從 GitHub 下載程式碼。以一般使用者權限開啟 PowerShell，執行：

```powershell
winget install --id Git.Git -e
```

### Python 3.12（64 位元）

專案的 GitHub Actions 使用 Python 3.12。安裝 Python Launcher，之後可用 `py -3.12` 明確指定版本：

```powershell
winget install --id Python.Python.3.12 -e
```

安裝完成後關閉並重新開啟 PowerShell，確認工具可用：

```powershell
git --version
py -3.12 --version
```

若電腦沒有 `winget`，可改由 [Git for Windows](https://git-scm.com/download/win) 與 [Python 3.12](https://www.python.org/downloads/windows/) 官方下載頁安裝。Python 安裝精靈請保留 Python Launcher；若要在終端機直接使用 `python`，也勾選 **Add python.exe to PATH**。

## 2. 下載程式碼

選擇存放位置後執行：

```powershell
cd $HOME
git clone https://github.com/blissjoy/tw_stock.git
cd .\tw_stock
```

若程式碼已透過其他方式複製到新電腦，請在 PowerShell 切換到該專案根目錄，後續步驟相同。

## 3. 建立虛擬環境並安裝套件

在專案根目錄執行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

虛擬環境只屬於目前這台電腦，不要從舊電腦複製 `.venv`；每台電腦都應依 `requirements.txt` 重新安裝。

若套件安裝時出現網路或編譯錯誤，先確認已安裝 64 位元 Python 3.12，並重新執行上述 pip 指令。一般桌面版不需要另外安裝 Node.js、資料庫伺服器或 SQLite。

## 4. 搬入舊電腦的本機資料（若適用）

若要保留舊電腦的股價資料、庫存清單與模擬紀錄：

1. 在舊電腦完全關閉桌面版、Streamlit 和相關資料更新排程，避免複製正在使用中的 SQLite 資料庫。
2. 將下列檔案從舊電腦的專案 `data` 資料夾複製到新電腦的 `data` 資料夾。若新電腦尚無 `data` 資料夾，先建立它。
   - `tw_stock.db`：股價及選股等主要資料。已有資料庫時，複製此檔才能看到既有歷史資料。
   - `portfolio.db`：庫存清單與觀察清單。
   - `sar_simulation.db`：策略模擬紀錄。
   - `google_oauth_token.json`：只有要延續 Google Sheets 登入狀態時才需要；此檔含授權資訊，請用安全方式傳輸並妥善保管。不複製也可在新電腦重新授權。
3. 確認檔案已複製完成，再啟動程式。

資料庫檔案不會由 Git 下載（`.gitignore` 會忽略 `*.db`）。舊電腦若沒有上述檔案，可略過；第一次啟動時程式會建立需要的本機資料庫，但新資料庫不含舊的歷史紀錄。`data` 裡的 JSON/JSONL 執行狀態與記錄檔通常不是啟動必要資料，可不搬。

## 5. 設定 `.env` 憑證

在專案根目錄建立本機設定檔：

```powershell
Copy-Item .env.example .env
notepad .env
```

若是從舊電腦搬遷，也可以透過安全的私人管道複製舊電腦的 `.env`，以保留原有設定；不要把它放進 Git 或公開分享。

依照要使用的功能填入設定：

- **每日資料更新／執行資料管線：**設定 `FINMIND_API_TOKEN`。請從 [FinMind](https://finmindtrade.com) 取得自己的 API Token。
- **LINE 或 Email 通知：**分別設定 `.env.example` 中的 LINE 或 Gmail 相關欄位；不使用通知可留空。
- **Google Sheets 匯出：**設定 Google OAuth 用戶端 JSON 與試算表 ID；不使用匯出功能可留空。
- **Turso：**桌面版預設使用本機 SQLite，不需填 `TURSO_*`。

即使尚未設定 API Token，通常仍可啟動桌面版並檢視已搬入的本機資料；但需要向外部來源更新資料時，必須設定 `FINMIND_API_TOKEN`。`.env` 含密鑰且不會被 Git 追蹤，請勿提交或公開分享。

## 6. 啟動桌面版

在專案根目錄執行：

```powershell
.\.venv\Scripts\python.exe desktop\main.py
```

桌面版預設使用專案內的 `data\tw_stock.db`、`data\portfolio.db` 及 `data\sar_simulation.db`。若畫面沒有歷史資料，請先確認主要資料庫 `data\tw_stock.db` 是否已從舊電腦複製。

## 可選功能

### MOPS 公司增減資資料抓取

只有要執行 `scripts\fetch_mops_capital_changes.py` 時，才需要下載 Playwright 使用的 Chromium：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

### Streamlit 網頁版

若要在本機啟動可選的網頁版：

```powershell
$env:LOCAL_DB_PATH = "data/tw_stock_dev.db"
.\.venv\Scripts\python.exe -m streamlit run dashboard\app.py
```

停止後若要清除目前 PowerShell 工作階段的設定：

```powershell
Remove-Item Env:LOCAL_DB_PATH
```

### 網頁版 PDF 匯出

`requirements.txt` 已安裝 WeasyPrint，但 Windows 還需要 GTK 3 執行期函式庫才能使用網頁版的 PDF 匯出。若有使用此功能，請依照 [WeasyPrint 官方 Windows 安裝說明](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows)安裝所需原生函式庫，並依文件設定執行檔搜尋路徑。此額外元件不影響 PySide6 桌面版的一般啟動。

### 自動每日更新

新電腦不會自動帶入舊電腦的 Windows 工作排程器工作。若要在本機排程自動更新，請先完成 `.env` 憑證設定，再依照 [README 的「本機每日排程」章節](README.md)重新建立工作，並確認排程使用新電腦的專案路徑與 `.venv\Scripts\python.exe`。

## 驗證安裝（可選）

在專案根目錄執行測試：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -q
```

## 問題排查

- **找不到 `py`：**重新開啟 PowerShell；若仍無法辨識，執行專案根目錄的 `setup_windows.bat`，它會嘗試使用 Python 執行檔的完整路徑。手動安裝時，請確認 Python Launcher 已安裝，或從 Python 3.12 安裝目錄直接執行 `python.exe`。
- **PowerShell 不允許啟用虛擬環境：**本指南使用 `.venv\Scripts\python.exe` 直接執行，不需要啟用虛擬環境。
- **`pip install` 後仍找不到套件：**確認安裝與啟動都使用專案的 `.venv\Scripts\python.exe`，不要混用其他 Python。
- **桌面版可開啟但畫面沒有資料：**確認已複製 `data\tw_stock.db`，並檢查應用程式實際使用的資料夾是目前專案目錄中的 `data`。
- **資料更新時提示缺少 FinMind Token：**在專案根目錄 `.env` 設定 `FINMIND_API_TOKEN` 後重新啟動程式。
