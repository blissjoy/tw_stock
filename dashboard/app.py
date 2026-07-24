"""Streamlit 儀表板：顯示每日選股結果（daily_candidates 表，預設最新一天、可用下拉選單
切換查看歷史候選清單，可點選清單中任一列直接看該檔股票的價格走勢），也可以手動輸入
股票代號或名稱查詢任意股票。

「🔄 立即重新篩選」按鈕呼叫 src/screener/daily_screener.run_screen_and_store()，只用
資料庫裡『目前已有』的資料重算訊號，不會對外重新抓取TWSE/TPEx資料（那個很慢，交給
scripts/daily_pipeline.py 的每日排程做），所以按下去通常幾秒內就有結果。

部署：Streamlit Community Cloud，在其後台 Secrets 設定與 GitHub Actions 同一組
TURSO_DATABASE_URL / TURSO_AUTH_TOKEN（st.secrets 在這裡先搬進 os.environ，讓
src/data/config.py 既有的讀取邏輯不必為了 Streamlit 另外寫一套）。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.indicators.moving_average import FULL_PERIODS  # noqa: E402
from src.patterns import chart_overlays, latest_day_summary  # noqa: E402
from src.presentation import chart_data  # noqa: E402
from src.presentation.chart_data import (  # noqa: E402
    CANDIDATE_FILTERS,
    apply_candidate_filters,
    build_candlestick_figure,
    get_latest_update_time,
    list_candidate_dates,
    load_candidates_for_date,
    load_holidays_for_chart,
    load_price_history,
    resolve_stock_id,
)
from src.presentation import pipeline_status  # noqa: E402


def main() -> None:
    import streamlit as st
    from streamlit.errors import StreamlitSecretNotFoundError

    from scripts.daily_pipeline import run_daily_pipeline
    from src.data import storage
    from src.data.connection import get_default_connection
    from src.screener.daily_screener import analyze_stock_signals, run_screen_and_store

    try:
        for key, value in st.secrets.items():
            os.environ.setdefault(key, str(value))
    except StreamlitSecretNotFoundError:
        # 本機開發(尤其搭配 LOCAL_DB_PATH 不連Turso時)通常沒有 secrets.toml，
        # st.secrets 存取本身就會丟例外(不是回傳空dict)，直接略過即可。
        pass

    st.set_page_config(page_title="台股每日選股", page_icon="📈", layout="wide")

    @st.cache_resource
    def get_conn():
        # get_default_connection()依LOCAL_DB_PATH環境變數決定開本機sqlite還是連線Turso
        # （Streamlit/PySide6兩個前端共用同一套判斷邏輯，見src/data/connection.py）。
        # 本機sqlite分支內部已經呼叫過ensure_schema()、一定成功；Turso分支則刻意不在這裡
        # 呼叫，因為Turso可能因為額度用完等原因寫入被封鎖(見src/data/turso_client.py的
        # 說明)，這裡自行try/except，失敗時只顯示警告、不讓整個儀表板crash掉——既有資料表
        # 通常早就建好了，讀取功能不該被「寫入被封鎖」這種跟讀取無關的問題波及。
        conn = get_default_connection()
        if not os.environ.get("LOCAL_DB_PATH"):
            try:
                storage.ensure_schema(conn)
            except Exception as exc:  # noqa: BLE001
                st.warning(f"⚠️ 無法確認資料庫schema已建立（{exc}），若資料表原本就存在應不影響讀取。")
        return conn

    conn = get_conn()

    def render_price_chart(stock_id: str, widget_key: str) -> None:
        price_df = load_price_history(conn, stock_id)
        if price_df.empty:
            st.warning(f"查無股票代號 {stock_id} 的價格資料。")
            return

        holidays, holidays_ok = load_holidays_for_chart(price_df)
        if not holidays_ok:
            st.caption("⚠️ 假日清單暫時無法取得，圖表可能仍有國定假日空白。")

        ma_options = [f"MA{n}" for n in FULL_PERIODS]
        selected_ma_labels = st.multiselect("顯示均線", ma_options, default=ma_options, key=f"{widget_key}_ma_select")
        selected_periods = tuple(int(label[2:]) for label in selected_ma_labels)

        trendlines = chart_overlays.compute_trendlines(price_df)
        trendline_options = [chart_data.TRENDLINE_LABELS[key] for key in chart_data.TRENDLINE_LABELS if key in trendlines]
        label_to_key = {v: k for k, v in chart_data.TRENDLINE_LABELS.items()}
        col1, col2, col3, col4, col5 = st.columns([3, 1, 1, 1, 1])
        with col1:
            if trendline_options:
                selected_trendline_labels = st.multiselect(
                    "顯示切線／軌道線", trendline_options, default=trendline_options, key=f"{widget_key}_trendline_select",
                )
            else:
                selected_trendline_labels = []
                st.caption("目前資料範圍內沒有找到符合「線不蓋線」條件的切線。")
        with col2:
            show_sr = st.checkbox("顯示支撐壓力", value=True, key=f"{widget_key}_sr_checkbox")
        with col3:
            show_macd = st.checkbox("顯示MACD", value=True, key=f"{widget_key}_macd_checkbox")
        with col4:
            show_kd = st.checkbox("顯示KD", value=True, key=f"{widget_key}_kd_checkbox")
        analysis_state_key = f"{widget_key}_show_analysis"
        with col5:
            if st.button("📊 個股分析", key=f"{widget_key}_analysis_btn"):
                st.session_state[analysis_state_key] = not st.session_state.get(analysis_state_key, False)
        selected_trendline_keys = tuple(label_to_key[label] for label in selected_trendline_labels)

        # 短/中/長(日/週/月)趨勢分類器要重新取樣出週線/月線，需要比畫K線圖用的顯示窗口
        # (price_df，預設120天)更長的歷史，見chart_data.TREND_LOOKBACK_DAYS的說明；下面
        # 「個股分析」面板與「最新交易日分析」摘要都要用，這裡只查一次共用。
        trend_df = load_price_history(conn, stock_id, days=chart_data.TREND_LOOKBACK_DAYS)

        if st.session_state.get(analysis_state_key, False):
            with st.expander("📊 個股分析", expanded=True):
                signal_matches = analyze_stock_signals(price_df, trend_df=trend_df)
                if not signal_matches:
                    st.write("目前沒有符合任何已接上規則庫的訊號。")
                else:
                    for m in signal_matches:
                        st.markdown(f"**{m['rule_id']}　{m['title']}（信心{m['confidence']}%）**")
                        if m["description"]:
                            st.write(m["description"])
                        if m.get("reference"):
                            st.caption(f"原文與頁碼：{m['reference']}")
                        if m.get("note"):
                            st.caption(f"目前狀態：{m['note']}")
                        st.divider()
        # 預設只顯示離現價最近的支撐/壓力各一條，不是把所有轉折點都疊上去(最多可能到6條、
        # 會把圖擠得很亂)——書中真正有參考意義的本來就是離現價最近的那一層。
        sr_levels = []
        if show_sr:
            all_levels = chart_overlays.compute_support_resistance_levels(price_df)
            sr_levels = chart_overlays.nearest_support_resistance(all_levels, float(price_df["close"].iloc[-1]))

        st.plotly_chart(
            build_candlestick_figure(
                price_df, holidays=holidays, ma_periods=selected_periods,
                trendlines=trendlines, show_trendline_keys=selected_trendline_keys,
                sr_levels=sr_levels, show_support_resistance=show_sr,
                show_macd=show_macd, show_kd=show_kd,
            ),
            use_container_width=True,
        )
        st.dataframe(price_df.tail(20), use_container_width=True)

        summary = latest_day_summary.summarize_latest_day(price_df, trend_df=trend_df)
        latest_date_label = price_df.index[-1].strftime("%Y-%m-%d")
        st.markdown(f"**📋 最新交易日分析（{latest_date_label}）**")
        # 短/中/長三種天期分開顯示、各自標示判斷依據的K棒週期(見R-INDICATOR-10：做短線看
        # 日線、中期看週線、長期看月線)，不合併成單一「目前趨勢」——三者可能不一致(例如
        # 日線走空、週線仍是多頭)，只看一種天期容易誤判。
        trend_text = "　".join(
            f"{label}({timeframe})：{trend}" for label, (timeframe, trend) in summary["trend"].items()
        )
        st.write(f"目前趨勢：{trend_text}")
        st.write(f"K棒名稱：{summary['candle_name']}")
        st.write("型態訊號：" + ("、".join(summary["patterns"]) if summary["patterns"] else "無明顯型態"))
        st.write("量價訊號：" + ("、".join(summary["volume_signals"]) if summary["volume_signals"] else "無明顯訊號"))
        st.caption("⚠️ 型態訊號僅判斷幾何條件是否成立，尚未確認是否位於真正的高檔/低檔位置（趨勢位置模組尚未實作）。")

    title_col, status_col = st.columns([4, 1])
    with title_col:
        st.title("📈 台股每日選股")
        st.caption("資料來源：TWSE / TPEx(透過FinMind) — 盤中每小時自動更新，收盤後取得最終數字")
    with status_col:
        status = pipeline_status.read_status() or {}
        if status.get("status") == "running" and pipeline_status.is_stale(status):
            # process被強制中止(kill/當機/斷電)時，Python的except/finally完全沒機會執行，
            # 狀態檔案會永久停在最後一次心跳的"running"——is_stale()判斷太久沒更新，這裡
            # 不能再顯示「更新中」誤導使用者，要明確標示可能已經中斷。
            st.markdown("**:red[⚠ 上次自動更新可能已中斷，請重新手動抓取]**")
        elif status.get("status") == "running":
            stage, progress = status.get("stage"), status.get("progress")
            detail = f"　{stage} {progress}檔" if stage and progress else ""
            st.markdown(f"**:orange[🔄 更新中...{detail}]**")
        else:
            latest_update = get_latest_update_time(conn)
            if latest_update:
                try:
                    formatted = datetime.fromisoformat(latest_update).strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    formatted = latest_update
                st.caption(f"資料更新至\n{formatted}")
            else:
                st.caption("尚無資料")

    st.caption("候選清單篩選條件（可複選，日後可在此擴充更多條件）")
    filter_cols = st.columns(len(CANDIDATE_FILTERS))
    active_filters = [
        label for col, label in zip(filter_cols, CANDIDATE_FILTERS)
        if col.checkbox(label, key=f"filter_{label}")
    ]

    button_col1, button_col2 = st.columns([1, 1])
    with button_col1:
        if st.button("🔄 立即重新篩選"):
            # 只用資料庫裡目前已有的資料重算訊號，不重新對外抓取TWSE/TPEx資料(那個很慢，
            # 交給下面的手動抓取按鈕或排程做)，所以這個按鈕通常幾秒內就能算完，可以隨時按
            # 而不用擔心額度或等待。
            with st.spinner("正在用目前資料庫裡的最新資料重新計算選股訊號..."):
                run_screen_and_store(conn)
            st.success("已重新計算完成，候選清單已更新。")
    with button_col2:
        if st.button("▶ 手動抓取今日資料"):
            # 跟桌面版「▶ 手動抓取今日資料」按鈕呼叫同一份run_daily_pipeline()，行為一致
            # (含TWSE官方端點優先、收盤前查無資料時退回yfinance盤中即時價備援)。Streamlit
            # 沒有背景執行緒機制，這裡是同步阻塞呼叫，按下去要等整個抓取跑完(TWSE+TPEx合計
            # 實測約1分鐘內)才會回應，用進度條讓使用者知道還在跑、跑到哪裡，不是卡住。
            progress_bar = st.progress(0.0, text="準備開始...")

            def _on_progress(stage: str, done: int, total: int) -> None:
                progress_bar.progress(done / total if total else 0.0, text=f"{stage} 下載進度：{done}/{total}檔")

            with st.spinner("正在抓取TWSE/TPEx今日資料並重新選股..."):
                candidates = run_daily_pipeline(conn, dry_run=False, on_progress=_on_progress)
            progress_bar.empty()
            st.success(f"抓取完成，候選清單共{len(candidates)}檔。")
            st.rerun()

    candidate_dates = list_candidate_dates(conn)
    selected_date = (
        st.selectbox("候選清單日期", candidate_dates, index=0, key="candidate_date_select")
        if candidate_dates else None
    )
    candidates_df, latest_date, is_intraday = load_candidates_for_date(conn, target_date=selected_date)
    candidates_df = apply_candidate_filters(conn, candidates_df, active_filters)

    selected_stock_id = None
    if latest_date is None:
        st.info("目前 Turso 資料庫裡還沒有任何每日選股紀錄，點上方「立即重新篩選」或等 GitHub Actions 排程跑完後就會顯示。")
    else:
        st.subheader(f"候選清單（{latest_date}，共 {len(candidates_df)} 檔）")
        if is_intraday:
            st.markdown("**:red[⚠ 尚未收盤，本頁為盤中即時資料，收盤後數字可能改變]**")
        if candidates_df.empty:
            st.write("這一天沒有符合條件的候選股。")
        else:
            st.caption("點選任一列可在下方查看該檔股票的價格走勢")
            event = st.dataframe(
                candidates_df, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row", key="candidates_table",
                column_config={
                    "stock_id": "股票代號", "name": "名稱", "industry": "產業別",
                    "signal_name": "訊號(信心%)",  # 信心分數已經內含在signal_name字串裡(見daily_screener.py)，這裡只是把「(信心%)」這個提示放進欄位標題，不用每一列都重複寫「信心」兩個字
                    "entry_price": "進場價", "stop_loss": "停損價",
                    "pct_change": st.column_config.NumberColumn("漲跌幅(%)", format="%.2f%%"),
                    "volume": st.column_config.NumberColumn("成交量", format="%d"),
                },
            )
            if event.selection.rows:
                selected_stock_id = str(candidates_df.iloc[event.selection.rows[0]]["stock_id"])

    st.divider()

    if selected_stock_id:
        st.subheader(f"📊 {selected_stock_id} 價格走勢（點選自候選清單）")
        render_price_chart(selected_stock_id, widget_key="drilldown")
        st.divider()

    st.subheader("個股價格走勢查詢（輸入股票代號或名稱）")
    query = st.text_input("輸入股票代號或名稱（例如 2330 或 台積電）", value="")
    if query:
        stock_id = resolve_stock_id(conn, query) or query.strip()
        render_price_chart(stock_id, widget_key="manual")


if __name__ == "__main__":
    main()
