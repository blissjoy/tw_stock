"""Turso（libsql）連線層：把「目前該用哪個 Turso Python 套件」的細節隔離在這一個檔案，
之後套件名稱/API 若又變動（這幾年已經改過：libsql-client -> libsql-experimental -> libsql，
甚至查證官方文件時兩次都拿到不完全一致的說法），只需要修改這裡，不影響 storage.py 或呼叫端。

實作時（2026-07）查證 docs.turso.tech/sdk/python 後採用直接連線遠端資料庫的方式：
`import libsql; libsql.connect(database=url, auth_token=token)`，
而非需要本機檔案的 embedded-replica sync 模式（那個模式要維護本機檔案 push()/pull()，
對GitHub Actions這種每次都是全新環境的runner沒有優勢）。若屆時套件名稱又變了，
只需要改下面 get_connection() 內部的 import 與呼叫方式即可。

storage.py 裡所有 upsert 函式的 SQL 都用 sqlite3 具名參數風格撰寫（例如 `:stock_id`），
但不同版本的 Turso client 對具名參數的支援程度不保證與 sqlite3 一致，因此這裡統一把
具名參數轉成標準 `?` 位置參數再交給底層連線執行，讓 storage.py 不必因為底層client版本
更迭而改寫SQL。
"""

from __future__ import annotations

import re
from typing import Any

from src.data.config import get_turso_credentials

_NAMED_PARAM_RE = re.compile(r":(\w+)")


def _to_positional(sql: str, row: dict) -> tuple[str, tuple]:
    """把 `:name` 具名參數改寫成 `?` 位置參數，回傳 (改寫後SQL, 依序排好的參數tuple)。"""
    names: list[str] = []

    def _replace(match: re.Match) -> str:
        names.append(match.group(1))
        return "?"

    positional_sql = _NAMED_PARAM_RE.sub(_replace, sql)
    return positional_sql, tuple(row[name] for name in names)


class TursoConnection:
    """包一層轉接器：統一介面(execute/executemany/executescript/commit/close)，
    不論底層 libsql client 實際支不支援具名參數或executemany，storage.py都能正常呼叫。
    """

    def __init__(self, raw_conn: Any) -> None:
        self._raw = raw_conn

    def execute(self, sql: str, params: dict | tuple | None = None):
        if isinstance(params, dict):
            sql, params = _to_positional(sql, params)
        if params is None:
            return self._raw.execute(sql)
        return self._raw.execute(sql, params)

    def executemany(self, sql: str, rows: list[dict]) -> None:
        """優先使用底層連線原生的 executemany（若有支援，通常是單次批次呼叫，避免逐列
        各打一次網路請求）；底層若不支援才退回逐列呼叫 execute() 的方式。"""
        if not rows:
            return
        if isinstance(rows[0], dict):
            positional_sql, first_params = _to_positional(sql, rows[0])
            params_list = [first_params] + [_to_positional(sql, row)[1] for row in rows[1:]]
        else:
            positional_sql, params_list = sql, list(rows)

        if hasattr(self._raw, "executemany"):
            self._raw.executemany(positional_sql, params_list)
        else:
            for params in params_list:
                self._raw.execute(positional_sql, params)

    def executescript(self, script: str) -> None:
        if hasattr(self._raw, "executescript"):
            self._raw.executescript(script)
            return
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self._raw.execute(statement)

    def commit(self) -> None:
        if hasattr(self._raw, "commit"):
            self._raw.commit()

    def close(self) -> None:
        self._raw.close()


def get_connection() -> TursoConnection:
    """連線到 Turso 雲端資料庫，回傳的物件介面與 sqlite3.Connection 相容，
    可直接傳給 src/data/storage.py 的所有 upsert_*/init_db 函式使用。
    """
    import libsql

    url, token = get_turso_credentials()
    raw_conn = libsql.connect(database=url, auth_token=token)
    return TursoConnection(raw_conn)
