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
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from desktop.chart_render import render_chart_html
from src.data import storage
from src.data.connection import get_default_connection
from src.data.yfinance_client import TAIEX_STOCK_ID
from src.indicators.moving_average import FULL_PERIODS
from src.patterns import chart_overlays, latest_day_summary
from src.presentation import chart_data, pipeline_status
from src.screener.daily_screener import analyze_stock_signals, run_screen_and_store, summarize_signal_matches

TAIEX_DISPLAY_NAME = "台股加權指數"

# 分頁索引，對應_build_ui()裡addTab()的呼叫順序：大盤/選股/個股清單。
TAB_MARKET = 0
TAB_SCREENER = 1
TAB_STOCK_DETAIL = 2


def _format_month_day(date_str: str) -> str:
    """"YYYY-MM-DD" -> "X月X日"(不補零)，供「個股清單」分頁右上角的來源標籤使用。
    格式不符預期時原樣回傳，不拋例外——來源標籤只是輔助資訊，不應該因為格式問題讓
    整個畫面crash。
    """
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    return f"{d.month}月{d.day}日"


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

        self._pipeline_worker: PipelineWorker | None = None
        self._current_stock_id: str | None = None
        # 目前「個股清單」分頁顯示的股票是從候選清單哪一天的選股策略點進來的("YYYY-MM-DD"
        # 字串)；手動查詢時設為None，右上角的來源標籤(self.stock_source_label)就不顯示
        # (見_on_candidate_selected()/_on_search())。
        self._current_stock_source: str | None = None
        # 追蹤上一次輪詢到的「候選清單算至」時間戳，供_poll_pipeline_status()偵測候選
        # 清單是否被外部(排程/Windows工作排程器背景觸發run_daily_pipeline())更新過，
        # 見_check_for_external_candidate_update()的說明。
        self._last_seen_candidate_update: str | None = None
        # QWebEngineView.setHtml()對內容大小有~2MB的隱性限制(Chromium的data: URL限制，超過
        # 會loadFinished(False)、畫面完全空白且不會報錯)——Plotly圖表把plotly.js整包內嵌後
        # 通常有4~5MB，遠超過這個限制。改成寫進暫存檔案再用load(QUrl.fromLocalFile(...))，
        # 檔案大小沒有這個限制。同一個視窗重複使用同一個暫存檔案，不會每次渲染都留下新檔案。
        self._chart_html_path = Path(tempfile.gettempdir()) / f"tw_stock_chart_{id(self)}.html"
        # 見showEvent()：「大盤」是分頁0、也是視窗一開啟就顯示的預設分頁，`tabs.
        # currentChanged`訊號只在「真正切換」時觸發，開頭從-1變成0這個初始狀態不算
        # 「切換」不會發訊號，導致大盤分頁沒有經過_on_tab_changed()、一直是空白，要手動
        # 切到別的分頁再切回來才會有內容。這個旗標確保只在視窗第一次顯示時補打一次。
        self._startup_tab_refreshed = False

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
        # 三個分頁：①大盤、②選股(候選清單篩選+清單本身)、③個股清單(個股查詢+K線圖+
        # 個股分析)——原本候選清單跟個股圖表擠在同一個分頁，使用者反映畫面太擁擠，拆開
        # 後候選清單點選任一列會自動切到③並代入該股票資料(見_on_candidate_selected())。
        # ①跟③都用同一套規則比對邏輯(_build_analysis_html())，只是分析對象(大盤/個股)
        # 不同，渲染格式共用。
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._build_market_tab()
        self._build_screener_tab()
        self._build_stock_detail_tab()
        # ⚠️ 分頁還沒被切換過去顯示之前，分頁裡的QTextEdit/QWebEngineView實際上沒有
        # 真正的layout(viewport寬度等於0或預設值)，這時候算文字框需要的高度一定不準
        # (實測算出來只有個位數px，見_build_market_tab()的說明)。改成切到對應分頁時
        # 才(重新)整理內容，順便也讓每次切回來都看得到最新資料。
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _build_screener_tab(self) -> None:
        """「選股」分頁：候選清單篩選條件+候選清單本身，不含個股圖表/分析(那些移到
        「個股清單」分頁，見_build_stock_detail_tab())——原本候選清單跟個股圖表擠在
        同一個分頁，使用者反映畫面太擁擠，拆開後這裡可以完整顯示候選清單，不用捲很久
        才看得到後面的圖表。點選候選清單裡任一列會自動切到「個股清單」分頁並代入該
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
            cb.setChecked(chart_data.CANDIDATE_FILTER_DEFAULTS.get(label, False))
            filter_bar.addWidget(cb)
            self.filter_checkboxes[label] = cb
        filter_bar.addStretch()
        root_layout.addLayout(filter_bar)

        # 「篩選方法：」這一列跟上面「篩選條件：」分開放——SAR翻轉/朱家泓技術分析是「訊號
        # 從哪裡/用什麼方法判斷出來的」，跟上面均線多頭排列這種「候選股本身要滿足的條件」
        # 概念上不同，使用者要求分開一列，視覺上也比較不擁擠。
        method_bar = QHBoxLayout()
        method_bar.addWidget(QLabel("篩選方法："))
        # SAR翻轉篩選：勾選框+多頭/空頭下拉+翻轉天數輸入綁在一起，不是單純的勾選框，因此沒有
        # 塞進CANDIDATE_FILTERS的registry迴圈，另外獨立組裝、獨立傳給apply_candidate_filters
        # 的sar_flip_option參數(見src/presentation/chart_data.py)。
        self.sar_flip_checkbox = QCheckBox("SAR 翻轉")
        method_bar.addWidget(self.sar_flip_checkbox)
        self.sar_flip_direction_combo = QComboBox()
        self.sar_flip_direction_combo.addItems(["多頭", "空頭"])
        method_bar.addWidget(self.sar_flip_direction_combo)
        self.sar_flip_days_spin = QSpinBox()
        self.sar_flip_days_spin.setRange(1, 60)
        self.sar_flip_days_spin.setValue(1)
        self.sar_flip_days_spin.setSuffix(" 天內翻轉")
        method_bar.addWidget(self.sar_flip_days_spin)

        # 「朱家泓技術分析」勾選框：2026-08-01新增，目前是標示用——候選清單目前100%來自
        # 朱家泓的書(見chart_data._signal_matches_zhu_rulebook())，還沒有其他規則來源，
        # 勾選/不勾選這裡暫時不會改變候選清單內容，等籌碼分析(陳家豐)規則接上候選清單
        # 產生流程後才會真正篩出差異。預設勾選，跟現況(全部都是朱家泓規則)一致。
        method_bar.addSpacing(20)
        self.zhu_rule_checkbox = QCheckBox("朱家泓技術分析")
        self.zhu_rule_checkbox.setChecked(True)
        self.zhu_rule_checkbox.setToolTip("目前候選清單全部來自朱家泓的書，這裡暫為標示用；等籌碼分析規則接上後才會真正篩選")
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
        # 在候選清單「內」搜尋(跟「個股清單」分頁裡的self.search_input不同——那個是不限
        # 候選清單、對任意股票代號/名稱做全域查詢；這個只在目前候選清單的列裡找，找到就
        # 選取+捲動過去，順便觸發_on_candidate_selected()連帶切到「個股清單」分頁)。
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

        self.intraday_label = QLabel("⚠ 尚未收盤，本頁為盤中即時資料，收盤後數字可能改變")
        self.intraday_label.setStyleSheet("color: red; font-weight: bold;")
        self.intraday_label.setVisible(False)
        root_layout.addWidget(self.intraday_label)

        self.candidates_table = QTableWidget()
        self.candidates_table.setColumnCount(8)
        self.candidates_table.setHorizontalHeaderLabels(["股票代號", "名稱", "產業別", "訊號(信心%)", "進場價", "停損價", "漲跌幅(%)", "成交量"])
        # ⚠️ 之前對整個header統一套用Stretch，會讓8欄一律平分寬度——「訊號」欄內容通常
        # 遠比其他欄位長，平分寬度下wrap出來的行數暴增、視覺上看起來像沒有斷行。改成除了
        # 「訊號」欄以外都用ResizeToContents(依內容自動給剛好的寬度)，多出來的空間全部
        # 留給「訊號」欄(Stretch)，這樣wrap後的行數才會合理。
        header = self.candidates_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        _SIGNAL_COLUMN = 3
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
        # src/presentation/chart_data.py的load_candidates_for_date())；開word wrap
        # 讓Qt正確把每個\n斷行顯示，而不是被裁掉或擠在一行，_reload_candidates()填完
        # 資料後還要呼叫resizeRowsToContents()讓列高跟著撐開，不然多行內容會被壓在
        # 原本單行的列高裡看不全。
        self.candidates_table.setWordWrap(True)
        self.candidates_table.itemSelectionChanged.connect(self._on_candidate_selected)
        root_layout.addWidget(self.candidates_table, stretch=1)

    def _build_stock_detail_tab(self) -> None:
        """「個股清單」分頁：個股查詢+K線圖+均線/切線/支撐壓力/MACD/KD/SAR切換+個股分析+
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
        self.tabs.addTab(detail_scroll, "個股清單")

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
        bottom_layout.addLayout(search_row)

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

        self.analysis_btn = QPushButton("📊 個股分析")
        self.analysis_btn.setCheckable(True)
        self.analysis_btn.setToolTip("顯示這檔股票目前符合規則庫中哪些訊號，依信心分數排序")
        self.analysis_btn.toggled.connect(self._on_analysis_toggled)
        controls_row.addWidget(self.analysis_btn)
        bottom_layout.addLayout(controls_row)

        # 「個股分析」內嵌展開面板：預設隱藏，按下上面的按鈕才顯示/計算內容，跟切換均線/切線
        # 那些checkbox不同(那些是「一定要顯示圖表」的常態設定)，這是選擇性才需要的額外資訊，
        # 不用一直佔畫面空間。
        #
        # ⚠️ 2026-07-29修正：原本用setMaximumHeight(200)固定高度，符合規則的訊號一多，內容
        # 塞不進200px，QTextEdit自己的捲軸就會出現——使用者反映「要在小框框裡捲動」體驗很差。
        # 改成不設上限高度，依實際內容量動態算出剛好的高度(setFixedHeight，見
        # _set_analysis_html())，關掉QTextEdit自己的垂直捲軸，讓內容多的時候由最外層
        # 的detail_scroll(整個分頁)捲動，不是在這個小框框內部另外捲動一次。
        self.analysis_view = QTextEdit()
        self.analysis_view.setReadOnly(True)
        self.analysis_view.setFrameShape(QFrame.Shape.NoFrame)
        self.analysis_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.analysis_view.setVisible(False)
        bottom_layout.addWidget(self.analysis_view)

        # 面板展開後可能撐得很高(訊號一多，見_set_analysis_html())，使用者反映展開後
        # 要收合得捲回最上面重新點一次上面的按鈕很麻煩——在面板正下方另外放一個「收合」
        # 按鈕，點了直接取消勾選analysis_btn(觸發跟上面按鈕完全相同的_on_analysis_
        # toggled(False)邏輯)，不用捲動視窗。
        self.analysis_collapse_btn = QPushButton("🔼 收合個股分析")
        self.analysis_collapse_btn.setVisible(False)
        self.analysis_collapse_btn.clicked.connect(lambda: self.analysis_btn.setChecked(False))
        bottom_layout.addWidget(self.analysis_collapse_btn)

        self.chart_view = QWebEngineView()
        self.chart_view.setMinimumHeight(450)  # 避免在QScrollArea裡被壓縮到看不出圖表內容
        bottom_layout.addWidget(self.chart_view, stretch=1)

        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)
        # 目前趨勢改成短/中/長三行各自附上判斷依據後內容變多，原本120px只夠顯示1~2行，
        # 拉高到220px讓大部分情況不用捲動就看得到完整的3行趨勢+K棒/型態/量價訊號。
        self.summary_view.setMaximumHeight(220)
        bottom_layout.addWidget(self.summary_view)

    def _build_market_tab(self) -> None:
        """「大盤分析」分頁：跟個股分析共用同一套規則比對渲染邏輯(_build_analysis_html())，
        只是分析對象固定是大盤(TAIEX_STOCK_ID)，不是使用者目前選取的個股。

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
        """
        market_scroll = QScrollArea()
        market_scroll.setWidgetResizable(True)
        self.tabs.addTab(market_scroll, "大盤")

        market_content = QWidget()
        market_scroll.setWidget(market_content)
        market_layout = QVBoxLayout(market_content)

        self.market_chart_view = QWebEngineView()
        # ⚠️ 2026-07-29修正：這裡固定show_macd=True/show_kd=True，build_candlestick_
        # figure()算出來的圖表本身高度是560+140*2=840px(見該函式的height計算)，原本
        # 這裡minimumHeight只給450且沒有stretch因子，QVBoxLayout會把多出來的空間都給
        # 後面的addStretch()而不是圖表，導致圖表被壓縮得比實際內容還小，看起來像「沒有
        # 展開」。改成minimumHeight貼近840的實際圖表高度、並給stretch=1(比照個股圖表
        # chart_view的做法)，讓圖表優先取得可用空間。
        self.market_chart_view.setMinimumHeight(820)
        market_layout.addWidget(self.market_chart_view, stretch=1)
        # 跟self._chart_html_path(個股圖表用)分開的暫存檔案，避免兩個分頁互相覆寫對方的
        # 圖表內容(見__init__裡_chart_html_path的說明：QWebEngineView.setHtml()對內容
        # 大小有隱性限制，兩邊都是寫進暫存檔案再load()開啟)。
        self._market_chart_html_path = Path(tempfile.gettempdir()) / f"tw_stock_market_chart_{id(self)}.html"

        # ⚠️ 2026-07-29修正：使用者反映分析文字不要看起來像獨立的一個「框」——QTextEdit
        # 預設會畫外框(QFrame的StyledPanel樣式)，改成NoFrame讓它視覺上融入頁面，跟上面
        # 的K線圖直接銜接，不是分頁裡另外圈出來的一塊。高度計算(_set_market_analysis_
        # html()裡的setFixedHeight)維持不變——那是為了讓QTextEdit本身能顯示完整內容
        # 不被截斷，不是「限縮」，是精準算出剛好的高度，跟這裡拿掉外框是两件事。
        self.market_analysis_view = QTextEdit()
        self.market_analysis_view.setReadOnly(True)
        self.market_analysis_view.setFrameShape(QFrame.Shape.NoFrame)
        self.market_analysis_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        market_layout.addWidget(self.market_analysis_view)
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
        price_df = chart_data.load_price_history(self.conn, TAIEX_STOCK_ID)
        if not price_df.empty:
            holidays, _holidays_ok = chart_data.load_holidays_for_chart(price_df)
            fig = chart_data.build_candlestick_figure(
                price_df, holidays=holidays, ma_periods=FULL_PERIODS,
                show_macd=True, show_kd=True, show_sar=True,
            )
            html_content = render_chart_html(fig, price_df, stock_label=TAIEX_DISPLAY_NAME)
            self._market_chart_html_path.write_text(html_content, encoding="utf-8")
            self.market_chart_view.load(QUrl.fromLocalFile(str(self._market_chart_html_path)))
        self._set_market_analysis_html(self._build_analysis_html(TAIEX_STOCK_ID, f"大盤分析：{TAIEX_DISPLAY_NAME}"))

    def showEvent(self, event) -> None:
        """視窗第一次顯示時，補打一次目前分頁(預設是分頁0「大盤」)的_on_tab_changed()，
        見__init__()裡self._startup_tab_refreshed的說明——`tabs.currentChanged`訊號
        在建構階段(-1→ 0)不會觸發，「大盤」分頁不像「個股清單」分頁那樣，一定會經過
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
            # 切到「個股清單」分頁時重新整理一次(不管是不是剛從候選清單點過來)，確保
            # 圖表/分析面板都是在分頁真正顯示、有正確layout之後才計算(見_build_stock_
            # detail_tab()的說明)；_rerender_chart()本身有「沒有選取任何股票」的
            # 空狀態防呆，不會因為使用者還沒選過股票就直接切過來而出錯。
            self._rerender_chart()

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
        df, latest_date, is_intraday = chart_data.load_candidates_for_date(self.conn, target_date=target_date)
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
        self.intraday_label.setVisible(is_intraday)
        if latest_date is None:
            self.setWindowTitle("台股每日選股（本機版）— 尚無候選清單")
            return
        self.setWindowTitle(f"台股每日選股（本機版）— {latest_date}，共{len(df)}檔")
        self.candidates_table.setRowCount(len(df))
        for row_idx, row in df.reset_index(drop=True).iterrows():
            pct_change = row["pct_change"]
            pct_text = f"{pct_change:+.2f}" if pd.notna(pct_change) else "-"
            volume = row["volume"]
            volume_text = f"{int(volume):,}" if pd.notna(volume) else "-"
            industry_text = row["industry"] if pd.notna(row["industry"]) else ""
            values = [
                row["stock_id"], row["name"], industry_text, row["signal_name"],
                f"{row['entry_price']:.2f}", f"{row['stop_loss']:.2f}", pct_text, volume_text,
            ]
            for col_idx, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                # 部分欄位內容常常比欄寬長、會被截斷看不到完整內容(尤其訊號欄位同時符合多條
                # 規則時)；設定tooltip讓滑鼠移過去任一儲存格都能懸浮顯示完整文字，不用特別
                # 放寬欄寬。
                item.setToolTip(str(value))
                self.candidates_table.setItem(row_idx, col_idx, item)
        self.candidates_table.resizeRowsToContents()  # 讓多行的訊號欄位撐開列高，完整顯示

    def _on_candidate_selected(self) -> None:
        rows = self.candidates_table.selectionModel().selectedRows()
        if not rows:
            return
        stock_id = self.candidates_table.item(rows[0].row(), 0).text()
        self._current_stock_id = stock_id
        # 記錄來源候選清單日期(目前分頁選取的日期)，供「個股清單」分頁右上角顯示
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
            stock_id_item = self.candidates_table.item(row, 0)
            name_item = self.candidates_table.item(row, 1)
            stock_id = stock_id_item.text() if stock_id_item else ""
            name = name_item.text() if name_item else ""
            if query_lower == stock_id.lower() or query in name:
                self.candidates_table.selectRow(row)
                self.candidates_table.scrollToItem(stock_id_item)
                return
        QMessageBox.information(self, "候選清單搜尋", f"目前候選清單中找不到「{query}」。")

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
        price_df = chart_data.load_price_history(self.conn, self._current_stock_id)
        if price_df.empty:
            self.chart_view.setHtml(f"<p>查無股票代號 {self._current_stock_id} 的價格資料。</p>")
            self.summary_view.setPlainText("")
            if self.analysis_btn.isChecked():
                self._set_analysis_html(f"<p>查無股票代號 {self._current_stock_id} 的價格資料。</p>")
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
        # 取代Plotly預設會跟著滑鼠跑的浮動tooltip，仿TradingView的畫法(desktop/chart_render.py
        # 有完整說明，這個效果只有桌面版能用，Streamlit版沒有對應機制)。include_plotlyjs=True
        # 把plotly.js整包內嵌，桌面版離線也能看圖。寫進暫存檔案再用load()開啟，理由見__init__裡
        # _chart_html_path的註解(setHtml對大內容會靜默失敗)。不傳title給build_candlestick_
        # figure(桌面版改用render_chart_html的stock_label固定列顯示代號+名稱，見那裡的說明)。
        stock_name = chart_data.get_stock_name(self.conn, self._current_stock_id)
        stock_label = f"{self._current_stock_id} {stock_name}" if stock_name else self._current_stock_id
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

        if self.analysis_btn.isChecked():
            self._refresh_analysis_view()

    def _on_analysis_toggled(self, checked: bool) -> None:
        self.analysis_view.setVisible(checked)
        self.analysis_collapse_btn.setVisible(checked)
        if checked:
            self._refresh_analysis_view()

    def _set_analysis_html(self, html_content: str) -> None:
        """設定「個股分析」面板內容，並依實際內容量重新算出剛好的高度(setFixedHeight)，
        取代原本寫死的200px上限——訊號一多就會超過200px，QTextEdit自己的垂直捲軸(已在
        _build_stock_detail_tab()關閉)原本就會被塞爆，變成使用者要在這個小框框裡另外
        捲動一次；改成跟內容一樣高，多出來的部分交給最外層的detail_scroll(整個分頁)
        捲動，只有一層捲軸，不是兩層。「個股清單」分頁用plain QVBoxLayout+QScrollArea
        (不像候選清單/個股圖表過去共用同一分頁時包了一層QSplitter)，一般QVBoxLayout+
        QScrollArea(setWidgetResizable(True))組合本來就能正確隨內容量調整捲軸範圍，
        不需要額外的高度同步workaround(見2026-07-29大盤分析截斷bug的除錯記錄)。
        """
        self.analysis_view.setHtml(html_content)
        doc_height = self.analysis_view.document().size().height()
        frame_width = self.analysis_view.frameWidth() * 2
        self.analysis_view.setFixedHeight(int(doc_height) + frame_width + 8)

    def _build_analysis_html(self, stock_id: str, header_label: str) -> str:
        """組出「規則比對清單+總結分析」的HTML內容，供個股分析(_refresh_analysis_view())
        與大盤分析(_refresh_market_analysis_view())共用同一套渲染邏輯——差別只在於分析
        對象是哪一個stock_id、標題文字，判斷/顯示格式完全一致(大盤本身就是`stock_prices`
        表裡的一筆特殊資料，`load_price_history()`/`analyze_stock_signals()`不需要知道
        它是大盤還是個股，見src/data/yfinance_client.py的fetch_taiex_prices())。
        """
        price_df = chart_data.load_price_history(self.conn, stock_id)
        if price_df.empty:
            return f"<p>查無股票代號 {html.escape(stock_id)} 的價格資料。</p>"
        # trend_df：短/中/長(日/週/月)趨勢分類器要重新取樣出週線/月線，需要比price_df
        # (預設120天顯示窗口)更長的歷史，見chart_data.TREND_LOOKBACK_DAYS的說明。
        trend_df = chart_data.load_price_history(self.conn, stock_id, days=chart_data.TREND_LOOKBACK_DAYS)
        matches = analyze_stock_signals(price_df, trend_df=trend_df)
        header = f"<p><b>{html.escape(header_label)}</b></p>"
        if not matches:
            return header + "<p>目前沒有符合任何已接上規則庫的訊號。</p>"
        # ⚠️ QTextEdit.setHtml()一定會把內容當HTML剖析，rule_scan.py的note文字裡常有
        # "MA5<MA10<MA20"這種原始"<"/">"符號(見rule_scan.py)，不escape的話會被誤判成
        # HTML標籤、內容被吃掉一截(實測"目前狀態：MA5<MA10<MA20..."只會顯示到"MA5"就斷掉)。
        # Streamlit版沒有這個問題是因為st.write/st.caption預設unsafe_allow_html=False，
        # 不會把文字內容當HTML剖析；這裡是QTextEdit本身的行為，只有桌面版需要escape。
        blocks = [header]
        for m in matches:
            block = f"<p><b>{html.escape(m['rule_id'])}　{html.escape(m['title'])}（信心{m['confidence']}%）</b><br>"
            # 「目前狀態」(這條規則今天為什麼觸發)排在規則名稱後第一個位置，跟dashboard/
            # app.py對齊——使用者最想先看到的是「現在是什麼情況」，解讀/原文頁碼是補充
            # 說明。analyze_stock_signals()裡同一個rule_id若對應多筆觸發(例如R-TREND-03
            # 短期/中期各自獨立判斷都是多頭)，note會是用換行接起來的多行文字，這裡逐行
            # 各自加註「目前狀態：」/縮排顯示，不能假設note永遠是單行字串。
            if m.get("note"):
                note_lines = m["note"].split("\n")
                block += f"目前狀態：{html.escape(note_lines[0])}<br>"
                for extra_line in note_lines[1:]:
                    block += f"　　{html.escape(extra_line)}<br>"
            if m["description"]:
                # 「分析：」明確標示這段是「為什麼」的解說(見dashboard/app.py同一處的說明)，
                # 跟上面的「目前狀態：」分開標籤，不是延續文字。
                block += f"分析：{html.escape(m['description'])}<br>"
            if m.get("reference"):
                block += f"<i>原文與頁碼：{html.escape(m['reference'])}</i>"
            block += "</p><hr>"
            blocks.append(block)
        # 「總結分析」放在列完所有規則之後——使用者反映一長串規則清單太雜亂，這裡用
        # daily_screener.summarize_signal_matches()統計出的多頭/空頭傾向數量+信心最高的
        # 規則，讓使用者不用自己從落落長的清單裡歸納重點。
        summary = summarize_signal_matches(matches)
        top = summary["top_match"]
        top_note = (top.get("note") or "").split("\n")[0] if top else ""
        summary_block = (
            "<p><b>📌 總結分析</b><br>"
            f"本次共觸發 {summary['total']} 條規則"
            f"（多頭傾向{summary['bullish']}條、空頭傾向{summary['bearish']}條、"
            f"其他{summary['other']}條 — 依規則標題文字粗略分類，僅供參考）。<br>"
            f"信心最高的訊號：{html.escape(top['rule_id'])}　{html.escape(top['title'])}"
            f"（{top['confidence']}%）"
            + (f"<br>目前狀態：{html.escape(top_note)}" if top_note else "")
            + "</p><hr>"
        )
        blocks.append(summary_block)
        return "".join(blocks)

    def _refresh_analysis_view(self) -> None:
        """填入「個股分析」面板內容：目前這檔股票符合規則庫中哪些訊號(依信心分數高到低)，
        每條附上從ai/zhu-rules/查出的規則說明。跟_rerender_chart各自重新查一次價格資料，
        不共用同一份df——避免兩邊狀態耦合(例如面板開著時切換股票，忘記同步更新)，運算成本
        很低(SQL查詢+5條screen_*規則判斷)，不需要為了省這點重算而增加程式複雜度。
        """
        if self.conn is None or not self._current_stock_id:
            self._set_analysis_html("<p>請先從候選清單點選或查詢一檔股票。</p>")
            return
        stock_name = chart_data.get_stock_name(self.conn, self._current_stock_id)
        stock_label = f"{self._current_stock_id} {stock_name}" if stock_name else self._current_stock_id
        self._set_analysis_html(self._build_analysis_html(self._current_stock_id, f"個股分析：{stock_label}"))

    def _set_market_analysis_html(self, html_content: str) -> None:
        """設定「大盤分析」面板內容並依實際內容量重新算出剛好的高度，跟_set_analysis_html()
        邏輯一致，但不需要呼叫_sync_central_height_to_content()——大盤分析分頁用的是
        plain QVBoxLayout+QScrollArea(見_build_market_tab())，不像候選清單分頁那樣包了
        一層QSplitter，一般QVBoxLayout+QScrollArea(setWidgetResizable(True))組合本來
        就能正確隨內容量調整捲軸範圍。
        """
        self.market_analysis_view.setHtml(html_content)
        doc_height = self.market_analysis_view.document().size().height()
        frame_width = self.market_analysis_view.frameWidth() * 2
        self.market_analysis_view.setFixedHeight(int(doc_height) + frame_width + 8)

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
        # 只在使用者目前真的停留在「大盤」/「個股清單」分頁時才立即整理——分頁還沒被
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

    def _poll_pipeline_status(self) -> None:
        # 如果本視窗自己觸發的PipelineWorker正在跑，狀態列已經由_on_fetch_progress()顯示
        # 更細緻的下載進度(例如「TPEx 500/1980檔」)，這裡就不要每5秒用pipeline_status.json
        # 的籠統「目前正在自動抓取資料…」蓋過去——這個輪詢機制主要是給「排程觸發、桌面版
        # 剛好開著」的情境用的，跟本視窗自己觸發的抓取搶著更新同一個label沒有意義。
        if self._pipeline_worker is not None and self._pipeline_worker.isRunning():
            return
        if self.conn is not None:
            self._check_for_external_candidate_update()
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
        def _fmt(ts: str | None) -> str:
            if not ts:
                return "尚無資料"
            try:
                return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                return ts

        if self.conn is None:
            self.status_label.setText(f"狀態：尚無資料\n下次更新時間：{next_run_label}")
            return
        price_update = _fmt(chart_data.get_latest_update_time(self.conn))
        candidate_update = _fmt(chart_data.get_latest_candidate_update_time(self.conn))
        self.status_label.setText(
            f"股價更新至：{price_update}\n候選清單算至：{candidate_update}\n下次更新時間：{next_run_label}"
        )
