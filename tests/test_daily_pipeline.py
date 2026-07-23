import scripts.daily_pipeline as daily_pipeline
import src.screener.daily_screener as daily_screener
from src.data import storage


def _fresh_conn():
    return storage.init_db(":memory:")


def _price_row(stock_id="2330", d="2026-07-22"):
    return {
        "stock_id": stock_id, "date": d, "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0,
        "volume": 1000000, "trading_money": None, "trading_turnover": None, "spread": None,
    }


def _stub_stock_info(monkeypatch, rows):
    """預設的FinMind股票基本資料回應；每個測試若不特別關心名稱，用一份最小的假資料即可。"""
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_info", lambda: rows)


def test_run_daily_pipeline_skips_when_twse_has_no_data(monkeypatch):
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [])

    candidates = daily_pipeline.run_daily_pipeline(conn, date_str="20260101", dry_run=True, skip_tpex=True)
    assert candidates == []


def test_run_daily_pipeline_writes_candidates_and_skips_notify_on_dry_run(monkeypatch):
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])

    fake_candidate = {
        "stock_id": "2330", "signal_name": "R-TREND-14多頭短線進場",
        "entry_price": 104.0, "stop_loss": 99.0, "note": "測試",
    }
    # 特意patch daily_screener.screen_all_stocks（而不是整個run_screen_and_store），讓
    # run_screen_and_store真正的「寫進daily_candidates」邏輯照常執行，才能驗證這條路徑。
    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [fake_candidate])

    notify_calls = []
    monkeypatch.setattr(daily_pipeline, "send_line_broadcast", lambda text: notify_calls.append(("line", text)))
    monkeypatch.setattr(daily_pipeline, "send_email", lambda subject, body: notify_calls.append(("email", subject, body)))

    candidates = daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=True)

    assert candidates == [fake_candidate]
    assert notify_calls == []  # dry_run不應真的發送通知

    row = conn.execute("SELECT stock_id, signal_name FROM daily_candidates WHERE date = '2026-07-22'").fetchone()
    assert row == ("2330", "R-TREND-14多頭短線進場")


def test_fetch_today_twse_stores_real_name_and_industry_from_finmind(monkeypatch):
    """先前的bug：stocks表的name欄位一律被寫成stock_id本身，而不是FinMind提供的真實公司名稱。"""
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])

    stock_info_by_id = {"2330": {"name": "台積電", "industry": "半導體"}}
    daily_pipeline.fetch_today_twse(conn, "20260722", stock_info_by_id)

    row = conn.execute("SELECT name, industry FROM stocks WHERE stock_id = '2330'").fetchone()
    assert row == ("台積電", "半導體")


def test_fetch_today_twse_falls_back_to_stock_id_when_name_unknown(monkeypatch):
    """FinMind名單可能沒有100%涵蓋所有代號，查不到時退回用代號本身當name，不應該crash。"""
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row(stock_id="9999")])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])

    daily_pipeline.fetch_today_twse(conn, "20260722", {})

    row = conn.execute("SELECT name FROM stocks WHERE stock_id = '9999'").fetchone()
    assert row == ("9999",)


def test_run_daily_pipeline_sends_notifications_when_not_dry_run(monkeypatch):
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    notify_calls = []
    monkeypatch.setattr(daily_pipeline, "send_line_broadcast", lambda text: notify_calls.append(("line", text)))
    monkeypatch.setattr(daily_pipeline, "send_email", lambda subject, body: notify_calls.append(("email", subject, body)))

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=False, skip_tpex=True)

    assert [c[0] for c in notify_calls] == ["line", "email"]


def test_run_daily_pipeline_line_still_sent_when_email_not_configured(monkeypatch):
    """Gmail憑證尚未設定時，send_email()會丟RuntimeError，但不應該阻止LINE通知照常發送、
    也不應該讓整條pipeline因此中斷（候選清單已經寫進資料庫了）。"""
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

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
    _stub_stock_info(monkeypatch, [
        {"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"},
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"},
    ])
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_prices", lambda sid, s, e: [_price_row(stock_id=sid, d="2026-07-22")])
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_institutional_investors", lambda sid, s, e: [])
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_margin_trading", lambda sid, s, e: [])
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=False)

    row = conn.execute("SELECT market, name FROM stocks WHERE stock_id = '6488'").fetchone()
    assert row == ("TPEx", "環球晶")


def test_fetch_today_tpex_filters_out_non_4_digit_codes(monkeypatch):
    """FinMind的TPEx股票清單裡混雜ETF/債券/權證等非4碼代號(例如00878B)，這些不是我們要
    每日追蹤的普通股，應該被濾掉，不浪費請求額度去抓它們。"""
    conn = _fresh_conn()
    stock_info = [
        {"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"},
        {"stock_id": "00878B", "name": "某ETF", "market": "TPEx", "industry": None},
        {"stock_id": "73107P", "name": "某權證", "market": "TPEx", "industry": None},
    ]
    fetched_ids = []
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_prices", lambda sid, s, e: (fetched_ids.append(sid), [_price_row(stock_id=sid)])[1])

    daily_pipeline.fetch_today_tpex(conn, "20260722", stock_info)

    assert fetched_ids == ["6488"]  # 只有4碼的普通股被抓


def test_fetch_today_tpex_does_not_fetch_institutional_or_margin_data(monkeypatch):
    """目前daily_screener沒有任何規則用到TPEx的法人/融資融券資料，這裡刻意不抓，
    節省約2/3的請求量；用「一被呼叫就報錯」確認真的完全沒有呼叫到這兩個函式。"""
    conn = _fresh_conn()
    stock_info = [{"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"}]
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_prices", lambda sid, s, e: [_price_row(stock_id=sid)])

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("不應該被呼叫")

    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_institutional_investors", _fail_if_called)
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_margin_trading", _fail_if_called)

    success_count = daily_pipeline.fetch_today_tpex(conn, "20260722", stock_info)

    assert success_count == 1


def test_run_daily_pipeline_continues_when_single_tpex_stock_fails(monkeypatch):
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    _stub_stock_info(monkeypatch, [
        {"stock_id": "9999", "name": "測試失敗股", "market": "TPEx", "industry": None},
        {"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"},
    ])

    def _flaky_prices(sid, s, e):
        if sid == "9999":
            raise RuntimeError("模擬FinMind暫時失敗")
        return [_price_row(stock_id=sid, d="2026-07-22")]

    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_prices", _flaky_prices)
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_institutional_investors", lambda sid, s, e: [])
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_margin_trading", lambda sid, s, e: [])
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=False)

    row = conn.execute("SELECT market, name FROM stocks WHERE stock_id = '6488'").fetchone()
    assert row == ("TPEx", "環球晶")


def test_run_daily_pipeline_writes_done_status_with_candidate_count(monkeypatch, tmp_path):
    """PySide6桌面版的狀態列輪詢pipeline_status.json——成功跑完後應該看到status=done、
    candidate_count正確，不是卡在running不放。"""
    monkeypatch.setattr(daily_pipeline.pipeline_status, "STATUS_PATH", tmp_path / "status.json")
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=True)

    status = daily_pipeline.pipeline_status.read_status()
    assert status["status"] == "done"
    assert status["candidate_count"] == 0
    assert status["date"] == "2026-07-22"


def test_run_daily_pipeline_writes_done_status_when_non_trading_day(monkeypatch, tmp_path):
    monkeypatch.setattr(daily_pipeline.pipeline_status, "STATUS_PATH", tmp_path / "status.json")
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260101", dry_run=True, skip_tpex=True)

    status = daily_pipeline.pipeline_status.read_status()
    assert status["status"] == "done"
    assert status["candidate_count"] == 0


def test_run_daily_pipeline_writes_failed_status_and_reraises_on_error(monkeypatch, tmp_path):
    """例如FinMind整個服務打不通這種非預期例外，狀態檔要更新成failed(而不是卡在running)，
    同時例外仍要往外拋，讓CLI呼叫端(排程)/桌面版的QThread都能各自感知失敗。"""
    monkeypatch.setattr(daily_pipeline.pipeline_status, "STATUS_PATH", tmp_path / "status.json")
    conn = _fresh_conn()

    def _raise():
        raise RuntimeError("模擬FinMind服務中斷")

    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_info", _raise)

    try:
        daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=True)
        assert False, "應該要往外拋出例外"
    except RuntimeError:
        pass

    status = daily_pipeline.pipeline_status.read_status()
    assert status["status"] == "failed"
