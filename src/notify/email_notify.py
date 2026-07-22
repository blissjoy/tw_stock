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

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(address, app_password)
        server.sendmail(address, [to_addr], msg.as_string())
