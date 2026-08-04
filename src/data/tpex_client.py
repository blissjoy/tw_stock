"""TPEx（上櫃）官方OpenAPI客戶端：一次抓全市場三大法人買賣超，取代逐檔呼叫FinMind。

⚠️ 這是TPEx **新版**開放資料系統(`www.tpex.org.tw/openapi/...`)，跟`ai/PLAN.md`
記錄過的舊版「after-trading」股價端點(`www.tpex.org.tw/www/zh-tw/afterTrading/...`、
`.../daily_close_quotes/stk_quote_result.php`)是不同系統——舊版被證實`date`參數常被
忽略、回傳錯誤日期的資料，因此股價才改用FinMind再改用yfinance。這個新版端點2026-08-04
已用真實HTTP呼叫+比對本地DB既有FinMind資料驗證過三大法人買賣超欄位的正確性(見下方
`_FIELD_MAP`的欄位選擇說明)，但尚未長期觀察其穩定性，之後若發現資料異常需要重新檢視。

`tpex_3insti_daily_trading`**沒有日期參數**，永遠回傳「目前最新一個交易日」的資料，
不保證等於呼叫當下的日曆日期（例如非交易日呼叫會拿到上一個交易日的資料）——呼叫端
必須用回傳資料裡的`Date`欄位（民國年格式）當作實際資料日期，不能假設是今天。

**欄位對照**（2026-08-04實測`repr()`所有key後確認，這份API的欄位名稱本身就不一致，
同一個數字常常有兩個拼法不同的key重複出現，必須用完全相符的key才安全）：
- 外資：優先用無空白的重複欄位`ForeignInvestorsIncludeMainlandAreaInvestors-TotalBuy`／
  `-TotalSell`（跟另一組`"Foreign Investors include Mainland Area Investors (Foreign
  Dealers excluded)-Total Buy"`/`" Foreign...-Total Sell"`永遠一致，780檔比對0筆不同，
  但後者的Sell欄位key開頭多一個空白字元，用無空白版本比較不容易踩到打字錯誤陷阱）。
- 自營商(自行買賣，不含避險)：`Foreign Dealers-Total Buy`／`Foreign Dealers-TotalSell`。
- 投信：`SecuritiesInvestmentTrustCompanies-TotalBuy`／`-TotalSell`。
- 自營商(合計，含避險)：`Dealers-TotalBuy`／`Dealers-TotalSell`——**注意**API還有一個
  幾乎同名但key多一個空白的`"Dealers -TotalSell"`，這個是有bug的重複欄位，148/780檔
  跟正確版本對不上（比對本地DB確認`"Dealers -TotalSell"`其實只等於避險倉那一小部分，
  不是合計數），絕對不能用。TPEx這個端點沒有像TWSE T86那樣把自營商拆成「自行買賣」跟
  「避險」兩欄，因此`fetch_institutional_investors()`把這個合計數整個寫進我們schema的
  `Dealer_self`，`Dealer_Hedging`對TPEx來源的資料列刻意不寫（呼叫端/查詢端要注意這點，
  跟TWSE來源的資料列規則不同）。
"""

from __future__ import annotations

import re

import requests

BASE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
STOCK_CODE_PATTERN = re.compile(r"^\d{4}$")

_FIELD_MAP = {
    "Foreign_Investor": (
        "ForeignInvestorsIncludeMainlandAreaInvestors-TotalBuy",
        "ForeignInvestorsIncludeMainlandAreaInvestors-TotalSell",
    ),
    "Foreign_Dealer_Self": ("Foreign Dealers-Total Buy", "Foreign Dealers-TotalSell"),
    "Investment_Trust": ("SecuritiesInvestmentTrustCompanies-TotalBuy", "SecuritiesInvestmentTrustCompanies-TotalSell"),
    "Dealer_self": ("Dealers-TotalBuy", "Dealers-TotalSell"),
}


def _parse_roc_date(roc_date: str) -> str:
    """民國年日期字串(例如"1150803")轉西元"YYYY-MM-DD"。"""
    year = int(roc_date[:-4]) + 1911
    month = roc_date[-4:-2]
    day = roc_date[-2:]
    return f"{year}-{month}-{day}"


def fetch_institutional_investors() -> list[dict]:
    """回傳TPEx全市場最新一個交易日的三大法人買賣超，格式對齊`institutional_investors`
    schema(stock_id/date/investor_type/buy/sell)，每檔股票展開成4筆(對應`_FIELD_MAP`的
    4種investor_type)。只保留4碼數字股票代號，過濾掉ETF/債券/基金等非個股列。"""
    resp = requests.get(BASE_URL, timeout=15)
    resp.raise_for_status()
    raw_rows = resp.json()

    rows: list[dict] = []
    for r in raw_rows:
        stock_id = r.get("SecuritiesCompanyCode", "")
        if not STOCK_CODE_PATTERN.match(stock_id):
            continue
        date = _parse_roc_date(r["Date"])
        for investor_type, (buy_key, sell_key) in _FIELD_MAP.items():
            rows.append({
                "stock_id": stock_id,
                "date": date,
                "investor_type": investor_type,
                "buy": int(r[buy_key]),
                "sell": int(r[sell_key]),
            })
    return rows
