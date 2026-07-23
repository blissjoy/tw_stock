import collections

import pytest

import src.data.finmind_client as finmind_client


def test_throttle_allows_requests_under_limit_without_sleeping(monkeypatch):
    monkeypatch.setattr(finmind_client, "_request_timestamps", collections.deque())
    monkeypatch.setattr(finmind_client, "MAX_REQUESTS_PER_HOUR", 3)
    fake_time = [1000.0]
    monkeypatch.setattr(finmind_client.time, "monotonic", lambda: fake_time[0])
    sleep_calls = []
    monkeypatch.setattr(finmind_client.time, "sleep", lambda s: sleep_calls.append(s))

    finmind_client._throttle()
    finmind_client._throttle()
    finmind_client._throttle()

    assert sleep_calls == []
    assert len(finmind_client._request_timestamps) == 3


def test_throttle_sleeps_until_oldest_request_ages_out_of_window(monkeypatch):
    """實測發現：超過額度時FinMind直接回傳402、要等整個小時視窗過去才恢復，重試機制救不了，
    所以_throttle()必須在送出第MAX_REQUESTS_PER_HOUR+1次請求前，主動睡到視窗內最舊那筆
    請求age out為止，而不是依賴事後才發現被拒絕。"""
    monkeypatch.setattr(finmind_client, "_request_timestamps", collections.deque())
    monkeypatch.setattr(finmind_client, "MAX_REQUESTS_PER_HOUR", 2)
    fake_time = [1000.0]
    monkeypatch.setattr(finmind_client.time, "monotonic", lambda: fake_time[0])
    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        fake_time[0] += seconds  # 模擬睡眠期間時間真的流逝

    monkeypatch.setattr(finmind_client.time, "sleep", fake_sleep)

    finmind_client._throttle()  # t=1000，第1筆
    fake_time[0] = 1010
    finmind_client._throttle()  # t=1010，第2筆，達到上限但還沒超過，不睡
    assert sleep_calls == []

    fake_time[0] = 1020
    finmind_client._throttle()  # t=1020，第3筆會讓視窗內超過2筆，應該睡到t=1000+3600為止
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(3600 - (1020 - 1000) + 0.1)


def test_throttle_expires_timestamps_older_than_one_hour(monkeypatch):
    monkeypatch.setattr(finmind_client, "_request_timestamps", collections.deque([900.0, 950.0]))
    monkeypatch.setattr(finmind_client, "MAX_REQUESTS_PER_HOUR", 2)
    fake_time = [900.0 + 3601]  # 兩筆舊紀錄都已經超過一小時視窗
    monkeypatch.setattr(finmind_client.time, "monotonic", lambda: fake_time[0])
    sleep_calls = []
    monkeypatch.setattr(finmind_client.time, "sleep", lambda s: sleep_calls.append(s))

    finmind_client._throttle()

    assert sleep_calls == []  # 過期紀錄已被清掉，不會誤判為還在限制內


def test_fetch_stock_info_maps_market(monkeypatch):
    def fake_get(dataset, **kwargs):
        assert dataset == "TaiwanStockInfo"
        return [
            {"stock_id": "2330", "stock_name": "台積電", "type": "twse", "industry_category": "半導體業"},
            {"stock_id": "6488", "stock_name": "環球晶", "type": "tpex", "industry_category": "半導體業"},
        ]

    monkeypatch.setattr(finmind_client, "_get", fake_get)
    rows = finmind_client.fetch_stock_info()
    by_id = {r["stock_id"]: r for r in rows}
    assert by_id["2330"]["market"] == "TWSE"
    assert by_id["6488"]["market"] == "TPEx"
    assert by_id["2330"]["name"] == "台積電"


def test_fetch_stock_prices_maps_fields(monkeypatch):
    def fake_get(dataset, data_id=None, start_date=None, end_date=None, **kwargs):
        assert dataset == "TaiwanStockPrice"
        assert data_id == "6488"
        return [{
            "date": "2025-07-15", "stock_id": "6488", "Trading_Volume": 1805862, "Trading_money": 564751306,
            "open": 311.5, "max": 316.5, "min": 309.5, "close": 310.0, "spread": -1.5, "Trading_turnover": 2065,
        }]

    monkeypatch.setattr(finmind_client, "_get", fake_get)
    rows = finmind_client.fetch_stock_prices("6488", "2025-07-15", "2025-07-15")
    assert len(rows) == 1
    row = rows[0]
    assert row["high"] == 316.5  # FinMind欄位名為max，需轉成high
    assert row["low"] == 309.5   # FinMind欄位名為min，需轉成low
    assert row["volume"] == 1805862


def test_fetch_institutional_investors_maps_name_to_investor_type(monkeypatch):
    def fake_get(dataset, data_id=None, start_date=None, end_date=None, **kwargs):
        return [{"date": "2025-07-15", "stock_id": "2330", "buy": 100, "sell": 50, "name": "Foreign_Investor"}]

    monkeypatch.setattr(finmind_client, "_get", fake_get)
    rows = finmind_client.fetch_institutional_investors("2330", "2025-07-15", "2025-07-15")
    assert rows[0]["investor_type"] == "Foreign_Investor"
    assert rows[0]["buy"] == 100


def test_fetch_margin_trading_maps_all_fields(monkeypatch):
    def fake_get(dataset, data_id=None, start_date=None, end_date=None, **kwargs):
        return [{
            "date": "2025-07-15", "stock_id": "2330",
            "MarginPurchaseBuy": 1288, "MarginPurchaseSell": 501, "MarginPurchaseCashRepayment": 10,
            "MarginPurchaseYesterdayBalance": 16064, "MarginPurchaseTodayBalance": 16841, "MarginPurchaseLimit": 6483153,
            "ShortSaleBuy": 30, "ShortSaleSell": 23, "ShortSaleCashRepayment": 1,
            "ShortSaleYesterdayBalance": 470, "ShortSaleTodayBalance": 462, "ShortSaleLimit": 6483153,
            "OffsetLoanAndShort": 2,
        }]

    monkeypatch.setattr(finmind_client, "_get", fake_get)
    rows = finmind_client.fetch_margin_trading("2330", "2025-07-15", "2025-07-15")
    row = rows[0]
    assert row["margin_purchase_today_balance"] == 16841
    assert row["short_sale_today_balance"] == 462
    assert row["offset_loan_and_short"] == 2


def test_fetch_broker_chips_raises_tier_error_on_insufficient_plan(monkeypatch):
    def fake_get(dataset, data_id=None, extra_params=None, **kwargs):
        raise finmind_client.FinMindTierError(f"{dataset}: Your level is register.")

    monkeypatch.setattr(finmind_client, "_get", fake_get)
    with pytest.raises(finmind_client.FinMindTierError):
        finmind_client.fetch_broker_chips("2330", "2025-07-15")
