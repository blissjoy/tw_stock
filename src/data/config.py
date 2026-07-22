"""環境設定：從 .env 檔或環境變數讀取密鑰。不依賴 python-dotenv（專案目前沒有這個套件），
自己寫一個最簡單的 KEY=VALUE 解析器就夠用，不需要為了這麼小的需求多裝一個依賴。
"""

from __future__ import annotations

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
