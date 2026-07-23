"""PySide6桌面版進入點：`python desktop/main.py`。

2026-07-23架構調整（改回本機優先）：預設走本機 `data/tw_stock.db`，不需要另外設定
LOCAL_DB_PATH環境變數就能直接跑（`os.environ.setdefault`，若使用者已經自己設定過
LOCAL_DB_PATH或想接回Turso，這裡不會覆蓋）。跟Streamlit版(`dashboard/app.py`)共用同一套
`src/data/connection.py`判斷邏輯，之後要切換DB只需要改環境變數，不用改程式碼。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LOCAL_DB_PATH", str(ROOT / "data" / "tw_stock.db"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.main_window import MainWindow  # noqa: E402


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.resize(1440, 960)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
