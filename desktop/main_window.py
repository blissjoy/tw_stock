"""PySide6桌面版主視窗：跟`dashboard/app.py`(Streamlit)共用同一套底層——`src/presentation/
chart_data.py`的圖表資料組裝、`src/patterns/chart_overlays.py`的切線/支撐壓力、
`src/screener/daily_screener.py`的選股、`scripts/daily_pipeline.py`的`run_daily_pipeline()`——
只是換一層UI框架，行為（均線/切線軌道線/支撐壓力可個別切換、候選清單點選、手動查詢、最新
交易日K棒分析）刻意跟Streamlit版對齊。

圖表用`QWebEngineView`顯示Plotly figure的`to_html()`輸出（`include_plotlyjs=True`整包內嵌，
不用CDN），桌面版離線也能看圖，不用在Qt原生元件裡重畫一次K線/均線/切線邏輯。
"""

from __future__ import annotations

import html
import re
import sqlite3
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path

import markdown
import pandas as pd
from PySide6.QtCore import QDate, QEvent, QRect, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyleOptionButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from src.data import finmind_client, portfolio_storage, storage
from src.data.connection import get_default_connection, get_default_portfolio_connection
from src.data.yfinance_client import TAIEX_STOCK_ID
from src.indicators.huang_chip_signals import COLOR_BUY, COLOR_SELL
from src.indicators.institutional_flow import INSTITUTIONAL_STREAK_THRESHOLD
from src.indicators.moving_average import FULL_PERIODS
from src.patterns import chart_overlays, latest_day_summary
from src import rule_docs
from src.presentation import chart_data, huang_chip_data, pipeline_status, portfolio_data, stock_detail_data
from src.presentation.chart_render import render_chart_html
from src.screener.daily_screener import (
    analyze_stock_signals,
    recompute_indicators_for_range,
    run_screen_and_store,
    run_screen_and_store_for_range,
    summarize_signal_matches,
)

TAIEX_DISPLAY_NAME = "台股加權指數"

# 分頁索引，對應_build_ui()裡addTab()的呼叫順序：大盤/選股/個股資訊/產業輪動/庫存清單/
# 觀察清單/回補資料。
TAB_MARKET = 0
TAB_SCREENER = 1
TAB_STOCK_DETAIL = 2
TAB_INDUSTRY_ROTATION = 3
TAB_INVENTORY = 4
TAB_WATCHLIST = 5
TAB_BACKFILL = 6

# 「選股」分頁「市場：」下拉選單顯示文字對應chart_data.load_stock_universe_for_date()
# 的market參數值；"全部"不在這裡，get()查不到就是None(不限制)。
_MARKET_FILTER_VALUES = {"上市": "TWSE", "上櫃": "TPEx"}

# 候選清單「訊號(信心%)」欄位最多顯示幾條規則，超過的話最後一行改顯示「(...+n more)」——
# 2026-08-03新增，使用者反映有些股票同時符合十幾條規則時，儲存格會被撐得很長。信心分數
# 加總排序(見chart_data.load_stock_universe_for_date())仍然吃全部規則，不受這裡的顯示
# 上限影響，只是「顯示」被截斷，排序依據的資料沒有跟著縮水。
CANDIDATE_SIGNAL_MAX_LINES = 5

# 觀察清單表格的欄位結構(見_populate_portfolio_table())：
# 股票代號/名稱/現價/漲跌幅(%)/SAR狀態/SAR距離%
# 2026-08-04：使用者要求拿掉「備註」欄位(觀察清單用不到，庫存清單的備註是
# _INVENTORY_TREE_HEADERS另一份獨立清單，不受影響)。
# 2026-08-06：使用者反映觀察清單不是真的持股，成本價/持股數/市值/帳面損益/報酬率
# 放在這裡沒有意義，拿掉這5欄(庫存清單改用QTreeWidget/_populate_inventory_tree()，
# 不受影響，這裡從一開始就只有觀察清單在用，「共用」是2026-08-02改版前留下來的
# 舊說法)。
_PORTFOLIO_NUMERIC_COLUMNS = {2, 3, 5}
_PORTFOLIO_BASE_COLUMN_COUNT = 6

# 黃豐凱籌碼分析法(見src/indicators/huang_chip_signals.py，程式碼來源private)接在
# 觀察清單表格既有12欄之後的額外欄位——2026-08-04新增，只接進觀察清單，跟_PORTFOLIO_
# NUMERIC_COLUMNS/_build_portfolio_table()/_populate_portfolio_table()這組庫存清單
# 也在用的共用邏輯分開處理，不會影響庫存清單(之後真的要擴充到庫存清單，屆時再讓
# 庫存清單的表格也接上同一組欄位，這裡的函式已經是可以直接重用的獨立單元)。J欄
# (大量K參考)原始程式碼裡沒有邏輯(手動欄位)，不顯示。
_HUANG_CHIP_HEADERS = [
    "投信", "外資", "大戶週變化", "散戶週變化", "均線狀態", "週K型態",
    "40日外資", "40日投信", "20日外資", "20日投信", "10日外資", "10日投信", "5日外資", "5日投信",
]
_HUANG_CHIP_NUMERIC_COLUMNS = {
    _PORTFOLIO_BASE_COLUMN_COUNT + i
    for i, h in enumerate(_HUANG_CHIP_HEADERS)
    if h in ("40日外資", "40日投信", "20日外資", "20日投信", "10日外資", "10日投信", "5日外資", "5日投信")
}

# 觀察清單「欄位顯示」下拉選單(見_build_watchlist_tab())的分組定義：{顯示文字: 欄位
# 索引清單}——「技術面」/「籌碼面」是子選單，各自底下的欄位一起顯示/隱藏；其餘幾項
# 是扁平的單一欄位開關。「股票代號/名稱/現價/漲跌幅」是識別用欄位，永遠顯示，不放進
# 選單；「備註」使用者要求先不用做成可切換選項，維持恆常顯示。2026-08-06：成本價/
# 持股數/市值/帳面損益/報酬率欄位整組拿掉(理由見上面_PORTFOLIO_BASE_COLUMN_COUNT
# 的說明)，這裡不再有對應的切換選項。
_WATCHLIST_COLUMN_TOGGLE_GROUPS: dict[str, list[int]] = {}
_WATCHLIST_TECH_TOGGLE_COLUMNS = [4, 5]  # SAR狀態/SAR距離%
_WATCHLIST_CHIP_TOGGLE_COLUMNS = list(range(_PORTFOLIO_BASE_COLUMN_COUNT, _PORTFOLIO_BASE_COLUMN_COUNT + len(_HUANG_CHIP_HEADERS)))

# 黃豐凱籌碼分析法欄位的「雙列表頭」分類群組(見_build_watchlist_group_header_table())：
# 比照temp/鉸哥籌碼.jpg截圖的分類方式(法人近期籌碼/大戶散戶持股變化(週)/技術型態/
# 法人買賣超（張數）)，{顯示文字: (底色, 欄位索引清單)}——這4組是視覺分類，跟「欄位
# 顯示」下拉選單的顯示/隱藏開關粒度(籌碼面整組一起開關)是分開的兩件事，這裡只負責
# 標籤怎麼分組顯示，不影響開關邏輯。前面6欄(股票代號~SAR距離%)是既有欄位，不屬於
# 黃豐凱籌碼分析法，這排分類標籤在那個範圍留空。
_WATCHLIST_CHIP_GROUP_LABELS: list[tuple[str, str, list[int]]] = [
    ("法人近期籌碼", "#FCEBEB", [6, 7]),
    ("大戶/散戶持股變化(週)", "#EEEDFE", [8, 9]),
    ("技術型態", "#E1F5EE", [10, 11]),
    ("法人買賣超（張數）", "#FAEEDA", [12, 13, 14, 15, 16, 17, 18, 19]),
]

# 庫存清單改用QTreeWidget(彙總父列+可展開的批次明細子列，見_populate_inventory_
# tree())的欄位結構——父列/子列共用同一組欄，欄位語意見_build_inventory_tab()。
_INVENTORY_TREE_HEADERS = [
    "股票代號", "名稱", "買入日期", "現價", "漲跌幅(%)", "成本價", "持股數",
    "手續費", "市值", "預估賣出成本", "帳面損益", "報酬率(%)", "SAR狀態", "SAR距離%", "批次數", "備註",
]
_INVENTORY_TREE_NUMERIC_COLUMNS = {
    _INVENTORY_TREE_HEADERS.index(h)
    for h in [
        "現價", "漲跌幅(%)", "成本價", "持股數", "手續費", "市值", "預估賣出成本",
        "帳面損益", "報酬率(%)", "SAR距離%", "批次數",
    ]
}
_INVENTORY_TREE_LOT_COUNT_COLUMN = _INVENTORY_TREE_HEADERS.index("批次數")

# 「產業輪動」分頁改用QTreeWidget(2026-08-06新增，母子列結構跟_INVENTORY_TREE_
# HEADERS同一個精神，見_build_industry_rotation_tab())的欄位結構：父列(產業彙總)
# 用「產業別/股票代號」「漲跌幅(%)」(平均)「總成交張數」(合計)「股票數」，子列(個股
# 明細)用「產業別/股票代號」(放股票代號)「名稱」「成交/開盤/最高/最低/漲跌/漲跌幅(%)/
# 總成交張數」，「股票數」留空——兩種列共用同一組欄位定義，彼此不適用的欄位留空字串，
# 跟_format_inventory_row()的is_lot參數是同一個模式。
_INDUSTRY_TREE_HEADERS = [
    "產業別 / 股票代號", "名稱", "成交", "開盤", "最高", "最低", "漲跌", "漲跌幅(%)",
    "總成交張數", "股票數",
]
_INDUSTRY_TREE_NUMERIC_COLUMNS = {
    _INDUSTRY_TREE_HEADERS.index(h)
    for h in ["成交", "開盤", "最高", "最低", "漲跌", "漲跌幅(%)", "總成交張數", "股票數"]
}
_INDUSTRY_TREE_STOCK_COUNT_COLUMN = _INDUSTRY_TREE_HEADERS.index("股票數")


def _format_month_day(date_str: str) -> str:
    """"YYYY-MM-DD" -> "X月X日"(不補零)，供「個股資訊」分頁右上角的來源標籤使用。
    格式不符預期時原樣回傳，不拋例外——來源標籤只是輔助資訊，不應該因為格式問題讓
    整個畫面crash。
    """
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    return f"{d.month}月{d.day}日"


def _normalize_date_text(text: str) -> str:
    """把使用者輸入的日期文字盡量轉成"YYYY-MM-DD"——2026-08-02新增，供
    _StockEditDialog的「買入日期」欄位在離開焦點時自動轉格式，使用者不用自己
    手動打分隔符號。做法是先去掉所有非數字字元，只要剩下的數字恰好是8碼(不論
    原本是"20260802"、"2026/08/02"、"2026.08.02"哪種輸入方式，去掉分隔符號後
    都是同樣8碼數字)，就照"YYYYMMDD"解析回"YYYY-MM-DD"；解析失敗(不是8碼、
    或不是合法日期，例如月份13)原樣放回，不阻擋輸入——這個專案對輸入格式一律
    採「盡量幫忙轉，轉不了就放過」的寬鬆原則。
    """
    text = text.strip()
    if not text:
        return text
    digits_only = re.sub(r"\D", "", text)
    if len(digits_only) != 8:
        return text
    try:
        return datetime.strptime(digits_only, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return text


def _truncate_signal_lines(signal_name: str | None, max_lines: int = CANDIDATE_SIGNAL_MAX_LINES) -> str:
    """候選清單「訊號(信心%)」欄位的顯示文字：signal_name本身用"\\n"分隔多條規則(見
    chart_data.load_stock_universe_for_date())，超過max_lines條時只顯示前max_lines條，
    最後多一行"(...+n more)"，n是省略掉的規則數——2026-08-03新增，避免同時符合十幾條
    規則的股票把儲存格撐得很長。只影響顯示，完整內容仍然透過tooltip看得到，排序依據的
    信心分數加總(chart_data.py的_confidence_sum)吃的是原始signal_name、不受這裡截斷
    影響。
    """
    if not signal_name:
        return "-"
    lines = signal_name.split("\n")
    if len(lines) <= max_lines:
        return signal_name
    hidden = len(lines) - max_lines
    return "\n".join(lines[:max_lines]) + f"\n(...+{hidden} more)"


class _NumericTableWidgetItem(QTableWidgetItem):
    """支援依實際數值排序的QTableWidgetItem，取代候選清單表格點欄位標題排序時預設的
    字串排序——字串排序會把"10.00"排在"9.00"前面(逐字元比較，'1'<'9')、"1,000"這種
    千分位逗號跟"+2.83%"的正負號/百分比也都會被字面當成一般字元、算不出正確大小關係。
    用在進場價/停損價/漲跌幅/成交量這幾個數字欄位；股票代號/名稱/產業別/訊號欄位維持
    QTableWidgetItem預設的字串排序即可，不需要特別處理。
    """

    def __lt__(self, other: object) -> bool:
        self_value = self._parse(self.text())
        other_value = self._parse(other.text()) if isinstance(other, QTableWidgetItem) else None
        if self_value is None:
            return other_value is not None  # "-"(無資料)固定排最前面
        if other_value is None:
            return False
        return self_value < other_value

    @staticmethod
    def _parse(text: str) -> float | None:
        text = text.strip()
        if text in ("", "-"):
            return None
        try:
            return float(text.replace(",", "").replace("%", "").replace("+", ""))
        except ValueError:
            return None


class _NumericTreeWidgetItem(QTreeWidgetItem):
    """QTreeWidgetItem版的_NumericTableWidgetItem——2026-08-02新增，庫存清單改用
    QTreeWidget(彙總父列+可展開的批次明細子列)後，數值欄位排序需要同樣依實際數值
    (不是字串)排序，QTreeWidgetItem沒有現成的「目前排序欄位」參數，改用
    self.treeWidget().sortColumn()取得。"""

    def __lt__(self, other: object) -> bool:
        column = self.treeWidget().sortColumn() if self.treeWidget() else 0
        self_value = _NumericTableWidgetItem._parse(self.text(column))
        other_value = _NumericTableWidgetItem._parse(other.text(column)) if isinstance(other, QTreeWidgetItem) else None
        if self_value is None:
            return other_value is not None
        if other_value is None:
            return False
        return self_value < other_value


class _AutoHeightTabWidget(QTabWidget):
    """QTabWidget預設的sizeHint()/minimumSizeHint()不會只反映『目前顯示中』那一頁的
    高度，包在QScrollArea(setWidgetResizable=True)裡時，捲軸範圍抓不到正確的內容高度
    ——跟過去QSplitter不轉發子元件sizeHint變化是同一類問題(2026-07-29修正大盤分析截斷
    bug時發現的)。這裡覆寫成只回報目前分頁的高度，並在currentChanged時呼叫
    updateGeometry()通知外層layout重新查詢，是Qt對這個已知限制的標準workaround
    (只用到QWidget.sizeHint()/updateGeometry()這些公開API，不依賴任何內部屬性)。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(lambda _: self.updateGeometry())

    def sizeHint(self) -> QSize:
        return self._current_size(super().sizeHint(), "sizeHint")

    def minimumSizeHint(self) -> QSize:
        return self._current_size(super().minimumSizeHint(), "minimumSizeHint")

    def _current_size(self, base: QSize, method_name: str) -> QSize:
        current = self.currentWidget()
        if current is None:
            return base
        extra = self.tabBar().sizeHint().height() + 8
        page_height = getattr(current, method_name)().height()
        return QSize(base.width(), page_height + extra)


class _CollapsibleBox(QWidget):
    """可展開/收合的區塊容器：標題列是一個按鈕(顯示▼/▶+標題文字)，點擊切換內容
    widget的顯示/隱藏，預設展開。2026-08-03新增，供「個股明細」tab的5個區塊
    (交易資訊/法人買賣/主力進出/資券變化/大戶籌碼)各自獨立收合使用——使用者要求
    每個block都能展開收合，預設打開。

    用setVisible()切換內容widget，不是清空內容或改高度限制——Qt layout系統會
    自動因應子widget的visible狀態重新流動版面，外層QScrollArea(_build_stock_
    detail_tab()的detail_scroll)本來就是setWidgetResizable(True)，捲軸範圍會
    自動反映內容增減，不需要另外同步高度。
    """

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._expanded = True

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 8)
        outer_layout.setSpacing(0)

        self._toggle_btn = QPushButton(self._header_text())
        self._toggle_btn.setStyleSheet(
            "QPushButton { text-align: left; font-weight: bold; padding: 6px 10px; "
            "background-color: #f0f0f0; border: 1px solid #d5d5d5; }"
            "QPushButton:hover { background-color: #e6e6e6; }"
        )
        self._toggle_btn.clicked.connect(self._on_toggle)
        outer_layout.addWidget(self._toggle_btn)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(10, 8, 10, 8)
        outer_layout.addWidget(self.content)

    def _header_text(self) -> str:
        return f"{'▼' if self._expanded else '▶'}  {self._title}"

    def _on_toggle(self) -> None:
        self._expanded = not self._expanded
        self.content.setVisible(self._expanded)
        self._toggle_btn.setText(self._header_text())

    def expand(self) -> None:
        """展開這個區塊(已經展開的話不做任何事)——2026-08-04新增，供「總結分析」的
        跳轉連結呼叫：收合狀態下直接捲過去也看不到內容，跳轉前要先確保展開。"""
        if not self._expanded:
            self._on_toggle()


class _FloatingTopButton(QPushButton):
    """浮動在QScrollArea右下角、隨畫面捲動固定跟隨的「回頂部」按鈕。2026-08-04新增，
    供「個股分析」/「大盤分析」面板使用——內容經常很長，使用者反映捲到下面想快速回到
    最上面時，每次都要手動捲回去太麻煩。

    做法：parent直接設成目標scroll_area本身(不是它的viewport內容元件)，用手動
    move()定位到右下角固定邊距處，不透過scroll_area自己的layout管理(那是留給
    viewport/scrollbar用的)，靠raise_()確保疊在最上層。scroll_area大小變化時
    (例如視窗縮放)要重新定位，這裡用installEventFilter監聽Resize事件，不用另外
    繼承QScrollArea改寫resizeEvent(現有的detail_scroll/market_scroll都是直接用
    QScrollArea()建構，改成子類別牽動範圍較大)。
    """

    _MARGIN = 24

    def __init__(self, scroll_area: QScrollArea) -> None:
        super().__init__("⬆ 回頂部", scroll_area)
        self._scroll_area = scroll_area
        self.setStyleSheet(
            "QPushButton { background-color: rgba(41, 128, 185, 220); color: white; "
            "border: none; border-radius: 16px; padding: 8px 16px; font-weight: bold; }"
            "QPushButton:hover { background-color: rgba(41, 128, 185, 255); }"
        )
        self.clicked.connect(lambda: scroll_area.verticalScrollBar().setValue(0))
        scroll_area.installEventFilter(self)
        self._reposition()
        self.raise_()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._scroll_area and event.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    def _reposition(self) -> None:
        self.adjustSize()
        area_size = self._scroll_area.size()
        self.move(
            area_size.width() - self.width() - self._MARGIN,
            area_size.height() - self.height() - self._MARGIN,
        )


class _CheckableComboBox(QComboBox):
    """支援複選(打勾)的QComboBox，用法類似Excel欄位篩選——點下拉選單裡任一項目只是
    切換打勾狀態，選單不會因此關閉，可以連續勾選多個項目再一次收合。第一項固定是
    `ALL_LABEL`("全部")，跟其他項目互斥：勾選"全部"會自動取消其他所有項目；勾選任何
    其他項目會自動取消"全部"。使用者把最後一個具體項目取消勾選、變成完全沒有勾選時，
    自動退回勾選"全部"，避免「什麼都沒勾」被誤解成「篩出0筆」的空結果狀態。
    """

    ALL_LABEL = "全部"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setModel(QStandardItemModel(self))
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.view().pressed.connect(self._on_item_pressed)

    def hidePopup(self) -> None:
        # 攔截QComboBox內建「點了任一項目就收合下拉選單」的預設行為，讓使用者能連續
        # 勾選多個項目不用重複點開；選單本身仍是獨立的popup視窗，點擊選單以外的地方
        # 還是會透過視窗系統自己的失焦機制關閉，不受這裡覆寫影響。
        pass

    def set_items(self, items: list[str]) -> None:
        self.model().clear()
        all_item = QStandardItem(self.ALL_LABEL)
        all_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        all_item.setData(Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        self.model().appendRow(all_item)
        for text in items:
            item = QStandardItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
            self.model().appendRow(item)
        self._refresh_display_text()

    def checked_items(self) -> list[str]:
        """回傳目前勾選的具體項目清單(不含"全部")；勾選"全部"或什麼都沒勾時回傳空list，
        代表「不限制」，呼叫端看到空list就不用套用這個篩選條件。"""
        items = []
        for row in range(1, self.model().rowCount()):
            item = self.model().item(row)
            if item.checkState() == Qt.CheckState.Checked:
                items.append(item.text())
        return items

    def _on_item_pressed(self, index) -> None:
        item = self.model().itemFromIndex(index)
        new_state = (
            Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        item.setCheckState(new_state)
        if item.text() == self.ALL_LABEL:
            if new_state == Qt.CheckState.Checked:
                for row in range(1, self.model().rowCount()):
                    self.model().item(row).setCheckState(Qt.CheckState.Unchecked)
        elif new_state == Qt.CheckState.Checked:
            self.model().item(0).setCheckState(Qt.CheckState.Unchecked)
        elif not self.checked_items():
            self.model().item(0).setCheckState(Qt.CheckState.Checked)
        self._refresh_display_text()

    def _refresh_display_text(self) -> None:
        selected = self.checked_items()
        if not selected:
            text = self.ALL_LABEL
        elif len(selected) == 1:
            text = selected[0]
        else:
            text = f"已選{len(selected)}項：" + "、".join(selected)
        self.lineEdit().setText(text)


class _CheckableHeaderView(QHeaderView):
    """表頭第0欄顯示一個checkbox，點擊切換「全選/取消全選」——2026-08-04新增，供
    「選股」分頁候選清單表格的勾選欄(見_build_screener_tab())使用。QHeaderView
    本身沒有原生的checkbox支援，這裡用最小的自訂繪製(paintSection)+點擊判斷
    (mousePressEvent)達成，不用整合第三方套件；其餘欄位(logical_index != 0)
    完全交回父類別處理，不影響既有的排序/欄寬調整行為。
    """

    toggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._checked = False

    @staticmethod
    def _checkbox_rect(section_rect: QRect) -> QRect:
        size = 16
        x = section_rect.x() + (section_rect.width() - size) // 2
        y = section_rect.y() + (section_rect.height() - size) // 2
        return QRect(x, y, size, size)

    def paintSection(self, painter, rect, logical_index) -> None:
        super().paintSection(painter, rect, logical_index)
        if logical_index != 0:
            return
        painter.save()
        option = QStyleOptionButton()
        option.rect = self._checkbox_rect(rect)
        option.state = QStyle.StateFlag.State_Enabled
        option.state |= QStyle.StateFlag.State_On if self._checked else QStyle.StateFlag.State_Off
        self.style().drawControl(QStyle.ControlElement.CE_CheckBox, option, painter)
        painter.restore()

    def mousePressEvent(self, event) -> None:
        if self.logicalIndexAt(event.pos()) == 0:
            self._checked = not self._checked
            self.updateSection(0)
            self.toggled.emit(self._checked)
            return
        super().mousePressEvent(event)

    def set_checked_silently(self, checked: bool) -> None:
        """外部同步狀態用(例如_reload_candidates()重新整理表格後，勾選欄全部重置成
        未勾選，表頭checkbox要跟著反映)——不透過setChecked這種會觸發toggled訊號的
        命名，避免呼叫端不小心接成迴圈(重新整理→重置表頭→又觸發一次全選/全不選)。
        """
        if self._checked != checked:
            self._checked = checked
            self.updateSection(0)


class _StockEditDialog(QDialog):
    """庫存清單／觀察清單共用的新增/編輯對話框：股票代號＋(買入日期)＋(成本價＋
    持股數＋手續費)＋備註。2026-08-02新增(移植ref-project的inventory_list.py/
    watchlist.py，兩者的編輯dialog欄位結構幾乎相同，這裡合併成一個共用class)。

    股票代號欄位離開焦點時用chart_data.resolve_stock_id()即時查名稱顯示在旁邊，
    查無此代號時只顯示提示文字、不阻擋送出——使用者可能想追蹤還沒被本系統資料庫
    收錄的股票(例如剛上市)，不應該完全卡死；送出時仍會嘗試resolve一次，能解析
    成功就存標準化後的stock_id，失敗就存使用者輸入的原始文字。

    ⚠️ 2026-08-02改版：使用者反映庫存實際上是分批買入，同一檔股票可能有好幾筆
    各自獨立的批次紀錄——「編輯」模式現在是編輯「某一筆批次」(用lot id識別，不是
    股票代號)，股票代號欄位唯讀的原因也從「是主鍵」改成「批次的股票代號不該事後
    亂改，要換股票應該是刪除重建，不是編輯」。is_inventory控制要不要顯示「買入
    日期」「手續費」這兩個庫存專屬欄位——觀察清單不是真的持股，不需要記錄買入
    日期/手續費，只有庫存清單的呼叫端會傳is_inventory=True。

    ⚠️ 2026-08-06改版：「成本價」「持股數」也改成is_inventory才顯示——使用者反映
    觀察清單不是真的持股，參考成本價/參考股數(連帶表格上衍生出來的市值/帳面損益/
    報酬率)放在那裡沒有意義，觀察清單的新增/編輯對話框現在只剩股票代號＋備註兩欄，
    values()回傳的cost_price/shares固定是None。
    """

    def __init__(
        self, conn, title: str, initial: dict | None = None, parent=None, is_inventory: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._conn = conn
        self._result_stock_id: str = ""
        self._is_inventory = is_inventory
        initial = initial or {}

        layout = QFormLayout(self)

        id_row = QHBoxLayout()
        self.stock_id_input = QLineEdit(initial.get("stock_id", ""))
        self.stock_id_input.setPlaceholderText("例如 2330 或 台積電")
        if "stock_id" in initial:
            self.stock_id_input.setReadOnly(True)  # 編輯模式是編輯「這一批」，股票代號不能改
        self.stock_id_input.editingFinished.connect(self._update_name_label)
        id_row.addWidget(self.stock_id_input)
        self.name_label = QLabel("")
        self.name_label.setStyleSheet("color: #666666;")
        id_row.addWidget(self.name_label)
        layout.addRow("股票代號：", id_row)

        self.buy_date_input = QLineEdit(initial.get("buy_date") or "")
        if is_inventory:
            self.buy_date_input.setPlaceholderText("YYYY-MM-DD，可留空，也可以直接打8碼數字如20260802")
            # 離開這個欄位(Tab跳下一格/點別的地方)時自動把"20260802"這類8碼數字
            # 轉成"2026-08-02"，不用使用者自己手動加分隔符號，見_normalize_date_
            # text()的說明。
            self.buy_date_input.editingFinished.connect(self._normalize_buy_date)
            layout.addRow("買入日期：", self.buy_date_input)

        # 2026-08-06修正：成本價/持股數(連帶市值/帳面損益/報酬率)只有庫存清單需要
        # (真的有持股才有成本基礎可言)，觀察清單不是真的持股，使用者反映「參考成本價/
        # 參考股數/市值/帳面損益/報酬率」放在觀察清單沒有意義，改成只在is_inventory
        # 時才建立這兩個輸入框；觀察清單完全不收集這兩個值(values()固定回傳None，
        # 見下面的說明)。
        self.cost_price_input: QDoubleSpinBox | None = None
        self.shares_input: QSpinBox | None = None
        self.fee_estimate_label = QLabel("")
        if is_inventory:
            # QDoubleSpinBox/QSpinBox沒有原生的「留空」狀態，用setSpecialValueText()
            # 讓數值等於最小值(0)時顯示提示文字取代"0.00"，values()裡把0視為None——
            # 成本價/持股數/手續費為0沒有實際意義，這個簡化不會誤傷真實情境。
            self.cost_price_input = QDoubleSpinBox()
            self.cost_price_input.setRange(0, 9_999_999)
            self.cost_price_input.setDecimals(2)
            self.cost_price_input.setSpecialValueText("（未填）")
            self.cost_price_input.setValue(initial.get("cost_price") or 0)
            layout.addRow("成本價：", self.cost_price_input)

            self.shares_input = QSpinBox()
            self.shares_input.setRange(0, 999_999_999)
            self.shares_input.setSpecialValueText("（未填）")
            self.shares_input.setValue(int(initial.get("shares") or 0))
            layout.addRow("持股數：", self.shares_input)

            # 手續費：2026-08-02改版，使用者反映庫存/證券app本來就內含這筆費用，
            # 新增庫存時不應該還要自己查來填——改成系統依成本價×股數自動估算
            # (見src/presentation/portfolio_data.py的estimate_buy_fee())，這裡
            # 只顯示估算結果，不是可編輯欄位；成本價/股數改變時即時重算顯示。
            # 會計入帳面損益/報酬率的成本基礎(見_merge_holdings_with_snapshot())。
            self.fee_estimate_label.setStyleSheet("color: #666666;")
            self.cost_price_input.valueChanged.connect(self._update_fee_estimate)
            self.shares_input.valueChanged.connect(self._update_fee_estimate)
            layout.addRow("預估買入手續費：", self.fee_estimate_label)

        self.note_input = QLineEdit(initial.get("note") or "")
        layout.addRow("備註：", self.note_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self._update_name_label()
        if is_inventory:
            self._update_fee_estimate()

    def _update_name_label(self) -> None:
        query = self.stock_id_input.text().strip()
        resolved = chart_data.resolve_stock_id(self._conn, query) if query and self._conn is not None else None
        if resolved:
            name = chart_data.get_stock_name(self._conn, resolved)
            self.name_label.setText(f"{resolved} {name}" if name else resolved)
        elif query:
            self.name_label.setText("（查無此股票代號，仍可儲存）")
        else:
            self.name_label.setText("")

    def _update_fee_estimate(self) -> None:
        """這裡只估算買進手續費(計入成本基礎，見estimate_buy_fee())——「如果現在
        賣出」要另外付的手續費+證交稅(estimate_sell_cost())會隨現價每天變動，不是
        買入當下就能決定的固定數字，不在這個新增/編輯批次的對話框估算，而是列表
        畫面「預估賣出成本」欄位即時算給使用者看(見_merge_holdings_with_
        snapshot())。"""
        fee = portfolio_data.estimate_buy_fee(self.cost_price_input.value() or None, self.shares_input.value() or None)
        if fee is None:
            self.fee_estimate_label.setText("（填成本價與持股數後自動估算）")
        else:
            self.fee_estimate_label.setText(f"{fee:,} 元（依成本價×股數自動估算，計入成本基礎）")

    def _normalize_buy_date(self) -> None:
        self.buy_date_input.setText(_normalize_date_text(self.buy_date_input.text()))

    def _on_accept(self) -> None:
        query = self.stock_id_input.text().strip()
        if not query:
            QMessageBox.warning(self, "請輸入股票代號", "股票代號不能留空。")
            return
        # 保險起見在送出前再轉一次格式——正常情況下editingFinished在跳到「確定」
        # 按鈕時就已經觸發過，這裡是防呆(例如某些平台上按Enter直接送出、沒有真正
        # 經過焦點轉移事件的情況)。
        if self._is_inventory:
            self._normalize_buy_date()
        buy_date = self.buy_date_input.text().strip()
        if self._is_inventory and buy_date:
            try:
                datetime.strptime(buy_date, "%Y-%m-%d")
            except ValueError:
                # 格式不符只提醒、不阻擋送出——跟股票代號解析不到時的處理方式一致，
                # 這個專案對使用者輸入格式的態度一律是「提示但不擋」。
                if QMessageBox.question(
                    self, "買入日期格式", f"「{buy_date}」不是YYYY-MM-DD格式，仍要儲存嗎？",
                ) != QMessageBox.StandardButton.Yes:
                    return
        resolved = chart_data.resolve_stock_id(self._conn, query) if self._conn is not None else None
        self._result_stock_id = resolved or query
        self.accept()

    def values(self) -> dict:
        """呼叫端在dialog.exec()回傳Accepted後呼叫，取得使用者輸入的結果。fee是
        系統自動估算的結果(見_update_fee_estimate()/portfolio_data.estimate_
        buy_fee())，不是使用者輸入，觀察清單(is_inventory=False)不需要這個概念，
        固定回傳None。"""
        cost_price = self.cost_price_input.value() or None if self.cost_price_input is not None else None
        shares = self.shares_input.value() or None if self.shares_input is not None else None
        return {
            "stock_id": self._result_stock_id,
            "buy_date": self.buy_date_input.text().strip() or None,
            "cost_price": cost_price,
            "shares": shares,
            "fee": portfolio_data.estimate_buy_fee(cost_price, shares) if self._is_inventory else None,
            "note": self.note_input.text().strip(),
        }


class PipelineWorker(QThread):
    """背景執行緒呼叫run_daily_pipeline()，避免手動抓取按鈕卡住UI主執行緒。

    刻意在這裡另外開一條獨立連線，不重用MainWindow.conn——同一個sqlite3連線物件不應該被
    主執行緒(畫面互動)跟背景執行緒(抓取寫入)同時使用，即使開連線時給了check_same_thread=False
    也一樣；各自獨立連線，SQLite自己的檔案鎖機制就足夠處理寫入時的序列化，不需要在Python
    這層另外加鎖。
    """

    finished_ok = Signal(int)
    failed = Signal(str)
    progress = Signal(str, int, int)  # (stage："TWSE"或"TPEx", 已處理檔數, 總檔數)

    def run(self) -> None:
        from scripts.daily_pipeline import run_daily_pipeline

        conn = None
        try:
            conn = get_default_connection()
            candidates = run_daily_pipeline(
                conn, dry_run=False,
                on_progress=lambda stage, done, total: self.progress.emit(stage, done, total),
            )
            self.finished_ok.emit(len(candidates))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            if conn is not None:
                conn.close()


class WatchlistExportWorker(QThread):
    """背景執行緒呼叫watchlist_export.export_all_watchlist_groups()+
    export_candidate_list()，避免「匯出到Google Sheet」按鈕卡住UI主執行緒——這是
    網路呼叫(Google Sheets API)，加上OAuth token可能需要刷新，耗時不可預期。跟
    PipelineWorker同一個理由，開獨立連線，不重用MainWindow.conn/portfolio_conn
    (同一個sqlite3連線物件不該被主執行緒跟背景執行緒同時使用)。

    2026-08-04新增：使用者要求「更新觀察清單時，選股清單也一起更新上去，分成
    不同的sheet」——選股清單匯出到同一個試算表底下另一個固定名稱的分頁，跟
    scripts/daily_pipeline.py的chip_refresh區塊接同一組函式。
    """

    finished_ok = Signal(int)
    failed = Signal(str)

    def run(self) -> None:
        from src.presentation import watchlist_export

        main_conn = None
        portfolio_conn = None
        try:
            main_conn = get_default_connection()
            portfolio_conn = get_default_portfolio_connection()
            count = watchlist_export.export_all_watchlist_groups(main_conn, portfolio_conn)
            watchlist_export.export_candidate_list(main_conn)
            self.finished_ok.emit(count)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            if main_conn is not None:
                main_conn.close()
            if portfolio_conn is not None:
                portfolio_conn.close()


class HolderShareFetchWorker(QThread):
    """背景執行緒即時補抓「觀察清單裡本地DB完全查無F/G資料」的股票——通常是每日
    17:00排程(daily_pipeline.py的--chip-refresh，實際呼叫
    src/data/holder_shares_sync.py的refresh_watchlist_holder_shares())執行完之後
    才被加入觀察清單的新股票，要等到隔天才會被每日排程涵蓋到。見MainWindow.
    _maybe_fetch_missing_holder_shares()。跟PipelineWorker/WatchlistExportWorker
    同一個理由，開獨立連線，不重用MainWindow.conn。
    """

    finished_ok = Signal(int)
    failed = Signal(str)

    def __init__(self, stock_ids: list[str]) -> None:
        super().__init__()
        self._stock_ids = stock_ids

    def run(self) -> None:
        from src.data.holder_shares_sync import fetch_and_store_holder_shares

        conn = None
        try:
            conn = get_default_connection()
            count = fetch_and_store_holder_shares(conn, self._stock_ids)
            self.finished_ok.emit(count)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            if conn is not None:
                conn.close()


class BackfillWorker(QThread):
    """背景執行緒跑「回補資料」分頁的整合回補流程：大盤股價→個股股價/法人/資券→均線/
    SAR快取重算→(選填)歷史候選清單重算，依序執行、逐步emit進度文字讓UI的log區塊顯示。
    跟其他Worker同一個理由，開獨立連線，不重用MainWindow.conn。

    2026-08-04設計定案：大盤跟個股共用同一個「開始回補」動作(不是分開兩個按鈕)，實際
    處理哪些項目由呼叫端在params裡打勾決定——使用者的理由是「用到回補功能的情境通常是
    發現缺資料或需要更早期歷史，這時候會想同時補大盤+個股」。

    支援用requestInterruption()中途取消：每個階段之間、以及TWSE/TPEx回補內部的逐日/
    逐股迴圈都會檢查isInterruptionRequested()，偵測到就提早結束並emit cancelled——回補
    本身是逐筆upsert，中途停止不會留下損壞資料，只是範圍沒跑完。

    params需含：start/end(YYYY-MM-DD字串)、force_overwrite(bool)、
    stock_id_filter(set[str] | None，None代表全市場)、taiex_price/stock_price/
    stock_institutional/stock_margin/recompute_candidates(bool)。
    """

    progress = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, params: dict) -> None:
        super().__init__()
        self._params = params

    def run(self) -> None:
        from datetime import date as _date

        from scripts import backfill_history
        from scripts.backfill_taiex import backfill_taiex_range

        conn = None
        try:
            conn = get_default_connection()
            p = self._params
            start, end = p["start"], p["end"]
            start_d, end_d = _date.fromisoformat(start), _date.fromisoformat(end)
            force_overwrite = p["force_overwrite"]
            stock_id_filter = p["stock_id_filter"]
            summary: dict = {}
            affected_stock_ids: set[str] = set()

            if p["taiex_price"]:
                self.progress.emit("開始回補大盤股價...")
                written = backfill_taiex_range(conn, start, end, force_overwrite=force_overwrite)
                summary["taiex_dates"] = len(written)
                self.progress.emit(f"大盤股價回補完成：{len(written)}筆")

            if self.isInterruptionRequested():
                self.cancelled.emit()
                return

            any_stock_item = p["stock_price"] or p["stock_institutional"] or p["stock_margin"]
            if any_stock_item:
                rows = conn.execute(
                    "SELECT stock_id, name, industry, market FROM stocks WHERE market IN ('TWSE', 'TPEx')"
                ).fetchall()
                known = {r[0]: {"stock_id": r[0], "name": r[1], "industry": r[2], "market": r[3]} for r in rows}
                if stock_id_filter is not None:
                    unknown = stock_id_filter - known.keys()
                    if unknown:
                        self.progress.emit(f"以下代號本機資料庫查無紀錄，已略過：{'、'.join(sorted(unknown))}")
                    scope_rows = [known[sid] for sid in stock_id_filter if sid in known]
                else:
                    scope_rows = list(known.values())

                twse_ids = {r["stock_id"] for r in scope_rows if r["market"] == "TWSE"}
                tpex_rows = [r for r in scope_rows if r["market"] == "TPEx"]
                affected_stock_ids = {r["stock_id"] for r in scope_rows}

                if twse_ids:
                    self.progress.emit(f"開始回補TWSE個股資料（{len(twse_ids)}檔）...")
                    backfill_history.backfill_twse(
                        conn, start_d, end_d,
                        include_price=p["stock_price"], include_institutional=p["stock_institutional"],
                        include_margin=p["stock_margin"], force_overwrite=force_overwrite,
                        stock_id_filter=twse_ids if stock_id_filter is not None else None,
                        on_progress=lambda done, total: self.progress.emit(f"TWSE回補中...{done}/{total}天"),
                    )

                if self.isInterruptionRequested():
                    self.cancelled.emit()
                    return

                if tpex_rows:
                    self.progress.emit(f"開始回補TPEx個股資料（{len(tpex_rows)}檔）...")
                    backfill_history.backfill_tpex(
                        conn, tpex_rows, start_d, end_d,
                        include_price=p["stock_price"], include_institutional=p["stock_institutional"],
                        include_margin=p["stock_margin"], force_overwrite=force_overwrite,
                        on_progress=lambda done, total: self.progress.emit(f"TPEx回補中...{done}/{total}檔"),
                    )

            if self.isInterruptionRequested():
                self.cancelled.emit()
                return

            # 均線/SAR快取／候選清單重算只跟「個股股價」有關(大盤本來就不接指標快取/選股
            # 規則，見src.screener.daily_screener.load_trailing_frames()排除market='INDEX')。
            stock_price_backfilled = bool(p["stock_price"] and any_stock_item and affected_stock_ids)
            if stock_price_backfilled:
                recompute_ids = None if stock_id_filter is None else list(affected_stock_ids)
                self.progress.emit("重算均線/SAR快取...")
                n = recompute_indicators_for_range(conn, recompute_ids, start, end)
                summary["indicators"] = n
                self.progress.emit(f"均線/SAR快取重算完成：{n}筆")

                if p["recompute_candidates"]:
                    self.progress.emit("重算歷史候選清單（較耗時）...")
                    n = run_screen_and_store_for_range(
                        conn, recompute_ids, start, end,
                        on_progress=lambda done, total: self.progress.emit(f"候選清單重算中...{done}/{total}天"),
                    )
                    summary["candidates"] = n
                    self.progress.emit(f"歷史候選清單重算完成：{n}筆")

            self.finished_ok.emit(summary)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            if conn is not None:
                conn.close()


class StockInfoRefreshWorker(QThread):
    """背景執行緒重新整理股票基本資料(名稱/產業別/市場別)——「回補資料」分頁的低優先
    小按鈕，跟每日排程用的是同一份FinMind TaiwanStockInfo，供使用者需要時手動立即觸發，
    不用等下一次排程。跟其他Worker同一個理由，開獨立連線，不重用MainWindow.conn。
    """

    finished_ok = Signal(int)
    failed = Signal(str)

    def run(self) -> None:
        conn = None
        try:
            conn = get_default_connection()
            rows = finmind_client.fetch_stock_info()
            storage.upsert_stocks(conn, rows)
            self.finished_ok.emit(len(rows))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            if conn is not None:
                conn.close()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("台股每日選股（本機版）")

        self.conn = None
        try:
            self.conn = get_default_connection()
            storage.ensure_schema(self.conn)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "資料庫連線失敗", str(exc))

        # 庫存清單／觀察清單專用連線，跟主DB(self.conn)分開——見src/data/portfolio_
        # storage.py開頭的說明，這條連線失敗不影響其他分頁正常運作，只有「庫存清單」/
        # 「觀察清單」兩個分頁會查不到資料。
        self.portfolio_conn = None
        try:
            self.portfolio_conn = get_default_portfolio_connection()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "庫存清單資料庫連線失敗", str(exc))

        self._pipeline_worker: PipelineWorker | None = None
        self._watchlist_export_worker: WatchlistExportWorker | None = None
        self._backfill_worker: BackfillWorker | None = None
        self._stock_info_worker: StockInfoRefreshWorker | None = None
        self._current_stock_id: str | None = None
        # 目前「個股資訊」分頁顯示的股票是從候選清單哪一天的選股策略點進來的("YYYY-MM-DD"
        # 字串)；手動查詢時設為None，右上角的來源標籤(self.stock_source_label)就不顯示
        # (見_on_candidate_selected()/_on_search())。
        self._current_stock_source: str | None = None
        # 追蹤上一次輪詢到的「候選清單算至」時間戳，供_poll_pipeline_status()偵測候選
        # 清單是否被外部(排程/Windows工作排程器背景觸發run_daily_pipeline())更新過，
        # 見_check_for_external_candidate_update()的說明。
        self._last_seen_candidate_update: str | None = None
        # 2026-08-04新增：觀察清單版本的同一種機制，見_check_for_external_watchlist_
        # update()——分別追蹤股價/法人資料(stocks.updated_at)跟F/G(holder_shares_
        # distribution.updated_at)兩個各自獨立的更新時間戳，任一個變了都代表觀察
        # 清單顯示的資料可能過時。
        self._last_seen_watchlist_price_update: str | None = None
        self._last_seen_watchlist_holder_update: str | None = None
        # 「重新整理」新股票F/G即時補抓(見_maybe_fetch_missing_holder_shares())：
        # _holder_fetch_worker追蹤目前是否有補抓在跑，避免重疊啟動；
        # _holder_fetch_attempted_stock_ids是整個桌面程式執行期間的一次性guard，
        # 避免真的查無資料的股票(例如剛IPO、TDCC還沒公布過集保股權分散表)每次
        # 「重新整理」都重打一次FinMind。
        self._holder_fetch_worker: HolderShareFetchWorker | None = None
        self._holder_fetch_attempted_stock_ids: set[str] = set()
        # QWebEngineView.setHtml()對內容大小有~2MB的隱性限制(Chromium的data: URL限制，超過
        # 會loadFinished(False)、畫面完全空白且不會報錯)——Plotly圖表把plotly.js整包內嵌後
        # 通常有4~5MB，遠超過這個限制。改成寫進暫存檔案再用load(QUrl.fromLocalFile(...))，
        # 檔案大小沒有這個限制。同一個視窗重複使用同一個暫存檔案，不會每次渲染都留下新檔案。
        self._chart_html_path = Path(tempfile.gettempdir()) / f"tw_stock_chart_{id(self)}.html"
        # 「產出報表」分頁的組合HTML(圖表+個股明細+個股分析)暫存檔案，同一套「setHtml()
        # 大小限制」考量，見上面_chart_html_path的說明。
        self._report_html_path = Path(tempfile.gettempdir()) / f"tw_stock_report_{id(self)}.html"
        # 見showEvent()：「大盤」是分頁0、也是視窗一開啟就顯示的預設分頁，`tabs.
        # currentChanged`訊號只在「真正切換」時觸發，開頭從-1變成0這個初始狀態不算
        # 「切換」不會發訊號，導致大盤分頁沒有經過_on_tab_changed()、一直是空白，要手動
        # 切到別的分頁再切回來才會有內容。這個旗標確保只在視窗第一次顯示時補打一次。
        self._startup_tab_refreshed = False
        # 「原文與頁碼」連結開的筆記閱讀視窗(見_open_rule_reference_window())——
        # PySide6沒有其他地方持有參照的QDialog會被提前GC回收(症狀是視窗一開就馬上
        # 自動關閉)，這個list負責讓視窗活著直到使用者自己關閉，視窗關閉時再從list移除。
        self._reference_windows: list[QDialog] = []

        self._build_ui()
        self._refresh_date_list()
        self._reload_candidates()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._poll_pipeline_status)
        self._status_timer.start(5000)
        self._poll_pipeline_status()

    # ------------------------------------------------------------------
    # UI 組裝
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # 七個分頁：①大盤、②選股(候選清單篩選+清單本身)、③個股資訊(個股查詢+K線圖+
        # 個股分析)、④產業輪動、⑤庫存清單、⑥觀察清單、⑦回補資料——原本候選清單跟個股
        # 圖表擠在同一個分頁，使用者反映畫面太擁擠，拆開後候選清單點選任一列會自動切到③
        # 並代入該股票資料(見_on_candidate_selected())。①跟③都用同一套規則比對邏輯
        # (_build_analysis_sections_html())，只是分析對象(大盤/個股)不同，渲染格式共用。
        # ⑤⑥移植自ref-project的庫存清單/觀察清單，用獨立的self.portfolio_conn(見
        # __init__())，不查主DB的候選清單/圖表相關資料。⑦是2026-08-04新增，取代原本
        # 只能下命令列跑scripts/backfill_*.py的回補流程。
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._build_market_tab()
        self._build_screener_tab()
        self._build_stock_detail_tab()
        self._build_industry_rotation_tab()
        self._build_inventory_tab()
        self._build_watchlist_tab()
        self._build_backfill_tab()
        # ⚠️ 分頁還沒被切換過去顯示之前，分頁裡的QTextEdit/QWebEngineView實際上沒有
        # 真正的layout(viewport寬度等於0或預設值)，這時候算文字框需要的高度一定不準
        # (實測算出來只有個位數px，見_build_market_tab()的說明)。改成切到對應分頁時
        # 才(重新)整理內容，順便也讓每次切回來都看得到最新資料。
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _build_screener_tab(self) -> None:
        """「選股」分頁：候選清單篩選條件+候選清單本身，不含個股圖表/分析(那些移到
        「個股資訊」分頁，見_build_stock_detail_tab())——原本候選清單跟個股圖表擠在
        同一個分頁，使用者反映畫面太擁擠，拆開後這裡可以完整顯示候選清單，不用捲很久
        才看得到後面的圖表。點選候選清單裡任一列會自動切到「個股資訊」分頁並代入該
        股票資料(見_on_candidate_selected())。
        """
        screener_scroll = QScrollArea()
        screener_scroll.setWidgetResizable(True)
        self.tabs.addTab(screener_scroll, "選股")

        screener_content = QWidget()
        screener_scroll.setWidget(screener_content)
        root_layout = QVBoxLayout(screener_content)

        date_bar = QHBoxLayout()
        date_bar.addWidget(QLabel("候選清單日期："))
        self.date_combo = QComboBox()
        self.date_combo.currentIndexChanged.connect(self._reload_candidates)
        date_bar.addWidget(self.date_combo)
        date_bar.addStretch()
        root_layout.addLayout(date_bar)

        # 「市場」「產業別」篩選：2026-08-02新增，跟下面的filter_bar/method_bar一樣是
        # 「篩選標準」(改完要按「套用篩選」才生效，不即時連動，見下面的說明)。產業別選項
        # 用chart_data.list_industries()在建構時查一次填入，self.conn在_build_ui()呼叫
        # 前就已經建好(見__init__())，這裡可以直接查。
        market_industry_bar = QHBoxLayout()
        market_industry_bar.addWidget(QLabel("市場："))
        self.market_filter_combo = QComboBox()
        self.market_filter_combo.addItems(["全部", "上市", "上櫃"])
        market_industry_bar.addWidget(self.market_filter_combo)
        market_industry_bar.addSpacing(20)
        market_industry_bar.addWidget(QLabel("產業別："))
        # 產業別可能同時符合好幾個想一起看的分類(使用者要求比照Excel欄位篩選的複選
        # 方式)，改用_CheckableComboBox取代單選的QComboBox，見該類別的docstring。
        self.industry_filter_combo = _CheckableComboBox()
        self.industry_filter_combo.setMinimumWidth(160)
        if self.conn is not None:
            self.industry_filter_combo.set_items(chart_data.list_industries(self.conn))
        market_industry_bar.addWidget(self.industry_filter_combo)
        market_industry_bar.addSpacing(20)
        # 2026-08-04新增：使用者要求候選清單能篩掉成交量太小、流動性不足的股票——跟
        # 市場/產業別同一套「候選股票池範圍」性質，改「套用篩選」按鈕時才生效(見
        # market_filter_combo/industry_filter_combo的既有慣例，不即時連動)，預設
        # 10張(使用者指定的預設值)。
        market_industry_bar.addWidget(QLabel("成交量 >="))
        self.volume_filter_spin = QSpinBox()
        self.volume_filter_spin.setRange(0, 999_999)
        self.volume_filter_spin.setValue(10)
        self.volume_filter_spin.setSuffix(" 張")
        market_industry_bar.addWidget(self.volume_filter_spin)
        market_industry_bar.addStretch()
        root_layout.addLayout(market_industry_bar)

        # ⚠️ 2026-08-01修正：篩選條件(以下這些勾選框/下拉/天數輸入)原本每改一個就立刻
        # 呼叫_reload_candidates()重新查DB+套用篩選，勾選框一多、候選股數也多時，每次
        # 微調都要等一次篩選運算，使用者反映「篩選花的時間很多」。改成不即時連動，
        # 統一改完再按下面的「套用篩選」按鈕才真正執行一次，同一組條件不會因為連續
        # 調整而重算好幾次。候選清單日期(上面的date_combo)例外，維持選了就立刻切換
        # ——那是「看哪一天」的導覽動作，不是「調整篩選標準」，語意上不同。
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("篩選條件："))
        self.filter_checkboxes: dict[str, QCheckBox] = {}
        for label in chart_data.CANDIDATE_FILTERS:
            cb = QCheckBox(label)
            default_checked = chart_data.CANDIDATE_FILTER_DEFAULTS.get(label, False)
            # 2026-08-04新增：記住使用者上次的勾選狀態(QSettings)，下次開APP不用重新
            # 勾一次——查無紀錄(第一次使用)才退回CANDIDATE_FILTER_DEFAULTS的預設值。
            cb.setChecked(self._app_settings().value(f"screener/filter/{label}", default_checked, type=bool))
            cb.toggled.connect(lambda checked, key=label: self._app_settings().setValue(f"screener/filter/{key}", checked))
            filter_bar.addWidget(cb)
            self.filter_checkboxes[label] = cb
        filter_bar.addStretch()
        root_layout.addLayout(filter_bar)

        # 「篩選方法：」這一列跟上面「篩選條件：」分開放，視覺上比較不擁擠。2026-08-02
        # 使用者釐清語意：這裡跟上面的均線多頭排列彼此是獨立的AND條件，候選清單的基礎池
        # 一律是全市場(見chart_data.load_stock_universe_for_date())——只勾均線多排
        # 但不勾朱家泓技術分析，等同對全市場做均線掃描，不受「當天有沒有觸發朱家泓規則」
        # 限制；勾了朱家泓技術分析才會額外要求當天有出現在daily_candidates。詳見
        # chart_data.apply_candidate_filters()的說明。
        method_bar = QHBoxLayout()
        method_bar.addWidget(QLabel("篩選方法："))
        # SAR翻轉篩選：勾選框+多頭/空頭下拉+翻轉天數輸入綁在一起，不是單純的勾選框，因此沒有
        # 塞進CANDIDATE_FILTERS的registry迴圈，另外獨立組裝、獨立傳給apply_candidate_filters
        # 的sar_flip_option參數(見src/presentation/chart_data.py)。
        self.sar_flip_checkbox = QCheckBox("SAR 翻轉")
        # 預設值改讀chart_data.CANDIDATE_SAR_FLIP_ENABLED_DEFAULT/_OPTION_DEFAULT
        # (2026-08-03改版：跟scripts/daily_pipeline.py的LINE/Email通知共用同一份
        # 常數，避免UI初始狀態跟通知內容各自維護一份預設值、之後改一邊忘記改另一邊
        # ——這正是使用者回報「LINE通知清單跟候選清單對不齊」的根因)。
        self.sar_flip_checkbox.setChecked(
            self._app_settings().value("screener/sar_flip_enabled", chart_data.CANDIDATE_SAR_FLIP_ENABLED_DEFAULT, type=bool)
        )
        self.sar_flip_checkbox.toggled.connect(lambda checked: self._app_settings().setValue("screener/sar_flip_enabled", checked))
        method_bar.addWidget(self.sar_flip_checkbox)
        self.sar_flip_direction_combo = QComboBox()
        self.sar_flip_direction_combo.addItems(["多頭", "空頭"])
        self.sar_flip_direction_combo.setCurrentText(
            self._app_settings().value("screener/sar_flip_direction", chart_data.CANDIDATE_SAR_FLIP_OPTION_DEFAULT["direction"])
        )
        self.sar_flip_direction_combo.currentTextChanged.connect(
            lambda text: self._app_settings().setValue("screener/sar_flip_direction", text)
        )
        method_bar.addWidget(self.sar_flip_direction_combo)
        self.sar_flip_days_spin = QSpinBox()
        self.sar_flip_days_spin.setRange(1, 60)
        self.sar_flip_days_spin.setValue(
            self._app_settings().value("screener/sar_flip_within_days", chart_data.CANDIDATE_SAR_FLIP_OPTION_DEFAULT["within_days"], type=int)
        )
        self.sar_flip_days_spin.setSuffix(" 天內翻轉")
        self.sar_flip_days_spin.valueChanged.connect(lambda value: self._app_settings().setValue("screener/sar_flip_within_days", value))
        method_bar.addWidget(self.sar_flip_days_spin)

        # 「朱家泓技術分析」勾選框：2026-08-01新增，2026-08-02改版跟其他「篩選方法」
        # (SAR翻轉)一樣是獨立的AND條件，不是「候選清單本來就限定在這個範圍」的基礎池
        # ——候選清單基礎池現在是全市場(見chart_data.load_stock_universe_for_date())，
        # 勾選這裡才會額外要求「當天有出現在daily_candidates(觸發過某條朱家泓規則)」；
        # 不勾選時，均線/SAR等其他條件會對全市場掃描，不受這個限制。無QSettings紀錄
        # 的全新使用者會fallback到chart_data.CANDIDATE_ZHU_RULE_ONLY_DEFAULT(2026-
        # 08-06改成False，「乾淨預設值」是SAR翻轉打勾、朱家泓技術分析不打勾)。
        method_bar.addSpacing(20)
        self.zhu_rule_checkbox = QCheckBox("朱家泓技術分析")
        self.zhu_rule_checkbox.setChecked(
            self._app_settings().value("screener/zhu_rule_only", chart_data.CANDIDATE_ZHU_RULE_ONLY_DEFAULT, type=bool)
        )
        self.zhu_rule_checkbox.toggled.connect(lambda checked: self._app_settings().setValue("screener/zhu_rule_only", checked))
        self.zhu_rule_checkbox.setToolTip("勾選時只保留當天有觸發朱家泓規則的股票；取消勾選則不限制，均線/SAR等條件會對全市場掃描")
        method_bar.addWidget(self.zhu_rule_checkbox)

        method_bar.addSpacing(20)
        self.apply_filter_btn = QPushButton("套用篩選")
        self.apply_filter_btn.setToolTip("篩選條件改完後按這裡才會重新套用，不用每改一項就等一次運算")
        self.apply_filter_btn.clicked.connect(self._reload_candidates)
        method_bar.addWidget(self.apply_filter_btn)

        method_bar.addStretch()
        root_layout.addLayout(method_bar)

        top_bar = QHBoxLayout()
        self.refresh_btn = QPushButton("🔄 立即重新篩選")
        self.refresh_btn.setToolTip("只用資料庫裡目前已有的資料重算候選清單，不重新抓取資料，通常幾秒內完成")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        self.fetch_btn = QPushButton("▶ 手動抓取今日資料")
        self.fetch_btn.setToolTip("抓取當天TWSE/TPEx資料並重新選股，較耗時(TPEx約需1小時內)，在背景執行不會卡住畫面")
        self.fetch_btn.clicked.connect(self._on_fetch_clicked)
        # 在候選清單「內」搜尋(跟「個股資訊」分頁裡的self.search_input不同——那個是不限
        # 候選清單、對任意股票代號/名稱做全域查詢；這個只在目前候選清單的列裡找，找到就
        # 選取+捲動過去，順便觸發_on_candidate_selected()連帶切到「個股資訊」分頁)。
        self.candidate_search_input = QLineEdit()
        self.candidate_search_input.setPlaceholderText("在候選清單中搜尋代號或名稱")
        self.candidate_search_input.setMaximumWidth(220)
        self.candidate_search_input.returnPressed.connect(self._on_candidate_search)
        self.status_label = QLabel("狀態：閒置")
        top_bar.addWidget(self.refresh_btn)
        top_bar.addWidget(self.fetch_btn)
        top_bar.addWidget(self.candidate_search_input)
        top_bar.addStretch()
        top_bar.addWidget(self.status_label)
        root_layout.addLayout(top_bar)

        # 「加入庫存」「加入觀察清單」：2026-08-04新增，候選清單表格最前面新增勾選欄
        # (見_reload_candidates())，這裡是對勾選的股票做批次動作的按鈕，跟「庫存清單」
        # 分頁既有的「加入觀察清單」(_on_inventory_add_to_watchlist())同一種bulk動作
        # 慣例，只是勾選來源換成候選清單的checkbox欄，不是QTableWidget原生的列選取。
        bulk_action_bar = QHBoxLayout()
        self.candidates_add_inventory_btn = QPushButton("加入庫存")
        self.candidates_add_inventory_btn.setToolTip("把勾選的股票各自新增一筆空白庫存批次，成本價/股數之後再自行編輯")
        self.candidates_add_inventory_btn.clicked.connect(self._on_candidates_add_to_inventory)
        bulk_action_bar.addWidget(self.candidates_add_inventory_btn)
        self.candidates_add_watchlist_btn = QPushButton("加入觀察清單")
        self.candidates_add_watchlist_btn.setToolTip("把勾選的股票加入指定的觀察清單群組（可複選）")
        self.candidates_add_watchlist_btn.clicked.connect(self._on_candidates_add_to_watchlist)
        bulk_action_bar.addWidget(self.candidates_add_watchlist_btn)
        bulk_action_bar.addStretch()
        root_layout.addLayout(bulk_action_bar)

        self.intraday_label = QLabel("⚠ 尚未收盤，本頁為盤中即時資料，收盤後數字可能改變")
        self.intraday_label.setStyleSheet("color: red; font-weight: bold;")
        self.intraday_label.setVisible(False)
        root_layout.addWidget(self.intraday_label)

        self.candidates_table = QTableWidget()
        self.candidates_table.setColumnCount(13)
        # 表頭第0欄用_CheckableHeaderView取代預設的QHeaderView，讓表頭本身就是一個
        # 「全選/取消全選」的checkbox——2026-08-04新增，必須在setHorizontalHeaderLabels()
        # 之前設定(晚一步取得的header變數才會是這個自訂類別)。
        self._candidates_header = _CheckableHeaderView(self.candidates_table)
        self.candidates_table.setHorizontalHeader(self._candidates_header)
        # 第0欄是勾選欄(見下面_reload_candidates())，2026-08-04新增，供「加入庫存」/
        # 「加入觀察清單」批次動作使用；其餘欄位順序不變，只是索引整體+1。
        self.candidates_table.setHorizontalHeaderLabels([
            "", "股票代號", "名稱", "產業別", "訊號(信心%)", "收盤價", "進場價", "停損價",
            "漲跌幅(%)", "成交量(張)", "SAR值", "SAR狀態", "SAR距離%",
        ])
        # 數值欄位靠右對齊(見下面_reload_candidates()裡的setTextAlignment)時，文字會
        # 緊貼著儲存格右側格線，加一點padding-right留出呼吸空間，不要看起來擠在線上。
        self.candidates_table.setStyleSheet("QTableWidget::item { padding-right: 10px; }")
        # ⚠️ 之前對整個header統一套用Stretch，會讓8欄一律平分寬度——「訊號」欄內容通常
        # 遠比其他欄位長，平分寬度下wrap出來的行數暴增、視覺上看起來像沒有斷行。改成除了
        # 「訊號」欄以外都用ResizeToContents(依內容自動給剛好的寬度)，多出來的空間全部
        # 留給「訊號」欄(Stretch)，這樣wrap後的行數才會合理。
        header = self.candidates_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        _CANDIDATE_CHECKBOX_COLUMN = 0
        header.setSectionResizeMode(_CANDIDATE_CHECKBOX_COLUMN, QHeaderView.ResizeMode.Fixed)
        self.candidates_table.setColumnWidth(_CANDIDATE_CHECKBOX_COLUMN, 30)
        _SIGNAL_COLUMN = 4
        header.setSectionResizeMode(_SIGNAL_COLUMN, QHeaderView.ResizeMode.Stretch)
        # 欄寬只有在視窗真正顯示、完成layout後才會是最終數值，_reload_candidates()在
        # __init__()裡就會被呼叫一次(視窗還沒show()、欄寬還是預設值)，resizeRowsToContents()
        # 這時算出來的列高會用到錯誤的欄寬、之後不會自動修正，導致文字被截斷看起來像沒
        # 斷行——sectionResized在欄寬因為Stretch隨視窗大小改變時也會觸發，一併重新計算
        # 列高，兩種情況(初次顯示/使用者拉大縮小視窗)都能修正。
        header.sectionResized.connect(lambda *_: QTimer.singleShot(0, self.candidates_table.resizeRowsToContents))
        self.candidates_table.setMinimumHeight(320)  # 至少完整顯示約8~10列，不用一開始就要捲動
        self.candidates_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.candidates_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # 同一檔股票符合多條規則時，訊號欄位的內容用「\n」分隔多行(見
        # src/presentation/chart_data.py的load_stock_universe_for_date())；開word wrap
        # 讓Qt正確把每個\n斷行顯示，而不是被裁掉或擠在一行，_reload_candidates()填完
        # 資料後還要呼叫resizeRowsToContents()讓列高跟著撐開，不然多行內容會被壓在
        # 原本單行的列高裡看不全。
        self.candidates_table.setWordWrap(True)
        self._candidates_header.toggled.connect(self._on_candidates_select_all_toggled)
        self.candidates_table.itemSelectionChanged.connect(self._on_candidate_selected)
        # 點欄位標題可以排序(股票代號/名稱/產業別/訊號用預設字串排序；進場價/停損價/
        # 漲跌幅/成交量用_NumericTableWidgetItem依實際數值排序，見該類別說明)。
        # _reload_candidates()填資料前後會暫時關掉/重新打開，避免QTableWidget在
        # 逐格setItem()的過程中就即時重新排序，導致資料填到錯的列。
        self.candidates_table.setSortingEnabled(True)
        root_layout.addWidget(self.candidates_table, stretch=1)

    def _build_stock_detail_tab(self) -> None:
        """「個股資訊」分頁：個股查詢+K線圖+均線/切線/支撐壓力/MACD/KD/SAR切換+個股分析+
        最新交易日摘要。從「選股」分頁候選清單點選任一列時會自動切換到這個分頁並代入
        該股票資料，右上角顯示「來源：X月X日的選股策略」；使用者在這個分頁自己手動
        查詢股票時則不顯示來源(見_on_candidate_selected()/_on_search())。

        這裡直接用QVBoxLayout(不是舊版候選清單+個股圖表共用的那個QSplitter)包在
        QScrollArea裡——候選清單移到獨立分頁後，不再需要讓使用者拖曳調整候選清單/
        圖表的相對高度，2026-07-29修正大盤分析截斷bug時發現的QSplitter不轉發
        sizeHint變化問題也就不再適用，不需要額外的高度同步workaround。
        """
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        self.tabs.addTab(detail_scroll, "個股資訊")

        detail_content = QWidget()
        detail_scroll.setWidget(detail_content)
        bottom_layout = QVBoxLayout(detail_content)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("個股查詢："))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("輸入股票代號或名稱（例如 2330 或 台積電）")
        self.search_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self.search_input)
        search_btn = QPushButton("查詢")
        search_btn.clicked.connect(self._on_search)
        search_row.addWidget(search_btn)
        search_row.addStretch()
        # 右上角標示這檔股票是從候選清單哪一天的選股策略點進來的，讓使用者知道現在看的
        # 是「當時」符合規則的股票，不是憑空冒出來的；手動查詢時不顯示(見_on_search())。
        self.stock_source_label = QLabel("")
        self.stock_source_label.setStyleSheet("color: #666666;")
        search_row.addWidget(self.stock_source_label)
        # 「資料更新至」：比照「大盤」/「觀察清單」分頁既有的做法(見_build_market_tab()/
        # _build_watchlist_tab())，放在查詢列最右邊、灰色小字——2026-08-04使用者反映
        # 個股資訊/產業輪動看不出目前顯示的股價資料多新，這裡跟其他分頁一致補上。
        self.stock_detail_update_label = QLabel("")
        self.stock_detail_update_label.setStyleSheet("color: #666666;")
        search_row.addWidget(self.stock_detail_update_label)
        bottom_layout.addLayout(search_row)

        # 2026-08-02改版：「個股分析」不再是按鈕展開/收合的內嵌面板，改成跟「圖表」平行的
        # 內層tab(見下面self.detail_inner_tabs)——使用者切到這個tab才需要顯示，不用像
        # 之前那樣另外維護一個顯示/隱藏的checkable按鈕狀態。
        # 用QTextBrowser(QTextEdit的子類別，多了連結導覽功能)取代單純的QTextEdit——
        # 「原文與頁碼」裡的.md檔名會被_render_rule_match_blocks()包成ruledoc:///連結
        # (見該函式說明)，anchorClicked訊號跟setOpenLinks()是QTextBrowser才有的API(單純
        # QTextEdit沒有，2026-08-04第一版誤用QTextEdit直接呼叫setOpenLinks()會
        # AttributeError)，除了連結功能外其餘用法(setReadOnly/setHtml/document()等)
        # 跟QTextEdit完全相容，不影響既有程式碼。setOpenLinks(False)讓它不要自己嘗試
        # 把這個非標準scheme當網址開啟，改由_on_reference_link_clicked()接手判斷、
        # 開新視窗顯示筆記內容。
        # 2026-08-04改版：原本單一self.analysis_view拆成三個QTextBrowser——summary
        # (📌總結分析，永遠可見，不包CollapsibleBox)＋tech(技術面)／chip(籌碼面)各自
        # 包一層_CollapsibleBox(可獨立收合，跟「個股明細」5個區塊同一種UI慣例)。三個
        # 都需要setOpenLinks(False)+anchorClicked，_build_analysis_text_view()統一
        # 建構、避免3份幾乎一樣的設定程式碼重複。
        self.analysis_summary_view = self._build_analysis_text_view()
        self._analysis_tech_box = _CollapsibleBox("技術面")
        self.analysis_tech_view = self._build_analysis_text_view()
        self._analysis_tech_box.content_layout.addWidget(self.analysis_tech_view)
        self._analysis_chip_box = _CollapsibleBox("籌碼面")
        self.analysis_chip_view = self._build_analysis_text_view()
        self._analysis_chip_box.content_layout.addWidget(self.analysis_chip_view)

        # ⚠️ 2026-08-02修正：原本靠setMinimumHeight(450)+stretch=1「猜」一個夠用的高度，
        # QWebEngineView的sizeHint()不會反映實際載入的Plotly圖表高度，_AutoHeightTabWidget
        # 量到的頁面高度可能比實際圖表內容矮，圖表看起來被壓縮在小框內(使用者反映「圖表
        # 圖示跟資訊框重疊」的一部分成因)。改成在_rerender_chart()裡讀
        # `fig.layout.height`後直接setFixedHeight()，不用猜。
        self.chart_view = QWebEngineView()

        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)
        self.summary_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # ⚠️ 2026-08-02修正：原本setMaximumHeight(220)固定上限，內容多時被截斷、要在小
        # 框裡另外捲動一次，使用者反映很難閱讀。拿掉上限，改成跟analysis_view同一套「依
        # 內容動態算高度」的作法(見_rerender_chart()裡setPlainText()之後的setFixedHeight)，
        # 交給detail_scroll(最外層QScrollArea)捲動，不是在這個小框內部另外捲動。

        # 2026-08-02改版：「圖表＋最新交易日摘要」跟「個股分析」拆成內層兩個tab，取代
        # 原本按鈕展開/收合的做法，版面不再同時擠著圖表跟一長串規則比對清單。
        # _AutoHeightTabWidget讓外層detail_scroll(QScrollArea)的捲軸範圍能正確反映
        # 「目前分頁」的實際高度(見該類別的docstring)。
        self.detail_inner_tabs = _AutoHeightTabWidget()
        chart_tab = QWidget()
        chart_tab_layout = QVBoxLayout(chart_tab)

        # ⚠️ 2026-08-02修正：這些均線/切線/支撐壓力/MACD/KD/SAR顯示切換原本跟搜尋列一樣
        # 放在detail_inner_tabs外面，不管切到「圖表」還是「個股分析」tab都看得到——但這些
        # checkbox只影響「圖表」的顯示內容，切到「個股分析」tab時完全用不到，使用者反映
        # 這樣放不合理(而且佔掉了「個股分析」tab上方的空間)。改成收進「圖表」tab內部，
        # 跟chart_view/summary_view放在同一個chart_tab_layout裡。
        controls_row = QHBoxLayout()

        ma_group = QGroupBox("顯示均線")
        ma_layout = QHBoxLayout(ma_group)
        self.ma_checkboxes: dict[int, QCheckBox] = {}
        for n in FULL_PERIODS:
            cb = QCheckBox(f"MA{n}")
            cb.setChecked(True)
            cb.stateChanged.connect(self._rerender_chart)
            ma_layout.addWidget(cb)
            self.ma_checkboxes[n] = cb
        controls_row.addWidget(ma_group)

        trend_group = QGroupBox("顯示切線／軌道線")
        trend_layout = QHBoxLayout(trend_group)
        self.trendline_checkboxes: dict[str, QCheckBox] = {}
        for key, label in chart_data.TRENDLINE_LABELS.items():
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.stateChanged.connect(self._rerender_chart)
            trend_layout.addWidget(cb)
            self.trendline_checkboxes[key] = cb
        controls_row.addWidget(trend_group)

        self.sr_checkbox = QCheckBox("顯示支撐壓力")
        self.sr_checkbox.setChecked(True)
        self.sr_checkbox.stateChanged.connect(self._rerender_chart)
        controls_row.addWidget(self.sr_checkbox)

        self.macd_checkbox = QCheckBox("顯示MACD")
        self.macd_checkbox.setChecked(True)
        self.macd_checkbox.stateChanged.connect(self._rerender_chart)
        controls_row.addWidget(self.macd_checkbox)

        self.kd_checkbox = QCheckBox("顯示KD")
        self.kd_checkbox.setChecked(True)
        self.kd_checkbox.stateChanged.connect(self._rerender_chart)
        controls_row.addWidget(self.kd_checkbox)

        self.sar_checkbox = QCheckBox("顯示SAR")
        self.sar_checkbox.setChecked(True)
        self.sar_checkbox.stateChanged.connect(self._rerender_chart)
        controls_row.addWidget(self.sar_checkbox)
        controls_row.addStretch()
        chart_tab_layout.addLayout(controls_row)

        chart_tab_layout.addWidget(self.chart_view)
        chart_tab_layout.addWidget(self.summary_view)
        self.detail_inner_tabs.addTab(chart_tab, "圖表")

        analysis_tab = QWidget()
        analysis_tab_layout = QVBoxLayout(analysis_tab)
        analysis_tab_layout.addWidget(self.analysis_summary_view)
        analysis_tab_layout.addWidget(self._analysis_tech_box)
        analysis_tab_layout.addWidget(self._analysis_chip_box)
        # ⚠️ 2026-08-04修正：使用者反映收合/展開技術面時，籌碼面的標題列跟下面的內容
        # 之間會出現一大段空白、整體版面還會跳動——查證是因為這裡少了跟「個股明細」
        # 分頁(overview_layout.addStretch()，見_build_stock_overview_tab())同樣的
        # 收尾addStretch()：detail_scroll(QScrollArea, setWidgetResizable(True))的
        # viewport高度不會剛好等於內容高度，收合技術面騰出空間後，QVBoxLayout會把
        # 這段「多出來的viewport空間」分配給其餘用預設Preferred size policy的子
        # widget(這裡是_analysis_chip_box)撐大，不是真的有更多內容，是空白被塞進
        # 收合框裡。加上addStretch()讓多餘空間統一被這個stretch吸收、留在整個分頁
        # 最下方，各個_CollapsibleBox的高度就只反映自己實際內容的需求，不會被撐大。
        analysis_tab_layout.addStretch()
        self.detail_inner_tabs.addTab(analysis_tab, "個股分析")
        self.analysis_summary_view.anchorClicked.connect(self._on_reference_link_clicked)
        self.analysis_summary_view.anchorClicked.connect(
            lambda url: self._on_analysis_jump_link_clicked(
                url, detail_scroll, self._analysis_tech_box, self._analysis_chip_box,
            )
        )
        for view in (self.analysis_tech_view, self.analysis_chip_view):
            view.anchorClicked.connect(self._on_reference_link_clicked)
            view.anchorClicked.connect(lambda url: self._on_analysis_top_link_clicked(url, detail_scroll))

        self._build_stock_overview_tab()

        # 2026-08-04新增：「產出報表」分頁，依序把圖表/個股明細/個股分析組合成一份
        # 完整HTML(見_build_report_html())，用QWebEngineView預覽、可匯出成PDF——
        # 跟其餘inner tab不同，這裡不追求「跟畫面內容一樣高、外層捲動」，內容本來就是
        # 一份完整文件，讓QWebEngineView自己捲動比較符合「預覽報表」的直覺(像瀏覽器
        # 分頁，不是像其餘QTextBrowser區塊那樣融入整頁)。
        report_tab = QWidget()
        report_tab_layout = QVBoxLayout(report_tab)
        report_toolbar = QHBoxLayout()
        self.report_export_btn = QPushButton("🖨 匯出PDF")
        self.report_export_btn.clicked.connect(self._on_export_report_clicked)
        report_toolbar.addWidget(self.report_export_btn)
        report_toolbar.addStretch()
        report_tab_layout.addLayout(report_toolbar)
        self.report_view = QWebEngineView()
        self.report_view.setMinimumHeight(700)
        self.report_view.page().pdfPrintingFinished.connect(self._on_report_pdf_finished)
        report_tab_layout.addWidget(self.report_view, stretch=1)
        self.detail_inner_tabs.addTab(report_tab, "產出報表")

        self.detail_inner_tabs.currentChanged.connect(self._on_detail_inner_tab_changed)
        _FloatingTopButton(detail_scroll)
        bottom_layout.addWidget(self.detail_inner_tabs)

    # 「個股明細」5個區塊的標題，同時是_build_stock_overview_tab()建立_CollapsibleBox
    # 的順序、也是_refresh_stock_overview_view()對應each區塊QTextEdit屬性名稱的依據
    # (見_STOCK_OVERVIEW_BLOCK_ATTRS)。
    _STOCK_OVERVIEW_BLOCKS = ["交易資訊", "法人買賣總覽", "主力進出", "資券變化總覽", "大戶籌碼"]

    def _build_stock_overview_tab(self) -> None:
        """「個股明細」內層tab(detail_inner_tabs第3個分頁，index==2)：交易資訊/法人
        買賣總覽/主力進出/資券變化總覽/大戶籌碼，仿使用者提供的參考截圖(temp/個股
        詳情-*.jpg)排版。2026-08-02新增，2026-08-03改版。

        目前只有「交易資訊」「法人買賣總覽」「資券變化總覽」三個區塊有真實資料來源
        (stock_prices/institutional_investors/margin_trading，見src/presentation/
        stock_detail_data.py的模組docstring)；「主力進出」需要券商分點籌碼(FinMind
        付費方案才能接上)、「大戶籌碼」需要股權分散/大戶持股資料(目前schema完全沒有
        對應的表)，這兩個區塊先建好框架、顯示「尚未串接資料來源」，不是造假資料湊。

        ⚠️ 2026-08-03改版：
        ①每個區塊改用_CollapsibleBox包起來，可各自獨立展開/收合(預設展開)——原本
          5個區塊擠在同一個QTextEdit裡，使用者反映想要能個別收合。
        ②法人買賣總覽拿掉「當日／累計」切換，固定顯示累計表格(欄位改成「1日、2日、
          3日、5日、10日、30日、40日、3個月、6個月、1年」)——使用者要求拿掉當日
          檢視，一律用累計表格；天期組合2026-08-03當天又改過一次(見
          stock_detail_data.py的INSTITUTIONAL_PERIODS)，這裡直接讀那份定義，
          不會漏改。
        ③資券變化總覽維持「當日／累計」切換(使用者這次沒有要求改，維持原樣)。
        ④主力進出改成仿照參考截圖的「4張指標卡片＋買超/賣超雙欄券商表格」排版，
          雖然目前沒有真實資料，框架本身要先做到位。
        """
        overview_tab = QWidget()
        overview_layout = QVBoxLayout(overview_tab)

        # 預設的checkable QPushButton在Fusion風格下checked/unchecked視覺差異很小，
        # 使用者不容易一眼看出目前是哪個檢視——明確加style讓checked狀態變成藍底白字，
        # 跟表格裡漲跌用的紅/綠是不同語意(這裡純粹是「目前選取」的UI狀態，不代表
        # 漲跌方向)，用藍色避免混淆。資券變化總覽的「當日／累計」切換仍然保留(見
        # class docstring③)，法人買賣總覽已經拿掉切換，不再需要這組按鈕。
        toggle_btn_style = (
            "QPushButton { padding: 3px 14px; }"
            "QPushButton:checked { background-color: #2980b9; color: white; font-weight: bold; }"
        )

        self._stock_overview_boxes: dict[str, _CollapsibleBox] = {}
        self._stock_overview_views: dict[str, QTextEdit] = {}
        for title in self._STOCK_OVERVIEW_BLOCKS:
            box = _CollapsibleBox(title, overview_tab)
            if title == "資券變化總覽":
                toggle_row = QHBoxLayout()
                toggle_row.addWidget(QLabel("檢視："))
                self._margin_cumulative = False
                margin_group = QButtonGroup(box)
                margin_daily_btn = QPushButton("當日")
                margin_daily_btn.setCheckable(True)
                margin_daily_btn.setChecked(True)
                margin_cumulative_btn = QPushButton("累計")
                margin_cumulative_btn.setCheckable(True)
                for btn in (margin_daily_btn, margin_cumulative_btn):
                    btn.setStyleSheet(toggle_btn_style)
                margin_group.addButton(margin_daily_btn)
                margin_group.addButton(margin_cumulative_btn)
                margin_daily_btn.clicked.connect(lambda: self._on_margin_view_toggle(False))
                margin_cumulative_btn.clicked.connect(lambda: self._on_margin_view_toggle(True))
                toggle_row.addWidget(margin_daily_btn)
                toggle_row.addWidget(margin_cumulative_btn)
                toggle_row.addStretch()
                box.content_layout.addLayout(toggle_row)

            view = QTextEdit()
            view.setReadOnly(True)
            view.setFrameShape(QFrame.Shape.NoFrame)
            view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            box.content_layout.addWidget(view)

            overview_layout.addWidget(box)
            self._stock_overview_boxes[title] = box
            self._stock_overview_views[title] = view

        overview_layout.addStretch()
        self.detail_inner_tabs.addTab(overview_tab, "個股明細")

    def _on_margin_view_toggle(self, cumulative: bool) -> None:
        self._margin_cumulative = cumulative
        self._refresh_stock_overview_view()

    def _build_industry_rotation_tab(self) -> None:
        """「產業輪動」分頁：某一天各產業別的成交量加總/平均漲跌幅/股票數，用來看資金
        目前比較集中往哪個產業移動——跟chart_data.load_industry_rotation()的說明一樣，
        日期選單用chart_data.list_price_dates()(不受daily_candidates限制)。表格內容
        延後到分頁真正顯示時才查(見_on_tab_changed())，日期選單本身在建構時就可以填好。

        ⚠️ 2026-08-06改版：使用者要求可以點開產業別、縮排列出該產業別底下每檔股票的
        成交/開盤/最高/最低/漲跌/漲跌幅/總成交張數——原本的`QTableWidget`(單純的產業
        彙總平面表格)改成`QTreeWidget`，比照「庫存清單」的母子列模式(見_build_
        inventory_tab())：每個產業別一個父列(彙總數字)，點父列的「股票數」欄位或原生
        展開箭頭才會展開底下該產業每一檔股票的明細子列(chart_data.load_industry_
        rotation_stocks())，預設全部收合。
        """
        rotation_scroll = QScrollArea()
        rotation_scroll.setWidgetResizable(True)
        self.tabs.addTab(rotation_scroll, "產業輪動")

        rotation_content = QWidget()
        rotation_scroll.setWidget(rotation_content)
        rotation_layout = QVBoxLayout(rotation_content)

        date_bar = QHBoxLayout()
        date_bar.addWidget(QLabel("日期："))
        self.industry_date_combo = QComboBox()
        if self.conn is not None:
            self.industry_date_combo.addItems(chart_data.list_price_dates(self.conn))
        self.industry_date_combo.currentIndexChanged.connect(self._refresh_industry_rotation_tab)
        date_bar.addWidget(self.industry_date_combo)
        date_bar.addStretch()
        # 「資料更新至」：比照「大盤」/「觀察清單」/「個股資訊」分頁既有的做法，放在
        # 日期選單列最右邊、灰色小字——2026-08-04使用者反映這個分頁看不出目前顯示的
        # 股價資料多新。這裡顯示全DB最新更新時間(不是單一股票的)，因為這個分頁本身
        # 是跨股票的產業別彙總，跟「個股資訊」分頁單一股票各自的updated_at不同語意。
        self.industry_update_label = QLabel("")
        self.industry_update_label.setStyleSheet("color: #666666;")
        date_bar.addWidget(self.industry_update_label)
        rotation_layout.addLayout(date_bar)

        self.industry_tree = QTreeWidget()
        self.industry_tree.setColumnCount(len(_INDUSTRY_TREE_HEADERS))
        self.industry_tree.setHeaderLabels(_INDUSTRY_TREE_HEADERS)
        # 2026-08-06使用者反映：①展開箭頭要一開始就看得到(不是點過才出現)——QTreeWidget
        # 預設只有「childCount()>0」的item才畫箭頭，這裡的子列是延後查詢(見_populate_
        # industry_stock_children())，一開始建parent_item時是真的沒有子節點，所以每個
        # 產業列都要顯式setChildIndicatorPolicy(ShowIndicator)強制畫出箭頭，即使還沒有
        # 子節點(標準的「延遲載入樹狀結構」Qt寫法)。②整體排版太單調(大片空白)，加淡淡的
        # 底部分隔線+交錯列底色，不是QTableWidget那種完整格線(QTreeWidget沒有原生
        # setShowGrid()，用stylesheet模擬)。
        self.industry_tree.setStyleSheet(
            "QTreeWidget::item { padding-right: 10px; border-bottom: 1px solid #e5e5e5; }"
        )
        self.industry_tree.setAlternatingRowColors(True)
        header = self.industry_tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.industry_tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.industry_tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        self.industry_tree.setSortingEnabled(True)
        self.industry_tree.itemClicked.connect(self._on_industry_tree_item_clicked)
        # 展開/收合狀態變化(不管是使用者點原生箭頭、點「股票數」欄、還是重新整理後
        # 程式碼還原展開狀態)統一走這兩個訊號處理：展開時第一次補上子列資料+套用底色，
        # 收合時清掉底色。見_on_industry_tree_item_expanded()的說明。
        self.industry_tree.itemExpanded.connect(self._on_industry_tree_item_expanded)
        self.industry_tree.itemCollapsed.connect(self._on_industry_tree_item_collapsed)
        rotation_layout.addWidget(self.industry_tree, stretch=1)

    def _on_industry_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """點「股票數」欄位文字也能展開/收合——不是只能點原生箭頭那個小三角，跟庫存
        清單「批次數」欄位的既有慣例一致。實際補資料/套底色的邏輯統一交給itemExpanded/
        itemCollapsed訊號處理(見_on_industry_tree_item_expanded())，這裡只負責觸發
        setExpanded()狀態切換本身，不管是點這裡還是點原生箭頭，最後都會走到同一套
        訊號處理，不會有兩份邏輯要維護。
        """
        if column != _INDUSTRY_TREE_STOCK_COUNT_COLUMN:
            return
        if item.data(0, Qt.ItemDataRole.UserRole) is None:
            return  # 子列(個股)本身沒有設這個UserRole，點子列的這一欄不會誤觸發
        item.setExpanded(not item.isExpanded())

    def _on_industry_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        """展開時觸發，不管展開動作是使用者點原生箭頭、點「股票數」欄文字、還是
        _refresh_industry_rotation_tab()重新整理後程式碼還原展開狀態，都會走到這裡。
        第一次展開(還沒查過個股明細)才即時查、補上子列——避免每次重新整理都要對
        「全部」產業各查一次個股明細(大部分產業使用者根本不會展開)，只在真的要展開時
        才查那一個產業；已經查過的產業(childCount()>0)不重複查。展開的產業列套上
        淡色底色，讓使用者一眼看出目前哪個產業是展開狀態。
        """
        industry = item.data(0, Qt.ItemDataRole.UserRole)
        if industry is None:
            return
        if item.childCount() == 0:
            self._populate_industry_stock_children(item, industry)
        self._set_industry_item_expanded_style(item, expanded=True)

    def _on_industry_tree_item_collapsed(self, item: QTreeWidgetItem) -> None:
        if item.data(0, Qt.ItemDataRole.UserRole) is None:
            return
        self._set_industry_item_expanded_style(item, expanded=False)

    def _set_industry_item_expanded_style(self, item: QTreeWidgetItem, expanded: bool) -> None:
        color = QColor("#E3F0FC") if expanded else QColor(Qt.GlobalColor.transparent)
        for col in range(self.industry_tree.columnCount()):
            item.setBackground(col, color)

    @staticmethod
    def _format_industry_stock_row(row: pd.Series) -> list[str]:
        """個股明細子列要顯示的文字，欄位結構跟父列(產業彙總列)共用同一組
        `_INDUSTRY_TREE_HEADERS`，「產業別/股票代號」欄放股票代號、「股票數」欄留空
        (股票數是產業層級的統計，子列不重複顯示)。"""
        return [
            row["stock_id"], row["name"],
            f"{row['close']:.2f}" if pd.notna(row["close"]) else "-",
            f"{row['open']:.2f}" if pd.notna(row["open"]) else "-",
            f"{row['high']:.2f}" if pd.notna(row["high"]) else "-",
            f"{row['low']:.2f}" if pd.notna(row["low"]) else "-",
            f"{row['change']:+.2f}" if pd.notna(row["change"]) else "-",
            f"{row['pct_change']:+.2f}" if pd.notna(row["pct_change"]) else "-",
            f"{int(row['volume']) // 1000:,}" if pd.notna(row["volume"]) else "-",
            "",
        ]

    def _populate_industry_stock_children(self, parent_item: QTreeWidgetItem, industry: str) -> None:
        """幫一個產業別的父列補上個股明細子列，`_refresh_industry_rotation_tab()`
        (還原重新整理前已展開的產業)、`_on_industry_tree_item_expanded()`(使用者第一次
        點開某個產業)共用這個方法，避免兩處各寫一份幾乎一樣的填值邏輯。"""
        if self.conn is None:
            return
        target_date = self.industry_date_combo.currentText() or None
        _, latest_date = chart_data.load_industry_rotation(self.conn, target_date=target_date)
        if latest_date is None:
            return
        stocks_df = chart_data.load_industry_rotation_stocks(self.conn, industry, latest_date)
        self.industry_tree.setSortingEnabled(False)
        for _, stock_row in stocks_df.iterrows():
            child_item = _NumericTreeWidgetItem()
            for col_idx, value in enumerate(self._format_industry_stock_row(stock_row)):
                child_item.setText(col_idx, value)
                if col_idx in _INDUSTRY_TREE_NUMERIC_COLUMNS:
                    child_item.setTextAlignment(col_idx, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            parent_item.addChild(child_item)
        self.industry_tree.setSortingEnabled(True)

    def _refresh_industry_rotation_tab(self) -> None:
        if self.conn is None:
            return
        self.industry_update_label.setText(
            f"資料更新至：{self._format_update_timestamp(chart_data.get_latest_update_time(self.conn))}"
        )
        target_date = self.industry_date_combo.currentText() or None
        df, latest_date = chart_data.load_industry_rotation(self.conn, target_date=target_date)
        tree = self.industry_tree
        # 重建前記錄目前已展開的產業別，重建後對這些產業的新父列重新setExpanded(True)——
        # 比照_populate_inventory_tree()同一個理由，不然每次切換日期都會把使用者剛展開
        # 看的產業收合回去。
        expanded_industries = {
            tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).isExpanded()
        }
        tree.setSortingEnabled(False)
        tree.clear()
        if latest_date is not None:
            for _, row in df.reset_index(drop=True).iterrows():
                parent_item = _NumericTreeWidgetItem()
                # 子列是延後查詢(見_populate_industry_stock_children())，這裡建parent_
                # item時實際上還沒有任何子節點，QTreeWidget預設「childCount()>0才畫箭頭」
                # 的行為會讓箭頭要等使用者點過一次(觸發查詢補上子列)才出現，使用者反映
                # 這樣很奇怪——改成強制一律顯示箭頭，即使目前還沒有子節點，跟庫存清單的
                # QTreeWidget不同：庫存清單的子列是重新整理當下就全部建好，本來就
                # childCount()>0，不需要這個設定。
                parent_item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                values = [
                    row["industry"],
                    "",  # 名稱：產業彙總列沒有對應的個股名稱
                    "", "", "", "", "",  # 成交/開盤/最高/最低/漲跌：個股才有的欄位
                    f"{row['avg_pct_change']:+.2f}" if pd.notna(row["avg_pct_change"]) else "-",
                    f"{int(row['total_volume']) // 1000:,}",
                    str(int(row["stock_count"])),
                ]
                for col_idx, value in enumerate(values):
                    parent_item.setText(col_idx, str(value))
                    if col_idx in _INDUSTRY_TREE_NUMERIC_COLUMNS:
                        parent_item.setTextAlignment(col_idx, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                parent_item.setData(0, Qt.ItemDataRole.UserRole, row["industry"])
                tree.addTopLevelItem(parent_item)

                if row["industry"] in expanded_industries:
                    self._populate_industry_stock_children(parent_item, row["industry"])
                    parent_item.setExpanded(True)
        tree.setSortingEnabled(True)
        # 2026-08-04新增：使用者要求預設用「平均漲跌幅(%)」由高到低排序，一打開就能看到
        # 資金最集中流入的產業排最前面，不用先手動點一次欄位標題排序；2026-08-06改用
        # QTreeWidget後這個排序同時也對子列的「漲跌幅(%)」(同一欄)生效，個股明細展開
        # 後預設一樣是漲跌幅由高到低。
        tree.sortByColumn(_INDUSTRY_TREE_HEADERS.index("漲跌幅(%)"), Qt.SortOrder.DescendingOrder)

    # ------------------------------------------------------------------
    # 庫存清單／觀察清單(2026-08-02移植自ref-project，DB跟主DB分開放，見
    # src/data/portfolio_storage.py開頭的說明)
    # ------------------------------------------------------------------

    def _populate_portfolio_table(self, table: QTableWidget, df: pd.DataFrame) -> None:
        """觀察清單表格的填值邏輯(庫存清單改用QTreeWidget/_populate_inventory_tree()，
        不經過這裡)。df仍然是portfolio_data.load_watchlist()回傳的完整DataFrame(含
        cost_price/shares/market_value/profit/return_pct等欄位，供_portfolio_summary_
        text()加總用)，只是2026-08-06起這幾欄不再填進表格顯示(使用者反映觀察清單不是
        真的持股，這些欄位放在這裡沒有意義)。
        """
        table.setSortingEnabled(False)
        table.setRowCount(len(df))
        for row_idx, row in df.reset_index(drop=True).iterrows():
            values = [
                row["stock_id"],
                row["name"] if pd.notna(row["name"]) else "-",
                f"{row['close']:.2f}" if pd.notna(row["close"]) else "-",
                f"{row['pct_change']:+.2f}" if pd.notna(row["pct_change"]) else "-",
                row["sar_status"] if pd.notna(row["sar_status"]) else "-",
                f"{row['sar_distance_pct']:+.2f}" if pd.notna(row["sar_distance_pct"]) else "-",
            ]
            for col_idx, value in enumerate(values):
                item_cls = _NumericTableWidgetItem if col_idx in _PORTFOLIO_NUMERIC_COLUMNS else QTableWidgetItem
                item = item_cls(str(value))
                if col_idx in _PORTFOLIO_NUMERIC_COLUMNS:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if col_idx == 1:
                    # 名稱欄依市場別上色(上市藍/上櫃黑/興櫃灰)，2026-08-04新增，
                    # 跟Google Sheet匯出(watchlist_export.py)共用同一份對照表。
                    item.setForeground(QColor(portfolio_data.listing_type_color(row.get("listing_type"))))
                table.setItem(row_idx, col_idx, item)
        table.setSortingEnabled(True)
        table.resizeRowsToContents()

    @staticmethod
    def _selected_portfolio_stock_ids(table: QTableWidget) -> list[str]:
        rows = table.selectionModel().selectedRows()
        return [table.item(r.row(), 0).text() for r in rows]

    @staticmethod
    def _portfolio_summary_text(df: pd.DataFrame, cost_label: str, value_label: str, profit_label: str) -> str:
        """算庫存清單／觀察清單頂部的摘要文字：總成本/總市值/累積損益(含%)/今日資產
        變動——只加總「有算出值」的列(pandas sum預設skipna=True)，成本價/持股數
        都沒填的列本來就不計入任何一個加總數字，不是遺漏。2026-08-02：總成本加上
        手續費加總(觀察清單沒有fee欄位時.get()拿到None、sum()視為0，不影響既有
        行為)，才會跟每一列profit/return_pct已經計入手續費的算法基準一致。
        """
        total_fee = df["fee"].sum() if "fee" in df.columns else 0
        total_cost = (df["cost_price"] * df["shares"]).sum() + total_fee
        total_value = df["market_value"].sum()
        total_profit = df["profit"].sum()
        total_change = df["today_change_value"].sum()
        return_pct_text = f"（{total_profit / total_cost * 100:+.2f}%）" if total_cost else ""
        return (
            f"{cost_label}：{total_cost:,.0f}　{value_label}：{total_value:,.0f}　"
            f"{profit_label}：{total_profit:+,.0f}{return_pct_text}　"
            f"今日資產變動：{total_change:+,.0f}"
        )

    def _build_inventory_tab(self) -> None:
        """「庫存清單」分頁：使用者實際持有的股票，記錄成本價/持股數/手續費，算
        浮動損益。現價/SAR資料直接查主DB既有的stock_prices/daily_indicators
        (daily_pipeline.py每天自動更新)，不像ref-project那樣需要另外做背景
        yfinance更新worker——本專案的股價/技術指標資料本來就已經是最新的，不需要
        per-股票手動觸發更新。

        ⚠️ 2026-08-02第三次改版：使用者反映上一輪「明細／彙總」下拉切換的操作
        方式麻煩，改成用`QTreeWidget`(樹狀表格)：每檔股票一個父列(彙總後的加權
        平均成本/總損益)，預設全部收合；點父列的「批次數」欄位或原生展開箭頭，
        才會展開底下每一筆批次(lot)各自的明細子列。這是QTreeWidget的原生用途
        (master-detail)，不用再自己維護「兩個表格+QStackedWidget+下拉選單」。
        """
        inventory_scroll = QScrollArea()
        inventory_scroll.setWidgetResizable(True)
        self.tabs.addTab(inventory_scroll, "庫存清單")

        inventory_content = QWidget()
        inventory_scroll.setWidget(inventory_content)
        inventory_layout = QVBoxLayout(inventory_content)

        self.inventory_summary_label = QLabel("")
        inventory_layout.addWidget(self.inventory_summary_label)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("新增")
        add_btn.clicked.connect(self._on_inventory_add)
        edit_btn = QPushButton("編輯選取")
        edit_btn.clicked.connect(self._on_inventory_edit_selected)
        delete_btn = QPushButton("刪除選取")
        delete_btn.clicked.connect(self._on_inventory_delete_selected)
        watchlist_btn = QPushButton("加入觀察清單")
        watchlist_btn.clicked.connect(self._on_inventory_add_to_watchlist)
        refresh_btn = QPushButton("🔄 重新整理")
        refresh_btn.clicked.connect(self._refresh_inventory_tab)
        for btn in (add_btn, edit_btn, delete_btn, watchlist_btn, refresh_btn):
            toolbar.addWidget(btn)
        toolbar.addStretch()
        inventory_layout.addLayout(toolbar)

        self.inventory_tree = QTreeWidget()
        self.inventory_tree.setColumnCount(len(_INVENTORY_TREE_HEADERS))
        self.inventory_tree.setHeaderLabels(_INVENTORY_TREE_HEADERS)
        self.inventory_tree.setStyleSheet("QTreeWidget::item { padding-right: 10px; }")
        header = self.inventory_tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_INVENTORY_TREE_HEADERS.index("備註"), QHeaderView.ResizeMode.Stretch)
        self.inventory_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.inventory_tree.setAllColumnsShowFocus(True)  # 整列反白選取，不是只有第0欄
        self.inventory_tree.setSortingEnabled(True)
        # 點「批次數」欄位的數字展開/收合該股票明細——原生展開箭頭(點第0欄前面的
        # 小三角)還是照常可以用，這是額外多一個可以點的地方，不是取代原生行為。
        self.inventory_tree.itemClicked.connect(self._on_inventory_tree_item_clicked)
        # F2編輯/Delete刪除快捷鍵：2026-08-04新增，比照ref-project(ui/widgets/
        # inventory_list.py)的既有慣例，直接重用「編輯選取」/「刪除選取」按鈕
        # 同一組handler，不是另外寫一套邏輯。綁在inventory_tree這個widget本身
        # (不是self/MainWindow)、context設WidgetShortcut，只有這個表格有focus
        # (使用者點過某一列)時按鍵才生效，不會跟其他分頁的表格互相干擾。
        self.inventory_edit_shortcut = QShortcut(QKeySequence("F2"), self.inventory_tree)
        self.inventory_edit_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.inventory_edit_shortcut.activated.connect(self._on_inventory_edit_selected)
        self.inventory_delete_shortcut = QShortcut(QKeySequence("Delete"), self.inventory_tree)
        self.inventory_delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.inventory_delete_shortcut.activated.connect(self._on_inventory_delete_selected)
        inventory_layout.addWidget(self.inventory_tree, stretch=1)

    def _build_portfolio_table(self, headers: list[str], stretch_column: str | None = None) -> QTableWidget:
        """觀察清單表格的建構邏輯(庫存清單改用QTreeWidget，見_build_inventory_
        tab())。stretch_column未指定時預設拉開最後一欄。"""
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setStyleSheet("QTableWidget::item { padding-right: 10px; }")
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        stretch_idx = headers.index(stretch_column) if stretch_column in headers else len(headers) - 1
        header.setSectionResizeMode(stretch_idx, QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(True)
        return table

    def _on_inventory_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == _INVENTORY_TREE_LOT_COUNT_COLUMN and item.childCount() > 0:
            item.setExpanded(not item.isExpanded())

    @staticmethod
    def _format_inventory_row(row: pd.Series, is_lot: bool) -> list[str]:
        """組出一列(父列或子列)要顯示的文字，父列/子列共用同一組欄位結構(見
        _INVENTORY_TREE_HEADERS)，只是彼此留空的欄位不同——父列沒有單一的買入
        日期/備註，子列則不重複顯示股票代號/名稱/現價/漲跌幅/SAR(樹狀縮排本身
        已經表達了從屬關係，不需要每個子列重複一次)。
        """
        def fmt(key: str, spec: str = "{:.2f}") -> str:
            value = row.get(key)
            return spec.format(value) if pd.notna(value) else "-"

        return [
            "" if is_lot else row["stock_id"],
            "" if is_lot else (row["name"] if pd.notna(row["name"]) else "-"),
            (row["buy_date"] if pd.notna(row["buy_date"]) and row["buy_date"] else "-") if is_lot else "",
            "" if is_lot else fmt("close"),
            "" if is_lot else fmt("pct_change", "{:+.2f}"),
            fmt("cost_price"),
            fmt("shares", "{:,.0f}"),
            fmt("fee", "{:,.0f}"),
            fmt("market_value", "{:,.0f}"),
            fmt("sell_fee", "{:,.0f}"),
            fmt("profit", "{:+,.0f}"),
            fmt("return_pct", "{:+.2f}"),
            "" if is_lot else (row["sar_status"] if pd.notna(row["sar_status"]) else "-"),
            "" if is_lot else fmt("sar_distance_pct", "{:+.2f}"),
            str(int(row["lot_count"])) if (not is_lot and "lot_count" in row and pd.notna(row.get("lot_count"))) else "",
            (row["note"] if pd.notna(row["note"]) and row["note"] else "") if is_lot else "",
        ]

    def _populate_inventory_tree(self, summary_df: pd.DataFrame, lots_df: pd.DataFrame) -> None:
        """重建整棵樹：每檔股票一個父列(彙總數字，來自summary_df)，底下掛著這檔
        股票的每一筆批次子列(來自lots_df依stock_id分組)。重建前記錄目前已展開的
        股票代號，重建後對這些股票的新父列重新setExpanded(True)——不然每次新增/
        編輯/刪除批次觸發的重新整理，都會把使用者剛展開看的股票收合回去，體驗
        很差。
        """
        tree = self.inventory_tree
        expanded_stock_ids = {
            tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).isExpanded()
        }
        tree.setSortingEnabled(False)
        tree.clear()

        lots_by_stock = dict(tuple(lots_df.groupby("stock_id"))) if not lots_df.empty else {}
        for _, row in summary_df.reset_index(drop=True).iterrows():
            parent_item = _NumericTreeWidgetItem()
            for col_idx, value in enumerate(self._format_inventory_row(row, is_lot=False)):
                parent_item.setText(col_idx, value)
                if col_idx in _INVENTORY_TREE_NUMERIC_COLUMNS:
                    parent_item.setTextAlignment(col_idx, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            parent_item.setData(0, Qt.ItemDataRole.UserRole, row["stock_id"])
            tree.addTopLevelItem(parent_item)

            for _, lot_row in lots_by_stock.get(row["stock_id"], pd.DataFrame()).iterrows():
                child_item = _NumericTreeWidgetItem()
                for col_idx, value in enumerate(self._format_inventory_row(lot_row, is_lot=True)):
                    child_item.setText(col_idx, value)
                    if col_idx in _INVENTORY_TREE_NUMERIC_COLUMNS:
                        child_item.setTextAlignment(col_idx, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                child_item.setData(0, Qt.ItemDataRole.UserRole, row["stock_id"])
                child_item.setData(0, Qt.ItemDataRole.UserRole + 1, int(lot_row["id"]))
                parent_item.addChild(child_item)

            if row["stock_id"] in expanded_stock_ids:
                parent_item.setExpanded(True)

        tree.setSortingEnabled(True)

    def _selected_inventory_lot_ids(self) -> list[int]:
        """只收集「有UserRole+1(lot id)資料」的選取item——父列(股票彙總列)沒有
        設這個role的值，data()查不到會回傳None，自然被濾掉，不會被誤當成
        可編輯/刪除的批次。"""
        return [
            lot_id
            for item in self.inventory_tree.selectedItems()
            if (lot_id := item.data(0, Qt.ItemDataRole.UserRole + 1)) is not None
        ]

    def _selected_inventory_stock_ids(self) -> list[str]:
        """取選取item(父列或子列皆可)所屬的股票代號——子列的股票代號欄位本身是
        留空的(見_format_inventory_row())，所以這裡讀UserRole(兩種item都有設
        值)，不是讀顯示文字。"""
        stock_ids = {item.data(0, Qt.ItemDataRole.UserRole) for item in self.inventory_tree.selectedItems()}
        return sorted(s for s in stock_ids if s)

    def _refresh_inventory_tab(self) -> None:
        if self.conn is None or self.portfolio_conn is None:
            return
        lots_df = portfolio_data.load_inventory_lots(self.conn, self.portfolio_conn)
        summary_df = portfolio_data.load_inventory_summary(self.conn, self.portfolio_conn)
        self._populate_inventory_tree(summary_df, lots_df)
        self.inventory_summary_label.setText(
            self._portfolio_summary_text(lots_df, "總持股成本", "總市值", "累積總損益"),
        )

    def _on_inventory_add(self) -> None:
        if self.portfolio_conn is None:
            return
        dialog = _StockEditDialog(self.conn, "新增庫存批次", parent=self, is_inventory=True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = dialog.values()
            portfolio_storage.add_inventory_stock(
                self.portfolio_conn, values["stock_id"], values["buy_date"],
                values["cost_price"], values["shares"], values["fee"], values["note"],
            )
            self._refresh_inventory_tab()

    def _on_inventory_edit_selected(self) -> None:
        if self.portfolio_conn is None:
            return
        lot_ids = self._selected_inventory_lot_ids()
        if len(lot_ids) != 1:
            QMessageBox.information(self, "編輯庫存", "請展開後選取一筆要編輯的批次。")
            return
        lot_id = lot_ids[0]
        existing = portfolio_storage.get_inventory_lot(self.portfolio_conn, lot_id)
        if existing is None:
            return
        dialog = _StockEditDialog(self.conn, "編輯庫存批次", initial=existing, parent=self, is_inventory=True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = dialog.values()
            portfolio_storage.update_inventory_stock(
                self.portfolio_conn, lot_id, values["buy_date"], values["cost_price"],
                values["shares"], values["fee"], values["note"],
            )
            self._refresh_inventory_tab()

    def _on_inventory_delete_selected(self) -> None:
        if self.portfolio_conn is None:
            return
        lot_ids = self._selected_inventory_lot_ids()
        if not lot_ids:
            QMessageBox.information(self, "刪除庫存", "請展開後選取要刪除的批次。")
            return
        if QMessageBox.question(
            self, "刪除庫存", f"確定要刪除{len(lot_ids)}筆批次紀錄嗎？",
        ) != QMessageBox.StandardButton.Yes:
            return
        for lot_id in lot_ids:
            portfolio_storage.delete_inventory_stock(self.portfolio_conn, lot_id)
        self._refresh_inventory_tab()

    def _on_inventory_add_to_watchlist(self) -> None:
        """庫存清單「加入觀察清單」：選取的股票(不管是父列還是子列)加進使用者
        勾選的一個或多個觀察清單群組——單向操作(庫存→觀察清單)，比照ref-project
        沒有反向的「轉為庫存」功能，這次也不做。
        """
        self._add_stocks_to_watchlist_via_dialog(self._selected_inventory_stock_ids())

    def _add_stocks_to_watchlist_via_dialog(self, stock_ids: list[str]) -> None:
        """共用的「選擇觀察清單群組(可複選)後批次加入」對話框，2026-08-04從
        _on_inventory_add_to_watchlist()抽出來，供「選股」分頁的「加入觀察清單」
        (_on_candidates_add_to_watchlist())共用，避免維護兩份幾乎一樣的群組
        勾選清單UI。
        """
        if self.portfolio_conn is None:
            return
        if not stock_ids:
            QMessageBox.information(self, "加入觀察清單", "請先選取要加入的股票。")
            return
        groups = portfolio_storage.list_watchlist_groups(self.portfolio_conn)
        if not groups:
            QMessageBox.information(self, "加入觀察清單", "目前沒有任何觀察清單群組，請先到「觀察清單」分頁新增群組。")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("加入觀察清單")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"把 {len(stock_ids)} 檔股票加入以下群組（可複選）："))
        list_widget = QListWidget()
        for group in groups:
            item = QListWidgetItem(group["group_name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, group["id"])
            list_widget.addItem(item)
        layout.addWidget(list_widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_group_ids = [
            list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(list_widget.count())
            if list_widget.item(i).checkState() == Qt.CheckState.Checked
        ]
        if not selected_group_ids:
            return
        portfolio_storage.add_stocks_to_watchlist(self.portfolio_conn, selected_group_ids, stock_ids)
        QMessageBox.information(self, "加入觀察清單", "已加入選取的群組。")

    def _build_watchlist_tab(self) -> None:
        """「觀察清單」分頁：想追蹤但還沒買的股票，支援多個群組(例如「半導體」
        「金融股」分開放，比照ref-project)。表格結構/資料來源跟庫存清單同構，
        只是多了群組管理，且成本價/持股數欄位標題改成「參考」開頭，強調不是
        真的持股。
        """
        watchlist_scroll = QScrollArea()
        watchlist_scroll.setWidgetResizable(True)
        self.tabs.addTab(watchlist_scroll, "觀察清單")

        watchlist_content = QWidget()
        watchlist_scroll.setWidget(watchlist_content)
        watchlist_layout = QVBoxLayout(watchlist_content)

        group_bar = QHBoxLayout()
        group_bar.addWidget(QLabel("群組："))
        self.watchlist_group_combo = QComboBox()
        self.watchlist_group_combo.currentIndexChanged.connect(self._refresh_watchlist_tab)
        group_bar.addWidget(self.watchlist_group_combo)
        add_group_btn = QPushButton("新增群組")
        add_group_btn.clicked.connect(self._on_watchlist_add_group)
        rename_group_btn = QPushButton("重新命名")
        rename_group_btn.clicked.connect(self._on_watchlist_rename_group)
        delete_group_btn = QPushButton("刪除群組")
        delete_group_btn.clicked.connect(self._on_watchlist_delete_group)
        for btn in (add_group_btn, rename_group_btn, delete_group_btn):
            group_bar.addWidget(btn)
        group_bar.addStretch()
        watchlist_layout.addLayout(group_bar)

        # 2026-08-06拿掉「總參考成本/總觀察市值/累積預估損益/今日資產變動」摘要列——
        # 跟表格拿掉的成本價/持股數/市值/帳面損益/報酬率欄位同一個理由，觀察清單不是
        # 真的持股，成本價/持股數輸入框也一起拿掉了(見_StockEditDialog)，這行摘要
        # 之後只會永遠顯示0，留著沒有意義。

        toolbar = QHBoxLayout()
        add_btn = QPushButton("新增")
        add_btn.clicked.connect(self._on_watchlist_add_stock)
        edit_btn = QPushButton("編輯選取")
        edit_btn.clicked.connect(self._on_watchlist_edit_selected)
        delete_btn = QPushButton("刪除選取")
        delete_btn.clicked.connect(self._on_watchlist_delete_selected)
        refresh_btn = QPushButton("🔄 重新整理")
        refresh_btn.clicked.connect(self._refresh_watchlist_tab)
        for btn in (add_btn, edit_btn, delete_btn, refresh_btn):
            toolbar.addWidget(btn)
        # 「欄位顯示」下拉選單：2026-08-04新增，籌碼面(黃豐凱籌碼分析法D~R共14欄)
        # 接進來後整張表變得很寬，讓使用者自己選要看哪些欄位。技術上用QToolButton+
        # QMenu(內含checkable QAction，技術面/籌碼面用子選單)組出「下拉+樹狀勾選」
        # 的效果——Qt沒有原生的下拉樹狀勾選元件。見_build_watchlist_column_menu()。
        self.watchlist_column_button = QToolButton()
        self.watchlist_column_button.setText("欄位顯示 ▾")
        self.watchlist_column_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._build_watchlist_column_menu()
        toolbar.addWidget(self.watchlist_column_button)
        # 「匯出到Google Sheet」：2026-08-04新增，見WatchlistExportWorker/
        # src/presentation/watchlist_export.py——把全部觀察清單群組(不只目前選取的
        # 這個)各自匯出成一個分頁，跟daily_pipeline.py的自動排程共用同一套匯出邏輯。
        self.watchlist_export_btn = QPushButton("匯出到Google Sheet")
        self.watchlist_export_btn.setToolTip("把全部觀察清單群組跟選股清單匯出到Google Sheet，各自獨立分頁")
        self.watchlist_export_btn.clicked.connect(self._on_watchlist_export_clicked)
        toolbar.addWidget(self.watchlist_export_btn)
        toolbar.addStretch()
        # 「資料更新至」：比照「大盤」分頁既有的做法(見_build_market_tab())，放在
        # 工具列最右邊、灰色小字。
        self.watchlist_update_label = QLabel("")
        self.watchlist_update_label.setStyleSheet("color: #666666;")
        toolbar.addWidget(self.watchlist_update_label)
        watchlist_layout.addLayout(toolbar)

        self.watchlist_table = self._build_portfolio_table(
            ["股票代號", "名稱", "現價", "漲跌幅(%)", "SAR狀態", "SAR距離%"]
            + _HUANG_CHIP_HEADERS,
            stretch_column="名稱",
        )
        self.watchlist_group_header_table = self._build_watchlist_group_header_table()
        watchlist_layout.addWidget(self.watchlist_group_header_table)
        watchlist_layout.addWidget(self.watchlist_table, stretch=1)

        # 2026-08-04新增：大戶/散戶持股分布(F/G)目前只有觀察清單裡的股票會自動更新
        # (見_maybe_fetch_missing_holder_shares())，非觀察清單股票沒有排程/回補機制
        # 補這塊資料(FinMind API token是朋友付費的，尚不可隨意擴大用量)——使用者要求
        # 在這裡放一個常駐提醒，隨時看得到這個限制，不用等到忘記了才發現資料是空的。
        holder_share_scope_hint = QLabel("ps: 大戶/散戶持股變化僅支持觀察清單")
        holder_share_scope_hint.setStyleSheet("color: #666666;")
        watchlist_layout.addWidget(holder_share_scope_hint)

        # Delete刪除快捷鍵：2026-08-04新增，比照庫存清單/ref-project的做法，重用
        # 「刪除選取」按鈕同一組handler。觀察清單目前只移植Delete(使用者這次沒有
        # 要求F2)，「編輯選取」維持只能點按鈕。
        self.watchlist_delete_shortcut = QShortcut(QKeySequence("Delete"), self.watchlist_table)
        self.watchlist_delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.watchlist_delete_shortcut.activated.connect(self._on_watchlist_delete_selected)

        self._restore_watchlist_column_visibility()
        self._reload_watchlist_groups()

    def _build_watchlist_group_header_table(self) -> QTableWidget:
        """黃豐凱籌碼分析法欄位的「雙列表頭」：QHeaderView本身不支援跨欄合併儲存格，
        這裡改用「疊一個獨立的1列QTableWidget在主表格正上方，欄數/欄寬/水平捲動都
        跟主表格同步」的常見Qt多層表頭技巧(見_sync_watchlist_group_header()、
        _on_watchlist_column_toggled())，不是動主表格本身的資料列——那樣會跟現有
        「點欄位標題排序」衝突(排序會把假標頭列當成普通資料一起排進去，沒辦法固定
        釘在最上面)。

        這個表格本身隱藏了自己的水平/垂直標頭(row數字、column名稱都不顯示，主表格
        的QHeaderView本來就會顯示真正的欄位名稱)、不可選取/不可編輯、沒有自己的
        捲軸(水平捲動完全由主表格的捲軸帶動，見_build_watchlist_tab()的訊號連接)，
        單純拿QTableWidget的cell/span機制來畫「一整排跨欄合併的分類色塊」。
        """
        table = QTableWidget()
        table.setRowCount(1)
        table.setColumnCount(self.watchlist_table.columnCount())
        table.horizontalHeader().hide()
        # ⚠️ 2026-08-04修正：原本用verticalHeader().hide()，結果這排標籤全部往左偏移了
        # 一整欄——主表格的QTableWidget左邊還有原生的「列號」欄(1、2、3...這欄，
        # 使用者用截圖圈起來抓到這個問題)，那欄本身也佔寬度，隱藏掉自己的垂直標頭
        # 等於少算了這段寬度，導致這裡的欄位位置跟主表格的欄位對不齊。改成不隱藏、
        # 但寬度跟主表格的列號欄同步(見_sync_watchlist_group_header())、內容留空，
        # 讓這欄「佔位但不顯示數字」，兩個表格的欄位位置才會真正對齊。
        table.setVerticalHeaderItem(0, QTableWidgetItem(""))
        table.setFixedHeight(30)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setShowGrid(False)  # 不要讓合併儲存格範圍內還看得到格線，才會像一整塊色塊
        table.setStyleSheet(
            "QTableWidget { border: none; gridline-color: transparent; }"
            "QTableWidget::item { padding: 0px; }"
            "QHeaderView::section { background: transparent; border: none; }"
        )

        for label, color, cols in _WATCHLIST_CHIP_GROUP_LABELS:
            start_col = cols[0]
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setBackground(QColor(color))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            table.setItem(0, start_col, item)
            if len(cols) > 1:
                table.setSpan(0, start_col, 1, len(cols))
        for col in range(table.columnCount()):
            if table.item(0, col) is None:
                table.setItem(0, col, QTableWidgetItem(""))

        # 水平捲動完全跟著主表格走：使用者橫向捲動主表格時，這排分類標籤要跟著同步
        # 移動，不然畫面看起來像是標籤「脫離」了底下真正的欄位。
        self.watchlist_table.horizontalScrollBar().valueChanged.connect(table.horizontalScrollBar().setValue)
        return table

    def _sync_watchlist_group_header(self) -> None:
        """把主表格目前每一欄的實際寬度/隱藏狀態複製到分類標籤列——這個表格的欄位是
        ResizeToContents(依內容自動調寬)+「名稱」欄Stretch，使用者不能用滑鼠拖曳
        調欄寬，所以欄寬只會在①重新整理資料後(內容變寬變窄)、②「欄位顯示」被切換
        後這兩個時機改變，呼叫端在這兩處呼叫這個方法就夠，不需要即時監聽resize事件。

        連主表格「列號」欄(最左邊顯示1、2、3...的原生欄位)的寬度也要一起同步——這欄
        的寬度會隨列數增加而變寬(例如超過9列後要多留一位數的空間給"10")，兩邊沒對齊
        會讓整排標籤跟著往左或往右偏移，見_build_watchlist_group_header_table()的
        修正說明。
        """
        header_table = self.watchlist_group_header_table
        main_table = self.watchlist_table
        if header_table.columnCount() != main_table.columnCount():
            header_table.setColumnCount(main_table.columnCount())
        header_table.verticalHeader().setFixedWidth(main_table.verticalHeader().width())
        for col in range(main_table.columnCount()):
            header_table.setColumnWidth(col, main_table.columnWidth(col))
            header_table.setColumnHidden(col, main_table.isColumnHidden(col))

    def _app_settings(self) -> QSettings:
        """整個桌面版共用的持久化設定(QSettings，Windows上實際存在登錄檔)——2026-08-04
        新增，使用者要求「記錄選股/觀察清單的勾選動作，這樣比較便利」，不用每次重開
        APP都要重新勾一次篩選條件/欄位顯示。目前只有這兩個地方在用，不需要另外包一層
        單例快取，QSettings本身建構成本很低。
        """
        return QSettings("tw_stock", "desktop")

    def _add_watchlist_column_checkbox(self, parent_menu: QMenu, label: str, key: str, columns: list[int]) -> None:
        """把一個可勾選項目加進選單，用QWidgetAction包一個真正的QCheckBox元件，不是
        單純的checkable QAction——2026-08-04改版：使用者反映用checkable QAction時，
        每點一下勾選框選單就整個關閉，要重新點「欄位顯示」才能繼續勾下一個，體驗很差。
        QMenu預設行為是「任何QAction被觸發就關閉選單」，但點擊QWidgetAction包住的
        真正widget(這裡是QCheckBox)屬於widget自己處理滑鼠事件，不會觸發QAction的
        triggered訊號，QMenu因此不會跟著關閉——這是Qt的標準做法，不是workaround。
        選單會在使用者點擊選單以外的地方時自然關閉(QMenu本身的既有行為)。
        """
        checkbox = QCheckBox(label, parent_menu)
        checkbox.setChecked(True)
        checkbox.toggled.connect(lambda checked, k=key, c=columns: self._on_watchlist_column_toggled(k, c, checked))
        widget_action = QWidgetAction(parent_menu)
        widget_action.setDefaultWidget(checkbox)
        parent_menu.addAction(widget_action)
        self._watchlist_column_actions[key] = (checkbox, columns)

    def _build_watchlist_column_menu(self) -> None:
        """組出「欄位顯示」下拉選單：Qt沒有原生的「下拉+樹狀勾選」元件，這裡用
        QToolButton+QMenu(內含QWidgetAction包的QCheckBox，技術面/籌碼面用子選單
        QMenu.addMenu())組出同樣的效果，見_add_watchlist_column_checkbox()。
        「全部顯示」是一般(非checkable)action，點一下把其餘所有勾選框都設成勾選；
        「股票代號/名稱/現價/漲跌幅」是識別用欄位，不放進選單、永遠顯示；「備註」
        欄位使用者要求直接拿掉，不是保留但不可切換。

        每個checkbox的toggled訊號都接到_on_watchlist_column_toggled()，依欄位索引
        清單顯示/隱藏對應的QTableWidget欄位，並把狀態存進QSettings，供
        _restore_watchlist_column_visibility()下次開啟APP時還原。
        """
        menu = QMenu(self.watchlist_column_button)
        self._watchlist_column_actions: dict[str, tuple[QCheckBox, list[int]]] = {}

        show_all_action = menu.addAction("全部顯示")
        show_all_action.triggered.connect(self._on_watchlist_show_all_columns)
        menu.addSeparator()

        for label, cols in _WATCHLIST_COLUMN_TOGGLE_GROUPS.items():
            self._add_watchlist_column_checkbox(menu, label, label, cols)

        tech_menu = menu.addMenu("技術面")
        self._add_watchlist_column_checkbox(tech_menu, "SAR狀態／SAR距離%", "技術面", _WATCHLIST_TECH_TOGGLE_COLUMNS)

        chip_menu = menu.addMenu("籌碼面")
        self._add_watchlist_column_checkbox(
            chip_menu, "投信/外資/大戶/散戶/均線/週K/買賣超張數", "籌碼面", _WATCHLIST_CHIP_TOGGLE_COLUMNS
        )

        self.watchlist_column_button.setMenu(menu)

    def _on_watchlist_show_all_columns(self) -> None:
        for checkbox, _cols in self._watchlist_column_actions.values():
            checkbox.setChecked(True)

    def _on_watchlist_column_toggled(self, group_key: str, columns: list[int], checked: bool) -> None:
        for col in columns:
            self.watchlist_table.setColumnHidden(col, not checked)
        self._app_settings().setValue(f"watchlist/column_visible/{group_key}", checked)
        self._sync_watchlist_group_header()

    def _restore_watchlist_column_visibility(self) -> None:
        settings = self._app_settings()
        for group_key, (checkbox, _cols) in self._watchlist_column_actions.items():
            checked = settings.value(f"watchlist/column_visible/{group_key}", True, type=bool)
            checkbox.setChecked(checked)  # 觸發toggled，連帶套用setColumnHidden()

    def _on_watchlist_export_clicked(self) -> None:
        if self._watchlist_export_worker is not None and self._watchlist_export_worker.isRunning():
            return
        self.watchlist_export_btn.setEnabled(False)
        self.watchlist_export_btn.setText("匯出中...")
        self._watchlist_export_worker = WatchlistExportWorker()
        self._watchlist_export_worker.finished_ok.connect(self._on_watchlist_export_finished)
        self._watchlist_export_worker.failed.connect(self._on_watchlist_export_failed)
        self._watchlist_export_worker.start()

    def _on_watchlist_export_finished(self, group_count: int) -> None:
        self.watchlist_export_btn.setEnabled(True)
        self.watchlist_export_btn.setText("匯出到Google Sheet")
        QMessageBox.information(self, "匯出完成", f"已匯出{group_count}個觀察清單群組到Google Sheet。")

    def _on_watchlist_export_failed(self, error_message: str) -> None:
        self.watchlist_export_btn.setEnabled(True)
        self.watchlist_export_btn.setText("匯出到Google Sheet")
        QMessageBox.critical(self, "匯出失敗", error_message)

    def _reload_watchlist_groups(self) -> None:
        """重新整理群組下拉選單，找不到任何群組時自動建立一個「預設觀察清單」
        (比照ref-project第一次使用時的行為)。盡量保留使用者目前選取的群組，找
        不到才退回選第一個(例如剛好是自己被刪除的那個群組)。
        """
        if self.portfolio_conn is None:
            return
        groups = portfolio_storage.list_watchlist_groups(self.portfolio_conn)
        if not groups:
            portfolio_storage.add_watchlist_group(self.portfolio_conn, "預設觀察清單")
            groups = portfolio_storage.list_watchlist_groups(self.portfolio_conn)
        current_selection = self.watchlist_group_combo.currentData()
        self.watchlist_group_combo.blockSignals(True)
        self.watchlist_group_combo.clear()
        for group in groups:
            self.watchlist_group_combo.addItem(group["group_name"], group["id"])
        if current_selection is not None:
            idx = self.watchlist_group_combo.findData(current_selection)
            if idx >= 0:
                self.watchlist_group_combo.setCurrentIndex(idx)
        self.watchlist_group_combo.blockSignals(False)

    def _refresh_watchlist_tab(self) -> None:
        if self.conn is None or self.portfolio_conn is None:
            return
        group_id = self.watchlist_group_combo.currentData()
        if group_id is None:
            self.watchlist_table.setRowCount(0)
            return
        df = portfolio_data.load_watchlist(self.conn, self.portfolio_conn, group_id)
        self._populate_portfolio_table(self.watchlist_table, df)
        self._populate_huang_chip_columns(self.watchlist_table, df)
        self._sync_watchlist_group_header()
        self.watchlist_update_label.setText(
            f"資料更新至：{self._format_update_timestamp(chart_data.get_latest_update_time(self.conn))}"
        )
        self._maybe_fetch_missing_holder_shares(list(df["stock_id"]))

    def _maybe_fetch_missing_holder_shares(self, stock_ids: list[str]) -> None:
        """「重新整理」除了重新查詢本地DB畫表格，也順便偵測「剛加入觀察清單、本地DB
        完全查無F/G資料」的股票並背景即時補抓——不然使用者要等到隔天17:00排程才看得到
        剛加入股票的F/G欄位(見src/data/holder_shares_sync.py的說明)。查詢本身
        (list_stock_ids_without_holder_data)純查本地DB，不是API呼叫，只有真的發現
        缺資料時才會背景呼叫FinMind，不會拖慢「重新整理」本身的畫面反應。
        """
        if self.conn is None:
            return
        if self._holder_fetch_worker is not None and self._holder_fetch_worker.isRunning():
            return
        from src.data.holder_shares_sync import list_stock_ids_without_holder_data

        candidates = [sid for sid in stock_ids if sid not in self._holder_fetch_attempted_stock_ids]
        missing = list_stock_ids_without_holder_data(self.conn, candidates)
        if not missing:
            return
        self._holder_fetch_attempted_stock_ids.update(missing)
        self._holder_fetch_worker = HolderShareFetchWorker(missing)
        self._holder_fetch_worker.finished_ok.connect(self._on_holder_fetch_finished)
        self._holder_fetch_worker.failed.connect(self._on_holder_fetch_failed)
        self._holder_fetch_worker.start()

    def _on_holder_fetch_finished(self, count: int) -> None:
        # 只有真的補到資料、而且使用者現在還停留在觀察清單分頁時才重新整理畫面，
        # 跟_check_for_external_watchlist_update()同一種「只在目前分頁時才重繪」考量。
        if count > 0 and self.tabs.currentIndex() == TAB_WATCHLIST:
            self._refresh_watchlist_tab()

    def _on_holder_fetch_failed(self, error_message: str) -> None:
        # 不彈窗——這是背景自動補抓，不是使用者主動觸發的動作，失敗只印出訊息即可，
        # 不應該用彈窗打斷使用者，跟其餘背景/排程性質的失敗處理一致。
        print(f"觀察清單新股票F/G即時補抓失敗（略過）：{error_message}")

    def _set_chip_text_item(self, table: QTableWidget, row_idx: int, col: int, label: dict | None) -> None:
        """label是{"text","color"}字典(或None)——黃豐凱籌碼分析法的判讀函式共用格式，
        見src/indicators/huang_chip_signals.py。text為空字串或label本身是None都顯示
        「-」，跟表格其餘欄位「查無資料顯示-」的既有慣例一致。"""
        text = label["text"] if label and label.get("text") else "-"
        item = QTableWidgetItem(text)
        if label and label.get("text"):
            item.setForeground(QColor(label["color"]))
        table.setItem(row_idx, col, item)

    def _populate_huang_chip_columns(self, table: QTableWidget, df: pd.DataFrame) -> None:
        """觀察清單表格既有12欄之後，接上黃豐凱籌碼分析法的D~R欄位(不含手動的J欄，
        見src/presentation/huang_chip_data.py)。跟_populate_portfolio_table()(庫存
        清單也在共用)分開處理，只影響觀察清單，不會動到庫存清單。逐股查詢本地DB
        (觀察清單股票數量少，成本可忽略，不需要背景執行緒)。
        """
        if self.conn is None:
            return
        base = _PORTFOLIO_BASE_COLUMN_COUNT
        flow_keys = [
            "foreign_40d", "invest_40d", "foreign_20d", "invest_20d",
            "foreign_10d", "invest_10d", "foreign_5d", "invest_5d",
        ]
        table.setSortingEnabled(False)
        for row_idx, stock_id in enumerate(df["stock_id"]):
            chip_row = huang_chip_data.load_huang_chip_row(self.conn, stock_id)

            self._set_chip_text_item(table, row_idx, base + 0, chip_row["invest_streak"])
            self._set_chip_text_item(table, row_idx, base + 1, chip_row["foreign_streak"])
            holder = chip_row["holder_change"]
            self._set_chip_text_item(table, row_idx, base + 2, holder["whale"] if holder else None)
            self._set_chip_text_item(table, row_idx, base + 3, holder["retail"] if holder else None)

            ma = chip_row["ma_price_position"]
            ma_text = "\n".join(line["text"] for line in ma["lines"]) if ma else "-"
            table.setItem(row_idx, base + 4, QTableWidgetItem(ma_text))

            weekly = chip_row["weekly_volume_pattern"]
            weekly_text = f"{weekly['pattern']}\n（{weekly['reference_week_start']}）" if weekly else "-"
            table.setItem(row_idx, base + 5, QTableWidgetItem(weekly_text))

            flow = chip_row["flow"]
            for i, key in enumerate(flow_keys):
                col = base + 6 + i
                if flow is None:
                    item = _NumericTableWidgetItem("-")
                else:
                    value = flow[key]
                    item = _NumericTableWidgetItem(f"{value:,}")
                    item.setForeground(QColor(COLOR_BUY if value >= 0 else COLOR_SELL))
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(row_idx, col, item)
        table.setSortingEnabled(True)
        table.resizeRowsToContents()

    def _build_backfill_tab(self) -> None:
        """「回補資料」分頁：取代原本只能下命令列跑scripts/backfill_*.py的回補流程。

        2026-08-04設計定案(多輪討論)：大盤跟個股共用同一個日期區間、同一個「開始回補」
        按鈕，用勾選框決定要補哪些項目——不是分成兩個獨立按鈕，理由是使用者實際會用到
        回補功能的情境(發現缺資料、需要更早期歷史)通常會想同時補大盤+個股。「強制覆蓋」
        預設不勾：DB已有資料的日期/股票就跳過、不呼叫API。「同時回補歷史候選清單訊號」
        預設不勾(較耗時)：回補股價後daily_indicators(均線/SAR快取)一定會自動重算，但
        daily_candidates(選股規則比對)預設不會，因為那是逐日重算的相對昂貴操作，見
        BackfillWorker/run_screen_and_store_for_range()的說明。
        """
        backfill_scroll = QScrollArea()
        backfill_scroll.setWidgetResizable(True)
        self.tabs.addTab(backfill_scroll, "回補資料")

        backfill_content = QWidget()
        backfill_scroll.setWidget(backfill_content)
        layout = QVBoxLayout(backfill_content)

        date_bar = QHBoxLayout()
        date_bar.addWidget(QLabel("日期區間："))
        self.backfill_start_date = QDateEdit()
        self.backfill_start_date.setCalendarPopup(True)
        self.backfill_start_date.setDisplayFormat("yyyy-MM-dd")
        self.backfill_start_date.setDate(QDate.currentDate().addDays(-30))
        date_bar.addWidget(self.backfill_start_date)
        date_bar.addWidget(QLabel("～"))
        self.backfill_end_date = QDateEdit()
        self.backfill_end_date.setCalendarPopup(True)
        self.backfill_end_date.setDisplayFormat("yyyy-MM-dd")
        self.backfill_end_date.setDate(QDate.currentDate())
        date_bar.addWidget(self.backfill_end_date)
        self.backfill_force_overwrite_checkbox = QCheckBox("強制覆蓋")
        date_bar.addWidget(self.backfill_force_overwrite_checkbox)
        date_bar.addStretch()
        layout.addLayout(date_bar)
        force_overwrite_hint = QLabel("不勾選：DB已有資料的日期會跳過，不呼叫API")
        force_overwrite_hint.setStyleSheet("color: #666666;")
        layout.addWidget(force_overwrite_hint)

        scope_bar = QHBoxLayout()
        scope_bar.addWidget(QLabel("股票範圍(個股)："))
        self.backfill_scope_all_radio = QRadioButton("全市場")
        self.backfill_scope_all_radio.setChecked(True)
        self.backfill_scope_custom_radio = QRadioButton("指定股票代號")
        self._backfill_scope_group = QButtonGroup(self)
        self._backfill_scope_group.addButton(self.backfill_scope_all_radio)
        self._backfill_scope_group.addButton(self.backfill_scope_custom_radio)
        scope_bar.addWidget(self.backfill_scope_all_radio)
        scope_bar.addWidget(self.backfill_scope_custom_radio)
        self.backfill_stock_codes_input = QLineEdit()
        self.backfill_stock_codes_input.setPlaceholderText("多筆用逗號分隔，例如 2330,2454,6488")
        self.backfill_stock_codes_input.setEnabled(False)
        scope_bar.addWidget(self.backfill_stock_codes_input, stretch=1)
        layout.addLayout(scope_bar)
        self.backfill_scope_custom_radio.toggled.connect(self.backfill_stock_codes_input.setEnabled)

        layout.addWidget(QLabel("回補項目："))
        items_row1 = QHBoxLayout()
        self.backfill_taiex_checkbox = QCheckBox("大盤股價")
        self.backfill_taiex_checkbox.setChecked(True)
        items_row1.addWidget(self.backfill_taiex_checkbox)
        items_row1.addStretch()
        layout.addLayout(items_row1)

        items_row2 = QHBoxLayout()
        self.backfill_stock_price_checkbox = QCheckBox("個股股價明細")
        self.backfill_stock_price_checkbox.setChecked(True)
        self.backfill_stock_institutional_checkbox = QCheckBox("個股三大法人買賣超")
        self.backfill_stock_institutional_checkbox.setChecked(True)
        items_row2.addWidget(self.backfill_stock_price_checkbox)
        items_row2.addWidget(self.backfill_stock_institutional_checkbox)
        items_row2.addStretch()
        layout.addLayout(items_row2)

        items_row3 = QHBoxLayout()
        self.backfill_stock_margin_checkbox = QCheckBox("個股融資融券(資券)")
        self.backfill_stock_margin_checkbox.setChecked(True)
        self.backfill_stock_broker_checkbox = QCheckBox("個股分點進出籌碼")
        self.backfill_stock_broker_checkbox.setEnabled(False)
        self.backfill_stock_broker_checkbox.setToolTip("需FinMind付費方案，尚未開通")
        items_row3.addWidget(self.backfill_stock_margin_checkbox)
        items_row3.addWidget(self.backfill_stock_broker_checkbox)
        items_row3.addStretch()
        layout.addLayout(items_row3)

        self.backfill_recompute_candidates_checkbox = QCheckBox("同時回補歷史候選清單訊號（較耗時，見下方說明）")
        layout.addWidget(self.backfill_recompute_candidates_checkbox)
        candidates_hint = QLabel(
            "候選清單平常只在「今天」執行排程時計算，回補的歷史日期預設不會自動產生候選清單紀錄——\n"
            "如果你需要回頭查某個歷史日期「當時」符合哪些規則，才需要勾選這個，會依日期區間逐日重算，\n"
            "股票多、天數多時非常耗時。"
        )
        candidates_hint.setStyleSheet("color: #666666;")
        layout.addWidget(candidates_hint)

        warning_label = QLabel("⚠ 上櫃法人/資券逐股查詢，股票數多時可能耗時較久")
        layout.addWidget(warning_label)
        auto_indicator_label = QLabel("ⓘ 股價回補完成後會自動重算均線/SAR快取，不用另外操作")
        layout.addWidget(auto_indicator_label)

        self.backfill_start_btn = QPushButton("開始回補")
        self.backfill_start_btn.clicked.connect(self._on_backfill_start)
        layout.addWidget(self.backfill_start_btn)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep1)

        layout.addWidget(QLabel("【股票基本資料】名稱/產業別/市場別"))
        stock_info_hint = QLabel("ⓘ 每日排程已會更新，通常不需要手動執行")
        stock_info_hint.setStyleSheet("color: #666666;")
        layout.addWidget(stock_info_hint)
        self.backfill_refresh_stock_info_btn = QPushButton("重新整理股票清單")
        self.backfill_refresh_stock_info_btn.clicked.connect(self._on_backfill_refresh_stock_info)
        layout.addWidget(self.backfill_refresh_stock_info_btn)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)

        layout.addWidget(QLabel("【執行進度】"))
        self.backfill_status_label = QLabel("狀態：閒置")
        layout.addWidget(self.backfill_status_label)
        self.backfill_log = QTextEdit()
        self.backfill_log.setReadOnly(True)
        self.backfill_log.setFixedHeight(160)
        layout.addWidget(self.backfill_log)
        self.backfill_cancel_btn = QPushButton("取消")
        self.backfill_cancel_btn.setEnabled(False)
        self.backfill_cancel_btn.clicked.connect(self._on_backfill_cancel_clicked)
        layout.addWidget(self.backfill_cancel_btn)

    def _collect_backfill_params(self) -> dict | None:
        start = self.backfill_start_date.date().toPython().isoformat()
        end = self.backfill_end_date.date().toPython().isoformat()
        if start > end:
            QMessageBox.warning(self, "日期區間錯誤", "開始日期不能晚於結束日期。")
            return None

        stock_id_filter = None
        if self.backfill_scope_custom_radio.isChecked():
            codes = {c.strip() for c in self.backfill_stock_codes_input.text().split(",") if c.strip()}
            if not codes:
                QMessageBox.warning(self, "股票範圍錯誤", "請輸入至少一個股票代號，或改選「全市場」。")
                return None
            stock_id_filter = codes

        taiex_price = self.backfill_taiex_checkbox.isChecked()
        stock_price = self.backfill_stock_price_checkbox.isChecked()
        stock_institutional = self.backfill_stock_institutional_checkbox.isChecked()
        stock_margin = self.backfill_stock_margin_checkbox.isChecked()
        if not (taiex_price or stock_price or stock_institutional or stock_margin):
            QMessageBox.warning(self, "尚未勾選回補項目", "請至少勾選一項回補項目。")
            return None

        return {
            "start": start, "end": end,
            "force_overwrite": self.backfill_force_overwrite_checkbox.isChecked(),
            "stock_id_filter": stock_id_filter,
            "taiex_price": taiex_price, "stock_price": stock_price,
            "stock_institutional": stock_institutional, "stock_margin": stock_margin,
            "recompute_candidates": self.backfill_recompute_candidates_checkbox.isChecked(),
        }

    def _on_backfill_start(self) -> None:
        if self._backfill_worker is not None and self._backfill_worker.isRunning():
            return
        params = self._collect_backfill_params()
        if params is None:
            return

        self.backfill_log.clear()
        self.backfill_status_label.setText("狀態：回補中...")
        self.backfill_start_btn.setEnabled(False)
        self.backfill_cancel_btn.setEnabled(True)

        self._backfill_worker = BackfillWorker(params)
        self._backfill_worker.progress.connect(self._on_backfill_progress)
        self._backfill_worker.finished_ok.connect(self._on_backfill_finished)
        self._backfill_worker.failed.connect(self._on_backfill_failed)
        self._backfill_worker.cancelled.connect(self._on_backfill_cancelled)
        self._backfill_worker.start()

    def _on_backfill_progress(self, message: str) -> None:
        self.backfill_log.append(message)

    def _on_backfill_finished(self, summary: dict) -> None:
        self.backfill_status_label.setText("狀態：閒置")
        self.backfill_start_btn.setEnabled(True)
        self.backfill_cancel_btn.setEnabled(False)
        parts = []
        if "taiex_dates" in summary:
            parts.append(f"大盤{summary['taiex_dates']}筆")
        if "indicators" in summary:
            parts.append(f"均線/SAR快取{summary['indicators']}筆")
        if "candidates" in summary:
            parts.append(f"歷史候選清單{summary['candidates']}筆")
        detail = "、".join(parts) if parts else "沒有新資料寫入"
        self.backfill_log.append(f"完成：{detail}")
        # 回補可能動到目前正在看的股票/大盤/候選清單，比照_on_fetch_finished()視情況刷新
        self._refresh_date_list()
        self._reload_candidates()
        current_tab = self.tabs.currentIndex()
        if current_tab == TAB_STOCK_DETAIL and self._current_stock_id:
            self._rerender_chart()
        elif current_tab == TAB_MARKET:
            self._refresh_market_tab()
        QMessageBox.information(self, "完成", f"回補完成：{detail}")

    def _on_backfill_failed(self, message: str) -> None:
        self.backfill_status_label.setText("狀態：閒置")
        self.backfill_start_btn.setEnabled(True)
        self.backfill_cancel_btn.setEnabled(False)
        self.backfill_log.append(f"失敗：{message}")
        QMessageBox.warning(self, "失敗", f"回補失敗：{message}")

    def _on_backfill_cancelled(self) -> None:
        self.backfill_status_label.setText("狀態：閒置")
        self.backfill_start_btn.setEnabled(True)
        self.backfill_cancel_btn.setEnabled(False)
        self.backfill_log.append("已取消。")

    def _on_backfill_cancel_clicked(self) -> None:
        if self._backfill_worker is not None and self._backfill_worker.isRunning():
            self._backfill_worker.requestInterruption()
            self.backfill_log.append("正在取消...")

    def _on_backfill_refresh_stock_info(self) -> None:
        if self._stock_info_worker is not None and self._stock_info_worker.isRunning():
            return
        self.backfill_refresh_stock_info_btn.setEnabled(False)
        self.backfill_refresh_stock_info_btn.setText("整理中...")
        self._stock_info_worker = StockInfoRefreshWorker()
        self._stock_info_worker.finished_ok.connect(self._on_backfill_stock_info_finished)
        self._stock_info_worker.failed.connect(self._on_backfill_stock_info_failed)
        self._stock_info_worker.start()

    def _on_backfill_stock_info_finished(self, count: int) -> None:
        self.backfill_refresh_stock_info_btn.setEnabled(True)
        self.backfill_refresh_stock_info_btn.setText("重新整理股票清單")
        QMessageBox.information(self, "完成", f"股票基本資料已更新：{count}筆")

    def _on_backfill_stock_info_failed(self, message: str) -> None:
        self.backfill_refresh_stock_info_btn.setEnabled(True)
        self.backfill_refresh_stock_info_btn.setText("重新整理股票清單")
        QMessageBox.warning(self, "失敗", f"股票基本資料更新失敗：{message}")

    def _on_watchlist_add_group(self) -> None:
        if self.portfolio_conn is None:
            return
        name, ok = QInputDialog.getText(self, "新增觀察清單群組", "群組名稱：")
        name = name.strip()
        if not ok or not name:
            return
        try:
            new_id = portfolio_storage.add_watchlist_group(self.portfolio_conn, name)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "新增群組失敗", f"群組名稱「{name}」已經存在。")
            return
        self._reload_watchlist_groups()
        idx = self.watchlist_group_combo.findData(new_id)
        if idx >= 0:
            self.watchlist_group_combo.setCurrentIndex(idx)

    def _on_watchlist_rename_group(self) -> None:
        if self.portfolio_conn is None:
            return
        group_id = self.watchlist_group_combo.currentData()
        if group_id is None:
            return
        current_name = self.watchlist_group_combo.currentText()
        name, ok = QInputDialog.getText(self, "重新命名群組", "群組名稱：", text=current_name)
        name = name.strip()
        if not ok or not name or name == current_name:
            return
        try:
            portfolio_storage.rename_watchlist_group(self.portfolio_conn, group_id, name)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "重新命名失敗", f"群組名稱「{name}」已經存在。")
            return
        self._reload_watchlist_groups()

    def _on_watchlist_delete_group(self) -> None:
        if self.portfolio_conn is None:
            return
        group_id = self.watchlist_group_combo.currentData()
        if group_id is None:
            return
        group_name = self.watchlist_group_combo.currentText()
        if QMessageBox.question(
            self, "刪除群組", f"確定要刪除群組「{group_name}」嗎？裡面的股票也會一併刪除。",
        ) != QMessageBox.StandardButton.Yes:
            return
        portfolio_storage.delete_watchlist_group(self.portfolio_conn, group_id)
        self._reload_watchlist_groups()
        self._refresh_watchlist_tab()

    def _on_watchlist_add_stock(self) -> None:
        if self.portfolio_conn is None:
            return
        group_id = self.watchlist_group_combo.currentData()
        if group_id is None:
            return
        dialog = _StockEditDialog(self.conn, "新增觀察股票", parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = dialog.values()
            portfolio_storage.add_watchlist_stock(
                self.portfolio_conn, group_id, values["stock_id"], values["cost_price"], values["shares"], values["note"],
            )
            self._refresh_watchlist_tab()

    def _on_watchlist_edit_selected(self) -> None:
        if self.portfolio_conn is None:
            return
        group_id = self.watchlist_group_combo.currentData()
        if group_id is None:
            return
        stock_ids = self._selected_portfolio_stock_ids(self.watchlist_table)
        if len(stock_ids) != 1:
            QMessageBox.information(self, "編輯觀察股票", "請先選取一筆要編輯的股票。")
            return
        stock_id = stock_ids[0]
        existing = portfolio_storage.get_watchlist_stock(self.portfolio_conn, group_id, stock_id) or {"stock_id": stock_id}
        dialog = _StockEditDialog(self.conn, "編輯觀察股票", initial=existing, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = dialog.values()
            portfolio_storage.update_watchlist_stock(
                self.portfolio_conn, group_id, stock_id, values["cost_price"], values["shares"], values["note"],
            )
            self._refresh_watchlist_tab()

    def _on_watchlist_delete_selected(self) -> None:
        if self.portfolio_conn is None:
            return
        group_id = self.watchlist_group_combo.currentData()
        if group_id is None:
            return
        stock_ids = self._selected_portfolio_stock_ids(self.watchlist_table)
        if not stock_ids:
            QMessageBox.information(self, "刪除觀察股票", "請先選取要刪除的股票。")
            return
        if QMessageBox.question(
            self, "刪除觀察股票", f"確定要刪除{len(stock_ids)}檔股票嗎？",
        ) != QMessageBox.StandardButton.Yes:
            return
        for stock_id in stock_ids:
            portfolio_storage.delete_watchlist_stock(self.portfolio_conn, group_id, stock_id)
        self._refresh_watchlist_tab()

    def _build_market_tab(self) -> None:
        """「大盤分析」分頁：跟個股分析共用同一套規則比對渲染邏輯(_build_analysis_sections_
        html())，只是分析對象固定是大盤(TAIEX_STOCK_ID)，不是使用者目前選取的個股。

        ⚠️ 2026-07-29修正：使用者反映不需要像個股分析那樣按鈕才展開——大盤只有一檔、
        資料量固定，不會像候選清單那樣「選了才知道要分析誰」，改成K線圖(含MACD/KD/SAR)
        跟規則比對清單都常駐顯示，不需要額外點擊；也移除了原本的「大盤分析」/「收合」
        按鈕，改在_refresh_market_tab()裡於視窗啟動、手動抓取/立即重新篩選完成時主動
        重新整理，確保顯示的一直是資料庫裡最新的大盤資料。

        這裡直接用QVBoxLayout(不是候選清單分頁那種QSplitter)包在QScrollArea裡——
        2026-07-29修正個股分析截斷bug時發現QSplitter不會把子元件sizeHint的變化轉發給
        外層QScrollArea，這裡沒有QSplitter，一般QVBoxLayout+QScrollArea(setWidgetResizable
        (True))組合本來就能正確隨內容量調整捲軸範圍，不需要_sync_central_height_to_
        content()那樣的手動同步workaround。

        2026-08-02改版：K線圖跟大盤分析拆成內層兩個tab(見self.market_inner_tabs)，跟
        個股資訊分頁的處理方式一致(見_build_stock_detail_tab())，版面不再同時擠著圖表
        跟一長串規則比對清單。
        """
        market_scroll = QScrollArea()
        market_scroll.setWidgetResizable(True)
        self.tabs.addTab(market_scroll, "大盤")

        market_content = QWidget()
        market_scroll.setWidget(market_content)
        market_layout = QVBoxLayout(market_content)

        # ⚠️ 2026-08-02修正：原本靠setMinimumHeight(820)「猜」一個貼近840px實際圖表
        # 高度的數字(build_candlestick_figure()固定show_macd=True/show_kd=True時算出
        # 的圖表高度是560+140*2=840px)，QWebEngineView的sizeHint()不會反映實際載入的
        # Plotly圖表高度，猜的數字容易跟實際內容有落差。改成在_refresh_market_tab()裡
        # 讀`fig.layout.height`後直接setFixedHeight()，不用猜。
        self.market_chart_view = QWebEngineView()
        # 跟self._chart_html_path(個股圖表用)分開的暫存檔案，避免兩個分頁互相覆寫對方的
        # 圖表內容(見__init__裡_chart_html_path的說明：QWebEngineView.setHtml()對內容
        # 大小有隱性限制，兩邊都是寫進暫存檔案再load()開啟)。
        self._market_chart_html_path = Path(tempfile.gettempdir()) / f"tw_stock_market_chart_{id(self)}.html"

        # ⚠️ 2026-07-29修正：使用者反映分析文字不要看起來像獨立的一個「框」——QTextEdit
        # 預設會畫外框(QFrame的StyledPanel樣式)，改成NoFrame讓它視覺上融入頁面，跟上面
        # 的K線圖直接銜接，不是分頁裡另外圈出來的一塊。高度計算(_set_autoheight_html()
        # 裡的setFixedHeight)維持不變——那是為了讓QTextBrowser本身能顯示完整內容不被
        # 截斷，不是「限縮」，是精準算出剛好的高度，跟這裡拿掉外框是两件事。
        # 2026-08-04改版：跟self.analysis_summary_view/tech_view/chip_view同一種
        # 「拆成技術面/籌碼面兩個可收合區塊」處理，見_build_stock_detail_tab()的說明。
        self.market_analysis_summary_view = self._build_analysis_text_view()
        self._market_analysis_tech_box = _CollapsibleBox("技術面")
        self.market_analysis_tech_view = self._build_analysis_text_view()
        self._market_analysis_tech_box.content_layout.addWidget(self.market_analysis_tech_view)
        self._market_analysis_chip_box = _CollapsibleBox("籌碼面")
        self.market_analysis_chip_view = self._build_analysis_text_view()
        self._market_analysis_chip_box.content_layout.addWidget(self.market_analysis_chip_view)

        # 「資料更新至」：使用者反映大盤分頁看不出目前顯示的股價資料多新——這個時間戳
        # 原本只有「選股」分頁的status_label有顯示(股價更新至/候選清單算至/下次更新
        # 時間)，但那個toolbar整組scope在_build_screener_tab()裡，只有切到「選股」
        # 分頁才看得到，大盤分頁完全沒有對應顯示。這裡不去動那個共用toolbar(牽涉範圍
        # 較大、風險較高)，改成大盤分頁自己顯示一個較簡單的版本，只顯示股價更新時間
        # (大盤分頁本來就沒有候選清單/下次排程時間這些概念)。2026-08-03改版：使用者
        # 指出應該放在右上角、跟「選股」分頁status_label同一個位置才一致——那邊是用
        # QHBoxLayout先addStretch()再放status_label，把它推到最右邊，這裡比照同一種
        # 排法，不是單獨佔一整行的靠左標籤。
        market_update_row = QHBoxLayout()
        market_update_row.addStretch()
        self.market_update_label = QLabel("")
        self.market_update_label.setStyleSheet("color: #666666;")
        market_update_row.addWidget(self.market_update_label)
        market_layout.addLayout(market_update_row)

        self.market_inner_tabs = _AutoHeightTabWidget()
        market_chart_tab = QWidget()
        market_chart_tab_layout = QVBoxLayout(market_chart_tab)
        market_chart_tab_layout.addWidget(self.market_chart_view)
        self.market_inner_tabs.addTab(market_chart_tab, "圖表")

        market_analysis_tab = QWidget()
        market_analysis_tab_layout = QVBoxLayout(market_analysis_tab)
        market_analysis_tab_layout.addWidget(self.market_analysis_summary_view)
        market_analysis_tab_layout.addWidget(self._market_analysis_tech_box)
        market_analysis_tab_layout.addWidget(self._market_analysis_chip_box)
        # 跟analysis_tab_layout同一個修正，見那裡的說明。
        market_analysis_tab_layout.addStretch()
        self.market_inner_tabs.addTab(market_analysis_tab, "大盤分析")
        self.market_analysis_summary_view.anchorClicked.connect(self._on_reference_link_clicked)
        self.market_analysis_summary_view.anchorClicked.connect(
            lambda url: self._on_analysis_jump_link_clicked(
                url, market_scroll, self._market_analysis_tech_box, self._market_analysis_chip_box,
            )
        )
        for view in (self.market_analysis_tech_view, self.market_analysis_chip_view):
            view.anchorClicked.connect(self._on_reference_link_clicked)
            view.anchorClicked.connect(lambda url: self._on_analysis_top_link_clicked(url, market_scroll))

        self.market_inner_tabs.currentChanged.connect(self._on_market_inner_tab_changed)
        market_layout.addWidget(self.market_inner_tabs)
        _FloatingTopButton(market_scroll)
        # ⚠️ 這裡不在建構時就呼叫_refresh_market_tab()：分頁2要等使用者實際切換過去才
        # 會有真正的layout(viewport寬度等於0或預設值)，document().size().height()算出來
        # 的高度會嚴重失真(實測只有個位數px)。改成在_on_tab_changed()裡、切到這個分頁時
        # 才整理，見__init__()裡tabs.currentChanged的連接。

    def _refresh_market_tab(self) -> None:
        """重新整理「大盤分析」分頁的K線圖(含MACD/KD/SAR)與規則比對清單，在切換到這個
        分頁、手動抓取今日資料/立即重新篩選完成時都要呼叫，確保顯示的是資料庫裡最新的
        大盤資料。
        """
        if self.conn is None:
            return
        self.market_update_label.setText(
            f"資料更新至：{self._format_update_timestamp(chart_data.get_latest_update_time(self.conn))}"
        )
        price_df = chart_data.load_price_history(self.conn, TAIEX_STOCK_ID)
        if not price_df.empty:
            holidays, _holidays_ok = chart_data.load_holidays_for_chart(price_df)
            fig = chart_data.build_candlestick_figure(
                price_df, holidays=holidays, ma_periods=FULL_PERIODS,
                show_macd=True, show_kd=True, show_sar=True,
            )
            # 直接讀build_candlestick_figure()算好的圖表高度，setFixedHeight()精準給
            # QWebEngineView剛好的空間，不用像之前那樣用setMinimumHeight()「猜」一個
            # 數字(見self.market_chart_view建構處的說明)。+20px緩衝對應瀏覽器預設
            # body margin，實際數字以截圖核對為準。
            self.market_chart_view.setFixedHeight(int(fig.layout.height) + 20)
            html_content = render_chart_html(fig, price_df, stock_label=TAIEX_DISPLAY_NAME)
            self._market_chart_html_path.write_text(html_content, encoding="utf-8")
            self.market_chart_view.load(QUrl.fromLocalFile(str(self._market_chart_html_path)))
        # 「大盤分析」內層tab還沒被切換過去顯示之前，QTextBrowser沒有正確的layout寬度，
        # document().size().height()算出來的高度會失真(見_on_market_inner_tab_changed()
        # 的說明)，只在使用者目前就停留在這個tab時才順便重算。
        if self.market_inner_tabs.currentIndex() == 1:
            self._refresh_market_analysis_sections()

    def _on_market_inner_tab_changed(self, index: int) -> None:
        """切到「大盤分析」tab(index==1)時才重新整理內容——沿用_on_tab_changed()/
        showEvent()已經驗證過的模式：tab還沒真正顯示、layout寬度還不正確前就呼叫
        document().size().height()，算出來的高度會嚴重失真(見_build_market_tab()的
        說明)，不能在建構或背景重新整理時就無條件計算。
        """
        if index == 1 and self.conn is not None:
            self._refresh_market_analysis_sections()

    def _refresh_market_analysis_sections(self) -> None:
        """重新整理「大盤分析」的總結/技術面/籌碼面三個QTextBrowser，兩處呼叫點
        (_refresh_market_tab()/_on_market_inner_tab_changed())共用，2026-08-04
        新增，避免兩處各自重複「呼叫_build_analysis_sections_html()+設定3個view」
        這段程式碼。"""
        sections = self._build_analysis_sections_html(TAIEX_STOCK_ID, f"大盤分析：{TAIEX_DISPLAY_NAME}")
        self._set_autoheight_html(self.market_analysis_summary_view, sections["summary"])
        self._set_autoheight_html(self.market_analysis_tech_view, sections["tech"])
        self._set_autoheight_html(self.market_analysis_chip_view, sections["chip"])

    def showEvent(self, event) -> None:
        """視窗第一次顯示時，補打一次目前分頁(預設是分頁0「大盤」)的_on_tab_changed()，
        見__init__()裡self._startup_tab_refreshed的說明——`tabs.currentChanged`訊號
        在建構階段(-1→ 0)不會觸發，「大盤」分頁不像「個股資訊」分頁那樣，一定會經過
        使用者手動切換一次才觸發刷新。用QTimer.singleShot(0, ...)延到這次show事件處理
        完之後才呼叫，確保跟手動切換分頁時一樣，viewport已經有真正的layout(不是0或
        預設值)，算出來的高度/圖表才會正確，不是這裡直接同步呼叫。
        """
        super().showEvent(event)
        if not self._startup_tab_refreshed:
            self._startup_tab_refreshed = True
            QTimer.singleShot(0, lambda: self._on_tab_changed(self.tabs.currentIndex()))

    def _on_tab_changed(self, index: int) -> None:
        if index == TAB_MARKET:
            self._refresh_market_tab()
        elif index == TAB_STOCK_DETAIL:
            # 切到「個股資訊」分頁時重新整理一次(不管是不是剛從候選清單點過來)，確保
            # 圖表/分析面板都是在分頁真正顯示、有正確layout之後才計算(見_build_stock_
            # detail_tab()的說明)；_rerender_chart()本身有「沒有選取任何股票」的
            # 空狀態防呆，不會因為使用者還沒選過股票就直接切過來而出錯。
            self._rerender_chart()
        elif index == TAB_INDUSTRY_ROTATION:
            self._refresh_industry_rotation_tab()
        elif index == TAB_INVENTORY:
            self._refresh_inventory_tab()
        elif index == TAB_WATCHLIST:
            self._refresh_watchlist_tab()

    # ------------------------------------------------------------------
    # 候選清單／圖表
    # ------------------------------------------------------------------

    def _refresh_date_list(self) -> None:
        """重新讀取daily_candidates裡目前有哪些日期，填入日期下拉選單。盡量保留使用者
        目前選取的日期(例如按了「手動抓取今日資料」後選單多了新的一天，但使用者原本在看
        某個歷史日期時不應該被強制跳回最新一天)，找不到才退回選最新一天(index 0，因為
        list_candidate_dates()本身就是新到舊排序)。用blockSignals避免repopulate過程
        觸發currentIndexChanged造成遞迴呼叫_reload_candidates()。
        """
        if self.conn is None:
            return
        current_selection = self.date_combo.currentText() or None
        dates = chart_data.list_candidate_dates(self.conn)
        self.date_combo.blockSignals(True)
        self.date_combo.clear()
        self.date_combo.addItems(dates)
        if current_selection and current_selection in dates:
            self.date_combo.setCurrentText(current_selection)
        self.date_combo.blockSignals(False)

    def _reload_candidates(self) -> None:
        if self.conn is None:
            return
        target_date = self.date_combo.currentText() or None
        # 「市場：全部/上市/上櫃」下拉對應load_stock_universe_for_date()的market參數
        # ("TWSE"/"TPEx"/None)；「全部」不在對照表裡，get()查不到就維持None(不限制)。
        market = _MARKET_FILTER_VALUES.get(self.market_filter_combo.currentText())
        df, latest_date, is_intraday = chart_data.load_stock_universe_for_date(
            self.conn, target_date=target_date, market=market,
        )
        # 「產業別」複選下拉：跟市場篩選一樣是「候選股票池的範圍」，在均線/SAR等篩選
        # 條件套用之前先縮小df，池子越小後面的篩選運算量越少。checked_items()回傳空
        # list代表勾選"全部"或什麼都沒勾，不套用這個篩選(見_CheckableComboBox說明)。
        selected_industries = self.industry_filter_combo.checked_items()
        if selected_industries:
            df = df[df["industry"].isin(selected_industries)].reset_index(drop=True)
        # 成交量門檻：跟市場/產業別一樣是「候選股票池範圍」，volume欄位原始單位是股，
        # 門檻(張)要乘以1000才能比較；0代表不限制(使用者可以把門檻調到0關閉這個篩選)，
        # 但0*1000=0，跟volume>=0邏輯上等同於不篩選，不需要另外特判。
        min_volume_lots = self.volume_filter_spin.value()
        if min_volume_lots > 0:
            df = df[df["volume"] >= min_volume_lots * 1000].reset_index(drop=True)
        active_filters = [label for label, cb in self.filter_checkboxes.items() if cb.isChecked()]
        sar_flip_option = None
        if self.sar_flip_checkbox.isChecked():
            sar_flip_option = {
                "direction": self.sar_flip_direction_combo.currentText(),
                "within_days": self.sar_flip_days_spin.value(),
            }
        df = chart_data.apply_candidate_filters(
            self.conn, df, active_filters, sar_flip_option=sar_flip_option,
            zhu_rule_only=self.zhu_rule_checkbox.isChecked(), as_of_date=latest_date,
        )
        self.candidates_table.setRowCount(0)
        # 重新整理表格後，勾選欄全部重置成未勾選，表頭的「全選」checkbox要跟著重置，
        # 不然重篩選後表頭還顯示上一批資料的勾選狀態。
        self._candidates_header.set_checked_silently(False)
        self.intraday_label.setVisible(is_intraday)
        if latest_date is None:
            self.setWindowTitle("台股每日選股（本機版）— 尚無候選清單")
            return
        self.setWindowTitle(f"台股每日選股（本機版）— {latest_date}，共{len(df)}檔")
        # 填資料期間先關掉排序：QTableWidget在setSortingEnabled(True)時，每次setItem()
        # 都會立刻重新排序一次，逐格填值的過程中列的位置會一直變動，導致同一列裡不同欄位
        # 的資料被錯配到不同列。填完畢後再打開，使用者才能點欄位標題排序。
        self.candidates_table.setSortingEnabled(False)
        self.candidates_table.setRowCount(len(df))
        # 收盤價/進場價/停損價/漲跌幅/成交量/SAR值/SAR距離%——欄位整體+1，因為第0欄是
        # 2026-08-04新增的勾選欄(見下面迴圈)。
        _NUMERIC_COLUMNS = {5, 6, 7, 8, 9, 10, 12}
        for row_idx, row in df.reset_index(drop=True).iterrows():
            pct_change = row["pct_change"]
            pct_text = f"{pct_change:+.2f}" if pd.notna(pct_change) else "-"
            volume = row["volume"]
            # 成交量改用「張」(1張=1000股)顯示，跟ref-project既有慣例一致(int除法無條件
            # 捨去)——DataFrame裡的volume欄位本身維持「股」不變，只在這裡顯示時轉換。
            volume_text = f"{int(volume) // 1000:,}" if pd.notna(volume) else "-"
            industry_text = row["industry"] if pd.notna(row["industry"]) else ""
            # entry_price/stop_loss：全市場掃描補進來、當天沒有觸發任何朱家泓規則的股票
            # (見chart_data.load_stock_universe_for_date())沒有對應的進場價/停損價可用，
            # 是None不是數字，格式化前要先判斷，否則.2f格式化None會直接crash。
            close_text = f"{row['close']:.2f}" if pd.notna(row["close"]) else "-"
            entry_price_text = f"{row['entry_price']:.2f}" if pd.notna(row["entry_price"]) else "-"
            stop_loss_text = f"{row['stop_loss']:.2f}" if pd.notna(row["stop_loss"]) else "-"
            # SAR三欄：還沒回補到daily_indicators的股票這幾個值是None，顯示"-"(見
            # chart_data.load_stock_universe_for_date()的說明)。
            sar_value_text = f"{row['sar_value']:.2f}" if pd.notna(row["sar_value"]) else "-"
            sar_status_text = row["sar_status"] if pd.notna(row["sar_status"]) else "-"
            sar_distance_text = f"{row['sar_distance_pct']:+.2f}" if pd.notna(row["sar_distance_pct"]) else "-"
            signal_full = row["signal_name"] if pd.notna(row["signal_name"]) else None
            signal_display = _truncate_signal_lines(signal_full)
            values = [
                row["stock_id"], row["name"], industry_text, signal_display, close_text,
                entry_price_text, stop_loss_text, pct_text, volume_text,
                sar_value_text, sar_status_text, sar_distance_text,
            ]
            # 訊號欄位(index 3)的tooltip用完整規則清單，不是被截斷成CANDIDATE_SIGNAL_MAX_LINES
            # 行的顯示文字——排序依據(信心分數加總)吃的也是完整清單，滑鼠移過去應該看得到
            # 被省略掉的規則實際是哪幾條，不是只看到跟儲存格一樣的截斷內容。
            tooltips = list(values)
            tooltips[3] = signal_full or "-"
            # 第0欄：勾選欄，2026-08-04新增，供「加入庫存」/「加入觀察清單」批次動作用
            # (見_checked_candidate_stock_ids())，跟其餘欄位分開設定(不走下面共用的
            # item_cls/tooltip迴圈，checkbox不需要文字內容/tooltip)。
            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            checkbox_item.setCheckState(Qt.CheckState.Unchecked)
            self.candidates_table.setItem(row_idx, 0, checkbox_item)
            for col_offset, (value, tooltip_value) in enumerate(zip(values, tooltips)):
                col_idx = col_offset + 1  # +1避開第0欄的勾選欄
                item_cls = _NumericTableWidgetItem if col_idx in _NUMERIC_COLUMNS else QTableWidgetItem
                item = item_cls(str(value))
                if col_idx in _NUMERIC_COLUMNS:
                    # 數值欄位靠右對齊，跟表頭文字/其他文字欄位(靠左)的閱讀習慣區分開來——
                    # 跟右邊格線的間距由candidates_table的stylesheet(padding-right)處理，
                    # 不是緊貼著格線。
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if col_offset == 1:
                    # 名稱欄依市場別上色(上市藍/上櫃黑/興櫃灰)，2026-08-04新增，跟觀察
                    # 清單/庫存清單(_populate_portfolio_table())、Google Sheet匯出
                    # (watchlist_export.py)共用同一份src.presentation.portfolio_data.
                    # listing_type_color()對照表。
                    item.setForeground(QColor(portfolio_data.listing_type_color(row.get("listing_type"))))
                # 部分欄位內容常常比欄寬長、會被截斷看不到完整內容(尤其訊號欄位同時符合多條
                # 規則時)；設定tooltip讓滑鼠移過去任一儲存格都能懸浮顯示完整文字，不用特別
                # 放寬欄寬。
                item.setToolTip(str(tooltip_value))
                self.candidates_table.setItem(row_idx, col_idx, item)
        self.candidates_table.setSortingEnabled(True)
        self.candidates_table.resizeRowsToContents()  # 讓多行的訊號欄位撐開列高，完整顯示

    def _on_candidate_selected(self) -> None:
        rows = self.candidates_table.selectionModel().selectedRows()
        if not rows:
            return
        stock_id = self.candidates_table.item(rows[0].row(), 1).text()  # 欄位1：股票代號(欄位0是勾選欄)
        self._current_stock_id = stock_id
        # 記錄來源候選清單日期(目前分頁選取的日期)，供「個股資訊」分頁右上角顯示
        # 「來源：X月X日的選股策略」。自動切到該分頁——切換會觸發_on_tab_changed()
        # 呼叫_rerender_chart()，這裡不用另外呼叫(也不應該在切換前就呼叫：分頁還沒
        # 顯示的話，圖表/分析面板還沒有正確的layout，見_build_stock_detail_tab()的
        # 說明)。
        self._current_stock_source = self.date_combo.currentText() or None
        self.tabs.setCurrentIndex(TAB_STOCK_DETAIL)

    def _on_candidate_search(self) -> None:
        """在目前候選清單的列裡搜尋代號或名稱是否存在，找到就選取該列並捲動過去——
        `selectRow()`會觸發`itemSelectionChanged`訊號，`_on_candidate_selected()`
        因此會自動連帶更新下方的個股圖表/分析面板，這裡不用另外呼叫。找不到時明確
        告知使用者，不要讓輸入框看起來像沒反應。"""
        query = self.candidate_search_input.text().strip()
        if not query:
            return
        query_lower = query.lower()
        for row in range(self.candidates_table.rowCount()):
            stock_id_item = self.candidates_table.item(row, 1)  # 欄位1：股票代號(欄位0是勾選欄)
            name_item = self.candidates_table.item(row, 2)
            stock_id = stock_id_item.text() if stock_id_item else ""
            name = name_item.text() if name_item else ""
            if query_lower == stock_id.lower() or query in name:
                self.candidates_table.selectRow(row)
                self.candidates_table.scrollToItem(stock_id_item)
                return
        QMessageBox.information(self, "候選清單搜尋", f"目前候選清單中找不到「{query}」。")

    def _checked_candidate_stock_ids(self) -> list[str]:
        """回傳候選清單裡目前勾選(第0欄checkbox)的股票代號清單，供「加入庫存」/
        「加入觀察清單」批次動作用。"""
        stock_ids = []
        for row in range(self.candidates_table.rowCount()):
            checkbox_item = self.candidates_table.item(row, 0)
            if checkbox_item is not None and checkbox_item.checkState() == Qt.CheckState.Checked:
                stock_id_item = self.candidates_table.item(row, 1)
                if stock_id_item is not None:
                    stock_ids.append(stock_id_item.text())
        return stock_ids

    def _on_candidates_select_all_toggled(self, checked: bool) -> None:
        """候選清單表頭的勾選欄checkbox(見_CheckableHeaderView)：切換目前顯示的
        所有列全選/取消全選。"""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.candidates_table.rowCount()):
            item = self.candidates_table.item(row, 0)
            if item is not None:
                item.setCheckState(state)

    def _on_candidates_add_to_inventory(self) -> None:
        """選股清單「加入庫存」：把勾選的股票各自新增一筆空白批次(成本價/股數留空，
        之後自行到「庫存清單」分頁編輯)——已經有既有批次的股票不重複新增(見
        src/data/portfolio_storage.py的add_stocks_to_inventory()說明)。"""
        if self.portfolio_conn is None:
            return
        stock_ids = self._checked_candidate_stock_ids()
        if not stock_ids:
            QMessageBox.information(self, "加入庫存", "請先勾選要加入的股票。")
            return
        added = portfolio_storage.add_stocks_to_inventory(self.portfolio_conn, stock_ids)
        skipped = len(stock_ids) - added
        message = f"已加入{added}檔股票。"
        if skipped:
            message += f"（{skipped}檔已經有庫存紀錄，略過）"
        QMessageBox.information(self, "加入庫存", message)

    def _on_candidates_add_to_watchlist(self) -> None:
        """選股清單「加入觀察清單」：把勾選的股票加入使用者選擇的一個或多個觀察
        清單群組，跟「庫存清單」分頁的「加入觀察清單」共用同一個群組勾選對話框
        (見_add_stocks_to_watchlist_via_dialog())。"""
        self._add_stocks_to_watchlist_via_dialog(self._checked_candidate_stock_ids())

    def _on_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            return
        resolved = chart_data.resolve_stock_id(self.conn, query) if self.conn is not None else None
        self._current_stock_id = resolved or query
        self._current_stock_source = None  # 手動查詢，右上角不顯示來源
        self._rerender_chart()

    def _rerender_chart(self) -> None:
        if self.conn is None or not self._current_stock_id:
            return
        # 右上角來源標籤：從候選清單點過來的才顯示「來源：X月X日的選股策略」，讓使用者
        # 知道現在看的是「當時」選股策略認為符合規則的股票；手動查詢(self.search_input)
        # 時_current_stock_source是None，標籤保持空白不顯示(見_on_search())。
        if self._current_stock_source:
            self.stock_source_label.setText(f"來源：{_format_month_day(self._current_stock_source)}的選股策略")
        else:
            self.stock_source_label.setText("")
        self.stock_detail_update_label.setText(
            f"資料更新至：{self._format_update_timestamp(chart_data.get_stock_update_time(self.conn, self._current_stock_id))}"
        )
        price_df = chart_data.load_price_history(self.conn, self._current_stock_id)
        if price_df.empty:
            self.chart_view.setFixedHeight(80)
            self.chart_view.setHtml(f"<p>查無股票代號 {self._current_stock_id} 的價格資料。</p>")
            self.summary_view.setPlainText("")
            self.summary_view.setFixedHeight(30)
            if self.detail_inner_tabs.currentIndex() == 1:
                error_html = f"<p>查無股票代號 {self._current_stock_id} 的價格資料。</p>"
                self._set_autoheight_html(self.analysis_summary_view, error_html)
                self._set_autoheight_html(self.analysis_tech_view, "")
                self._set_autoheight_html(self.analysis_chip_view, "")
            elif self.detail_inner_tabs.currentIndex() == 2:
                self._refresh_stock_overview_view()
            return

        holidays, holidays_ok = chart_data.load_holidays_for_chart(price_df)

        selected_periods = tuple(n for n, cb in self.ma_checkboxes.items() if cb.isChecked())

        trendlines = chart_overlays.compute_trendlines(price_df)
        selected_trendline_keys = tuple(
            key for key, cb in self.trendline_checkboxes.items() if cb.isChecked() and key in trendlines
        )

        sr_levels: list[dict] = []
        show_sr = self.sr_checkbox.isChecked()
        if show_sr:
            all_levels = chart_overlays.compute_support_resistance_levels(price_df)
            sr_levels = chart_overlays.nearest_support_resistance(all_levels, float(price_df["close"].iloc[-1]))

        fig = chart_data.build_candlestick_figure(
            price_df, holidays=holidays, ma_periods=selected_periods,
            trendlines=trendlines, show_trendline_keys=selected_trendline_keys,
            sr_levels=sr_levels, show_support_resistance=show_sr,
            show_macd=self.macd_checkbox.isChecked(), show_kd=self.kd_checkbox.isChecked(),
            show_sar=self.sar_checkbox.isChecked(),
        )
        # render_chart_html()疊加滑鼠十字線(貫穿價格/成交量/MACD/KD子圖)+左上角動態資訊框，
        # 取代Plotly預設會跟著滑鼠跑的浮動tooltip，仿TradingView的畫法(src/presentation/
        # chart_render.py有完整說明；2026-08-04起web版也透過st.components.v1.html()共用
        # 同一個函式，不再是桌面版專屬效果)。include_plotlyjs=True
        # 把plotly.js整包內嵌，桌面版離線也能看圖。寫進暫存檔案再用load()開啟，理由見__init__裡
        # _chart_html_path的註解(setHtml對大內容會靜默失敗)。不傳title給build_candlestick_
        # figure(桌面版改用render_chart_html的stock_label固定列顯示代號+名稱，見那裡的說明)。
        stock_name = chart_data.get_stock_name(self.conn, self._current_stock_id)
        stock_label = f"{self._current_stock_id} {stock_name}" if stock_name else self._current_stock_id
        # 直接讀build_candlestick_figure()算好的圖表高度，setFixedHeight()精準給
        # QWebEngineView剛好的空間，不用像之前那樣用setMinimumHeight()「猜」一個數字
        # (見self.chart_view建構處的說明)。+20px緩衝對應瀏覽器預設body margin，實際
        # 數字以截圖核對為準。
        self.chart_view.setFixedHeight(int(fig.layout.height) + 20)
        html = render_chart_html(fig, price_df, stock_label=stock_label)
        self._chart_html_path.write_text(html, encoding="utf-8")
        self.chart_view.load(QUrl.fromLocalFile(str(self._chart_html_path)))

        # trend_df：短/中/長(日/週/月)趨勢分類器要重新取樣出週線/月線，需要比price_df
        # (預設120天顯示窗口)更長的歷史，見chart_data.TREND_LOOKBACK_DAYS的說明。
        trend_df = chart_data.load_price_history(self.conn, self._current_stock_id, days=chart_data.TREND_LOOKBACK_DAYS)
        summary = latest_day_summary.summarize_latest_day(price_df, trend_df=trend_df)
        latest_date_label = price_df.index[-1].strftime("%Y-%m-%d")
        # 短/中/長三種天期分開顯示、各自標示判斷依據的K棒週期(見R-INDICATOR-10：做短線看
        # 日線、中期看週線、長期看月線)，不合併成單一「目前趨勢」——三者可能不一致(例如
        # 日線走空、週線仍是多頭)，只看一種天期容易誤判。每個天期都附上「依據」(最近兩個
        # 頭部/底部的實際價格、日期、頭頭高低/底底高低的判讀)，讓使用者能自己核對演算法的
        # 判斷——改成每行一種天期，跟Streamlit版對齊，附上依據後單行會過長不好讀。
        #
        # ⚠️ TrendHorizonResult 2026-07-26新增了第4個欄位freshness(見trend_state.py)，
        # 這裡原本用固定3個一組的tuple unpacking(`(timeframe, trend, reason)`)寫死解構，
        # 沒有跟著更新，多了freshness欄位後每次呼叫都會丟ValueError("too many values to
        # unpack")、讓_rerender_chart()整個中斷在這裡——後面的summary_view.setPlainText()
        # 跟_refresh_analysis_view()都執行不到，這就是「圖表下方說明沒顯示」「個股分析
        # 沒有自動更新」的根本原因(dashboard/app.py跟rule_scan.py當時都已經一併修正過，
        # 唯獨這個桌面版檔案漏掉)。改成`*_freshness`吸收多出來的欄位，並在需要提醒使用者
        # 「轉折點可能已經跟不上盤面」時另外附上一行，不是直接丟掉不用。
        trend_lines = []
        for label, (timeframe, trend, reason, *freshness_rest) in summary["trend"].items():
            trend_lines.append(f"　- {label}（{timeframe}）：{trend}（依據：{reason}）")
            if freshness_rest and "⚠️" in freshness_rest[0]:
                trend_lines.append(f"　　{freshness_rest[0]}")
        # 使用者反映「不知道現在顯示的是誰的分析」——第一行固定加註股票代碼+名稱，跟圖表
        # 標題(render_chart_html的stock_label)、視窗標題一致，三處都看得到同一個代碼名稱
        # 才不會誤把上一檔股票的資料當成目前這檔。
        stock_name = chart_data.get_stock_name(self.conn, self._current_stock_id)
        stock_label = f"{self._current_stock_id} {stock_name}" if stock_name else self._current_stock_id
        lines = [
            f"{stock_label}｜最新交易日分析（{latest_date_label}）",
            "目前趨勢：",
            *trend_lines,
            f"K棒名稱：{summary['candle_name']}",
            "型態訊號：" + ("、".join(summary["patterns"]) if summary["patterns"] else "無明顯型態"),
            "量價訊號：" + ("、".join(summary["volume_signals"]) if summary["volume_signals"] else "無明顯訊號"),
            "⚠️ 型態訊號的高低檔判斷已接上趨勢位置模組，但還沒有初升/主升/末升等更細的子階段分類。",
        ]
        if not holidays_ok:
            lines.append("⚠️ 假日清單暫時無法取得，圖表可能仍有國定假日空白。")
        self.summary_view.setPlainText("\n".join(lines))
        # 依內容動態算高度，取代原本setMaximumHeight(220)的固定上限(見self.summary_view
        # 建構處的說明)，讓內容多的時候完整展開，不用在小框裡另外捲動一次。
        summary_doc_height = self.summary_view.document().size().height()
        summary_frame_width = self.summary_view.frameWidth() * 2
        self.summary_view.setFixedHeight(int(summary_doc_height) + summary_frame_width + 8)

        if self.detail_inner_tabs.currentIndex() == 1:
            self._refresh_analysis_view()
        elif self.detail_inner_tabs.currentIndex() == 2:
            self._refresh_stock_overview_view()

    def _on_detail_inner_tab_changed(self, index: int) -> None:
        """切到「個股分析」(index==1)、「個股明細」(index==2)、「產出報表」(index==3)
        tab時才重新整理內容——沿用_on_tab_changed()/showEvent()已經驗證過的模式：
        tab還沒真正顯示、layout寬度還不正確前就呼叫document().size().height()，
        算出來的高度會嚴重失真，不能在建構或背景重新整理時就無條件計算(見
        _build_stock_detail_tab()/_AutoHeightTabWidget的說明)——「產出報表」用
        QWebEngineView預覽，沒有這個高度計算問題，但仍然沿用「切到這個tab才重新整理」
        的原則，避免每次查詢股票都無條件重算一次組合HTML(組合了5個明細區塊+規則
        比對清單，運算量不小)。
        """
        if index == 1:
            self._refresh_analysis_view()
        elif index == 2:
            self._refresh_stock_overview_view()
        elif index == 3:
            self._refresh_report_view()

    @staticmethod
    def _build_analysis_text_view() -> QTextBrowser:
        """建構「個股分析」/「大盤分析」用的QTextBrowser，統一設定(唯讀/無外框/關閉
        內部捲軸/不自動開連結)，供summary/tech/chip六個view共用，避免2026-08-04
        拆成三個view後重複6遍幾乎一樣的設定程式碼。連結點擊處理(anchorClicked)由
        呼叫端自行connect——summary跟tech/chip需要接不同的jump連結handler(見
        _build_stock_detail_tab()/_build_market_tab())，這裡不預先幫忙連好。
        """
        view = QTextBrowser()
        view.setReadOnly(True)
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setOpenLinks(False)
        return view

    @staticmethod
    def _set_autoheight_html(view: QTextEdit, html_content: str) -> None:
        """設定view的內容，並依實際內容量重新算出剛好的高度(setFixedHeight)，取代
        寫死的高度上限——訊號一多就會超過寫死的高度，QTextBrowser自己的垂直捲軸
        (已在建構時關閉)原本就會被塞爆，變成使用者要在這個小框框裡另外捲動一次；
        改成跟內容一樣高，多出來的部分交給最外層的QScrollArea(整個分頁)捲動，只有
        一層捲軸，不是兩層。「個股資訊」/「大盤」分頁都是plain QVBoxLayout+
        QScrollArea(setWidgetResizable(True))，本來就能正確隨內容量調整捲軸範圍，
        不需要額外的高度同步workaround(見2026-07-29大盤分析截斷bug的除錯記錄)。

        2026-08-04改版：原本是`_set_analysis_html()`/`_set_market_analysis_html()`
        兩個近乎重複的方法，各自只服務`self.analysis_view`/`self.market_analysis_
        view`一個QTextBrowser；拆成技術面/籌碼面兩個獨立可收合區塊後，個股分析＋
        大盤分析各自變成3個QTextBrowser(總結/技術面/籌碼面)，改成這一個共用的
        static method，呼叫端自己傳入要設定的view，不用維護6份幾乎一樣的程式碼。
        """
        view.setHtml(html_content)
        doc_height = view.document().size().height()
        frame_width = view.frameWidth() * 2
        view.setFixedHeight(int(doc_height) + frame_width + 8)

    @staticmethod
    def _render_rule_match_blocks(matches: list[dict], note_anchor_map: dict[str, str] | None = None) -> str:
        """把analyze_stock_signals()/stock_detail_data.analyze_chip_signals()回傳
        的規則清單逐條組成HTML——技術面／籌碼面共用同一套渲染邏輯，2026-08-04從
        原本的_build_analysis_html()抽出來，避免兩邊各維護一份幾乎一樣的程式碼。

        note_anchor_map：{筆記檔名: 附錄裡的HTML錨點id}，只有「產出報表」呼叫時
        才會傳入(見_build_report_html())——「個股分析」即時畫面裡的「原文與頁碼」
        是`ruledoc:///`連結，點擊後開新視窗讀筆記(見_on_reference_link_clicked())，
        但報表匯出成PDF後沒有「新視窗」可開，改成跳到報表自己附錄章節的PDF內部
        錨點連結(`_format_reference_html_as_anchors()`)。每個規則block本身也加上
        `id="cite-{rule_id}"`，供附錄裡「回引用處」連結回跳，不管是哪種模式都加，
        沒有note_anchor_map時這個id單純不會被用到，不影響原有行為。
        """
        blocks = []
        for m in matches:
            block = f"<p id=\"cite-{html.escape(m['rule_id'])}\"><b>{html.escape(m['rule_id'])}　{html.escape(m['title'])}（信心{m['confidence']}%）</b><br>"
            # 「目前狀態」(這條規則今天為什麼觸發)排在規則名稱後第一個位置，跟dashboard/
            # app.py對齊——使用者最想先看到的是「現在是什麼情況」，解讀/原文頁碼是補充
            # 說明。同一個rule_id若對應多筆觸發，note會是用換行接起來的多行文字，這裡
            # 逐行各自加註「目前狀態：」/縮排顯示，不能假設note永遠是單行字串。
            if m.get("note"):
                note_lines = m["note"].split("\n")
                block += f"目前狀態：{html.escape(note_lines[0])}<br>"
                for extra_line in note_lines[1:]:
                    block += f"　　{html.escape(extra_line)}<br>"
            if m.get("description"):
                # 「分析：」明確標示這段是「為什麼」的解說(見dashboard/app.py同一處的說明)，
                # 跟上面的「目前狀態：」分開標籤，不是延續文字。
                block += f"分析：{html.escape(m['description'])}<br>"
            if m.get("reference"):
                if note_anchor_map is not None:
                    reference_html = MainWindow._format_reference_html_as_anchors(m["reference"], note_anchor_map)
                else:
                    reference_html = MainWindow._format_reference_html(m["reference"])
                block += f"<i>原文與頁碼：{reference_html}</i>"
            block += "</p><hr>"
            blocks.append(block)
        return "".join(blocks)

    @staticmethod
    def _format_reference_html_as_anchors(reference: str, note_anchor_map: dict[str, str]) -> str:
        """跟_format_reference_html()同樣的「文字裡找出.md檔名、包成連結」邏輯，差別
        是連結目標改成報表內部錨點(`#note-N`，跳到_build_report_reference_appendix()
        產生的附錄章節)，不是開新視窗的`ruledoc:///`——2026-08-04新增，供「產出報表」
        的PDF使用：PDF匯出後沒有「開新視窗」這回事，筆記全文要嵌在同一份文件裡，靠
        PDF內部連結跳轉(QWebEnginePage.printToPdf()會把HTML錨點連結保留成PDF內部
        可點擊的跳轉連結)。找不到對照(理論上不會發生，note_anchor_map是從同一批
        matches算出來的)的檔名維持原樣文字，不做成連結。
        """
        parts: list[str] = []
        last_end = 0
        for match in rule_docs.MD_FILENAME_PATTERN.finditer(reference):
            filename = match.group(0)
            parts.append(html.escape(reference[last_end:match.start()]))
            anchor_id = note_anchor_map.get(filename)
            if anchor_id:
                parts.append(f'<a href="#{anchor_id}">{html.escape(filename)}</a>')
            else:
                parts.append(html.escape(filename))
            last_end = match.end()
        parts.append(html.escape(reference[last_end:]))
        return "".join(parts)

    def _build_analysis_sections_html(self, stock_id: str, header_label: str) -> dict[str, str]:
        """組出「個股分析」/「大盤分析」面板的三段HTML內容：
        {"summary": 📌總結分析(含跳轉連結), "tech": 技術面規則清單, "chip": 籌碼面
        規則清單}，供個股分析(_refresh_analysis_view())與大盤分析
        (_refresh_market_tab())共用同一套渲染邏輯——差別只在於分析對象是哪一個
        stock_id、標題文字，判斷/顯示格式完全一致(大盤本身就是`stock_prices`表裡的
        一筆特殊資料，`load_price_history()`/`analyze_stock_signals()`不需要知道
        它是大盤還是個股，見src/data/yfinance_client.py的fetch_taiex_prices())。

        2026-08-04改版：原本是單一`_build_analysis_html()`回傳一整段HTML(技術面
        規則清單+總結)，使用者要求拆成「技術面」／「籌碼面」兩個可各自收合的區塊，
        上方先有「📌 總結分析」列出兩個區塊各自的連結(點擊直接跳到下方對應區塊，見
        _on_analysis_jump_link_clicked())。籌碼面內容來自stock_detail_data.
        analyze_chip_signals()(R-SCREEN-06三大法人連續賣超/R-CHIP-01投信連續買超/
        R-CHIP-02融資維持率規則，見ai/chen-rules/籌碼面/)——大盤(^TWII)沒有法人
        籌碼資料，這裡不特別分支處理，讓它自然回傳空list、顯示「目前沒有符合任何
        已接上的籌碼規則」，跟一般沒有觸發任何規則的個股一樣的文字，不是bug。
        """
        price_df = chart_data.load_price_history(self.conn, stock_id)
        if price_df.empty:
            error_html = f"<p>查無股票代號 {html.escape(stock_id)} 的價格資料。</p>"
            return {"summary": error_html, "tech": "", "chip": ""}
        # trend_df：短/中/長(日/週/月)趨勢分類器要重新取樣出週線/月線，需要比price_df
        # (預設120天顯示窗口)更長的歷史，見chart_data.TREND_LOOKBACK_DAYS的說明。
        trend_df = chart_data.load_price_history(self.conn, stock_id, days=chart_data.TREND_LOOKBACK_DAYS)
        tech_matches = analyze_stock_signals(price_df, trend_df=trend_df)
        chip_matches = stock_detail_data.analyze_chip_signals(self.conn, stock_id)

        # ⚠️ QTextBrowser.setHtml()一定會把內容當HTML剖析，rule_scan.py的note文字裡常有
        # "MA5<MA10<MA20"這種原始"<"/">"符號，不escape的話會被誤判成HTML標籤、內容被
        # 吃掉一截(實測"目前狀態：MA5<MA10<MA20..."只會顯示到"MA5"就斷掉)。Streamlit版
        # 沒有這個問題是因為st.write/st.caption預設unsafe_allow_html=False，這裡是
        # QTextBrowser本身的行為，只有桌面版需要escape。
        _EMPTY_TEASER = {"tech": "目前沒有符合任何已接上規則庫的訊號。", "chip": "目前沒有符合任何已接上的籌碼規則。"}

        def section_teaser(matches: list[dict], anchor: str) -> str:
            """2026-08-04第二次改版：使用者反映拆成技術面/籌碼面後，總結分析退化成
            「共N條規則，信心最高：...」這種資訊量太少的版本，要求照拆分前的舊格式
            (多頭/空頭/其他傾向統計＋信心最高訊號的目前狀態)——這裡沿用拆分前
            _build_analysis_html()的summary_block寫法，籌碼面套用同一套格式，不是
            只給tech用。"""
            link = f'<a href="jumpto:///{anchor}">查看{"技術面" if anchor == "tech" else "籌碼面"}↓</a>'
            if not matches:
                return f"{_EMPTY_TEASER[anchor]}{link}"
            summary = summarize_signal_matches(matches)
            top = summary["top_match"]
            top_note = (top.get("note") or "").split("\n")[0] if top else ""
            return (
                f"本次共觸發 {summary['total']} 條規則"
                f"（多頭傾向{summary['bullish']}條、空頭傾向{summary['bearish']}條、"
                f"其他{summary['other']}條 — 依規則標題文字粗略分類，僅供參考）。<br>"
                f"信心最高的訊號：{html.escape(top['rule_id'])}　{html.escape(top['title'])}"
                f"（{top['confidence']}%）"
                + (f"<br>目前狀態：{html.escape(top_note)}" if top_note else "")
                + f"<br>{link}"
            )

        summary_html = (
            f"<p><b>{html.escape(header_label)}</b></p>"
            '<p><b>📌 總結分析</b></p>'
            f"<p><b>1. 技術面</b><br>{section_teaser(tech_matches, 'tech')}</p>"
            f"<p><b>2. 籌碼面</b><br>{section_teaser(chip_matches, 'chip')}</p>"
        )

        def section_html(title: str, matches: list[dict], anchor: str) -> str:
            body = self._render_rule_match_blocks(matches) if matches else f"<p>{_EMPTY_TEASER[anchor]}</p>"
            return f"<p><b>{title}</b></p>" + body + '<p><a href="jumpto:///top">🔼 回頂部</a></p>'

        return {
            "summary": summary_html,
            "tech": section_html("技術面", tech_matches, "tech"),
            "chip": section_html("籌碼面", chip_matches, "chip"),
        }

    @staticmethod
    def _format_reference_html(reference: str) -> str:
        """把「原文與頁碼」文字裡引用的.md檔名轉成可點擊連結，點擊後開新視窗直接
        閱讀該份筆記(見_on_reference_link_clicked())，不用手動去ai/ebook-summary/
        資料夾找檔案。2026-08-04新增。

        用`ruledoc:///`這個非標準scheme當連結(不是真的網址)，配合建構時對
        analysis_view/market_analysis_view呼叫的`setOpenLinks(False)`，QTextEdit
        不會自己嘗試把它當成外部連結開啟，而是觸發`anchorClicked`訊號交給
        `_on_reference_link_clicked()`處理。找不到對應實體檔案的檔名(理論上不該
        發生，通常是規則庫文字本身筆誤)維持原樣文字，不會被包成連結——連到不存在
        的檔案比留著純文字更容易誤導使用者。
        """
        resolved_names = {name for name, _ in rule_docs.resolve_reference_files(reference)}
        if not resolved_names:
            return html.escape(reference)
        parts: list[str] = []
        last_end = 0
        for match in rule_docs.MD_FILENAME_PATTERN.finditer(reference):
            filename = match.group(0)
            parts.append(html.escape(reference[last_end:match.start()]))
            if filename in resolved_names:
                href = f"ruledoc:///{urllib.parse.quote(filename)}"
                parts.append(f'<a href="{href}">{html.escape(filename)}</a>')
            else:
                parts.append(html.escape(filename))
            last_end = match.end()
        parts.append(html.escape(reference[last_end:]))
        return "".join(parts)

    def _on_reference_link_clicked(self, url: QUrl) -> None:
        """「原文與頁碼」連結的點擊處理：只接受_format_reference_html()產生的
        `ruledoc:///`連結，解析出檔名後開新視窗顯示筆記內容(見
        _open_rule_reference_window())。"""
        if url.scheme() != "ruledoc":
            return
        filename = urllib.parse.unquote(url.path().lstrip("/"))
        path = rule_docs.find_ebook_summary_file(filename)
        if path is None:
            QMessageBox.warning(self, "找不到筆記檔案", f"找不到「{filename}」，可能是規則庫連結有誤。")
            return
        self._open_rule_reference_window(path)

    def _on_analysis_jump_link_clicked(
        self, url: QUrl, scroll_area: QScrollArea, tech_box: "_CollapsibleBox", chip_box: "_CollapsibleBox",
    ) -> None:
        """「📌 總結分析」裡「1. 技術面」/「2. 籌碼面」連結的點擊處理：只接受
        `_build_analysis_sections_html()`產生的`jumpto:///tech`／`jumpto:///chip`
        連結，展開對應的可收合區塊並捲動scroll_area讓它進入可視範圍。2026-08-04
        新增。個股分析/大盤分析各自呼叫時綁定各自的scroll_area跟box(見
        _build_stock_detail_tab()/_build_market_tab()裡的連接程式碼)，這裡不用
        知道目前是哪一個面板。
        """
        if url.scheme() != "jumpto":
            return
        target = {"tech": tech_box, "chip": chip_box}.get(url.path().lstrip("/"))
        if target is None:
            return
        target.expand()
        # expand()剛觸發的layout變化要等當前事件循環跑完才會反映正確幾何位置，
        # 立即呼叫ensureWidgetVisible()算出來的位置可能還是收合前的舊高度——跟
        # _build_market_tab()docstring裡「分頁要等使用者實際切換過去才會有真正的
        # layout」是同一類「剛顯示的內容量高度算不準」情境，延後一拍處理。
        QTimer.singleShot(0, lambda: scroll_area.ensureWidgetVisible(target, 0, 10))

    def _on_analysis_top_link_clicked(self, url: QUrl, scroll_area: QScrollArea) -> None:
        """技術面/籌碼面區塊結尾「🔼 回頂部」連結的點擊處理：只接受
        `jumpto:///top`連結，捲動scroll_area回到最上方。2026-08-04新增。"""
        if url.scheme() != "jumpto" or url.path().lstrip("/") != "top":
            return
        scroll_area.verticalScrollBar().setValue(0)

    def _open_rule_reference_window(self, path: Path) -> None:
        """開一個新的非模態視窗，把`path`這份ai/ebook-summary/筆記檔案轉成HTML顯示。
        2026-08-04新增，使用者要求「原文與頁碼」的引用真的能點開來讀，不用自己去
        資料夾找檔案。

        每次點擊都開一個新視窗(不是重用同一個)，方便使用者同時對照好幾份筆記；
        用self._reference_windows留著參照，避免PySide6沒有其他地方持有參照時視窗
        被提前GC回收(症狀是視窗一開就馬上自動關閉)。視窗關閉時从list移除，不會
        無限累積記憶體。
        """
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "讀取失敗", f"無法讀取「{path.name}」：{exc}")
            return
        body_html = markdown.markdown(content, extensions=["tables", "fenced_code"])

        window = QDialog(self)
        window.setWindowTitle(path.stem)
        window.resize(900, 800)
        window.setModal(False)
        layout = QVBoxLayout(window)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setHtml(body_html)
        layout.addWidget(view)

        self._reference_windows.append(window)
        window.finished.connect(lambda _: self._reference_windows.remove(window))
        window.show()

    def _refresh_analysis_view(self) -> None:
        """填入「個股分析」面板內容：目前這檔股票符合規則庫中哪些訊號(依信心分數高到低)，
        每條附上從ai/zhu-rules/查出的規則說明。跟_rerender_chart各自重新查一次價格資料，
        不共用同一份df——避免兩邊狀態耦合(例如面板開著時切換股票，忘記同步更新)，運算成本
        很低(SQL查詢+5條screen_*規則判斷)，不需要為了省這點重算而增加程式複雜度。
        """
        if self.conn is None or not self._current_stock_id:
            self._set_autoheight_html(self.analysis_summary_view, "<p>請先從候選清單點選或查詢一檔股票。</p>")
            self._set_autoheight_html(self.analysis_tech_view, "")
            self._set_autoheight_html(self.analysis_chip_view, "")
            return
        stock_name = chart_data.get_stock_name(self.conn, self._current_stock_id)
        stock_label = f"{self._current_stock_id} {stock_name}" if stock_name else self._current_stock_id
        sections = self._build_analysis_sections_html(self._current_stock_id, f"個股分析：{stock_label}")
        self._set_autoheight_html(self.analysis_summary_view, sections["summary"])
        self._set_autoheight_html(self.analysis_tech_view, sections["tech"])
        self._set_autoheight_html(self.analysis_chip_view, sections["chip"])

    @staticmethod
    def _build_report_reference_appendix(matches: list[dict]) -> tuple[str, dict[str, str]]:
        """從規則清單(technical+chip matches合併後傳入)收集所有「原文與頁碼」實際
        引用到的筆記檔案(去重複，依第一次出現順序)，組出報表的「附錄：引用筆記
        全文」章節，跟{筆記檔名: 附錄裡的錨點id}對照表(供_render_rule_match_
        blocks()的note_anchor_map參數，把內文的引用轉成跳到這裡的連結)。

        2026-08-04新增：使用者要分享報表PDF給其他人，原本「原文與頁碼」點擊開新
        視窗讀ai/ebook-summary(-chen)/筆記的做法只在本機互動畫面有用，PDF裡沒有
        「開新視窗」這回事，改成把引用到的筆記全文直接嵌進報表(不是嵌入書籍原文，
        避免版權疑慮——這幾份筆記本身是使用者自己整理的分析文字，不是書籍逐字
        複製)，靠PDF內部錨點連結(printToPdf()會保留HTML錨點連結成PDF可點擊的
        內部跳轉)前後互相跳轉。附錄每段筆記帶一個「回引用處」連結，跳回第一條
        引用它的規則(見_render_rule_match_blocks()幫每個規則block加的
        `id="cite-{rule_id}"`)——同一份筆記可能被好幾條規則引用，只回第一條，
        不追蹤全部引用處，避免過度複雜。
        """
        seen: dict[str, Path] = {}
        first_citing_rule: dict[str, str] = {}
        for m in matches:
            reference = m.get("reference")
            if not reference:
                continue
            for filename, path in rule_docs.resolve_reference_files(reference):
                if filename not in seen:
                    seen[filename] = path
                    first_citing_rule[filename] = m["rule_id"]
        if not seen:
            return "", {}

        anchor_ids = {filename: f"note-{i}" for i, filename in enumerate(seen)}
        blocks = ['<h2 id="report-appendix">附錄：引用筆記全文</h2>']
        for filename, path in seen.items():
            anchor_id = anchor_ids[filename]
            back_link = f'<a href="#cite-{html.escape(first_citing_rule[filename])}">🔙 回引用處（{html.escape(first_citing_rule[filename])}）</a>'
            try:
                content = path.read_text(encoding="utf-8")
                body_html = markdown.markdown(content, extensions=["tables", "fenced_code"])
            except OSError as exc:
                body_html = f"<p>(讀取失敗：{html.escape(str(exc))})</p>"
            blocks.append(f'<h3 id="{anchor_id}">{html.escape(filename)}</h3>{body_html}<p>{back_link}</p><hr>')
        return "".join(blocks), anchor_ids

    def _build_report_html(self, stock_id: str, stock_label: str) -> str:
        """組出「產出報表」的完整HTML文件，依使用者指定的順序包含：圖表、個股明細、
        個股分析、附錄(引用筆記全文)——2026-08-04新增，供匯出/列印成PDF用，不是
        即時互動畫面：
        - 圖表：用`<iframe>`內嵌已經寫好的self._chart_html_path(render_chart_html()
          產生的完整獨立HTML檔案，含Plotly.js)，不重新處理它的內部結構——這個檔案
          在_rerender_chart()裡只要查詢過股票就會寫入，不受目前停留在哪個inner tab
          影響，所以這裡可以直接引用。
        - 個股明細：重用_build_overview_*_html()這5個既有方法，跟_refresh_stock_
          overview_view()用的是同一組函式。
        - 個股分析：這裡不能直接重用_build_analysis_sections_html()(那個是給即時
          互動畫面用的，「原文與頁碼」是開新視窗的ruledoc:///連結，「查看技術面↓」
          是跳到_CollapsibleBox的jumpto:///連結，兩者在匯出的PDF裡都不會有作用)。
          改成自己重新呼叫analyze_stock_signals()/analyze_chip_signals()拿到原始
          matches，總結文字沿用summarize_signal_matches()同一套統計(不含跳轉連結，
          報表不需要)，規則清單改用_render_rule_match_blocks()的report模式(傳入
          note_anchor_map，「原文與頁碼」變成跳到附錄的PDF內部連結)。
        """
        detail_builders = {
            "交易資訊": self._build_overview_quote_html,
            "法人買賣總覽": self._build_overview_institutional_html,
            "主力進出": self._build_overview_dealer_html,
            "資券變化總覽": self._build_overview_margin_html,
            "大戶籌碼": self._build_overview_chip_html,
        }
        detail_html = "".join(
            f"<h3>{html.escape(title)}</h3>{builder(stock_id)}" for title, builder in detail_builders.items()
        )

        price_df = chart_data.load_price_history(self.conn, stock_id)
        trend_df = chart_data.load_price_history(self.conn, stock_id, days=chart_data.TREND_LOOKBACK_DAYS)
        tech_matches = analyze_stock_signals(price_df, trend_df=trend_df) if not price_df.empty else []
        chip_matches = stock_detail_data.analyze_chip_signals(self.conn, stock_id)
        appendix_html, note_anchor_map = self._build_report_reference_appendix([*tech_matches, *chip_matches])

        def section_summary(matches: list[dict]) -> str:
            if not matches:
                return "<p>目前沒有符合任何已接上規則庫的訊號。</p>"
            summary = summarize_signal_matches(matches)
            top = summary["top_match"]
            top_note = (top.get("note") or "").split("\n")[0] if top else ""
            return (
                f"<p>本次共觸發 {summary['total']} 條規則"
                f"（多頭傾向{summary['bullish']}條、空頭傾向{summary['bearish']}條、"
                f"其他{summary['other']}條 — 依規則標題文字粗略分類，僅供參考）。<br>"
                f"信心最高的訊號：{html.escape(top['rule_id'])}　{html.escape(top['title'])}"
                f"（{top['confidence']}%）"
                + (f"<br>目前狀態：{html.escape(top_note)}" if top_note else "")
                + "</p>"
            )

        analysis_html = (
            "<h3>技術面</h3>" + section_summary(tech_matches) + self._render_rule_match_blocks(tech_matches, note_anchor_map)
            + "<h3>籌碼面</h3>" + section_summary(chip_matches) + self._render_rule_match_blocks(chip_matches, note_anchor_map)
        )
        chart_src = QUrl.fromLocalFile(str(self._chart_html_path)).toString()

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ font-family: 'Microsoft JhengHei UI', sans-serif; padding: 0 16px; }}
h1 {{ font-size: 20px; }}
h2 {{ font-size: 16px; border-bottom: 2px solid #2980b9; padding-bottom: 4px; margin-top: 32px; }}
h3 {{ font-size: 13px; color: #2980b9; margin-top: 20px; }}
iframe {{ width: 100%; height: 900px; border: none; }}
</style></head>
<body>
<h1 id="report-top">{html.escape(stock_label)} 個股報表</h1>
<h2>圖表</h2>
<iframe src="{chart_src}"></iframe>
<h2>個股明細</h2>
{detail_html}
<h2>個股分析</h2>
{analysis_html}
{appendix_html}
</body></html>"""

    def _refresh_report_view(self) -> None:
        """填入「產出報表」分頁的預覽內容——跟chart_view/market_chart_view同一套
        「寫進暫存檔案再load()」做法(見self._chart_html_path的說明)，組合後的HTML
        含個股分析/個股明細所有規則說明文字，容易超過setHtml()的~2MB隱性限制。
        """
        if self.conn is None or not self._current_stock_id:
            self.report_view.setHtml("<p>請先從候選清單點選或查詢一檔股票。</p>")
            return
        stock_name = chart_data.get_stock_name(self.conn, self._current_stock_id)
        stock_label = f"{self._current_stock_id} {stock_name}" if stock_name else self._current_stock_id
        report_html = self._build_report_html(self._current_stock_id, stock_label)
        self._report_html_path.write_text(report_html, encoding="utf-8")
        self.report_view.load(QUrl.fromLocalFile(str(self._report_html_path)))

    def _on_export_report_clicked(self) -> None:
        """「🖨 匯出PDF」按鈕：把目前「產出報表」預覽的內容(self.report_view已載入的
        HTML)輸出成PDF檔案，使用者選擇存檔位置。QWebEnginePage.printToPdf()是非同步
        API，完成與否透過pdfPrintingFinished訊號回報(見_on_report_pdf_finished()，
        在_build_stock_detail_tab()建構時就連接好，不是每次點擊都重新連接一次)。
        """
        if self.conn is None or not self._current_stock_id:
            QMessageBox.information(self, "尚未選取股票", "請先從候選清單點選或查詢一檔股票，才能匯出報表。")
            return
        stock_name = chart_data.get_stock_name(self.conn, self._current_stock_id)
        stock_label = f"{self._current_stock_id}_{stock_name}" if stock_name else self._current_stock_id
        default_path = str(Path.home() / "Desktop" / f"{stock_label}_報表.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "匯出報表PDF", default_path, "PDF 檔案 (*.pdf)")
        if not path:
            return
        self.report_view.page().printToPdf(path)

    def _on_report_pdf_finished(self, file_path: str, success: bool) -> None:
        if success:
            QMessageBox.information(self, "匯出完成", f"報表已儲存至：\n{file_path}")
        else:
            QMessageBox.warning(self, "匯出失敗", "PDF匯出失敗，請重試。")

    @staticmethod
    def _colored_num(value, decimals: int = 0, signed: bool = False, suffix: str = "") -> str:
        """數字上紅(正)下綠(負)——跟K棒/MACD既有的紅漲綠跌配色一致(見chart_data.py
        build_candlestick_figure()的顏色說明)。value是None/NaN時回傳"-"，不是"0"，
        區分「沒有資料」跟「剛好是0」。"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return "-"
        color = "#c0392b" if value > 0 else ("#27ae60" if value < 0 else "#333333")
        sign = "+" if signed and value > 0 else ""
        return f'<span style="color:{color};">{sign}{value:,.{decimals}f}{suffix}</span>'

    def _refresh_stock_overview_view(self) -> None:
        """填入「個股明細」5個區塊各自的內容，跟_refresh_analysis_view()同一個模式：
        只在使用者目前真的停留在這個tab、或剛切換過來時才呼叫(見_on_detail_inner_
        tab_changed()/_rerender_chart())，不用每次股票查詢都無條件計算，省下法人/
        資券的查詢成本。2026-08-03改版：原本是單一QTextEdit塞5個區塊，現在每個區塊
        各自一個_CollapsibleBox+QTextEdit(見_build_stock_overview_tab())，分開設值。
        """
        if self.conn is None or not self._current_stock_id:
            placeholder = "<p>請先從候選清單點選或查詢一檔股票。</p>"
            for title in self._STOCK_OVERVIEW_BLOCKS:
                self._set_overview_block_html(title, placeholder)
            return
        stock_id = self._current_stock_id
        builders = {
            "交易資訊": self._build_overview_quote_html,
            "法人買賣總覽": self._build_overview_institutional_html,
            "主力進出": self._build_overview_dealer_html,
            "資券變化總覽": self._build_overview_margin_html,
            "大戶籌碼": self._build_overview_chip_html,
        }
        for title, builder in builders.items():
            self._set_overview_block_html(title, builder(stock_id))

    def _set_overview_block_html(self, title: str, html_content: str) -> None:
        """跟_set_autoheight_html()同一套「依內容動態算高度」作法，套用到
        _STOCK_OVERVIEW_BLOCKS裡指定區塊自己的QTextEdit——這裡的view是QTextEdit
        不是QTextBrowser，但_set_autoheight_html()用到的API(setHtml/document()/
        frameWidth()/setFixedHeight())兩者共通，可以直接共用同一個方法。"""
        self._set_autoheight_html(self._stock_overview_views[title], html_content)

    def _build_overview_quote_html(self, stock_id: str) -> str:
        """「交易資訊」區塊內容，對應temp/個股詳情-1.jpg。2026-08-03改版：均價/成交
        金額(億)缺值時改顯示估算值(見stock_detail_data.load_quote_summary()的
        avg_price_is_estimated)，並多加一行「外資/投信持有成本(預估)」。
        """
        quote = stock_detail_data.load_quote_summary(self.conn, stock_id)
        if quote is None:
            return "<p>查無成交資料。</p>"
        c = self._colored_num
        estimated_suffix = "（估）" if quote["avg_price_is_estimated"] else ""
        avg_price_text = f"{quote['avg_price']:,.2f}{estimated_suffix}" if quote["avg_price"] is not None else "-"
        trading_money_text = (
            f"{quote['trading_money_billion']:,.2f}{estimated_suffix}"
            if quote["trading_money_billion"] is not None else "-"
        )
        cost_summary = stock_detail_data.load_latest_institutional_cost_summary(self.conn, stock_id)
        foreign_cost = cost_summary["外資"] if cost_summary else None
        trust_cost = cost_summary["投信"] if cost_summary else None
        foreign_cost_text = f"{foreign_cost:,.2f}" if foreign_cost is not None else "不適用"
        trust_cost_text = f"{trust_cost:,.2f}" if trust_cost is not None else "不適用"
        rows = [
            ("成交", f"<b>{c(quote['close'], 2)}</b>", "昨收", f"{quote['prev_close']:,.2f}" if quote["prev_close"] is not None else "-"),
            ("開盤", f"{quote['open']:,.2f}", "漲跌幅", c(quote["change_pct"], 2, signed=True, suffix="%")),
            ("最高", f"{quote['high']:,.2f}", "漲跌", c(quote["change"], 2, signed=True)),
            ("最低", f"{quote['low']:,.2f}", "總量", f"{quote['volume_lots']:,} 張"),
            ("均價", avg_price_text, "昨量",
             f"{quote['prev_volume_lots']:,} 張" if quote["prev_volume_lots"] is not None else "-"),
            ("成交金額(億)", trading_money_text,
             "振幅", c(quote["amplitude_pct"], 2, suffix="%") if quote["amplitude_pct"] is not None else "-"),
            ("外資持有成本(預估)", foreign_cost_text, "投信持有成本(預估)", trust_cost_text),
        ]
        table = f'<p style="color:#666666;">資料時間：{quote["date"]}</p><table cellspacing="0" cellpadding="4" width="100%">'
        for label1, value1, label2, value2 in rows:
            table += (
                f'<tr><td width="15%" style="color:#666666;">{label1}</td><td width="35%">{value1}</td>'
                f'<td width="15%" style="color:#666666;">{label2}</td><td width="35%">{value2}</td></tr>'
            )
        table += "</table>"
        return table

    def _build_overview_institutional_html(self, stock_id: str) -> str:
        """「法人買賣總覽」區塊內容，對應temp/個股詳情-2.jpg。2026-08-03改版：拿掉
        「當日／累計」切換，固定顯示累計表格(見_build_stock_overview_tab()的說明③)
        ——欄位天期直接沿用INSTITUTIONAL_PERIODS的標籤(見stock_detail_data.py)，
        不再另外把"1日"改標成"當日"(2026-08-03第二次改版：使用者確認直接顯示
        "1日"即可)。
        """
        cumulative = stock_detail_data.load_institutional_cumulative(self.conn, stock_id)
        if cumulative is None:
            return "<p>查無法人買賣資料。</p>"
        periods = list(stock_detail_data.INSTITUTIONAL_PERIODS.keys())
        table = '<p style="color:#666666;">單位：張</p><table cellspacing="0" cellpadding="4" width="100%" border="1" bordercolor="#e0e0e0"><tr><td></td>'
        for label in periods:
            table += f"<td align='right'><b>{label}</b></td>"
        table += "</tr>"
        for group in stock_detail_data.INSTITUTIONAL_GROUPS:
            table += f"<tr><td>{group}</td>"
            for label in periods:
                table += f"<td align='right'>{self._colored_num(cumulative[group][label] / 1000, 0, signed=True)}</td>"
            table += "</tr>"
        table += "</table>"
        cost = stock_detail_data.load_institutional_estimated_cost(self.conn, stock_id)
        table += self._build_institutional_cost_html(cost, periods)
        flow = stock_detail_data.load_institutional_flow_analysis(self.conn, stock_id)
        momentum = stock_detail_data.load_institutional_momentum_analysis(self.conn, stock_id)
        return table + self._build_institutional_flow_analysis_html(flow, momentum)

    @staticmethod
    def _build_institutional_cost_html(cost: dict | None, periods: list[str]) -> str:
        """外資／投信「預估持股成本價」表格，緊接在法人買賣總覽表格下方。2026-08-03
        新增，計算方式與「淨賣出天期標示不適用」的處理見stock_detail_data.load_
        institutional_estimated_cost()的docstring，這裡只負責把結果組成HTML表格，
        不含任何計算邏輯。cost是None(查無法人資料，理論上跟主表格是否為None一致，
        這裡獨立防呆)時不顯示這個區塊。"""
        if cost is None:
            return ""
        html_parts = [
            '<p style="margin-top:10px; color:#666666;">預估持股成本價（單位：元，淨賣出天期無累積部位，標示為不適用）</p>',
            '<table cellspacing="0" cellpadding="4" width="100%" border="1" bordercolor="#e0e0e0"><tr><td></td>',
        ]
        for label in periods:
            html_parts.append(f"<td align='right'><b>{label}</b></td>")
        html_parts.append("</tr>")
        for group in stock_detail_data.ESTIMATED_COST_GROUPS:
            html_parts.append(f"<tr><td>{group}</td>")
            for label in periods:
                value = cost[group][label]
                cell = f"{value:,.2f}" if value is not None else '<span style="color:#999999;">不適用</span>'
                html_parts.append(f"<td align='right'>{cell}</td>")
            html_parts.append("</tr>")
        html_parts.append("</table>")
        return "".join(html_parts)

    @staticmethod
    def _build_institutional_flow_analysis_html(flow: dict | None, momentum: dict | None = None) -> str:
        """依load_institutional_flow_analysis()的「連續買超／賣超天數」判讀結果，
        組出法人買賣總覽表格下方的分析文字。2026-08-03新增，使用者要求依朱家泓/
        陳家豐書中的理論基礎，把「連續N日買賣超」判讀成停損觀察或可能的進場訊號。

        理論依據(見src/indicators/institutional_flow.py模組docstring完整說明)：
        - 三大法人連續賣超≥3天 → 停損觀察，依朱家泓《抓住飆股輕鬆賺》淘汰法選股
          排除規則第8項(R-SCREEN-06)，書中明確門檻3天。
        - 投信連續買超≥3天 → 觀察是否為布局訊號，依陳家豐《看懂籌碼 股市賺大錢》
          第4篇第2章「風向球 投信動向幫抬轎」(書中給3~5天，這裡採下限3天)。
        - 外資/自營商的可信度提醒同樣出自陳家豐書中第4篇第1章：自營商操作週期短、
          建議「首先剔除」，不用連續性判斷趨勢；外資只有中小型股才有參考價值，
          權值股/大型股受全球布局/期貨套利/指數調整干擾，不宜直接採信。
        這裡刻意不去猜測「主力底部吸籌」「高檔換手」這類更細緻的敘事——陳家豐書中
        雖然有類似案例(例如可成2012年公司派逆勢加碼的故事)，但明確承認沒有給出
        「連續幾天／多少金額才算數」的精確門檻，程式化時勉強湊一個數字出來反而是
        過度宣稱，不在這裡呈現，只呈現書中真正給出明確天數門檻的兩條規則。

        momentum是stock_detail_data.load_institutional_momentum_analysis()的
        「近N天合計 vs 前N天合計」比較結果，2026-08-03新增：使用者要求另外呈現
        累積近5日/20日/40日的買賣力道是變多還是變少，且要求拆成外資／投信各自
        分析、再看兩者加總，自營商不計入(理由跟上面「自營商連續買賣超」段落
        引用的陳家豐書中提醒一致：自營商操作週期短、不適合判斷趨勢性力道)。
        這段不是書中理論，是使用者直接指定的量化比較邏輯，跟上面「連續同方向
        天數」的判讀角度不同(那個看方向持續多久，這個看力道有沒有比上一個
        同長度區間更強)，文字裡不掛書名，避免誤植成書中規則。
        """
        if flow is None and momentum is None:
            return ""
        lines = ['<p style="margin-top:10px;"><b>📊 法人籌碼分析</b></p>']
        streak_lines_start = len(lines)

        if flow is not None:
            sanhua = flow["三大法人"]
            if sanhua["is_sell_warning"]:
                lines.append(
                    f'<p style="color:#27ae60;">⚠️ 三大法人已連續賣超{sanhua["streak_days"]}天，'
                    "達到停損觀察門檻（依朱家泓《抓住飆股輕鬆賺》淘汰法選股排除規則第8項："
                    "三大法人連續賣超應避開，建議留意停損／減碼）。</p>"
                )
            elif sanhua["is_buy_watch"]:
                lines.append(
                    f'<p style="color:#c0392b;">三大法人已連續買超{sanhua["streak_days"]}天，'
                    "短線動能偏多，但書中沒有給「連續買超代表安全」的保證，僅供參考。</p>"
                )

            invtrust = flow["投信"]
            if invtrust["is_buy_watch"]:
                lines.append(
                    f'<p style="color:#c0392b;">📈 投信已連續買超{invtrust["streak_days"]}天'
                    "（依陳家豐《看懂籌碼 股市賺大錢》：投信受法規限制(單一個股持股上限10%、"
                    "單日買進不得超過成交量10%)須分批布局，連續加碼3~5天且個股剛脫離下跌"
                    "整理通常是切入時機——本畫面沒有另外判斷「是否剛脫離整理區」，需自行"
                    "對照K線圖）。</p>"
                )
            elif invtrust["is_sell_warning"]:
                lines.append(
                    f'<p style="color:#27ae60;">投信已連續賣超{invtrust["streak_days"]}天，'
                    "留意是否轉向保守（書中提到投信若轉向防禦型持股，代表對後市看淡）。</p>"
                )

            foreign = flow["外資"]
            if foreign["is_buy_watch"] or foreign["is_sell_warning"]:
                direction_text = "買超" if foreign["is_buy_watch"] else "賣超"
                lines.append(
                    f'<p style="color:#999999;">外資已連續{direction_text}{foreign["streak_days"]}天——'
                    "⚠️ 陳家豐書中提醒：外資買賣單只有在中小型股(非權值股)才有參考價值，"
                    "權值股/大型股的外資買賣受全球布局、期貨套利、指數調整干擾，不宜直接"
                    "採信本訊號判斷多空。</p>"
                )

            dealer = flow["自營商"]
            if dealer["is_buy_watch"] or dealer["is_sell_warning"]:
                direction_text = "買超" if dealer["is_buy_watch"] else "賣超"
                lines.append(
                    f'<p style="color:#999999;">自營商連續{direction_text}{dealer["streak_days"]}天——'
                    "⚠️ 陳家豐書中建議「自營商首先剔除」，操作週期短、常忽買忽賣，不建議"
                    "用連續性判斷趨勢，僅供參考。</p>"
                )

            if len(lines) == streak_lines_start:
                lines.append(f"<p>近期法人買賣方向尚未達連續{INSTITUTIONAL_STREAK_THRESHOLD}天門檻，暫無明顯訊號。</p>")

        if momentum:
            trend_colors = {
                "買超力道增強": "#c0392b",
                "由賣轉買": "#c0392b",
                "賣壓加重": "#27ae60",
                "由買轉賣": "#27ae60",
            }
            group_titles = {
                "外資": "📐 外資買賣力道變化（近期比前期）",
                "投信": "📐 投信買賣力道變化（近期比前期）",
                "外資+投信": "📐 外資＋投信合計買賣力道變化（近期比前期，不含自營商）",
            }
            for group in stock_detail_data.MOMENTUM_GROUPS:
                periods = momentum.get(group)
                if not periods:
                    continue
                lines.append(f'<p style="margin-top:6px;"><b>{group_titles[group]}</b></p>')
                for label, info in periods.items():
                    current_lots = info["current"] / 1000
                    prior_lots = info["prior"] / 1000
                    trend = info["trend"]
                    color = trend_colors.get(trend, "#999999")
                    lines.append(
                        f'<p style="color:{color};">近{label}合計買賣超{current_lots:+,.0f}張，'
                        f"較前{label}（{prior_lots:+,.0f}張）{trend}"
                        f'——{"持續買進" if trend in ("買超力道增強", "由賣轉買") else ("持續賣出" if trend in ("賣壓加重", "由買轉賣") else "力道趨緩，方向未明確轉變")}。</p>'
                    )

        return "".join(lines)

    def _build_overview_dealer_html(self, stock_id: str) -> str:
        """「主力進出」區塊內容，對應temp/個股詳情-3.jpg：4張指標卡片(主力買賣超/
        主力買超/主力賣超/買賣超佔成交量)＋買超/賣超雙欄券商表格。目前沒有券商分點
        籌碼資料來源(見stock_detail_data.py模組docstring)，先照這個版面做框架，
        所有數值顯示"-"，不是造假資料湊。"""
        warning = (
            "<p style=\"color:#999999;\">⚠️ 尚未串接資料來源（需要券商分點籌碼資料，"
            "schema已預留broker_chips表，待FinMind付費方案開通後才能接上）。</p>"
        )
        cards = (
            '<table cellspacing="8" cellpadding="10" width="100%" border="1" bordercolor="#e0e0e0">'
            "<tr>"
            "<td width='25%' align='center'><span style='color:#666666;'>主力買賣超(張)</span><br>"
            "<span style='font-size:18pt; font-weight:bold;'>-</span></td>"
            "<td width='25%' align='center'><span style='color:#666666;'>主力買超(張)</span><br>"
            "<span style='font-size:18pt; font-weight:bold;'>-</span></td>"
            "<td width='25%' align='center'><span style='color:#666666;'>主力賣超(張)</span><br>"
            "<span style='font-size:18pt; font-weight:bold;'>-</span></td>"
            "<td width='25%' align='center'><span style='color:#666666;'>買賣超佔成交量</span><br>"
            "<span style='font-size:18pt; font-weight:bold;'>-</span></td>"
            "</tr></table>"
        )
        broker_table = (
            '<table cellspacing="0" cellpadding="4" width="100%" border="1" bordercolor="#e0e0e0">'
            "<tr><td><b>買超券商</b></td><td align='right'><b>買進</b></td><td align='right'><b>賣出</b></td>"
            "<td align='right'><b>買超張數</b></td>"
            "<td><b>賣超券商</b></td><td align='right'><b>買進</b></td><td align='right'><b>賣出</b></td>"
            "<td align='right'><b>賣超張數</b></td></tr>"
            "<tr><td>-</td><td align='right'>-</td><td align='right'>-</td><td align='right'>-</td>"
            "<td>-</td><td align='right'>-</td><td align='right'>-</td><td align='right'>-</td></tr>"
            "</table>"
        )
        return warning + cards + broker_table

    def _build_overview_margin_html(self, stock_id: str) -> str:
        """「資券變化總覽」區塊內容，對應temp/個股詳情-4.jpg，維持「當日／累計」
        切換(self._margin_cumulative，見_build_stock_overview_tab())。2026-08-03
        新增：表格下方固定加一段融資維持率分析(不受當日/累計切換影響，是「目前狀態」
        的快照，不是某個期間的加總)，見_build_margin_maintenance_analysis_html()。
        """
        maintenance = stock_detail_data.load_margin_maintenance_analysis(self.conn, stock_id)
        analysis_html = self._build_margin_maintenance_analysis_html(maintenance)

        if not self._margin_cumulative:
            margin = stock_detail_data.load_margin_daily(self.conn, stock_id)
            if margin is None:
                return "<p>查無資券資料。</p>" + analysis_html
            table = (
                f'<p style="color:#666666;">資料時間：{margin["date"]}</p>'
                '<table cellspacing="0" cellpadding="4" width="100%" border="1" bordercolor="#e0e0e0">'
                "<tr><td></td><td align='right'><b>買進</b></td><td align='right'><b>賣出</b></td>"
                "<td align='right'><b>現價</b></td><td align='right'><b>增減</b></td>"
                "<td align='right'><b>餘額</b></td><td align='right'><b>使用率</b></td>"
                "<td align='right'><b>連增連減</b></td></tr>"
            )
            for row_label, key in (("融資", "margin"), ("融券", "short")):
                r = margin[key]
                usage_rate_cell = f"{r['usage_rate']:.2f}%" if r["usage_rate"] is not None else "-"
                table += (
                    f"<tr><td>{row_label}</td>"
                    f"<td align='right'>{r['buy']:,}</td><td align='right'>{r['sell']:,}</td>"
                    f"<td align='right'>{margin['close']:,.2f}</td>"
                    f"<td align='right'>{self._colored_num(r['change'], 0, signed=True)}</td>"
                    f"<td align='right'>{r['balance']:,}</td>"
                    f"<td align='right'>{usage_rate_cell}</td>"
                    f"<td align='right'>{r['streak'] or '-'}</td></tr>"
                )
            table += "</table>"
            offset_text = f"{margin['offset_loan_and_short']:,}" if margin["offset_loan_and_short"] is not None else "-"
            ratio_text = f"{margin['short_to_margin_ratio_pct']:.2f}%" if margin["short_to_margin_ratio_pct"] is not None else "-"
            table += f"<p>資券互抵：{offset_text} 張　券資比：{ratio_text}</p>"
            return table + analysis_html

        cumulative_days = 20
        margin_cum = stock_detail_data.load_margin_cumulative(self.conn, stock_id, days=cumulative_days)
        if margin_cum is None:
            return "<p>查無資券資料。</p>" + analysis_html
        table = (
            f'<p style="color:#666666;">最近{margin_cum["days"]}個交易日累計</p>'
            '<table cellspacing="0" cellpadding="4" width="100%" border="1" bordercolor="#e0e0e0">'
            "<tr><td></td><td align='right'><b>買進</b></td><td align='right'><b>賣出</b></td>"
            "<td align='right'><b>餘額增減</b></td></tr>"
        )
        for row_label, key in (("融資", "margin"), ("融券", "short")):
            r = margin_cum[key]
            table += (
                f"<tr><td>{row_label}</td><td align='right'>{r['buy']:,}</td>"
                f"<td align='right'>{r['sell']:,}</td>"
                f"<td align='right'>{self._colored_num(r['change'], 0, signed=True)}</td></tr>"
            )
        table += "</table>"
        return table + analysis_html

    @staticmethod
    def _build_margin_maintenance_analysis_html(maintenance: dict | None) -> str:
        """依load_margin_maintenance_analysis()的融資維持率估算結果，組出資券變化
        總覽表格下方的分析文字。2026-08-03新增。

        理論依據：陳家豐《看懂籌碼 股市賺大錢》第2篇第4章「融資維持率揭秘 勿買斷頭股」
        (見src/indicators/margin_trading.py模組docstring完整說明)——166%(初始，
        假設6成融資)→135%(警戒，「爹不疼娘不愛」時期，主力不會這時候進場)→120%
        (斷頭線，券商強制賣出)→連續N天低於120%(超跌反彈訊號，書中：融資維持率過低
        導致超跌，是搶短線反彈的好機會，但僅適合手腳靈活的投資人，需嚴設停利)。

        融資成數用書中預設的6成估算(不是這檔股票實際的融資成數規定，TWSE公開資料
        查不到逐股融資成數)，這裡明確標註出來，避免使用者誤以為是精確數字。
        """
        if maintenance is None or maintenance["ratio"] is None:
            return ""
        ratio_pct = maintenance["ratio"] * 100
        state = maintenance["state"]
        lines = [
            '<p style="margin-top:10px;"><b>📊 融資維持率分析</b></p>',
            f"<p>估算融資維持率：{ratio_pct:.1f}%（狀態：{state}；融資成數估算採書中預設"
            "的6成，非個股實際規定，僅供參考）</p>",
        ]
        if state == "已跌破斷頭線":
            lines.append(
                '<p style="color:#27ae60;">⚠️ 估算已跌破斷頭線(120%)，代表融資部位可能面臨'
                "券商強制賣出的斷頭賣壓（依陳家豐《看懂籌碼》第2篇第4章）。</p>"
            )
        elif state == "警戒區(爹不疼娘不愛)":
            lines.append(
                '<p style="color:#e67e22;">融資維持率落在135%以下的警戒區——書中提醒：'
                "主力通常不會在這個階段進場，因為要面對層層融資套牢賣壓，此時股票容易"
                "「爹不疼、娘不愛」，若後續跌破120%，可留意超跌反彈機會。</p>"
            )
        if maintenance["oversold_rebound_signal"]:
            lines.append(
                '<p style="color:#c0392b;">📈 已連續多日低於120%斷頭線，符合書中「超跌'
                "反彈」訊號條件——僅適合手腳靈活、能嚴設停利的短線操作，不是長線買進"
                "依據。</p>"
            )
        return "".join(lines)

    def _build_overview_chip_html(self, stock_id: str) -> str:
        """「大戶籌碼」區塊內容，對應temp/個股詳情-5.jpg。目前資料庫schema沒有股權
        分散/大戶持股資料表，先做表格框架，數值顯示"-"。"""
        warning = (
            "<p style=\"color:#999999;\">⚠️ 尚未串接資料來源（需要股權分散/大戶持股統計資料，"
            "目前資料庫schema還沒有對應的表）。</p>"
        )
        table = (
            '<table cellspacing="0" cellpadding="4" width="100%" border="1" bordercolor="#e0e0e0">'
            "<tr><td>年度/日期</td><td>外資籌碼</td><td>大戶籌碼</td><td>董監持股</td><td>股價</td></tr>"
            "<tr><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td></tr></table>"
        )
        return warning + table

    # ------------------------------------------------------------------
    # 按鈕
    # ------------------------------------------------------------------

    def _on_refresh_clicked(self) -> None:
        if self.conn is None:
            return
        run_screen_and_store(self.conn)
        self._refresh_date_list()
        self._reload_candidates()
        self._poll_pipeline_status()  # 立即刷新狀態列的「候選清單算至：...」，不等下一次5秒輪詢
        # 只在使用者目前真的停留在「大盤」/「個股資訊」分頁時才立即整理——分頁還沒被
        # 切換過去顯示的話，QTextEdit/QWebEngineView還沒有真正的layout，這時候整理
        # 只會算出錯誤的高度(見_build_stock_detail_tab()/_build_market_tab()的說明)。
        # 之後切過去時_on_tab_changed()會自動重新整理，資料本來就是即時查DB，不會
        # 顯示到舊資料。
        current_tab = self.tabs.currentIndex()
        if current_tab == TAB_STOCK_DETAIL and self._current_stock_id:
            self._rerender_chart()
        elif current_tab == TAB_MARKET:
            self._refresh_market_tab()

    def _on_fetch_clicked(self) -> None:
        if self._pipeline_worker is not None and self._pipeline_worker.isRunning():
            return
        self.fetch_btn.setEnabled(False)
        self._pipeline_worker = PipelineWorker()
        self._pipeline_worker.finished_ok.connect(self._on_fetch_finished)
        self._pipeline_worker.failed.connect(self._on_fetch_failed)
        self._pipeline_worker.progress.connect(self._on_fetch_progress)
        self._pipeline_worker.start()

    def _on_fetch_progress(self, stage: str, done: int, total: int) -> None:
        self.status_label.setText(f"狀態：抓取中...{stage} {done}/{total}檔")

    def _on_fetch_finished(self, candidate_count: int) -> None:
        self.fetch_btn.setEnabled(True)
        self._refresh_date_list()
        self._reload_candidates()
        self._poll_pipeline_status()  # 立即刷新狀態列成「資料更新至：...」，不等下一次5秒輪詢
        # 手動抓取也會更新大盤資料(見fetch_today_taiex())跟目前選取股票的價格資料，
        # 只在使用者目前就停留在對應分頁時才立即整理，理由同_on_refresh_clicked()。
        current_tab = self.tabs.currentIndex()
        if current_tab == TAB_STOCK_DETAIL and self._current_stock_id:
            self._rerender_chart()
        elif current_tab == TAB_MARKET:
            self._refresh_market_tab()
        QMessageBox.information(self, "完成", f"今日資料抓取完成，候選清單共{candidate_count}檔。")

    def _on_fetch_failed(self, message: str) -> None:
        self.fetch_btn.setEnabled(True)
        QMessageBox.warning(self, "失敗", f"抓取失敗：{message}")

    # ------------------------------------------------------------------
    # 狀態列（跟排程觸發的run_daily_pipeline()共用同一份pipeline_status.json）
    # ------------------------------------------------------------------

    def _check_for_external_candidate_update(self) -> None:
        """⚠️ 2026-08-01修正使用者回報的bug：排程(Windows工作排程器)背景觸發
        run_daily_pipeline()完成後，狀態列的「候選清單算至：...」時間戳會正確更新
        (直接查DB)，但候選清單日期下拉選單/候選清單表格本身完全沒有跟著重新載入——
        因為原本的_poll_pipeline_status()只更新狀態文字，從來沒有呼叫_refresh_date_
        list()/_reload_candidates()，只有「本視窗自己按按鈕觸發抓取/重新篩選」的路徑
        才會主動刷新，排程在背景默默跑完的情況完全沒被感知到。

        改成每次輪詢都比對候選清單最新一筆的created_at時間戳有沒有變化，變了(且不是
        本函式第一次執行、只是在建立比對基準，避免視窗剛啟動時誤判成「外部更新」而
        重複刷新一次)就代表候選清單被更新過(不管是誰觸發的)，主動重新載入日期下拉
        選單跟候選清單表格。
        """
        latest = chart_data.get_latest_candidate_update_time(self.conn)
        if self._last_seen_candidate_update is not None and latest != self._last_seen_candidate_update:
            self._refresh_date_list()
            self._reload_candidates()
        self._last_seen_candidate_update = latest

    def _check_for_external_watchlist_update(self) -> None:
        """觀察清單版本的_check_for_external_candidate_update()——同一個bug、同一種
        修法：背景排程(Windows工作排程器觸發的daily_pipeline.py，或F/G的排程/手動
        回補)把DB更新完後，如果使用者剛好停留在觀察清單分頁，畫面不會自動反映新
        資料，只有切換分頁或手動按「重新整理」才看得到。

        分別追蹤股價/法人資料(`stocks.updated_at`)跟F/G(`holder_shares_
        distribution.updated_at`)兩個獨立的時間戳，任一個變了就代表觀察清單顯示
        的資料可能過時；只在使用者「目前正停留在觀察清單分頁」時才真的重新整理
        (不像候選清單版本那樣不管在哪個分頁都重刷——觀察清單重新整理要逐股查
        D~R好幾張表，沒必要在使用者根本沒在看這個分頁時也做；切換分頁本身
        (`_on_tab_changed()`)已經會在切過去的當下重新整理一次，兩者互補)。
        """
        if self.conn is None:
            return
        price_update = chart_data.get_latest_update_time(self.conn)
        holder_update = huang_chip_data.get_latest_holder_update_time(self.conn)
        price_changed = self._last_seen_watchlist_price_update is not None and price_update != self._last_seen_watchlist_price_update
        holder_changed = self._last_seen_watchlist_holder_update is not None and holder_update != self._last_seen_watchlist_holder_update
        if (price_changed or holder_changed) and self.tabs.currentIndex() == TAB_WATCHLIST:
            self._refresh_watchlist_tab()
        self._last_seen_watchlist_price_update = price_update
        self._last_seen_watchlist_holder_update = holder_update

    def _poll_pipeline_status(self) -> None:
        # 如果本視窗自己觸發的PipelineWorker正在跑，狀態列已經由_on_fetch_progress()顯示
        # 更細緻的下載進度(例如「TPEx 500/1980檔」)，這裡就不要每5秒用pipeline_status.json
        # 的籠統「目前正在自動抓取資料…」蓋過去——這個輪詢機制主要是給「排程觸發、桌面版
        # 剛好開著」的情境用的，跟本視窗自己觸發的抓取搶著更新同一個label沒有意義。
        if self._pipeline_worker is not None and self._pipeline_worker.isRunning():
            return
        if self.conn is not None:
            self._check_for_external_candidate_update()
            self._check_for_external_watchlist_update()
        status = pipeline_status.read_status()
        state = status.get("status") if status else None
        if state == "running":
            date_label = status.get("date", "")
            if pipeline_status.is_stale(status):
                # process被強制中止(kill/當機/斷電)時，Python的except/finally完全沒機會
                # 執行，狀態檔案會永久停在最後一次心跳的"running"——is_stale()判斷太久
                # 沒更新，這裡不能再顯示「更新中」誤導使用者，要明確標示可能已經中斷。
                self.status_label.setText(f"⚠ 上次自動更新可能已中斷（{date_label}，請重新手動抓取）")
                return
            # 排程觸發(Windows工作排程器)剛好在桌面版開著的時候跑，這裡是唯一會顯示
            # 「更新中」的路徑；本視窗自己按按鈕觸發的情況已經被上面的guard擋掉，改由
            # _on_fetch_progress()顯示更細緻的下載進度。心跳機制(見daily_pipeline.py的
            # _on_progress())順便也把stage/progress寫進狀態檔，排程觸發的執行一樣能在
            # 這裡顯示細緻進度，不是只有本視窗自己觸發才看得到。
            stage = status.get("stage")
            progress = status.get("progress")
            detail = f" {stage} {progress}檔" if stage and progress else ""
            self.status_label.setText(f"🔄 更新中...（{date_label}）{detail}")
            return
        next_run_label = pipeline_status.next_scheduled_run_time().strftime("%Y-%m-%d %H:%M")

        if state == "failed":
            date_label = status.get("date", "")
            self.status_label.setText(f"⚠ 上次抓取失敗（{date_label}）\n下次更新時間：{next_run_label}")
            return

        # 閒置狀態：股價DB更新時間跟候選清單重算時間是兩件各自獨立的事(股價可能已更新到
        # 今天，但候選清單是剛才手動重篩才產生的)，分開顯示成兩行，不能只顯示其中一個、
        # 讓使用者誤判「候選清單是不是也跟著更新了」。「下次更新時間」是Windows工作排程器
        # 下一個固定時段(見pipeline_status.SCHEDULED_TIMES)，只代表排程「預期何時會嘗試」，
        # 不保證那天一定是交易日(是否為交易日由run_daily_pipeline()執行當下自己判斷)。
        if self.conn is None:
            self.status_label.setText(f"狀態：尚無資料\n下次更新時間：{next_run_label}")
            return
        price_update = self._format_update_timestamp(chart_data.get_latest_update_time(self.conn))
        candidate_update = self._format_update_timestamp(chart_data.get_latest_candidate_update_time(self.conn))
        self.status_label.setText(
            f"股價更新至：{price_update}\n候選清單算至：{candidate_update}\n下次更新時間：{next_run_label}"
        )

    @staticmethod
    def _format_update_timestamp(ts: str | None) -> str:
        """把DB裡存的ISO8601時間戳字串格式化成"YYYY-MM-DD HH:MM"給使用者看，缺值時
        顯示「尚無資料」——status_label(選股分頁)、market_update_label(大盤分頁)
        共用同一套格式，避免兩處各自維護一份格式邏輯而不小心不一致。"""
        if not ts:
            return "尚無資料"
        try:
            return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return ts
