import json
from datetime import datetime, timedelta, timezone

import src.presentation.data_fetch_log as data_fetch_log
from src.data.storage import (
    init_db,
    upsert_institutional_investors,
    upsert_margin_trading,
    upsert_stock_prices,
    upsert_stocks,
)


def _fresh_conn():
    return init_db(":memory:")


def _seed_twse_stock(conn, stock_id="2330", d="2026-08-06"):
    upsert_stocks(conn, [{"stock_id": stock_id, "name": "台積電", "market": "TWSE", "industry": "半導體業", "updated_at": d}])
    upsert_stock_prices(conn, [
        {"stock_id": stock_id, "date": d, "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0,
         "volume": 1000, "trading_money": None, "trading_turnover": None, "spread": None},
    ])
    upsert_institutional_investors(conn, [
        {"stock_id": stock_id, "date": d, "investor_type": "Foreign_Investor", "buy": 500, "sell": 200},
    ])
    upsert_margin_trading(conn, [
        {"stock_id": stock_id, "date": d, "margin_purchase_buy": 10, "margin_purchase_sell": 5,
         "margin_purchase_cash_repayment": 0, "margin_purchase_yesterday_balance": 100,
         "margin_purchase_today_balance": 105, "margin_purchase_limit": 1000,
         "short_sale_buy": 0, "short_sale_sell": 0, "short_sale_cash_repayment": 0,
         "short_sale_yesterday_balance": 0, "short_sale_today_balance": 0, "short_sale_limit": 500,
         "offset_loan_and_short": 0},
    ])


def test_record_fetch_run_counts_rows_per_market_and_table(tmp_path, monkeypatch):
    monkeypatch.setattr(data_fetch_log, "LOG_PATH", tmp_path / "log.jsonl")
    conn = _fresh_conn()
    _seed_twse_stock(conn)

    data_fetch_log.record_fetch_run(
        conn, trigger="automatic", start_date="2026-08-06", end_date="2026-08-06",
        status="success", is_intraday=False, candidates_count=3, indicators_refreshed=1,
    )

    entries = data_fetch_log.read_recent_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["trigger"] == "automatic"
    assert entry["date_range"] == "2026-08-06"
    assert entry["status"] == "success"
    assert entry["is_intraday"] is False
    assert entry["candidates_recomputed"] == 3
    assert entry["indicators_refreshed_rows"] == 1
    twse = entry["markets"]["TWSE"]
    assert twse["stock_count"] == 1
    assert twse["stock_prices"]["rows"] == 1
    assert twse["stock_prices"]["columns"] == data_fetch_log.STOCK_PRICES_COLUMNS
    assert twse["institutional_investors"]["rows"] == 1
    assert twse["margin_trading"]["rows"] == 1
    tpex = entry["markets"]["TPEx"]
    assert tpex["stock_count"] == 0
    assert tpex["stock_prices"]["rows"] == 0


def test_record_fetch_run_date_range_formats_single_day_without_tilde(tmp_path, monkeypatch):
    monkeypatch.setattr(data_fetch_log, "LOG_PATH", tmp_path / "log.jsonl")
    conn = _fresh_conn()

    data_fetch_log.record_fetch_run(
        conn, trigger="backfill", start_date="2026-07-01", end_date="2026-08-06", status="success",
    )

    entry = data_fetch_log.read_recent_entries()[0]
    assert entry["date_range"] == "2026-07-01~2026-08-06"


def test_record_fetch_run_records_errors_on_failed_status(tmp_path, monkeypatch):
    monkeypatch.setattr(data_fetch_log, "LOG_PATH", tmp_path / "log.jsonl")
    conn = _fresh_conn()

    data_fetch_log.record_fetch_run(
        conn, trigger="manual", start_date="2026-08-06", end_date="2026-08-06",
        status="failed", errors=["FinMind API 連續3次請求失敗"],
    )

    entry = data_fetch_log.read_recent_entries()[0]
    assert entry["status"] == "failed"
    assert entry["errors"] == ["FinMind API 連續3次請求失敗"]


def test_record_fetch_run_appends_not_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(data_fetch_log, "LOG_PATH", tmp_path / "log.jsonl")
    conn = _fresh_conn()

    data_fetch_log.record_fetch_run(conn, trigger="automatic", start_date="2026-08-05", end_date="2026-08-05", status="success")
    data_fetch_log.record_fetch_run(conn, trigger="manual", start_date="2026-08-06", end_date="2026-08-06", status="success")

    entries = data_fetch_log.read_recent_entries()
    assert len(entries) == 2


def test_read_recent_entries_sorts_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(data_fetch_log, "LOG_PATH", tmp_path / "log.jsonl")
    conn = _fresh_conn()

    data_fetch_log.record_fetch_run(conn, trigger="automatic", start_date="2026-08-01", end_date="2026-08-01", status="success")
    data_fetch_log.record_fetch_run(conn, trigger="automatic", start_date="2026-08-06", end_date="2026-08-06", status="success")

    entries = data_fetch_log.read_recent_entries()
    assert [e["date_range"] for e in entries] == ["2026-08-06", "2026-08-01"]


def test_record_fetch_run_prunes_entries_older_than_retention_window(tmp_path, monkeypatch):
    """使用者明確要求只保留最近一星期，不能像pipeline_run_history.jsonl一樣無限
    膨脹——這裡直接寫一筆超過RETENTION_DAYS的舊紀錄進log檔案，確認下一次
    record_fetch_run()呼叫時會把它清掉。"""
    log_path = tmp_path / "log.jsonl"
    monkeypatch.setattr(data_fetch_log, "LOG_PATH", log_path)
    conn = _fresh_conn()

    old_run_at = (datetime.now(timezone.utc) - timedelta(days=data_fetch_log.RETENTION_DAYS + 1)).isoformat()
    stale_entry = {
        "run_at": old_run_at, "trigger": "automatic", "date_range": "2026-01-01",
        "status": "success", "is_intraday": False, "markets": {}, "candidates_recomputed": 0,
        "indicators_refreshed_rows": 0, "errors": [],
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(stale_entry) + "\n", encoding="utf-8")

    data_fetch_log.record_fetch_run(conn, trigger="automatic", start_date="2026-08-06", end_date="2026-08-06", status="success")

    entries = data_fetch_log.read_recent_entries()
    assert len(entries) == 1
    assert entries[0]["date_range"] == "2026-08-06"


def test_read_recent_entries_returns_empty_list_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(data_fetch_log, "LOG_PATH", tmp_path / "nonexistent.jsonl")

    assert data_fetch_log.read_recent_entries() == []


def test_record_fetch_run_swallows_exceptions_from_bad_connection():
    """DB查詢失敗(例如conn已經關閉)不應該讓呼叫端(pipeline/回補流程)中斷，這份紀錄
    只是輔助顯示用途，跟pipeline_status.py既有的容錯原則一致。"""
    conn = _fresh_conn()
    conn.close()

    data_fetch_log.record_fetch_run(conn, trigger="automatic", start_date="2026-08-06", end_date="2026-08-06", status="success")
    # 沒有拋例外就算通過
