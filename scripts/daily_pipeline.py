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
from src.presentation import chart_data, pipeline_status  # noqa: E402
from src.screener.daily_screener import refresh_indicator_window, run_screen_and_store  # noqa: E402


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
        # fetch_twse_prices_batch()內部已經對失敗股票重試過，這裡剩下的是重試後依然
        # 沒有資料的，印出好懂的訊息(理由同fetch_today_tpex()的相同處理)
        for stock_id in twse_ids:
            if stock_id not in prices_by_stock:
                print(f"{stock_id}{stock_info_by_id.get(stock_id, {}).get('name', '')} 下載錯誤")
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

    # fetch_tpex_prices_batch()內部已經對失敗的股票重試過(見yfinance_client.py)，
    # 這裡剩下的才是重試後依然沒有資料的，印出好懂的訊息取代yfinance原始的除錯輸出
    # (見2026-07-29的討論：不要把"possibly delisted...(1d 2026-07-27 -> 2026-07-28)"
    # 這種原始參數格式直接丟給使用者看)。
    for stock_id in info_by_id:
        if stock_id not in prices_by_stock:
            print(f"{stock_id}{info_by_id[stock_id].get('name', '')} 下載錯誤")

    for stock_id, prices in prices_by_stock.items():
        row = info_by_id[stock_id]
        storage.upsert_stocks(conn, [{
            "stock_id": stock_id, "name": row.get("name", stock_id), "market": "TPEx",
            "industry": row.get("industry"), "updated_at": datetime.now().isoformat(),
        }])
        storage.upsert_stock_prices(conn, prices)

    return len(prices_by_stock)


# 每次更新大盤時，往回一併重新抓取的天數(見fetch_today_taiex()說明)。10個日曆天
# 涵蓋約6~7個交易日，足夠蓋過連續假期，成本可忽略(yfinance單一ticker的請求，範圍
# 拉長不會明顯增加耗時)。
TAIEX_REFETCH_WINDOW_DAYS = 10

# 每次排程執行時，往回重刷daily_indicators(均線/SAR快取)的交易日數(見
# src.screener.daily_screener.refresh_indicator_window()、src/data/schema.sql的
# daily_indicators表說明)。用來吸收股價資料事後被修正的風險——精神上跟上面的
# TAIEX_REFETCH_WINDOW_DAYS一樣，10天(以交易日為單位，不是日曆天)是「安全邊際 vs
# 排程多花的時間」的取捨，全市場~2300檔股票、每檔10天，比只算「今天」一天多了10倍
# 運算量，但仍遠比之前「每次套用篩選都對全市場即時重算SAR」(15~35秒)划算，且是背景
# 排程執行、不會卡住任何互動操作。
INDICATOR_REFRESH_WINDOW_DAYS = 10


def fetch_today_taiex(conn, date_str: str) -> bool:
    """更新大盤(台股加權指數，`yfinance_client.TAIEX_STOCK_ID`)的OHLCV資料。

    獨立於TWSE/TPEx兩條個股抓取路徑之外，只有一筆OHLCV資料要處理(不像個股還要合併
    三大法人/融資融券報表)。先upsert_stocks()寫一筆market="INDEX"的紀錄滿足
    stock_prices.stock_id的外鍵參照(見yfinance_client.fetch_taiex_prices()
    docstring)，再寫入價格。呼叫端(`run_daily_pipeline()`)應該把這個函式包在自己的
    try/except裡——任何一方失敗都不應該互相影響，這裡本身不吞例外。

    ⚠️ 2026-08-01發現：這裡原本只抓`date_str`當天單一天，之後排程永遠不會回頭重新
    查詢過去的日期。實測發現Yahoo Finance的^TWII成交量資料在當天查詢時常常還沒回補
    完整(觀察到連續4天volume=0)，但幾天後同一天再查就有正確數字——這是Yahoo那端的
    資料延遲，不是我們解析錯誤(個股股價那邊有TWSE官方查詢+is_intraday機制處理類似的
    「盤中查詢資料還沒到位」情境，大盤原本沒有對應機制)。改成每次順便回頭多抓
    `TAIEX_REFETCH_WINDOW_DAYS`天(不只當天)，`storage.upsert_stock_prices()`本來
    就是ON CONFLICT DO UPDATE，回抓到比之前更完整的資料時會自動覆蓋掉舊的0值，不需要
    額外偵測「哪幾天資料不完整」的邏輯，日後也會持續自我修正。
    """
    iso_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
    next_day = (date.fromisoformat(iso_date) + timedelta(days=1)).isoformat()
    start_date = (date.fromisoformat(iso_date) - timedelta(days=TAIEX_REFETCH_WINDOW_DAYS)).isoformat()
    rows = yfinance_client.fetch_taiex_prices(start_date, next_day)
    if not rows:
        return False
    storage.upsert_stocks(conn, [{
        "stock_id": yfinance_client.TAIEX_STOCK_ID, "name": "台股加權指數", "market": "INDEX",
        "industry": None, "updated_at": datetime.now().isoformat(),
    }])
    storage.upsert_stock_prices(conn, rows)
    return True


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

        # 大盤(台股加權指數)更新獨立包一層try/except——失敗不應該讓整條pipeline中斷，
        # 個股資料已經抓完、候選清單照樣要算，大盤只是額外的補充分析素材。
        try:
            taiex_ok = fetch_today_taiex(conn, date_str)
            print(f"大盤({yfinance_client.TAIEX_STOCK_ID})：{'更新成功' if taiex_ok else '查無資料，略過'}")
        except Exception as exc:  # noqa: BLE001
            print(f"大盤({yfinance_client.TAIEX_STOCK_ID})更新失敗（略過，不影響個股資料）：{exc}")

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

        # 均線/SAR快取往回刷新：run_screen_and_store()本身只算iso_date當天，這裡額外
        # 補刷最近INDICATOR_REFRESH_WINDOW_DAYS個交易日，吸收股價資料事後被修正的風險
        # (見INDICATOR_REFRESH_WINDOW_DAYS常數說明)。獨立包一層try/except——失敗不應該
        # 讓整條pipeline中斷，候選清單已經算完寫入了，均線/SAR快取只是輔助查詢用的
        # 衍生資料，即使這裡失敗，篩選頂多退回「找不到快取列視為不成立」，不影響核心資料。
        try:
            refreshed = refresh_indicator_window(conn, iso_date, INDICATOR_REFRESH_WINDOW_DAYS)
            print(f"均線/SAR快取：往回刷新{INDICATOR_REFRESH_WINDOW_DAYS}個交易日，共{refreshed}筆")
        except Exception as exc:  # noqa: BLE001
            print(f"均線/SAR快取刷新失敗（略過，不影響候選清單）：{exc}")

        if dry_run:
            print("--dry-run：略過實際發送LINE/Email通知。")
        else:
            # ⚠️ 2026-08-03修正：通知內容套用跟UI候選清單「預設檢視」完全相同的篩選
            # 條件(chart_data.CANDIDATE_FILTER_DEFAULTS/CANDIDATE_SAR_FLIP_*_DEFAULT/
            # CANDIDATE_ZHU_RULE_ONLY_DEFAULT)，不是直接送出run_screen_and_store()的
            # 原始candidates——candidates是「當天觸發過任何一條規則」的完整清單，沒有
            # 套用UI預設額外要求的均線多頭排列+SAR翻轉條件，範圍比UI候選清單寬很多。
            # 使用者回報收到的LINE通知涵蓋股票數比打開UI看到的候選清單多，兩邊篩選
            # 條件對不齊、讓人誤以為系統有bug，才發現這個落差——這裡改成先用跟UI同一套
            # 預設條件篩選過，再從candidates篩出通過的股票才格式化發送，確保「主動推播
            # 收到的」跟「打開UI看到的」是同一個集合。獨立包一層try/except，理由跟下面
            # 兩個通知管道一致：候選清單已經成功寫進DB了，如果這層篩選本身出問題(例如
            # daily_indicators查詢失敗)，不應該讓整條pipeline被標記成失敗、也不應該讓
            # 使用者今天完全收不到通知——退回寄出未篩選的原始candidates(寧可訊息範圍
            # 比UI寬，也不要整批通知直接消失)。
            notify_candidates = candidates
            try:
                universe_df, _, _ = chart_data.load_stock_universe_for_date(conn, target_date=iso_date)
                active_filter_labels = [
                    label for label, default in chart_data.CANDIDATE_FILTER_DEFAULTS.items() if default
                ]
                filtered_df = chart_data.apply_candidate_filters(
                    conn, universe_df, active_filter_labels,
                    sar_flip_option=(
                        chart_data.CANDIDATE_SAR_FLIP_OPTION_DEFAULT
                        if chart_data.CANDIDATE_SAR_FLIP_ENABLED_DEFAULT else None
                    ),
                    zhu_rule_only=chart_data.CANDIDATE_ZHU_RULE_ONLY_DEFAULT,
                    as_of_date=iso_date,
                )
                notify_stock_ids = set(filtered_df["stock_id"])
                notify_candidates = [c for c in candidates if c["stock_id"] in notify_stock_ids]
                print(f"通知套用UI預設篩選後：{len(notify_stock_ids)}檔（原始規則命中{len(candidates)}筆）")
            except Exception as exc:  # noqa: BLE001
                print(f"通知篩選失敗，改寄未篩選的原始候選清單（略過，不影響已寫入的候選清單）：{exc}")

            # 兩個通知管道各自獨立try/except：例如Gmail憑證還沒設定時，LINE通知仍應正常發送，
            # 不應該讓其中一個管道還沒設定/暫時失敗就讓整條pipeline中斷（候選清單已經寫進Turso了）。
            try:
                stock_names = {sid: info.get("name", "") for sid, info in stock_info_by_id.items()}
                send_line_broadcast(format_candidates_message(iso_date, notify_candidates, stock_names=stock_names))
            except Exception as exc:  # noqa: BLE001
                print(f"LINE通知發送失敗（略過，不影響已寫入的候選清單）：{exc}")
            try:
                send_email(f"[每日選股] {iso_date}", format_candidates_email_body(iso_date, notify_candidates))
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
