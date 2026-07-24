"""每日自動化 pipeline 進入點：本機Windows工作排程器或GitHub Actions排程呼叫這支腳本。

流程：連線資料庫(本機sqlite或Turso) → 抓「今天」的 TWSE 全市場批次 + TPEx(透過yfinance
批次下載，只抓股價) 增量資料 → 寫入 → 跑 daily_screener 算候選清單 → 寫入 daily_candidates
表 → 發送 LINE + Email 通知。

⚠️ **TPEx資料來源沿革**：一開始用FinMind逐股抓取(~640檔各自一次請求)，實測發現需要約1小時
(FinMind的TPEx股票清單裡有~1900筆，但只有~640筆是純4碼的普通股票，其餘是ETF/債券/權證；
且FinMind超過額度時直接回傳402、要等一小時視窗過去才恢復)。2026-07-23改用
`src/data/yfinance_client.py`批次下載(仿照`ref-project/tw_stock_analyzer`長期實測驗證過
的`yf.download(tickers_array)`做法)，同樣資料量縮短到數十秒內。目前daily_screener只用得到
股價，法人與融資融券還沒有任何規則在用，所以TPEx仍然只抓股價，需要時再另外補。

--local-db 參數可以指向本機sqlite檔案而不連線Turso，本機優先架構下(見README)這是預設
的日常使用方式；不加這個參數則連線Turso，用於之後恢復雲端部署時。

用法：
    # 正式：連線Turso（需先在.env設定TURSO_DATABASE_URL/TURSO_AUTH_TOKEN）
    python scripts/daily_pipeline.py

    # 本機測試：不連Turso，改用本機sqlite檔案，且不真的發通知、跳過耗時的TPEx更新
    python scripts/daily_pipeline.py --local-db data/tw_stock_dryrun.db --dry-run --skip-tpex
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import finmind_client, storage, twse_client, yfinance_client  # noqa: E402
from src.data.twse_client import STOCK_CODE_PATTERN  # noqa: E402
from src.notify.email_notify import format_candidates_email_body, send_email  # noqa: E402
from src.notify.line_notify import format_candidates_message, send_line_broadcast  # noqa: E402
from src.presentation import pipeline_status  # noqa: E402
from src.screener.daily_screener import run_screen_and_store  # noqa: E402


def fetch_today_twse(
    conn, date_str: str, stock_info_by_id: dict[str, dict],
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[bool, bool]:
    """抓TWSE當天股價並寫入conn。回傳(is_trading_day, is_intraday)：
    - is_trading_day：是否抓到任何股價資料(不論是否已收盤)，False代表非交易日或兩個
      來源都失敗。
    - is_intraday：True代表這批股價來自yfinance的盤中即時價備援，還不是官方最終收盤價。

    優先嘗試TWSE官方「每日收盤行情」(MI_INDEX)端點——這是收盤後才會公布的最終定案數字，
    可靠性最高；但官方端點在收盤前查詢一律回傳空(已用真實請求驗證過，見ai/PLAN.md)。查無
    官方資料時改用yfinance批次下載盤中即時價當備援(比照fetch_today_tpex()既有的做法)，
    讓「手動抓取」按鈕在盤中也能拿到資料做即時訊號判斷——代價是拿到的是還在變動的當下
    價格，不是最終收盤價，數字可能在收盤前反覆changed。呼叫端(run_daily_pipeline)會把
    is_intraday寫進daily_data_status表，兩個前端UI依此顯示「尚未收盤」提示。

    stock_info_by_id: {stock_id: {"name":..., "industry":..., "market":...}}，來自FinMind
    TaiwanStockInfo，一律用來讓stocks表存真實公司名稱／產業別（此前的bug：TWSE官方端點
    回應本身雖有股票名稱欄位，但twse_client的parse函式沒有取用，這裡改用FinMind的名單
    補上）；yfinance備援路徑還額外需要它篩出「market=TWSE且是純4碼普通股」的股票清單
    (yfinance沒有股票清單這種基本資料，必須另外提供要下載哪些代號)。
    on_progress：官方端點成功時是單一請求，直接回報(1,1)；yfinance備援路徑則逐批次
    回報(見src/data/yfinance_client.py的fetch_prices_batch())。
    """
    prices = twse_client.fetch_stock_prices(date_str)
    is_intraday = False

    if prices:
        if on_progress is not None:
            on_progress(1, 1)
    else:
        twse_ids = [
            sid for sid, info in stock_info_by_id.items()
            if info.get("market") == "TWSE" and STOCK_CODE_PATTERN.match(sid)
        ]
        if not twse_ids:
            return False, False

        iso_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
        next_day = (date.fromisoformat(iso_date) + timedelta(days=1)).isoformat()
        try:
            prices_by_stock = yfinance_client.fetch_twse_prices_batch(twse_ids, iso_date, next_day, on_progress=on_progress)
        except Exception as exc:  # noqa: BLE001 - 備援下載失敗不應該讓整條pipeline中斷
            print(f"[TWSE/yfinance備援] 批次下載失敗：{exc}")
            return False, False
        if not prices_by_stock:
            return False, False
        prices = [row for rows in prices_by_stock.values() for row in rows]
        is_intraday = True

    institutional = twse_client.fetch_institutional_investors(date_str)
    margin = twse_client.fetch_margin_trading(date_str)

    # 三份報表的股票代號集合不完全相同，取聯集才不會在寫margin/institutional時違反外鍵約束
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
    return True, is_intraday


def fetch_today_tpex(
    conn, date_str: str, stock_info: list[dict], on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """批次抓TPEx普通股股價當天增量資料，回傳成功更新的股票數。

    ⚠️ **改用yfinance批次下載(2026-07-23)**：原本透過FinMind逐股抓取(~640檔各自一次
    請求)實測約需1小時；改用`src.data.yfinance_client`一次批次下載多檔(仿照
    `ref-project/tw_stock_analyzer`長期實測驗證過的做法)，同樣資料量可以縮短到數十秒內。
    yfinance是靠爬Yahoo Finance內部API運作的非官方套件，整批下載若失敗(例如網路問題)
    不會逐檔重試，而是這一步直接回傳0檔成功、印出錯誤訊息，讓呼叫端知道當天TPEx更新
    整批失敗，不強行假裝部分成功——原本FinMind逐股抓法仍保留在`src/data/finmind_client.py`，
    之後若yfinance失效可以退回使用。

    stock_info: finmind_client.fetch_stock_info() 的結果，只用來取得TPEx股票清單與真實
    名稱/產業別（yfinance沒有這些基本資料，仍需要FinMind的名單來補stocks表的name/industry
    欄位）。只篩選純4碼的普通股票代號(排除ETF/債券/權證等，比照
    src.data.twse_client.STOCK_CODE_PATTERN 對TWSE的既有做法一致)。
    """
    tpex_rows = [r for r in stock_info if r["market"] == "TPEx" and STOCK_CODE_PATTERN.match(r["stock_id"])]
    if not tpex_rows:
        return 0
    info_by_id = {r["stock_id"]: r for r in tpex_rows}

    iso_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
    next_day = (date.fromisoformat(iso_date) + timedelta(days=1)).isoformat()

    try:
        prices_by_stock = yfinance_client.fetch_tpex_prices_batch(
            list(info_by_id.keys()), iso_date, next_day, on_progress=on_progress,
        )
    except Exception as exc:  # noqa: BLE001 - 整批下載失敗不應該讓整條pipeline中斷
        print(f"[TPEx/yfinance] 批次下載失敗，本次TPEx更新整批略過：{exc}")
        return 0

    for stock_id, prices in prices_by_stock.items():
        row = info_by_id[stock_id]
        storage.upsert_stocks(conn, [{
            "stock_id": stock_id, "name": row.get("name", stock_id), "market": "TPEx",
            "industry": row.get("industry"), "updated_at": datetime.now().isoformat(),
        }])
        storage.upsert_stock_prices(conn, prices)

    return len(prices_by_stock)


def run_daily_pipeline(
    conn, date_str: str | None = None, min_days: int = 60, dry_run: bool = False, skip_tpex: bool = False,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[dict]:
    """核心orchestration，刻意與「conn是Turso還是本機sqlite」無關，方便測試與dry-run重用。

    開始/結束時會寫入`data/pipeline_status.json`（見`src/presentation/pipeline_status.py`），
    不管是Windows工作排程器排程觸發、還是PySide6桌面版的手動抓取按鈕呼叫，都是同一個進入點、
    同一份狀態檔，桌面版UI只需要輪詢這個檔案就能顯示「目前正在自動跑」，不用另外設計通知機制。

    on_progress(stage, done, total)：stage是"TWSE"或"TPEx"，在對應的批次下載過程中被呼叫，
    供呼叫端顯示下載進度(例如桌面版狀態列顯示「TPEx 500/1980」)。
    """
    storage.ensure_schema(conn)

    if date_str is None:
        date_str = date.today().strftime("%Y%m%d")
    iso_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"

    pipeline_status.write_status("running", date=iso_date)

    def _on_progress(stage: str, done: int, total: int) -> None:
        # 心跳：每次進度回報順便重寫一次running狀態，讓updated_at持續往前推進——2026-07-24
        # 的事故發現process被強制中止(kill/當機)時，Python的except/finally完全沒機會
        # 執行，狀態檔案會永久停在最初寫入的"running"、UI因此永遠顯示「更新中」。有了心跳，
        # pipeline_status.is_stale()才能正確判斷「updated_at已經很久沒更新=process可能
        # 已經非正常終止」，不用等process自己回報失敗。
        pipeline_status.write_status("running", date=iso_date, stage=stage, progress=f"{done}/{total}")
        if on_progress is not None:
            on_progress(stage, done, total)

    try:
        # 只呼叫一次FinMind的股票基本資料(涵蓋TWSE+TPEx)，同時供TWSE/TPEx兩條路徑取得真實
        # 公司名稱/產業別；即使skip_tpex=True也需要這份名單來修正TWSE的name欄位，成本很低(單次請求)。
        stock_info = finmind_client.fetch_stock_info()
        stock_info_by_id = {r["stock_id"]: r for r in stock_info}

        is_trading_day, is_intraday = fetch_today_twse(
            conn, date_str, stock_info_by_id,
            on_progress=lambda done, total: _on_progress("TWSE", done, total),
        )
        if not is_trading_day:
            print(f"{iso_date} TWSE官方收盤資料與yfinance盤中備援都查無資料，判定為非交易日，跳過選股與通知。")
            pipeline_status.write_status("done", date=iso_date, candidate_count=0, note="非交易日")
            return []

        storage.upsert_daily_data_status(conn, iso_date, is_intraday)
        if is_intraday:
            # 注意：這裡刻意不用⚠這類emoji/特殊符號——Windows主控台預設編碼(cp950)無法
            # 編碼這個字元，會直接讓print()丟UnicodeEncodeError整個中斷排程(已實測踩過)。
            print(f"注意：{iso_date} TWSE尚未收盤，本次使用yfinance盤中即時價，收盤後建議重新抓取一次取得最終數字。")

        if not skip_tpex:
            tpex_count = fetch_today_tpex(
                conn, date_str, stock_info,
                on_progress=lambda done, total: _on_progress("TPEx", done, total),
            )
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

        pipeline_status.write_status("done", date=iso_date, candidate_count=len(candidates))
        return candidates
    except Exception:
        pipeline_status.write_status("failed", date=iso_date)
        raise


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

    def _print_progress(stage: str, done: int, total: int) -> None:
        print(f"  {stage} 下載進度：{done}/{total}")

    run_daily_pipeline(
        conn, date_str=args.date, min_days=args.min_days, dry_run=args.dry_run, skip_tpex=args.skip_tpex,
        on_progress=_print_progress,
    )
    conn.close()


if __name__ == "__main__":
    main()
