"""結構化紀錄「這次資料抓取實際發生了什麼」，供web+桌面版新增的「日誌」分頁顯示——
使用者2026-08-06要求：每次自動排程/手動抓取/回補，各市場(TWSE/TPEx)各表(股價/法人/
資券)寫了幾筆、欄位有哪些、有沒有失敗，以及(之後web版做的時候)有沒有同步到Turso。

跟`pipeline_status.py`既有的兩個機制是不同用途，不整合進同一個檔案：
- `write_status()`/`read_status()`：目前「正在執行中」的即時輪詢狀態，用完即丟，
  不是歷史紀錄。
- `append_run_snapshot()`：SAR翻轉事後診斷專用，只記錄`sar_flip_days_ago<=3`的
  股票，不是給使用者看的「這次抓了什麼」總覽。
這裡是給使用者(不是給開發者除錯)看的「日誌」內容，欄位設計以「使用者想知道什麼」
為主，不是內部診斷用途。

⚠️ 純本機檔案(`data/data_fetch_log.jsonl`)，不寫進Turso——使用者2026-08-06明確
要求用寫檔案的方式，避免消耗Turso寫入額度(這個log功能本身不是選股候選清單那種
關鍵資料，遺失也不影響核心功能，不值得用寫入額度換)。跟`pipeline_run_history.jsonl`
不同的是這裡有做保留期限清理(`RETENTION_DAYS`)，不會像那份檔案一樣無限膨脹
(那份檔案寫這裡時已經累積到4.2MB，從來沒清理過)。

計數方式：不修改`scripts/daily_pipeline.py`裡`fetch_today_twse()`/`fetch_today_
tpex()`的回傳值(那樣會牽動一大票既有測試的tuple unpacking)，改成事後直接查DB
「這個日期範圍、這個市場，現在各表各有幾筆」——這也更準確，因為算的是upsert後
DB裡實際的筆數，不是fetch當下拿到的原始筆數(兩者理論上該相等，但DB查詢法對
「to what extent 這次真的寫進去了」這個問題回答得更直接)。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "data_fetch_log.jsonl"
RETENTION_DAYS = 7

# 三張事實表的欄位清單，抄自src/data/schema.sql——這些欄位名稱本身不會逐次改變(schema
# 不變)，這裡當成固定圖例跟著每筆log記錄一起存，UI端不用另外查schema，log檔案本身就是
# 自足的(即使schema之後改了，舊的log紀錄仍然如實反映「那次寫入當下」欄位是什麼)。
STOCK_PRICES_COLUMNS = ["open", "high", "low", "close", "volume", "trading_money", "trading_turnover", "spread"]
INSTITUTIONAL_INVESTORS_COLUMNS = ["investor_type", "buy", "sell"]
MARGIN_TRADING_COLUMNS = [
    "margin_purchase_buy", "margin_purchase_sell", "margin_purchase_cash_repayment",
    "margin_purchase_yesterday_balance", "margin_purchase_today_balance", "margin_purchase_limit",
    "short_sale_buy", "short_sale_sell", "short_sale_cash_repayment",
    "short_sale_yesterday_balance", "short_sale_today_balance", "short_sale_limit",
    "offset_loan_and_short",
]

_MARKETS = ("TWSE", "TPEx")


def _count_rows(conn, table: str, start_date: str, end_date: str, market: str) -> int:
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM {table} t
        JOIN stocks s ON s.stock_id = t.stock_id
        WHERE t.date BETWEEN ? AND ? AND s.market = ?
        """,
        (start_date, end_date, market),
    ).fetchone()
    return row[0] if row else 0


def _count_stocks(conn, table: str, start_date: str, end_date: str, market: str) -> int:
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT t.stock_id) FROM {table} t
        JOIN stocks s ON s.stock_id = t.stock_id
        WHERE t.date BETWEEN ? AND ? AND s.market = ?
        """,
        (start_date, end_date, market),
    ).fetchone()
    return row[0] if row else 0


def record_fetch_run(
    conn, *, trigger: str, start_date: str, end_date: str, status: str,
    is_intraday: bool | None = None, candidates_count: int | None = None,
    indicators_refreshed: int | None = None, errors: list[str] | None = None,
) -> None:
    """記錄一次資料抓取(trigger="automatic"排程／"manual"手動抓取／"backfill"回補)。

    status："success"(正常跑完)／"skipped"(例如非交易日，沒有實際抓資料)／
    "failed"(中途拋例外)。status="skipped"時仍然會嘗試查DB算筆數(通常會是0，
    如實反映「這次沒有新資料」，不是不查)。

    寫入失敗(例如data/目錄不存在、DB查詢失敗)不應該讓呼叫端(pipeline/回補流程)
    中斷，這裡直接吞掉例外，跟pipeline_status.py的既有容錯原則一致——這份紀錄
    是給使用者看的輔助資訊，不是判斷抓取本身是否成功的依據。
    """
    try:
        markets: dict[str, Any] = {}
        for market in _MARKETS:
            markets[market] = {
                "stock_count": _count_stocks(conn, "stock_prices", start_date, end_date, market),
                "stock_prices": {
                    "rows": _count_rows(conn, "stock_prices", start_date, end_date, market),
                    "columns": STOCK_PRICES_COLUMNS,
                },
                "institutional_investors": {
                    "rows": _count_rows(conn, "institutional_investors", start_date, end_date, market),
                    "columns": INSTITUTIONAL_INVESTORS_COLUMNS,
                },
                "margin_trading": {
                    "rows": _count_rows(conn, "margin_trading", start_date, end_date, market),
                    "columns": MARGIN_TRADING_COLUMNS,
                },
            }
        entry = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "trigger": trigger,
            "date_range": start_date if start_date == end_date else f"{start_date}~{end_date}",
            "status": status,
            "is_intraday": is_intraday,
            "markets": markets,
            "candidates_recomputed": candidates_count,
            "indicators_refreshed_rows": indicators_refreshed,
            "errors": errors or [],
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _prune_old_entries()
    except Exception:  # noqa: BLE001
        pass


def _prune_old_entries() -> None:
    """只保留最近RETENTION_DAYS天內的紀錄——每次append完順手清一次，不用另外排程，
    檔案不會像pipeline_run_history.jsonl那樣無限膨脹。單筆記錄格式壞掉(理論上不該
    發生，防禦性處理)就直接捨棄那一行，不讓整個清理動作失敗。
    """
    if not LOG_PATH.exists():
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    kept_lines = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            run_at = datetime.fromisoformat(entry["run_at"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if run_at >= cutoff:
            kept_lines.append(line)
    LOG_PATH.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")


def read_recent_entries() -> list[dict[str, Any]]:
    """回傳目前檔案裡的所有紀錄(已經是_prune_old_entries()清過的，只有最近
    RETENTION_DAYS天內的)，依run_at由新到舊排序，供UI直接顯示。讀不到／格式壞掉
    的行直接略過，不拋例外——這是純顯示用途，不應該因為log檔案的問題影響其他功能。
    """
    if not LOG_PATH.exists():
        return []
    entries = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    entries.sort(key=lambda e: e.get("run_at", ""), reverse=True)
    return entries
