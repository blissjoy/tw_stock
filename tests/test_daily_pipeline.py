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


def test_fetch_today_twse_returns_final_false_when_official_endpoint_succeeds(monkeypatch):
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])

    is_trading_day, is_intraday = daily_pipeline.fetch_today_twse(conn, "20260722", {"2330": {"name": "台積電", "industry": None}})

    assert is_trading_day is True
    assert is_intraday is False


def test_fetch_today_twse_falls_back_to_yfinance_when_official_endpoint_has_no_data(monkeypatch):
    """官方「每日收盤行情」端點在收盤前查詢會回傳空——這是2026-07-24發現的真實情境：
    使用者盤中手動抓取時官方端點還沒有資料，改用yfinance盤中即時價當備援，讓「手動抓取」
    在盤中也能拿到資料。"""
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    stock_info_by_id = {"2330": {"name": "台積電", "industry": "半導體", "market": "TWSE"}}

    captured = {}

    def _fake_batch(stock_ids, start_date, end_date, on_progress=None):
        captured["stock_ids"] = stock_ids
        return {"2330": [_price_row(stock_id="2330")]}

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_twse_prices_batch", _fake_batch)

    is_trading_day, is_intraday = daily_pipeline.fetch_today_twse(conn, "20260724", stock_info_by_id)

    assert is_trading_day is True
    assert is_intraday is True
    assert captured["stock_ids"] == ["2330"]
    row = conn.execute("SELECT name FROM stocks WHERE stock_id = '2330'").fetchone()
    assert row == ("台積電",)


def test_fetch_today_twse_yfinance_fallback_excludes_non_twse_and_non_4_digit_codes(monkeypatch):
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    stock_info_by_id = {
        "2330": {"name": "台積電", "industry": "半導體", "market": "TWSE"},
        "6488": {"name": "環球晶", "industry": "半導體", "market": "TPEx"},  # 不同市場，不該被抓
        "00878": {"name": "某ETF", "industry": None, "market": "TWSE"},  # 非4碼，不該被抓
    }
    captured = {}

    def _fake_batch(stock_ids, start_date, end_date, on_progress=None):
        captured["stock_ids"] = stock_ids
        return {}

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_twse_prices_batch", _fake_batch)

    daily_pipeline.fetch_today_twse(conn, "20260724", stock_info_by_id)

    assert captured["stock_ids"] == ["2330"]


def test_fetch_today_twse_returns_false_when_both_sources_have_no_data(monkeypatch):
    """官方端點跟yfinance備援都查無資料才真的判定為非交易日(而不是還沒收盤)。"""
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [])
    stock_info_by_id = {"2330": {"name": "台積電", "industry": "半導體", "market": "TWSE"}}
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_twse_prices_batch", lambda *a, **k: {})

    is_trading_day, is_intraday = daily_pipeline.fetch_today_twse(conn, "20260101", stock_info_by_id)

    assert is_trading_day is False
    assert is_intraday is False


def test_fetch_today_twse_returns_false_when_yfinance_fallback_raises(monkeypatch):
    """yfinance備援下載失敗(例如網路問題)不應該讓pipeline中斷，視同查無資料處理。"""
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [])
    stock_info_by_id = {"2330": {"name": "台積電", "industry": "半導體", "market": "TWSE"}}

    def _raise(*a, **k):
        raise RuntimeError("模擬yfinance網路逾時")

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_twse_prices_batch", _raise)

    is_trading_day, is_intraday = daily_pipeline.fetch_today_twse(conn, "20260101", stock_info_by_id)

    assert is_trading_day is False
    assert is_intraday is False


def test_fetch_today_twse_forwards_progress_callback_only_on_fallback_path(monkeypatch):
    conn = _fresh_conn()
    progress_calls = []

    # 官方端點成功時，直接回報單一批次完成(1,1)，不會去呼叫yfinance
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    daily_pipeline.fetch_today_twse(
        conn, "20260722", {"2330": {"name": "台積電", "industry": None, "market": "TWSE"}},
        on_progress=lambda done, total: progress_calls.append((done, total)),
    )
    assert progress_calls == [(1, 1)]


def test_run_daily_pipeline_records_intraday_status_when_falling_back_to_yfinance(monkeypatch, tmp_path):
    monkeypatch.setattr(daily_pipeline.pipeline_status, "STATUS_PATH", tmp_path / "status.json")
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(
        daily_pipeline.yfinance_client, "fetch_twse_prices_batch",
        lambda stock_ids, start_date, end_date, on_progress=None: {"2330": [_price_row(stock_id="2330", d="2026-07-24")]},
    )
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260724", dry_run=True, skip_tpex=True)

    assert storage.get_daily_data_status(conn, "2026-07-24") is True


def test_run_daily_pipeline_records_final_status_when_official_endpoint_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(daily_pipeline.pipeline_status, "STATUS_PATH", tmp_path / "status.json")
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=True)

    assert storage.get_daily_data_status(conn, "2026-07-22") is False


def test_run_daily_pipeline_forwards_progress_callback_for_twse_and_tpex_stages(monkeypatch, tmp_path):
    monkeypatch.setattr(daily_pipeline.pipeline_status, "STATUS_PATH", tmp_path / "status.json")
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"},
        {"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"},
    ])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(
        daily_pipeline.yfinance_client, "fetch_tpex_prices_batch",
        lambda stock_ids, start_date, end_date, on_progress=None: (
            on_progress(len(stock_ids), len(stock_ids)) if on_progress else None
        ) or {"6488": [_price_row(stock_id="6488")]},
    )
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    stages = []
    daily_pipeline.run_daily_pipeline(
        conn, date_str="20260722", dry_run=True, skip_tpex=False,
        on_progress=lambda stage, done, total: stages.append((stage, done, total)),
    )

    assert ("TWSE", 1, 1) in stages
    assert ("TPEx", 1, 1) in stages


def test_run_daily_pipeline_writes_heartbeat_status_on_each_progress_tick(monkeypatch, tmp_path):
    """對應2026-07-24的事故：process被強制中止時，狀態檔案的updated_at要能持續往前推進
    (不是只有開始/結束才寫入)，pipeline_status.is_stale()才能正確判斷「太久沒更新=可能
    已經非正常終止」。這裡驗證每次TWSE/TPEx進度回報都會順便重寫一次running狀態，且附上
    stage/progress方便UI顯示。"""
    monkeypatch.setattr(daily_pipeline.pipeline_status, "STATUS_PATH", tmp_path / "status.json")
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [{"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])

    observed_statuses = []

    def _fake_batch(stock_ids, start_date, end_date, on_progress=None):
        if on_progress:
            on_progress(len(stock_ids), len(stock_ids))
            # 進度回報當下(TPEx批次下載途中)，狀態檔案應該已經被心跳寫成running+stage資訊，
            # 不是要等到整個pipeline結束才第一次看到TPEx這個階段。
            observed_statuses.append(daily_pipeline.pipeline_status.read_status())
        return {"6488": [_price_row(stock_id="6488")]}

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_tpex_prices_batch", _fake_batch)
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=False)

    assert len(observed_statuses) == 1
    mid_run_status = observed_statuses[0]
    assert mid_run_status["status"] == "running"
    assert mid_run_status["stage"] == "TPEx"
    assert mid_run_status["progress"] == "1/1"


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
    monkeypatch.setattr(
        daily_pipeline.yfinance_client, "fetch_tpex_prices_batch",
        lambda stock_ids, start_date, end_date, on_progress=None: {"6488": [_price_row(stock_id="6488", d="2026-07-22")]},
    )
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=False)

    row = conn.execute("SELECT market, name FROM stocks WHERE stock_id = '6488'").fetchone()
    assert row == ("TPEx", "環球晶")


def test_fetch_today_tpex_filters_out_non_4_digit_codes(monkeypatch):
    """TPEx股票清單裡混雜ETF/債券/權證等非4碼代號(例如00878B)，這些不是我們要每日追蹤的
    普通股，應該被濾掉，不浪費批次下載的額度去抓它們。"""
    conn = _fresh_conn()
    stock_info = [
        {"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"},
        {"stock_id": "00878B", "name": "某ETF", "market": "TPEx", "industry": None},
        {"stock_id": "73107P", "name": "某權證", "market": "TPEx", "industry": None},
    ]
    fetched_ids = []

    def _fake_batch(stock_ids, start_date, end_date, on_progress=None):
        fetched_ids.extend(stock_ids)
        return {sid: [_price_row(stock_id=sid)] for sid in stock_ids}

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_tpex_prices_batch", _fake_batch)

    daily_pipeline.fetch_today_tpex(conn, "20260722", stock_info)

    assert fetched_ids == ["6488"]  # 只有4碼的普通股被抓


def test_fetch_today_tpex_does_not_call_finmind_anymore(monkeypatch):
    """2026-07-23改用yfinance批次下載取代FinMind逐股抓取後，fetch_today_tpex不應該再
    呼叫finmind_client的任何抓取函式——用「一被呼叫就報錯」確認沒有不小心殘留舊路徑。"""
    conn = _fresh_conn()
    stock_info = [{"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"}]

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("不應該被呼叫：TPEx股價已改用yfinance批次下載")

    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_stock_prices", _fail_if_called)
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_institutional_investors", _fail_if_called)
    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_margin_trading", _fail_if_called)
    monkeypatch.setattr(
        daily_pipeline.yfinance_client, "fetch_tpex_prices_batch",
        lambda stock_ids, start_date, end_date, on_progress=None: {"6488": [_price_row(stock_id="6488")]},
    )

    success_count = daily_pipeline.fetch_today_tpex(conn, "20260722", stock_info)

    assert success_count == 1


def test_run_daily_pipeline_continues_when_one_tpex_stock_has_no_data(monkeypatch):
    """yfinance批次下載時，個別股票查無資料(例如剛下市)不會出現在回傳的dict裡(見
    src/data/yfinance_client.py)，其餘股票應該正常處理、不受影響。"""
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    _stub_stock_info(monkeypatch, [
        {"stock_id": "9999", "name": "測試查無資料股", "market": "TPEx", "industry": None},
        {"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"},
    ])

    def _fake_batch(stock_ids, start_date, end_date, on_progress=None):
        # 模擬9999查無資料(例如剛下市)，只有6488有資料回傳
        return {"6488": [_price_row(stock_id="6488", d="2026-07-22")]}

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_tpex_prices_batch", _fake_batch)
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=False)

    row = conn.execute("SELECT market, name FROM stocks WHERE stock_id = '6488'").fetchone()
    assert row == ("TPEx", "環球晶")
    assert conn.execute("SELECT COUNT(*) FROM stocks WHERE stock_id = '9999'").fetchone()[0] == 0


def test_fetch_today_tpex_returns_zero_and_does_not_raise_when_batch_download_fails(monkeypatch):
    """整批yfinance下載失敗(例如網路問題)不應該讓整條pipeline中斷，這一步直接回傳0檔成功、
    印出錯誤訊息即可(呼叫端/排程紀錄可以看到當天TPEx更新失敗)。"""
    conn = _fresh_conn()
    stock_info = [{"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"}]

    def _raise(stock_ids, start_date, end_date):
        raise RuntimeError("模擬yfinance網路逾時")

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_tpex_prices_batch", _raise)

    success_count = daily_pipeline.fetch_today_tpex(conn, "20260722", stock_info)

    assert success_count == 0


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
