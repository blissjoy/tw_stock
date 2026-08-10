import json
from datetime import datetime, timedelta, timezone

import src.presentation.pipeline_status as pipeline_status
from src.data.storage import init_db, upsert_daily_indicators, upsert_stocks


def test_read_status_returns_none_when_file_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_status, "STATUS_PATH", tmp_path / "nonexistent.json")
    assert pipeline_status.read_status() is None


def test_write_then_read_status_round_trips_extra_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_status, "STATUS_PATH", tmp_path / "status.json")

    pipeline_status.write_status("running", date="2026-07-23")
    status = pipeline_status.read_status()

    assert status["status"] == "running"
    assert status["date"] == "2026-07-23"
    assert "updated_at" in status


def test_write_status_creates_parent_directory_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_status, "STATUS_PATH", tmp_path / "nested" / "status.json")

    pipeline_status.write_status("done", candidate_count=5)

    assert pipeline_status.read_status()["candidate_count"] == 5


def test_read_status_returns_none_on_malformed_json(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    path.write_text("not valid json{{{", encoding="utf-8")
    monkeypatch.setattr(pipeline_status, "STATUS_PATH", path)

    assert pipeline_status.read_status() is None


def test_is_stale_false_when_status_is_not_running():
    status = {
        "status": "done",
        "updated_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    }
    assert pipeline_status.is_stale(status) is False


def test_is_stale_false_when_running_and_recently_updated():
    status = {
        "status": "running",
        "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
    }
    assert pipeline_status.is_stale(status) is False


def test_is_stale_true_when_running_but_updated_at_is_old():
    """對應2026-07-24的事故：process被強制kill後，updated_at永遠停在最後一次心跳，
    長時間沒有更新代表pipeline很可能已經非正常終止，不是還在跑。"""
    status = {
        "status": "running",
        "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=pipeline_status.STALE_RUNNING_THRESHOLD_SECONDS + 60)).isoformat(),
    }
    assert pipeline_status.is_stale(status) is True


def test_is_stale_false_when_updated_at_missing_or_malformed():
    assert pipeline_status.is_stale({"status": "running"}) is False
    assert pipeline_status.is_stale({"status": "running", "updated_at": "not-a-timestamp"}) is False


def test_next_scheduled_run_time_returns_next_time_slot_same_day():
    now = datetime(2026, 7, 29, 10, 30)  # 週三，剛過10:00那個時段
    next_run = pipeline_status.next_scheduled_run_time(now)
    assert next_run == datetime(2026, 7, 29, 11, 0)


def test_next_scheduled_run_time_rolls_to_next_weekday_after_last_slot():
    now = datetime(2026, 7, 29, 15, 0)  # 週三，已過當天最後一個時段(14:30)
    next_run = pipeline_status.next_scheduled_run_time(now)
    assert next_run == datetime(2026, 7, 30, 10, 0)  # 隔天(週四)第一個時段


def test_next_scheduled_run_time_skips_weekend():
    now = datetime(2026, 7, 31, 15, 0)  # 2026-07-31是週五，已過當天最後時段
    next_run = pipeline_status.next_scheduled_run_time(now)
    assert next_run == datetime(2026, 8, 3, 10, 0)  # 跳過六日，落在下週一


def test_append_run_snapshot_records_only_recently_flipped_stocks(tmp_path, monkeypatch):
    """2026-08-04新增：只記錄sar_flip_days_ago<=3的股票(候選清單「SAR翻轉」篩選最
    關心的邊界情況)，避免log檔案隨全市場股票數無限膨脹——9999超過3天不該被記錄。"""
    monkeypatch.setattr(pipeline_status, "RUN_HISTORY_PATH", tmp_path / "history.jsonl")
    conn = init_db(":memory:")
    upsert_stocks(conn, [
        {"stock_id": "1742", "name": "台蠟", "market": "TPEx", "industry": None, "updated_at": "2026-08-03"},
        {"stock_id": "9999", "name": "測試", "market": "TWSE", "industry": None, "updated_at": "2026-08-03"},
    ])
    upsert_daily_indicators(conn, [
        {"stock_id": "1742", "date": "2026-08-03", "ma5": 17.0, "ma10": 16.63, "ma20": 16.14,
         "ma60": None, "ma120": None, "ma200": None, "ma240": None, "sar_value": 15.85, "sar_is_bull": True,
         "sar_flip_days_ago": 1, "trend_is_at_high": None, "trend_is_at_low": None, "trend_swing_pct": None,
         "updated_at": "2026-08-03T17:03:00"},
        {"stock_id": "9999", "date": "2026-08-03", "ma5": 10.0, "ma10": 9.5, "ma20": 9.0,
         "ma60": None, "ma120": None, "ma200": None, "ma240": None, "sar_value": 8.0, "sar_is_bull": True,
         "sar_flip_days_ago": 10, "trend_is_at_high": None, "trend_is_at_low": None, "trend_swing_pct": None,
         "updated_at": "2026-08-03T17:03:00"},
    ])

    pipeline_status.append_run_snapshot(conn, "2026-08-03", is_intraday=False, candidate_count=12)

    lines = pipeline_status.RUN_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    snapshot = json.loads(lines[0])
    assert snapshot["iso_date"] == "2026-08-03"
    assert snapshot["is_intraday"] is False
    assert snapshot["candidate_count"] == 12
    stock_ids = {r["stock_id"] for r in snapshot["recent_sar_flips"]}
    assert stock_ids == {"1742"}


def test_append_run_snapshot_appends_not_overwrites(tmp_path, monkeypatch):
    """跟write_status()(覆寫)不同，這份紀錄要能保留同一天多次執行的歷史，才能事後
    比對「14:30那次」跟「17:00那次」的實際數值有沒有不一樣。"""
    monkeypatch.setattr(pipeline_status, "RUN_HISTORY_PATH", tmp_path / "history.jsonl")
    conn = init_db(":memory:")

    pipeline_status.append_run_snapshot(conn, "2026-08-03", is_intraday=True, candidate_count=5)
    pipeline_status.append_run_snapshot(conn, "2026-08-03", is_intraday=False, candidate_count=12)

    lines = pipeline_status.RUN_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["candidate_count"] == 5
    assert json.loads(lines[1])["candidate_count"] == 12


def test_append_run_snapshot_swallows_exceptions():
    """DB查詢失敗不應該讓呼叫端crash——這份紀錄是事後診斷輔助，不是pipeline是否成功
    的判準，跟write_status()同樣的容錯原則。"""
    class _BadConn:
        def execute(self, *args, **kwargs):
            raise RuntimeError("boom")

    pipeline_status.append_run_snapshot(_BadConn(), "2026-08-03", is_intraday=False, candidate_count=0)


def test_next_scheduled_run_time_defaults_to_now_when_not_given(monkeypatch):
    """不傳now參數時應該用目前時間，而不是crash或用固定值。"""
    fixed_now = datetime(2026, 7, 29, 9, 0)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(pipeline_status, "datetime", _FixedDatetime)
    next_run = pipeline_status.next_scheduled_run_time()
    assert next_run == datetime(2026, 7, 29, 10, 0)
