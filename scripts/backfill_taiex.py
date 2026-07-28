"""回補大盤(台灣加權股價指數)歷史資料：透過yfinance的`^TWII`代號一次批次下載即可取得
完整歷史OHLCV，不像個股TWSE/TPEx資料需要逐日/逐股回補(`^TWII`本身就是Yahoo Finance
現成的完整歷史時間序列，一次yf.download()呼叫就抓得到，見src/data/yfinance_client.py
的fetch_taiex_prices())。

⚠️ 大盤在`stocks`表裡用market="INDEX"這個特殊值(不是"TWSE"/"TPEx")：`stock_prices.
stock_id`有外鍵參照`stocks(stock_id)`，一定要有一筆對應的stocks資料才能寫入價格；
market="INDEX"是為了讓`src.screener.daily_screener.load_trailing_frames()`(批次選股
邏輯讀取全部stock_id逐一跑個股適用的screen_*規則)能篩掉它，避免大盤被誤判成一檔可以
交易的股票、混進候選清單。

用法：
    python scripts/backfill_taiex.py --db data/tw_stock.db --start 2023-01-01 --end 2026-07-29
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import storage, yfinance_client  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD（exclusive，yfinance慣例，要包含這天要傳隔天）")
    args = parser.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = storage.init_db(db_path)

    rows = yfinance_client.fetch_taiex_prices(args.start, args.end)
    if not rows:
        print("查無資料，未寫入。")
        return
    storage.upsert_stocks(conn, [{
        "stock_id": yfinance_client.TAIEX_STOCK_ID, "name": "台股加權指數", "market": "INDEX",
        "industry": None, "updated_at": datetime.now().isoformat(),
    }])
    storage.upsert_stock_prices(conn, rows)
    conn.close()
    print(f"大盤({yfinance_client.TAIEX_STOCK_ID})回補完成：{len(rows)}筆，日期範圍 {rows[0]['date']} ~ {rows[-1]['date']}")


if __name__ == "__main__":
    main()
