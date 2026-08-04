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
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import finmind_client, storage, twse_client  # noqa: E402

# backfill_tpex()的_covers_range()只允許查這幾張表，避免table名稱被誤傳成其他來源
# (目前呼叫端都是寫死的字面值，不是外部輸入，但白名單能防呆)
_COVERAGE_TABLES = {"stock_prices", "institutional_investors", "margin_trading"}


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:  # 只嘗試週一~週五，週末必為非交易日不浪費請求
            yield d
        d += timedelta(days=1)


def _covers_range(conn, table: str, stock_id: str, start_str: str, end_str: str) -> bool:
    """回補資料桌面分頁的「強制覆蓋」關閉時，用這個檢查該股票在該區間的table裡是不是
    已經有任何一筆資料——有就跳過FinMind呼叫，省API配額。這是「存在性」檢查，不是逐日
    精確缺口掃描(精確版見scripts/backfill_tpex_institutional_gaps.py的find_gapped_
    stocks())，區間內只要缺一部份也會被判定為「已覆蓋」而跳過，足夠應付「DB有資料就不
    重抓」的一般使用情境，但不保證抓到部分缺漏。
    """
    if table not in _COVERAGE_TABLES:
        raise ValueError(f"不支援的table: {table}")
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE stock_id = ? AND date BETWEEN ? AND ? LIMIT 1",
        (stock_id, start_str, end_str),
    ).fetchone()
    return row is not None


def backfill_twse(
    conn, start: date, end: date, stock_info_by_id: dict[str, dict] | None = None, sleep_sec: float = 0.6,
    include_price: bool = True, include_institutional: bool = True, include_margin: bool = True,
    force_overwrite: bool = False, stock_id_filter: set[str] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """全上市市場逐日回補股價/三大法人/融資融券。回傳 {日期字串: 是否有資料} 供驗證用。

    stock_info_by_id: {stock_id: {"name":..., "industry":...}}，來自FinMind TaiwanStockInfo，
    用來讓stocks表存真實公司名稱/產業別（此前的bug：一律用stock_id頂替name）；省略時
    (None)退回用代號本身當name，保留舊行為給不需要名稱的呼叫端使用。

    include_price/include_institutional/include_margin：個別控制是否要把對應資料寫入DB
    (官方批次端點本來就是「一次抓全市場」，不管勾選哪個都還是會呼叫fetch_stock_prices()
    確認當天是否為交易日，差別只在於要不要另外呼叫法人/資券端點、要不要真的upsert)。

    force_overwrite=False(預設)時沿用既有is_fetched()跳過邏輯；True時忽略該檢查、永遠重抓。

    stock_id_filter：對應桌面版「指定股票代號」情境，只把抓回來的資料篩到這個集合內才寫入；
    None(預設，全市場情境)不篩選。

    on_progress(done, total)：每處理完一個交易日呼叫一次，供GUI顯示進度用。
    """
    stock_info_by_id = stock_info_by_id or {}
    results = {}
    dates = list(_daterange(start, end))
    for i, d in enumerate(dates, 1):
        date_str = d.strftime("%Y%m%d")
        if not force_overwrite and storage.is_fetched(conn, "TWSE_ALL", "ALL", date_str):
            if on_progress is not None:
                on_progress(i, len(dates))
            continue

        prices = twse_client.fetch_stock_prices(date_str)
        if not prices:
            results[date_str] = False
            storage.mark_fetched(conn, "TWSE_ALL", "ALL", date_str, datetime.now().isoformat())
            if on_progress is not None:
                on_progress(i, len(dates))
            continue

        institutional = twse_client.fetch_institutional_investors(date_str) if include_institutional else []
        margin = twse_client.fetch_margin_trading(date_str) if include_margin else []

        if stock_id_filter is not None:
            prices = [r for r in prices if r["stock_id"] in stock_id_filter]
            institutional = [r for r in institutional if r["stock_id"] in stock_id_filter]
            margin = [r for r in margin if r["stock_id"] in stock_id_filter]

        # 三份報表的股票代號集合不完全相同(例如當天無三大法人進出的股票不會出現在institutional，
        # 但margin_trading/institutional仍可能包含prices沒有的代號)，取聯集才不會違反外鍵約束
        stock_ids = {r["stock_id"] for r in prices} | {r["stock_id"] for r in institutional} | {r["stock_id"] for r in margin}
        if stock_ids:
            storage.upsert_stocks(conn, [
                {
                    "stock_id": sid, "name": stock_info_by_id.get(sid, {}).get("name", sid),
                    "market": "TWSE", "industry": stock_info_by_id.get(sid, {}).get("industry"),
                    "updated_at": datetime.now().isoformat(),
                }
                for sid in stock_ids
            ])
        if include_price and prices:
            storage.upsert_stock_prices(conn, prices)
        if institutional:
            storage.upsert_institutional_investors(conn, institutional)
        if margin:
            storage.upsert_margin_trading(conn, margin)

        storage.mark_fetched(conn, "TWSE_ALL", "ALL", date_str, datetime.now().isoformat())
        results[date_str] = True
        print(f"[TWSE] {date_str}: {len(prices)}檔股價, {len(institutional)}筆法人, {len(margin)}筆資券")
        if on_progress is not None:
            on_progress(i, len(dates))
        time.sleep(sleep_sec)
    return results


def backfill_tpex(
    conn, stock_rows: list[dict], start: date, end: date, sleep_sec: float = 0.3,
    include_price: bool = True, include_institutional: bool = True, include_margin: bool = True,
    force_overwrite: bool = False, on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """透過FinMind逐股回補TPEx股價/三大法人/融資融券。回傳 {股票代號: 是否成功} 供驗證用。

    stock_rows: 每筆需含stock_id，name/industry/market選填(供寫入stocks表用)——呼叫端可以
    是finmind_client.fetch_stock_info()篩出market=="TPEx"的結果，也可以是本機stocks表現有
    資料(回補資料桌面分頁走這條路，省一次FinMind stock_info請求)。

    include_price/include_institutional/include_margin：個別控制要不要呼叫對應FinMind端點。
    force_overwrite=False(預設)時，每個include_*為真的項目都先用_covers_range()檢查DB是否
    已有該股票在該區間的任何資料，有的話跳過該項目的FinMind呼叫；True時忽略檢查、永遠呼叫。
    """
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    results = {}
    total = len(stock_rows)
    for i, row in enumerate(stock_rows, 1):
        stock_id = row["stock_id"]
        try:
            need_price = include_price and (force_overwrite or not _covers_range(conn, "stock_prices", stock_id, start_str, end_str))
            need_institutional = include_institutional and (
                force_overwrite or not _covers_range(conn, "institutional_investors", stock_id, start_str, end_str)
            )
            need_margin = include_margin and (force_overwrite or not _covers_range(conn, "margin_trading", stock_id, start_str, end_str))

            prices = finmind_client.fetch_stock_prices(stock_id, start_str, end_str) if need_price else []
            institutional = finmind_client.fetch_institutional_investors(stock_id, start_str, end_str) if need_institutional else []
            margin = finmind_client.fetch_margin_trading(stock_id, start_str, end_str) if need_margin else []

            storage.upsert_stocks(conn, [{
                "stock_id": stock_id, "name": row.get("name", stock_id), "market": row.get("market", "TPEx"),
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
        if on_progress is not None:
            on_progress(i, total)
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
