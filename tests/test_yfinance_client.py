import pandas as pd

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

    def _fake_download(tickers, start, end, interval, progress, auto_adjust):
        assert tickers == ["2330.TW", "5871.TW"]
        return df_batch

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_prices_batch(["2330", "5871"], "2026-07-22", "2026-07-23", market_suffix=".TW")

    assert set(result.keys()) == {"2330", "5871"}
    assert result["2330"][0]["open"] == 100.0
    assert result["5871"][0]["open"] == 110.0  # _multi_ticker_df讓第2檔的base價位高10


def test_fetch_tpex_prices_batch_uses_two_suffix(monkeypatch):
    captured = {}

    def _fake_download(tickers, start, end, interval, progress, auto_adjust):
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
    df_batch = _multi_ticker_df(["5871.TWO"], ["2026-07-22"])

    def _fake_download(tickers, start, end, interval, progress, auto_adjust):
        return df_batch

    monkeypatch.setattr("yfinance.download", _fake_download)

    # 要求2檔，但yf.download只回傳其中1檔有資料(模擬另一檔下市/查無資料的情況)
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

    def _fake_download(tickers, start, end, interval, progress, auto_adjust):
        return df_batch

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_prices_batch(["5871"], "2026-07-22", "2026-07-24", market_suffix=".TWO")

    assert len(result["5871"]) == 1  # 第二天Close是NaN，應該被濾掉
    assert result["5871"][0]["date"] == "2026-07-22"


def test_fetch_prices_batch_handles_single_ticker_non_multiindex(monkeypatch):
    def _fake_download(tickers, start, end, interval, progress, auto_adjust):
        return _single_ticker_df(["2026-07-22"])

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_prices_batch(["5871"], "2026-07-22", "2026-07-23", market_suffix=".TWO")

    assert result["5871"][0]["close"] == 102.0


def test_fetch_prices_batch_returns_empty_dict_when_download_returns_empty(monkeypatch):
    monkeypatch.setattr("yfinance.download", lambda *a, **k: pd.DataFrame())

    result = yfinance_client.fetch_prices_batch(["5871"], "2026-07-22", "2026-07-23", market_suffix=".TWO")

    assert result == {}


def test_fetch_twse_prices_batch_uses_tw_suffix(monkeypatch):
    captured = {}

    def _fake_download(tickers, start, end, interval, progress, auto_adjust):
        captured["tickers"] = tickers
        return _multi_ticker_df(tickers, ["2026-07-24"])

    monkeypatch.setattr("yfinance.download", _fake_download)

    result = yfinance_client.fetch_twse_prices_batch(["2330", "1101"], "2026-07-24", "2026-07-25")

    assert captured["tickers"] == ["2330.TW", "1101.TW"]
    assert set(result.keys()) == {"2330", "1101"}


def test_fetch_prices_batch_reports_progress_after_each_batch(monkeypatch):
    monkeypatch.setattr(yfinance_client, "BATCH_SIZE", 2)  # 縮小批次大小方便測試多批次的情境

    def _fake_download(tickers, start, end, interval, progress, auto_adjust):
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
