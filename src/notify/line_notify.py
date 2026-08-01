"""LINE Messaging API 推播：用 broadcast endpoint 送給所有加這個 bot 為好友的人
（也就是只有使用者自己），不需要另外查詢/儲存特定的 LINE userId。

fetch/parse分離的既有慣例在這裡對應為「格式化(純函式，好測試)」與「真的發送(打網路，不測試)」
分開，比照 src/data/twse_client.py 的風格。
"""

from __future__ import annotations

import re

import requests

from src.data.config import get_line_channel_token

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
MAX_MESSAGE_LENGTH = 5000  # LINE單則文字訊息長度上限

# 從signal_name字串(例如"R-MA-23單一均線短線做空（88%）")抽出信心分數，跟
# src/presentation/chart_data.py的_CONFIDENCE_PATTERN是同一個規則(那邊獨立定義一份，
# 這裡不特地跨模組import一個presentation層的private常數，兩處各自維護同一個簡單regex
# 比額外耦合划算)。
_CONFIDENCE_PATTERN = re.compile(r"（(\d+)%）")


def format_candidates_message(date: str, candidates: list[dict], stock_names: dict[str, str] | None = None) -> str:
    """把每日候選清單格式化成單則LINE文字訊息，超過LINE長度上限時截斷。

    ⚠️ 2026-08-01改版：`candidates`(來自`screen_all_stocks()`)同一檔股票符合多條
    規則時刻意不去重、每條規則各自一筆(見該函式docstring)——原本這裡逐筆列出，
    使用者收到的LINE訊息因此充滿同一檔股票重複出現(只差在觸發哪條規則)的雜訊，
    跟候選清單畫面(`chart_data.load_candidates_for_date()`已經合併成同一檔股票
    一列)差異很大，讓人困惑。改成依stock_id分組，一檔股票只顯示一行
    「{代號}{名稱} 符合規格數{n}」，依信心分數加總由高到低排序(反映「這檔股票有
    多少、多強的訊號重疊」，不是隨意順序)。

    stock_names：{stock_id: name}，省略或查無名稱時該檔只顯示代號。
    """
    if not candidates:
        return f"【{date} 每日選股】今天沒有符合條件的候選股。"
    stock_names = stock_names or {}

    grouped: dict[str, list[dict]] = {}
    for c in candidates:
        grouped.setdefault(c["stock_id"], []).append(c)

    def _confidence_sum(matches: list[dict]) -> int:
        total = 0
        for m in matches:
            match = _CONFIDENCE_PATTERN.search(m.get("signal_name", "") or "")
            if match:
                total += int(match.group(1))
        return total

    ranked = sorted(grouped.items(), key=lambda item: _confidence_sum(item[1]), reverse=True)

    lines = [f"【{date} 每日選股】共{len(grouped)}檔候選："]
    for stock_id, matches in ranked:
        name = stock_names.get(stock_id, "")
        lines.append(f"・{stock_id}{name} 符合規格數{len(matches)}")
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
