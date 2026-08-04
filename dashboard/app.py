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
from src.presentation import chart_data, portfolio_data, stock_detail_data  # noqa: E402
from src.presentation.chart_data import (  # noqa: E402
    CANDIDATE_FILTER_DEFAULTS,
    CANDIDATE_FILTERS,
    apply_candidate_filters,
    build_candlestick_figure,
    get_latest_candidate_update_time,
    get_latest_update_time,
    list_candidate_dates,
    list_industries,
    list_price_dates,
    load_industry_rotation,
    load_stock_universe_for_date,
    load_holidays_for_chart,
    load_price_history,
    resolve_stock_id,
)
from src.presentation import pipeline_status  # noqa: E402
from src.presentation.chart_render import render_chart_html  # noqa: E402

# 「市場」篩選下拉對應load_stock_universe_for_date()的market參數("TWSE"/"TPEx"/None)，
# 照抄桌面版desktop/main_window.py的_MARKET_FILTER_VALUES；"全部"不在對照表裡，
# get()查不到就維持None(不限制)。
_MARKET_FILTER_VALUES = {"上市": "TWSE", "上櫃": "TPEx"}

TAIEX_DISPLAY_NAME = "台股加權指數"

TAB_MARKET = "大盤"
TAB_SCREENER = "選股"
TAB_STOCK_DETAIL = "個股資訊"
TAB_INDUSTRY_ROTATION = "產業輪動"
TAB_OPTIONS = [TAB_MARKET, TAB_SCREENER, TAB_STOCK_DETAIL, TAB_INDUSTRY_ROTATION]


def _format_month_day(date_str: str) -> str:
    """"YYYY-MM-DD" -> "X月X日"(不補零)，供「個股資訊」分頁右上角的來源標籤使用
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

        # 2026-08-04起改成「技術面」/「籌碼面」兩個可收合區塊(照抄desktop/main_window.py
        # 的_build_analysis_sections_html()/_render_rule_match_blocks())：上方先有
        # 📌總結分析列出兩區塊各自的一句話摘要+跳轉連結，下方兩個st.expander各自展開
        # (預設展開，跟桌面版_CollapsibleBox預設展開一致)顯示完整規則清單，區塊結尾
        # 附「🔼回頂部」連結。跳轉/回頂部改用瀏覽器原生錨點連結(<a href="#id">+
        # <div id="id">)，不是桌面版那套`jumpto:///`+anchorClicked的Qt專屬機制——
        # Streamlit沒有對應的訊號攔截架構，錨點連結是web原生就有、不需要額外JS的做法。
        # 籌碼面資料來自stock_detail_data.analyze_chip_signals()，大盤(^TWII)沒有
        # 法人籌碼資料，讓它自然回傳空list、顯示「目前沒有符合任何已接上的籌碼規則」，
        # 跟桌面版一致，不是bug。
        _EMPTY_TEASER = {"tech": "目前沒有符合任何已接上規則庫的訊號。", "chip": "目前沒有符合任何已接上的籌碼規則。"}

        def _render_rule_matches(matches: list[dict]) -> None:
            if not matches:
                st.write(_EMPTY_TEASER["tech"])
                return
            for m in matches:
                st.markdown(f"**{m['rule_id']}　{m['title']}（信心{m['confidence']}%）**")
                # 「目前狀態」(這條規則今天為什麼觸發)排在規則名稱後第一個位置，
                # 使用者最想先看到的是「現在是什麼情況」，解讀/原文頁碼是補充說明，
                # 排序上應該讓位。同一個rule_id若對應多筆觸發(例如R-TREND-03短期/
                # 中期各自獨立判斷都是多頭)，note會是用換行接起來的多行文字，這裡
                # 逐行各自加註「目前狀態：」/縮排顯示，不能假設note永遠是單行字串。
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

        def _section_teaser(matches: list[dict], anchor: str) -> str:
            if not matches:
                return _EMPTY_TEASER[anchor]
            summary = summarize_signal_matches(matches)
            top = summary["top_match"]
            top_note = (top.get("note") or "").split("\n")[0] if top else ""
            text = (
                f"本次共觸發 {summary['total']} 條規則"
                f"（多頭傾向{summary['bullish']}條、空頭傾向{summary['bearish']}條、"
                f"其他{summary['other']}條 — 依規則標題文字粗略分類，僅供參考）。  \n"
                f"信心最高的訊號：{top['rule_id']}　{top['title']}（{top['confidence']}%）"
            )
            if top_note:
                text += f"  \n目前狀態：{top_note}"
            return text

        def _render_analysis_panel() -> None:
            tech_matches = analyze_stock_signals(price_df, trend_df=trend_df)
            chip_matches = stock_detail_data.analyze_chip_signals(conn, stock_id)
            top_anchor, tech_anchor, chip_anchor = f"{widget_key}-analysis-top", f"{widget_key}-tech-section", f"{widget_key}-chip-section"

            st.markdown(f'<div id="{top_anchor}"></div>', unsafe_allow_html=True)
            st.markdown("**📌 總結分析**")
            st.markdown(f"**1. 技術面**  \n{_section_teaser(tech_matches, 'tech')}")
            st.markdown(f'<a href="#{tech_anchor}">查看技術面 ↓</a>', unsafe_allow_html=True)
            st.markdown(f"**2. 籌碼面**  \n{_section_teaser(chip_matches, 'chip')}")
            st.markdown(f'<a href="#{chip_anchor}">查看籌碼面 ↓</a>', unsafe_allow_html=True)

            st.markdown(f'<div id="{tech_anchor}"></div>', unsafe_allow_html=True)
            with st.expander("技術面", expanded=True):
                _render_rule_matches(tech_matches)
            st.markdown(f'<a href="#{top_anchor}">🔼 回頂部</a>', unsafe_allow_html=True)

            st.markdown(f'<div id="{chip_anchor}"></div>', unsafe_allow_html=True)
            with st.expander("籌碼面", expanded=True):
                _render_rule_matches(chip_matches)
            st.markdown(f'<a href="#{top_anchor}">🔼 回頂部</a>', unsafe_allow_html=True)

        if always_show_analysis:
            # 大盤分析：直接顯示在K線圖下方，不用外層st.expander的邊框「盒子」外觀，
            # 也不限制高度，全部展開——大盤只有一檔、位置夠大，不需要像候選清單那樣
            # 收合節省空間(內層技術面/籌碼面兩個小區塊還是各自可收合)。
            st.markdown("### 📊 大盤分析")
            _render_analysis_panel()
        elif st.session_state.get(analysis_state_key, False):
            with st.expander("📊 個股分析", expanded=True):
                _render_analysis_panel()
                # 面板展開後可能撐得很長(訊號一多)，使用者反映展開後要收合得捲回最上面
                # 重新點一次上面col6的按鈕很麻煩——在展開內容最下方再放一個「收合」按鈕，
                # 點了直接把狀態設回False並rerun，不用捲動頁面。
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
        # 十字準星：2026-08-04起改用render_chart_html()+st.components.v1.html()(iframe
        # 執行原始HTML+JS)取代st.plotly_chart()，才能疊加貫穿價格/成交量/MACD/KD子圖的
        # 十字線＋左上角動態資訊框，跟桌面版效果一致，見src/presentation/chart_render.py。
        # 不傳title給build_candlestick_figure(改用render_chart_html的stock_label固定列
        # 顯示代號+名稱，見該模組docstring)。
        fig = build_candlestick_figure(
            price_df, holidays=holidays, ma_periods=selected_periods,
            trendlines=trendlines, show_trendline_keys=selected_trendline_keys,
            sr_levels=sr_levels, show_support_resistance=show_sr,
            show_macd=show_macd, show_kd=show_kd, show_sar=show_sar,
        )
        html_str = render_chart_html(fig, price_df, stock_label=chart_title, div_id=f"tw-stock-chart-{widget_key}")
        # +40px留給桌面版同款的固定資訊框/軸數值標籤疊加空間(見chart_render.py)，避免
        # iframe高度剛好卡住把最上面幾列文字裁掉。
        st.components.v1.html(html_str, height=int(fig.layout.height or 700) + 40, scrolling=False)
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
            # 2026-08-04新增：`.github/workflows/daily_pipeline.yml`的排程自2026-07-23起
            # 被註解暫停(Turso帳號寫入額度用完，架構改成本機優先運作)，只留workflow_dispatch
            # 可以手動觸發——web版現在其實沒有任何自動更新機制，不能沿用桌面版「下次更新
            # 時間」那套邏輯(那是讀Windows工作排程器寫死的時間表，跟這裡的情境不同)，改成
            # 明確提醒使用者目前是純手動更新，避免誤以為資料會自動保持最新。
            st.caption("⚠️ 目前無自動排程更新中（GitHub Actions 排程已暫停，資料需手動觸發「▶ 手動抓取今日資料」更新）")

    # 三個分頁：①大盤、②選股(候選清單篩選+清單本身)、③個股資訊(個股查詢+K線圖+個股
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
        # 「候選股票池範圍」：市場/產業別/成交量門檻縮小候選股票池，跟下面「篩選條件」/
        # 「篩選方法」同一套deferred-apply慣例(改完UI不會馬上生效，按「套用篩選」才套用)，
        # 照抄桌面版desktop/main_window.py的market_filter_combo/industry_filter_combo/
        # volume_filter_spin(main_window.py:2911-2928)。
        st.caption("候選股票池範圍（市場/產業別/成交量門檻，同樣要按「套用篩選」才會生效）：")
        pool_col1, pool_col2, pool_col3 = st.columns([1, 2, 1.3])
        market_label = pool_col1.selectbox("市場", ["全部", "上市", "上櫃"], index=0, key="filter_market_label")
        selected_industries = pool_col2.multiselect("產業別", list_industries(conn), key="filter_industries")
        min_volume_lots_input = pool_col3.number_input(
            "成交量 >= (張)", min_value=0, value=10, step=1, key="filter_min_volume_lots"
        )

        st.caption("候選清單篩選條件（可複選，日後可在此擴充更多條件），改完後按「套用篩選」才會重新套用")
        filter_cols = st.columns(len(CANDIDATE_FILTERS))
        active_filters = [
            label for col, label in zip(filter_cols, CANDIDATE_FILTERS)
            if col.checkbox(label, value=CANDIDATE_FILTER_DEFAULTS.get(label, False), key=f"filter_{label}")
        ]

        # 「篩選方法：」這一列跟上面「篩選條件」分開放，視覺上比較不擁擠。2026-08-02
        # 使用者釐清語意：這裡跟上面的均線多頭排列彼此是獨立的AND條件，候選清單的基礎池
        # 一律是全市場(見chart_data.load_stock_universe_for_date())——只勾均線多排
        # 但不勾朱家泓技術分析，等同對全市場做均線掃描，不受「當天有沒有觸發朱家泓規則」
        # 限制；勾了朱家泓技術分析才會額外要求當天有出現在daily_candidates。詳見
        # chart_data.apply_candidate_filters()的說明(見desktop/main_window.py同一天
        # 的對應調整)。
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

        # 「朱家泓技術分析」勾選框：2026-08-01新增，2026-08-02改版跟其他「篩選方法」
        # (SAR翻轉)一樣是獨立的AND條件，不是「候選清單本來就限定在這個範圍」的基礎池
        # ——候選清單基礎池現在是全市場(見chart_data.load_stock_universe_for_date())，
        # 勾選這裡才會額外要求「當天有出現在daily_candidates(觸發過某條朱家泓規則)」；
        # 不勾選時，均線/SAR等其他條件會對全市場掃描，不受這個限制。預設勾選，維持
        # 「候選清單=已觸發朱家泓規則的股票」這個原本的預設體驗。
        zhu_rule_only = zhu_col.checkbox(
            "朱家泓技術分析", value=True, key="filter_zhu_rule_only",
            help="勾選時只保留當天有觸發朱家泓規則的股票；取消勾選則不限制，均線/SAR等條件會對全市場掃描",
        )

        if "applied_filters" not in st.session_state:
            st.session_state["applied_filters"] = {
                "active_filters": [], "sar_flip_option": None, "zhu_rule_only": True,
                "market": None, "industries": [], "min_volume_lots": 10,
            }
        with apply_col:
            st.markdown("&nbsp;")  # 對齊上面其他欄位的label高度，讓按鈕跟輸入框大致同一條水平線
            if st.button("套用篩選"):
                st.session_state["applied_filters"] = {
                    "active_filters": active_filters, "sar_flip_option": sar_flip_option,
                    "zhu_rule_only": zhu_rule_only,
                    "market": _MARKET_FILTER_VALUES.get(market_label),
                    "industries": selected_industries, "min_volume_lots": int(min_volume_lots_input),
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
        applied = st.session_state["applied_filters"]
        candidates_df, latest_date, is_intraday = load_stock_universe_for_date(
            conn, target_date=selected_date, market=applied.get("market"),
        )
        # 產業別/成交量：跟市場篩選一樣是「候選股票池的範圍」，在均線/SAR等篩選條件套用
        # 之前先縮小df，照抄桌面版_reload_candidates()的順序(main_window.py:2917-2928)。
        if applied.get("industries"):
            candidates_df = candidates_df[candidates_df["industry"].isin(applied["industries"])].reset_index(drop=True)
        min_volume_lots = applied.get("min_volume_lots", 0)
        if min_volume_lots > 0:
            candidates_df = candidates_df[candidates_df["volume"] >= min_volume_lots * 1000].reset_index(drop=True)
        candidates_df = apply_candidate_filters(
            conn, candidates_df, applied["active_filters"], sar_flip_option=applied["sar_flip_option"],
            zhu_rule_only=applied.get("zhu_rule_only", True), as_of_date=latest_date,
        )

        if latest_date is None:
            st.info("目前 Turso 資料庫裡還沒有任何每日選股紀錄，點上方「立即重新篩選」或等 GitHub Actions 排程跑完後就會顯示。")
        else:
            st.subheader(f"候選清單（{latest_date}，共 {len(candidates_df)} 檔）")
            if is_intraday:
                st.markdown("**:red[⚠ 尚未收盤，本頁為盤中即時資料，收盤後數字可能改變]**")
            # 候選清單內搜尋：純顯示層過濾，不透過「套用篩選」按鈕(不是資料查詢，即時生效)，
            # 跟桌面版_on_candidate_search()「找到後自動選取捲動」的做法不同——Streamlit的
            # dataframe沒有程式化捲動/選取特定列的API，改成直接篩掉不符合的列，達到同樣
            # 「從一長串候選清單快速縮小範圍」的目的。
            search_query = st.text_input("搜尋候選清單（代號或名稱）", key="candidate_search_query")
            if search_query:
                q = search_query.strip().lower()
                candidates_df = candidates_df[
                    candidates_df["stock_id"].str.lower().str.contains(q, na=False)
                    | candidates_df["name"].str.lower().str.contains(q, na=False)
                ].reset_index(drop=True)
            if candidates_df.empty:
                st.write("搜尋不到符合的股票。" if search_query else "這一天沒有符合條件的候選股。")
            else:
                st.caption("點選任一列會自動切換到「個股資訊」分頁查看該檔股票的價格走勢")

                def _style_name_by_listing_type(row: pd.Series) -> list[str]:
                    # 依上市/上櫃/興櫃上色股票名稱，照抄桌面版main_window.py的listing_type_
                    # color()慣例；只對"name"欄位回傳實際樣式，其他欄位回傳空字串不受影響。
                    color = portfolio_data.listing_type_color(row.get("listing_type"))
                    return [f"color: {color}" if col == "name" else "" for col in row.index]

                event = st.dataframe(
                    candidates_df.style.apply(_style_name_by_listing_type, axis=1),
                    use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row", key="candidates_table",
                    column_order=[
                        "stock_id", "name", "industry", "signal_name", "close", "entry_price",
                        "stop_loss", "pct_change", "volume", "sar_value", "sar_status", "sar_distance_pct",
                    ],
                    column_config={
                        "stock_id": "股票代號", "name": "名稱", "industry": "產業別",
                        "signal_name": "訊號(信心%)",  # 信心分數已經內含在signal_name字串裡(見daily_screener.py)，這裡只是把「(信心%)」這個提示放進欄位標題，不用每一列都重複寫「信心」兩個字
                        "close": st.column_config.NumberColumn("收盤價", format="%.2f"),
                        "entry_price": "進場價", "stop_loss": "停損價",
                        "pct_change": st.column_config.NumberColumn("漲跌幅(%)", format="%.2f%%"),
                        "volume": st.column_config.NumberColumn("成交量", format="%d"),
                        "sar_value": st.column_config.NumberColumn("SAR值", format="%.2f"),
                        "sar_status": "SAR狀態",
                        "sar_distance_pct": st.column_config.NumberColumn("SAR距離%", format="%.2f%%"),
                    },
                )
                if event.selection.rows:
                    selected_stock_id = str(candidates_df.iloc[event.selection.rows[0]]["stock_id"])
                    # 記錄來源候選清單日期，供「個股資訊」分頁右上角顯示「來源：X月X日的
                    # 選股策略」；順便清掉手動查詢欄位殘留的舊文字(不然下面「個股資訊」
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

    elif active_tab == TAB_INDUSTRY_ROTATION:
        # 「產業輪動」：某一天各產業別的成交量加總/平均漲跌幅/股票數，看資金比較集中往
        # 哪個產業移動——照抄桌面版desktop/main_window.py的_build_industry_rotation_
        # tab()/_refresh_industry_rotation_tab()，底層查詢函式(chart_data.list_price_
        # dates()/load_industry_rotation())兩前端共用。日期選單不受daily_candidates
        # 限制(跟「選股」分頁的候選清單日期選單不同)，只要有股價資料就能選。
        price_dates = list_price_dates(conn)
        rotation_date = (
            st.selectbox("日期", price_dates, index=0, key="industry_rotation_date_select")
            if price_dates else None
        )
        update_ts = get_latest_update_time(conn)
        update_label = datetime.fromisoformat(update_ts).strftime("%Y-%m-%d %H:%M") if update_ts else "尚無資料"
        st.caption(f"資料更新至　{update_label}")
        rotation_df, latest_date = load_industry_rotation(conn, target_date=rotation_date)
        if latest_date is None or rotation_df.empty:
            st.info("目前沒有股價資料可以計算產業輪動。")
        else:
            # 依平均漲跌幅由高到低排序，一打開就能看到資金最集中流入的產業排最前面，
            # 照抄桌面版的預設排序慣例(不用先手動點一次欄位標題排序)。
            rotation_df = rotation_df.sort_values("avg_pct_change", ascending=False).reset_index(drop=True)
            rotation_df["total_volume_lots"] = (rotation_df["total_volume"] // 1000).astype(int)
            st.subheader(f"產業輪動（{latest_date}）")
            st.dataframe(
                rotation_df, use_container_width=True, hide_index=True,
                column_order=["industry", "total_volume_lots", "avg_pct_change", "stock_count"],
                column_config={
                    "industry": "產業別",
                    "total_volume_lots": st.column_config.NumberColumn("成交量合計(張)", format="%d"),
                    "avg_pct_change": st.column_config.NumberColumn("平均漲跌幅(%)", format="%+.2f%%"),
                    "stock_count": st.column_config.NumberColumn("股票數", format="%d"),
                },
            )


if __name__ == "__main__":
    main()
