import pytest

import src.data.finmind_client as finmind_client


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
