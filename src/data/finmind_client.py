"""FinMind API客戶端：上櫃(TPEx)股價/三大法人/融資融券（TWSE官方歷史端點對上櫃不穩定，改用此）、
股票基本資料(涵蓋上市+上櫃)、券商分點參考資料、以及分點進出籌碼。

**重要**：已用真實token實測確認，`TaiwanStockTradingDailyReport`（分點進出籌碼本體）需要
FinMind「Sponsor」付費方案，免費/註冊會員呼叫會收到 status=400 "Your level is register.
Please update your user level."；`TaiwanSecuritiesTraderInfo`（分點基本資料，非交易明細）
則不需要，免費可用。`fetch_broker_chips()` 已寫好、格式已備妥，帳號升級後即可直接使用，
現階段呼叫會拋出 FinMindTierError 讓呼叫端明確知道是權限問題而不是程式或網路錯誤。

免費/註冊帳號額度：未帶token 300次/小時，帶token 600次/小時（見 config.get_finmind_token）。
"""

from __future__ import annotations

import time

import requests

from src.data.config import get_finmind_token

BASE_URL = "https://api.finmindtrade.com/api/v4/data"


class FinMindTierError(RuntimeError):
    """帳號方案不足以呼叫該dataset（例如分點進出籌碼需要Sponsor付費方案）。"""


def _get(dataset: str, data_id: str | None = None, start_date: str | None = None,
         end_date: str | None = None, extra_params: dict | None = None, retries: int = 3) -> list[dict]:
    params: dict = {"dataset": dataset, "token": get_finmind_token()}
    if data_id is not None:
        params["data_id"] = data_id
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    if extra_params:
        params.update(extra_params)

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") == 400 and "level" in str(payload.get("msg", "")).lower():
                raise FinMindTierError(f"{dataset}: {payload.get('msg')}")
            if payload.get("status") != 200:
                raise RuntimeError(f"FinMind API錯誤 (dataset={dataset}): {payload.get('msg')}")
            return payload.get("data", [])
        except FinMindTierError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"FinMind API 連續{retries}次請求失敗：dataset={dataset}") from last_error


def fetch_stock_info() -> list[dict]:
    """股票基本資料，涵蓋上市(twse)+上櫃(tpex)，用於填 stocks 參考表。"""
    raw_rows = _get("TaiwanStockInfo")
    return [
        {
            "stock_id": r["stock_id"],
            "name": r["stock_name"],
            "market": "TWSE" if r.get("type") == "twse" else "TPEx",
            "industry": r.get("industry_category"),
        }
        for r in raw_rows
    ]


def fetch_stock_prices(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    """date格式為 'YYYY-MM-DD'（與TWSE官方端點的西元年YYYYMMDD不同，注意轉換）。"""
    raw_rows = _get("TaiwanStockPrice", data_id=stock_id, start_date=start_date, end_date=end_date)
    return [
        {
            "stock_id": r["stock_id"],
            "date": r["date"],
            "open": r["open"],
            "high": r["max"],
            "low": r["min"],
            "close": r["close"],
            "volume": r["Trading_Volume"],
            "trading_money": r.get("Trading_money"),
            "trading_turnover": r.get("Trading_turnover"),
            "spread": r.get("spread"),
        }
        for r in raw_rows
    ]


def fetch_institutional_investors(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    raw_rows = _get("TaiwanStockInstitutionalInvestorsBuySell", data_id=stock_id, start_date=start_date, end_date=end_date)
    return [
        {"stock_id": r["stock_id"], "date": r["date"], "investor_type": r["name"], "buy": r["buy"], "sell": r["sell"]}
        for r in raw_rows
    ]


def fetch_margin_trading(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    raw_rows = _get("TaiwanStockMarginPurchaseShortSale", data_id=stock_id, start_date=start_date, end_date=end_date)
    return [
        {
            "stock_id": r["stock_id"],
            "date": r["date"],
            "margin_purchase_buy": r.get("MarginPurchaseBuy"),
            "margin_purchase_sell": r.get("MarginPurchaseSell"),
            "margin_purchase_cash_repayment": r.get("MarginPurchaseCashRepayment"),
            "margin_purchase_yesterday_balance": r.get("MarginPurchaseYesterdayBalance"),
            "margin_purchase_today_balance": r.get("MarginPurchaseTodayBalance"),
            "margin_purchase_limit": r.get("MarginPurchaseLimit"),
            "short_sale_buy": r.get("ShortSaleBuy"),
            "short_sale_sell": r.get("ShortSaleSell"),
            "short_sale_cash_repayment": r.get("ShortSaleCashRepayment"),
            "short_sale_yesterday_balance": r.get("ShortSaleYesterdayBalance"),
            "short_sale_today_balance": r.get("ShortSaleTodayBalance"),
            "short_sale_limit": r.get("ShortSaleLimit"),
            "offset_loan_and_short": r.get("OffsetLoanAndShort"),
        }
        for r in raw_rows
    ]


def fetch_securities_traders() -> list[dict]:
    """券商分點基本資料（免費可用，僅是名稱/地址對照表，不含交易明細）。"""
    raw_rows = _get("TaiwanSecuritiesTraderInfo")
    return [
        {
            "securities_trader_id": r["securities_trader_id"],
            "securities_trader": r["securities_trader"],
            "address": r.get("address"),
            "phone": r.get("phone"),
        }
        for r in raw_rows
    ]


def fetch_broker_chips(stock_id: str, date: str) -> list[dict]:
    """分點進出籌碼（單日查詢，不支援日期範圍）。需要Sponsor付費方案，免費帳號會拋出FinMindTierError。"""
    raw_rows = _get("TaiwanStockTradingDailyReport", data_id=stock_id, extra_params={"date": date})
    return [
        {
            "stock_id": r["stock_id"],
            "date": r["date"],
            "securities_trader_id": r["securities_trader_id"],
            "price": r["price"],
            "buy": r["buy"],
            "sell": r["sell"],
        }
        for r in raw_rows
    ]
