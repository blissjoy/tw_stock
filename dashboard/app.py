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

from src.data.yfinance_client import TAIEX_STOCK_ID  # noqa: E402
from src.indicators.moving_average import FULL_PERIODS  # noqa: E402
from src.patterns import chart_overlays, latest_day_summary  # noqa: E402
from src.presentation import chart_data  # noqa: E402
from src.presentation.chart_data import (  # noqa: E402
    CANDIDATE_FILTER_DEFAULTS,
    CANDIDATE_FILTERS,
    apply_candidate_filters,
    build_candlestick_figure,
    get_latest_candidate_update_time,
    get_latest_update_time,
    list_candidate_dates,
    load_candidates_for_date,
    load_holidays_for_chart,
    load_price_history,
    resolve_stock_id,
)
from src.presentation import pipeline_status  # noqa: E402

TAIEX_DISPLAY_NAME = "台股加權指數"

TAB_MARKET = "大盤"
TAB_SCREENER = "選股"
TAB_STOCK_DETAIL = "個股清單"
TAB_OPTIONS = [TAB_MARKET, TAB_SCREENER, TAB_STOCK_DETAIL]


def _format_month_day(date_str: str) -> str:
    """"YYYY-MM-DD" -> "X月X日"(不補零)，供「個股清單」分頁右上角的來源標籤使用
    (跟桌面版desktop/main_window.py的同名函式對齊)。格式不符預期時原樣回傳，不拋
    例外——來源標籤只是輔助資訊，不應該因為格式問題讓整頁crash。
    """
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    return f"{d.month}月{d.day}日"


def main() -> None:
    import streamlit as st
    from streamlit.errors import StreamlitSecretNotFoundError

    from scripts.daily_pipeline import run_daily_pipeline
    from src.data import storage
    from src.data.connection import get_default_connection
    from src.screener.daily_screener import analyze_stock_signals, run_screen_and_store, summarize_signal_matches

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

    def render_price_chart(stock_id: str, widget_key: str, always_show_analysis: bool = False) -> None:
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
        col1, col2, col3, col4, col5, col6 = st.columns([3, 1, 1, 1, 1, 1])
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
        with col5:
            show_sar = st.checkbox("顯示SAR", value=True, key=f"{widget_key}_sar_checkbox")
        analysis_state_key = f"{widget_key}_show_analysis"
        with col6:
            # always_show_analysis=True(目前只有大盤分析用)時不需要這顆按鈕——大盤只有
            # 一檔、資料量固定，不像候選清單那樣「選了才知道要分析誰」，直接常駐顯示
            # 下面的分析框即可，不用多一次點擊。
            if not always_show_analysis:
                if st.button("📊 個股分析", key=f"{widget_key}_analysis_btn"):
                    st.session_state[analysis_state_key] = not st.session_state.get(analysis_state_key, False)
        selected_trendline_keys = tuple(label_to_key[label] for label in selected_trendline_labels)

        # 短/中/長(日/週/月)趨勢分類器要重新取樣出週線/月線，需要比畫K線圖用的顯示窗口
        # (price_df，預設120天)更長的歷史，見chart_data.TREND_LOOKBACK_DAYS的說明；下面
        # 「個股分析」面板與「最新交易日分析」摘要都要用，這裡只查一次共用。
        trend_df = load_price_history(conn, stock_id, days=chart_data.TREND_LOOKBACK_DAYS)

        def _render_analysis_body() -> None:
            signal_matches = analyze_stock_signals(price_df, trend_df=trend_df)
            if not signal_matches:
                st.write("目前沒有符合任何已接上規則庫的訊號。")
                return
            for m in signal_matches:
                st.markdown(f"**{m['rule_id']}　{m['title']}（信心{m['confidence']}%）**")
                # 「目前狀態」(這條規則今天為什麼觸發)排在規則名稱後第一個位置，
                # 使用者最想先看到的是「現在是什麼情況」，解讀/原文頁碼是補充說明，
                # 排序上應該讓位。analyze_stock_signals()裡同一個rule_id若對應多筆
                # 觸發(例如R-TREND-03短期/中期各自獨立判斷都是多頭)，note會是用
                # 換行接起來的多行文字，這裡逐行各自加註「目前狀態：」/縮排顯示，
                # 不能假設note永遠是單行字串。
                if m.get("note"):
                    note_lines = m["note"].split("\n")
                    st.caption(f"目前狀態：{note_lines[0]}")
                    for extra_line in note_lines[1:]:
                        st.caption(f"　　{extra_line}")
                if m["description"]:
                    # 「分析：」明確標示這段是「為什麼」的解說(ai/zhu-rules/裡每條規則的
                    # 「解讀」欄位)，不是「目前狀態」的延續文字——股市新手最常問的就是
                    # 「為什麼這個狀態下不能／可以進場」，這裡的解讀內容本來就有回答
                    # 這個問題(例如R-INDICATOR-09說明為什麼盤整時KD交叉訊號無效)，
                    # 只是原本沒有標籤、容易被當成普通補充文字略過不看。
                    st.write(f"分析：{m['description']}")
                if m.get("reference"):
                    st.caption(f"原文與頁碼：{m['reference']}")
                st.divider()
            # 「總結分析」放在列完所有規則之後——使用者反映一長串規則清單太雜亂，
            # 這裡用summarize_signal_matches()統計出的多頭/空頭傾向數量+信心最高的
            # 規則，讓使用者不用自己從落落長的清單裡歸納重點。
            summary = summarize_signal_matches(signal_matches)
            top = summary["top_match"]
            top_note = (top.get("note") or "").split("\n")[0] if top else ""
            st.markdown("**📌 總結分析**")
            st.write(
                f"本次共觸發 {summary['total']} 條規則"
                f"（多頭傾向{summary['bullish']}條、空頭傾向{summary['bearish']}條、"
                f"其他{summary['other']}條 — 依規則標題文字粗略分類，僅供參考）。"
            )
            st.write(f"信心最高的訊號：{top['rule_id']}　{top['title']}（{top['confidence']}%）")
            if top_note:
                st.caption(f"目前狀態：{top_note}")

        if always_show_analysis:
            # 大盤分析：直接顯示在K線圖下方，不用st.expander的邊框「盒子」外觀，
            # 也不限制高度，全部展開——大盤只有一檔、位置夠大，不需要像候選清單那樣
            # 收合節省空間。
            st.markdown("### 📊 大盤分析")
            _render_analysis_body()
        elif st.session_state.get(analysis_state_key, False):
            with st.expander("📊 個股分析", expanded=True):
                _render_analysis_body()
                # 面板展開後可能撐得很長(訊號一多)，使用者反映展開後要收合得捲回最上面
                # 重新點一次上面col6的按鈕很麻煩——在展開內容最下方(不管有沒有符合任何
                # 規則都顯示)再放一個「收合」按鈕，點了直接把狀態設回False並rerun，
                # 不用捲動頁面。
                if st.button("🔼 收合個股分析", key=f"{widget_key}_analysis_collapse_btn"):
                    st.session_state[analysis_state_key] = False
                    st.rerun()
        # 預設只顯示離現價最近的支撐/壓力各一條，不是把所有轉折點都疊上去(最多可能到6條、
        # 會把圖擠得很亂)——書中真正有參考意義的本來就是離現價最近的那一層。
        sr_levels = []
        if show_sr:
            all_levels = chart_overlays.compute_support_resistance_levels(price_df)
            sr_levels = chart_overlays.nearest_support_resistance(all_levels, float(price_df["close"].iloc[-1]))

        stock_name = chart_data.get_stock_name(conn, stock_id)
        chart_title = f"{stock_id} {stock_name}" if stock_name else stock_id
        st.plotly_chart(
            build_candlestick_figure(
                price_df, title=chart_title, holidays=holidays, ma_periods=selected_periods,
                trendlines=trendlines, show_trendline_keys=selected_trendline_keys,
                sr_levels=sr_levels, show_support_resistance=show_sr,
                show_macd=show_macd, show_kd=show_kd, show_sar=show_sar,
            ),
            use_container_width=True,
        )
        st.dataframe(price_df.tail(20), use_container_width=True)

        summary = latest_day_summary.summarize_latest_day(price_df, trend_df=trend_df)
        latest_date_label = price_df.index[-1].strftime("%Y-%m-%d")
        st.markdown(f"**📋 最新交易日分析（{latest_date_label}）**")
        # 短/中/長三種天期分開顯示、各自標示判斷依據的K棒週期(見R-INDICATOR-10：做短線看
        # 日線、中期看週線、長期看月線)，不合併成單一「目前趨勢」——三者可能不一致(例如
        # 日線走空、週線仍是多頭)，只看一種天期容易誤判。每個天期都附上「依據」(最近兩個
        # 頭部/底部的實際價格、日期、頭頭高低/底底高低的判讀)，讓使用者能自己核對演算法的
        # 判斷，不是只丟一個「多頭/空頭/盤整」結論字串——改成每行一種天期，不是併成一行，
        # 附上依據後單行會過長不好讀。freshness額外用st.caption另起一行顯示(2026-07-26新增
        # ——轉折點是事後才確認的，trend/reason用的可能是噴出/破底之前的舊轉折點，這一行
        # 明確標註「最近一次確認轉折點的日期」跟「目前是否有還沒被確認的新波段正在進行中」，
        # 讓使用者自己判斷trend/reason的結論夠不夠新鮮，見trend_state.py第四次修正說明)。
        st.write("目前趨勢：")
        for label, (timeframe, trend, reason, *freshness_rest) in summary["trend"].items():
            st.write(f"　- {label}（{timeframe}）：{trend}（依據：{reason}）")
            if freshness_rest:
                st.caption(f"　　{freshness_rest[0]}")
        st.write(f"K棒名稱：{summary['candle_name']}")
        st.write("型態訊號：" + ("、".join(summary["patterns"]) if summary["patterns"] else "無明顯型態"))
        st.write("量價訊號：" + ("、".join(summary["volume_signals"]) if summary["volume_signals"] else "無明顯訊號"))
        st.caption("⚠️ 型態訊號的「高檔/低檔」判斷已接上趨勢位置模組(is_at_high/is_at_low)，但目前只用單一容忍帶門檻，還沒有初升/主升/末升等更細的子階段分類。")

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
            def _fmt(ts: str | None) -> str:
                if not ts:
                    return "尚無資料"
                try:
                    return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    return ts

            # 股價資料跟候選清單是兩件各自獨立更新的東西：股價可能已經更新到今天，但候選
            # 清單是幾分鐘前手動按「立即重新篩選」才重算的，兩個時間點不會永遠一致，混在
            # 一起顯示會讓使用者誤判「候選清單是不是也跟著更新了」，所以分開各顯示一行。
            st.caption(f"股價更新至　{_fmt(get_latest_update_time(conn))}")
            st.caption(f"候選清單算至　{_fmt(get_latest_candidate_update_time(conn))}")

    # 三個分頁：①大盤、②選股(候選清單篩選+清單本身)、③個股清單(個股查詢+K線圖+個股
    # 分析)——原本候選清單跟個股圖表擠在同一個分頁，使用者反映畫面太擁擠，拆開後候選
    # 清單點選任一列會自動切到③並代入該股票資料。
    #
    # ⚠️ st.tabs()本身不支援用程式碼切換「目前使用中」的分頁(Streamlit已知限制，
    # tabs是純前端狀態，session_state管不到)，改用st.radio(horizontal=True)模擬
    # 分頁列，這是Streamlit唯一支援「用程式碼控制分頁」的做法(跟桌面版desktop/
    # main_window.py的self.tabs.setCurrentIndex()對應)。
    #
    # ⚠️ 2026-08-01踩到的坑：不能在radio widget已經instantiate之後、同一輪script
    # 執行裡直接`st.session_state["active_tab"] = ...`——會丟
    # `StreamlitAPIException: cannot be modified after the widget with key
    # active_tab is instantiated`(這裡的radio在最上面就建立了，候選清單點選事件
    # 卻是在後面的分頁內容裡才觸發，時間點必然在widget instantiate之後)。正確做法
    # 是改寫一個「不綁定任何widget」的中介session_state key(_pending_active_tab)，
    # 在radio建立"之前"讀取它、寫回active_tab，候選清單點選時只設定這個中介key再
    # st.rerun()，下一輪script重新執行到這裡時，radio都還沒建立，這時候寫入
    # active_tab才合法。
    if "_pending_active_tab" in st.session_state:
        st.session_state["active_tab"] = st.session_state.pop("_pending_active_tab")
    elif "active_tab" not in st.session_state:
        st.session_state["active_tab"] = TAB_SCREENER
    active_tab = st.radio(
        "分頁", TAB_OPTIONS, key="active_tab", horizontal=True, label_visibility="collapsed",
    )
    st.divider()

    if active_tab == TAB_MARKET:
        # 大盤只有一檔、資料量固定，不像候選清單那樣「選了才知道要分析誰」，切到這個分頁
        # 就直接顯示K線圖(含MACD/KD/SAR)+規則比對清單，不需要按鈕才展開(跟桌面版一致，
        # 見desktop/main_window.py的_refresh_market_tab()說明)。
        st.subheader(f"📈 {TAIEX_DISPLAY_NAME}")
        render_price_chart(TAIEX_STOCK_ID, widget_key="taiex", always_show_analysis=True)

    elif active_tab == TAB_SCREENER:
        # ⚠️ 2026-08-01修正：篩選條件(以下勾選框/下拉/天數輸入)原本每改一個就立刻用
        # apply_candidate_filters()重新查DB+套用篩選——Streamlit本來就是「互動一次
        # 整個腳本重跑一次」的架構，沒辦法避免rerun本身，但rerun時"是否要重新套用
        # 篩選"是可以自己控制的：改成勾選框只更新畫面上的UI狀態，實際套用篩選延後到
        # 按下「套用篩選」按鈕才發生，儲存進session_state["applied_filters"]，同一組
        # 條件不會因為連續調整而重算好幾次(見desktop/main_window.py同一天的對應修正)。
        st.caption("候選清單篩選條件（可複選，日後可在此擴充更多條件），改完後按「套用篩選」才會重新套用")
        filter_cols = st.columns(len(CANDIDATE_FILTERS))
        active_filters = [
            label for col, label in zip(filter_cols, CANDIDATE_FILTERS)
            if col.checkbox(label, value=CANDIDATE_FILTER_DEFAULTS.get(label, False), key=f"filter_{label}")
        ]

        # 「篩選方法：」這一列跟上面「篩選條件」分開放——SAR翻轉/朱家泓技術分析(日後還有
        # 籌碼分析)是「訊號用什麼方法判斷出來的」，跟上面均線多頭排列這種「候選股本身要
        # 滿足的門檻」概念上不同，使用者要求分開一列，視覺上也比較不擁擠(見
        # desktop/main_window.py同一天的對應調整)。
        st.caption("篩選方法：")
        # SAR翻轉篩選：勾選框+多頭/空頭下拉+翻轉天數輸入綁在一起，不是單純的勾選框，因此沒有
        # 放進上面CANDIDATE_FILTERS那組迴圈，改用獨立的sar_flip_option參數傳給
        # apply_candidate_filters(見src/presentation/chart_data.py)。
        sar_col1, sar_col2, sar_col3, zhu_col, apply_col = st.columns([1, 1, 1.3, 1.3, 1])
        sar_flip_enabled = sar_col1.checkbox("SAR 翻轉", value=False, key="filter_sar_flip_enabled")
        sar_flip_direction = sar_col2.selectbox("方向", ["多頭", "空頭"], index=0, key="filter_sar_flip_direction")
        sar_flip_within_days = sar_col3.number_input(
            "天數內翻轉", min_value=1, max_value=60, value=1, step=1, key="filter_sar_flip_within_days"
        )
        sar_flip_option = (
            {"direction": sar_flip_direction, "within_days": int(sar_flip_within_days)}
            if sar_flip_enabled else None
        )

        # 「朱家泓技術分析」勾選框：2026-08-01新增，目前是標示用——候選清單目前100%來自
        # 朱家泓的書(見chart_data._signal_matches_zhu_rulebook())，還沒有其他規則來源，
        # 勾選/不勾選這裡暫時不會改變候選清單內容，等籌碼分析(陳家豐)規則接上候選清單
        # 產生流程後才會真正篩出差異。預設勾選，跟現況(全部都是朱家泓規則)一致。
        zhu_rule_only = zhu_col.checkbox(
            "朱家泓技術分析", value=True, key="filter_zhu_rule_only",
            help="目前候選清單全部來自朱家泓的書，這裡暫為標示用；等籌碼分析規則接上後才會真正篩選",
        )

        if "applied_filters" not in st.session_state:
            st.session_state["applied_filters"] = {"active_filters": [], "sar_flip_option": None, "zhu_rule_only": True}
        with apply_col:
            st.markdown("&nbsp;")  # 對齊上面其他欄位的label高度，讓按鈕跟輸入框大致同一條水平線
            if st.button("套用篩選"):
                st.session_state["applied_filters"] = {
                    "active_filters": active_filters, "sar_flip_option": sar_flip_option,
                    "zhu_rule_only": zhu_rule_only,
                }

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
        applied = st.session_state["applied_filters"]
        candidates_df = apply_candidate_filters(
            conn, candidates_df, applied["active_filters"], sar_flip_option=applied["sar_flip_option"],
            zhu_rule_only=applied.get("zhu_rule_only", True),
        )

        if latest_date is None:
            st.info("目前 Turso 資料庫裡還沒有任何每日選股紀錄，點上方「立即重新篩選」或等 GitHub Actions 排程跑完後就會顯示。")
        else:
            st.subheader(f"候選清單（{latest_date}，共 {len(candidates_df)} 檔）")
            if is_intraday:
                st.markdown("**:red[⚠ 尚未收盤，本頁為盤中即時資料，收盤後數字可能改變]**")
            if candidates_df.empty:
                st.write("這一天沒有符合條件的候選股。")
            else:
                st.caption("點選任一列會自動切換到「個股清單」分頁查看該檔股票的價格走勢")
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
                    # 記錄來源候選清單日期，供「個股清單」分頁右上角顯示「來源：X月X日的
                    # 選股策略」；順便清掉手動查詢欄位殘留的舊文字(不然下面「個股清單」
                    # 分頁重新渲染時，text_input帶著上次查詢的舊文字又會把這裡剛設定的
                    # stock_id蓋掉，見下面TAB_STOCK_DETAIL分支的說明)，再切到該分頁。
                    st.session_state["detail_stock_id"] = selected_stock_id
                    st.session_state["detail_stock_source"] = selected_date or latest_date
                    st.session_state["detail_query_input"] = ""
                    # 不能直接寫st.session_state["active_tab"](radio widget已經在這輪
                    # script執行的更上面instantiate過了)，寫中介key、下一輪script重新
                    # 執行到radio建立"之前"再轉寫進active_tab，見上面的說明。
                    st.session_state["_pending_active_tab"] = TAB_STOCK_DETAIL
                    st.rerun()

    elif active_tab == TAB_STOCK_DETAIL:
        query_col, source_col = st.columns([3, 1])
        with query_col:
            query = st.text_input(
                "輸入股票代號或名稱（例如 2330 或 台積電）", value="", key="detail_query_input",
            )
        if query:
            # 手動查詢：清掉來源標籤(不是從候選清單點過來的)。
            st.session_state["detail_stock_id"] = resolve_stock_id(conn, query) or query.strip()
            st.session_state["detail_stock_source"] = None
        with source_col:
            source_date = st.session_state.get("detail_stock_source")
            if source_date:
                st.caption(f"來源：{_format_month_day(source_date)}的選股策略")

        detail_stock_id = st.session_state.get("detail_stock_id")
        if detail_stock_id:
            render_price_chart(detail_stock_id, widget_key="detail")
        else:
            st.info("請輸入股票代號或名稱查詢，或到「選股」分頁點選候選股票。")


if __name__ == "__main__":
    main()
