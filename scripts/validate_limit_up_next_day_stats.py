"""驗證腳本：用本機真實DB的股價資料，重新統計「短線券商鎖漲停後隔日走勢」(R-CHIP-15，
見`ai/chen-rules/籌碼面/鎖漲停後隔日走勢統計.md`)，跟陳家豐書中2014年前的benchmark
數字比對，確認這組統計在近期市場是否還成立。

⚠️這裡用「近似漲停判定」(`src/indicators/limit_up_stats.py`的`is_limit_up()`)偵測
全市場「所有」鎖漲停事件，不是書中原文「短線券商進出的2,000多筆」這個更狹窄的子
集合——書中的樣本額外要求是短線券商所為，需要分點資料才能篩選出來(本專案目前沒有
`broker_chips`，見R-CHIP-16)。這裡驗證的是更寬鬆的版本：「任何鎖漲停後的隔日走勢
統計」，跟書中數字不是嚴格對照組，只能看整體方向是否一致(例如開高機率是否明顯
高於50%)，不能拿來精確驗證「短線客特有」這個更窄的子命題。

用法：
    python scripts/validate_limit_up_next_day_stats.py
    python scripts/validate_limit_up_next_day_stats.py --local-db data/tw_stock.db
    python scripts/validate_limit_up_next_day_stats.py --years-back 3
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.indicators import limit_up_stats  # noqa: E402

BOOK_BENCHMARK = {
    "open_higher_rate": 0.757,
    "avg_open_pct": 0.0191,
    "avg_high_pct": 0.0395,
    "avg_low_pct": -0.0078,
    "avg_amplitude": 0.0473,
    "avg_close_pct": 0.007,
    "red_rate": 0.80,
}
LABELS = {
    "open_higher_rate": "隔日開高機率",
    "avg_open_pct": "平均開高幅度",
    "avg_high_pct": "平均最高點漲幅",
    "avg_low_pct": "平均最低點跌幅",
    "avg_amplitude": "平均日內振幅",
    "avg_close_pct": "平均收盤漲幅",
    "red_rate": "隔日收黑機率(代理指標，見模組docstring)",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-db", default="data/tw_stock.db", help="本機sqlite檔案路徑（預設data/tw_stock.db）")
    parser.add_argument("--years-back", type=int, default=5, help="只統計最近N年的資料（預設5年）")
    args = parser.parse_args()

    conn = sqlite3.connect(args.local_db)
    cutoff = (date.today() - timedelta(days=args.years_back * 365)).isoformat()

    rows = conn.execute(
        "SELECT stock_id, date, open, high, low, close FROM stock_prices WHERE date >= ? ORDER BY stock_id, date",
        (cutoff,),
    ).fetchall()
    conn.close()

    by_stock: dict[str, list[tuple]] = {}
    for stock_id, date_, open_, high, low, close in rows:
        by_stock.setdefault(stock_id, []).append((date_, open_, high, low, close))

    events = []
    total_limit_up_days = 0
    for series in by_stock.values():
        for i in range(1, len(series) - 1):  # 需要前一天(算prev_close)跟後一天(隔日OHLC)
            prev_close = series[i - 1][4]
            today_close = series[i][4]
            if limit_up_stats.is_limit_up(prev_close, today_close):
                total_limit_up_days += 1
                _, next_open, next_high, next_low, next_close = series[i + 1]
                events.append(limit_up_stats.next_day_event_stats(next_open, next_high, next_low, next_close, today_close))

    stats = limit_up_stats.summarize_events(events)

    print(f"統計期間：近{args.years_back}年（{cutoff}起）")
    print(f"鎖漲停(近似判定)事件數：{total_limit_up_days}，可算隔日報酬的樣本數：{stats['n']}")
    print()
    header = f"{'指標':<28}{'本次統計':>12}{'書中benchmark':>16}"
    print(header)
    print("-" * len(header))
    for key, label in LABELS.items():
        actual = stats[key]
        actual_str = f"{actual:+.2%}" if actual is not None else "N/A"
        bench_str = f"{BOOK_BENCHMARK[key]:+.2%}"
        print(f"{label:<28}{actual_str:>12}{bench_str:>16}")


if __name__ == "__main__":
    main()
