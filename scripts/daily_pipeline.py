"""每日自動化 pipeline 進入點：GitHub Actions 排程呼叫這支腳本。

流程：連線 Turso → 抓「今天」的 TWSE 全市場批次 + TPEx(透過FinMind逐股) 增量資料 → 寫入 →
跑 daily_screener 算候選清單 → 寫入 daily_candidates 表 → 發送 LINE + Email 通知。

TPEx 透過 FinMind 逐股抓取的成本是「股票數 x 資料集數」，跟日期範圍長短無關（FinMind
一次請求拿一檔股票的一段區間），就算只抓「今天」一天，全部上櫃股票仍需要約2400次請求，
在600次/小時免費額度下約需4小時，這是每日自動化排程本身要跑好幾小時的原因（見 ai/PLAN.md）。

--local-db 參數可以指向本機sqlite檔案而不連線Turso，方便在還沒申請Turso帳號、或想在
本機快速驗證整條管線邏輯時使用。

用法：
    # 正式：連線Turso（需先在.env設定TURSO_DATABASE_URL/TURSO_AUTH_TOKEN）
    python scripts/daily_pipeline.py

    # 本機測試：不連Turso，改用本機sqlite檔案，且不真的發通知、跳過耗時的TPEx更新
    python scripts/daily_pipeline.py --local-db data/tw_stock_dryrun.db --dry-run --skip-tpex
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import finmind_client, storage, twse_client  # noqa: E402
from src.notify.email_notify import format_candidates_email_body, send_email  # noqa: E402
from src.notify.line_notify import format_candidates_message, send_line_broadcast  # noqa: E402
from src.screener.daily_screener import run_screen_and_store  # noqa: E402


def fetch_today_twse(conn, date_str: str) -> bool:
    """抓TWSE當天全市場批次資料並寫入conn，回傳是否有資料(True代表是交易日)。"""
    prices = twse_client.fetch_stock_prices(date_str)
    if not prices:
        return False
    institutional = twse_client.fetch_institutional_investors(date_str)
    margin = twse_client.fetch_margin_trading(date_str)

    # 三份報表的股票代號集合不完全相同，取聯集才不會在寫margin/institutional時違反外鍵約束
    stock_ids = {r["stock_id"] for r in prices} | {r["stock_id"] for r in institutional} | {r["stock_id"] for r in margin}
    storage.upsert_stocks(conn, [
        {"stock_id": sid, "name": sid, "market": "TWSE", "industry": None, "updated_at": datetime.now().isoformat()}
        for sid in stock_ids
    ])
    storage.upsert_stock_prices(conn, prices)
    if institutional:
        storage.upsert_institutional_investors(conn, institutional)
    if margin:
        storage.upsert_margin_trading(conn, margin)
    return True


def fetch_today_tpex(conn, date_str: str) -> int:
    """透過FinMind抓TPEx全部上櫃股票當天增量資料，回傳成功更新的股票數。單檔失敗不中斷整批。"""
    stock_info = finmind_client.fetch_stock_info()
    tpex_ids = [r["stock_id"] for r in stock_info if r["market"] == "TPEx"]
    success_count = 0
    for stock_id in tpex_ids:
        try:
            prices = finmind_client.fetch_stock_prices(stock_id, date_str, date_str)
            institutional = finmind_client.fetch_institutional_investors(stock_id, date_str, date_str)
            margin = finmind_client.fetch_margin_trading(stock_id, date_str, date_str)

            storage.upsert_stocks(conn, [
                {"stock_id": stock_id, "name": stock_id, "market": "TPEx", "industry": None, "updated_at": datetime.now().isoformat()}
            ])
            if prices:
                storage.upsert_stock_prices(conn, prices)
            if institutional:
                storage.upsert_institutional_investors(conn, institutional)
            if margin:
                storage.upsert_margin_trading(conn, margin)
            success_count += 1
        except Exception as exc:  # noqa: BLE001 - 單檔失敗不應中斷整批更新
            print(f"[TPEx/FinMind] {stock_id}: 失敗 - {exc}")
    return success_count


def run_daily_pipeline(
    conn, date_str: str | None = None, min_days: int = 60, dry_run: bool = False, skip_tpex: bool = False,
) -> list[dict]:
    """核心orchestration，刻意與「conn是Turso還是本機sqlite」無關，方便測試與dry-run重用。"""
    storage.ensure_schema(conn)

    if date_str is None:
        date_str = date.today().strftime("%Y%m%d")
    iso_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"

    is_trading_day = fetch_today_twse(conn, date_str)
    if not is_trading_day:
        print(f"{iso_date} TWSE無資料，判定為非交易日，跳過選股與通知。")
        return []

    if not skip_tpex:
        tpex_count = fetch_today_tpex(conn, date_str)
        print(f"TPEx：{tpex_count} 檔成功更新")

    candidates = run_screen_and_store(conn, iso_date=iso_date, min_days=min_days)

    print(f"=== {iso_date} 候選清單（共{len(candidates)}檔）===")
    for c in candidates:
        print(f"  {c['stock_id']}：進場{c['entry_price']:.2f} 停損{c['stop_loss']:.2f}")

    if dry_run:
        print("--dry-run：略過實際發送LINE/Email通知。")
    else:
        # 兩個通知管道各自獨立try/except：例如Gmail憑證還沒設定時，LINE通知仍應正常發送，
        # 不應該讓其中一個管道還沒設定/暫時失敗就讓整條pipeline中斷（候選清單已經寫進Turso了）。
        try:
            send_line_broadcast(format_candidates_message(iso_date, candidates))
        except Exception as exc:  # noqa: BLE001
            print(f"LINE通知發送失敗（略過，不影響已寫入的候選清單）：{exc}")
        try:
            send_email(f"[每日選股] {iso_date}", format_candidates_email_body(iso_date, candidates))
        except Exception as exc:  # noqa: BLE001
            print(f"Email通知發送失敗（略過，不影響已寫入的候選清單）：{exc}")

    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-db", default=None, help="指向本機sqlite檔案而非連線Turso（測試/dry-run用）")
    parser.add_argument("--date", default=None, help="YYYYMMDD，預設為今天（補跑特定日期用）")
    parser.add_argument("--min-days", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true", help="只計算候選清單並印出，不實際發送LINE/Email通知")
    parser.add_argument("--skip-tpex", action="store_true", help="只更新TWSE，不透過FinMind更新TPEx（加速本機測試用）")
    args = parser.parse_args()

    if args.local_db:
        db_path = Path(args.local_db)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = storage.init_db(db_path)
    else:
        from src.data import turso_client
        conn = turso_client.get_connection()

    run_daily_pipeline(conn, date_str=args.date, min_days=args.min_days, dry_run=args.dry_run, skip_tpex=args.skip_tpex)
    conn.close()


if __name__ == "__main__":
    main()
