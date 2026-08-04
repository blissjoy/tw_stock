"""環境設定：從 .env 檔或環境變數讀取密鑰。不依賴 python-dotenv（專案目前沒有這個套件），
自己寫一個最簡單的 KEY=VALUE 解析器就夠用，不需要為了這麼小的需求多裝一個依賴。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


def _load_dotenv(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


_load_dotenv()


def get_finmind_token() -> str:
    """回傳FINMIND_API_TOKEN，未設定時丟出清楚的錯誤訊息（而非讓後續HTTP呼叫收到401才發現）。"""
    token = os.environ.get("FINMIND_API_TOKEN")
    if not token:
        raise RuntimeError(
            "找不到 FINMIND_API_TOKEN，請在專案根目錄的 .env 檔設定（可參考 .env.example），"
            "或直接設定同名環境變數。"
        )
    return token


def get_turso_credentials() -> tuple[str, str]:
    """回傳 (TURSO_DATABASE_URL, TURSO_AUTH_TOKEN)。"""
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise RuntimeError(
            "找不到 TURSO_DATABASE_URL / TURSO_AUTH_TOKEN，請先到 turso.tech 建立資料庫取得憑證，"
            "在 .env 檔設定（可參考 .env.example）或直接設定同名環境變數。"
        )
    return url, token


def get_line_channel_token() -> str:
    """回傳 LINE_CHANNEL_ACCESS_TOKEN（LINE Messaging API 頻道存取權杖，用於 broadcast 推播）。"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "找不到 LINE_CHANNEL_ACCESS_TOKEN，請先到 LINE Developers Console 建立 Messaging API 頻道取得權杖，"
            "在 .env 檔設定（可參考 .env.example）或直接設定同名環境變數。"
        )
    return token


def get_gmail_credentials() -> tuple[str, str, str]:
    """回傳 (GMAIL_ADDRESS, GMAIL_APP_PASSWORD, NOTIFY_EMAIL_TO)。"""
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    to_addr = os.environ.get("NOTIFY_EMAIL_TO")
    if not address or not app_password or not to_addr:
        raise RuntimeError(
            "找不到 GMAIL_ADDRESS / GMAIL_APP_PASSWORD / NOTIFY_EMAIL_TO，請先在 Gmail 開啟兩步驟驗證後"
            "產生應用程式密碼，在 .env 檔設定（可參考 .env.example）或直接設定同名環境變數。"
        )
    return address, app_password, to_addr


def get_google_oauth_client_config() -> dict:
    """回傳GOOGLE_OAUTH_CLIENT_SECRET_JSON解析後的dict(OAuth Desktop app用戶端設定，
    格式是Google Cloud Console下載的client_secret_*.json整份內容)。

    2026-08-04新增：觀察清單匯出Google Sheet功能(src/data/google_sheets_client.py)
    原本想用服務帳戶(Service Account)金鑰(不需要每次登入)，但使用者的Google Cloud
    專案有「強制執行的組織政策iam.disableServiceAccountKeyCreation」，個人帳號沒有
    機構層級可以解除，改用OAuth Desktop app使用者登入這條路——JSON內容整個塞進單一
    環境變數(跟FINMIND_API_TOKEN等其他密鑰一樣放在.env，不是另外存一個檔案)。
    """
    raw = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
    if not raw:
        raise RuntimeError(
            "找不到 GOOGLE_OAUTH_CLIENT_SECRET_JSON，請先到Google Cloud Console建立OAuth "
            "client ID(Desktop app類型)並發布成正式版，把下載的JSON內容整個貼進 .env 檔"
            "（可參考 .env.example）或直接設定同名環境變數。"
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_SECRET_JSON 內容不是合法的JSON，請確認貼上時沒有被截斷或跳脫錯誤。") from exc


def get_google_sheet_id() -> str:
    """回傳GOOGLE_SHEET_ID(觀察清單自動匯出的目標試算表ID，從試算表網址
    https://docs.google.com/spreadsheets/d/{這一段}/edit 取出)。"""
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError(
            "找不到 GOOGLE_SHEET_ID，請在 .env 檔設定（可參考 .env.example）或直接設定同名環境變數。"
        )
    return sheet_id
