"""補齊法人(institutional_investors)/資券(margin_trading)資料缺口：掃描stock_prices
有資料、但institutional_investors或margin_trading缺資料的日期，從最新往回補。

背景：TWSE的T86(法人)/MI_MARGN(資券)官方端點偶爾會在某天回傳空結果(即使當天收盤價
已經是正式定案的最終數字，is_intraday=0)，daily_pipeline.py只抓「今天」、沒有回頭
補漏的機制，這種缺口一旦出現就會一直留著(2026-08-03實測發現：2023-01-03~2026-07-31
共863個交易日，法人/資券各自缺8天，全部集中在最近的日期)。

這裡用的還是同一組免費、不需token的TWSE官方端點(src/data/twse_client.py，跟
scripts/daily_pipeline.py/scripts/backfill_history.py共用)，不是新的資料來源——
純粹是「已知有缺口，重新嘗試」的補漏腳本，不是初次回補。

用法：
    python scripts/backfill_institutional_margin_gaps.py --db data/tw_stock.db

背景執行＋每5分鐘更新進度檔：
    python scripts/backfill_institutional_margin_gaps.py \
        --progress-file ai/BACKFILL_GAPS_LOG.md &

每次執行都是重新掃描目前DB實際缺什麼，不依賴額外的「上次補到哪」狀態檔——這個操作
本來就是idempotent的(重跑只會處理仍然缺資料的日期)，中斷後直接重跑即可，不需要
resume邏輯。TWSE當天真的沒有公布資料(retries後依然回傳空)的日期會記錄在進度檔裡，
不會每次重跑都re-try——呼叫端如果之後想強制重試，直接刪除/忽略這份記錄即可，不需要
額外參數。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import storage, twse_client  # noqa: E402

PROGRESS_INTERVAL_SEC = 300  # 每5分鐘更新一次進度檔(使用者要求)


def find_gap_dates(conn) -> list[str]:
    """回傳stock_prices有資料、但institutional_investors或margin_trading缺資料的
    日期清單("YYYY-MM-DD")，由新到舊排序(「從最近交易往前回補」)。"""
    price_dates = {r[0] for r in conn.execute("SELECT DISTINCT date FROM stock_prices").fetchall()}
    inst_dates = {r[0] for r in conn.execute("SELECT DISTINCT date FROM institutional_investors").fetchall()}
    margin_dates = {r[0] for r in conn.execute("SELECT DISTINCT date FROM margin_trading").fetchall()}
    # 缺法人「或」缺資券都算缺口——用交集(兩者都有)反過來扣，比分別算兩次聯集簡潔。
    complete_dates = inst_dates & margin_dates
    return sorted(price_dates - complete_dates, reverse=True)


def _write_progress(path: Path, done: int, total: int, current_date: str | None, results: dict, finished: bool) -> None:
    lines = [
        "# 法人/資券資料缺口回補進度",
        "",
        f"更新時間：{datetime.now().isoformat(timespec='seconds')}",
        f"狀態：{'已完成' if finished else '進行中'}",
        f"進度：{done}/{total} 天",
        f"目前處理到：{current_date or '-'}",
        "",
        f"- 補上法人資料：{results['filled_institutional']} 天",
        f"- 補上資券資料：{results['filled_margin']} 天",
        f"- 仍查無資料(TWSE當天確實沒有公布)：{len(results['still_empty'])} 天",
        f"- 發生錯誤：{len(results['errors'])} 天",
    ]
    if results["still_empty"]:
        lines += ["", "## 仍查無資料的日期"]
        lines += [f"- {d}（缺{kind}）" for kind, d in results["still_empty"]]
    if results["errors"]:
        lines += ["", "## 發生錯誤的日期"]
        lines += [f"- {d}：{err}" for d, err in results["errors"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def backfill_gaps(conn, gap_dates: list[str], sleep_sec: float, progress_path: Path) -> dict:
    inst_dates = {r[0] for r in conn.execute("SELECT DISTINCT date FROM institutional_investors").fetchall()}
    margin_dates = {r[0] for r in conn.execute("SELECT DISTINCT date FROM margin_trading").fetchall()}
    results: dict = {"filled_institutional": 0, "filled_margin": 0, "still_empty": [], "errors": []}
    total = len(gap_dates)
    last_report = 0.0  # 強制第一輪迴圈就寫一次進度檔，不用等滿5分鐘使用者才看得到東西

    for i, date_iso in enumerate(gap_dates, 1):
        date_str = date_iso.replace("-", "")
        try:
            if date_iso not in inst_dates:
                institutional = twse_client.fetch_institutional_investors(date_str)
                if institutional:
                    storage.upsert_institutional_investors(conn, institutional)
                    results["filled_institutional"] += 1
                    print(f"[法人] {date_iso}：補上{len(institutional)}筆")
                else:
                    results["still_empty"].append(("法人", date_iso))
                    print(f"[法人] {date_iso}：TWSE查無資料")
                time.sleep(sleep_sec)
            if date_iso not in margin_dates:
                margin = twse_client.fetch_margin_trading(date_str)
                if margin:
                    storage.upsert_margin_trading(conn, margin)
                    results["filled_margin"] += 1
                    print(f"[資券] {date_iso}：補上{len(margin)}筆")
                else:
                    results["still_empty"].append(("資券", date_iso))
                    print(f"[資券] {date_iso}：TWSE查無資料")
                time.sleep(sleep_sec)
        except Exception as exc:  # noqa: BLE001 - 單一日期失敗不應該中斷整批補漏
            results["errors"].append((date_iso, str(exc)))
            print(f"[錯誤] {date_iso}：{exc}")

        now = time.time()
        if now - last_report >= PROGRESS_INTERVAL_SEC:
            _write_progress(progress_path, i, total, date_iso, results, finished=False)
            last_report = now

    _write_progress(progress_path, total, total, gap_dates[0] if gap_dates else None, results, finished=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "tw_stock.db"))
    parser.add_argument("--sleep", type=float, default=1.0, help="每次TWSE請求之間的秒數間隔(預設1.0秒，比backfill_history.py既有的0.6秒更保守)")
    parser.add_argument("--progress-file", default=str(ROOT / "ai" / "BACKFILL_GAPS_LOG.md"))
    args = parser.parse_args()

    conn = storage.init_db(Path(args.db))
    progress_path = Path(args.progress_file)
    gap_dates = find_gap_dates(conn)

    if not gap_dates:
        print("沒有發現任何缺口，結束。")
        progress_path.write_text(
            f"# 法人/資券資料缺口回補進度\n\n更新時間：{datetime.now().isoformat(timespec='seconds')}\n"
            "狀態：已完成\n目前沒有發現任何缺口。\n",
            encoding="utf-8",
        )
        conn.close()
        return

    preview = "、".join(gap_dates[:5]) + ("..." if len(gap_dates) > 5 else "")
    print(f"共發現{len(gap_dates)}天缺口，從最新往回補：{preview}")
    results = backfill_gaps(conn, gap_dates, args.sleep, progress_path)
    conn.close()
    print(
        f"完成：補上法人{results['filled_institutional']}天、資券{results['filled_margin']}天，"
        f"仍查無資料{len(results['still_empty'])}天，錯誤{len(results['errors'])}天。"
        f"詳見{progress_path}"
    )


if __name__ == "__main__":
    main()
