"""手動執行腳本：抓公開資訊觀測站(MOPS)「公司增減資表」(IRB160報表)，存進本機sqlite的
mops_capital_changes表，見src/data/mops_client.py模組docstring的完整背景說明。

刻意不接進scripts/daily_pipeline.py的自動排程——IRB160報表本身是月頻率更新，不需要
跟現有排程一天跑8次；且這是全新、還沒長期驗證過穩定性的爬蟲邏輯(第一次在這個專案裡
用Playwright驅動真實瀏覽器)，先手動執行、確認穩定後再考慮併入自動化。

⚠️ 需要先執行過一次`playwright install chromium`下載瀏覽器執行檔(只`pip install
playwright`不會自動下載)。

用法：
    # 預設抓「現在」這個民國年/月的上市＋上櫃資料，寫進data/tw_stock.db
    python scripts/fetch_mops_capital_changes.py

    # 指定民國年/月/市場別
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


def _current_roc_year_month() -> tuple[str, str]:
    today = date.today()
    return str(today.year - ROC_YEAR_OFFSET), f"{today.month:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_year, default_month = _current_roc_year_month()
    parser.add_argument("--local-db", default="data/tw_stock.db", help="本機sqlite檔案路徑（預設data/tw_stock.db）")
    parser.add_argument("--year", default=default_year, help=f"民國年，預設現在（{default_year}）")
    parser.add_argument("--month", default=default_month, help=f"月份(2位數)，預設現在（{default_month}）")
    parser.add_argument(
        "--market", default="both", choices=["sii", "otc", "both"],
        help="sii=上市，otc=上櫃，both=兩者都抓（預設）",
    )
    args = parser.parse_args()

    db_path = Path(args.local_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = storage.init_db(db_path)

    markets = ["sii", "otc"] if args.market == "both" else [args.market]
    year_month = f"{args.year}{args.month}"
    fetched_at = datetime.now().isoformat()

    total = 0
    for market in markets:
        print(f"抓取 {args.year}年{args.month}月 市場別={market} ...")
        companies = mops_client.fetch_capital_change_companies(args.year, args.month, market)
        if not companies:
            print(f"  查無資料（該年月可能尚未由MOPS公告，或剛好沒有公司辦理增減資）")
            continue
        storage.upsert_mops_capital_changes(conn, [
            {
                "stock_id": c["stock_id"], "name": c["name"], "market": market,
                "year_month": year_month, "fetched_at": fetched_at,
            }
            for c in companies
        ])
        print(f"  找到 {len(companies)} 檔公司，已寫入 mops_capital_changes")
        total += len(companies)

    print(f"\n完成，共寫入 {total} 筆。")


if __name__ == "__main__":
    main()
