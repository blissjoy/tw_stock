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

    # 17:00排程：法人資料公布後，額外更新TPEx三大法人＋觀察清單F/G＋Google Sheet匯出
    # (--chip-refresh獨立於--dry-run之外，見run_daily_pipeline() docstring)
    python scripts/daily_pipeline.py --chip-refresh
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import finmind_client, holder_shares_sync, storage, tpex_client, twse_client, yfinance_client  # noqa: E402
from src.data.connection import get_default_portfolio_connection  # noqa: E402
from src.data.twse_client import STOCK_CODE_PATTERN  # noqa: E402
from src.notify.email_notify import format_candidates_email_body, send_email  # noqa: E402
from src.notify.line_notify import format_candidates_message, send_line_broadcast  # noqa: E402
from src.presentation import chart_data, pipeline_status, watchlist_export  # noqa: E402
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
            "listing_type": stock_info_by_id.get(sid, {}).get("listing_type"),
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

    ⚠️ **2026-08-04發現「下載錯誤」的真正root cause**：使用者回報14:30(TPEx早已收盤)
    仍看到一長串「下載錯誤」，之前誤判成「上櫃還沒收盤」——直接把同一批股票代號拿去
    真實查yfinance才發現：這些公司大多已經完成「轉上市」(從上櫃轉到上市)，Yahoo
    Finance根本不認得`.TWO`(上櫃)這個ticker後綴、要改用`.TW`(上市)才查得到(29檔
    實測失敗股票裡27檔用`.TW`查都有正常資料，錯誤訊息是`possibly delisted; no
    timezone found`，代表Yahoo完全不認得這個ticker，不是「今天還沒收盤」那種
    暫時性查無資料)。FinMind的`TaiwanStockInfo`分類資料本身會在稍晚才追上(事後
    重查，27檔裡多數FinMind已經改標TWSE了)，但排程當下抓到的可能還是尚未更新的
    舊分類，不能假設market欄位永遠即時正確。因此改成：TPEx批次下載(含內部重試)後
    仍失敗的股票，再用`.TW`後綴查一次當作「轉上市」備援，查到的話用market="TWSE"
    寫入(順便修正掉FinMind暫時落後的分類)，兩種後綴都查不到才是真正的「下載錯誤」
    (例如剛IPO還沒被Yahoo收錄的股票)。

    ⚠️ **同一天再追查剩餘的29-27=2檔「兩種後綴都查不到」**(4578/6813)+3檔第一版
    修好後仍不定期出現的個案：直接上網查證實全部10檔都是真的下市/併購/終止興櫃買賣
    (捷必勝-KY私有化下市、南璋停止買賣逾半年終止上櫃、金利被健策股份轉換、東元精電
    併回母公司東元等)，不是誤判——代表「兩種市場後綴皆查無資料」這個條件本身就是
    可靠的下市判斷依據。改成用`storage.delisted_stocks`表記錄下來(見schema.sql)，
    下次排程直接跳過這些股票，不再每天重複浪費下載嘗試、也不再重複印出同樣的錯誤
    訊息。已知下市的股票(`known_delisted_ids`)一開始就從`tpex_rows`篩掉，不會被
    納入這次的下載清單。
    """
    known_delisted_ids = storage.list_delisted_stock_ids(conn)
    tpex_rows = [
        r for r in stock_info
        if r["market"] == "TPEx" and STOCK_CODE_PATTERN.match(r["stock_id"]) and r["stock_id"] not in known_delisted_ids
    ]
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
    # 這裡剩下的才是重試後依然沒有資料的——先當作「轉上市」用.TW後綴再查一次(見上方
    # docstring)，兩種後綴都查不到才記錄成下市、印出訊息，取代yfinance原始的除錯輸出
    # (見2026-07-29的討論：不要把"possibly delisted...(1d 2026-07-27 -> 2026-07-28)"
    # 這種原始參數格式直接丟給使用者看)。
    remaining_ids = [sid for sid in info_by_id if sid not in prices_by_stock]
    reclassified_to_twse: dict[str, list[dict]] = {}
    if remaining_ids:
        try:
            reclassified_to_twse = yfinance_client.fetch_twse_prices_batch(remaining_ids, iso_date, next_day)
        except Exception as exc:  # noqa: BLE001
            print(f"[TPEx轉上市備援/yfinance] 批次下載失敗，略過：{exc}")

    newly_delisted = [sid for sid in remaining_ids if sid not in reclassified_to_twse]
    if newly_delisted:
        now_iso = datetime.now().isoformat()
        storage.upsert_delisted_stocks(conn, [
            {
                "stock_id": sid, "name": info_by_id[sid].get("name", sid), "delisted_date": None,
                "reason": "兩種市場後綴(.TWO/.TW)皆查無資料，可能已下市/併購/終止興櫃買賣",
                "noted_at": now_iso,
            }
            for sid in newly_delisted
        ])
        for stock_id in newly_delisted:
            print(f"{stock_id}{info_by_id[stock_id].get('name', '')} 下載錯誤（已記錄為疑似下市，之後排程不再嘗試）")

    for stock_id, prices in prices_by_stock.items():
        row = info_by_id[stock_id]
        storage.upsert_stocks(conn, [{
            "stock_id": stock_id, "name": row.get("name", stock_id), "market": "TPEx",
            "industry": row.get("industry"), "listing_type": row.get("listing_type"),
            "updated_at": datetime.now().isoformat(),
        }])
        storage.upsert_stock_prices(conn, prices)

    for stock_id, prices in reclassified_to_twse.items():
        row = info_by_id[stock_id]
        storage.upsert_stocks(conn, [{
            "stock_id": stock_id, "name": row.get("name", stock_id), "market": "TWSE",
            # listing_type刻意寫死"twse"、不沿用row.get("listing_type")：這個分支代表
            # FinMind的分類(可能是"tpex"，已經過期)被.TW後綴的下載成功結果推翻，見上方
            # docstring的「轉上市」說明，不該讓過期的原始分類值蓋掉這裡剛驗證過的結果。
            "industry": row.get("industry"), "listing_type": "twse",
            "updated_at": datetime.now().isoformat(),
        }])
        storage.upsert_stock_prices(conn, prices)

    return len(prices_by_stock) + len(reclassified_to_twse)


def fetch_today_tpex_institutional(conn, stock_info_by_id: dict) -> int:
    """抓TPEx全市場最新一個交易日的三大法人買賣超(見`src/data/tpex_client.py`)，寫入
    `institutional_investors`表。2026-08-04新增：修正TPEx股價自2026-07-23改用yfinance
    後，法人資料就再也沒有排程更新過的落差(股價/法人資料原本走同一支FinMind逐股迴圈，
    分開後法人資料被漏掉了——見`ai/BACKFILL_TPEX_INSTITUTIONAL_LOG.md`的一次性補值紀錄，
    這裡是讓它之後每天自動維護、不再變舊)。

    `tpex_client.fetch_institutional_investors()`一次回傳全市場資料，沒有股價那樣
    逐檔yfinance失敗的風險，呼叫端應該獨立包一層try/except；這裡本身不吞例外，讓
    呼叫端統一處理。寫入前先確保`stocks`表已經有對應股票(滿足`institutional_investors.
    stock_id`的外鍵參照)，用`stock_info_by_id`(FinMind股票基本資料)補name/industry，
    查無則退回用stock_id本身當name——理論上不會發生，tpex_client已過濾成4碼股票代號，
    跟FinMind股票清單高度重疊。
    """
    rows = tpex_client.fetch_institutional_investors()
    if not rows:
        return 0

    stock_ids = sorted({r["stock_id"] for r in rows})
    now_iso = datetime.now().isoformat()
    stock_rows = [
        {
            "stock_id": stock_id,
            "name": stock_info_by_id.get(stock_id, {}).get("name", stock_id),
            "market": "TPEx",
            "industry": stock_info_by_id.get(stock_id, {}).get("industry"),
            "updated_at": now_iso,
        }
        for stock_id in stock_ids
    ]
    storage.upsert_stocks(conn, stock_rows)
    storage.upsert_institutional_investors(conn, rows)
    return len(stock_ids)


# F/G(大戶/散戶持股週變化)抓取＋寫入邏輯搬到src/data/holder_shares_sync.py，跟
# desktop/main_window.py「重新整理」偵測到新加入觀察清單、本地DB查無F/G資料的股票時
# 即時補抓共用同一份邏輯，避免兩處各自維護一份幾乎相同的抓取迴圈。這裡保留原本的
# 函式名稱、原封不動轉呼叫，run_daily_pipeline()跟既有測試都不需要改動呼叫方式。
refresh_watchlist_holder_shares = holder_shares_sync.refresh_watchlist_holder_shares


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


def _month_starts(start_date: str, end_date: str) -> list[str]:
    """回傳start_date~end_date(含)涵蓋到的每個月份第一天，格式'YYYYMM01'(西元年)，
    供_fetch_taiex_official_volume()逐月查詢TWSE FMTQIK用(這個端點以「月」為單位
    回傳整月資料)。"""
    cur = date.fromisoformat(start_date).replace(day=1)
    end = date.fromisoformat(end_date).replace(day=1)
    months = []
    while cur <= end:
        months.append(cur.strftime("%Y%m01"))
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return months


def _fetch_taiex_official_volume(start_date: str, end_date: str) -> dict[str, int]:
    """呼叫TWSE官方FMTQIK，合併start_date~end_date(含)涵蓋到的每個月份，回傳
    {date: 成交股數}對照表。見fetch_today_taiex()說明：這是用來覆蓋掉yfinance
    ^TWII不可靠的volume欄位。"""
    result: dict[str, int] = {}
    for month_str in _month_starts(start_date, end_date):
        result.update(twse_client.fetch_taiex_volume(month_str))
    return result


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

    ⚠️ 2026-08-04發現：上面這個「回頭重抓等Yahoo補齊」的假設本身有破綻——實測發現
    Yahoo Finance的^TWII有時不是「volume=0還沒補」，而是**整天的資料列直接從回應裡
    消失**(用同一個查詢範圍連續呼叫3次結果一致，不是隨機的網路問題)，之後也不會再
    出現，往回重抓的自我修復機制對這種情況完全沒用。改用TWSE官方FMTQIK(大盤統計
    資訊)的「成交股數」覆蓋掉yfinance的volume欄位——這是TWSE逐日公布的官方數字，
    已用「發行量加權股價指數」欄位比對我們既有的收盤價數字完全一致，確認抓對列。
    OHLC(開高低收)繼續用yfinance(TWSE沒有提供大盤指數的開高低，只有收盤指數這一個
    數字)，兩個來源合併成同一列才寫入，跟`fetch_today_twse()`「TWSE股價+FinMind
    名稱/產業別」合併同一列的既有模式一致。TWSE查詢失敗只印出訊息、volume欄位維持
    yfinance原始值，不應該讓OHLC這部分已經抓到的資料也一起遺失。

    ⚠️ **這個merge邏輯不能只是「逐一走過`rows`、把符合日期的volume換掉」**：實測
    發現Yahoo「整天資料列消失」的那幾天，`rows`裡根本沒有那個日期可以走到，逐一遍歷
    `rows`永遠不會處理到它，volume就會永遠停在DB裡舊的錯誤值(第一版修法就是這樣，
    真實驗證時才發現漏掉這個情況)。改成先用`rows`建`date -> row`字典，再走過TWSE
    回傳的每個日期：`rows`裡已經有的直接換volume；`rows`裡沒有但DB裡已經存過OHLC
    (代表Yahoo以前回傳過這天、現在才消失)的，用DB裡的舊OHLC+TWSE的新volume組一筆
    補回去；兩邊都沒有OHLC可用的日期(從來没被yfinance回傳過)就放棄，沒有open/high/
    low/close可以寫。
    """
    iso_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
    next_day = (date.fromisoformat(iso_date) + timedelta(days=1)).isoformat()
    start_date = (date.fromisoformat(iso_date) - timedelta(days=TAIEX_REFETCH_WINDOW_DAYS)).isoformat()
    rows = yfinance_client.fetch_taiex_prices(start_date, next_day)
    rows_by_date = {row["date"]: row for row in rows}

    try:
        volume_by_date = _fetch_taiex_official_volume(start_date, iso_date)
    except Exception as exc:  # noqa: BLE001
        print(f"大盤官方成交量(TWSE FMTQIK)更新失敗（略過，volume欄位維持yfinance原始值）：{exc}")
        volume_by_date = {}

    for row_date, volume in volume_by_date.items():
        if row_date in rows_by_date:
            rows_by_date[row_date]["volume"] = volume
            continue
        existing = conn.execute(
            "SELECT open, high, low, close FROM stock_prices WHERE stock_id = ? AND date = ?",
            (yfinance_client.TAIEX_STOCK_ID, row_date),
        ).fetchone()
        if existing is None:
            continue  # 從來沒有任何來源回傳過這天的OHLC，沒有資料可以補
        open_, high, low, close = existing
        rows_by_date[row_date] = {
            "stock_id": yfinance_client.TAIEX_STOCK_ID, "date": row_date,
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "trading_money": None, "trading_turnover": None, "spread": None,
        }

    merged_rows = list(rows_by_date.values())
    if not merged_rows:
        return False

    storage.upsert_stocks(conn, [{
        "stock_id": yfinance_client.TAIEX_STOCK_ID, "name": "台股加權指數", "market": "INDEX",
        "industry": None, "updated_at": datetime.now().isoformat(),
    }])
    storage.upsert_stock_prices(conn, merged_rows)
    return True


def run_daily_pipeline(
    conn, date_str: str | None = None, min_days: int = 60, dry_run: bool = False, skip_tpex: bool = False,
    chip_refresh: bool = False, on_progress: Callable[[str, int, int], None] | None = None,
) -> list[dict]:
    """核心orchestration，刻意與「conn是Turso還是本機sqlite」無關，方便測試與dry-run重用。

    開始/結束時會寫入`data/pipeline_status.json`（見`src/presentation/pipeline_status.py`），
    不管是Windows工作排程器排程觸發、還是PySide6桌面版的手動抓取按鈕呼叫，都是同一個進入點、
    同一份狀態檔，桌面版UI只需要輪詢這個檔案就能顯示「目前正在自動跑」，不用另外設計通知機制。

    on_progress(stage, done, total)：stage是"TWSE"或"TPEx"，在對應的批次下載過程中被呼叫，
    供呼叫端顯示下載進度(例如桌面版狀態列顯示「TPEx 500/1980」)。

    chip_refresh：2026-08-04新增，獨立於dry_run之外的旗標，控制「法人籌碼公布後才需要
    做」的三件事：TPEx全市場三大法人買賣超、觀察清單F/G(大戶/散戶持股)重抓、觀察清單
    Google Sheet匯出。之所以獨立於dry_run，是因為既有8個Windows排程任務裡只有17:00/
    21:00這兩個帶`--dry-run`（其餘6個是全量抓取+真實LINE/Email通知），法人資料要17:00
    後才公布，若沿用「不dry_run才做」的舊邏輯，這幾件事反而永遠不會在正確的時間點執行；
    拆成獨立旗標後，17:00那次排程可以同時帶`--dry-run --chip-refresh`（不重複發送當天
    已經發過的LINE/Email候選清單通知，但仍執行這三件籌碼相關更新）。
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

            if chip_refresh:
                # 法人買賣超17:00才公布，跟股價一起在skip_tpex=False的路徑下更新，
                # 理由見run_daily_pipeline() chip_refresh參數說明。獨立try/except——
                # 失敗不應該讓股價/候選清單這些已經算完的資料受影響。
                try:
                    tpex_insti_count = fetch_today_tpex_institutional(conn, stock_info_by_id)
                    print(f"TPEx三大法人：{tpex_insti_count} 檔成功更新")
                except Exception as exc:  # noqa: BLE001
                    print(f"TPEx三大法人更新失敗（略過，不影響已寫入的股價/候選清單）：{exc}")

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

        if chip_refresh:
            # F/G(觀察清單股票)+Google Sheet匯出：2026-08-04改成獨立於dry_run之外的
            # chip_refresh旗標(理由見run_daily_pipeline() docstring)。兩件事共用同一個
            # portfolio_conn，各自獨立try/except——任一方失敗都不應該影響另一方，也不
            # 應該影響已經寫入的候選清單。interactive=False：自動排程沒有人在旁邊完成
            # 瀏覽器登入，本機沒有可用/可刷新的token時Sheets匯出會直接拋出
            # GoogleAuthRequiresInteractionError(見google_sheets_client.py)，這裡當
            # 一般失敗處理、印出訊息即可，不會讓pipeline卡住等待永遠不會來的瀏覽器互動。
            portfolio_conn = get_default_portfolio_connection()
            try:
                try:
                    holder_count = refresh_watchlist_holder_shares(conn, portfolio_conn)
                    print(f"F/G(觀察清單)：{holder_count} 檔成功更新")
                except Exception as exc:  # noqa: BLE001
                    print(f"F/G(觀察清單)更新失敗（略過，不影響已寫入的候選清單）：{exc}")

                try:
                    group_count = watchlist_export.export_all_watchlist_groups(conn, portfolio_conn, interactive=False)
                    print(f"觀察清單已匯出Google Sheet：{group_count}個群組")
                except Exception as exc:  # noqa: BLE001
                    print(f"觀察清單匯出Google Sheet失敗（略過，不影響已寫入的候選清單）：{exc}")
            finally:
                portfolio_conn.close()

        pipeline_status.append_run_snapshot(conn, iso_date, is_intraday, len(candidates))
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
    parser.add_argument(
        "--chip-refresh", action="store_true",
        help="更新TPEx三大法人買賣超＋觀察清單F/G(大戶/散戶持股)＋觀察清單Google Sheet匯出。"
             "獨立於--dry-run之外，17:00法人資料公布後的排程應加這個旗標（見run_daily_pipeline() docstring）",
    )
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
        chip_refresh=args.chip_refresh, on_progress=_print_progress,
    )
    conn.close()


if __name__ == "__main__":
    main()
