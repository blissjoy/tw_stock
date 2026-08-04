"""TWSE(上市)官方開放資料客戶端：股價/三大法人/融資融券，皆為免費、不需token的公開端點。

三個端點都是「單日全市場」批次端點，一次抓到當天所有股票的資料，比逐檔查詢有效率很多。
TPEx(上櫃)的對應歷史端點經實測不可靠（date參數常被忽略，回傳與請求日期不符的資料，見
ai/PLAN.md資料層章節的測試記錄），上櫃改用FinMind取得，見 finmind_client.py。

fetch/parse 刻意分離：parse_* 是純函式（輸入TWSE回應的dict，輸出符合storage.upsert_*格式
的list[dict]），可以用手造/擷取的樣本JSON做單元測試，不需要真的打網路；fetch_* 才負責HTTP。
"""

from __future__ import annotations

import re
import time
from datetime import date as _date

import requests

STOCK_PRICE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
INSTITUTIONAL_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
# 大盤統計資訊(FMTQIK)：官方逐日公布的大盤成交股數/成交金額/發行量加權股價指數，
# 2026-08-04新增，取代yfinance的^TWII volume欄位——見scripts/daily_pipeline.py
# fetch_today_taiex()的說明，Yahoo的大盤成交量欄位長期不可靠。
TAIEX_VOLUME_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"

# 只收4碼數字股票代號，排除ETF(5~6碼)、權證(6碼)、債券等其他證券類型
STOCK_CODE_PATTERN = re.compile(r"^\d{4}$")


def _to_number(text: str) -> float:
    """TWSE回傳的數字欄位是含千分位逗號的字串，也可能是空字串或'--'代表無資料。"""
    text = text.strip().replace(",", "")
    if text in ("", "--", "---"):
        return 0.0
    return float(text)


def _to_int(text: str) -> int:
    return int(_to_number(text))


def format_date(date_str: str) -> str:
    """回應中的 date 欄位其實已經是西元年YYYYMMDD（頁面上的「114年07月15日」只是給人看的標題文字，
    容易誤判成民國年格式——這裡只是單純插入分隔線變成 YYYY-MM-DD，不做任何年份換算。"""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"


def parse_stock_prices(raw: dict) -> list[dict]:
    """解析 MI_INDEX 回應。非交易日(stat!='OK')或找不到明細表時回傳[]，不拋錯（讓呼叫端自行判斷跳過）。"""
    if raw.get("stat") != "OK":
        return []
    quote_table = next((t for t in raw.get("tables", []) if t.get("title") and "每日收盤行情" in t["title"]), None)
    if quote_table is None:
        return []
    date = format_date(raw["date"])

    rows = []
    for row in quote_table["data"]:
        code = row[0].strip()
        if not STOCK_CODE_PATTERN.match(code):
            continue
        close_text = row[8].strip().replace(",", "")
        if close_text in ("", "--", "---"):
            # 當日該股票無成交（收盤欄位為佔位符），不是「收盤價=0」，跳過整列避免下游報酬率計算除以0
            continue
        rows.append({
            "stock_id": code,
            "date": date,
            "open": _to_number(row[5]),
            "high": _to_number(row[6]),
            "low": _to_number(row[7]),
            "close": _to_number(row[8]),
            "volume": _to_int(row[2]),
            "trading_money": _to_int(row[4]),
            "trading_turnover": _to_int(row[3]),
            "spread": _to_number(row[10]),
        })
    return rows


# T86 逐欄對應：(investor_type, 買進欄位index, 賣出欄位index)，直接對應T86原始5類法人分項，
# 與FinMind TaiwanStockInstitutionalInvestorsBuySell的investor_type命名一致（已用真實API驗證）
_INSTITUTIONAL_COLUMN_MAP = [
    ("Foreign_Investor", 2, 3),
    ("Foreign_Dealer_Self", 5, 6),
    ("Investment_Trust", 8, 9),
    ("Dealer_self", 12, 13),
    ("Dealer_Hedging", 15, 16),
]


def parse_institutional_investors(raw: dict) -> list[dict]:
    """解析 T86 回應。"""
    if raw.get("stat") != "OK":
        return []
    date = format_date(raw["date"])
    rows = []
    for row in raw.get("data", []):
        code = row[0].strip()
        if not STOCK_CODE_PATTERN.match(code):
            continue
        for investor_type, buy_idx, sell_idx in _INSTITUTIONAL_COLUMN_MAP:
            rows.append({
                "stock_id": code,
                "date": date,
                "investor_type": investor_type,
                "buy": _to_int(row[buy_idx]),
                "sell": _to_int(row[sell_idx]),
            })
    return rows


def _parse_roc_slash_date(roc_date: str) -> str:
    """民國年日期字串(例如'115/08/03')轉西元'YYYY-MM-DD'。FMTQIK用斜線分隔的民國年
    格式，跟fetch_stock_prices()等其他端點吃的西元年YYYYMMDD不同，不要搞混。"""
    year_str, month, day = roc_date.split("/")
    return f"{int(year_str) + 1911}-{month}-{day}"


def parse_taiex_volume(raw: dict) -> dict[str, int]:
    """解析FMTQIK(大盤統計資訊)回應，回傳{date('YYYY-MM-DD'): 成交股數}對照表。這個
    端點以「月」為單位回傳整月資料(date參數指定要查哪個月)，非交易日(stat!='OK'，
    例如查詢還沒有任何資料的未來月份)回傳空dict，不拋錯。"""
    if raw.get("stat") != "OK":
        return {}
    return {_parse_roc_slash_date(row[0].strip()): _to_int(row[1]) for row in raw.get("data", [])}


def parse_margin_trading(raw: dict) -> list[dict]:
    """解析 MI_MARGN 回應（取「融資融券彙總」逐股明細表，不取開頭的全市場統計表）。"""
    if raw.get("stat") != "OK":
        return []
    table = next((t for t in raw.get("tables", []) if t.get("title") and "融資融券彙總" in t["title"]), None)
    if table is None:
        return []
    date = format_date(raw["date"])

    rows = []
    for row in table["data"]:
        code = row[0].strip()
        if not STOCK_CODE_PATTERN.match(code):
            continue
        rows.append({
            "stock_id": code,
            "date": date,
            "margin_purchase_buy": _to_int(row[2]),
            "margin_purchase_sell": _to_int(row[3]),
            "margin_purchase_cash_repayment": _to_int(row[4]),
            "margin_purchase_yesterday_balance": _to_int(row[5]),
            "margin_purchase_today_balance": _to_int(row[6]),
            "margin_purchase_limit": _to_int(row[7]),
            "short_sale_buy": _to_int(row[8]),
            "short_sale_sell": _to_int(row[9]),
            "short_sale_cash_repayment": _to_int(row[10]),
            "short_sale_yesterday_balance": _to_int(row[11]),
            "short_sale_today_balance": _to_int(row[12]),
            "short_sale_limit": _to_int(row[13]),
            "offset_loan_and_short": _to_int(row[14]),
        })
    return rows


def _get_json(url: str, params: dict, retries: int = 3, timeout: int = 15) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"TWSE API 連續{retries}次請求失敗：{url}") from last_error


def fetch_stock_prices(date: str) -> list[dict]:
    """date: 'YYYYMMDD' 西元年格式（TWSE官方端點吃西元年，不是民國年，容易搞混）。"""
    raw = _get_json(STOCK_PRICE_URL, {"date": date, "type": "ALL", "response": "json"})
    return parse_stock_prices(raw)


def fetch_institutional_investors(date: str) -> list[dict]:
    raw = _get_json(INSTITUTIONAL_URL, {"date": date, "selectType": "ALL", "response": "json"})
    return parse_institutional_investors(raw)


def fetch_margin_trading(date: str) -> list[dict]:
    raw = _get_json(MARGIN_URL, {"date": date, "selectType": "ALL", "response": "json"})
    return parse_margin_trading(raw)


def fetch_taiex_volume(date: str) -> dict[str, int]:
    """date: 'YYYYMMDD'西元年格式，只有年月部分有意義(TWSE用來決定回傳哪個月，日期
    數字本身被忽略、回傳整個月份的資料)。查無資料(例如還沒收盤公布)回傳空dict。"""
    raw = _get_json(TAIEX_VOLUME_URL, {"date": date, "response": "json"})
    return parse_taiex_volume(raw)


def _month_starts(start_date: str, end_date: str) -> list[str]:
    """回傳start_date~end_date(含)涵蓋到的每個月份第一天，格式'YYYYMM01'(西元年)，
    供fetch_taiex_volume_range()逐月查詢FMTQIK用(這個端點以「月」為單位回傳整月
    資料)。"""
    cur = _date.fromisoformat(start_date).replace(day=1)
    end = _date.fromisoformat(end_date).replace(day=1)
    months = []
    while cur <= end:
        months.append(cur.strftime("%Y%m01"))
        cur = _date(cur.year + 1, 1, 1) if cur.month == 12 else _date(cur.year, cur.month + 1, 1)
    return months


def fetch_taiex_volume_range(start_date: str, end_date: str) -> dict[str, int]:
    """呼叫FMTQIK，合併start_date~end_date(含，'YYYY-MM-DD'西元年格式)涵蓋到的每個
    月份，回傳{date: 成交股數}對照表。2026-08-04新增，從scripts/daily_pipeline.py
    的fetch_today_taiex()抽出來(原本是該檔案內部的私有函式)，讓一次性回補整段歷史
    volume的`scripts/backfill_taiex_volume.py`也能重用同一份邏輯，不用複製貼上。"""
    result: dict[str, int] = {}
    for month_str in _month_starts(start_date, end_date):
        result.update(fetch_taiex_volume(month_str))
    return result
