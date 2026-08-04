from datetime import date, timedelta

import pandas as pd
import pytest

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


@pytest.fixture(autouse=True)
def _no_real_taiex_network_calls(monkeypatch):
    """run_daily_pipeline()新增了大盤(TAIEX)更新(見fetch_today_taiex())，會無條件呼叫
    yfinance_client.fetch_taiex_prices()——這是一個真實對外的網路呼叫，這個檔案裡沒有
    明確要測試這個行為的既有測試(例如test_run_daily_pipeline_writes_candidates_and_
    skips_notify_on_dry_run)不應該不小心真的打到Yahoo Finance。只在這個測試檔案生效
    (不是全域tests/conftest.py)，因為只有run_daily_pipeline()這條路徑會無條件觸發，
    test_yfinance_client.py測的是這個函式本身，不應該被這個安全網擋住。個別測試需要驗證
    真正行為時，在測試函式主體內用monkeypatch覆蓋即可(晚於這個fixture執行，會蓋過去)。

    2026-08-04新增：fetch_today_taiex()同時也會呼叫twse_client.fetch_taiex_volume()
    (見_fetch_taiex_official_volume())覆蓋yfinance的volume欄位，同一個理由，一併
    擋住避免真的打到TWSE。

    同一天再新增：fetch_today_tpex()對批次下載後仍失敗的股票，現在會呼叫
    yfinance_client.fetch_twse_prices_batch()當「轉上市」備援(見fetch_today_tpex()
    docstring)——這個檔案裡不少既有測試會故意讓TPEx批次下載對某些股票查無資料
    (模擬剛下市/查無資料的情境)，觸發到這個新的備援呼叫，同一個理由一併擋住預設
    回傳空dict，個別測試需要驗證這個備援行為時在測試函式主體內覆蓋即可。
    """
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda *args, **kwargs: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_taiex_volume", lambda *args, **kwargs: {})
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_twse_prices_batch", lambda *args, **kwargs: {})


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


def test_run_daily_pipeline_notifications_apply_same_default_filters_as_ui(monkeypatch):
    """使用者回報：收到的LINE通知涵蓋股票數比打開UI看到的候選清單多很多，兩邊篩選
    條件對不齊。根因是通知端直接送出run_screen_and_store()的原始candidates(「當天
    觸發過任何一條規則」的完整清單)，沒有套用UI候選清單預設額外要求的均線多頭排列+
    SAR翻轉條件。這裡驗證修正後：daily_pipeline.py會用跟UI完全同一套預設參數
    (chart_data.CANDIDATE_FILTER_DEFAULTS/CANDIDATE_SAR_FLIP_*_DEFAULT/
    CANDIDATE_ZHU_RULE_ONLY_DEFAULT)呼叫apply_candidate_filters()，只有通過篩選
    的股票才會出現在LINE/Email內容裡——不重新測試apply_candidate_filters()本身的
    篩選邏輯是否正確(那部分tests/test_chart_data.py已經覆蓋)，只驗證daily_
    pipeline.py有沒有正確接上、用篩選後的結果縮小candidates。
    """
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    raw_candidates = [
        {"stock_id": "2330", "signal_name": "R-TEST", "entry_price": 100.0, "stop_loss": 95.0, "note": ""},
        {"stock_id": "9999", "signal_name": "R-TEST", "entry_price": 50.0, "stop_loss": 48.0, "note": ""},
    ]
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: raw_candidates)

    captured_filter_args = {}

    def _fake_apply_filters(conn, df, active_filter_labels, sar_flip_option=None, zhu_rule_only=False, as_of_date=None):
        captured_filter_args["active_filter_labels"] = active_filter_labels
        captured_filter_args["sar_flip_option"] = sar_flip_option
        captured_filter_args["zhu_rule_only"] = zhu_rule_only
        # 模擬只有2330通過篩選(9999被均線/SAR條件濾掉)
        return pd.DataFrame({"stock_id": ["2330"]})

    monkeypatch.setattr(daily_pipeline.chart_data, "apply_candidate_filters", _fake_apply_filters)
    monkeypatch.setattr(
        daily_pipeline.chart_data, "load_stock_universe_for_date",
        lambda conn, target_date=None: (pd.DataFrame({"stock_id": ["2330", "9999"]}), target_date, False),
    )

    sent_texts = []
    monkeypatch.setattr(daily_pipeline, "send_line_broadcast", lambda text: sent_texts.append(text))
    monkeypatch.setattr(daily_pipeline, "send_email", lambda subject, body: sent_texts.append(body))

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=False, skip_tpex=True)

    from src.presentation import chart_data

    assert captured_filter_args["active_filter_labels"] == [
        label for label, default in chart_data.CANDIDATE_FILTER_DEFAULTS.items() if default
    ]
    assert captured_filter_args["sar_flip_option"] == chart_data.CANDIDATE_SAR_FLIP_OPTION_DEFAULT
    assert captured_filter_args["zhu_rule_only"] is chart_data.CANDIDATE_ZHU_RULE_ONLY_DEFAULT
    assert any("2330" in text for text in sent_texts)
    assert not any("9999" in text for text in sent_texts)


def test_run_daily_pipeline_falls_back_to_unfiltered_candidates_when_notify_filter_fails(monkeypatch):
    """通知篩選這一步(load_stock_universe_for_date/apply_candidate_filters)萬一
    出錯，不應該讓整條pipeline中斷或掉入'failed'狀態、也不應該讓使用者今天完全
    收不到通知——候選清單已經成功寫進DB了，退回寄出未篩選的原始candidates即可。
    """
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    raw_candidates = [
        {"stock_id": "2330", "signal_name": "R-TEST", "entry_price": 100.0, "stop_loss": 95.0, "note": ""},
    ]
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: raw_candidates)

    def _raise(*args, **kwargs):
        raise RuntimeError("模擬daily_indicators查詢失敗")

    monkeypatch.setattr(daily_pipeline.chart_data, "load_stock_universe_for_date", _raise)

    sent_texts = []
    monkeypatch.setattr(daily_pipeline, "send_line_broadcast", lambda text: sent_texts.append(text))
    monkeypatch.setattr(daily_pipeline, "send_email", lambda subject, body: sent_texts.append(body))

    result = daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=False, skip_tpex=True)

    assert result == raw_candidates
    assert any("2330" in text for text in sent_texts)


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


def test_fetch_today_tpex_falls_back_to_twse_suffix_for_transferred_listings(monkeypatch, capsys):
    """2026-08-04發現的真正root cause：使用者回報14:30(TPEx早已收盤)仍看到一長串
    「下載錯誤」，之前誤判成「上櫃還沒收盤」——直接用同一批代號查yfinance才發現這些
    公司大多已經「轉上市」，Yahoo根本不認得.TWO這個後綴，要改用.TW才查得到。這裡驗證
    TPEx批次下載失敗的股票會用.TW後綴再查一次，查到的話market要寫成TWSE(不是TPEx)，
    不應該再印出「下載錯誤」。"""
    conn = _fresh_conn()
    stock_info = [{"stock_id": "6472", "name": "保瑞", "market": "TPEx", "industry": "醫藥"}]
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_tpex_prices_batch", lambda *a, **k: {})
    monkeypatch.setattr(
        daily_pipeline.yfinance_client, "fetch_twse_prices_batch",
        lambda stock_ids, start_date, end_date: {"6472": [_price_row(stock_id="6472")]},
    )

    success_count = daily_pipeline.fetch_today_tpex(conn, "20260722", stock_info)

    assert success_count == 1
    row = conn.execute("SELECT market, name FROM stocks WHERE stock_id = '6472'").fetchone()
    assert row == ("TWSE", "保瑞")  # 修正掉FinMind暫時落後的TPEx分類，不是寫成TPEx
    assert "下載錯誤" not in capsys.readouterr().out


def test_fetch_today_tpex_prints_download_error_only_when_both_suffixes_fail(monkeypatch, capsys):
    """兩種市場後綴都查不到才是真正查無資料——2026-08-04追查證實這種情況背後幾乎
    都是真的下市/併購/終止興櫃買賣(不是誤判)，這時應該印出訊息、同時記錄進
    delisted_stocks表，之後排程才不會每天重複浪費下載嘗試。"""
    conn = _fresh_conn()
    stock_info = [{"stock_id": "9999", "name": "測試查無資料股", "market": "TPEx", "industry": None}]
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_tpex_prices_batch", lambda *a, **k: {})
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_twse_prices_batch", lambda *a, **k: {})

    success_count = daily_pipeline.fetch_today_tpex(conn, "20260722", stock_info)

    assert success_count == 0
    assert "9999測試查無資料股 下載錯誤" in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM stocks WHERE stock_id = '9999'").fetchone()[0] == 0
    row = conn.execute("SELECT name, delisted_date, reason FROM delisted_stocks WHERE stock_id = '9999'").fetchone()
    assert row == ("測試查無資料股", None, "兩種市場後綴(.TWO/.TW)皆查無資料，可能已下市/併購/終止興櫃買賣")


def test_fetch_today_tpex_skips_already_known_delisted_stocks(monkeypatch):
    """已經記錄在delisted_stocks的股票，一開始就該從下載清單裡篩掉，不再浪費一次
    嘗試(不管是TPEx還是轉上市備援都不應該被呼叫到這檔)。"""
    conn = _fresh_conn()
    storage.upsert_delisted_stocks(conn, [
        {"stock_id": "8418", "name": "捷必勝-KY", "delisted_date": "2024-01-31",
         "reason": "私有化下市", "noted_at": "2026-08-04T00:00:00"},
    ])
    stock_info = [
        {"stock_id": "8418", "name": "捷必勝-KY", "market": "TPEx", "industry": None},
        {"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"},
    ]
    requested_ids = []

    def _fake_batch(stock_ids, start_date, end_date, on_progress=None):
        requested_ids.extend(stock_ids)
        return {sid: [_price_row(stock_id=sid)] for sid in stock_ids}

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_tpex_prices_batch", _fake_batch)

    success_count = daily_pipeline.fetch_today_tpex(conn, "20260722", stock_info)

    assert success_count == 1
    assert requested_ids == ["6488"]  # 已知下市的8418不應該被納入下載清單


def test_fetch_today_tpex_continues_when_twse_fallback_raises(monkeypatch):
    """轉上市備援下載失敗(例如網路問題)不應該讓整條函式中斷——TPEx那批成功抓到的股票
    依然要正常寫入。"""
    conn = _fresh_conn()
    stock_info = [
        {"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"},
        {"stock_id": "6472", "name": "保瑞", "market": "TPEx", "industry": "醫藥"},
    ]
    monkeypatch.setattr(
        daily_pipeline.yfinance_client, "fetch_tpex_prices_batch",
        lambda *a, **k: {"6488": [_price_row(stock_id="6488")]},
    )

    def _raise(stock_ids, start_date, end_date):
        raise RuntimeError("模擬yfinance網路逾時")

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_twse_prices_batch", _raise)

    success_count = daily_pipeline.fetch_today_tpex(conn, "20260722", stock_info)

    assert success_count == 1
    row = conn.execute("SELECT market FROM stocks WHERE stock_id = '6488'").fetchone()
    assert row == ("TPEx",)


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


def test_fetch_today_taiex_upserts_rows_and_returns_true(monkeypatch):
    conn = _fresh_conn()
    fake_rows = [{
        "stock_id": daily_pipeline.yfinance_client.TAIEX_STOCK_ID, "date": "2026-07-22",
        "open": 17000.0, "high": 17100.0, "low": 16950.0, "close": 17050.0,
        "volume": 5000000, "trading_money": None, "trading_turnover": None, "spread": None,
    }]
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: fake_rows)

    result = daily_pipeline.fetch_today_taiex(conn, "20260722")

    assert result is True
    row = conn.execute(
        "SELECT close FROM stock_prices WHERE stock_id = ? AND date = ?",
        (daily_pipeline.yfinance_client.TAIEX_STOCK_ID, "2026-07-22"),
    ).fetchone()
    assert row == (17050.0,)


def test_fetch_today_taiex_requests_a_lookback_window_not_just_today(monkeypatch):
    """2026-08-01修正：Yahoo Finance的^TWII成交量常在當天查詢時還沒回補完整(volume=0)，
    幾天後同一天再查才有正確數字，但這裡原本只抓`date_str`當天，之後排程永遠不會回頭
    重新查詢——資料因此永久卡在0。改成每次連同`TAIEX_REFETCH_WINDOW_DAYS`天前一併
    重新抓取，這裡驗證真的把start往回拉，不是只抓當天那一天。"""
    conn = _fresh_conn()
    captured = {}

    def _capture(start, end):
        captured["start"] = start
        captured["end"] = end
        return []

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", _capture)

    daily_pipeline.fetch_today_taiex(conn, "20260731")

    assert captured["end"] == "2026-08-01"
    expected_start = (
        date(2026, 7, 31) - timedelta(days=daily_pipeline.TAIEX_REFETCH_WINDOW_DAYS)
    ).isoformat()
    assert captured["start"] == expected_start


def test_fetch_today_taiex_repair_refetch_overwrites_stale_zero_volume(monkeypatch):
    """驗證回抓的舊資料能透過upsert_stock_prices()的ON CONFLICT DO UPDATE正確覆蓋掉
    先前存進DB的過期0成交量，不需要額外的「偵測哪幾天是0」邏輯。"""
    conn = _fresh_conn()
    stock_id = daily_pipeline.yfinance_client.TAIEX_STOCK_ID
    storage.upsert_stocks(conn, [{
        "stock_id": stock_id, "name": "台股加權指數", "market": "INDEX",
        "industry": None, "updated_at": "2026-07-28T19:30:00",
    }])
    storage.upsert_stock_prices(conn, [{
        "stock_id": stock_id, "date": "2026-07-28", "open": 43000.0, "high": 43200.0,
        "low": 41500.0, "close": 41603.36, "volume": 0, "trading_money": None,
        "trading_turnover": None, "spread": None,
    }])

    fake_rows = [{
        "stock_id": stock_id, "date": "2026-07-28", "open": 43000.0, "high": 43200.0,
        "low": 41500.0, "close": 41603.36, "volume": 4492500, "trading_money": None,
        "trading_turnover": None, "spread": None,
    }]
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: fake_rows)

    daily_pipeline.fetch_today_taiex(conn, "20260731")

    row = conn.execute(
        "SELECT volume FROM stock_prices WHERE stock_id = ? AND date = ?", (stock_id, "2026-07-28"),
    ).fetchone()
    assert row == (4492500,)


def test_fetch_today_taiex_overwrites_volume_with_twse_official_figure(monkeypatch):
    """2026-08-04修正：yfinance的^TWII volume欄位長期不可靠(甚至整天資料直接從回應
    消失，不只是volume=0)，改用TWSE官方FMTQIK的成交股數覆蓋掉yfinance回傳的volume，
    OHLC(開高低收)繼續維持yfinance原始值。"""
    conn = _fresh_conn()
    stock_id = daily_pipeline.yfinance_client.TAIEX_STOCK_ID
    fake_rows = [{
        "stock_id": stock_id, "date": "2026-08-03", "open": 42780.42, "high": 43784.19,
        "low": 42780.42, "close": 43386.41, "volume": 0, "trading_money": None,
        "trading_turnover": None, "spread": None,
    }]
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: fake_rows)
    monkeypatch.setattr(
        daily_pipeline.twse_client, "fetch_taiex_volume",
        lambda month: {"2026-08-03": 11427047935} if month.startswith("202608") else {},
    )

    daily_pipeline.fetch_today_taiex(conn, "20260803")

    row = conn.execute(
        "SELECT open, close, volume FROM stock_prices WHERE stock_id = ? AND date = ?",
        (stock_id, "2026-08-03"),
    ).fetchone()
    assert row == (42780.42, 43386.41, 11427047935)  # OHLC維持yfinance原值，只有volume被換掉


def test_fetch_today_taiex_keeps_yfinance_volume_when_twse_has_no_data_for_that_date(monkeypatch):
    """TWSE FMTQIK查無某一天的資料(例如今天還沒收盤公布)時，該天的volume維持
    yfinance原始值，不應該被清空或報錯中斷。"""
    conn = _fresh_conn()
    stock_id = daily_pipeline.yfinance_client.TAIEX_STOCK_ID
    fake_rows = [{
        "stock_id": stock_id, "date": "2026-08-04", "open": 43092.49, "high": 43912.77,
        "low": 42895.81, "close": 43360.66, "volume": 0, "trading_money": None,
        "trading_turnover": None, "spread": None,
    }]
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: fake_rows)
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_taiex_volume", lambda month: {})

    daily_pipeline.fetch_today_taiex(conn, "20260804")

    row = conn.execute(
        "SELECT volume FROM stock_prices WHERE stock_id = ? AND date = ?", (stock_id, "2026-08-04"),
    ).fetchone()
    assert row == (0,)


def test_fetch_today_taiex_continues_when_twse_volume_fetch_raises(monkeypatch):
    """TWSE FMTQIK呼叫失敗不應該讓整個大盤更新失敗——yfinance抓到的OHLC資料依然要
    正常寫入，只有volume欄位維持yfinance原始值。"""
    conn = _fresh_conn()
    stock_id = daily_pipeline.yfinance_client.TAIEX_STOCK_ID
    fake_rows = [{
        "stock_id": stock_id, "date": "2026-08-03", "open": 42780.42, "high": 43784.19,
        "low": 42780.42, "close": 43386.41, "volume": 0, "trading_money": None,
        "trading_turnover": None, "spread": None,
    }]
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: fake_rows)

    def _raise(month):
        raise RuntimeError("模擬TWSE暫時打不通")

    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_taiex_volume", _raise)

    result = daily_pipeline.fetch_today_taiex(conn, "20260803")

    assert result is True
    row = conn.execute(
        "SELECT close, volume FROM stock_prices WHERE stock_id = ? AND date = ?",
        (stock_id, "2026-08-03"),
    ).fetchone()
    assert row == (43386.41, 0)


def test_fetch_today_taiex_repairs_volume_for_date_yfinance_no_longer_returns(monkeypatch):
    """2026-08-04真實驗證時發現的真正bug：Yahoo有時不是「volume=0」，是把整天的資料
    列從回應裡拿掉，rows根本沒有那個日期可以走到、逐一遍歷rows永遠不會修到它。這裡
    模擬這個情境：DB裡已經有這天先前存過的OHLC(Yahoo以前回傳過)，但這次yfinance的
    rows完全不含這天，TWSE卻有這天的官方成交量——應該要用DB裡的舊OHLC+TWSE的新
    volume組一筆補回去，不能因為rows沒有這個日期就放棄。"""
    conn = _fresh_conn()
    stock_id = daily_pipeline.yfinance_client.TAIEX_STOCK_ID
    storage.upsert_stocks(conn, [{
        "stock_id": stock_id, "name": "台股加權指數", "market": "INDEX",
        "industry": None, "updated_at": "2026-08-03T14:00:00",
    }])
    storage.upsert_stock_prices(conn, [{
        "stock_id": stock_id, "date": "2026-08-03", "open": 42780.42, "high": 43784.19,
        "low": 42780.42, "close": 43386.41, "volume": 0, "trading_money": None,
        "trading_turnover": None, "spread": None,
    }])

    # yfinance這次完全不包含2026-08-03這一天(模擬Yahoo把這天從回應裡拿掉)
    fake_rows = [{
        "stock_id": stock_id, "date": "2026-08-04", "open": 43092.49, "high": 43912.77,
        "low": 42895.81, "close": 43360.66, "volume": 0, "trading_money": None,
        "trading_turnover": None, "spread": None,
    }]
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: fake_rows)
    monkeypatch.setattr(
        daily_pipeline.twse_client, "fetch_taiex_volume",
        lambda month: {"2026-08-03": 11427047935} if month.startswith("202608") else {},
    )

    result = daily_pipeline.fetch_today_taiex(conn, "20260804")

    assert result is True
    row = conn.execute(
        "SELECT open, close, volume FROM stock_prices WHERE stock_id = ? AND date = ?",
        (stock_id, "2026-08-03"),
    ).fetchone()
    assert row == (42780.42, 43386.41, 11427047935)  # 舊OHLC保留，volume被官方數字補上


def test_fetch_today_taiex_skips_date_with_twse_volume_but_no_ohlc_anywhere(monkeypatch):
    """TWSE有某天的官方成交量，但yfinance從來沒回傳過那天(rows不含、DB裡也沒有既有
    資料)——沒有open/high/low/close可以組成一筆完整的紀錄，應該跳過，不寫入殘缺列。"""
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_taiex_volume", lambda month: {"2026-08-03": 11427047935})

    result = daily_pipeline.fetch_today_taiex(conn, "20260804")

    assert result is False
    stock_id = daily_pipeline.yfinance_client.TAIEX_STOCK_ID
    row = conn.execute(
        "SELECT COUNT(*) FROM stock_prices WHERE stock_id = ? AND date = ?", (stock_id, "2026-08-03"),
    ).fetchone()
    assert row == (0,)


def test_month_starts_covers_month_boundary_including_year_rollover():
    assert daily_pipeline._month_starts("2026-07-28", "2026-08-04") == ["20260701", "20260801"]
    assert daily_pipeline._month_starts("2026-08-01", "2026-08-04") == ["20260801"]
    assert daily_pipeline._month_starts("2025-12-20", "2026-01-05") == ["20251201", "20260101"]


def test_fetch_today_taiex_returns_false_when_no_data(monkeypatch):
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: [])

    assert daily_pipeline.fetch_today_taiex(conn, "20260722") is False


def test_run_daily_pipeline_updates_taiex_alongside_stocks(monkeypatch):
    """大盤更新是run_daily_pipeline()流程的一部分，個股資料抓取成功時應該一併呼叫。"""
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [])

    fake_rows = [{
        "stock_id": daily_pipeline.yfinance_client.TAIEX_STOCK_ID, "date": "2026-07-22",
        "open": 17000.0, "high": 17100.0, "low": 16950.0, "close": 17050.0,
        "volume": 5000000, "trading_money": None, "trading_turnover": None, "spread": None,
    }]
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: fake_rows)

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=True)

    row = conn.execute(
        "SELECT close FROM stock_prices WHERE stock_id = ? AND date = ?",
        (daily_pipeline.yfinance_client.TAIEX_STOCK_ID, "2026-07-22"),
    ).fetchone()
    assert row == (17050.0,)
    # 大盤要有一筆market="INDEX"的stocks資料才能滿足stock_prices的外鍵參照，但market
    # 要跟"TWSE"/"TPEx"區分開，讓load_trailing_frames()能篩掉它、不被個股批次選股邏輯誤判
    stock_row = conn.execute(
        "SELECT market FROM stocks WHERE stock_id = ?", (daily_pipeline.yfinance_client.TAIEX_STOCK_ID,)
    ).fetchone()
    assert stock_row == ("INDEX",)


def test_run_daily_pipeline_refreshes_indicator_window_after_screening(monkeypatch):
    """2026-08-02新增：均線/SAR快取(daily_indicators)除了run_screen_and_store()裡
    只算當天一天之外，排程執行時要額外往回刷新INDICATOR_REFRESH_WINDOW_DAYS個交易日，
    吸收股價資料事後被修正的風險(見scripts/daily_pipeline.py的INDICATOR_REFRESH_
    WINDOW_DAYS常數說明)。"""
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: [])
    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [])

    calls = []
    monkeypatch.setattr(
        daily_pipeline, "refresh_indicator_window",
        lambda conn, end_date, window_days: calls.append((end_date, window_days)) or 0,
    )

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=True)

    assert calls == [("2026-07-22", daily_pipeline.INDICATOR_REFRESH_WINDOW_DAYS)]


def test_run_daily_pipeline_continues_when_indicator_refresh_raises(monkeypatch):
    """均線/SAR快取刷新失敗不應該讓整條pipeline中斷——候選清單已經算完寫入了，快取
    只是輔助查詢用的衍生資料。"""
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", lambda start, end: [])
    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [])

    def _raise(conn, end_date, window_days):
        raise RuntimeError("模擬刷新失敗")

    monkeypatch.setattr(daily_pipeline, "refresh_indicator_window", _raise)

    candidates = daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=True)

    assert candidates == []  # 沒有中斷拋出


def test_run_daily_pipeline_continues_when_taiex_update_raises(monkeypatch):
    """大盤更新失敗不應該讓整條pipeline中斷——個股資料已經抓完，候選清單照樣要算。"""
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(daily_screener, "screen_all_stocks", lambda frames, min_days: [])

    def _raise(start, end):
        raise RuntimeError("模擬Yahoo Finance暫時打不通")

    monkeypatch.setattr(daily_pipeline.yfinance_client, "fetch_taiex_prices", _raise)

    candidates = daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=True)

    assert candidates == []  # screen_all_stocks本身回傳空list，跟大盤更新失敗與否無關，只驗證沒有中斷拋出


# ============================================================
# fetch_today_tpex_institutional / refresh_watchlist_holder_shares (2026-08-04新增)
# ============================================================


def _fresh_portfolio_conn():
    import sqlite3

    from src.data import portfolio_storage
    conn = sqlite3.connect(":memory:")
    portfolio_storage.ensure_portfolio_schema(conn)
    return conn


def test_fetch_today_tpex_institutional_writes_rows_and_upserts_stocks(monkeypatch):
    conn = _fresh_conn()
    fake_rows = [
        {"stock_id": "1264", "date": "2026-08-03", "investor_type": "Foreign_Investor", "buy": 1000, "sell": 500},
        {"stock_id": "1264", "date": "2026-08-03", "investor_type": "Dealer_self", "buy": 9000, "sell": 9137},
    ]
    monkeypatch.setattr(daily_pipeline.tpex_client, "fetch_institutional_investors", lambda: fake_rows)

    count = daily_pipeline.fetch_today_tpex_institutional(conn, {"1264": {"name": "test名稱", "industry": "測試業"}})

    assert count == 1
    stock_row = conn.execute("SELECT name, market, industry FROM stocks WHERE stock_id = '1264'").fetchone()
    assert stock_row == ("test名稱", "TPEx", "測試業")
    rows = conn.execute(
        "SELECT investor_type, buy, sell FROM institutional_investors WHERE stock_id = '1264' ORDER BY investor_type"
    ).fetchall()
    assert rows == [("Dealer_self", 9000, 9137), ("Foreign_Investor", 1000, 500)]


def test_fetch_today_tpex_institutional_falls_back_to_stock_id_when_no_stock_info(monkeypatch):
    conn = _fresh_conn()
    fake_rows = [{"stock_id": "1264", "date": "2026-08-03", "investor_type": "Foreign_Investor", "buy": 1, "sell": 0}]
    monkeypatch.setattr(daily_pipeline.tpex_client, "fetch_institutional_investors", lambda: fake_rows)

    count = daily_pipeline.fetch_today_tpex_institutional(conn, {})

    assert count == 1
    stock_row = conn.execute("SELECT name FROM stocks WHERE stock_id = '1264'").fetchone()
    assert stock_row == ("1264",)


def test_fetch_today_tpex_institutional_returns_zero_when_no_rows(monkeypatch):
    conn = _fresh_conn()
    monkeypatch.setattr(daily_pipeline.tpex_client, "fetch_institutional_investors", lambda: [])

    assert daily_pipeline.fetch_today_tpex_institutional(conn, {}) == 0


def test_refresh_watchlist_holder_shares_only_covers_watchlist_stocks(monkeypatch):
    from src.data import portfolio_storage

    conn = _fresh_conn()
    storage.upsert_stocks(conn, [{"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-08-04"}])
    portfolio_conn = _fresh_portfolio_conn()
    group_id = portfolio_storage.add_watchlist_group(portfolio_conn, "半導體")
    portfolio_storage.add_watchlist_stock(portfolio_conn, group_id, "2330")

    fetch_calls = []

    def _fake_fetch(stock_id, start_date, end_date):
        fetch_calls.append(stock_id)
        return [{"stock_id": stock_id, "date": "2026-07-31", "holding_shares_level": "more than 1,000,001",
                  "people": 10, "unit": 100, "percent": 12.0}]

    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_holding_shares_per", _fake_fetch)

    count = daily_pipeline.refresh_watchlist_holder_shares(conn, portfolio_conn)

    assert fetch_calls == ["2330"]  # 只查觀察清單裡的股票，不是全市場
    assert count == 1
    row = conn.execute(
        "SELECT stock_id, percent FROM holder_shares_distribution WHERE stock_id = '2330'"
    ).fetchone()
    assert row == ("2330", 12.0)


def test_refresh_watchlist_holder_shares_skips_failed_stock_and_continues(monkeypatch):
    from src.data import portfolio_storage

    conn = _fresh_conn()
    storage.upsert_stocks(conn, [
        {"stock_id": "2330", "name": "台積電", "market": "TWSE", "industry": None, "updated_at": "2026-08-04"},
        {"stock_id": "2454", "name": "聯發科", "market": "TWSE", "industry": None, "updated_at": "2026-08-04"},
    ])
    portfolio_conn = _fresh_portfolio_conn()
    group_id = portfolio_storage.add_watchlist_group(portfolio_conn, "半導體")
    portfolio_storage.add_watchlist_stock(portfolio_conn, group_id, "2330")
    portfolio_storage.add_watchlist_stock(portfolio_conn, group_id, "2454")

    def _fake_fetch(stock_id, start_date, end_date):
        if stock_id == "2330":
            raise RuntimeError("模擬FinMind暫時性錯誤")
        return [{"stock_id": stock_id, "date": "2026-07-31", "holding_shares_level": "more than 1,000,001",
                  "people": 10, "unit": 100, "percent": 5.0}]

    monkeypatch.setattr(daily_pipeline.finmind_client, "fetch_holding_shares_per", _fake_fetch)

    count = daily_pipeline.refresh_watchlist_holder_shares(conn, portfolio_conn)

    assert count == 1  # 2330失敗略過，2454照常成功
    assert conn.execute("SELECT COUNT(*) FROM holder_shares_distribution WHERE stock_id = '2330'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM holder_shares_distribution WHERE stock_id = '2454'").fetchone()[0] == 1


def test_refresh_watchlist_holder_shares_returns_zero_when_watchlist_empty(monkeypatch):
    conn = _fresh_conn()
    portfolio_conn = _fresh_portfolio_conn()

    assert daily_pipeline.refresh_watchlist_holder_shares(conn, portfolio_conn) == 0


def test_run_daily_pipeline_chip_refresh_triggers_tpex_institutional_fg_and_sheets_export(monkeypatch):
    """chip_refresh=True(獨立於dry_run)應該觸發：TPEx三大法人、觀察清單F/G、Google
    Sheet匯出這三件事——即使dry_run=True(對應17:00排程帶--dry-run --chip-refresh的
    情境，理由見run_daily_pipeline() docstring)。"""
    conn = _fresh_conn()
    portfolio_conn = _fresh_portfolio_conn()
    monkeypatch.setattr(daily_pipeline, "get_default_portfolio_connection", lambda: portfolio_conn)

    _stub_stock_info(monkeypatch, [{"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(
        daily_pipeline.yfinance_client, "fetch_tpex_prices_batch",
        lambda stock_ids, start_date, end_date, on_progress=None: {"6488": [_price_row(stock_id="6488")]},
    )
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    tpex_institutional_calls = []
    monkeypatch.setattr(
        daily_pipeline, "fetch_today_tpex_institutional",
        lambda conn, stock_info_by_id: tpex_institutional_calls.append(stock_info_by_id) or 1,
    )
    holder_refresh_calls = []
    monkeypatch.setattr(
        daily_pipeline, "refresh_watchlist_holder_shares",
        lambda conn, pconn: holder_refresh_calls.append(pconn) or 0,
    )
    export_calls = []
    monkeypatch.setattr(
        daily_pipeline.watchlist_export, "export_all_watchlist_groups",
        lambda conn, pconn, interactive: export_calls.append((pconn, interactive)) or 0,
    )

    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=False, chip_refresh=True)

    assert len(tpex_institutional_calls) == 1
    assert holder_refresh_calls == [portfolio_conn]
    assert export_calls == [(portfolio_conn, False)]


def test_run_daily_pipeline_chip_refresh_false_skips_the_three_steps(monkeypatch):
    conn = _fresh_conn()
    _stub_stock_info(monkeypatch, [{"stock_id": "6488", "name": "環球晶", "market": "TPEx", "industry": "半導體"}])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_stock_prices", lambda date_str: [_price_row()])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_institutional_investors", lambda date_str: [])
    monkeypatch.setattr(daily_pipeline.twse_client, "fetch_margin_trading", lambda date_str: [])
    monkeypatch.setattr(
        daily_pipeline.yfinance_client, "fetch_tpex_prices_batch",
        lambda stock_ids, start_date, end_date, on_progress=None: {"6488": [_price_row(stock_id="6488")]},
    )
    monkeypatch.setattr(daily_pipeline, "run_screen_and_store", lambda conn, iso_date, min_days: [])

    def _fail(*args, **kwargs):
        raise AssertionError("chip_refresh=False時不應該被呼叫")

    monkeypatch.setattr(daily_pipeline, "fetch_today_tpex_institutional", _fail)
    monkeypatch.setattr(daily_pipeline, "refresh_watchlist_holder_shares", _fail)
    monkeypatch.setattr(daily_pipeline.watchlist_export, "export_all_watchlist_groups", _fail)
    monkeypatch.setattr(daily_pipeline, "get_default_portfolio_connection", _fail)

    # 不應該拋出AssertionError
    daily_pipeline.run_daily_pipeline(conn, date_str="20260722", dry_run=True, skip_tpex=False, chip_refresh=False)
