"""補齊TPEx(上櫃)股票的法人買賣超(institutional_investors)資料缺口。

背景(2026-08-04發現)：`scripts/daily_pipeline.py`自2026-07-23起TPEx股價改用yfinance
批次下載後，該檔案docstring明講「法人與融資融券還沒有任何規則在用，所以TPEx仍然只抓
股價，需要時再另外補」——也就是說TPEx股票的法人資料從那之後就沒有再被每天更新過，是
刻意延後、不是bug漏掉。全DB掃描(2026-08-04)發現：2495檔股票裡710檔(幾乎全是TPEx，
17檔例外是TWSE)完全沒有法人資料、另外695檔資料落後超過3天(559檔落後8~14天、136檔
落後15天以上)。黃豐凱籌碼分析法(`src/indicators/huang_chip_signals.py`，程式碼來源
private)的D/E(投信/外資連續買賣超狀態)、K~R(法人買賣超張數)這幾欄需要用到近期(40天內)
的法人買賣超資料，這個缺口必須補齊才能正確顯示。

用FinMind的逐股`TaiwanStockInstitutionalInvestorsBuySell`(`src/data/finmind_client.py`，
已有節流機制、550次/小時)補資料——只抓最近`--lookback-days`天(預設120個曆日，涵蓋
`huang_chip_data.py`的`INSTITUTIONAL_LOOKBACK_DAYS`(90天)所需範圍，留一些餘裕)，不是
全歷史回補；每檔股票只需要1次API請求，但股票數量多(~1400檔缺口)，全部跑完保守估計
要2.5小時以上(1400/550≈2.5小時，逐股呼叫的成本由請求次數決定、不是資料量)。

每次執行都重新掃描目前DB實際缺什麼(比對`stocks`跟`institutional_investors`的
MAX(date))，不依賴額外的「上次補到哪」狀態檔——這個操作是idempotent的，中斷後直接
重跑即可，已經補到最新的股票不會重複處理，只會處理當下still缺的那些。

⚠️ 這是長時間背景作業，會持續呼叫FinMind——避免跟`scripts/daily_pipeline.py`的排程
同時執行(兩個獨立process各自的節流狀態不共用，同時跑可能一起超過FinMind伺服器端
600次/小時的真正上限)。

用法：
    python scripts/backfill_tpex_institutional_gaps.py --db data/tw_stock.db
    python scripts/backfill_tpex_institutional_gaps.py --limit 10  # 先測試少量股票
    python scripts/backfill_tpex_institutional_gaps.py --stale-threshold-days 3

背景執行＋每5分鐘更新進度檔：
    python scripts/backfill_tpex_institutional_gaps.py \\
        --progress-file ai/BACKFILL_TPEX_INSTITUTIONAL_LOG.md &
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import finmind_client, storage  # noqa: E402

# 跟src/data/twse_client.py的STOCK_CODE_PATTERN同一個定義：只處理純4碼的普通股票，
# 排除ETF/債券/權證(例如00744B這種5~6碼代號)——這些本來就沒有法人買賣超資料，FinMind
# 查了也是空的，篩掉才不會浪費API額度在注定查無資料的代號上(2026-08-04測試--limit 5
# 時發現，沒篩選前抓到的全是00744B/00745B這類代號)。
STOCK_CODE_PATTERN = re.compile(r"^\d{4}$")

PROGRESS_INTERVAL_SEC = 300  # 每5分鐘更新一次進度檔，跟backfill_institutional_margin_gaps.py同慣例
DEFAULT_LOOKBACK_DAYS = 120  # 涵蓋huang_chip_data.INSTITUTIONAL_LOOKBACK_DAYS(90天)所需範圍，留餘裕
DEFAULT_STALE_THRESHOLD_DAYS = 3  # 落後超過幾天視為需要補(對應全DB掃描時分類「有落後」的門檻)


def find_gapped_stocks(conn, stale_threshold_days: int, as_of_date: str) -> list[str]:
    """回傳institutional_investors資料完全缺失、或最新一筆落後as_of_date超過
    stale_threshold_days天的股票代號清單(依股票代號排序)——只看TWSE/TPEx(排除
    INDEX這種非個股的市場分類，例如加權指數本身沒有法人買賣超資料，永遠會被誤判
    成缺口，實際上打了也是白打)。
    """
    rows = conn.execute(
        """
        SELECT s.stock_id, MAX(ii.date) AS max_date
        FROM stocks s
        LEFT JOIN institutional_investors ii ON ii.stock_id = s.stock_id
        WHERE s.market IN ('TWSE', 'TPEx')
        GROUP BY s.stock_id
        """
    ).fetchall()

    as_of = date.fromisoformat(as_of_date)
    gapped: list[str] = []
    for stock_id, max_date in rows:
        if not STOCK_CODE_PATTERN.match(stock_id):
            continue
        if max_date is None:
            gapped.append(stock_id)
            continue
        days_behind = (as_of - date.fromisoformat(max_date)).days
        if days_behind > stale_threshold_days:
            gapped.append(stock_id)
    return sorted(gapped)


def _write_progress(path: Path, done: int, total: int, current_stock: str | None, results: dict, finished: bool) -> None:
    lines = [
        "# TPEx法人買賣超資料缺口回補進度",
        "",
        f"更新時間：{datetime.now().isoformat(timespec='seconds')}",
        f"狀態：{'已完成' if finished else '進行中'}",
        f"進度：{done}/{total} 檔",
        f"目前處理到：{current_stock or '-'}",
        "",
        f"- 補上資料：{results['filled']} 檔",
        f"- 查無資料(FinMind該股票確實沒有法人買賣超紀錄)：{len(results['still_empty'])} 檔",
        f"- 發生錯誤：{len(results['errors'])} 檔",
    ]
    if results["still_empty"]:
        lines += ["", "## 查無資料的股票"]
        lines += [f"- {sid}" for sid in results["still_empty"]]
    if results["errors"]:
        lines += ["", "## 發生錯誤的股票"]
        lines += [f"- {sid}：{err}" for sid, err in results["errors"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def backfill_stocks(conn, stock_ids: list[str], start_date: str, end_date: str, progress_path: Path) -> dict:
    results: dict = {"filled": 0, "still_empty": [], "errors": []}
    total = len(stock_ids)
    last_report = 0.0  # 強制第一輪迴圈就寫一次進度檔

    start_time = time.time()
    for i, stock_id in enumerate(stock_ids, 1):
        try:
            rows = finmind_client.fetch_institutional_investors(stock_id, start_date, end_date)
            if rows:
                storage.upsert_institutional_investors(conn, rows)
                results["filled"] += 1
                print(f"[{i}/{total}] {stock_id}：補上{len(rows)}筆")
            else:
                results["still_empty"].append(stock_id)
                print(f"[{i}/{total}] {stock_id}：FinMind查無資料")
        except Exception as exc:  # noqa: BLE001 - 單一股票失敗不應該中斷整批補漏
            results["errors"].append((stock_id, str(exc)))
            print(f"[{i}/{total}] {stock_id}：錯誤 - {exc}")

        now = time.time()
        if now - last_report >= PROGRESS_INTERVAL_SEC:
            _write_progress(progress_path, i, total, stock_id, results, finished=False)
            last_report = now

    elapsed = time.time() - start_time
    _write_progress(progress_path, total, total, stock_ids[-1] if stock_ids else None, results, finished=True)
    print(f"耗時{elapsed:.1f}秒")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--as-of-date", default=None, help="判斷「落後幾天」的基準日，預設用DB裡stock_prices的最新日期")
    parser.add_argument("--stale-threshold-days", type=int, default=DEFAULT_STALE_THRESHOLD_DAYS)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="每檔股票回補最近幾個曆日")
    parser.add_argument("--limit", type=int, default=None, help="只處理前N檔，方便測試")
    parser.add_argument("--progress-file", default=str(ROOT / "ai" / "BACKFILL_TPEX_INSTITUTIONAL_LOG.md"))
    args = parser.parse_args()

    conn = storage.init_db(Path(args.db))
    progress_path = Path(args.progress_file)

    as_of_date = args.as_of_date or conn.execute("SELECT MAX(date) FROM stock_prices").fetchone()[0]
    if as_of_date is None:
        print("stock_prices沒有任何資料，無法判斷基準日，結束。")
        conn.close()
        return

    gapped = find_gapped_stocks(conn, args.stale_threshold_days, as_of_date)
    if args.limit:
        gapped = gapped[: args.limit]

    if not gapped:
        print("沒有發現任何缺口，結束。")
        progress_path.write_text(
            f"# TPEx法人買賣超資料缺口回補進度\n\n更新時間：{datetime.now().isoformat(timespec='seconds')}\n"
            "狀態：已完成\n目前沒有發現任何缺口。\n",
            encoding="utf-8",
        )
        conn.close()
        return

    start_date = (date.fromisoformat(as_of_date) - timedelta(days=args.lookback_days)).isoformat()
    preview = "、".join(gapped[:10]) + ("..." if len(gapped) > 10 else "")
    print(f"基準日：{as_of_date}，共發現{len(gapped)}檔缺口(落後>{args.stale_threshold_days}天或完全沒資料)：{preview}")
    print(f"回補區間：{start_date} ~ {as_of_date}")

    results = backfill_stocks(conn, gapped, start_date, as_of_date, progress_path)
    conn.close()
    print(
        f"完成：補上{results['filled']}檔，查無資料{len(results['still_empty'])}檔，"
        f"錯誤{len(results['errors'])}檔。詳見{progress_path}"
    )


if __name__ == "__main__":
    main()
