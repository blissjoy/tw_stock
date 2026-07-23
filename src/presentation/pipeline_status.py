"""每日pipeline執行狀態的小型狀態檔：`scripts/daily_pipeline.py`(Windows工作排程器排程觸發)
與`desktop/`(PySide6桌面版的手動抓取按鈕)共用同一份`run_daily_pipeline()`，這個模組讓兩種
觸發方式都能被桌面版UI用同一個機制偵測到「目前正在跑」——UI用QTimer輪詢這個檔案內容，不需要
另外設計跨thread/跨process的通知機制。

純本機檔案，不寫進資料庫（跟conn是本機sqlite還是Turso無關，狀態只是給本機UI看的）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_PATH = Path(__file__).resolve().parents[2] / "data" / "pipeline_status.json"


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
