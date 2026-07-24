from datetime import datetime, timedelta, timezone

import src.presentation.pipeline_status as pipeline_status


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
