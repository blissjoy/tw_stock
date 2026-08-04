"""F/G(大戶/散戶持股週變化)資料的抓取＋寫入邏輯。供`scripts/daily_pipeline.py`(每日
17:00排程，`--chip-refresh`旗標下，範圍=目前全部觀察清單股票)跟`desktop/main_
window.py`(「重新整理」偵測到觀察清單裡有股票本地DB完全查無F/G資料時，即時補抓，
範圍=那幾檔)共用，避免兩處各自維護一份幾乎相同的抓取迴圈。

放在`src/data/`(不是`src/presentation/`)是因為這裡會實際呼叫FinMind API並寫入DB，
跟`src/presentation/huang_chip_data.py`「純查詢本地DB、不呼叫任何API」的既有定位不同。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from src.data import finmind_client, portfolio_storage, storage

# TDCC集保戶股權分散表是週更新(通常每週五)，90天涵蓋約12週，足夠讓
# huang_chip_data.load_holder_change()算出「本週vs上週」的變化，即使某幾週因為連假
# 等原因延後公布也留有餘裕。
HOLDER_SHARES_LOOKBACK_DAYS = 90


def fetch_and_store_holder_shares(conn, stock_ids: list[str]) -> int:
    """逐檔呼叫FinMind(`TaiwanStockHoldingSharesPer`)、寫入`holder_shares_
    distribution`。單一股票抓取失敗(例如FinMind暫時性錯誤)只印出訊息、略過該檔，
    不讓整個迴圈中斷。回傳成功寫入的股票數。"""
    if not stock_ids:
        return 0

    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=HOLDER_SHARES_LOOKBACK_DAYS)).isoformat()
    now_iso = datetime.now().isoformat()

    success_count = 0
    for stock_id in stock_ids:
        try:
            rows = finmind_client.fetch_holding_shares_per(stock_id, start_date, end_date)
        except Exception as exc:  # noqa: BLE001
            print(f"F/G(大戶/散戶持股)：{stock_id} 抓取失敗（略過）：{exc}")
            continue
        if not rows:
            continue
        for row in rows:
            row["updated_at"] = now_iso
        storage.upsert_holder_shares_distribution(conn, rows)
        success_count += 1
    return success_count


def refresh_watchlist_holder_shares(conn, portfolio_conn) -> int:
    """F/G每日無條件全量重抓，範圍只限「目前觀察清單裡的股票」（不是全市場）。
    2026-08-04使用者明確要求：「t-7的判斷本來就該每天重新算，不用檢查是否超過一週
    才抓，每天無條件重抓就好」——這裡不做staleness判斷，每天對觀察清單裡的每一檔
    都重新查一次，觀察清單通常只有幾十檔，成本可忽略。"""
    stock_ids = portfolio_storage.list_all_watchlist_stock_ids(portfolio_conn)
    return fetch_and_store_holder_shares(conn, stock_ids)


def list_stock_ids_without_holder_data(conn, stock_ids: list[str]) -> list[str]:
    """回傳stock_ids裡「holder_shares_distribution完全沒有任何一筆資料」的子集合——
    純查本地DB，不是API呼叫，供desktop/main_window.py判斷「這幾檔要不要即時補抓」用
    (通常是17:00排程執行後才加入觀察清單的新股票，還沒被refresh_watchlist_holder_
    shares()涵蓋過)。"""
    if not stock_ids:
        return []
    placeholders = ",".join("?" * len(stock_ids))
    cur = conn.execute(
        f"SELECT DISTINCT stock_id FROM holder_shares_distribution WHERE stock_id IN ({placeholders})",
        stock_ids,
    )
    have_data = {row[0] for row in cur.fetchall()}
    return [stock_id for stock_id in stock_ids if stock_id not in have_data]
