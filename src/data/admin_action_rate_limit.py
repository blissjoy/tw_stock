"""web版對主DB Turso帳號有實質額度風險的「高風險動作」共用的速率限制：把每次嘗試
記錄進主DB的`admin_action_attempts`表(見schema.sql)，讓冷卻時間的計算跨process/
跨容器重啟都能維持(不能用本機檔案，Streamlit Cloud容器重啟會消失，且冷卻要保護的是
「Turso帳號」這個共用資源，不是單一process)。

2026-08-05：一開始只給「回補資料」用(模組名一度叫backfill_rate_limit)，後來
「▶ 手動抓取今日資料」按鈕發現是同一類風險(公開網址下任何訪客都能觸發、對Turso
寫入+耗FinMind額度、手動抓取還會觸發真實LINE/Email通知)，改成用kind參數
("backfill"/"manual_fetch")分開追蹤各自的冷卻時間，但共用同一張表/同一組
存取密碼(ADMIN_ACCESS_CODE，見src/data/config.py的get_admin_access_code())。

冷卻計算基準是「這次嘗試開始寫入的時間」(`started_at`)，不是完成時間：
即使這次執行途中失敗，Turso額度也已經被消耗掉一部分，不能因為「還沒完成」
就允許立刻重試，否則變成可以無限重試炸額度的漏洞。桌面版沒有這個風險
(一律寫本機sqlite)，完全不使用這個模組。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


def record_attempt_start(conn, kind: str, params: dict) -> int:
    """記錄一次動作嘗試開始，回傳這筆紀錄的id(之後record_attempt_result()要用)。"""
    cur = conn.execute(
        "INSERT INTO admin_action_attempts (kind, started_at, status, params_json) VALUES (?, ?, 'running', ?)",
        (kind, datetime.now(timezone.utc).isoformat(), json.dumps(params, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def record_attempt_result(conn, attempt_id: int, status: str, result: dict) -> None:
    """動作結束(成功或失敗)後補上結果——status應為'done'或'failed'。"""
    conn.execute(
        "UPDATE admin_action_attempts SET status = ?, result_json = ?, finished_at = ? WHERE id = ?",
        (status, json.dumps(result, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), attempt_id),
    )
    conn.commit()


def get_last_attempt(conn, kind: str) -> dict | None:
    """回傳指定kind最新一筆嘗試紀錄，沒有任何紀錄時回傳None。"""
    cur = conn.execute(
        "SELECT id, started_at, finished_at, status, params_json, result_json "
        "FROM admin_action_attempts WHERE kind = ? ORDER BY id DESC LIMIT 1",
        (kind,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "started_at": row[1], "finished_at": row[2], "status": row[3],
        "params": json.loads(row[4]) if row[4] else None,
        "result": json.loads(row[5]) if row[5] else None,
    }


def seconds_until_next_allowed(conn, kind: str, cooldown_seconds: int) -> float:
    """距離下次可以觸發指定kind的動作還要等幾秒，沒有任何紀錄或已過冷卻時回傳0.0。"""
    last = get_last_attempt(conn, kind)
    if last is None:
        return 0.0
    started_at = datetime.fromisoformat(last["started_at"])
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    remaining = cooldown_seconds - elapsed
    return max(0.0, remaining)
