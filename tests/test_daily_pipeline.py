import scripts.daily_pipeline as daily_pipeline
from src.data import storage


def _fresh_conn():
    return storage.init_db(":memory:")


def _price_row(stock_id="2330", d="2026-07-22"):
    return {
        "stock_id": stock_id, "date": d, "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0,
        "volume": 1000000, "trading_money": None, "trading_turnover": None, "spread": None,
    }


def test_run_daily_pipeline_skips_when_twse_has_no_data(monkeypatch):
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [])

    candidates = daily_pipeline.run_daily_pipeline(conn, date_str="20260101", dry_run=True, skip_tpex=True)
    assert candidates == []


def test_run_daily_pipeline_writes_candidates_and_skips_notify_on_dry_run(monkeypatch):
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])

    fake_candidate = {
        "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
        "entry_price": 104.0, "stop_loss": 99.0, "note": "測試",
    }
    monkeypatch.setattr(daily_pipeline, "screen_all_stocks", lambda frames, min_days: [fake_candidate])

    notify_calls = []
    monkeypatch.setattr(daily_pipeline, "send_line_broadcast", lambda text: notify_calls.append(("line", text)))
    monkeypatch.setattr(daily_pipeline, "send_email", lambda subject, body: notify_calls.append(("email", subject, body)))

    candidates = daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=True)

    assert candidates == [fake_candidate]
    assert notify_calls == []  # dry_run不應真的發送通知

    row = conn.execute("SELECT stock_id, signal_name FROM daily_candidates WHERE date = '2026-07-22'").fetchone()
    assert row == ("2330", "R-TREND-14多頭短線進場")


def test_run_daily_pipeline_sends_notifications_when_not_dry_run(monkeypatch):
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline, "screen_all_stocks", lambda frames, min_days: [])

    notify_calls = []
    monkeypatch.setattr(daily_pipeline, "send_line_broadcast", lambda text: notify_calls.append(("line", text)))
    monkeypatch.setattr(daily_pipeline, "send_email", lambda subject, body: notify_calls.append(("email", subject, body)))

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=False, skip_tpex=True)

    assert [c[0] for c in notify_calls] == ["line", "email"]


def test_run_daily_pipeline_line_still_sent_when_email_not_configured(monkeypatch):
    """Gmail憑證尚未設定時，send_email()會丟RuntimeError，但不應該阻止LINE通知照常發送、
    也不應該讓整條pipeline因此中斷（候選清單已經寫進資料庫了）。"""
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline, "screen_all_stocks", lambda frames, min_days: [])

    notify_calls = []
    monkeypatch.setattr(daily_pipeline, "send_line_broadcast", lambda text: notify_calls.append("line"))

    def _raise_missing_gmail_creds(subject, body):
        raise RuntimeError("找不到 GMAIL_ADDRESS ...")

    monkeypatch.setattr(daily_pipeline, "send_email", _raise_missing_gmail_creds)

    # 不應該拋出例外中斷整個呼叫
    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=False, skip_tpex=True)

    assert notify_calls == ["line"]


def test_run_daily_pipeline_updates_tpex_when_not_skipped(monkeypatch):
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_info", lambda: [
        {"stock_id": "6488", "market": "TPEx"}, {"stock_id": "2330", "market": "TWSE"},
    ])
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_prices", lambda sid, s, e: [_price_row(stock_id=sid, d="2026-07-22")])
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_institutional_investors", lambda sid, s, e: [])
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_margin_trading", lambda sid, s, e: [])
    monkeypatch.setattr(daily_pipeline, "screen_all_stocks", lambda frames, min_days: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=False)

    row = conn.execute("SELECT market FROM stocks WHERE stock_id = '6488'").fetchone()
    assert row == ("TPEx",)


def test_run_daily_pipeline_continues_when_single_tpex_stock_fails(monkeypatch):
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_info", lambda: [
        {"stock_id": "9999", "market": "TPEx"}, {"stock_id": "6488", "market": "TPEx"},
    ])

    def _flaky_prices(sid, s, e):
        if sid == "9999":
            raise RuntimeError("模擬FinMind暫時失敗")
        return [_price_row(stock_id=sid, d="2026-07-22")]

    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_prices", _flaky_prices)
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_institutional_investors", lambda sid, s, e: [])
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_margin_trading", lambda sid, s, e: [])
    monkeypatch.setattr(daily_pipeline, "screen_all_stocks", lambda frames, min_days: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=False)

    row = conn.execute("SELECT market FROM stocks WHERE stock_id = '6488'").fetchone()
    assert row == ("TPEx",)
