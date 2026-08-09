"""手動執行腳本：抓公開資訊觀測站(MOPS)「公司增減資表」(IRB160報表)，存進本機sqlite的
mops_capital_changes表，見src/data/mops_client.py模組docstring的完整背景說明。

刻意不接進scripts/daily_pipeline.py的自動排程——IRB160報表本身是月頻率更新，不需要
跟現有排程一天跑8次；且這是全新、還沒長期驗證過穩定性的爬蟲邏輯(第一次在這個專案裡
用Playwright驅動真實瀏覽器)，先手動執行、確認穩定後再考慮併入自動化。

⚠️ 需要先執行過一次`playwright install chromium`下載瀏覽器執行檔(只`pip install
playwright`不會自動下載)。

用法：
    # 預設抓「最近6個月」(含當月)的上市＋上櫃資料，寫進data/tw_stock.db
    python scripts/fetch_mops_capital_changes.py

    # 指定要往回抓幾個月
    python scripts/fetch_mops_capital_changes.py --months-back 12

    # 只指定單一民國年/月/市場別(不用往回抓的邏輯)
    python scripts/fetch_mops_capital_changes.py --year 115 --month 01 --market sii

    # 指定本機DB路徑(預設data/tw_stock.db，跟daily_pipeline.py的--local-db預設一致)
    python scripts/fetch_mops_capital_changes.py --local-db data/tw_stock_dryrun.db
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import mops_client, storage  # noqa: E402

ROC_YEAR_OFFSET = 1911  # 西元年轉民國年


def _current_roc_year_month() -> tuple[int, int]:
    today = date.today()
    return today.year - ROC_YEAR_OFFSET, today.month


def _trailing_roc_year_months(count: int) -> list[tuple[str, str]]:
    """回傳從「現在」往回數count個月的(民國年, 月份)清單，由舊到新排序
    (例如count=3、現在是115年01月，回傳[("114","11"), ("114","12"), ("115","01")])。"""
    year, month = _current_roc_year_month()
    months: list[tuple[str, str]] = []
    for _ in range(count):
        months.append((str(year), f"{month:02d}"))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    months.reverse()
    return months


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-db", default="data/tw_stock.db", help="本機sqlite檔案路徑（預設data/tw_stock.db）")
    parser.add_argument("--year", default=None, help="民國年，只抓單一年月時用（跟--month搭配，會忽略--months-back）")
    parser.add_argument("--month", default=None, help="月份(2位數)，只抓單一年月時用")
    parser.add_argument("--months-back", type=int, default=6, help="往回抓幾個月(含當月)，預設6個月，指定--year/--month時忽略")
    parser.add_argument(
        "--market", default="both", choices=["sii", "otc", "both"],
        help="sii=上市，otc=上櫃，both=兩者都抓（預設）",
    )
    args = parser.parse_args()

    db_path = Path(args.local_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = storage.init_db(db_path)

    markets = ["sii", "otc"] if args.market == "both" else [args.market]
    year_months = [(args.year, args.month)] if args.year and args.month else _trailing_roc_year_months(args.months_back)

    queries = [(year, month, market) for year, month in year_months for market in markets]
    print(f"準備抓取 {len(year_months)} 個月份 × {len(markets)} 個市場別，共 {len(queries)} 組查詢...")
    results = mops_client.fetch_capital_change_companies_batch(queries)

    fetched_at = datetime.now().isoformat()
    total = 0
    for (year, month, market), companies in results.items():
        year_month = f"{year}{month}"
        if not companies:
            print(f"  {year}年{month}月 市場別={market}：查無資料")
            continue
        storage.upsert_mops_capital_changes(conn, [
            {
                "stock_id": c["stock_id"], "name": c["name"], "market": market,
                "year_month": year_month, "fetched_at": fetched_at,
            }
            for c in companies
        ])
        print(f"  {year}年{month}月 市場別={market}：找到 {len(companies)} 檔公司")
        total += len(companies)

    print(f"\n完成，共寫入 {total} 筆（含重複月份/股票的更新，非唯一股票數）。")


if __name__ == "__main__":
    main()
