from src.data import tpex_client


def _fake_response(rows: list[dict]):
    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return rows

    return _FakeResponse()


def test_parse_roc_date():
    assert tpex_client._parse_roc_date("1150803") == "2026-08-03"
    assert tpex_client._parse_roc_date("1150101") == "2026-01-01"


def _raw_row(stock_id: str) -> dict:
    return {
        "Date": "1150803",
        "SecuritiesCompanyCode": stock_id,
        "CompanyName": "測試股",
        "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Buy": "1000",
        " Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell": "500",
        "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "500",
        "Foreign Dealers-Total Buy": "10",
        "Foreign Dealers-TotalSell": "5",
        "ForeignDealers-Difference": "5",
        "ForeignInvestorsIncludeMainlandAreaInvestors-TotalBuy": "1000",
        "ForeignInvestorsIncludeMainlandAreaInvestors-TotalSell": "500",
        "ForeignInvestorsInclude MainlandAreaInvestors-Difference": "500",
        "SecuritiesInvestmentTrustCompanies-TotalBuy": "300",
        "SecuritiesInvestmentTrustCompanies-TotalSell": "100",
        "SecuritiesInvestmentTrustCompanies-Difference": "200",
        "Dealers-TotalBuy": "9000",
        "Dealers-TotalSell": "9137",
        "Dealers-Difference": "-137",
        "Dealers -TotalSell": "137",
        "TotalDifference": "1068",
    }


def test_fetch_institutional_investors_maps_fields_and_avoids_buggy_dealers_key(monkeypatch):
    monkeypatch.setattr(tpex_client.requests, "get", lambda url, timeout: _fake_response([_raw_row("1264")]))

    rows = tpex_client.fetch_institutional_investors()

    by_type = {r["investor_type"]: r for r in rows}
    assert by_type["Foreign_Investor"] == {"stock_id": "1264", "date": "2026-08-03", "investor_type": "Foreign_Investor", "buy": 1000, "sell": 500}
    assert by_type["Foreign_Dealer_Self"] == {"stock_id": "1264", "date": "2026-08-03", "investor_type": "Foreign_Dealer_Self", "buy": 10, "sell": 5}
    assert by_type["Investment_Trust"] == {"stock_id": "1264", "date": "2026-08-03", "investor_type": "Investment_Trust", "buy": 300, "sell": 100}
    # 必須用"Dealers-TotalSell"(9137)而不是有bug的"Dealers -TotalSell"(137，只是避險倉那一小部分)
    assert by_type["Dealer_self"] == {"stock_id": "1264", "date": "2026-08-03", "investor_type": "Dealer_self", "buy": 9000, "sell": 9137}


def test_fetch_institutional_investors_filters_non_4digit_codes(monkeypatch):
    rows_raw = [_raw_row("1264"), _raw_row("00679B"), _raw_row("6488A")]
    monkeypatch.setattr(tpex_client.requests, "get", lambda url, timeout: _fake_response(rows_raw))

    rows = tpex_client.fetch_institutional_investors()

    stock_ids = {r["stock_id"] for r in rows}
    assert stock_ids == {"1264"}
