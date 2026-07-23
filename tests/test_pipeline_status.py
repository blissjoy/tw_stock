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
