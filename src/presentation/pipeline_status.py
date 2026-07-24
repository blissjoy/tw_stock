"""每日pipeline執行狀態的小型狀態檔：`scripts/daily_pipeline.py`(Windows工作排程器排程觸發)
與兩個前端(桌面版/Streamlit版的「▶ 手動抓取今日資料」按鈕)共用同一份`run_daily_pipeline()`，
這個模組讓三種觸發方式都能被UI用同一個機制偵測到「目前正在跑」——UI輪詢這個檔案內容，不需要
另外設計跨thread/跨process的通知機制。

純本機檔案，不寫進資料庫（跟conn是本機sqlite還是Turso無關，狀態只是給本機UI看的）。

⚠️ 2026-07-24排程首次真實觸發時的事故發現一個沒設計到的情境：如果pipeline的process被
強制中止(手動kill、當機、斷電、Windows工作排程器本身把逾時的工作砍掉)，Python的
except/finally完全沒有機會執行，`write_status("failed", ...)`永遠不會被呼叫，狀態檔案
會永久停在`"running"`——UI會一直顯示「更新中」，即使實際上什麼都沒有在跑。`run_daily_
pipeline()`現在會在每次`on_progress`回報進度時「順便」重寫一次running狀態(見
scripts/daily_pipeline.py)，讓`updated_at`在正常執行期間持續往前推進；`is_stale()`
判斷「顯示running但updated_at已經很久沒更新」的情況，UI依此顯示「可能已中斷」而不是
誤導性的「更新中」。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_PATH = Path(__file__).resolve().parents[2] / "data" / "pipeline_status.json"

# 正常執行期間，run_daily_pipeline()每處理完一批(TWSE/TPEx各自的yfinance批次，或至少在
# fetch_today_twse()這種單一請求完成時)都會重寫一次running狀態；即使剛好卡在批次之間
# (單批次硬性逾時上限60秒，見yfinance_client.py)，正常情況下兩次心跳間隔也不會超過幾分鐘。
# 這裡的門檻刻意抓比正常心跳間隔寬鬆很多的5分鐘，避免網路稍慢時被誤判成「已中斷」。
STALE_RUNNING_THRESHOLD_SECONDS = 300


def write_status(status: str, **extra: Any) -> None:
    """status: "running"/"done"/"failed"。extra裡的其他欄位(date/candidate_count/note等)
    原樣寫入，供UI顯示更多細節。寫入失敗(例如data/目錄不存在)不應該讓pipeline本身中斷，
    這裡直接吞掉例外——狀態顯示只是錦上添花，不是pipeline是否成功的判準。"""
    payload = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat(), **extra}
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def read_status() -> dict[str, Any] | None:
    """讀不到（檔案不存在、格式壞掉）都回傳None，呼叫端視為「沒有執行中的紀錄」處理，
    不拋例外——這是純UI顯示用途，不應該因為狀態檔案的問題影響桌面版其他功能。"""
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_stale(status: dict[str, Any]) -> bool:
    """status顯示"running"，但updated_at已經超過STALE_RUNNING_THRESHOLD_SECONDS秒沒有
    更新，代表pipeline很可能已經非正常終止(被強制kill/當機/斷電)，不是真的還在執行中——
    正常執行完成或失敗都會呼叫write_status()更新這個時間戳，也會透過心跳持續更新，只有
    「整個process直接消失」這種Python自己攔截不到的情況才會讓updated_at長時間凍結不動。
    """
    if status.get("status") != "running":
        return False
    try:
        updated_at = datetime.fromisoformat(status["updated_at"])
    except (KeyError, TypeError, ValueError):
        return False
    now = datetime.now(timezone.utc)
    return (now - updated_at).total_seconds() > STALE_RUNNING_THRESHOLD_SECONDS
