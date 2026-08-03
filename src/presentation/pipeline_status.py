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
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any

STATUS_PATH = Path(__file__).resolve().parents[2] / "data" / "pipeline_status.json"

# 2026-08-04新增：使用者發現同一個交易日內，14:30跟17:00兩次排程跑出來的候選清單
# (MA5>MA10>MA20+SAR翻轉)不一致，但`stock_prices`/`daily_indicators`每次排程都是
# 直接覆寫、沒有保留歷史，事後完全查不到「14:30那次實際寫入的數值」，只能推測不能
# 舉證。這裡新增一份「累積、不覆寫」的執行紀錄檔，之後再發生類似情況，可以直接比對
# 同一天不同時段的實際數值，不用再事後憑印象猜測。
RUN_HISTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "pipeline_run_history.jsonl"

# Windows工作排程器實際建立的每日排程時間(週一~五，見README.md「Windows工作排程器」章節
# 與ai/PLAN.md 2026-07-24的建立紀錄：`tw_stock_pipeline_1000`~`_1430`共6個排程工作)，
# 用來推算「下次更新時間」顯示給使用者看。這裡刻意寫死這份清單，不在執行期間呼叫
# `schtasks /query`——排程本身是使用者作業系統設定，不歸這支程式管理，寫死清單只是為了
# 在UI顯示「下次預期會嘗試更新的時間」，不是要去讀取/驗證排程器實際狀態(那需要另外的
# 排程監控機制，不是這裡的職責)。**若之後手動調整過Windows工作排程器的時間，這裡也要
# 跟著手動同步更新，兩邊不會自動同步。**
SCHEDULED_TIMES = [
    dt_time(10, 0), dt_time(11, 0), dt_time(12, 0),
    dt_time(13, 0), dt_time(13, 30), dt_time(14, 30),
]


def next_scheduled_run_time(now: datetime | None = None) -> datetime:
    """算出下一次排程「預期會嘗試觸發」的時間點——只考慮週一到週五(工作排程器本身設定
    的觸發日)，不考慮國定假日；是否為真正的交易日、要不要實際抓資料，是`run_daily_
    pipeline()`執行當下自己判斷並跳過的事，這裡只回答「排程本身下一次何時啟動」。
    """
    now = now or datetime.now()
    candidate_date = now.date()
    while True:
        if candidate_date.weekday() < 5:  # 0=Monday ... 4=Friday
            for scheduled_time in SCHEDULED_TIMES:
                candidate_dt = datetime.combine(candidate_date, scheduled_time)
                if candidate_dt > now:
                    return candidate_dt
        candidate_date += timedelta(days=1)

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


def append_run_snapshot(conn, iso_date: str, is_intraday: bool, candidate_count: int) -> None:
    """`run_daily_pipeline()`每次執行完畢時追加一筆執行紀錄到`RUN_HISTORY_PATH`(JSONL，
    一行一筆，只增不減)，供之後診斷「同一個交易日內、不同時段執行結果不一致」這類問題
    用——2026-08-04新增，起因見上面`RUN_HISTORY_PATH`的說明。

    不記錄全市場每一檔股票(那樣log檔案會無限膨脹)，只記錄`daily_indicators`裡
    `sar_flip_days_ago<=3`的股票(最近3個交易日內才翻轉、最容易因為資料事後修正而
    在不同時段執行時「翻轉判定」跟著變動的邊界情況)——這組股票剛好就是候選清單
    「SAR翻轉」篩選最關心的對象，鎖定這裡記錄，log檔案大小可控。

    寫入失敗(例如data/目錄不存在、DB查詢失敗)不應該讓pipeline本身中斷，這裡直接
    吞掉例外——這份紀錄是事後診斷輔助，不是pipeline是否成功的判準，跟write_status()
    同樣的容錯原則。
    """
    try:
        rows = conn.execute(
            "SELECT stock_id, sar_value, sar_is_bull, sar_flip_days_ago, ma5, ma10, ma20 "
            "FROM daily_indicators WHERE date = ? AND sar_flip_days_ago <= 3",
            (iso_date,),
        ).fetchall()
        snapshot = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "iso_date": iso_date,
            "is_intraday": is_intraday,
            "candidate_count": candidate_count,
            "recent_sar_flips": [
                {
                    "stock_id": r[0], "sar_value": r[1], "sar_is_bull": bool(r[2]),
                    "sar_flip_days_ago": r[3], "ma5": r[4], "ma10": r[5], "ma20": r[6],
                }
                for r in rows
            ],
        }
        RUN_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RUN_HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


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
