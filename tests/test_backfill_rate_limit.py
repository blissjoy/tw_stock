from src.data.backfill_rate_limit import (
    get_last_attempt,
    record_attempt_result,
    record_attempt_start,
    seconds_until_next_allowed,
)
from src.data.storage import init_db


def _fresh_db():
    return init_db(":memory:")


def test_seconds_until_next_allowed_with_no_history_is_zero():
    conn = _fresh_db()
    assert seconds_until_next_allowed(conn, cooldown_seconds=3600) == 0.0


def test_record_attempt_start_returns_lastrowid_and_sets_running_status():
    conn = _fresh_db()
    attempt_id = record_attempt_start(conn, {"start": "2026-08-01", "end": "2026-08-05"})
    assert attempt_id == 1

    last = get_last_attempt(conn)
    assert last["id"] == attempt_id
    assert last["status"] == "running"
    assert last["params"] == {"start": "2026-08-01", "end": "2026-08-05"}
    assert last["result"] is None
    assert last["finished_at"] is None


def test_seconds_until_next_allowed_is_positive_right_after_a_fresh_attempt():
    conn = _fresh_db()
    record_attempt_start(conn, {"start": "2026-08-01", "end": "2026-08-05"})

    remaining = seconds_until_next_allowed(conn, cooldown_seconds=3600)
    assert 0 < remaining <= 3600


def test_seconds_until_next_allowed_is_zero_once_cooldown_already_elapsed():
    conn = _fresh_db()
    record_attempt_start(conn, {"start": "2026-08-01", "end": "2026-08-05"})

    # cooldown_seconds=0：任何過去時間都已經超過冷卻，用來模擬「冷卻已過」而不必真的sleep。
    assert seconds_until_next_allowed(conn, cooldown_seconds=0) == 0.0


def test_failed_attempt_still_counts_toward_cooldown():
    """核心防呆邏輯：即使這次回補最後失敗，冷卻時間依然要算數(用started_at當基準，
    不是finished_at)，避免「失敗就可以立刻重試」變成無限重試炸額度的漏洞。"""
    conn = _fresh_db()
    attempt_id = record_attempt_start(conn, {"start": "2026-08-01", "end": "2026-08-05"})
    record_attempt_result(conn, attempt_id, "failed", {"error": "boom"})

    remaining = seconds_until_next_allowed(conn, cooldown_seconds=3600)
    assert 0 < remaining <= 3600

    last = get_last_attempt(conn)
    assert last["status"] == "failed"
    assert last["result"] == {"error": "boom"}
    assert last["finished_at"] is not None


def test_record_attempt_result_done_status():
    conn = _fresh_db()
    attempt_id = record_attempt_start(conn, {"start": "2026-08-01", "end": "2026-08-05"})
    record_attempt_result(conn, attempt_id, "done", {"taiex_dates": 3, "indicators": 10})

    last = get_last_attempt(conn)
    assert last["status"] == "done"
    assert last["result"] == {"taiex_dates": 3, "indicators": 10}


def test_get_last_attempt_returns_most_recent_of_multiple():
    conn = _fresh_db()
    record_attempt_start(conn, {"start": "2026-08-01", "end": "2026-08-01"})
    second_id = record_attempt_start(conn, {"start": "2026-08-02", "end": "2026-08-02"})

    last = get_last_attempt(conn)
    assert last["id"] == second_id
    assert last["params"] == {"start": "2026-08-02", "end": "2026-08-02"}
