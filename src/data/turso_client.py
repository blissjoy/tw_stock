"""Turso（libsql）連線層：把「目前該用哪個 Turso Python 套件」的細節隔離在這一個檔案，
之後套件名稱/API 若又變動，只需要修改這裡，不影響 storage.py 或呼叫端。

⚠️ 這裡的套件選擇是實測過的結果，不是文件查證的推測：官方文件當時查到的新版 `libsql`
套件（PyO3/Rust原生binding）在本機 Python 3.14 環境下沒有預編譯wheel，需要Rust+cmake
從原始碼編譯，安裝失敗；改用舊版但**純Python實作**的 `libsql-client`（不需要編譯任何原生
擴充），實測可正常安裝與運作，因此採用這個。若之後 `libsql-client` 停止維護導致無法安裝，
一樣只需要改這個檔案。

`libsql_client.ClientSync` 的介面實測後發現與 sqlite3.Connection 差異不小，此檔案負責
把差異抹平，讓 storage.py 完全不用知道底層是哪個套件：
  - `execute()` 回傳的是 `ResultSet`（有 `.rows`/`.columns`），不是 sqlite3 的 Cursor
    （沒有 `.fetchone()`/`.fetchall()`/`.description`），這裡包一層 `_ResultSetCursor` 補齊。
  - 沒有 `.commit()`（每個 statement 各自即時生效），`TursoConnection.commit()` 保持no-op即可。
  - 沒有 `.executemany()`，但有 `.batch(list_of_(sql, params))` 可以一次網路請求送多筆，
    這裡分chunk呼叫，避免一次batch塞進去幾十萬筆造成單次請求過大。
  - 具名參數(`:name`)實測`.execute()`可以直接吃dict，但 `.batch()` 逐項的具名參數支援
    沒有把握跟`.execute()`一致，所以統一都先轉成 `?` 位置參數再送出，行為不依賴這個細節。
"""

from __future__ import annotations

import re
import time
from typing import Any

from src.data.config import get_turso_credentials

_NAMED_PARAM_RE = re.compile(r":(\w+)")
_BATCH_CHUNK_SIZE = 500  # 每次.batch()呼叫送幾筆，避免單次HTTP請求過大
_SCHEMA_RETRY_ATTEMPTS = 3
_SCHEMA_RETRY_DELAY_SECONDS = 1.0


def _to_positional(sql: str, row: dict) -> tuple[str, tuple]:
    """把 `:name` 具名參數改寫成 `?` 位置參數，回傳 (改寫後SQL, 依序排好的參數tuple)。"""
    names: list[str] = []

    def _replace(match: re.Match) -> str:
        names.append(match.group(1))
        return "?"

    positional_sql = _NAMED_PARAM_RE.sub(_replace, sql)
    return positional_sql, tuple(row[name] for name in names)


class _ResultSetCursor:
    """把 libsql_client 的 ResultSet 包成 sqlite3.Cursor 慣用介面(fetchone/fetchall/description)，
    讓 storage.py 既有的呼叫方式（例如 `fetchone()[0]`、`[d[0] for d in cur.description]`）
    不必為了這個client另外改寫。"""

    def __init__(self, rows: list, columns: list[str]) -> None:
        self._rows = [tuple(r) for r in rows]
        self.description = [(name,) for name in columns]
        self._pos = 0

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list[tuple]:
        remaining = self._rows[self._pos:]
        self._pos = len(self._rows)
        return remaining


class TursoConnection:
    """包一層轉接器：統一介面(execute/executemany/executescript/commit/close)，
    不論底層 libsql client 實際支不支援具名參數、executemany或原生cursor介面，
    storage.py 都能正常呼叫。
    """

    def __init__(self, raw_conn: Any) -> None:
        self._raw = raw_conn

    def _wrap_result(self, result: Any):
        if hasattr(result, "rows") and hasattr(result, "columns"):
            return _ResultSetCursor(result.rows, list(result.columns))
        return result  # 底層已經是sqlite3風格cursor時（例如測試用sqlite3.Connection）直接透傳

    def execute(self, sql: str, params: dict | tuple | None = None):
        if isinstance(params, dict):
            sql, params = _to_positional(sql, params)
        if params is None:
            result = self._raw.execute(sql)
        else:
            result = self._raw.execute(sql, list(params))
        return self._wrap_result(result)

    def executemany(self, sql: str, rows: list[dict]) -> None:
        """優先使用底層連線的批次寫入能力（`.batch()`分chunk，或原生`.executemany()`），
        避免每一列都各自打一次網路請求；底層都不支援才退回逐列呼叫execute()。"""
        if not rows:
            return
        if isinstance(rows[0], dict):
            positional_sql, first_params = _to_positional(sql, rows[0])
            params_list = [first_params] + [_to_positional(sql, row)[1] for row in rows[1:]]
        else:
            positional_sql, params_list = sql, [tuple(r) for r in rows]

        if hasattr(self._raw, "batch"):
            for start in range(0, len(params_list), _BATCH_CHUNK_SIZE):
                chunk = params_list[start:start + _BATCH_CHUNK_SIZE]
                self._raw.batch([(positional_sql, list(params)) for params in chunk])
        elif hasattr(self._raw, "executemany"):
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
            if not statement:
                continue
            self._execute_schema_statement_with_retry(statement)

    def _execute_schema_statement_with_retry(self, statement: str) -> None:
        """schema.sql 裡的每個statement（PRAGMA、CREATE TABLE/INDEX IF NOT EXISTS）套用起來
        都是冪等的，重複執行是安全的。但實測發現：多個process同時對Turso執行ensure_schema()
        時（例如daily_pipeline.py背景在跑，使用者手動又跑一次，或Streamlit Cloud冷啟動時
        剛好撞上），libsql_client 0.3.1（已停止維護）對伺服器端在併發衝突下回傳的錯誤回應
        處理不完善，會在libsql_client/http.py內部丟出未預期的裸KeyError('result')而不是
        正常的錯誤訊息——這是套件本身在瞬間併發衝突下的bug，跟這句statement本身合不合法
        無關（先前版本只放行statement文字裡包含"IF NOT EXISTS"的情況，但schema.sql開頭的
        `PRAGMA foreign_keys = ON;`不含這段文字，一樣會撞到同一個底層bug，卻沒被涵蓋到）。
        短暫重試就能恢復；重試次數用完仍失敗，代表不是這個瞬間衝突，照常往外拋出，不會
        掩蓋真正的錯誤（真正的SQL語法錯誤不會是KeyError，會是libsql_client自己的例外類別）。
        """
        for attempt in range(_SCHEMA_RETRY_ATTEMPTS):
            try:
                self._raw.execute(statement)
                return
            except KeyError:
                if attempt == _SCHEMA_RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(_SCHEMA_RETRY_DELAY_SECONDS * (attempt + 1))

    def commit(self) -> None:
        if hasattr(self._raw, "commit"):
            self._raw.commit()

    def close(self) -> None:
        self._raw.close()


def _force_https_scheme(url: str) -> str:
    """把 `libsql://`（會被libsql_client轉譯成`wss://` hrana websocket協議）強制換成
    `https://`（純HTTP batch API，非streaming）。

    ⚠️ 實測發現：這個套件版本(libsql-client 0.3.1，已停止維護)的websocket實作對目前
    Turso伺服器不穩定，會隨機出現連線hang住或`aiohttp.client_exceptions.WSServerHandshakeError:
    400 Invalid response status`的錯誤；改用同一個host的https scheme實測穩定且更快，
    因此固定換成https，不使用 TURSO_DATABASE_URL 原本設定的scheme。
    """
    return "https://" + url.split("://", 1)[1]


def get_connection() -> TursoConnection:
    """連線到 Turso 雲端資料庫，回傳的物件介面與 sqlite3.Connection 相容，
    可直接傳給 src/data/storage.py 的所有 upsert_*/init_db 函式使用。
    """
    import libsql_client

    url, token = get_turso_credentials()
    raw_conn = libsql_client.create_client_sync(_force_https_scheme(url), auth_token=token)
    return TursoConnection(raw_conn)
