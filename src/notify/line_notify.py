"""LINE Messaging API 推播：用 broadcast endpoint 送給所有加這個 bot 為好友的人
（也就是只有使用者自己），不需要另外查詢/儲存特定的 LINE userId。

fetch/parse分離的既有慣例在這裡對應為「格式化(純函式，好測試)」與「真的發送(打網路，不測試)」
分開，比照 src/data/twse_client.py 的風格。
"""

from __future__ import annotations

import requests

from src.data.config import get_line_channel_token

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
MAX_MESSAGE_LENGTH = 5000  # LINE單則文字訊息長度上限


def format_candidates_message(date: str, candidates: list[dict]) -> str:
    """把每日候選清單格式化成單則LINE文字訊息，超過LINE長度上限時截斷。"""
    if not candidates:
        return f"【{date} 每日選股】今天沒有符合條件的候選股。"
    lines = [f"【{date} 每日選股】共{len(candidates)}檔候選："]
    for c in candidates:
        lines.append(
            f"・{c['stock_id']} {c.get('signal_name', '')} "
            f"進場價{c['entry_price']:.2f} 停損{c['stop_loss']:.2f}"
        )
    return "\n".join(lines)[:MAX_MESSAGE_LENGTH]


def send_line_broadcast(text: str) -> None:
    """呼叫 LINE Messaging API 的 broadcast endpoint，送給所有加此頻道為好友的人。"""
    token = get_line_channel_token()
    response = requests.post(
        LINE_BROADCAST_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"messages": [{"type": "text", "text": text}]},
        timeout=10,
    )
    response.raise_for_status()
