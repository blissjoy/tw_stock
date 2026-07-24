"""Gmail SMTP 通知：用應用程式密碼(App Password)透過SMTP_SSL寄送每日選股結果。

寄送函式(打網路)刻意不寫測試，只測格式化函式，比照 line_notify.py 與
src/data/twse_client.py 既有的 fetch/parse 分離慣例。
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from src.data.config import get_gmail_credentials

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
# 2026-07-24排查yfinance批次下載無限期掛住的事故時，順便檢查了pipeline其餘會打網路的
# 步驟——smtplib.SMTP_SSL()原本沒有指定timeout，預設是不限時間等待，同樣有無限期卡住的
# 風險(只是目前.env還沒填齊GMAIL_APP_PASSWORD/NOTIFY_EMAIL_TO，get_gmail_credentials()
# 會在打網路前就先丟RuntimeError，還沒真的踩到)；比照line_notify.py既有的timeout=10，
# 這裡也補上，避免之後補齊憑證後才第一次踩到同一類問題。
SMTP_TIMEOUT_SECONDS = 15


def format_candidates_email_body(date: str, candidates: list[dict]) -> str:
    """把每日候選清單格式化成Email純文字內文。"""
    if not candidates:
        return f"{date} 今天沒有符合條件的候選股。"
    lines = [f"{date} 每日選股，共{len(candidates)}檔候選：\n"]
    for c in candidates:
        lines.append(
            f"{c['stock_id']}｜{c.get('signal_name', '')}｜"
            f"進場價 {c['entry_price']:.2f}｜停損 {c['stop_loss']:.2f}｜{c.get('note', '')}"
        )
    return "\n".join(lines)


def send_email(subject: str, body: str) -> None:
    address, app_password, to_addr = get_gmail_credentials()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = to_addr

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
        server.login(address, app_password)
        server.sendmail(address, [to_addr], msg.as_string())
