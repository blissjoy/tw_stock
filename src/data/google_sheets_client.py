"""Google Sheets匯出用的底層用戶端：OAuth Desktop app授權流程＋基礎的讀寫/格式化操作。

刻意不含任何「觀察清單」/「黃豐凱籌碼分析法」的領域知識——那些「要匯出哪些欄位、
group成幾個分頁、哪個儲存格該是什麼顏色」的組裝邏輯放在
`src/presentation/watchlist_export.py`，這裡只負責跟Google Sheets API打交道，之後
如果有其他資料也要匯出Google Sheet，可以直接重用這個模組。

⚠️ 認證方式的來龍去脈：原本想用服務帳戶(Service Account)金鑰(免使用者互動、最適合
daily_pipeline.py背景自動執行)，但使用者的Google Cloud專案有「強制執行的組織政策
iam.disableServiceAccountKeyCreation」，個人帳號沒有機構層級的權限可以解除，改用
OAuth Desktop app使用者登入這條路。第一次呼叫`get_authorized_client()`時會跳出瀏覽器
要求登入同意，把取得的token存進本機`TOKEN_PATH`；之後每次呼叫都直接讀這份快取，
過期時用內含的refresh token自動換發新的，不需要每次都重新跳瀏覽器——前提是使用者
照指示把OAuth同意畫面從「測試中」發布成「正式版」，否則refresh token會在7天後失效，
daily_pipeline.py的自動排程沒多久就會又跳回要求瀏覽器登入(背景執行時無法互動，會直接
失敗)。
"""

from __future__ import annotations

from pathlib import Path

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from src.data.config import get_google_oauth_client_config

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TOKEN_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "google_oauth_token.json"


class GoogleAuthRequiresInteractionError(RuntimeError):
    """在interactive=False的情境(例如daily_pipeline.py的自動排程)下，本機沒有可用/
    可刷新的token，需要跳瀏覽器互動才能取得新授權——排程背景執行時不會有人在旁邊
    完成登入，直接跳瀏覽器只會讓整條pipeline卡住(run_local_server()會無限期等待
    永遠不會到來的callback)，改成拋出這個例外讓呼叫端能明確跳過，而不是憑空卡住。
    """


def _load_or_refresh_credentials(interactive: bool = True) -> Credentials:
    """interactive=False時，如果沒有可用(或可用refresh token刷新)的本機快取token，
    不會跳瀏覽器要求登入，而是直接拋出GoogleAuthRequiresInteractionError——見該
    例外類別的說明。desktop/main_window.py手動點「匯出到Google Sheet」按鈕時維持
    預設的interactive=True(使用者就坐在電腦前，跳瀏覽器沒問題)。
    """
    creds: Credentials | None = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif interactive:
        client_config = get_google_oauth_client_config()
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(port=0)
    else:
        raise GoogleAuthRequiresInteractionError(
            "本機沒有可用的Google授權token(或已過期且無法自動刷新)，自動排程情境下"
            "不會跳瀏覽器要求登入——請先在桌面版手動點一次「匯出到Google Sheet」"
            "完成一次互動式登入，之後自動排程才能沿用快取的token。"
        )

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_authorized_client(interactive: bool = True) -> gspread.Client:
    """回傳已授權的gspread Client。第一次呼叫(或token過期且無法用refresh token換發)時
    會跳出瀏覽器要求登入同意，之後的呼叫直接用本機快取的token，不會再跳瀏覽器。

    interactive=False時(daily_pipeline.py自動排程用)不會跳瀏覽器，改成沒有可用token
    就直接拋出GoogleAuthRequiresInteractionError，見_load_or_refresh_credentials()。
    """
    creds = _load_or_refresh_credentials(interactive=interactive)
    return gspread.authorize(creds)


def get_or_create_worksheet(spreadsheet: gspread.Spreadsheet, title: str, rows: int, cols: int) -> gspread.Worksheet:
    """回傳指定分頁(tab)，不存在就建立一個新的——每個觀察清單群組各自對應一個分頁。"""
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=str(rows), cols=str(cols))


def _hex_to_rgb_fraction(hex_color: str) -> dict[str, float]:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return {"red": r, "green": g, "blue": b}


def write_formatted_table(
    spreadsheet_id: str,
    worksheet_title: str,
    values: list[list[str]],
    background_colors: dict[tuple[int, int], str] | None = None,
    text_colors: dict[tuple[int, int], str] | None = None,
    bold_cells: set[tuple[int, int]] | None = None,
    merges: list[tuple[int, int, int, int]] | None = None,
    interactive: bool = True,
) -> None:
    """把一份表格(2維陣列，含表頭)整批寫進指定分頁，清掉舊內容/舊合併儲存格後重新寫入。

    values：表格內容，第一列開始就是實際要顯示的資料(呼叫端自己決定要不要含表頭列)。
    background_colors：{(row, col): "#RRGGBB", ...}，0-indexed，套用儲存格底色
    (對應分類群組標籤列的色塊)。
    text_colors：{(row, col): "#RRGGBB", ...}，套用文字顏色(對應D~R欄位紅漲綠跌的
    既有慣例，跟原始參考來源、桌面版UI一致用文字顏色而不是儲存格底色)。
    bold_cells：需要加粗的儲存格座標集合(分類群組標籤列)。
    interactive：見get_authorized_client()，daily_pipeline.py自動排程要傳False。
    merges：[(start_row, start_col, end_row, end_col), ...]，0-indexed、含端點，
    對應觀察清單畫面的分類群組合併儲存格(見watchlist_export.py)。

    每次呼叫都是「清空整張分頁重新寫入」，不是增量更新——匯出的資料本來就是「這次
    當下觀察清單的完整狀態」，不需要保留分頁裡先前殘留的舊內容。
    """
    client = get_authorized_client(interactive=interactive)
    spreadsheet = client.open_by_key(spreadsheet_id)
    row_count = max(len(values), 10)
    col_count = max((len(r) for r in values), default=10)
    worksheet = get_or_create_worksheet(spreadsheet, worksheet_title, rows=row_count, cols=col_count)

    worksheet.clear()
    full_range = f"A1:{gspread.utils.rowcol_to_a1(worksheet.row_count, worksheet.col_count)}"
    worksheet.unmerge_cells(full_range)

    if values:
        worksheet.update(values, "A1")

    if merges:
        for start_row, start_col, end_row, end_col in merges:
            start_a1 = gspread.utils.rowcol_to_a1(start_row + 1, start_col + 1)
            end_a1 = gspread.utils.rowcol_to_a1(end_row + 1, end_col + 1)
            worksheet.merge_cells(f"{start_a1}:{end_a1}")

    bold_cells = bold_cells or set()
    format_by_cell: dict[tuple[int, int], dict] = {}
    for cell, color in (background_colors or {}).items():
        format_by_cell.setdefault(cell, {})["backgroundColor"] = _hex_to_rgb_fraction(color)
    for cell, color in (text_colors or {}).items():
        format_by_cell.setdefault(cell, {}).setdefault("textFormat", {})["foregroundColor"] = _hex_to_rgb_fraction(color)
    for cell in bold_cells:
        format_by_cell.setdefault(cell, {}).setdefault("textFormat", {})["bold"] = True

    if format_by_cell:
        format_requests: list[gspread.worksheet.CellFormat] = [
            {"range": gspread.utils.rowcol_to_a1(row + 1, col + 1), "format": fmt}
            for (row, col), fmt in format_by_cell.items()
        ]
        worksheet.batch_format(format_requests)
