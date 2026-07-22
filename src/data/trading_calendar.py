"""台股交易日曆：只用來取得TWSE公告的休市日清單，供圖表畫K線圖時的x軸rangebreaks使用
（讓週末/國定假日不要在圖上留白），不是給資料抓取判斷交易日用——那部分已經用「直接嘗試
抓取，TWSE回應空就跳過」的更簡單方式處理（見 scripts/backfill_history.py 的說明）。

fetch/parse 刻意分離，比照 src/data/twse_client.py 的既有慣例：parse_holiday_csv() 是
純函式（輸入已解碼成字串的CSV內容，輸出YYYY-MM-DD清單），可以用手造樣本測試，不需要打網路。

參考 ref-project/tw_stock_analyzer/database/holiday.py 的TWSE holidaySchedule端點與CSV
格式（已用WebFetch查證過：2行標頭+4欄，日期欄格式「M月D日 (星期)」），但這裡改用Python
內建csv模組解析（比手刻逐字元解析器穩健），且只做模組內記憶體快取（假日清單一年才變一次，
不需要額外的檔案快取與過期邏輯）。
"""

from __future__ import annotations

import csv
import io
import re

import requests

HOLIDAY_URL_TEMPLATE = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?date={query_date}&response=csv"

_DATE_PATTERN = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")

_cache: dict[int, list[str]] = {}


def parse_holiday_csv(content: str, year: int) -> list[str]:
    """解析TWSE holidaySchedule的CSV內容，回傳該年份的休市日期清單("YYYY-MM-DD")。
    前2行是標題/欄位名稱，從第3行開始才是資料；日期欄格式「M月D日 (星期)」，只取月/日，
    忽略括號內的星期文字。格式不符或空白的列直接跳過，不拋錯。
    """
    lines = content.splitlines()
    if len(lines) <= 2:
        return []

    holidays: list[str] = []
    reader = csv.reader(lines[2:])
    for row in reader:
        if not row:
            continue
        match = _DATE_PATTERN.search(row[0])
        if not match:
            continue
        month, day = int(match.group(1)), int(match.group(2))
        holidays.append(f"{year}-{month:02d}-{day:02d}")
    return holidays


def fetch_holidays(year: int) -> list[str]:
    """向TWSE查詢指定年份的休市日曆。"""
    url = HOLIDAY_URL_TEMPLATE.format(query_date=f"{year}0101")
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    content = resp.content.decode("big5", errors="ignore")
    return parse_holiday_csv(content, year)


def get_holidays(year: int) -> list[str]:
    """取得指定年份的休市日曆，同一個process內同一年份只會真的打一次API。"""
    if year not in _cache:
        _cache[year] = fetch_holidays(year)
    return _cache[year]


def holidays_between(start_year: int, end_year: int) -> list[str]:
    """回傳跨年份範圍(含起訖年)的休市日清單合併結果，供圖表資料橫跨年份邊界時使用。"""
    holidays: list[str] = []
    for year in range(start_year, end_year + 1):
        holidays.extend(get_holidays(year))
    return holidays
