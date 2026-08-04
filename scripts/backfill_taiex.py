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
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import storage, twse_client, yfinance_client  # noqa: E402


def _weekdays_in_range(start_date: str, end_date: str) -> list[str]:
    """跟scripts/backfill_history.py的_daterange()同一種「只挑週一~週五」近似，不維護
    獨立假日曆——用來判斷force_overwrite=False時該區間是否已經完全有資料。"""
    d = _date.fromisoformat(start_date)
    end = _date.fromisoformat(end_date)
    out = []
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def backfill_taiex_range(conn, start_date: str, end_date: str, force_overwrite: bool = False) -> list[str]:
    """回補大盤(^TWII)歷史OHLCV，OHLC來自yfinance、成交量改用TWSE官方FMTQIK(twse_client.
    fetch_taiex_volume_range()，跟scripts/backfill_taiex_volume.py共用同一個函式，理由見
    該腳本docstring：yfinance的volume單位跟TWSE官方量級不一致)。

    force_overwrite=False(預設)時，先確認[start_date, end_date]裡的交易日(週一~週五近似)
    是否已經全部在stock_prices裡有資料，若是則完全不呼叫API、回傳空list；True時忽略既有
    資料，整段重新抓取覆蓋。回傳實際寫入的日期字串清單。
    """
    if not force_overwrite:
        expected = _weekdays_in_range(start_date, end_date)
        existing_rows = conn.execute(
            "SELECT date FROM stock_prices WHERE stock_id = ? AND date BETWEEN ? AND ?",
            (yfinance_client.TAIEX_STOCK_ID, start_date, end_date),
        ).fetchall()
        existing = {r[0] for r in existing_rows}
        if all(d in existing for d in expected):
            return []

    end_exclusive = (_date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
    rows = yfinance_client.fetch_taiex_prices(start_date, end_exclusive)
    if not rows:
        return []

    volume_by_date = twse_client.fetch_taiex_volume_range(start_date, end_date)
    for row in rows:
        if row["date"] in volume_by_date:
            row["volume"] = volume_by_date[row["date"]]

    if not force_overwrite:
        existing_rows = conn.execute(
            "SELECT date FROM stock_prices WHERE stock_id = ? AND date BETWEEN ? AND ?",
            (yfinance_client.TAIEX_STOCK_ID, start_date, end_date),
        ).fetchall()
        existing = {r[0] for r in existing_rows}
        rows = [r for r in rows if r["date"] not in existing]
        if not rows:
            return []

    storage.upsert_stocks(conn, [{
        "stock_id": yfinance_client.TAIEX_STOCK_ID, "name": "台股加權指數", "market": "INDEX",
        "industry": None, "updated_at": datetime.now().isoformat(),
    }])
    storage.upsert_stock_prices(conn, rows)
    return [r["date"] for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD（inclusive；內部會自動處理yfinance的exclusive慣例）")
    parser.add_argument("--force-overwrite", action="store_true", help="忽略既有資料，整段重新抓取覆蓋")
    args = parser.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = storage.init_db(db_path)

    written = backfill_taiex_range(conn, args.start, args.end, force_overwrite=args.force_overwrite)
    conn.close()
    if not written:
        print("查無新資料，未寫入(或該區間已經有完整資料，force_overwrite未開啟)。")
        return
    print(f"大盤({yfinance_client.TAIEX_STOCK_ID})回補完成：{len(written)}筆，日期範圍 {written[0]} ~ {written[-1]}")


if __name__ == "__main__":
    main()
