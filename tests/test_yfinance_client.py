import time

import pandas as pd
import pytest

import src.data.yfinance_client as yfinance_client


def _multi_ticker_df(tickers: list[str], dates: list[str]) -> pd.DataFrame:
    """模擬yf.download()對多檔ticker的批次回傳格式：欄位是(欄位名, ticker)的MultiIndex，
    level 0是OHLCV欄位名、level 1是ticker，這是yfinance在沒有指定group_by時的預設結構。"""
    idx = pd.date_range(dates[0], periods=len(dates))
    columns = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], tickers])
    data = {}
    for field in ["Open", "High", "Low", "Close", "Volume"]:
        for i, ticker in enumerate(tickers):
            base = 100.0 + i * 10
            data[(field, ticker)] = [base + j for j in range(len(dates))]
    return pd.DataFrame(data, index=idx, columns=columns)


def _single_ticker_df(dates: list[str]) -> pd.DataFrame:
    """模擬yf.download()只下載1檔ticker時的回傳格式：一般Index，不是MultiIndex。"""
    idx = pd.date_range(dates[0], periods=len(dates))
    return pd.DataFrame(
        {"Open": [100.0] * len(dates), "High": [105.0] * len(dates), "Low": [99.0] * len(dates),
         "Close": [102.0] * len(dates), "Volume": [1000] * len(dates)},
        index=idx,
    )


def test_fetch_prices_batch_extracts_each_ticker_from_multiindex(monkeypatch):
    df_batch = _multi_ticker_df(["2330.TW", "5871.TW"], ["2026-07-22"])

    def _fake_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        assert tickers == ["2330.TW", "5871.TW"]
        return df_batch

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_prices_batch(["2330", "5871"], "2026-07-22", "2026-07-23", market_suffix=".TW")

    assert set(result.keys()) == {"2330", "5871"}
    assert result["2330"][0]["open"] == 100.0
    assert result["5871"][0]["open"] == 110.0  # _multi_ticker_df讓第2檔的base價位高10


def test_fetch_tpex_prices_batch_uses_two_suffix(monkeypatch):
    captured = {}

    def _fake_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        captured["tickers"] = tickers
        return _multi_ticker_df(tickers, ["2026-07-22", "2026-07-23"])

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_tpex_prices_batch(["5871", "6488"], "2026-07-22", "2026-07-24")

    assert captured["tickers"] == ["5871.TWO", "6488.TWO"]
    assert set(result.keys()) == {"5871", "6488"}
    assert len(result["5871"]) == 2
    row = result["5871"][0]
    assert row["stock_id"] == "5871"
    assert row["date"] == "2026-07-22"
    assert row["open"] == 100.0  # _multi_ticker_df讓每個欄位都用同一組base+j值，含close/volume
    assert row["close"] == 100.0
    assert row["volume"] == 100
    assert row["trading_money"] is None


def test_fetch_prices_batch_skips_ticker_with_no_data(monkeypatch):
    monkeypatch.setattr(yfinance_client, "RETRY_DELAY_SECONDS", 0)  # 加速測試，不用真的等待
    df_batch = _multi_ticker_df(["5871.TWO"], ["2026-07-22"])

    def _fake_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        return df_batch

    monkeypatch.setattr("yfinance.download", _fake_download)

    # 要求2檔，但yf.download只回傳其中1檔有資料(模擬另一檔下市/查無資料的情況)，且
    # 重試RETRY_ATTEMPTS次後依然查不到("9999"從頭到尾都不在_fake_download的回應裡)
    result = yfinance_client.fetch_prices_batch(["5871", "9999"], "2026-07-22", "2026-07-23", market_suffix=".TWO")

    assert set(result.keys()) == {"5871"}


def test_fetch_prices_batch_drops_rows_with_nan_close(monkeypatch):
    idx = pd.date_range("2026-07-22", periods=2)
    columns = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], ["5871.TWO"]])
    df_batch = pd.DataFrame(
        {("Open", "5871.TWO"): [100.0, 101.0], ("High", "5871.TWO"): [105.0, 106.0],
         ("Low", "5871.TWO"): [99.0, 100.0], ("Close", "5871.TWO"): [102.0, float("nan")],
         ("Volume", "5871.TWO"): [1000, 1100]},
        index=idx, columns=columns,
    )

    def _fake_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        return df_batch

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_prices_batch(["5871"], "2026-07-22", "2026-07-24", market_suffix=".TWO")

    assert len(result["5871"]) == 1  # 第二天Close是NaN，應該被濾掉
    assert result["5871"][0]["date"] == "2026-07-22"


def test_fetch_prices_batch_handles_single_ticker_non_multiindex(monkeypatch):
    def _fake_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        return _single_ticker_df(["2026-07-22"])

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_prices_batch(["5871"], "2026-07-22", "2026-07-23", market_suffix=".TWO")

    assert result["5871"][0]["close"] == 102.0


def test_fetch_prices_batch_returns_empty_dict_when_download_returns_empty(monkeypatch):
    monkeypatch.setattr(yfinance_client, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr("yfinance.download", lambda *a, **k: pd.DataFrame())

    result = yfinance_client.fetch_prices_batch(["5871"], "2026-07-22", "2026-07-23", market_suffix=".TWO")

    assert result == {}


def test_fetch_twse_prices_batch_uses_tw_suffix(monkeypatch):
    captured = {}

    def _fake_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        captured["tickers"] = tickers
        return _multi_ticker_df(tickers, ["2026-07-24"])

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_twse_prices_batch(["2330", "1101"], "2026-07-24", "2026-07-25")

    assert captured["tickers"] == ["2330.TW", "1101.TW"]
    assert set(result.keys()) == {"2330", "1101"}


def test_fetch_prices_batch_reports_progress_after_each_batch(monkeypatch):
    monkeypatch.setattr(yfinance_client, "BATCH_SIZE", 2)  # 縮小批次大小方便測試多批次的情境

    def _fake_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        return _multi_ticker_df(tickers, ["2026-07-22"])

    monkeypatch.setattr("yfinance.download", _fake_download)

    progress_calls = []
    yfinance_client.fetch_prices_batch(
        ["1101", "1102", "1103", "1104", "1105"], "2026-07-22", "2026-07-23",
        market_suffix=".TW", on_progress=lambda done, total: progress_calls.append((done, total)),
    )

    # 5檔、每批2檔：應該回報(2,5) (4,5) (5,5)三次，最後一批不足批次大小也要回報實際處理數
    assert progress_calls == [(2, 5), (4, 5), (5, 5)]


def test_fetch_prices_batch_works_without_progress_callback(monkeypatch):
    """on_progress是選填參數，不傳時不應該crash(既有呼叫端沒有指定這個參數)。"""
    monkeypatch.setattr("yfinance.download", lambda *a, **k: _multi_ticker_df(["5871.TWO"], ["2026-07-22"]))

    result = yfinance_client.fetch_prices_batch(["5871"], "2026-07-22", "2026-07-23", market_suffix=".TWO")

    assert set(result.keys()) == {"5871"}


def test_download_with_hard_timeout_raises_when_download_hangs(monkeypatch):
    """對應2026-07-24排程首次真實觸發時踩到的事故：yf.download()自己的timeout=10參數
    不保證真的會讓函式返回(觀察到CPU降到0%、連線卡在CloseWait狀態12分鐘以上)。
    _download_with_hard_timeout()額外包一層自己控制的硬性逾時，逾時要拋出
    BatchDownloadTimeout，不能讓呼叫端也跟著卡住。"""
    monkeypatch.setattr(yfinance_client, "HARD_TIMEOUT_SECONDS", 0.2)

    def _hanging_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        time.sleep(5)  # 模擬掛住，遠超過0.2秒的硬性逾時

    monkeypatch.setattr("yfinance.download", _hanging_download)

    with pytest.raises(yfinance_client.BatchDownloadTimeout):
        yfinance_client._download_with_hard_timeout(["1101.TW"], "2026-07-22", "2026-07-23")


def test_download_with_hard_timeout_reraises_underlying_exception(monkeypatch):
    """yf.download()本身丟出例外(例如網路錯誤)時，應該原樣往外拋，不是被吞掉或誤判成逾時。"""
    def _raise(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        raise RuntimeError("模擬yfinance網路錯誤")

    monkeypatch.setattr("yfinance.download", _raise)

    with pytest.raises(RuntimeError, match="模擬yfinance網路錯誤"):
        yfinance_client._download_with_hard_timeout(["1101.TW"], "2026-07-22", "2026-07-23")


def test_fetch_prices_batch_skips_hung_batch_and_keeps_other_batches(monkeypatch):
    """兩批下載，第一批卡住逾時、第二批正常——第一批被跳過不拋例外，第二批的結果不受影響，
    對應yfinance批次下載其中一批卡住時，不該讓整次抓取全部失敗。"""
    monkeypatch.setattr(yfinance_client, "BATCH_SIZE", 1)
    monkeypatch.setattr(yfinance_client, "HARD_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(yfinance_client, "RETRY_DELAY_SECONDS", 0)

    def _fake_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        if tickers[0] == "1101.TW":
            time.sleep(5)
        return _multi_ticker_df(tickers, ["2026-07-22"])

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_prices_batch(["1101", "1102"], "2026-07-22", "2026-07-23", market_suffix=".TW")

    assert "1101" not in result  # 這一批逾時被跳過
    assert "1102" in result  # 另一批正常成功，不受影響


def test_fetch_prices_batch_retries_failed_tickers_and_succeeds_on_second_attempt(monkeypatch):
    """對應2026-07-29的使用者回報：個股偶發下載失敗多半是暫時性問題，重試就會成功。
    模擬"5871"第一次查無資料、重試時才查到，最終應該出現在結果裡。"""
    monkeypatch.setattr(yfinance_client, "RETRY_DELAY_SECONDS", 0)
    call_count = {"n": 0}

    def _fake_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return pd.DataFrame()  # 第一次(第一輪)查無資料
        return _multi_ticker_df(tickers, ["2026-07-22"])  # 重試時成功

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_prices_batch(["5871"], "2026-07-22", "2026-07-23", market_suffix=".TWO")

    assert set(result.keys()) == {"5871"}
    assert call_count["n"] == 2  # 第一輪失敗+第1次重試就成功，不需要用到第2次重試


def test_fetch_prices_batch_retries_exactly_retry_attempts_times_then_gives_up(monkeypatch):
    """驗證重試次數剛好是RETRY_ATTEMPTS次，不多不少：第一輪(1次)+RETRY_ATTEMPTS次重試
    都失敗後，該股票確定不在結果裡，且總呼叫次數等於1+RETRY_ATTEMPTS。"""
    monkeypatch.setattr(yfinance_client, "RETRY_DELAY_SECONDS", 0)
    call_count = {"n": 0}

    def _always_empty(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        call_count["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr("yfinance.download", _always_empty)

    result = yfinance_client.fetch_prices_batch(["9999"], "2026-07-22", "2026-07-23", market_suffix=".TWO")

    assert result == {}
    assert call_count["n"] == 1 + yfinance_client.RETRY_ATTEMPTS


def test_fetch_prices_batch_only_retries_the_stocks_that_failed(monkeypatch):
    """第一輪部分成功、部分失敗時，重試只應該針對失敗的那些股票，已成功的不該被重新請求。"""
    monkeypatch.setattr(yfinance_client, "RETRY_DELAY_SECONDS", 0)
    requested_tickers_per_call = []

    def _fake_download(tickers, start, end, interval, progress, auto_adjust, timeout=10):
        requested_tickers_per_call.append(list(tickers))
        # 只有"5871.TWO"查得到資料，"9999.TWO"永遠查不到
        found = [t for t in tickers if t == "5871.TWO"]
        if not found:
            return pd.DataFrame()
        return _multi_ticker_df(found, ["2026-07-22"])

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_prices_batch(["5871", "9999"], "2026-07-22", "2026-07-23", market_suffix=".TWO")

    assert set(result.keys()) == {"5871"}
    # 第一輪請求兩檔，之後每一輪重試都只請求還沒成功的"9999.TWO"
    assert requested_tickers_per_call[0] == ["5871.TWO", "9999.TWO"]
    for later_call in requested_tickers_per_call[1:]:
        assert later_call == ["9999.TWO"]


def test_yfinance_logger_is_suppressed_to_avoid_raw_error_dump():
    """避免yfinance內部"possibly delisted...(1d 2026-07-27 -> 2026-07-28)"這類原始
    除錯訊息直接洩漏到console，logger等級應該被調到CRITICAL以上(見2026-07-29討論)。"""
    import logging

    assert logging.getLogger("yfinance").level >= logging.CRITICAL
