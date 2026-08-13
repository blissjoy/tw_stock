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


def _fmt_num(value: float | None, decimals: int = 2, signed: bool = False) -> str:
    """數字轉字串，缺值顯示"-"——庫存警示裡現價/成本/損益%都可能因為使用者沒填
    成本價、或查無最新股價而缺值，不能直接f-string格式化(None會丟TypeError)。"""
    if value is None:
        return "-"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.{decimals}f}"


def format_candidates_message(
    date: str, candidates: list[dict], stock_names: dict[str, str] | None = None,
    stock_industries: dict[str, str] | None = None,
    inventory_warnings: list[dict] | None = None,
) -> str:
    """把每日候選清單格式化成單則LINE文字訊息，超過LINE長度上限時截斷。

    ⚠️ 2026-08-01改版：`candidates`(來自`screen_all_stocks()`)同一檔股票符合多條
    規則時刻意不去重、每條規則各自一筆(見該函式docstring)——原本這裡逐筆列出，
    使用者收到的LINE訊息因此充滿同一檔股票重複出現(只差在觸發哪條規則)的雜訊，
    跟候選清單畫面(`chart_data.load_stock_universe_for_date()`已經合併成同一檔股票
    一列)差異很大，讓人困惑。改成依stock_id分組，一檔股票只顯示一行
    「{代號}{名稱}(產業別) 符合規格數{n}」，依信心分數加總由高到低排序(反映「這檔
    股票有多少、多強的訊號重疊」，不是隨意順序)。

    stock_names：{stock_id: name}，省略或查無名稱時該檔只顯示代號。
    stock_industries(2026-08-13新增)：{stock_id: 產業別}，省略或查無產業別時不顯示
    括號那段——使用者反映只看代號+名稱不容易一眼分辨這檔股票的產業背景。

    inventory_warnings(2026-08-12新增，2026-08-13改版)：庫存逃命示警清單，每筆
    {"stock_id", "name", "close", "cost_price", "return_pct"}，只放「目前有逃命
    示警」的持股(呼叫端已經用src.presentation.portfolio_data.load_escape_signals_
    for_stocks()篩過)。3種狀態：
    - None(預設)：庫存檢查沒有執行/失敗，訊息裡完全不出現「【庫存警示】」這段——
      維持跟候選清單通知獨立的降級行為，檢查失敗不該讓使用者誤以為「有檢查、沒問題」。
    - []（空清單）：庫存檢查有跑，但目前沒有任何一檔持股有逃命示警，顯示「安全」。
    - 非空清單：逐檔列出「{名稱} 現{現價} 成{成本} {損益%}」，現價/成本/損益%缺值
      (例如使用者沒填成本價)時顯示"-"，不是讓TypeError直接炸掉整則通知。
    使用者明確要求跟候選清單同一則LINE訊息一起發送，不要分成兩則——理由是庫存
    損益是整個系統存在的關鍵，獨立成另一則訊息容易被候選清單通知的訊息量淹沒、
    漏看。截斷長度限制(MAX_MESSAGE_LENGTH)在兩段都組完之後才套用一次，不是候選
    清單先自己截斷一次、庫存警示再疊加上去(那樣總長度可能超過LINE單則訊息上限)。
    """
    if not candidates:
        lines = [f"【{date} 每日選股】今天沒有符合條件的候選股。"]
    else:
        stock_names = stock_names or {}
        stock_industries = stock_industries or {}
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
            industry = stock_industries.get(stock_id, "")
            industry_suffix = f"({industry})" if industry else ""
            lines.append(f"・{stock_id}{name}{industry_suffix} 符合規格數{len(matches)}")

    if inventory_warnings is not None:
        lines.append("")
        lines.append("【庫存警示】")
        if inventory_warnings:
            for w in inventory_warnings:
                close_text = _fmt_num(w.get("close"))
                cost_text = _fmt_num(w.get("cost_price"))
                return_pct_text = _fmt_num(w.get("return_pct"), decimals=1, signed=True)
                return_pct_suffix = "%" if w.get("return_pct") is not None else ""
                lines.append(f"・{w.get('name', '')} 現{close_text} 成{cost_text} {return_pct_text}{return_pct_suffix}")
        else:
            lines.append("安全")

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
