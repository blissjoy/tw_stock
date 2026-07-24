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
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from desktop.chart_render import render_chart_html
from src.data import storage
from src.data.connection import get_default_connection
from src.indicators.moving_average import FULL_PERIODS
from src.patterns import chart_overlays, latest_day_summary
from src.presentation import chart_data, pipeline_status
from src.screener.daily_screener import analyze_stock_signals, run_screen_and_store


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
        # QWebEngineView.setHtml()對內容大小有~2MB的隱性限制(Chromium的data: URL限制，超過
        # 會loadFinished(False)、畫面完全空白且不會報錯)——Plotly圖表把plotly.js整包內嵌後
        # 通常有4~5MB，遠超過這個限制。改成寫進暫存檔案再用load(QUrl.fromLocalFile(...))，
        # 檔案大小沒有這個限制。同一個視窗重複使用同一個暫存檔案，不會每次渲染都留下新檔案。
        self._chart_html_path = Path(tempfile.gettempdir()) / f"tw_stock_chart_{id(self)}.html"

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
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("候選清單日期："))
        self.date_combo = QComboBox()
        self.date_combo.currentIndexChanged.connect(self._reload_candidates)
        filter_bar.addWidget(self.date_combo)
        filter_bar.addSpacing(20)
        filter_bar.addWidget(QLabel("篩選條件："))
        self.filter_checkboxes: dict[str, QCheckBox] = {}
        for label in chart_data.CANDIDATE_FILTERS:
            cb = QCheckBox(label)
            cb.stateChanged.connect(self._reload_candidates)
            filter_bar.addWidget(cb)
            self.filter_checkboxes[label] = cb
        filter_bar.addStretch()
        root_layout.addLayout(filter_bar)

        top_bar = QHBoxLayout()
        self.refresh_btn = QPushButton("🔄 立即重新篩選")
        self.refresh_btn.setToolTip("只用資料庫裡目前已有的資料重算候選清單，不重新抓取資料，通常幾秒內完成")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        self.fetch_btn = QPushButton("▶ 手動抓取今日資料")
        self.fetch_btn.setToolTip("抓取當天TWSE/TPEx資料並重新選股，較耗時(TPEx約需1小時內)，在背景執行不會卡住畫面")
        self.fetch_btn.clicked.connect(self._on_fetch_clicked)
        self.status_label = QLabel("狀態：閒置")
        top_bar.addWidget(self.refresh_btn)
        top_bar.addWidget(self.fetch_btn)
        top_bar.addStretch()
        top_bar.addWidget(self.status_label)
        root_layout.addLayout(top_bar)

        self.intraday_label = QLabel("⚠ 尚未收盤，本頁為盤中即時資料，收盤後數字可能改變")
        self.intraday_label.setStyleSheet("color: red; font-weight: bold;")
        self.intraday_label.setVisible(False)
        root_layout.addWidget(self.intraday_label)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Vertical)
        root_layout.addWidget(splitter)

        self.candidates_table = QTableWidget()
        self.candidates_table.setColumnCount(8)
        self.candidates_table.setHorizontalHeaderLabels(["股票代號", "名稱", "產業別", "訊號(信心%)", "進場價", "停損價", "漲跌幅(%)", "成交量"])
        self.candidates_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.candidates_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.candidates_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # 同一檔股票符合多條規則時，訊號欄位的內容用「\n」分隔多行(見
        # src/presentation/chart_data.py的load_candidates_for_date())；開word wrap
        # 讓Qt正確把每個\n斷行顯示，而不是被裁掉或擠在一行，_reload_candidates()填完
        # 資料後還要呼叫resizeRowsToContents()讓列高跟著撐開，不然多行內容會被壓在
        # 原本單行的列高裡看不全。
        self.candidates_table.setWordWrap(True)
        self.candidates_table.itemSelectionChanged.connect(self._on_candidate_selected)
        splitter.addWidget(self.candidates_table)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("個股查詢："))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("輸入股票代號或名稱（例如 2330 或 台積電）")
        self.search_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self.search_input)
        search_btn = QPushButton("查詢")
        search_btn.clicked.connect(self._on_search)
        search_row.addWidget(search_btn)
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
        self.analysis_view = QTextEdit()
        self.analysis_view.setReadOnly(True)
        self.analysis_view.setMaximumHeight(200)
        self.analysis_view.setVisible(False)
        bottom_layout.addWidget(self.analysis_view)

        self.chart_view = QWebEngineView()
        bottom_layout.addWidget(self.chart_view, stretch=1)

        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)
        self.summary_view.setMaximumHeight(120)
        bottom_layout.addWidget(self.summary_view)

        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

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
        df = chart_data.apply_candidate_filters(self.conn, df, active_filters)
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
        self._rerender_chart()

    def _on_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            return
        resolved = chart_data.resolve_stock_id(self.conn, query) if self.conn is not None else None
        self._current_stock_id = resolved or query
        self._rerender_chart()

    def _rerender_chart(self) -> None:
        if self.conn is None or not self._current_stock_id:
            return
        price_df = chart_data.load_price_history(self.conn, self._current_stock_id)
        if price_df.empty:
            self.chart_view.setHtml(f"<p>查無股票代號 {self._current_stock_id} 的價格資料。</p>")
            self.summary_view.setPlainText("")
            if self.analysis_btn.isChecked():
                self.analysis_view.setHtml(f"<p>查無股票代號 {self._current_stock_id} 的價格資料。</p>")
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
            price_df, title=self._current_stock_id, holidays=holidays, ma_periods=selected_periods,
            trendlines=trendlines, show_trendline_keys=selected_trendline_keys,
            sr_levels=sr_levels, show_support_resistance=show_sr,
            show_macd=self.macd_checkbox.isChecked(), show_kd=self.kd_checkbox.isChecked(),
        )
        # render_chart_html()疊加滑鼠十字線(貫穿價格/成交量兩個子圖)+左上角動態資訊框，
        # 取代Plotly預設會跟著滑鼠跑的浮動tooltip，仿TradingView的畫法(desktop/chart_render.py
        # 有完整說明，這個效果只有桌面版能用，Streamlit版沒有對應機制)。include_plotlyjs=True
        # 把plotly.js整包內嵌，桌面版離線也能看圖。寫進暫存檔案再用load()開啟，理由見__init__裡
        # _chart_html_path的註解(setHtml對大內容會靜默失敗)。
        html = render_chart_html(fig, price_df)
        self._chart_html_path.write_text(html, encoding="utf-8")
        self.chart_view.load(QUrl.fromLocalFile(str(self._chart_html_path)))

        summary = latest_day_summary.summarize_latest_day(price_df)
        latest_date_label = price_df.index[-1].strftime("%Y-%m-%d")
        # 短/中/長三種天期分開顯示、各自標示判斷依據的均線天期(見R-TREND-01：轉折波取點
        # 演算法5/10/20日短中長線)，不合併成單一「目前趨勢」——三者可能不一致(例如短線
        # 走空、長線仍是多頭)，只看一種天期容易誤判。
        trend_text = "　".join(f"{label}(MA{n})：{trend}" for label, (n, trend) in summary["trend"].items())
        lines = [
            f"最新交易日分析（{latest_date_label}）",
            f"目前趨勢：{trend_text}",
            f"K棒名稱：{summary['candle_name']}",
            "型態訊號：" + ("、".join(summary["patterns"]) if summary["patterns"] else "無明顯型態"),
            "量價訊號：" + ("、".join(summary["volume_signals"]) if summary["volume_signals"] else "無明顯訊號"),
            "⚠️ 型態訊號僅判斷幾何條件是否成立，尚未確認是否位於真正的高檔/低檔位置。",
        ]
        if not holidays_ok:
            lines.append("⚠️ 假日清單暫時無法取得，圖表可能仍有國定假日空白。")
        self.summary_view.setPlainText("\n".join(lines))

        if self.analysis_btn.isChecked():
            self._refresh_analysis_view()

    def _on_analysis_toggled(self, checked: bool) -> None:
        self.analysis_view.setVisible(checked)
        if checked:
            self._refresh_analysis_view()

    def _refresh_analysis_view(self) -> None:
        """填入「個股分析」面板內容：目前這檔股票符合規則庫中哪些訊號(依信心分數高到低)，
        每條附上從ai/zhu-rules/查出的規則說明。跟_rerender_chart各自重新查一次價格資料，
        不共用同一份df——避免兩邊狀態耦合(例如面板開著時切換股票，忘記同步更新)，運算成本
        很低(SQL查詢+5條screen_*規則判斷)，不需要為了省這點重算而增加程式複雜度。
        """
        if self.conn is None or not self._current_stock_id:
            self.analysis_view.setHtml("<p>請先從候選清單點選或查詢一檔股票。</p>")
            return
        price_df = chart_data.load_price_history(self.conn, self._current_stock_id)
        if price_df.empty:
            self.analysis_view.setHtml(f"<p>查無股票代號 {self._current_stock_id} 的價格資料。</p>")
            return
        matches = analyze_stock_signals(price_df)
        if not matches:
            self.analysis_view.setHtml("<p>目前沒有符合任何已接上規則庫的訊號。</p>")
            return
        # ⚠️ QTextEdit.setHtml()一定會把內容當HTML剖析，rule_scan.py的note文字裡常有
        # "MA5<MA10<MA20"這種原始"<"/">"符號(見rule_scan.py)，不escape的話會被誤判成
        # HTML標籤、內容被吃掉一截(實測"目前狀態：MA5<MA10<MA20..."只會顯示到"MA5"就斷掉)。
        # Streamlit版沒有這個問題是因為st.write/st.caption預設unsafe_allow_html=False，
        # 不會把文字內容當HTML剖析；這裡是QTextEdit本身的行為，只有桌面版需要escape。
        blocks = []
        for m in matches:
            block = f"<p><b>{html.escape(m['rule_id'])}　{html.escape(m['title'])}（信心{m['confidence']}%）</b><br>"
            if m["description"]:
                block += f"{html.escape(m['description'])}<br>"
            if m.get("reference"):
                block += f"<i>原文與頁碼：{html.escape(m['reference'])}</i><br>"
            if m.get("note"):
                block += f"目前狀態：{html.escape(m['note'])}"
            block += "</p><hr>"
            blocks.append(block)
        self.analysis_view.setHtml("".join(blocks))

    # ------------------------------------------------------------------
    # 按鈕
    # ------------------------------------------------------------------

    def _on_refresh_clicked(self) -> None:
        if self.conn is None:
            return
        run_screen_and_store(self.conn)
        self._refresh_date_list()
        self._reload_candidates()
        if self._current_stock_id:
            self._rerender_chart()

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
        QMessageBox.information(self, "完成", f"今日資料抓取完成，候選清單共{candidate_count}檔。")

    def _on_fetch_failed(self, message: str) -> None:
        self.fetch_btn.setEnabled(True)
        QMessageBox.warning(self, "失敗", f"抓取失敗：{message}")

    # ------------------------------------------------------------------
    # 狀態列（跟排程觸發的run_daily_pipeline()共用同一份pipeline_status.json）
    # ------------------------------------------------------------------

    def _poll_pipeline_status(self) -> None:
        # 如果本視窗自己觸發的PipelineWorker正在跑，狀態列已經由_on_fetch_progress()顯示
        # 更細緻的下載進度(例如「TPEx 500/1980檔」)，這裡就不要每5秒用pipeline_status.json
        # 的籠統「目前正在自動抓取資料…」蓋過去——這個輪詢機制主要是給「排程觸發、桌面版
        # 剛好開著」的情境用的，跟本視窗自己觸發的抓取搶著更新同一個label沒有意義。
        if self._pipeline_worker is not None and self._pipeline_worker.isRunning():
            return
        status = pipeline_status.read_status()
        state = status.get("status") if status else None
        if state == "running":
            # 排程觸發(Windows工作排程器)剛好在桌面版開著的時候跑，這裡是唯一會顯示
            # 「更新中」的路徑；本視窗自己按按鈕觸發的情況已經被上面的guard擋掉，改由
            # _on_fetch_progress()顯示更細緻的下載進度。
            date_label = status.get("date", "")
            self.status_label.setText(f"🔄 更新中...（{date_label}）")
            return
        if state == "failed":
            date_label = status.get("date", "")
            self.status_label.setText(f"⚠ 上次抓取失敗（{date_label}）")
            return

        # 閒置狀態：顯示DB裡目前最新一次成功寫入股價的時間戳，比pipeline_status.json的
        # 「date」欄位更精確(date只到日期、不含時分，看不出來是幾點抓的)。
        latest_update = chart_data.get_latest_update_time(self.conn) if self.conn is not None else None
        if latest_update:
            try:
                formatted = datetime.fromisoformat(latest_update).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                formatted = latest_update
            self.status_label.setText(f"資料更新至：{formatted}")
        else:
            self.status_label.setText("狀態：尚無資料")
