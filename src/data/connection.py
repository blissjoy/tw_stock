"""前端無關的資料庫連線選擇邏輯：依 LOCAL_DB_PATH 環境變數決定要開本機sqlite還是連線Turso。

2026-07-23架構調整（改回本機優先、PySide6桌面版）之前，這段判斷邏輯寫在`dashboard/app.py`
的`get_conn()`裡，只有Streamlit能用；搬出來後，Streamlit(`dashboard/app.py`)與PySide6
(`desktop/`)兩個前端都呼叫同一份，「DB隨前端切換」不需要在各自前端重複實作一次。

`ensure_schema()`刻意不在這裡呼叫Turso分支——本機sqlite的`storage.init_db()`內部已經會
呼叫，一定成功；但Turso可能因為額度用完等原因寫入被封鎖（見`src/data/turso_client.py`的
說明），呼叫端各自決定要不要try/except、失敗時如何呈現（例如儀表板用警告訊息、桌面版用
狀態列），不在這個共用函式裡假設任何特定的錯誤處理方式。
"""

from __future__ import annotations

import os

from src.data import storage, turso_client


def get_default_connection(local_db_path: str | None = None):
    """local_db_path未傳入時，讀取LOCAL_DB_PATH環境變數；有值就開本機sqlite檔案，
    否則連線Turso。"""
    local_db_path = local_db_path or os.environ.get("LOCAL_DB_PATH")
    if local_db_path:
        # check_same_thread=False：快取的連線可能被不同執行緒重用(Streamlit的
        # @st.cache_resource、或PySide6背景QThread呼叫manual pipeline時)，sqlite3預設會
        # 拒絕跨thread共用同一個connection。
        return storage.init_db(local_db_path, check_same_thread=False)
    return turso_client.get_connection()
