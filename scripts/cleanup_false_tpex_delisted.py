"""一次性清理腳本：刪除Turso雲端主DB裡被誤判為下市的股票紀錄。

背景(2026-08-07)：`scripts/daily_pipeline.py`的`fetch_today_tpex()`原本「單一交易日
兩種市場後綴(.TWO/.TW)都查無資料」就直接判定下市、寫進`delisted_stocks`表，之後排程
永久跳過不再嘗試。這個判斷在盤剛開不久的排程時段(10:00)並不可靠——Yahoo Finance對
成交量小的股票，盤中資料常常還沒更新，會被誤判成「查無資料」。2026-08-04上線這個
功能以來，`delisted_stocks`累積116筆，其中只有8筆是當初真的人工查證過(`delisted_date`
有值)，其餘108筆全是自動誤判、從未人工複核，使用者實測發現多檔其實仍是正常上市股票。

已經修正`fetch_today_tpex()`改成要求連續3個不同交易日都查無資料才會真正判定下市(見
`TPEX_DELISTING_CONFIRM_DAYS`)，並且已經清理過本機`data/tw_stock.db`——但Turso雲端
主DB還沒清，因為`scripts/sync_local_to_turso.py`每天會把本機`delisted_stocks`整份
表同步上去(只upsert、不會刪除)，被誤判的108筆很可能已經被同步進Turso好幾次(見
`data/sync_to_turso_log.jsonl`：2026-08-05同步了83筆、2026-08-06同步了98筆)。

⚠️ 這支腳本必須在有真實網路連線可以連到Turso的環境執行(不能在某些沙盒/受限網路環境
執行，會直接卡住連不上)——直接刪除`delisted_date IS NULL`的所有列，這個條件安全的
理由：這支腳本修正後的程式碼再也不會寫入`delisted_date IS NULL`的紀錄(判定下市時一律
`delisted_date`留空是舊行為，但已經不會再被觸發，之後如果要真的手動記錄確認下市的
股票，本來就應該帶著查到的確切日期一起寫，不會是NULL)，所以留下來的都是最早8筆有
人工查證日期的紀錄，不會誤刪。

用法：
    # 先確認會刪掉哪些列，不會真的連線寫入
    python scripts/cleanup_false_tpex_delisted.py --dry-run

    # 正式執行(需先在.env或環境變數設定TURSO_DATABASE_URL/TURSO_AUTH_TOKEN)
    python scripts/cleanup_false_tpex_delisted.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import turso_client  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="只列出會刪除的股票，不實際連線寫入")
    args = parser.parse_args()

    conn = turso_client.get_connection()
    rows = conn.execute(
        "SELECT stock_id, name, reason, noted_at FROM delisted_stocks WHERE delisted_date IS NULL ORDER BY noted_at"
    ).fetchall()

    if not rows:
        print("Turso上沒有delisted_date為NULL的誤判紀錄，不需要清理。")
        return

    print(f"找到 {len(rows)} 筆誤判為下市的紀錄：")
    for stock_id, name, reason, noted_at in rows:
        print(f"  {stock_id}{name}（{noted_at}）")

    if args.dry_run:
        print(f"\n--dry-run模式，尚未實際刪除。確認無誤後移除--dry-run重新執行。")
        return

    conn.execute("DELETE FROM delisted_stocks WHERE delisted_date IS NULL")
    conn.commit()
    remaining = conn.execute("SELECT COUNT(*) FROM delisted_stocks").fetchone()[0]
    print(f"\n已刪除 {len(rows)} 筆，Turso上delisted_stocks剩下 {remaining} 筆(應為原本人工查證過的紀錄)。")


if __name__ == "__main__":
    main()
