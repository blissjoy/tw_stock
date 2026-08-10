from datetime import datetime

from scripts.sync_local_to_turso import collect_sync_data, sync
from src.data import storage
from src.data.storage import init_db


def _fresh_db():
    return init_db(":memory:")


def _seed_local(conn, dates: list[str]) -> None:
    """塞一批跨多個交易日的本機資料，模擬daily_pipeline.py已經寫好的內容。"""
    now = datetime.now().isoformat()
    storage.upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體", "updated_at": now},
        {"stock_id": "2317", "name": "鴻海", "market": "TWSE", "industry": "電子", "updated_at": now},
    ])
    for d in dates:
        storage.upsert_stock_prices(conn, [
            {"stock_id": "2330", "date": d, "open": 100, "high": 105, "low": 99, "close": 103,
             "volume": 1000, "trading_money": 100000, "trading_turnover": 500, "spread": 1.0},
        ])
        storage.upsert_institutional_investors(conn, [
            {"stock_id": "2330", "date": d, "investor_type": "Foreign_Investor", "buy": 1000, "sell": 500},
        ])
        storage.upsert_margin_trading(conn, [
            {"stock_id": "2330", "date": d, "margin_purchase_buy": 10, "margin_purchase_sell": 5,
             "margin_purchase_cash_repayment": 0, "margin_purchase_yesterday_balance": 100,
             "margin_purchase_today_balance": 105, "margin_purchase_limit": 1000,
             "short_sale_buy": 0, "short_sale_sell": 0, "short_sale_cash_repayment": 0,
             "short_sale_yesterday_balance": 0, "short_sale_today_balance": 0, "short_sale_limit": 500,
             "offset_loan_and_short": 0},
        ])
        storage.upsert_daily_indicators(conn, [
            {"stock_id": "2330", "date": d, "ma5": 100.0, "ma10": 99.0, "ma20": 98.0, "ma60": None,
             "ma120": None, "ma200": None, "ma240": None, "sar_value": 95.0, "sar_is_bull": 1,
             "sar_flip_days_ago": 3, "trend_is_at_high": None, "trend_is_at_low": None, "trend_swing_pct": None,
             "updated_at": now},
        ])
        storage.upsert_daily_candidates(conn, [
            {"date": d, "stock_id": "2330", "signal_name": "R-TREND-14", "entry_price": 103.0,
             "stop_loss": 98.0, "note": "test", "created_at": now},
        ])
        storage.upsert_daily_data_status(conn, d, False)
    storage.upsert_delisted_stocks(conn, [
        {"stock_id": "9999", "name": "已下市股票", "delisted_date": None, "reason": "test", "noted_at": now},
    ])


def test_collect_sync_data_with_no_local_history_returns_empty():
    conn = _fresh_db()
    collected = collect_sync_data(conn, days=10)
    assert collected["dates"] == []
    assert collected["data"] == {}


def test_collect_sync_data_scopes_to_recent_trading_days_only():
    conn = _fresh_db()
    _seed_local(conn, ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"])

    collected = collect_sync_data(conn, days=2)
    assert collected["date_range"] == "2026-08-04~2026-08-05"
    assert {row["date"] for row in collected["data"]["stock_prices"]} == {"2026-08-04", "2026-08-05"}
    # stocks/delisted_stocks是全表，不受交易日窗口限制
    assert len(collected["data"]["stocks"]) == 2
    assert len(collected["data"]["delisted_stocks"]) == 1


def test_sync_writes_all_tables_to_destination_conn():
    local_conn = _fresh_db()
    _seed_local(local_conn, ["2026-08-04", "2026-08-05"])
    turso_conn = _fresh_db()  # 用另一個獨立的in-memory sqlite模擬Turso連線

    result = sync(local_conn, turso_conn, days=10)

    assert result["date_range"] == "2026-08-04~2026-08-05"
    assert all(info["status"] == "done" for info in result["tables"].values())
    assert result["tables"]["stock_prices"]["count"] == 2
    assert result["tables"]["daily_indicators"]["count"] == 2
    assert result["tables"]["daily_candidates"]["count"] == 2
    assert result["tables"]["daily_data_status"]["count"] == 2
    assert result["tables"]["stocks"]["count"] == 2
    assert result["tables"]["delisted_stocks"]["count"] == 1

    turso_prices = turso_conn.execute("SELECT COUNT(*) FROM stock_prices").fetchone()[0]
    assert turso_prices == 2


def test_sync_is_idempotent_running_twice_does_not_duplicate_rows():
    """核心安全性質：這支腳本設計上會重複執行(每天一次)，同一天的資料被同步兩次
    不該產生重複列或報錯——全部依賴upsert的ON CONFLICT DO UPDATE。"""
    local_conn = _fresh_db()
    _seed_local(local_conn, ["2026-08-05"])
    turso_conn = _fresh_db()

    sync(local_conn, turso_conn, days=10)
    sync(local_conn, turso_conn, days=10)

    assert turso_conn.execute("SELECT COUNT(*) FROM stock_prices").fetchone()[0] == 1
    assert turso_conn.execute("SELECT COUNT(*) FROM daily_candidates").fetchone()[0] == 1


def test_sync_continues_other_tables_when_one_table_fails():
    """某一張表寫入失敗不該讓整支腳本中斷——用一個「只有execute沒有executemany/batch」
    的假連線讓寫入拋例外，確認其他表仍然正常完成。"""
    local_conn = _fresh_db()
    _seed_local(local_conn, ["2026-08-05"])
    turso_conn = _fresh_db()

    original_upsert = storage.upsert_margin_trading

    def _broken_upsert_margin_trading(conn, rows):
        raise RuntimeError("simulated Turso failure")

    import scripts.sync_local_to_turso as sync_module
    sync_module.storage.upsert_margin_trading = _broken_upsert_margin_trading
    try:
        result = sync(local_conn, turso_conn, days=10)
    finally:
        sync_module.storage.upsert_margin_trading = original_upsert

    assert result["tables"]["margin_trading"]["status"] == "failed"
    assert "simulated Turso failure" in result["tables"]["margin_trading"]["error"]
    assert result["tables"]["stock_prices"]["status"] == "done"
    assert result["tables"]["daily_candidates"]["status"] == "done"
