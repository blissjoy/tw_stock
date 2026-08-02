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
# 2026-08-02新增：庫存清單/觀察清單用獨立的DB檔案(不混進tw_stock.db)，比照LOCAL_DB_PATH
# 的寫法設定預設值，見src/data/connection.py的get_default_portfolio_connection()。
os.environ.setdefault("PORTFOLIO_DB_PATH", str(ROOT / "data" / "portfolio.db"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.main_window import MainWindow  # noqa: E402


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 2026-08-02：使用者反映版面太擠、字太小，全域字級調大兩級。桌面版所有widget都沒有
    # 自己setFont()/font-size樣式覆寫過，一律cascade自QApplication預設字型，改這裡就能
    # 讓所有Qt原生元件統一放大，不用逐一改widget。
    font = app.font()
    font.setPointSize(font.pointSize() + 2)
    app.setFont(font)

    window = MainWindow()
    # ⚠️ 2026-08-02修正：原本固定resize(1440, 960)，字級調大兩級後版面自然高度變高，
    # 960px在解析度較低的螢幕(例如筆電常見的1366x768，扣掉工作列後可視高度更矮)會
    # 超出可視範圍，開起來一部分視窗被切在螢幕外。改成依目前螢幕的可視範圍
    # (availableGeometry()，已經排除工作列等系統保留區域)動態算出視窗大小——
    # 寬高都取「理想值(1440x960)」跟「可視範圍90%」兩者的較小值，畫面大的螢幕維持
    # 原本大小不會過度放大，畫面小的螢幕會自動縮小到塞得進可視範圍，並置中顯示。
    screen = app.primaryScreen()
    available = screen.availableGeometry() if screen is not None else None
    if available is not None:
        width = min(1440, int(available.width() * 0.9))
        height = min(960, int(available.height() * 0.9))
        window.resize(width, height)
        window.move(available.center() - window.rect().center())
    else:
        window.resize(1440, 960)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
