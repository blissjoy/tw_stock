"""回補歷史資料：TWSE(上市)用官方免費批次端點逐日抓（全市場一次抓完，免費無限制），
TPEx(上櫃)透過FinMind逐股抓（成本是「股票數 x 資料集數」，與日期範圍長短基本無關，
見 ai/PLAN.md 資料層章節的估算）。

用法：
    # 小範圍驗證管線：TWSE全市場1週 + TPEx前10檔股票1週
    python scripts/backfill_history.py --db data/tw_stock.db --start 2025-07-01 --end 2025-07-15 --tpex-limit 10

    # 全市場2年正式回補
    python scripts/backfill_history.py --db data/tw_stock.db --start 2023-07-22 --end 2025-07-22

非交易日偵測：不維護獨立的假日曆，直接嘗試抓取，TWSE回應無資料(parse結果為空)就跳過當天，
簡單且不會因為假日曆本身過期而出錯。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import finmind_client, storage, twse_client  # noqa: E402


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:  # 只嘗試週一~週五，週末必為非交易日不浪費請求
            yield d
        d += timedelta(days=1)


def backfill_twse(conn, start: date, end: date, stock_info_by_id: dict[str, dict] | None = None, sleep_sec: float = 0.6) -> dict:
    """全上市市場逐日回補股價/三大法人/融資融券。回傳 {日期字串: 是否有資料} 供驗證用。

    stock_info_by_id: {stock_id: {"name":..., "industry":...}}，來自FinMind TaiwanStockInfo，
    用來讓stocks表存真實公司名稱/產業別（此前的bug：一律用stock_id頂替name）；省略時
    (None)退回用代號本身當name，保留舊行為給不需要名稱的呼叫端使用。
    """
    stock_info_by_id = stock_info_by_id or {}
    results = {}
    for d in _daterange(start, end):
        date_str = d.strftime("%Y%m%d")
        if storage.is_fetched(conn, "TWSE_ALL", "ALL", date_str):
            continue

        prices = twse_client.fetch_stock_prices(date_str)
        if not prices:
            results[date_str] = False
            storage.mark_fetched(conn, "TWSE_ALL", "ALL", date_str, datetime.now().isoformat())
            continue

        institutional = twse_client.fetch_institutional_investors(date_str)
        margin = twse_client.fetch_margin_trading(date_str)

        # 三份報表的股票代號集合不完全相同(例如當天無三大法人進出的股票不會出現在institutional，
        # 但margin_trading/institutional仍可能包含prices沒有的代號)，取聯集才不會違反外鍵約束
        stock_ids = {r["stock_id"] for r in prices} | {r["stock_id"] for r in institutional} | {r["stock_id"] for r in margin}
        storage.upsert_stocks(conn, [
            {
                "stock_id": sid, "name": stock_info_by_id.get(sid, {}).get("name", sid),
                "market": "TWSE", "industry": stock_info_by_id.get(sid, {}).get("industry"),
                "updated_at": datetime.now().isoformat(),
            }
            for sid in stock_ids
        ])
        storage.upsert_stock_prices(conn, prices)
        if institutional:
            storage.upsert_institutional_investors(conn, institutional)
        if margin:
            storage.upsert_margin_trading(conn, margin)

        storage.mark_fetched(conn, "TWSE_ALL", "ALL", date_str, datetime.now().isoformat())
        results[date_str] = True
        print(f"[TWSE] {date_str}: {len(prices)}檔股價, {len(institutional)}筆法人, {len(margin)}筆資券")
        time.sleep(sleep_sec)
    return results


def backfill_tpex(conn, stock_rows: list[dict], start: date, end: date, sleep_sec: float = 0.3) -> dict:
    """透過FinMind逐股回補TPEx股價/三大法人/融資融券。回傳 {股票代號: 是否成功} 供驗證用。

    stock_rows: finmind_client.fetch_stock_info() 篩選出market=="TPEx"的結果，同時提供
    股票清單與真實名稱/產業別（此前的bug：明明已經呼叫過這個API拿到真實名稱，卻只取了
    stock_id，寫入stocks表時又用stock_id頂替了name）。
    """
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    results = {}
    for row in stock_rows:
        stock_id = row["stock_id"]
        try:
            prices = finmind_client.fetch_stock_prices(stock_id, start_str, end_str)
            institutional = finmind_client.fetch_institutional_investors(stock_id, start_str, end_str)
            margin = finmind_client.fetch_margin_trading(stock_id, start_str, end_str)

            storage.upsert_stocks(conn, [{
                "stock_id": stock_id, "name": row.get("name", stock_id), "market": "TPEx",
                "industry": row.get("industry"), "updated_at": datetime.now().isoformat(),
            }])
            if prices:
                storage.upsert_stock_prices(conn, prices)
            if institutional:
                storage.upsert_institutional_investors(conn, institutional)
            if margin:
                storage.upsert_margin_trading(conn, margin)

            results[stock_id] = True
            print(f"[TPEx/FinMind] {stock_id}: {len(prices)}天股價, {len(institutional)}筆法人, {len(margin)}筆資券")
        except Exception as exc:  # noqa: BLE001 - 單檔失敗不應中斷整批回補
            results[stock_id] = False
            print(f"[TPEx/FinMind] {stock_id}: 失敗 - {exc}")
        time.sleep(sleep_sec)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--tpex-limit", type=int, default=None, help="只回補前N檔上櫃股票（驗證管線用，省略則跑全部上櫃股票）")
    parser.add_argument("--skip-tpex", action="store_true", help="只跑TWSE，不透過FinMind抓TPEx")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = storage.init_db(db_path)

    # 只呼叫一次，同時供TWSE(補name/industry)與TPEx(股票清單+name/industry)使用
    stock_info = finmind_client.fetch_stock_info()
    stock_info_by_id = {r["stock_id"]: r for r in stock_info}

    print(f"=== TWSE 回補 {args.start} ~ {args.end} ===")
    twse_results = backfill_twse(conn, start, end, stock_info_by_id=stock_info_by_id)
    print(f"TWSE 完成：{sum(twse_results.values())}個交易日有資料 / 共嘗試{len(twse_results)}天")

    if not args.skip_tpex:
        print("\n=== TPEx 股票清單 (透過FinMind) ===")
        tpex_rows = [r for r in stock_info if r["market"] == "TPEx"]
        if args.tpex_limit:
            tpex_rows = tpex_rows[: args.tpex_limit]
        print(f"共{len(tpex_rows)}檔上櫃股票")

        print(f"\n=== TPEx 回補 {args.start} ~ {args.end} ===")
        tpex_results = backfill_tpex(conn, tpex_rows, start, end)
        print(f"TPEx 完成：{sum(tpex_results.values())}檔成功 / 共{len(tpex_results)}檔")

    conn.close()
    print(f"\n資料庫位置：{db_path}")


if __name__ == "__main__":
    main()
