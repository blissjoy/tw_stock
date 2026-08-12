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

import base64
import html
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import markdown
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import rule_docs  # noqa: E402
from src.data.yfinance_client import TAIEX_STOCK_ID  # noqa: E402
from src.indicators.moving_average import FULL_PERIODS  # noqa: E402
from src.patterns import chart_overlays, latest_day_summary  # noqa: E402
from src.presentation import chart_data, portfolio_data, q3_analysis, stock_detail_data  # noqa: E402
from src.screener import q3_patterns  # noqa: E402
from src.presentation.chart_data import (  # noqa: E402
    CANDIDATE_FILTER_DEFAULTS,
    CANDIDATE_FILTERS,
    CANDIDATE_SAR_FLIP_ENABLED_DEFAULT,
    CANDIDATE_SAR_FLIP_OPTION_DEFAULT,
    CANDIDATE_ZHU_RULE_ONLY_DEFAULT,
    INSTITUTIONAL_ACCUMULATION_MIN_RATIO_PCT_DEFAULT,
    INSTITUTIONAL_ACCUMULATION_WINDOW_DAYS_DEFAULT,
    apply_candidate_filters,
    build_candlestick_figure,
    get_latest_candidate_update_time,
    get_latest_update_time,
    get_stock_update_time,
    list_candidate_dates,
    list_industries,
    list_price_dates,
    load_industry_rotation,
    load_industry_rotation_stocks,
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
TAB_INVENTORY = "庫存清單"
TAB_WATCHLIST = "觀察清單"
TAB_BACKFILL = "回補資料"
TAB_OPTIONS = [
    TAB_MARKET, TAB_SCREENER, TAB_STOCK_DETAIL, TAB_INDUSTRY_ROTATION,
    TAB_INVENTORY, TAB_WATCHLIST, TAB_BACKFILL,
]

# web版對主DB Turso帳號有實質額度風險的動作的冷卻時間(見src/data/admin_action_
# rate_limit.py)——同一種動作同一時間只能有一次嘗試在跑，完成後這段時間內不能再次
# 觸發，理由是保護主DB的Turso帳號額度(曾經被用完封鎖過，見.github/workflows/
# daily_pipeline.yml開頭註解)。「回補資料」跟「手動抓取今日資料」風險同一類(公開
# 網址下任何訪客都能觸發)但操作規模差很多，各自獨立設定冷卻秒數。
BACKFILL_COOLDOWN_SECONDS = 6 * 60 * 60
BACKFILL_MAX_RANGE_DAYS = 30
MANUAL_FETCH_COOLDOWN_SECONDS = 60 * 60

# 黃豐凱籌碼分析法(見src/presentation/huang_chip_data.py)接在觀察清單表格既有欄位
# 之後的額外欄位——照抄desktop/main_window.py的_HUANG_CHIP_HEADERS，J欄(大量K參考)
# 原始方法論裡沒有邏輯(手動欄位)，不顯示。
_HUANG_CHIP_HEADERS = [
    "invest_streak", "foreign_streak", "holder_whale", "holder_retail",
    "ma_price_position", "weekly_volume_pattern",
    "foreign_40d", "invest_40d", "foreign_20d", "invest_20d",
    "foreign_10d", "invest_10d", "foreign_5d", "invest_5d",
]
_HUANG_CHIP_LABELS = {
    "invest_streak": "投信", "foreign_streak": "外資",
    "holder_whale": "大戶週變化", "holder_retail": "散戶週變化",
    "ma_price_position": "均線狀態", "weekly_volume_pattern": "週K型態",
    "foreign_40d": "40日外資", "invest_40d": "40日投信",
    "foreign_20d": "20日外資", "invest_20d": "20日投信",
    "foreign_10d": "10日外資", "invest_10d": "10日投信",
    "foreign_5d": "5日外資", "invest_5d": "5日投信",
}
_HUANG_CHIP_FLOW_COLUMNS = ["foreign_40d", "invest_40d", "foreign_20d", "invest_20d", "foreign_10d", "invest_10d", "foreign_5d", "invest_5d"]


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

    import sqlite3

    from scripts import backfill_history
    from scripts.backfill_taiex import backfill_taiex_range
    from scripts.daily_pipeline import run_daily_pipeline
    from src.data import admin_action_rate_limit, portfolio_storage, storage
    from src.data.config import get_admin_access_code
    from src.data.connection import get_default_connection, get_default_portfolio_connection
    from src.data.trading_calendar import holidays_between
    from src.indicators.huang_chip_signals import COLOR_BUY, COLOR_SELL
    from src.indicators.institutional_flow import INSTITUTIONAL_STREAK_THRESHOLD
    from src.presentation import huang_chip_data
    from src.screener.daily_screener import (
        analyze_stock_signals,
        recompute_indicators_for_range,
        run_screen_and_store,
        run_screen_and_store_for_range,
        summarize_signal_matches,
    )

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

    @st.cache_resource
    def get_portfolio_conn():
        # 庫存清單/觀察清單專用連線，跟主DB(conn)分開——見src/data/portfolio_storage.py
        # 開頭的說明。2026-08-05起get_default_portfolio_connection()也比照get_conn()
        # 有Turso分支(用跟主DB分開的第二個Turso資料庫，見該函式docstring)，這裡同樣
        # 只在走本機分支時略過ensure_schema(storage.init_db()內部已經呼叫過)，Turso
        # 分支才需要自己呼叫+try/except，失敗只顯示警告不crash，理由跟get_conn()一致。
        portfolio_conn = get_default_portfolio_connection()
        if not os.environ.get("PORTFOLIO_DB_PATH"):
            try:
                portfolio_storage.ensure_portfolio_schema(portfolio_conn)
            except Exception as exc:  # noqa: BLE001
                st.warning(f"⚠️ 無法確認庫存清單資料庫schema已建立（{exc}），若資料表原本就存在應不影響讀取。")
        return portfolio_conn

    portfolio_conn = get_portfolio_conn()

    def render_price_chart(stock_id: str, widget_key: str, is_market_overview: bool = False):
        """回傳(render_chart_and_summary, render_analysis_panel)兩個callable，不自己
        決定排版——大盤只需要這兩塊(圖表/大盤分析各一個st.tabs()分頁)，但個股資訊
        還要跟「個股明細」「產出報表」(來自這個函式外的render_stock_overview_
        section()/render_stock_report_section())擠進同一組st.tabs()分頁列，版面
        怎麼組交給呼叫端決定，這個函式只負責把內容算好、包成兩個「呼叫了才畫」的
        函式。查無資料時回傳(None, None)。
        """
        price_df = load_price_history(conn, stock_id)
        if price_df.empty:
            st.warning(f"查無股票代號 {stock_id} 的價格資料。")
            return None, None

        holidays, holidays_ok = load_holidays_for_chart(price_df)
        if not holidays_ok:
            st.caption("⚠️ 假日清單暫時無法取得，圖表可能仍有國定假日空白。")

        trendlines = chart_overlays.compute_trendlines(price_df)

        if is_market_overview:
            # 大盤只有一檔、資料量固定，不像個股資訊那樣需要讓使用者調整顯示項目——固定
            # 顯示全部均線/切線/支撐壓力/MACD/KD/SAR，比照桌面版desktop/main_window.py的
            # _refresh_market_tab()(show_macd=True/show_kd=True/show_sar=True都是寫死的，
            # 不是使用者可以關掉的checkbox)。改成顯示「資料更新至」時間戳，照抄「產業輪動」
            # 分頁既有的寫法。
            selected_periods = FULL_PERIODS
            selected_trendline_keys = tuple(trendlines.keys())
            show_sr = show_macd = show_kd = show_sar = True
            update_ts = get_latest_update_time(conn)
            update_label = datetime.fromisoformat(update_ts).strftime("%Y-%m-%d %H:%M") if update_ts else "尚無資料"
            st.caption(f"資料更新至　{update_label}")
        else:
            # 2026-08-05改版：均線/切線改成勾選框分組(st.container(border=True)包一排
            # st.checkbox)，比照桌面版desktop/main_window.py的QGroupBox(「顯示均線」/
            # 「顯示切線／軌道線」各自一個帶邊框的群組，逐項checkbox勾選)，取代原本的
            # st.multiselect下拉選單——桌面版原本就是這樣操作，不是下拉多選。
            control_col1, control_col2, control_col3, control_col4, control_col5, control_col6 = st.columns(
                [2, 2, 1, 1, 1, 1],
            )
            with control_col1:
                with st.container(border=True):
                    st.caption("顯示均線")
                    ma_cols = st.columns(len(FULL_PERIODS))
                    selected_periods = tuple(
                        n for n, ma_col in zip(FULL_PERIODS, ma_cols)
                        if ma_col.checkbox(f"MA{n}", value=True, key=f"{widget_key}_ma_{n}")
                    )
            with control_col2:
                with st.container(border=True):
                    st.caption("顯示切線／軌道線")
                    trendline_keys_available = list(trendlines.keys())
                    if trendline_keys_available:
                        trend_cols = st.columns(len(trendline_keys_available))
                        selected_trendline_keys = tuple(
                            key for key, trend_col in zip(trendline_keys_available, trend_cols)
                            if trend_col.checkbox(chart_data.TRENDLINE_LABELS[key], value=True, key=f"{widget_key}_trend_{key}")
                        )
                    else:
                        selected_trendline_keys = ()
                        st.caption("目前資料範圍內沒有找到符合「線不蓋線」條件的切線。")
            with control_col3:
                show_sr = st.checkbox("顯示支撐壓力", value=True, key=f"{widget_key}_sr_checkbox")
            with control_col4:
                show_macd = st.checkbox("顯示MACD", value=True, key=f"{widget_key}_macd_checkbox")
            with control_col5:
                show_kd = st.checkbox("顯示KD", value=True, key=f"{widget_key}_kd_checkbox")
            with control_col6:
                show_sar = st.checkbox("顯示SAR", value=True, key=f"{widget_key}_sar_checkbox")

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

            # 2026-08-11新增「逃命示警」：使用者反映一長串規則列表看不出哪些是該注意快逃
            # 的訊號，要求把偏向「示警/賣出」性質的既有規則(見src.screener.escape_
            # signals)提到最上方、用紅色粗體獨立標示，跟下面依信心分數排序的完整清單
            # 分開——只挑技術面(tech_matches)的escape訊號，籌碼面訊號性質不同，這次
            # 範圍不含在內。
            escape_matches = [m for m in tech_matches if m.get("is_escape")]
            if escape_matches:
                # ⚠️ 這裡跟_render_rule_matches()不同(st.write/st.caption預設不解析HTML)，
                # 用了unsafe_allow_html=True才能套用紅色粗體樣式，note文字裡常見的
                # "MA5<MA10<MA20"這種"<"/">"符號沒有escape的話會被誤判成HTML標籤、內容
                # 被吃掉一截(桌面版QTextBrowser.setHtml()踩過同樣的坑，這裡要一併escape)。
                escape_lines = "".join(
                    f"<li><b>{html.escape(m['rule_id'])} {html.escape(m['title'])}</b>："
                    f"{html.escape((m.get('note') or '').split(chr(10))[0])}</li>"
                    for m in escape_matches
                )
                st.markdown(
                    f'<p style="color:#cc0000;font-weight:bold;font-size:110%;">'
                    f"🚨 逃命示警（{len(escape_matches)}條）</p>"
                    f'<ul style="color:#cc0000;font-weight:bold;">{escape_lines}</ul>',
                    unsafe_allow_html=True,
                )

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

        def _render_chart_and_summary() -> None:
            # 預設只顯示離現價最近的支撐/壓力各一條，不是把所有轉折點都疊上去(最多可能到
            # 6條、會把圖擠得很亂)——書中真正有參考意義的本來就是離現價最近的那一層。
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
            # 2026-08-05拿掉這裡原本多出來的st.dataframe(price_df.tail(20))原始資料
            # 表格——桌面版desktop/main_window.py的「圖表」分頁(大盤跟個股資訊都一樣)
            # 圖表下方接的是文字型「最新交易日摘要」，沒有原始OHLCV表格，這個表格純粹
            # 是web版多出來的東西，兩邊分頁都拿掉，徹底跟桌面版一致。

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
            # 2026-08-08新增：今天量能相對5日均量(書中「基本量」)的水位判讀，跟上面
            # 「量價訊號」不同——那裡只在攻擊量/爆量等特定命名訊號觸發時才顯示，這裡是
            # 每天都有結論的基礎陳述(見src/patterns/latest_day_summary.py的
            # summarize_volume_vs_ma5()說明)。
            volume_vs_ma5 = summary.get("volume_vs_ma5")
            if volume_vs_ma5:
                direction = "高於" if volume_vs_ma5["is_above"] else "低於"
                st.write(f"今日量能：{direction}5日均量（{volume_vs_ma5['ratio']:.2f}倍）")
                st.caption(volume_vs_ma5["note"])
            else:
                st.write("今日量能：資料不足5天，無法比較5日均量")
            st.caption("⚠️ 型態訊號的「高檔/低檔」判斷已接上趨勢位置模組(is_at_high/is_at_low)，但目前只用單一容忍帶門檻，還沒有初升/主升/末升等更細的子階段分類。")

        # 2026-08-05改版：不在這裡自己決定排版(st.tabs()或堆疊)，回傳兩個callable交給
        # 呼叫端組裝——大盤只需要跟「大盤分析」拼成2個分頁，個股資訊還要跟「個股明細」/
        # 「產出報表」拼成同一組4個分頁的st.tabs()，這個函式不知道、也不需要知道外面
        # 還有哪些分頁，只負責把「圖表」跟「分析」這兩塊內容準備好。
        return _render_chart_and_summary, _render_analysis_panel

    def _colored_num(value, decimals: int = 0, signed: bool = False, suffix: str = "") -> str:
        """數字上紅(正)下綠(負)——跟K棒既有的紅漲綠跌配色一致，照抄desktop/main_window.py
        的_colored_num()。value是None/NaN時回傳"-"，不是"0"，區分「沒有資料」跟「剛好是0」。"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return "-"
        color = "#c0392b" if value > 0 else ("#27ae60" if value < 0 else "#333333")
        sign = "+" if signed and value > 0 else ""
        return f'<span style="color:{color};">{sign}{value:,.{decimals}f}{suffix}</span>'

    def _render_institutional_flow_analysis(flow: dict | None, momentum: dict | None) -> None:
        """依load_institutional_flow_analysis()/load_institutional_momentum_analysis()
        的判讀結果，組出法人買賣總覽下方的「📊 法人籌碼分析」文字——照抄desktop/
        main_window.py的_build_institutional_flow_analysis_html()，理論依據/書籍
        引用文字完全一致(見該函式docstring)，這裡不重複展開說明。"""
        if flow is None and momentum is None:
            return
        st.markdown("**📊 法人籌碼分析**")
        streak_shown = False
        if flow is not None:
            sanhua = flow["三大法人"]
            if sanhua["is_sell_warning"]:
                st.markdown(
                    f'<p style="color:#27ae60;">⚠️ 三大法人已連續賣超{sanhua["streak_days"]}天，'
                    "達到停損觀察門檻（依朱家泓《抓住飆股輕鬆賺》淘汰法選股排除規則第8項："
                    "三大法人連續賣超應避開，建議留意停損／減碼）。</p>", unsafe_allow_html=True,
                )
                streak_shown = True
            elif sanhua["is_buy_watch"]:
                st.markdown(
                    f'<p style="color:#c0392b;">三大法人已連續買超{sanhua["streak_days"]}天，'
                    "短線動能偏多，但書中沒有給「連續買超代表安全」的保證，僅供參考。</p>", unsafe_allow_html=True,
                )
                streak_shown = True

            invtrust = flow["投信"]
            if invtrust["is_buy_watch"]:
                st.markdown(
                    f'<p style="color:#c0392b;">📈 投信已連續買超{invtrust["streak_days"]}天'
                    "（依陳家豐《看懂籌碼 股市賺大錢》：投信受法規限制(單一個股持股上限10%、"
                    "單日買進不得超過成交量10%)須分批布局，連續加碼3~5天且個股剛脫離下跌"
                    "整理通常是切入時機——本畫面沒有另外判斷「是否剛脫離整理區」，需自行"
                    "對照K線圖）。</p>", unsafe_allow_html=True,
                )
                streak_shown = True
            elif invtrust["is_sell_warning"]:
                st.markdown(
                    f'<p style="color:#27ae60;">投信已連續賣超{invtrust["streak_days"]}天，'
                    "留意是否轉向保守（書中提到投信若轉向防禦型持股，代表對後市看淡）。</p>", unsafe_allow_html=True,
                )
                streak_shown = True

            foreign = flow["外資"]
            if foreign["is_buy_watch"] or foreign["is_sell_warning"]:
                direction_text = "買超" if foreign["is_buy_watch"] else "賣超"
                st.markdown(
                    f'<p style="color:#999999;">外資已連續{direction_text}{foreign["streak_days"]}天——'
                    "⚠️ 陳家豐書中提醒：外資買賣單只有在中小型股(非權值股)才有參考價值，"
                    "權值股/大型股的外資買賣受全球布局、期貨套利、指數調整干擾，不宜直接"
                    "採信本訊號判斷多空。</p>", unsafe_allow_html=True,
                )
                streak_shown = True

            dealer = flow["自營商"]
            if dealer["is_buy_watch"] or dealer["is_sell_warning"]:
                direction_text = "買超" if dealer["is_buy_watch"] else "賣超"
                st.markdown(
                    f'<p style="color:#999999;">自營商連續{direction_text}{dealer["streak_days"]}天——'
                    "⚠️ 陳家豐書中建議「自營商首先剔除」，操作週期短、常忽買忽賣，不建議"
                    "用連續性判斷趨勢，僅供參考。</p>", unsafe_allow_html=True,
                )
                streak_shown = True

            if not streak_shown:
                st.write(f"近期法人買賣方向尚未達連續{INSTITUTIONAL_STREAK_THRESHOLD}天門檻，暫無明顯訊號。")

        if momentum:
            trend_colors = {
                "買超力道增強": "#c0392b", "由賣轉買": "#c0392b",
                "賣壓加重": "#27ae60", "由買轉賣": "#27ae60",
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
                st.markdown(f"**{group_titles[group]}**")
                for label, info in periods.items():
                    current_lots, prior_lots = info["current"] / 1000, info["prior"] / 1000
                    trend = info["trend"]
                    color = trend_colors.get(trend, "#999999")
                    verb = "持續買進" if trend in ("買超力道增強", "由賣轉買") else ("持續賣出" if trend in ("賣壓加重", "由買轉賣") else "力道趨緩，方向未明確轉變")
                    st.markdown(
                        f'<p style="color:{color};">近{label}合計買賣超{current_lots:+,.0f}張，'
                        f"較前{label}（{prior_lots:+,.0f}張）{trend}——{verb}。</p>", unsafe_allow_html=True,
                    )

    def _render_huang_chip_supplementary(conn, stock_id: str) -> None:
        """觀察清單黃豐凱籌碼分析法(見src/presentation/huang_chip_data.py)的「法人近期
        籌碼(投信/外資)」「大戶/散戶持股變化(週)」「技術型態(均線狀態/週K型態)」3組，
        2026-08-09使用者要求搬到「個股明細」的「📊 法人籌碼分析」下方顯示——重用
        huang_chip_data.load_huang_chip_row()既有的查詢/判讀邏輯，跟觀察清單顯示的
        是同一份計算結果，不重新實作一次。「法人買賣超(K~R，40/20/10/5日張數)」那組
        刻意不搬過來：上面「法人買賣總覽」表格已經有涵蓋1日~1年多種天期的累計買賣超，
        兩者功能重複。
        """
        chip = huang_chip_data.load_huang_chip_row(conn, stock_id)

        invest, foreign = chip["invest_streak"], chip["foreign_streak"]
        if (invest and invest.get("text")) or (foreign and foreign.get("text")):
            st.markdown("**法人近期籌碼**")
            # 2026-08-09使用者反映：這裡看「連續同方向天數」，跟上面「買賣力道變化」看
            # 「近N日加總後比較」是不同算法(見huang_chip_signals.classify_institutional_
            # streak()的docstring：連續天數要滿3天才給確定方向，天天翻來覆去時即使加總
            # 是正的也只會顯示「方向未定」)，同一個投資人分類可能同時出現看似矛盾的結論，
            # 兩者都對，只是評估角度不同——加這句說明，避免使用者誤以為系統算錯。
            st.caption("⚠️ 這裡看「從今天起連續同方向天數」(未連續滿3天顯示「方向未定」)，跟上面「買賣力道變化」的「近N日加總後比較」是不同算法，兩者角度不同，可能同時出現看似矛盾的結論，都正確。")
            c1, c2 = st.columns(2)
            invest_html = f'投信：<span style="color:{invest["color"]};">{invest["text"]}</span>' if invest and invest.get("text") else "投信：-"
            foreign_html = f'外資：<span style="color:{foreign["color"]};">{foreign["text"]}</span>' if foreign and foreign.get("text") else "外資：-"
            c1.markdown(invest_html, unsafe_allow_html=True)
            c2.markdown(foreign_html, unsafe_allow_html=True)

        st.markdown("**大戶/散戶持股變化(週)**")
        holder = chip["holder_change"]
        if holder is None:
            st.caption("尚未有資料（大戶/散戶持股變化目前只有觀察清單裡的股票會自動更新）")
        else:
            c1, c2 = st.columns(2)
            c1.markdown(f'<span style="color:{holder["whale"]["color"]};">{holder["whale"]["text"]}</span>', unsafe_allow_html=True)
            c2.markdown(f'<span style="color:{holder["retail"]["color"]};">{holder["retail"]["text"]}</span>', unsafe_allow_html=True)

        ma, weekly = chip["ma_price_position"], chip["weekly_volume_pattern"]
        if ma or weekly:
            st.markdown("**技術型態**")
            if ma:
                for line in ma["lines"]:
                    st.markdown(f'<span style="color:{line["color"]};">{line["text"]}</span>　', unsafe_allow_html=True)
            if weekly:
                st.write(f"{weekly['pattern']}（{weekly['reference_week_start']}）")

    def _render_margin_maintenance_analysis(maintenance: dict | None) -> None:
        """融資維持率分析文字，照抄desktop/main_window.py的
        _build_margin_maintenance_analysis_html()，理論依據見該函式docstring。"""
        if maintenance is None or maintenance["ratio"] is None:
            return
        ratio_pct = maintenance["ratio"] * 100
        state = maintenance["state"]
        st.markdown("**📊 融資維持率分析**")
        st.write(f"估算融資維持率：{ratio_pct:.1f}%（狀態：{state}；融資成數估算採書中預設的6成，非個股實際規定，僅供參考）")
        if state == "已跌破斷頭線":
            st.markdown(
                '<p style="color:#27ae60;">⚠️ 估算已跌破斷頭線(120%)，代表融資部位可能面臨'
                "券商強制賣出的斷頭賣壓（依陳家豐《看懂籌碼》第2篇第4章）。</p>", unsafe_allow_html=True,
            )
        elif state == "警戒區(爹不疼娘不愛)":
            st.markdown(
                '<p style="color:#e67e22;">融資維持率落在135%以下的警戒區——書中提醒：'
                "主力通常不會在這個階段進場，因為要面對層層融資套牢賣壓，此時股票容易"
                "「爹不疼、娘不愛」，若後續跌破120%，可留意超跌反彈機會。</p>", unsafe_allow_html=True,
            )
        if maintenance["oversold_rebound_signal"]:
            st.markdown(
                '<p style="color:#c0392b;">📈 已連續多日低於120%斷頭線，符合書中「超跌'
                "反彈」訊號條件——僅適合手腳靈活、能嚴設停利的短線操作，不是長線買進"
                "依據。</p>", unsafe_allow_html=True,
            )

    def render_q3_analysis_section(stock_id: str) -> None:
        """「三維過濾法」分頁：複製外部工具Antigravity儀表板的個股健檢面板(見
        ai/q3-rules/)——AI判定(規則版)＋12種量價矩陣＋30種主力型態＋技術指標總覽。
        照抄桌面版desktop/main_window.py的_build_q3_analysis_html()同一份資料來源
        (q3_analysis.load_q3_analysis())，只是渲染成streamlit元件而非HTML字串。
        2026-08-10新增。
        """
        price_df = chart_data.load_price_history(conn, stock_id)
        if price_df.empty:
            st.info(f"查無股票代號 {stock_id} 的價格資料。")
            return
        trend_df = chart_data.load_price_history(conn, stock_id, days=chart_data.TREND_LOOKBACK_DAYS)
        result = q3_analysis.load_q3_analysis(price_df, trend_df=trend_df)
        if result is None:
            st.info("資料天數不足，無法計算三維過濾法。")
            return

        ind = result["indicators"]
        verdict = result["verdict"]

        def fmt(value, digits: int = 2) -> str:
            return f"{value:.{digits}f}" if value is not None else "-"

        st.markdown(f"#### 🤖 AI判定(規則版)：{verdict['text']}")
        col_support, col_score = st.columns(2)
        col_support.metric("防守支撐價", f"${fmt(verdict['support_price'])}")
        col_score.metric("綜合評分", f"{verdict['score']:+d}")
        for b in verdict["bullets"]:
            st.markdown(f"- {b}")

        st.markdown("#### 12種量價矩陣")
        matrix = result["matrix"]
        if matrix is not None:
            st.markdown(f"**{matrix['label']}**（{matrix['rule_id']}）")
            st.caption(matrix["interpretation"])
            if matrix.get("caveat"):
                st.warning(matrix["caveat"], icon="⚠️")
        else:
            st.caption("今天沒有落在任何已定義的矩陣格。")

        st.markdown("#### 30種主力型態")
        patterns = result["patterns"]
        if patterns:
            st.caption(
                "「⚠️簡化版」代表本專案沒有對應既有規則，由本功能自行實作核心條件，跟外部工具"
                "原文定義不會100%一致；「沿用既有規則」代表判斷邏輯跟系統其他地方完全一致。"
            )
            for p in patterns:
                basis_badge = "⚠️簡化版" if p["basis"] == q3_patterns.BASIS_SIMPLIFIED else "沿用既有規則"
                st.markdown(f"- `{basis_badge}` **{p['name']}**（{p['rule_id']}）：{p['note']}")
        else:
            st.caption("今天沒有觸發任何已定義的型態。")

        st.markdown("#### 技術指標總覽")
        indicator_rows = [
            ("8日均線(8MA)", fmt(ind["ma8"])),
            ("月線(20MA)", fmt(ind["ma20"])),
            ("季線(60MA)", fmt(ind["ma60"])),
            ("KD指標", f"K:{fmt(ind['k'])} D:{fmt(ind['d'])}"),
            ("RSI", fmt(ind["rsi"])),
            ("MACD柱狀", fmt(ind["macd_osc"])),
            ("OBV(能量潮)", fmt(ind["obv"], 0)),
            ("ATR(真實波幅)", fmt(ind["atr"])),
            ("乖離率(BIAS)", f"{fmt(ind['bias_pct'])}%"),
            ("連續站上布林上軌", f"{ind['bollinger_streak_days']} 天"),
            ("均線交叉", ind["ma_cross"]),
            ("爆量訊號", "有" if ind["big_volume_today"] else "無"),
            (
                "今日量價關係",
                (ind["volume_price_relation"] or "-")
                + (
                    f"　（今日量/5日均量={ind['volume_ratio_vs_ma5_pct']:.0f}%）"
                    if ind["volume_ratio_vs_ma5_pct"] is not None
                    else ""
                ),
            ),
            ("今日Q1/Q2/Q3", f"{ind['q1_price']}／{ind['q2_volume']}／{ind['q3_position']}"),
        ]
        st.dataframe(
            pd.DataFrame(indicator_rows, columns=["指標", "數值"]),
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "「三維過濾法」複製外部工具Antigravity儀表板的分析框架(見ai/q3-rules/)，"
            "AI判定為規則版加權評分，非LLM生成文字，僅供參考，不是投資建議。"
        )

    def render_stock_overview_section(stock_id: str) -> None:
        """「個股明細」6個區塊：交易資訊/法人買賣總覽/主力進出/資券變化總覽/大戶籌碼/
        MOPS增減資紀錄(2026-08-09新增)，照抄desktop/main_window.py的_build_stock_
        overview_tab()版面結構(各自獨立可收合的區塊，這裡用st.expander(expanded=True)取代_CollapsibleBox，
        預設展開一致)。只有個股資訊分頁呼叫，大盤不顯示這個區塊(桌面版也是同樣的
        個股專屬邊界，見_build_market_tab()跟_build_stock_detail_tab()是分開的
        兩份inner tabs，大盤那份沒有「個股明細」)。主力進出/大戶籌碼目前資料庫
        schema還沒有對應的資料來源(見stock_detail_data.py模組docstring)，維持
        桌面版同樣的「尚未串接資料來源」提示，不假造資料。2026-08-05拿掉開頭原本
        的「## 個股明細」標題——桌面版對應的inner tab內容本身沒有重複標題(分頁
        標籤本身就是標題)，這個函式現在是被包在st.tabs()的「個股明細」分頁裡呼叫。
        """
        # 「全部展開／全部收合」：2026-08-09新增，使用者要求6個區塊個別展開/收合
        # 之外，還要能一次全部展開或全部收合。
        #
        # ⚠️ 這裡的寫法是實測踩過坑才定案的，記錄清楚避免之後改壞：st.expander()
        # 帶key時，直接把session_state[key]改成目標值(True/False)再rerun，實測
        # 「收合」方向常常正常生效，但「展開」方向即使session_state確實已經變成
        # True，畫面上的expander卻常常維持收合——這個現象在單頁小範例裡有時會動、
        # 有時不會動，行為不穩定，看起來是st.expander()這個元件對「同一個key、
        # 只改session_state值」這種更新路徑的支援並不可靠(streamlit 1.60.0)。
        # 唯一實測穩定可靠(連續切換多次都正確)的做法：**不要對同一個key改
        # session_state值**，而是每次按「全部展開/收合」都讓generation計數器
        # +1、所有expander的key都帶上這個generation號碼——這樣每次群組操作都是
        # 給widget一把「全新、從沒出現過」的key，同時直接把這次的目標展開狀態
        # 當成expanded=的字面值傳入(不是True的寫死值，是bulk_default這個變數)，
        # 讓每個expander在「第一次以這把新key出現」的當下就正確初始化成目標
        # 狀態。個別區塊自己點頭部箭頭展開/收合，在同一個generation內仍然正常
        # 運作(那是widget自己原生的session_state追蹤，不受這裡影響)。
        # 2026-08-12新增：使用者反映「個股明細」最上方看不出現在顯示的是哪一檔股票，
        # 跟「個股分析」／「三維過濾法」分頁一樣在最上方顯示「代號 名稱」。
        stock_name = chart_data.get_stock_name(conn, stock_id)
        st.markdown(f"#### {stock_id} {stock_name}" if stock_name else f"#### {stock_id}")

        overview_titles = ["交易資訊", "法人買賣總覽", "主力進出", "資券變化總覽", "大戶籌碼", "MOPS增減資紀錄"]
        gen_key = f"stock_overview_gen_{stock_id}"
        default_key = f"stock_overview_bulk_default_{stock_id}"

        col_expand, col_collapse, _ = st.columns([1, 1, 6])
        if col_expand.button("全部展開", key=f"stock_overview_expand_all_{stock_id}"):
            st.session_state[default_key] = True
            st.session_state[gen_key] = st.session_state.get(gen_key, 0) + 1
            st.rerun()
        if col_collapse.button("全部收合", key=f"stock_overview_collapse_all_{stock_id}"):
            st.session_state[default_key] = False
            st.session_state[gen_key] = st.session_state.get(gen_key, 0) + 1
            st.rerun()

        _overview_gen = st.session_state.get(gen_key, 0)
        _overview_bulk_default = st.session_state.get(default_key, True)

        def _stock_overview_expander_key(title: str) -> str:
            return f"stock_overview_expanded_{stock_id}_{title}_{_overview_gen}"

        with st.expander("交易資訊", expanded=_overview_bulk_default, key=_stock_overview_expander_key("交易資訊")):
            quote = stock_detail_data.load_quote_summary(conn, stock_id)
            if quote is None:
                st.write("查無成交資料。")
            else:
                st.caption(f"資料時間：{quote['date']}")
                estimated_suffix = "（估）" if quote["avg_price_is_estimated"] else ""
                avg_price_text = f"{quote['avg_price']:,.2f}{estimated_suffix}" if quote["avg_price"] is not None else "-"
                trading_money_text = (
                    f"{quote['trading_money_billion']:,.2f}{estimated_suffix}"
                    if quote["trading_money_billion"] is not None else "-"
                )
                cost_summary = stock_detail_data.load_latest_institutional_cost_summary(conn, stock_id)
                foreign_cost = cost_summary["外資"] if cost_summary else None
                trust_cost = cost_summary["投信"] if cost_summary else None
                foreign_cost_text = f"{foreign_cost:,.2f}" if foreign_cost is not None else "不適用"
                trust_cost_text = f"{trust_cost:,.2f}" if trust_cost is not None else "不適用"
                rows = [
                    ("成交", _colored_num(quote["close"], 2), "昨收", f"{quote['prev_close']:,.2f}" if quote["prev_close"] is not None else "-"),
                    ("開盤", f"{quote['open']:,.2f}", "漲跌幅", _colored_num(quote["change_pct"], 2, signed=True, suffix="%")),
                    ("最高", f"{quote['high']:,.2f}", "漲跌", _colored_num(quote["change"], 2, signed=True)),
                    ("最低", f"{quote['low']:,.2f}", "總量", f"{quote['volume_lots']:,} 張"),
                    ("均價", avg_price_text, "昨量", f"{quote['prev_volume_lots']:,} 張" if quote["prev_volume_lots"] is not None else "-"),
                    ("成交金額(億)", trading_money_text, "振幅", _colored_num(quote["amplitude_pct"], 2, suffix="%") if quote["amplitude_pct"] is not None else "-"),
                    ("外資持有成本(預估)", foreign_cost_text, "投信持有成本(預估)", trust_cost_text),
                ]
                for label1, value1, label2, value2 in rows:
                    c1, c2, c3, c4 = st.columns([1, 2, 1, 2])
                    c1.caption(label1)
                    c2.markdown(value1, unsafe_allow_html=True)
                    c3.caption(label2)
                    c4.markdown(value2, unsafe_allow_html=True)

        with st.expander("法人買賣總覽", expanded=_overview_bulk_default, key=_stock_overview_expander_key("法人買賣總覽")):
            cumulative = stock_detail_data.load_institutional_cumulative(conn, stock_id)
            if cumulative is None:
                st.write("查無法人買賣資料。")
            else:
                periods = list(stock_detail_data.INSTITUTIONAL_PERIODS.keys())
                st.caption("單位：張")
                table_df = pd.DataFrame(
                    [[cumulative[group][label] / 1000 for label in periods] for group in stock_detail_data.INSTITUTIONAL_GROUPS],
                    index=stock_detail_data.INSTITUTIONAL_GROUPS, columns=periods,
                )
                st.dataframe(
                    table_df.style.format("{:+,.0f}").map(lambda v: "color:#c0392b" if v > 0 else ("color:#27ae60" if v < 0 else "")),
                    use_container_width=True,
                )

                cost = stock_detail_data.load_institutional_estimated_cost(conn, stock_id)
                if cost:
                    st.caption("預估持股成本價（單位：元，淨賣出天期無累積部位，標示為不適用）")
                    # ⚠️ 實測發現：不管是None還是轉成float("nan")，st.dataframe()搭配
                    # Styler時對缺值儲存格一律顯示Python的"None"字面字串，Styler.format()
                    # 的na_rep參數不會生效(疑似st.dataframe內部Arrow轉換對NaN有自己的
                    # 固定顯示邏輯，蓋過Styler的格式化結果)——改成在建DataFrame前就把每格
                    # 轉成「已經格式化好的字串」("不適用"或數字字串)，不依賴Styler處理NA，
                    # 犧牲純數字欄位的靠右對齊，但保證顯示正確。
                    cost_df = pd.DataFrame(
                        [[f"{cost[group][label]:,.2f}" if cost[group][label] is not None else "不適用" for label in periods]
                         for group in stock_detail_data.ESTIMATED_COST_GROUPS],
                        index=stock_detail_data.ESTIMATED_COST_GROUPS, columns=periods,
                    )
                    st.dataframe(cost_df, use_container_width=True)

                flow = stock_detail_data.load_institutional_flow_analysis(conn, stock_id)
                momentum = stock_detail_data.load_institutional_momentum_analysis(conn, stock_id)
                _render_institutional_flow_analysis(flow, momentum)
                _render_huang_chip_supplementary(conn, stock_id)

        with st.expander("主力進出", expanded=_overview_bulk_default, key=_stock_overview_expander_key("主力進出")):
            st.caption("⚠️ 尚未串接資料來源（需要券商分點籌碼資料，schema已預留broker_chips表，待FinMind付費方案開通後才能接上）。")

        with st.expander("資券變化總覽", expanded=_overview_bulk_default, key=_stock_overview_expander_key("資券變化總覽")):
            margin_view = st.radio(
                "檢視", ["當日", "累計"], key=f"margin_view_{stock_id}", horizontal=True, label_visibility="collapsed",
            )
            maintenance = stock_detail_data.load_margin_maintenance_analysis(conn, stock_id)
            if margin_view == "當日":
                margin = stock_detail_data.load_margin_daily(conn, stock_id)
                if margin is None:
                    st.write("查無資券資料。")
                else:
                    st.caption(f"資料時間：{margin['date']}")
                    rows = []
                    for row_label, key in (("融資", "margin"), ("融券", "short")):
                        r = margin[key]
                        rows.append({
                            "": row_label, "買進": r["buy"], "賣出": r["sell"], "現價": margin["close"],
                            "增減": r["change"], "餘額": r["balance"],
                            "使用率": f"{r['usage_rate']:.2f}%" if r["usage_rate"] is not None else "-",
                            "連增連減": r["streak"] or "-",
                        })
                    st.dataframe(pd.DataFrame(rows).set_index(""), use_container_width=True)
                    offset_text = f"{margin['offset_loan_and_short']:,}" if margin["offset_loan_and_short"] is not None else "-"
                    ratio_text = f"{margin['short_to_margin_ratio_pct']:.2f}%" if margin["short_to_margin_ratio_pct"] is not None else "-"
                    st.caption(f"資券互抵：{offset_text} 張　券資比：{ratio_text}")
            else:
                cumulative_days = 20
                margin_cum = stock_detail_data.load_margin_cumulative(conn, stock_id, days=cumulative_days)
                if margin_cum is None:
                    st.write("查無資券資料。")
                else:
                    st.caption(f"最近{margin_cum['days']}個交易日累計")
                    rows = [
                        {"": row_label, "買進": margin_cum[key]["buy"], "賣出": margin_cum[key]["sell"], "餘額增減": margin_cum[key]["change"]}
                        for row_label, key in (("融資", "margin"), ("融券", "short"))
                    ]
                    st.dataframe(pd.DataFrame(rows).set_index(""), use_container_width=True)
            _render_margin_maintenance_analysis(maintenance)

        with st.expander("大戶籌碼", expanded=_overview_bulk_default, key=_stock_overview_expander_key("大戶籌碼")):
            st.caption("⚠️ 尚未串接資料來源（需要股權分散/大戶持股統計資料，目前資料庫schema還沒有對應的表）。")

        with st.expander("MOPS增減資紀錄", expanded=_overview_bulk_default, key=_stock_overview_expander_key("MOPS增減資紀錄")):
            # 2026-08-09新增：只顯示「哪個月有公司增減資公告」這個中性事實(見
            # stock_detail_data.load_mops_capital_changes()的已知限制)，沒有方向/
            # 金額/恢復交易日期，也不做任何規則判讀，避免使用者誤以為系統已經幫忙
            # 判斷過是增資還是減資。照抄desktop/main_window.py的_build_overview_
            # mops_capital_changes_html()同一套邏輯。
            records = stock_detail_data.load_mops_capital_changes(conn, stock_id)
            if not records:
                st.caption("查無資料（該股票這段期間沒有增減資公告，或尚未手動執行 scripts/fetch_mops_capital_changes.py 抓取）。")
            else:
                st.caption("⚠️ 僅顯示「哪個年月有公司增減資公告」，來源：公開資訊觀測站（MOPS）公司增減資表。尚未區分增資／減資方向、金額、恢復交易日期。")
                market_label = {"sii": "上市", "otc": "上櫃"}
                for r in records:
                    year, month = r["year_month"][:-2], r["year_month"][-2:]
                    market_text = market_label.get(r["market"], r["market"])
                    st.write(f"- {year}年{month}月（{market_text}）")

    # ------------------------------------------------------------------
    # 個股報表PDF匯出（2026-08-05新增，見ai/PLAN.md第10批）
    # ------------------------------------------------------------------
    #
    # 桌面版用QWebEnginePage.printToPdf()對含JS的HTML(chart_render.py那份互動圖表)
    # 直接印成PDF。weasyprint不執行JavaScript，這裡改成：①圖表用kaleido把Plotly
    # Figure轉成靜態PNG直接嵌入(不是<iframe>)；②個股明細5個區塊/個股分析/附錄的HTML
    # 字串產生邏輯逐字照抄desktop/main_window.py對應的_build_overview_*_html()／
    # _render_rule_match_blocks()／_build_report_reference_appendix()方法，只是簽名
    # 改成明確傳入stock_id(不像desktop方法綁在self.conn)。不重構桌面版、不共用這組
    # 函式——理由跟這個session其餘web端函式一致：桌面版方法已經穩定運作、沒有測試
    # 覆蓋，貿然抽成共用模組風險大於效益。

    def _build_report_quote_html(stock_id: str) -> str:
        """「交易資訊」表格，照抄desktop/main_window.py的_build_overview_quote_html()。"""
        quote = stock_detail_data.load_quote_summary(conn, stock_id)
        if quote is None:
            return "<p>查無成交資料。</p>"
        c = _colored_num
        estimated_suffix = "（估）" if quote["avg_price_is_estimated"] else ""
        avg_price_text = f"{quote['avg_price']:,.2f}{estimated_suffix}" if quote["avg_price"] is not None else "-"
        trading_money_text = (
            f"{quote['trading_money_billion']:,.2f}{estimated_suffix}"
            if quote["trading_money_billion"] is not None else "-"
        )
        cost_summary = stock_detail_data.load_latest_institutional_cost_summary(conn, stock_id)
        foreign_cost = cost_summary["外資"] if cost_summary else None
        trust_cost = cost_summary["投信"] if cost_summary else None
        foreign_cost_text = f"{foreign_cost:,.2f}" if foreign_cost is not None else "不適用"
        trust_cost_text = f"{trust_cost:,.2f}" if trust_cost is not None else "不適用"
        rows = [
            ("成交", f"<b>{c(quote['close'], 2)}</b>", "昨收", f"{quote['prev_close']:,.2f}" if quote["prev_close"] is not None else "-"),
            ("開盤", f"{quote['open']:,.2f}", "漲跌幅", c(quote["change_pct"], 2, signed=True, suffix="%")),
            ("最高", f"{quote['high']:,.2f}", "漲跌", c(quote["change"], 2, signed=True)),
            ("最低", f"{quote['low']:,.2f}", "總量", f"{quote['volume_lots']:,} 張"),
            ("均價", avg_price_text, "昨量", f"{quote['prev_volume_lots']:,} 張" if quote["prev_volume_lots"] is not None else "-"),
            ("成交金額(億)", trading_money_text, "振幅", c(quote["amplitude_pct"], 2, suffix="%") if quote["amplitude_pct"] is not None else "-"),
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

    def _institutional_flow_analysis_html(flow: dict | None, momentum: dict | None) -> str:
        """法人籌碼分析文字，跟_render_institutional_flow_analysis()同一套判讀邏輯
        (理論依據見該函式docstring)，這裡回傳HTML字串而不是呼叫st.markdown()。"""
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
                "買超力道增強": "#c0392b", "由賣轉買": "#c0392b",
                "賣壓加重": "#27ae60", "由買轉賣": "#27ae60",
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
                    verb = "持續買進" if trend in ("買超力道增強", "由賣轉買") else ("持續賣出" if trend in ("賣壓加重", "由買轉賣") else "力道趨緩，方向未明確轉變")
                    lines.append(
                        f'<p style="color:{color};">近{label}合計買賣超{current_lots:+,.0f}張，'
                        f"較前{label}（{prior_lots:+,.0f}張）{trend}——{verb}。</p>"
                    )

        return "".join(lines)

    def _margin_maintenance_analysis_html(maintenance: dict | None) -> str:
        """融資維持率分析文字，跟_render_margin_maintenance_analysis()同一套判讀
        邏輯(理論依據見該函式docstring)，這裡回傳HTML字串而不是呼叫st.markdown()。"""
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

    def _build_report_institutional_html(stock_id: str) -> str:
        """「法人買賣總覽」報表區塊：累計表格+預估持股成本表格+法人籌碼分析文字，
        照抄desktop/main_window.py的_build_overview_institutional_html()。"""
        cumulative = stock_detail_data.load_institutional_cumulative(conn, stock_id)
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
                table += f"<td align='right'>{_colored_num(cumulative[group][label] / 1000, 0, signed=True)}</td>"
            table += "</tr>"
        table += "</table>"

        cost = stock_detail_data.load_institutional_estimated_cost(conn, stock_id)
        if cost:
            cost_parts = [
                '<p style="margin-top:10px; color:#666666;">預估持股成本價（單位：元，淨賣出天期無累積部位，標示為不適用）</p>',
                '<table cellspacing="0" cellpadding="4" width="100%" border="1" bordercolor="#e0e0e0"><tr><td></td>',
            ]
            for label in periods:
                cost_parts.append(f"<td align='right'><b>{label}</b></td>")
            cost_parts.append("</tr>")
            for group in stock_detail_data.ESTIMATED_COST_GROUPS:
                cost_parts.append(f"<tr><td>{group}</td>")
                for label in periods:
                    value = cost[group][label]
                    cell = f"{value:,.2f}" if value is not None else '<span style="color:#999999;">不適用</span>'
                    cost_parts.append(f"<td align='right'>{cell}</td>")
                cost_parts.append("</tr>")
            cost_parts.append("</table>")
            table += "".join(cost_parts)

        flow = stock_detail_data.load_institutional_flow_analysis(conn, stock_id)
        momentum = stock_detail_data.load_institutional_momentum_analysis(conn, stock_id)
        return table + _institutional_flow_analysis_html(flow, momentum)

    def _build_report_margin_html(stock_id: str) -> str:
        """「資券變化總覽」報表區塊：固定顯示「當日」表格(不做當日/累計切換，靜態
        報表用當下最新狀態即可，跟頁面上「圖表」區塊的即時切換不同)+融資維持率
        分析，照抄desktop/main_window.py的_build_overview_margin_html()當日分支。"""
        maintenance = stock_detail_data.load_margin_maintenance_analysis(conn, stock_id)
        analysis_html = _margin_maintenance_analysis_html(maintenance)

        margin = stock_detail_data.load_margin_daily(conn, stock_id)
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
                f"<td align='right'>{_colored_num(r['change'], 0, signed=True)}</td>"
                f"<td align='right'>{r['balance']:,}</td>"
                f"<td align='right'>{usage_rate_cell}</td>"
                f"<td align='right'>{r['streak'] or '-'}</td></tr>"
            )
        table += "</table>"
        offset_text = f"{margin['offset_loan_and_short']:,}" if margin["offset_loan_and_short"] is not None else "-"
        ratio_text = f"{margin['short_to_margin_ratio_pct']:.2f}%" if margin["short_to_margin_ratio_pct"] is not None else "-"
        table += f"<p>資券互抵：{offset_text} 張　券資比：{ratio_text}</p>"
        return table + analysis_html

    def _build_report_dealer_html() -> str:
        """「主力進出」報表區塊，照抄desktop/main_window.py的_build_overview_dealer_html()
        ——目前沒有券商分點籌碼資料來源，維持「尚未串接資料來源」的框架提示，不假造資料。"""
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

    def _build_report_chip_html() -> str:
        """「大戶籌碼」報表區塊，照抄desktop/main_window.py的_build_overview_chip_html()。"""
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

    def _format_reference_html_as_anchors(reference: str, note_anchor_map: dict[str, str]) -> str:
        """把「原文與頁碼」文字裡的.md檔名轉成跳到附錄的PDF內部錨點連結，照抄desktop/
        main_window.py的_format_reference_html_as_anchors()——PDF匯出後沒有「開新
        視窗」這回事，筆記全文改嵌在同一份文件的附錄章節，靠錨點連結跳轉。"""
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

    def _render_rule_match_blocks_html(matches: list[dict], note_anchor_map: dict[str, str] | None = None) -> str:
        """把規則清單逐條組成HTML，照抄desktop/main_window.py的_render_rule_match_
        blocks()。每個規則block加上id="cite-{rule_id}"，供附錄裡「回引用處」連結
        回跳；note_anchor_map存在時把「原文與頁碼」轉成PDF內部錨點連結。"""
        blocks = []
        for m in matches:
            block = f"<p id=\"cite-{html.escape(m['rule_id'])}\"><b>{html.escape(m['rule_id'])}　{html.escape(m['title'])}（信心{m['confidence']}%）</b><br>"
            if m.get("note"):
                note_lines = m["note"].split("\n")
                block += f"目前狀態：{html.escape(note_lines[0])}<br>"
                for extra_line in note_lines[1:]:
                    block += f"　　{html.escape(extra_line)}<br>"
            if m.get("description"):
                block += f"分析：{html.escape(m['description'])}<br>"
            if m.get("reference"):
                reference_html = _format_reference_html_as_anchors(m["reference"], note_anchor_map or {})
                block += f"<i>原文與頁碼：{reference_html}</i>"
            block += "</p><hr>"
            blocks.append(block)
        return "".join(blocks)

    def _build_report_reference_appendix_html(matches: list[dict]) -> tuple[str, dict[str, str]]:
        """收集規則清單「原文與頁碼」實際引用到的筆記檔案(去重複，依第一次出現順序)，
        組出報表「附錄：引用筆記全文」章節，跟{筆記檔名: 錨點id}對照表(供
        _render_rule_match_blocks_html()的note_anchor_map參數)——照抄desktop/
        main_window.py的_build_report_reference_appendix()。嵌入的是使用者自己
        整理的分析筆記全文，不是書籍原文，避免版權疑慮。"""
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

    def _build_report_analysis_html(stock_id: str) -> tuple[str, str]:
        """個股分析(技術面/籌碼面規則清單+總結文字)，回傳(analysis_html,
        appendix_html)——照抄desktop/main_window.py的_build_report_html()裡組
        analysis_html/appendix_html那段，不含jumpto:///跳轉連結(PDF是一次印完的
        靜態文件，不需要頁內導覽按鈕)。"""
        price_df = load_price_history(conn, stock_id)
        trend_df = load_price_history(conn, stock_id, days=chart_data.TREND_LOOKBACK_DAYS)
        tech_matches = analyze_stock_signals(price_df, trend_df=trend_df) if not price_df.empty else []
        chip_matches = stock_detail_data.analyze_chip_signals(conn, stock_id)
        appendix_html, note_anchor_map = _build_report_reference_appendix_html([*tech_matches, *chip_matches])

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
            "<h3>技術面</h3>" + section_summary(tech_matches) + _render_rule_match_blocks_html(tech_matches, note_anchor_map)
            + "<h3>籌碼面</h3>" + section_summary(chip_matches) + _render_rule_match_blocks_html(chip_matches, note_anchor_map)
        )
        return analysis_html, appendix_html

    def _build_report_chart_image_html(stock_id: str) -> str:
        """報表用圖表：固定開啟全部均線/切線/支撐壓力/MACD/KD/SAR，不受頁面上
        「圖表」區塊目前的勾選狀態影響(報表是靜態文件，用固定的完整版本，不是
        使用者當下畫面的篩選快照)。weasyprint不執行JavaScript，不能像頁面上的
        即時圖表用chart_render.py那份含十字準星/資訊框的HTML+JS，改用kaleido
        (Plotly官方推薦的靜態圖匯出引擎)把Figure轉成PNG，base64編碼後直接嵌入
        <img>標籤——不是<iframe>。"""
        price_df = load_price_history(conn, stock_id)
        if price_df.empty:
            return "<p>查無價格資料，無法產生圖表。</p>"
        holidays, _holidays_ok = load_holidays_for_chart(price_df)
        trendlines = chart_overlays.compute_trendlines(price_df)
        all_levels = chart_overlays.compute_support_resistance_levels(price_df)
        sr_levels = chart_overlays.nearest_support_resistance(all_levels, float(price_df["close"].iloc[-1]))
        fig = build_candlestick_figure(
            price_df, holidays=holidays, ma_periods=FULL_PERIODS,
            trendlines=trendlines, show_trendline_keys=tuple(trendlines.keys()),
            sr_levels=sr_levels, show_support_resistance=True,
            show_macd=True, show_kd=True, show_sar=True,
        )
        png_bytes = fig.to_image(format="png", width=1200)
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return f'<img src="data:image/png;base64,{b64}" style="width:100%;">'

    def _build_full_report_html(stock_id: str, stock_label: str) -> str:
        """組合圖表/個股明細/個股分析/附錄成完整報表HTML文件，照抄desktop/
        main_window.py的_build_report_html()整體結構(含CSS)，差別只在圖表改用
        kaleido靜態PNG。字型堆疊多加'Noto Sans CJK TC'/'Noto Sans TC'：本機
        Windows有內建的'Microsoft JhengHei UI'，但Streamlit Cloud的Debian容器
        沒有，要靠packages.txt裝的fonts-noto-cjk套件+這個字型堆疊才能正確顯示
        中文，不然weasyprint排版出來的PDF中文字會是缺字方框。"""
        detail_builders = {
            "交易資訊": _build_report_quote_html,
            "法人買賣總覽": _build_report_institutional_html,
            "主力進出": lambda _sid: _build_report_dealer_html(),
            "資券變化總覽": _build_report_margin_html,
            "大戶籌碼": lambda _sid: _build_report_chip_html(),
        }
        detail_html = "".join(
            f"<h3>{html.escape(title)}</h3>{builder(stock_id)}" for title, builder in detail_builders.items()
        )

        analysis_html, appendix_html = _build_report_analysis_html(stock_id)
        chart_html = _build_report_chart_image_html(stock_id)

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ font-family: 'Microsoft JhengHei UI', 'Noto Sans CJK TC', 'Noto Sans TC', sans-serif; padding: 0 16px; }}
h1 {{ font-size: 20px; }}
h2 {{ font-size: 16px; border-bottom: 2px solid #2980b9; padding-bottom: 4px; margin-top: 32px; }}
h3 {{ font-size: 13px; color: #2980b9; margin-top: 20px; }}
</style></head>
<body>
<h1 id="report-top">{html.escape(stock_label)} 個股報表</h1>
<h2>圖表</h2>
{chart_html}
<h2>個股明細</h2>
{detail_html}
<h2>個股分析</h2>
{analysis_html}
{appendix_html}
</body></html>"""

    def render_stock_report_section(stock_id: str) -> None:
        """「產出報表」分頁內容，對齊桌面版「圖表→個股分析→個股明細→產出報表」的
        分頁順序(desktop/main_window.py的self.detail_inner_tabs第4個分頁)。
        weasyprint是同步阻塞呼叫，跟這個session其餘長時間操作(手動抓取今日資料/
        回補資料)同一種st.spinner作法，PDF產出通常數秒內完成(kaleido圖表轉檔
        是主要耗時來源)，不需要額外的進度條。2026-08-05拿掉開頭原本的「## 產出
        報表」標題——桌面版對應的inner tab內容本身沒有重複標題，這個函式現在是
        被包在st.tabs()的「產出報表」分頁裡呼叫。"""
        if st.button("🖨 產生PDF報表", key=f"report_pdf_btn_{stock_id}"):
            import weasyprint

            stock_name = chart_data.get_stock_name(conn, stock_id)
            stock_label = f"{stock_id} {stock_name}" if stock_name else stock_id
            with st.spinner("正在產生PDF報表..."):
                report_html = _build_full_report_html(stock_id, stock_label)
                pdf_bytes = weasyprint.HTML(string=report_html).write_pdf()
            st.download_button(
                "⬇️ 下載PDF", data=pdf_bytes, file_name=f"{stock_id}_報表.pdf",
                mime="application/pdf", key=f"report_pdf_download_{stock_id}",
            )

    def _portfolio_summary_text(df: pd.DataFrame, cost_label: str, value_label: str, profit_label: str) -> str:
        """算庫存清單／觀察清單頂部的摘要文字：總成本/總市值/累積損益(含%)/今日資產
        變動——照抄desktop/main_window.py的_portfolio_summary_text()，只加總「有算出值」
        的列(pandas sum預設skipna=True)，成本價/持股數都沒填的列本來就不計入任何一個
        加總數字，不是遺漏。總成本加上手續費加總，才會跟每一列profit/return_pct已經
        計入手續費的算法基準一致。"""
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

    def _fmt_or_dash(value, decimals: int = 0, signed: bool = False, suffix: str = "") -> str:
        """數字轉成顯示字串，None/NaN顯示"-"——⚠️ 庫存清單/觀察清單表格都要用這個
        預先把數字欄位轉成字串，不能依賴st.dataframe的column_config.NumberColumn
        自動格式化再交給Styler：實測發現一整欄全部是None的情況(例如某個觀察清單
        群組完全沒有任何一檔股票填過參考成本價)，該欄dtype會停在object而不是自動
        升級成float64+NaN，column_config的數字格式化對這種object欄位裡的None
        會直接顯示Python的"None"字面字串，不會呈現"-"。跟「個股明細」那批踩過的
        坑同一個成因(Styler+缺值儲存格的顯示邏輯有已知限制)，統一用這個函式預先
        轉成字串繞開，不依賴column_config處理缺值。"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return "-"
        sign = "+" if signed and value > 0 else ""
        return f"{sign}{value:,.{decimals}f}{suffix}"

    def _inline_field(container, label: str, render_widget, *, label_width: float = 1, widget_width: float = 2):
        """在container(st本身或某個st.columns()切出來的欄位)裡排出「label：緊湊widget」
        同一列的效果，取代Streamlit預設「label在上、widget在下」的堆疊排版。2026-08-06
        新增：使用者提供參考截圖(temp/1785930965019.jpg紅框下方的Analyzer/Group那排)
        明確要求這種排法，先前「下拉選單太寬/太高是platform限制，不做CSS」的立場是
        誤判——st.selectbox()/st.multiselect()/st.number_input()都有公開支援的width=
        參數(直接指定像素寬度)，不需要動Streamlit內部DOM class就能縮小，只是先前沒
        查到這個參數就下了結論。

        render_widget是no-arg callable，呼叫端要記得在裡面傳label_visibility=
        "collapsed"(隱藏widget自帶的label，改用這裡手動畫的文字，不會兩個label疊在
        一起)+合理的width=(px)。標籤文字用極簡的inline style div手動padding-top
        對齊widget的高度——這裡的HTML只是單純文字容器+padding，不是挖Streamlit內部
        元件的class，不受版本更新影響，跟「改內部DOM class」那種脆弱做法風險等級
        不同。checkbox不需要這個函式，checkbox的label本來就顯示在同一行右側，不是
        堆疊排版。
        """
        label_col, widget_col = container.columns([label_width, widget_width], gap="small")
        with label_col:
            st.markdown(
                f"<div style='padding-top:0.5rem; white-space:nowrap; font-size:0.875rem;'>{label}</div>",
                unsafe_allow_html=True,
            )
        with widget_col:
            return render_widget()

    def _style_name_by_listing_type_row(row: pd.Series) -> list[str]:
        """依上市/上櫃/興櫃上色「名稱」欄位——跟選股分頁candidates_df用的_style_name_
        by_listing_type()同一套邏輯，這裡獨立一份是因為欄位集合(index)不同，无法直接
        共用同一個closure(選股那份綁定在選股分頁的區塊內)。"""
        color = portfolio_data.listing_type_color(row.get("listing_type"))
        return [f"color: {color}" if col == "name" else "" for col in row.index]

    @st.dialog("庫存批次")
    def _inventory_lot_dialog(initial: dict | None) -> None:
        """新增/編輯庫存批次共用的表單——st.dialog裝飾器的title在函式定義時就固定，
        沒辦法依initial是否有值動態換標題，改成用st.subheader顯示動態標題文字。
        股票代號欄位：新增時可輸入(即時解析顯示名稱預覽)，編輯時disabled(不能改批次
        所屬股票，照抄桌面版_StockEditDialog的規則——編輯是改一筆既有批次的內容，
        不是把這筆批次移動到別檔股票)。成本價/持股數用0代表未填，照抄桌面版
        QDoubleSpinBox/QSpinBox的「0=空白」慣例。"""
        is_edit = initial is not None
        st.subheader("編輯庫存批次" if is_edit else "新增庫存批次")

        if is_edit:
            st.text_input("股票代號", value=initial["stock_id"], disabled=True)
            resolved_id = initial["stock_id"]
        else:
            query = st.text_input("股票代號或名稱", placeholder="例如 2330 或 台積電")
            resolved_id = chart_data.resolve_stock_id(conn, query) or query.strip() if query else None
            if query:
                resolved_name = chart_data.get_stock_name(conn, resolved_id) if resolved_id else None
                st.caption(f"解析為：{resolved_id} {resolved_name}" if resolved_name else "（查無此股票代號，仍可儲存）")

        # 2026-08-12新增：新增庫存批次時預設帶入今天日期(不是空白)，使用者反映多數
        # 情況買入日期就是今天，每次都要自己打很麻煩；編輯既有批次時仍照舊顯示那一筆
        # 原本存的日期，不強制蓋成今天，比照桌面版_StockEditDialog同一個判斷方式。
        default_buy_date = (initial.get("buy_date") or "") if is_edit else date.today().isoformat()
        buy_date = st.text_input("買入日期(YYYY-MM-DD)", value=default_buy_date)
        cost_price = st.number_input("成本價", min_value=0.0, step=0.01, value=float(initial.get("cost_price") or 0) if is_edit else 0.0)
        shares = st.number_input("持股數", min_value=0, step=1000, value=int(initial.get("shares") or 0) if is_edit else 0)

        # 2026-08-12改版：使用者反映系統估算的手續費可能跟券商實收不同，要求開放
        # 手動修改，不能只是唯讀顯示。key加上「這是哪一筆批次(或新增)」，避免不同
        # 批次的session_state互相污染(例如編輯A批次時手動改過的金額，被拿去當B
        # 批次的預設值)。
        #
        # ⚠️ 用一個明確的「已鎖定」布林旗標(_fee_locked_key)、而不是單純比較「上一次
        # 估算值」，是實測踩過坑才改對的：一開始只成本價填了、股數還沒填時estimate_
        # buy_fee()回傳None(target視為0)，session_state[_fee_key]被初始化成0；等
        # 股數也填好、估算值第一次從None變成一個真正的數字(例如43)時，如果只比較
        # 「這次估算值跟上次估算值(None)是否都不是None」，這個從無到有的第一次更新
        # 會被誤判成不該套用，導致欄位卡在0、白白算出估算值卻顯示不出來(截圖實測
        # 發現的真bug)。改成明確追蹤「目前顯示的值是不是等於上一次我們自己填入的
        # 值(_fee_auto_value_key)」，只要沒被使用者改動過就持續跟隨最新估算值，
        # 這裡不會有None/0混用比較造成的邊界問題。編輯模式一開始就直接鎖定(視為
        # 已確認)，不會被成本價/股數變動覆蓋，跟桌面版_StockEditDialog的判斷一致。
        fee_estimate = portfolio_data.estimate_buy_fee(cost_price or None, shares or None)
        target_fee = int(fee_estimate) if fee_estimate is not None else 0
        _dialog_instance_key = f"lot_{initial['id']}" if is_edit else "new_lot"
        _fee_key = f"inventory_lot_dialog_fee_{_dialog_instance_key}"
        _fee_locked_key = f"inventory_lot_dialog_fee_locked_{_dialog_instance_key}"
        _fee_auto_value_key = f"inventory_lot_dialog_fee_auto_{_dialog_instance_key}"
        if _fee_key not in st.session_state:
            if is_edit:
                st.session_state[_fee_key] = int(initial.get("fee") or 0)
                st.session_state[_fee_locked_key] = True
            else:
                st.session_state[_fee_key] = target_fee
                st.session_state[_fee_locked_key] = False
        elif not st.session_state[_fee_locked_key]:
            if st.session_state[_fee_key] != st.session_state.get(_fee_auto_value_key):
                st.session_state[_fee_locked_key] = True  # 使用者上次rerun之間手動改過
            elif target_fee != st.session_state.get(_fee_auto_value_key):
                st.session_state[_fee_key] = target_fee
        st.session_state[_fee_auto_value_key] = target_fee

        fee = st.number_input("買入手續費", min_value=0, step=1, key=_fee_key)
        st.caption(
            f"系統估算：{fee_estimate:,} 元（依成本價×股數計算，可能跟券商實收不同，可手動修改）"
            if fee_estimate is not None else "（填成本價與持股數後自動估算，可再手動修改）"
        )
        note = st.text_input("備註", value=initial.get("note") or "" if is_edit else "")

        if st.button("確認", key="inventory_lot_dialog_confirm"):
            if not resolved_id:
                st.warning("請輸入股票代號。")
                return
            # 成本價/股數/手續費都是空的(0)時視為None，跟舊版estimate_buy_fee()兩者
            # 缺一就回傳None的語意一致，避免存進一筆意義不明的「0元手續費」。
            fee_to_save = fee if (cost_price or shares or fee) else None
            if is_edit:
                portfolio_storage.update_inventory_stock(
                    portfolio_conn, initial["id"], buy_date or None, cost_price or None, shares or None, fee_to_save, note,
                )
            else:
                portfolio_storage.add_inventory_stock(
                    portfolio_conn, resolved_id, buy_date or None, cost_price or None, shares or None, fee_to_save, note,
                )
            st.rerun()

    @st.dialog("加入觀察清單")
    def _watchlist_group_picker_dialog(stock_ids: list[str]) -> None:
        """勾選要加入的觀察清單群組——跟桌面版_add_stocks_to_watchlist_via_dialog()
        同一個功能，這個dialog之後觀察清單分頁本身/選股分頁批次動作也會重用。"""
        groups = portfolio_storage.list_watchlist_groups(portfolio_conn)
        if not groups:
            st.info("目前沒有任何觀察清單群組，請先到「觀察清單」分頁建立群組。")
            return
        selected_ids = [g["id"] for g in groups if st.checkbox(g["group_name"], key=f"watchlist_group_{g['id']}")]
        if st.button("確認加入", key="watchlist_group_picker_confirm"):
            if not selected_ids:
                st.warning("請至少勾選一個群組。")
                return
            portfolio_storage.add_stocks_to_watchlist(portfolio_conn, selected_ids, stock_ids)
            st.success(f"已加入{len(stock_ids)}檔股票到{len(selected_ids)}個群組。")
            st.rerun()

    def render_inventory_tab() -> None:
        """「庫存清單」分頁：使用者實際持有的股票，記錄成本價/持股數/手續費，算浮動
        損益。桌面版用QTreeWidget做master-detail(父列=每檔股票的加權平均彙總，子列=
        個別買入批次)，Streamlit沒有對應的樹狀元件，改成兩層表格：彙總表格(一列一檔
        股票)可點選一列，選中後下方顯示該股票的批次明細表格(一列一筆批次，原生多選
        用於批次刪除)。底層資料函式(portfolio_data.load_inventory_summary()/
        load_inventory_lots())跟桌面版共用，未改動。
        """
        lots_df = portfolio_data.load_inventory_lots(conn, portfolio_conn)
        summary_df = portfolio_data.load_inventory_summary(conn, portfolio_conn)

        st.caption(_portfolio_summary_text(lots_df, "總持股成本", "總市值", "累積總損益") if not lots_df.empty else "尚無庫存資料。")

        if st.button("➕ 新增批次"):
            _inventory_lot_dialog(None)

        if summary_df.empty:
            st.info("目前沒有任何庫存股票，點上方「➕ 新增批次」開始記錄。")
            return

        st.subheader("庫存總覽")
        # ⚠️ 數字欄位先轉成「已格式化好的字串」("-"代表缺值)，不依賴column_config.
        # NumberColumn自動格式化——見_fmt_or_dash()的說明，一整欄全部是None時
        # column_config對object dtype的None會顯示"None"字面字串。summary_df(含
        # listing_type等其他欄位)保留給後面selection查詢用，只有display版本套用
        # 字串轉換。
        summary_display = summary_df.copy()
        summary_display["close"] = summary_df["close"].apply(lambda v: _fmt_or_dash(v, 2))
        summary_display["pct_change"] = summary_df["pct_change"].apply(lambda v: _fmt_or_dash(v, 2, suffix="%"))
        summary_display["cost_price"] = summary_df["cost_price"].apply(lambda v: _fmt_or_dash(v, 2))
        summary_display["shares"] = summary_df["shares"].apply(lambda v: _fmt_or_dash(v, 0))
        summary_display["market_value"] = summary_df["market_value"].apply(lambda v: _fmt_or_dash(v, 0))
        summary_display["profit"] = summary_df["profit"].apply(lambda v: _fmt_or_dash(v, 0, signed=True))
        summary_display["return_pct"] = summary_df["return_pct"].apply(lambda v: _fmt_or_dash(v, 2, signed=True, suffix="%"))
        summary_display["sar_distance_pct"] = summary_df["sar_distance_pct"].apply(lambda v: _fmt_or_dash(v, 2, suffix="%"))
        summary_display["lot_count"] = summary_df["lot_count"].apply(lambda v: _fmt_or_dash(v, 0))
        # 2026-08-07新增：selection_mode混用single-row(點列選取，展開下方批次明細，
        # 既有行為不變)+single-cell(判斷有沒有點到「名稱」欄，點到就跳轉個股資訊)，
        # 邏輯跟選股分頁候選清單表格一致(見TAB_SCREENER分支的說明)。點「名稱」欄
        # 同時也會選到那一列(single-row)，但下面就直接rerun跳轉分頁了，不會真的
        # 顯示一次批次明細再跳走。
        st.caption("點列可展開下方批次明細；點「名稱」欄可直接查看個股資訊")
        summary_event = st.dataframe(
            summary_display.style.apply(_style_name_by_listing_type_row, axis=1),
            use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode=["single-row", "single-cell"], key="inventory_summary_table",
            column_order=["stock_id", "name", "close", "pct_change", "cost_price", "shares", "market_value", "profit", "return_pct", "sar_status", "sar_distance_pct", "lot_count"],
            column_config={
                "stock_id": "股票代號", "name": "名稱", "close": "現價", "pct_change": "漲跌幅(%)",
                "cost_price": "成本價", "shares": "持股數", "market_value": "市值",
                "profit": "帳面損益", "return_pct": "報酬率(%)", "sar_status": "SAR狀態",
                "sar_distance_pct": "SAR距離%", "lot_count": "批次數",
            },
        )
        name_clicked_row = next(
            (row for row, col in summary_event.selection.cells if col == "name"), None,
        )
        if name_clicked_row is not None:
            stock_id = str(summary_df.iloc[name_clicked_row]["stock_id"])
            st.session_state["detail_stock_id"] = stock_id
            st.session_state["detail_stock_source"] = None
            # 2026-08-12修正：改成把搜尋框設成股票代號本身(不是清空)，同時同步
            # _detail_query_last_seen，讓「個股資訊」分頁的搜尋框能直接顯示現在
            # 看的是哪一檔，不會空白造成混淆——見TAB_STOCK_DETAIL分支的完整說明。
            st.session_state["detail_query_input"] = stock_id
            st.session_state["_detail_query_last_seen"] = stock_id
            st.session_state["_pending_active_tab"] = TAB_STOCK_DETAIL
            st.session_state["_pending_inventory_summary_table_reset"] = True
            st.rerun()

        if not summary_event.selection.rows:
            return
        selected_stock_id = str(summary_df.iloc[summary_event.selection.rows[0]]["stock_id"])
        selected_name = summary_df.iloc[summary_event.selection.rows[0]]["name"]

        st.subheader(f"批次明細：{selected_stock_id} {selected_name}")
        if st.button("➕ 加入觀察清單", key="inventory_add_to_watchlist"):
            _watchlist_group_picker_dialog([selected_stock_id])

        stock_lots_df = lots_df[lots_df["stock_id"] == selected_stock_id].reset_index(drop=True)
        lots_display = stock_lots_df.copy()
        lots_display["cost_price"] = stock_lots_df["cost_price"].apply(lambda v: _fmt_or_dash(v, 2))
        lots_display["shares"] = stock_lots_df["shares"].apply(lambda v: _fmt_or_dash(v, 0))
        lots_display["fee"] = stock_lots_df["fee"].apply(lambda v: _fmt_or_dash(v, 0))
        lots_display["market_value"] = stock_lots_df["market_value"].apply(lambda v: _fmt_or_dash(v, 0))
        lots_display["profit"] = stock_lots_df["profit"].apply(lambda v: _fmt_or_dash(v, 0, signed=True))
        lots_display["return_pct"] = stock_lots_df["return_pct"].apply(lambda v: _fmt_or_dash(v, 2, signed=True, suffix="%"))
        lots_event = st.dataframe(
            lots_display, use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="multi-row", key="inventory_lots_table",
            column_order=["buy_date", "cost_price", "shares", "fee", "market_value", "profit", "return_pct", "note"],
            column_config={
                "buy_date": "買入日期", "cost_price": "成本價", "shares": "持股數",
                "fee": "手續費", "market_value": "市值", "profit": "帳面損益", "return_pct": "報酬率(%)",
                "note": "備註",
            },
        )
        selected_lot_rows = lots_event.selection.rows

        edit_col, delete_col = st.columns([1, 1])
        with edit_col:
            if st.button("✏️ 編輯選取批次"):
                if len(selected_lot_rows) != 1:
                    st.warning("請選取剛好一筆批次再編輯。")
                else:
                    lot_id = int(stock_lots_df.iloc[selected_lot_rows[0]]["id"])
                    _inventory_lot_dialog(portfolio_storage.get_inventory_lot(portfolio_conn, lot_id))
        with delete_col:
            if st.button("🗑️ 刪除選取批次"):
                if not selected_lot_rows:
                    st.warning("請至少選取一筆批次再刪除。")
                else:
                    st.session_state["pending_delete_lot_ids"] = [int(stock_lots_df.iloc[i]["id"]) for i in selected_lot_rows]

        # 兩段式確認刪除(跟桌面版QMessageBox.question的精神一致，避免誤刪)：
        # 上面「刪除選取批次」按鈕只記錄「打算刪除哪些id」，這裡才是真正執行刪除，
        # 使用者要再點一次「確認刪除」才會真的動到DB。
        pending_ids = st.session_state.get("pending_delete_lot_ids")
        if pending_ids:
            st.warning(f"確定要刪除{len(pending_ids)}筆批次嗎？此動作無法復原。")
            confirm_col, cancel_col = st.columns([1, 1])
            with confirm_col:
                if st.button("確認刪除", key="inventory_delete_confirm"):
                    for lot_id in pending_ids:
                        portfolio_storage.delete_inventory_stock(portfolio_conn, lot_id)
                    st.session_state["pending_delete_lot_ids"] = None
                    st.rerun()
            with cancel_col:
                if st.button("取消", key="inventory_delete_cancel"):
                    st.session_state["pending_delete_lot_ids"] = None
                    st.rerun()

    @st.dialog("新增觀察股票")
    def _watchlist_stock_dialog(group_id: int) -> None:
        """新增觀察清單股票——跟_inventory_lot_dialog()同一個結構，但沒有買入日期/
        預估手續費欄位(觀察清單不是真的持股，add_watchlist_stock()本來就沒有這兩個
        參數)。2026-08-06拿掉參考成本價/參考股數輸入框(比照desktop/main_window.py的
        _StockEditDialog同一天的改版)：使用者反映觀察清單不是真的持股，這兩個欄位、
        連同表格上衍生出來的市值/帳面損益/報酬率欄位放在這裡沒有意義；add_watchlist_
        stock()的cost_price/shares參數本來就是optional，這裡固定傳None，不需要改
        DB層。2026-08-07拿掉「編輯選取」(使用者反映沒有作用、要求移除)後，這個dialog
        只剩新增用途，不再需要is_edit分支。
        """
        query = st.text_input("股票代號或名稱", placeholder="例如 2330 或 台積電")
        resolved_id = chart_data.resolve_stock_id(conn, query) or query.strip() if query else None
        if query:
            resolved_name = chart_data.get_stock_name(conn, resolved_id) if resolved_id else None
            st.caption(f"解析為：{resolved_id} {resolved_name}" if resolved_name else "（查無此股票代號，仍可儲存）")

        note = st.text_input("備註", value="")

        if st.button("確認", key="watchlist_stock_dialog_confirm"):
            if not resolved_id:
                st.warning("請輸入股票代號。")
                return
            portfolio_storage.add_watchlist_stock(portfolio_conn, group_id, resolved_id, None, None, note)
            st.rerun()

    @st.dialog("觀察清單群組")
    def _watchlist_group_name_dialog(mode: str, group_id: int | None, current_name: str) -> None:
        """新增/重新命名群組共用的表單。群組名稱有DB層唯一約束，重複時捕捉
        sqlite3.IntegrityError顯示錯誤訊息、不關閉dialog，讓使用者原地修改重試
        (跟桌面版QMessageBox.warning後dialog繼續開著的行為一致)。"""
        st.subheader("新增群組" if mode == "add" else "重新命名群組")
        name = st.text_input("群組名稱", value=current_name)
        if st.button("確認", key="watchlist_group_name_dialog_confirm"):
            name = name.strip()
            if not name:
                st.warning("請輸入群組名稱。")
                return
            try:
                if mode == "add":
                    portfolio_storage.add_watchlist_group(portfolio_conn, name)
                else:
                    portfolio_storage.rename_watchlist_group(portfolio_conn, group_id, name)
            except sqlite3.IntegrityError:
                st.error("群組名稱重複，請換一個名稱。")
                return
            st.rerun()

    def render_watchlist_tab() -> None:
        """「觀察清單」分頁：想追蹤但還沒買的股票，支援多個群組，額外接上黃豐凱籌碼
        分析法14個欄位。跟桌面版desktop/main_window.py的_build_watchlist_tab()比，
        這次刻意簡化3點(理由見ai/PLAN.md對應批次的紀錄)：①欄位顯示/隱藏改用
        Streamlit原生的表格欄位右鍵選單，不自己刻下拉選單；②不做雙列表頭的4色分類
        標籤(Streamlit表格不支援分組表頭)；③黃豐凱籌碼欄位只對「法人買賣超（張數）」
        8個數字欄位依正負值上紅/綠色，其餘6個文字欄位(投信/外資/大戶散戶週變化/
        均線狀態/週K型態)只顯示文字，不逐儲存格套用桌面版那種每列各自不同的自訂
        顏色。也不做F/G背景自動補抓(Streamlit沒有背景執行緒模型，且目前排程本來就
        暫停中，新股票的F/G本來就要等手動觸發或排程恢復)。

        2026-08-06拿掉參考成本價/參考股數/市值/帳面損益/報酬率這5欄(連同上方的
        「總參考成本/總觀察市值/累積預估損益」摘要文字、新增/編輯對話框的輸入框，
        比照桌面版desktop/main_window.py同一天的改版)：使用者反映觀察清單不是真的
        持股，這些欄位放在這裡沒有意義。
        """
        groups = portfolio_storage.list_watchlist_groups(portfolio_conn)
        if not groups:
            portfolio_storage.add_watchlist_group(portfolio_conn, "預設觀察清單")
            groups = portfolio_storage.list_watchlist_groups(portfolio_conn)

        # ⚠️ 2026-08-07修正：原本selectbox用options=group_names(字串)+key=自動追蹤
        # 選取狀態，實測發現重新命名後「切到別的分頁再切回來」選取會跑掉——這個分頁
        # 是用`if tab_watchlist.open: render_watchlist_tab()`包起來的(見下方st.tabs()
        # 呼叫處)，切到別的分頁時這段程式碼整個不會執行，selectbox這個widget那一輪
        # 沒有被instantiate，Streamlit的key自動管理的session_state會被重置，切回來
        # 時前一次的選取狀態就丟失了(不是只有改名字串對不上這麼單純，用group_id當
        # options一樣會遇到，已經實測確認過)。真正的解法是自己另外用一個「不綁定任何
        # widget」的session_state key手動記住目前選到哪個群組(watchlist_selected_
        # group_id，不用selectbox的key=自動管理)，每次進來都用它算出index=要預選第
        # 幾個選項、每次使用者互動完也手動把最新選取寫回去——這個key是plain data，
        # 不會因為widget那一輪沒被instantiate就被清掉，才能撐過「切到別的分頁」這種
        # 空窗期。找不到(群組被刪除等情況)才退回index=0。
        group_name_by_id = {g["id"]: g["group_name"] for g in groups}
        group_ids = list(group_name_by_id.keys())
        remembered_group_id = st.session_state.get("watchlist_selected_group_id")
        default_index = group_ids.index(remembered_group_id) if remembered_group_id in group_ids else 0
        # 2026-08-07改版：label跟下拉改成同一列的緊湊排法(見_inline_field()的說明，
        # 跟選股/產業輪動分頁同一套做法)，取代原本label在上、下拉在下的堆疊排版。
        group_id = _inline_field(
            st, "群組",
            lambda: st.selectbox(
                "群組", group_ids, index=default_index,
                format_func=lambda gid: group_name_by_id.get(gid, str(gid)),
                label_visibility="collapsed", width=250,
            ),
            label_width=0.5, widget_width=3,
        )
        st.session_state["watchlist_selected_group_id"] = group_id
        selected_group_name = group_name_by_id[group_id]

        # 2026-08-07改版：按鈕改成窄欄位+吸收剩餘寬度的spacer_col，取代原本st.columns
        # ([1,1,1])平分寬度的寫法——比照候選清單/觀察清單股票列那批「按鈕靠左排列」
        # 的既有做法，讓按鈕緊貼在左側，不要被平分寬度拉開。
        group_col1, group_col2, group_col3, group_spacer = st.columns([1.2, 1.6, 1.2, 4])
        with group_col1:
            if st.button("➕ 新增群組"):
                _watchlist_group_name_dialog("add", None, "")
        with group_col2:
            if st.button("✏️ 重新命名群組"):
                _watchlist_group_name_dialog("rename", group_id, selected_group_name)
        with group_col3:
            if st.button("🗑️ 刪除群組"):
                st.session_state["pending_delete_group_id"] = group_id

        pending_group_id = st.session_state.get("pending_delete_group_id")
        if pending_group_id:
            st.warning(f"確定要刪除群組「{selected_group_name}」嗎？裡面的股票也會一併刪除，此動作無法復原。")
            confirm_col, cancel_col = st.columns([1, 1])
            with confirm_col:
                if st.button("確認刪除群組", key="watchlist_group_delete_confirm"):
                    portfolio_storage.delete_watchlist_group(portfolio_conn, pending_group_id)
                    st.session_state["pending_delete_group_id"] = None
                    st.rerun()
            with cancel_col:
                if st.button("取消", key="watchlist_group_delete_cancel"):
                    st.session_state["pending_delete_group_id"] = None
                    st.rerun()
            # ⚠️ 2026-08-07修正：使用者反映「群組是空的時候按刪除群組，卻跳出『請至少
            # 選取一檔股票再刪除』」——實測過刪除邏輯本身沒問題(空群組確認後真的能刪掉)，
            # 但原本這裡沒有return，「確認刪除群組」/「取消」按鈕會緊接著下面「新增股票」/
            # 「刪除選取」那排一起顯示，兩排視覺上太靠近、「確認刪除群組」還跟「新增股票」
            # 同一欄對齊，很容易誤點成「刪除選取」(空群組沒有任何列可選，點下去自然跳出
            # 「請至少選取一檔股票再刪除」)。改成確認中就return，強制先處理完刪除群組的
            # 確認/取消，不會同時看到容易混淆的兩排按鈕。
            return

        watchlist_df = portfolio_data.load_watchlist(conn, portfolio_conn, group_id)

        # 讀「上一輪」的selection狀態(比照候選清單「加入庫存/加入觀察清單」批次按鈕的
        # 做法)：這一輪script重新執行時，使用者剛做的表格勾選已經先寫進session_state了，
        # 比下面真正呼叫st.dataframe()還早，這樣「編輯選取」/「刪除選取」按鈕才能畫在
        # 表格「上面」、緊貼著「新增股票」，卻還是讀得到目前的選取狀態。
        prior_selection_rows = st.session_state.get("watchlist_table", {}).get("selection", {}).get("rows", [])
        prior_selection_rows = [i for i in prior_selection_rows if i < len(watchlist_df)]

        # 「資料更新至」：2026-08-05新增，比照桌面版desktop/main_window.py的
        # watchlist_update_label(_build_watchlist_tab()，工具列最右邊)——先前web版
        # 這個分頁完全沒有這個時間戳，屬於漏做的部分，不是刻意簡化(跟同一批次
        # 刻意簡化的①欄位顯示選單②雙列表頭③籌碼欄位逐格套色三點不同)。
        # 2026-08-07拿掉「編輯選取」(使用者反映沒有作用、要求移除，桌面版沒有這個
        # 問題保留不動)。按鈕維持窄欄位+吸收剩餘寬度的spacer_col靠左排列。
        add_col, delete_col, spacer_col, update_col = st.columns([1, 1, 1, 3])
        with add_col:
            if st.button("➕ 新增股票", key="watchlist_add_stock"):
                _watchlist_stock_dialog(group_id)
        with delete_col:
            if st.button("🗑️ 刪除選取"):
                if not prior_selection_rows:
                    st.warning("請至少選取一檔股票再刪除。")
                else:
                    st.session_state["pending_delete_watchlist_stock_ids"] = [
                        str(watchlist_df.iloc[i]["stock_id"]) for i in prior_selection_rows
                    ]
        with update_col:
            update_ts = get_latest_update_time(conn)
            update_label = datetime.fromisoformat(update_ts).strftime("%Y-%m-%d %H:%M") if update_ts else "尚無資料"
            st.caption(f"資料更新至　{update_label}")

        if watchlist_df.empty:
            st.info("這個群組還沒有任何股票，點上方「➕ 新增股票」開始追蹤。")
            st.caption("ps: 大戶/散戶持股變化僅支持觀察清單")
            return

        # 批次查詢黃豐凱籌碼分析法欄位(2026-08-07改批次：原本逐股呼叫load_huang_chip_row()
        # 是N檔股票=9N條依序SQL，本機sqlite感覺不出來，但web版部署到雲端連的是Turso，
        # 每條SQL都是一次網路來回，切換觀察清單群組因此變得很慢，診斷過程見ai/PLAN.md)。
        # label-dict欄位/流量欄位都在建DataFrame前就轉成「已格式化好的字串」("-"或
        # +/-千分位數字字串)，不留原始None/NaN——跟「個股明細」那批踩過的坑一樣，
        # st.dataframe搭配Styler對None/NaN儲存格會顯示"None"字面字串，不會套用自訂
        # 格式化邏輯。
        chip_rows_by_stock = huang_chip_data.load_huang_chip_rows_batch(conn, list(watchlist_df["stock_id"]))
        chip_rows = []
        for stock_id in watchlist_df["stock_id"]:
            chip = chip_rows_by_stock[stock_id]
            flow = chip.get("flow") or {}
            ma = chip.get("ma_price_position")
            weekly = chip.get("weekly_volume_pattern")
            holder = chip.get("holder_change") or {}
            row = {
                "invest_streak": (chip.get("invest_streak") or {}).get("text", "-"),
                "foreign_streak": (chip.get("foreign_streak") or {}).get("text", "-"),
                "holder_whale": (holder.get("whale") or {}).get("text", "-"),
                "holder_retail": (holder.get("retail") or {}).get("text", "-"),
                "ma_price_position": "\n".join(line["text"] for line in ma["lines"]) if ma else "-",
                "weekly_volume_pattern": f"{weekly['pattern']}\n（{weekly['reference_week_start']}）" if weekly else "-",
            }
            for key in _HUANG_CHIP_FLOW_COLUMNS:
                value = flow.get(key)
                row[key] = f"{value:+,.0f}" if value is not None else "-"
            chip_rows.append(row)
        chip_df = pd.DataFrame(chip_rows, columns=_HUANG_CHIP_HEADERS)
        watchlist_df = pd.concat([watchlist_df.reset_index(drop=True), chip_df], axis=1)

        # ⚠️ 數字欄位先轉成「已格式化好的字串」("-"代表缺值)，不依賴column_config.
        # NumberColumn自動格式化(理由見_fmt_or_dash()的說明)。watchlist_df保留原始
        # 數值給上面「編輯選取」/「刪除選取」查詢用(編輯dialog需要真的數字才能帶入
        # number_input預設值)，只有watchlist_display套用字串轉換、傳給st.dataframe。
        watchlist_display = watchlist_df.copy()
        watchlist_display["close"] = watchlist_df["close"].apply(lambda v: _fmt_or_dash(v, 2))
        watchlist_display["pct_change"] = watchlist_df["pct_change"].apply(lambda v: _fmt_or_dash(v, 2, suffix="%"))
        watchlist_display["sar_distance_pct"] = watchlist_df["sar_distance_pct"].apply(lambda v: _fmt_or_dash(v, 2, suffix="%"))

        def _style_watchlist_row(row: pd.Series) -> list[str]:
            styles = []
            name_color = portfolio_data.listing_type_color(row.get("listing_type"))
            for col in row.index:
                if col == "name":
                    styles.append(f"color: {name_color}")
                elif col in _HUANG_CHIP_FLOW_COLUMNS:
                    text = str(row[col])
                    if text.startswith("-"):
                        styles.append(f"color: {COLOR_SELL}")
                    elif text.startswith("+") and not text.startswith("+0"):
                        styles.append(f"color: {COLOR_BUY}")
                    else:
                        styles.append("")
                else:
                    styles.append("")
            return styles

        # 2026-08-07新增：跟選股分頁的候選清單表格同一套做法(見TAB_SCREENER分支的
        # 說明)——selection_mode混用multi-row(勾選欄，「刪除選取」用)+single-cell
        # (判斷有沒有點到「名稱」欄，點到就跳轉個股資訊)，兩者各自獨立、互不干擾。
        st.caption("勾選左側checkbox可「刪除選取」；點「名稱」欄可直接查看個股資訊")
        watchlist_event = st.dataframe(
            watchlist_display.style.apply(_style_watchlist_row, axis=1),
            use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode=["multi-row", "single-cell"], key="watchlist_table",
            column_order=["stock_id", "name", "close", "pct_change", "sar_status", "sar_distance_pct"] + _HUANG_CHIP_HEADERS,
            column_config={
                "stock_id": "股票代號", "name": "名稱", "close": "現價", "pct_change": "漲跌幅(%)",
                "sar_status": "SAR狀態", "sar_distance_pct": "SAR距離%",
                **{key: label for key, label in _HUANG_CHIP_LABELS.items()},
            },
        )
        # 點「名稱」欄的儲存格就跳轉個股資訊，邏輯跟候選清單一致(見上面TAB_SCREENER
        # 分支的詳細說明)：從觀察清單點過來不是候選清單策略，detail_stock_source
        # 留None，跟手動查詢一樣不顯示「來源：X月X日的選股策略」標籤。
        name_clicked_row = next(
            (row for row, col in watchlist_event.selection.cells if col == "name"), None,
        )
        if name_clicked_row is not None:
            stock_id = str(watchlist_df.iloc[name_clicked_row]["stock_id"])
            st.session_state["detail_stock_id"] = stock_id
            st.session_state["detail_stock_source"] = None
            # 2026-08-12修正：搜尋框同步顯示股票代號，見inventory_summary_table
            # 那處(TAB_INVENTORY分支)的完整說明。
            st.session_state["detail_query_input"] = stock_id
            st.session_state["_detail_query_last_seen"] = stock_id
            st.session_state["_pending_active_tab"] = TAB_STOCK_DETAIL
            st.session_state["_pending_watchlist_table_reset"] = True
            st.rerun()

        pending_stock_ids = st.session_state.get("pending_delete_watchlist_stock_ids")
        if pending_stock_ids:
            st.warning(f"確定要刪除{len(pending_stock_ids)}檔股票嗎？此動作無法復原。")
            confirm_col, cancel_col = st.columns([1, 1])
            with confirm_col:
                if st.button("確認刪除", key="watchlist_delete_confirm"):
                    for stock_id in pending_stock_ids:
                        portfolio_storage.delete_watchlist_stock(portfolio_conn, group_id, stock_id)
                    st.session_state["pending_delete_watchlist_stock_ids"] = None
                    st.rerun()
            with cancel_col:
                if st.button("取消", key="watchlist_delete_cancel"):
                    st.session_state["pending_delete_watchlist_stock_ids"] = None
                    st.rerun()

        st.caption("ps: 大戶/散戶持股變化僅支持觀察清單")

    def _run_backfill(params: dict, attempt_id: int) -> None:
        """實際執行回補寫入，呼叫順序照抄桌面版desktop/main_window.py的
        BackfillWorker.run()：大盤價→(TWSE/TPEx分流)個股價/法人/資券→(股價有補到
        才需要)指標重算→(打勾才需要)候選清單重算。跟desktop不同的是這裡沒有背景
        執行緒，整個function同步阻塞執行到完成，也不支援中途取消。不論成功/失敗都
        會呼叫record_attempt_result()，讓冷卻時間確實算數(見src/data/
        admin_action_rate_limit.py的模組docstring)。
        """
        summary: dict = {}
        progress_bar = st.progress(0.0, text="準備開始...")
        try:
            with st.spinner("正在回補資料..."):
                start, end = params["start"], params["end"]
                force_overwrite = params["force_overwrite"]
                stock_id_filter = set(params["stock_id_filter"])

                if params["taiex_price"]:
                    progress_bar.progress(0.05, text="回補大盤股價中...")
                    written = backfill_taiex_range(conn, start, end, force_overwrite=force_overwrite)
                    summary["taiex_dates"] = len(written)

                any_stock_item = params["stock_price"] or params["stock_institutional"] or params["stock_margin"]
                affected_stock_ids: set[str] = set()
                if any_stock_item:
                    known = {
                        r[0]: {"stock_id": r[0], "name": r[1], "industry": r[2], "market": r[3]}
                        for r in conn.execute(
                            "SELECT stock_id, name, industry, market FROM stocks WHERE market IN ('TWSE', 'TPEx')"
                        ).fetchall()
                    }
                    unknown = stock_id_filter - known.keys()
                    if unknown:
                        st.warning(f"以下股票代號查無資料，已略過：{', '.join(sorted(unknown))}")
                    scope_rows = [known[sid] for sid in stock_id_filter if sid in known]
                    affected_stock_ids = {r["stock_id"] for r in scope_rows}
                    twse_ids = {r["stock_id"] for r in scope_rows if r["market"] == "TWSE"}
                    tpex_rows = [r for r in scope_rows if r["market"] == "TPEx"]

                    if twse_ids:
                        progress_bar.progress(0.2, text="回補TWSE個股資料中...")

                        def _twse_progress(done: int, total: int) -> None:
                            progress_bar.progress(
                                0.2 + 0.3 * (done / total if total else 0), text=f"TWSE回補中...{done}/{total}天",
                            )

                        backfill_history.backfill_twse(
                            conn, date.fromisoformat(start), date.fromisoformat(end),
                            include_price=params["stock_price"], include_institutional=params["stock_institutional"],
                            include_margin=params["stock_margin"], force_overwrite=force_overwrite,
                            stock_id_filter=twse_ids, on_progress=_twse_progress,
                        )

                    if tpex_rows:
                        progress_bar.progress(0.5, text="回補TPEx個股資料中...")

                        def _tpex_progress(done: int, total: int) -> None:
                            progress_bar.progress(
                                0.5 + 0.3 * (done / total if total else 0), text=f"TPEx回補中...{done}/{total}檔",
                            )

                        backfill_history.backfill_tpex(
                            conn, tpex_rows, date.fromisoformat(start), date.fromisoformat(end),
                            include_price=params["stock_price"], include_institutional=params["stock_institutional"],
                            include_margin=params["stock_margin"], force_overwrite=force_overwrite,
                            on_progress=_tpex_progress,
                        )

                if params["stock_price"] and any_stock_item and affected_stock_ids:
                    progress_bar.progress(0.85, text="重算均線/SAR快取中...")
                    n = recompute_indicators_for_range(conn, list(affected_stock_ids), start, end)
                    summary["indicators"] = n
                    if params["recompute_candidates"]:
                        def _candidates_progress(done: int, total: int) -> None:
                            progress_bar.progress(
                                0.9 + 0.1 * (done / total if total else 0), text=f"候選清單重算中...{done}/{total}天",
                            )

                        n = run_screen_and_store_for_range(
                            conn, list(affected_stock_ids), start, end, on_progress=_candidates_progress,
                        )
                        summary["candidates"] = n

            progress_bar.progress(1.0, text="完成")
            admin_action_rate_limit.record_attempt_result(conn, attempt_id, "done", summary)
            parts = []
            if "taiex_dates" in summary:
                parts.append(f"大盤{summary['taiex_dates']}天")
            if "indicators" in summary:
                parts.append(f"指標{summary['indicators']}筆")
            if "candidates" in summary:
                parts.append(f"歷史候選清單{summary['candidates']}筆")
            detail = "、".join(parts) if parts else "沒有新資料寫入"
            st.success(f"回補完成：{detail}")
        except Exception as exc:  # noqa: BLE001
            admin_action_rate_limit.record_attempt_result(conn, attempt_id, "failed", {"error": str(exc)})
            st.error(f"回補失敗：{exc}")
        finally:
            progress_bar.empty()
        st.rerun()

    def render_backfill_tab() -> None:
        """「回補資料」分頁：跟桌面版desktop/main_window.py的_build_backfill_tab()/
        BackfillWorker共用同一組底層回補函式，但web版額外加上Turso額度保護
        (ai/PLAN.md第9批)——桌面版一律寫本機sqlite沒有這個風險，完全不受這裡的限制。
        規則(顯示在頁面上，不是只有後端悄悄擋)：①只能指定股票代號清單，不開放
        「全市場」；②單次日期區間有上限；③按下「確認送出」執行寫入時需要密碼；
        ④不論成功/失敗，完成後有冷卻時間內不能再次觸發。
        """
        st.info(
            "⚠️ 這個功能會對主資料庫的Turso帳號做批次寫入，過去發生過額度用完被封鎖的"
            "事故，因此web版加上以下限制(桌面版不受影響，可以自由使用)：\n"
            "- 只能指定股票代號清單，不開放「全市場」範圍\n"
            f"- 單次日期區間最多{BACKFILL_MAX_RANGE_DAYS}天\n"
            "- 按下「確認送出，開始回補」實際執行寫入時需要輸入存取密碼\n"
            f"- 不論這次成功或失敗，完成後{BACKFILL_COOLDOWN_SECONDS // 3600}小時內"
            "都不能再次觸發"
        )

        access_code = get_admin_access_code()
        if access_code is None:
            st.warning("尚未設定 ADMIN_ACCESS_CODE，此功能已停用。")
            return

        remaining = admin_action_rate_limit.seconds_until_next_allowed(conn, "backfill", BACKFILL_COOLDOWN_SECONDS)
        if remaining > 0:
            hours, rem = divmod(int(remaining), 3600)
            minutes = rem // 60
            st.warning(f"距離下次可以回補還要等 {hours} 小時 {minutes} 分鐘。")
            last = admin_action_rate_limit.get_last_attempt(conn, "backfill")
            if last:
                result_text = f"，結果：{last['result']}" if last.get("result") else ""
                st.caption(f"上一次嘗試：{last['started_at']}，狀態：{last['status']}{result_text}")
            return

        st.subheader("回補設定")
        date_col1, date_col2 = st.columns(2)
        with date_col1:
            start_date = st.date_input("開始日期", key="backfill_start_date")
        with date_col2:
            end_date = st.date_input("結束日期", key="backfill_end_date")

        stock_codes_input = st.text_input(
            "股票代號（逗號分隔，必填，例如 2317, 2330）", key="backfill_stock_codes",
        )

        st.markdown("回補項目：")
        item_col1, item_col2, item_col3, item_col4 = st.columns(4)
        with item_col1:
            taiex_price = st.checkbox("大盤股價", value=True, key="backfill_taiex_price")
        with item_col2:
            stock_price = st.checkbox("個股股價明細", value=True, key="backfill_stock_price")
        with item_col3:
            stock_institutional = st.checkbox("個股三大法人買賣超", value=True, key="backfill_stock_institutional")
        with item_col4:
            stock_margin = st.checkbox("個股融資融券(資券)", value=True, key="backfill_stock_margin")

        force_overwrite = st.checkbox(
            "強制覆蓋（忽略已有資料，全部重新抓取）", value=False, key="backfill_force_overwrite",
        )
        recompute_candidates = st.checkbox(
            "同時回補歷史候選清單訊號（較耗時）", value=False, key="backfill_recompute_candidates",
        )

        if st.button("🔍 預覽並驗證"):
            errors = []
            if start_date > end_date:
                errors.append("開始日期不能晚於結束日期。")
            elif (end_date - start_date).days > BACKFILL_MAX_RANGE_DAYS:
                errors.append(f"日期區間最多{BACKFILL_MAX_RANGE_DAYS}天。")

            stock_codes = {c.strip() for c in stock_codes_input.split(",") if c.strip()}
            if not stock_codes:
                errors.append("請輸入至少一個股票代號。")

            if not (taiex_price or stock_price or stock_institutional or stock_margin):
                errors.append("請至少勾選一項回補項目。")

            if errors:
                for msg in errors:
                    st.error(msg)
            else:
                holidays = set(holidays_between(start_date.year, end_date.year))
                trading_day_estimate = sum(
                    1 for i in range((end_date - start_date).days + 1)
                    if (start_date + timedelta(days=i)).weekday() < 5
                    and (start_date + timedelta(days=i)).isoformat() not in holidays
                )
                st.session_state["pending_backfill_params"] = {
                    "start": start_date.isoformat(), "end": end_date.isoformat(),
                    "force_overwrite": force_overwrite,
                    "stock_id_filter": sorted(stock_codes),
                    "taiex_price": taiex_price, "stock_price": stock_price,
                    "stock_institutional": stock_institutional, "stock_margin": stock_margin,
                    "recompute_candidates": recompute_candidates,
                    "estimate_trading_days": trading_day_estimate,
                }

        pending = st.session_state.get("pending_backfill_params")
        if pending:
            st.success(
                f"預計影響 {len(pending['stock_id_filter'])} 檔股票、最多 "
                f"{pending['estimate_trading_days']} 個交易日。"
            )
            password_input = st.text_input("存取密碼", type="password", key="backfill_password_input")
            confirm_col, cancel_col = st.columns(2)
            with confirm_col:
                confirm_clicked = st.button("✅ 確認送出，開始回補", key="backfill_confirm")
            with cancel_col:
                if st.button("取消", key="backfill_cancel"):
                    st.session_state["pending_backfill_params"] = None
                    st.rerun()

            if confirm_clicked:
                # 防止兩個分頁/兩次點擊之間的race：密碼比對前再檢查一次冷卻。
                remaining = admin_action_rate_limit.seconds_until_next_allowed(conn, "backfill", BACKFILL_COOLDOWN_SECONDS)
                if remaining > 0:
                    st.error("剛剛已經有其他嘗試觸發，請稍後再試。")
                elif password_input != access_code:
                    st.error("密碼錯誤。")
                else:
                    params = {k: v for k, v in pending.items() if k != "estimate_trading_days"}
                    st.session_state["pending_backfill_params"] = None
                    attempt_id = admin_action_rate_limit.record_attempt_start(conn, "backfill", params)
                    _run_backfill(params, attempt_id)

    # 2026-08-05拿掉這裡原本的全域「📈 台股每日選股」標題+股價更新至/候選清單算至
    # 狀態列——桌面版完全沒有等同的東西(那是本機視窗標題列的事，不會出現在分頁內容
    # 裡)，`st.set_page_config(page_title=..., ...)`(本檔案開頭)已經把瀏覽器分頁
    # 標題設好了，這裡再放一次是純web版多出來的重複資訊。狀態列邏輯(股價更新至/
    # 候選清單算至/下次更新時間/更新中/中斷警告)搬到「選股」分頁自己的按鈕列，比照
    # 桌面版desktop/main_window.py的self.status_label(只在_build_screener_tab()
    # 裡，不是全域的)。

    # 七個分頁：①大盤、②選股(候選清單篩選+清單本身)、③個股資訊(個股查詢+K線圖+個股
    # 分析)、④產業輪動、⑤庫存清單、⑥觀察清單、⑦回補資料——原本候選清單跟個股圖表擠
    # 在同一個分頁，使用者反映畫面太擁擠，拆開後候選清單點選任一列會自動切到③並代入
    # 該股票資料。
    #
    # 2026-08-06改版：改用真正的st.tabs()(取代先前模擬分頁列外觀的st.radio)——
    # streamlit 1.60起st.tabs()支援key=+on_change="rerun"讀寫目前分頁(st.session_
    # state[key])、用.open判斷目前分頁是否為選取中的分頁，可以做到跟st.radio一樣的
    # 「用程式碼控制分頁」效果(先前這裡的說明是根據更早期的streamlit版本寫的，已經
    # 過時；使用者2026-08-06指出「我公司的系統可以切分頁」才發現這個限制已經解除)。
    #
    # ⚠️ 2026-08-01踩到的坑(改用st.tabs()後機制不變，只是widget種類換了)：不能在
    # tabs widget已經instantiate之後、同一輪script執行裡直接`st.session_state
    # ["active_tab"] = ...`——會丟`StreamlitAPIException: cannot be modified after
    # the widget with key active_tab is instantiated`(這裡的tabs在最上面就建立了，
    # 候選清單點選事件卻是在後面的分頁內容裡才觸發，時間點必然在widget instantiate
    # 之後)。正確做法是改寫一個「不綁定任何widget」的中介session_state key(_pending_
    # active_tab)，在tabs建立"之前"讀取它、寫回active_tab，候選清單點選時只設定這個
    # 中介key再st.rerun()，下一輪script重新執行到這裡時，tabs都還沒建立，這時候寫入
    # active_tab才合法。
    if "_pending_active_tab" in st.session_state:
        st.session_state["active_tab"] = st.session_state.pop("_pending_active_tab")
    elif "active_tab" not in st.session_state:
        st.session_state["active_tab"] = TAB_SCREENER
    # 候選清單表格點「名稱」欄跳轉個股資訊後(見下面TAB_SCREENER的說明)，要把這次觸發
    # 跳轉的cell selection清掉，不然使用者之後手動切回「選股」分頁時，st.dataframe()
    # 還記得上次選到的那個「名稱」儲存格，會被誤判成「使用者又點了一次」再度跳轉，
    # 造成使用者卡在「選股」分頁待不住的迴圈——同一個「widget instantiate前才能寫入」
    # 限制，比照上面_pending_active_tab的中介key模式處理。
    if "_pending_candidates_table_reset" in st.session_state:
        st.session_state.pop("_pending_candidates_table_reset")
        st.session_state["candidates_table"] = {"selection": {"rows": [], "columns": [], "cells": []}}
    # 2026-08-07新增：觀察清單/庫存清單表格點「名稱」欄跳轉個股資訊後，同一個道理
    # 也要清掉這次觸發跳轉的cell selection，不然切回這兩個分頁時會被誤判成「又點了
    # 一次」再度跳轉——理由、模式都跟上面_pending_candidates_table_reset一致。
    if "_pending_watchlist_table_reset" in st.session_state:
        st.session_state.pop("_pending_watchlist_table_reset")
        st.session_state["watchlist_table"] = {"selection": {"rows": [], "columns": [], "cells": []}}
    if "_pending_inventory_summary_table_reset" in st.session_state:
        st.session_state.pop("_pending_inventory_summary_table_reset")
        st.session_state["inventory_summary_table"] = {"selection": {"rows": [], "columns": [], "cells": []}}
    # 2026-08-12新增：產業輪動每個產業各自一張個股明細表格，key是動態組出來的
    # (f"industry_stocks_table_{industry}")，不是固定key，這裡記的是「要重置哪一個
    # key」本身，不是布林旗標，理由跟上面幾個_pending_*_table_reset一致。
    pending_industry_stocks_key = st.session_state.pop("_pending_industry_stocks_table_reset_key", None)
    if pending_industry_stocks_key is not None:
        st.session_state[pending_industry_stocks_key] = {"selection": {"rows": [], "columns": [], "cells": []}}
    tab_market, tab_screener, tab_stock_detail, tab_industry, tab_inventory, tab_watchlist, tab_backfill = st.tabs(
        TAB_OPTIONS, key="active_tab", on_change="rerun",
    )
    with tab_market:
        if tab_market.open:
            # 大盤只有一檔、資料量固定，不像個股資訊那樣需要互動控制項，版面拆成「圖表」／
            # 「大盤分析」兩個分頁(跟桌面版一致，見desktop/main_window.py的_build_market_
            # tab()/_refresh_market_tab()說明)。
            st.subheader(f"📈 {TAIEX_DISPLAY_NAME}")
            render_chart_fn, render_analysis_fn = render_price_chart(TAIEX_STOCK_ID, widget_key="taiex", is_market_overview=True)
            if render_chart_fn is not None:
                chart_tab, analysis_tab = st.tabs(["圖表", "大盤分析"])
                with chart_tab:
                    render_chart_fn()
                with analysis_tab:
                    render_analysis_fn()

    with tab_screener:
        if tab_screener.open:
            # 2026-08-05改版：候選清單日期選單搬到最前面(比照桌面版desktop/main_window.py
            # 的_build_screener_tab()第一個元件date_bar，選了立即切換，不用等「套用篩選」)，
            # 原本擠在按鈕列後面、跟其他篩選條件的deferred-apply順序混在一起，使用者反映
            # 位置跟桌面版不一致、難以辨識。
            candidate_dates = list_candidate_dates(conn)
            selected_date = (
                _inline_field(
                    st, "候選清單日期",
                    lambda: st.selectbox(
                        "候選清單日期", candidate_dates, index=0, key="candidate_date_select",
                        label_visibility="collapsed", width=160,
                    ),
                    label_width=0.6, widget_width=5,
                )
                if candidate_dates else None
            )

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
            # 2026-08-06改版：label跟下拉/輸入框改成同一列的緊湊排法(見_inline_field()
            # 的說明)，取代原本Streamlit預設label在上、widget在下、又滿版拉寬的排版。
            pool_col1, pool_col2, pool_col3 = st.columns([1, 1.6, 1])
            market_label = _inline_field(
                pool_col1, "市場",
                lambda: st.selectbox(
                    "市場", ["全部", "上市", "上櫃"], index=0, key="filter_market_label",
                    label_visibility="collapsed", width=90,
                ),
                label_width=0.6, widget_width=1.6,
            )
            selected_industries = _inline_field(
                pool_col2, "產業別",
                lambda: st.multiselect(
                    "產業別", list_industries(conn), key="filter_industries",
                    label_visibility="collapsed", width=260,
                ),
                label_width=0.6, widget_width=3.6,
            )
            min_volume_lots_input = _inline_field(
                pool_col3, "成交量>=(張)",
                lambda: st.number_input(
                    "成交量 >= (張)", min_value=0, value=10, step=1, key="filter_min_volume_lots",
                    label_visibility="collapsed", width=90,
                ),
                label_width=1.4, widget_width=1,
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
            # 2026-08-06改版：「方向」「天數內翻轉」改成_inline_field()緊湊排法，
            # 「SAR 翻轉」checkbox本身label就在同一行，不需要這個處理。
            sar_col1, sar_col2, sar_col3, zhu_col, apply_col = st.columns([1, 1.3, 1.6, 1.3, 1])
            sar_flip_enabled = sar_col1.checkbox(
                "SAR 翻轉", value=CANDIDATE_SAR_FLIP_ENABLED_DEFAULT, key="filter_sar_flip_enabled"
            )
            sar_flip_direction = _inline_field(
                sar_col2, "方向",
                lambda: st.selectbox(
                    "方向", ["多頭", "空頭"],
                    index=["多頭", "空頭"].index(CANDIDATE_SAR_FLIP_OPTION_DEFAULT["direction"]),
                    key="filter_sar_flip_direction", label_visibility="collapsed", width=80,
                ),
                label_width=0.5, widget_width=1.3,
            )
            sar_flip_within_days = _inline_field(
                sar_col3, "天數內翻轉",
                lambda: st.number_input(
                    "天數內翻轉", min_value=1, max_value=60,
                    value=CANDIDATE_SAR_FLIP_OPTION_DEFAULT["within_days"], step=1,
                    key="filter_sar_flip_within_days", label_visibility="collapsed", width=70,
                ),
                label_width=1.4, widget_width=1,
            )
            sar_flip_option = (
                {"direction": sar_flip_direction, "within_days": int(sar_flip_within_days)}
                if sar_flip_enabled else None
            )

            # 「朱家泓技術分析」勾選框：2026-08-01新增，2026-08-02改版跟其他「篩選方法」
            # (SAR翻轉)一樣是獨立的AND條件，不是「候選清單本來就限定在這個範圍」的基礎池
            # ——候選清單基礎池現在是全市場(見chart_data.load_stock_universe_for_date())，
            # 勾選這裡才會額外要求「當天有出現在daily_candidates(觸發過某條朱家泓規則)」；
            # 不勾選時，均線/SAR等其他條件會對全市場掃描，不受這個限制。2026-08-06修正：
            # 預設值改成CANDIDATE_ZHU_RULE_ONLY_DEFAULT(現在是False，跟SAR翻轉的「乾淨
            # 預設值」一起由使用者確認，見chart_data.py同一天的說明)。
            zhu_rule_only = zhu_col.checkbox(
                "朱家泓技術分析", value=CANDIDATE_ZHU_RULE_ONLY_DEFAULT, key="filter_zhu_rule_only",
                help="勾選時只保留當天有觸發朱家泓規則的股票；取消勾選則不限制，均線/SAR等條件會對全市場掃描",
            )

            # 「外資/投信累計買超」篩選：2026-08-10新增，使用者自訂的分析工具(不是書中
            # 規則，見chart_data.py的INSTITUTIONAL_ACCUMULATION_*常數說明)，跟SAR翻轉
            # 同一種「勾選框+多個參數」綁在一起的UI模式，獨立傳給apply_candidate_
            # filters()的institutional_accumulation_option參數。核心用途：找出「即使
            # 中間偶爾賣超，但N天累計買超佔均量比例仍達門檻」的緩步建倉股票，跟「連續
            # 買超天數」(CANDIDATE_FILTERS既有規則)不同，那個指標中間出現一天賣超就會
            # 歸零，抓不到這種型態。
            st.caption("外資/投信累計買超（使用者自訂分析工具，非書中規則）：")
            inst_col1, inst_col2, inst_col3, inst_col4, inst_col5 = st.columns([0.8, 1.1, 1.4, 1.8, 1.3])
            institutional_accumulation_enabled = inst_col1.checkbox(
                "啟用", value=False, key="filter_institutional_accumulation_enabled"
            )
            institutional_accumulation_type = _inline_field(
                inst_col2, "法人別",
                lambda: st.selectbox(
                    "法人別", ["外資", "投信"], key="filter_institutional_accumulation_type",
                    label_visibility="collapsed", width=80,
                ),
                label_width=0.7, widget_width=1.3,
            )
            institutional_accumulation_window_days = _inline_field(
                inst_col3, "天內累計",
                lambda: st.number_input(
                    "天內累計", min_value=5, max_value=120,
                    value=INSTITUTIONAL_ACCUMULATION_WINDOW_DAYS_DEFAULT, step=1,
                    key="filter_institutional_accumulation_window_days", label_visibility="collapsed", width=70,
                ),
                label_width=1.2, widget_width=1,
            )
            institutional_accumulation_min_ratio_pct = _inline_field(
                inst_col4, "%門檻(佔均量)",
                lambda: st.number_input(
                    "%門檻(佔均量)", min_value=0.1, max_value=100.0,
                    value=INSTITUTIONAL_ACCUMULATION_MIN_RATIO_PCT_DEFAULT, step=0.5,
                    key="filter_institutional_accumulation_min_ratio_pct", label_visibility="collapsed", width=80,
                ),
                label_width=1.6, widget_width=1,
            )
            # 不勾時，底部建倉/追價買超都會被篩出來(用途更彈性)，但訊號欄位一律會註明是
            # 「底部」還是「追價」(見load_institutional_accumulation_flags())，使用者
            # 可自行判斷；勾選時只保留判定為「底部」(trend_position.is_at_low)的股票。
            institutional_accumulation_require_at_low = inst_col5.checkbox(
                "只看底部建倉", value=False, key="filter_institutional_accumulation_require_at_low",
            )
            institutional_accumulation_option = (
                {
                    "investor_type": institutional_accumulation_type,
                    "window_days": int(institutional_accumulation_window_days),
                    "min_ratio_pct": float(institutional_accumulation_min_ratio_pct),
                    "require_at_low": institutional_accumulation_require_at_low,
                }
                if institutional_accumulation_enabled else None
            )

            # ⚠️ 2026-08-06修正真實bug：這裡先前是獨立寫死一組"active_filters: []、
            # sar_flip_option: None、market: None"等等，跟上面checkbox/下拉實際顯示的
            # 預設值(CANDIDATE_FILTER_DEFAULTS/CANDIDATE_SAR_FLIP_ENABLED_DEFAULT等)完全
            # 對不上——使用者第一次進這個分頁、還沒按過「套用篩選」前，畫面上打勾的條件
            # 跟下面候選清單表格實際套用的條件是兩組不同的東西(表格顯示的是這裡寫死的
            # 「無篩選」狀態)，改成直接拿上面剛算好的active_filters/sar_flip_option/
            # zhu_rule_only/market_label/selected_industries/min_volume_lots_input初始化，
            # 保證第一次進來時「畫面勾的」跟「表格套用的」一致；deferred-apply設計本身
            # 不變(改完checkbox還是要按「套用篩選」才會重新套用)，只有初始值的來源改了。
            if "applied_filters" not in st.session_state:
                st.session_state["applied_filters"] = {
                    "active_filters": active_filters, "sar_flip_option": sar_flip_option,
                    "zhu_rule_only": zhu_rule_only,
                    "market": _MARKET_FILTER_VALUES.get(market_label),
                    "industries": selected_industries, "min_volume_lots": int(min_volume_lots_input),
                    "institutional_accumulation_option": institutional_accumulation_option,
                }
            with apply_col:
                st.markdown("&nbsp;")  # 對齊上面其他欄位的label高度，讓按鈕跟輸入框大致同一條水平線
                if st.button("套用篩選"):
                    st.session_state["applied_filters"] = {
                        "active_filters": active_filters, "sar_flip_option": sar_flip_option,
                        "zhu_rule_only": zhu_rule_only,
                        "market": _MARKET_FILTER_VALUES.get(market_label),
                        "industries": selected_industries, "min_volume_lots": int(min_volume_lots_input),
                        "institutional_accumulation_option": institutional_accumulation_option,
                    }

            # 按鈕列：🔄立即重新篩選/▶手動抓取今日資料/候選清單內搜尋框/狀態文字都在同一列，
            # 比照桌面版desktop/main_window.py的top_bar(self.refresh_btn/self.fetch_btn/
            # self.candidate_search_input/self.status_label依序排開，靠右對齊)。搜尋框
            # 2026-08-05從結果區搬過來(原本跟表格擠在一起，現在跟桌面版一樣緊接在按鈕
            # 旁邊)，狀態文字2026-08-05從整個頁面最上方的全域標題列搬過來(桌面版的
            # status_label只在「選股」分頁裡，不是跨分頁都看得到的全域資訊)。
            button_col1, button_col2, search_col, status_col = st.columns([1, 1.5, 1.5, 2])
            with button_col1:
                if st.button("🔄 立即重新篩選"):
                    # 只用資料庫裡目前已有的資料重算訊號，不重新對外抓取TWSE/TPEx資料(那個很慢，
                    # 交給下面的手動抓取按鈕或排程做)，所以這個按鈕通常幾秒內就能算完，可以隨時按
                    # 而不用擔心額度或等待。
                    with st.spinner("正在用目前資料庫裡的最新資料重新計算選股訊號..."):
                        run_screen_and_store(conn)
                    st.success("已重新計算完成，候選清單已更新。")
            with button_col2:
                # ⚠️ 2026-08-05加上密碼+冷卻保護(比照回補資料，見ai/PLAN.md第9批)：這顆
                # 按鈕會對主DB的Turso帳號寫入、呼叫FinMind額度，dry_run=False還會觸發真實
                # LINE/Email通知——公開網址下任何訪客都能點，风险跟回補資料同一類，只是
                # 操作規模小很多(單日而非日期區間)，冷卻時間也短很多(見MANUAL_FETCH_
                # COOLDOWN_SECONDS)。
                manual_fetch_access_code = get_admin_access_code()
                manual_fetch_remaining = (
                    admin_action_rate_limit.seconds_until_next_allowed(conn, "manual_fetch", MANUAL_FETCH_COOLDOWN_SECONDS)
                    if manual_fetch_access_code is not None else 0.0
                )
                if manual_fetch_access_code is None:
                    st.caption("⚠️ 尚未設定 ADMIN_ACCESS_CODE，「手動抓取今日資料」已停用。")
                elif manual_fetch_remaining > 0:
                    minutes = int(manual_fetch_remaining) // 60
                    st.caption(f"⏳ 距離下次可手動抓取還要等 {minutes} 分鐘。")
                else:
                    manual_fetch_password = st.text_input(
                        "存取密碼", type="password", key="manual_fetch_password_input",
                        label_visibility="collapsed", placeholder="輸入存取密碼",
                    )
                    if st.button("▶ 手動抓取今日資料"):
                        if manual_fetch_password != manual_fetch_access_code:
                            st.error("密碼錯誤。")
                        else:
                            # 跟桌面版「▶ 手動抓取今日資料」按鈕呼叫同一份run_daily_pipeline()，行為
                            # 一致(含TWSE官方端點優先、收盤前查無資料時退回yfinance盤中即時價備援)。
                            # Streamlit沒有背景執行緒機制，這裡是同步阻塞呼叫，按下去要等整個抓取跑完
                            # (TWSE+TPEx合計實測約1分鐘內)才會回應，用進度條讓使用者知道還在跑、跑到
                            # 哪裡，不是卡住。
                            attempt_id = admin_action_rate_limit.record_attempt_start(conn, "manual_fetch", {})
                            progress_bar = st.progress(0.0, text="準備開始...")

                            def _on_progress(stage: str, done: int, total: int) -> None:
                                progress_bar.progress(done / total if total else 0.0, text=f"{stage} 下載進度：{done}/{total}檔")

                            try:
                                with st.spinner("正在抓取TWSE/TPEx今日資料並重新選股..."):
                                    candidates = run_daily_pipeline(conn, dry_run=False, on_progress=_on_progress)
                                admin_action_rate_limit.record_attempt_result(
                                    conn, attempt_id, "done", {"candidate_count": len(candidates)},
                                )
                                progress_bar.empty()
                                st.success(f"抓取完成，候選清單共{len(candidates)}檔。")
                            except Exception as exc:  # noqa: BLE001
                                admin_action_rate_limit.record_attempt_result(conn, attempt_id, "failed", {"error": str(exc)})
                                progress_bar.empty()
                                st.error(f"抓取失敗：{exc}")
                            st.rerun()

            with search_col:
                # 候選清單內搜尋：純顯示層過濾，不透過「套用篩選」按鈕(不是資料查詢，即時
                # 生效)，跟桌面版_on_candidate_search()「找到後自動選取捲動」的做法不同——
                # Streamlit的dataframe沒有程式化捲動/選取特定列的API，改成直接篩掉不符合
                # 的列，達到同樣「從一長串候選清單快速縮小範圍」的目的。
                search_query = st.text_input(
                    "搜尋候選清單（代號或名稱）", key="candidate_search_query", label_visibility="collapsed",
                    placeholder="搜尋候選清單（代號或名稱）",
                )

            with status_col:
                status = pipeline_status.read_status() or {}
                if status.get("status") == "running" and pipeline_status.is_stale(status):
                    # process被強制中止(kill/當機/斷電)時，Python的except/finally完全沒機會
                    # 執行，狀態檔案會永久停在最後一次心跳的"running"——is_stale()判斷太久沒
                    # 更新，這裡不能再顯示「更新中」誤導使用者，要明確標示可能已經中斷。
                    st.markdown("**:red[⚠ 上次自動更新可能已中斷，請重新手動抓取]**")
                elif status.get("status") == "running":
                    stage, progress = status.get("stage"), status.get("progress")
                    detail = f"　{stage} {progress}檔" if stage and progress else ""
                    st.markdown(f"**:orange[🔄 更新中...{detail}]**")
                else:
                    def _fmt_status_ts(ts: str | None) -> str:
                        if not ts:
                            return "尚無資料"
                        try:
                            return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
                        except ValueError:
                            return ts

                    # 股價資料跟候選清單是兩件各自獨立更新的東西：股價可能已經更新到今天，但
                    # 候選清單是幾分鐘前手動按「立即重新篩選」才重算的，兩個時間點不會永遠
                    # 一致，混在一起顯示會讓使用者誤判「候選清單是不是也跟著更新了」，所以
                    # 分開各顯示一行。
                    st.caption(f"股價更新至　{_fmt_status_ts(get_latest_update_time(conn))}")
                    st.caption(f"候選清單算至　{_fmt_status_ts(get_latest_candidate_update_time(conn))}")
                    # `.github/workflows/daily_pipeline.yml`的排程自2026-07-23起被註解暫停
                    # (Turso帳號寫入額度用完，架構改成本機優先運作)，只留workflow_dispatch
                    # 可以手動觸發——web版現在其實沒有任何自動更新機制，不能沿用桌面版「下次
                    # 更新時間」那套邏輯(那是讀Windows工作排程器寫死的時間表，跟這裡的情境
                    # 不同)，改成明確提醒使用者目前是純手動更新，避免誤以為資料會自動保持
                    # 最新。
                    st.caption("⚠️ 目前無自動排程更新中（GitHub Actions 排程已暫停，資料需手動觸發「▶ 手動抓取今日資料」更新）")

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
                institutional_accumulation_option=applied.get("institutional_accumulation_option"),
            )

            if latest_date is None:
                st.info("目前 Turso 資料庫裡還沒有任何每日選股紀錄，點上方「立即重新篩選」或等 GitHub Actions 排程跑完後就會顯示。")
            else:
                st.subheader(f"候選清單（{latest_date}，共 {len(candidates_df)} 檔）")
                if is_intraday:
                    st.markdown("**:red[⚠ 尚未收盤，本頁為盤中即時資料，收盤後數字可能改變]**")
                # search_query已經在上面按鈕列的search_col裡取得(2026-08-05搬過去，比照
                # 桌面版搜尋框跟🔄/▶按鈕同一列的位置)，這裡直接用。
                if search_query:
                    q = search_query.strip().lower()
                    candidates_df = candidates_df[
                        candidates_df["stock_id"].str.lower().str.contains(q, na=False)
                        | candidates_df["name"].str.lower().str.contains(q, na=False)
                    ].reset_index(drop=True)
                if candidates_df.empty:
                    st.write("搜尋不到符合的股票。" if search_query else "這一天沒有符合條件的候選股。")
                else:
                    # 2026-08-06改版：使用者反映「點任一列(包含勾選checkbox本身)就跳轉
                    # 個股資訊」很奇怪——勾選是要做「加入庫存/加入觀察清單」批次動作，
                    # 跟「查看這檔股票」是兩件不同的事，卻共用同一個single-row selection
                    # 觸發跳轉。查證過st.dataframe()沒有雙擊事件可用(streamlit issue
                    # #10190/#10212，官方連「單擊 vs 選取」都還沒分開，更別說雙擊)。
                    # 使用者接著提議「名稱」欄改成可點連結跳轉——查證selection_mode可以
                    # 同時混用["multi-row", "single-cell"]，回傳的event.selection.cells
                    # 是(row, column_name)清單，可以精確判斷「使用者點的是不是名稱欄」，
                    # 不用真的做成`<a href>`連結(那樣要另外接st.query_params橋接，架構
                    # 改動大很多)：①selection_mode混用multi-row(勾選欄，批次動作用)+
                    # single-cell(判斷有沒有點到「名稱」欄，點到就跳轉個股資訊)；②批次
                    # 動作按鈕(加入庫存/加入觀察清單)搬到表格上方，直接讀multi-row
                    # selection。
                    #
                    # 讀「上一輪」的selection狀態：這一輪script重新執行時，使用者剛做的
                    # 勾選/取消勾選已經先寫進session_state了，比下面真正呼叫st.dataframe()
                    # 還早，這樣按鈕才能畫在表格「上面」卻還是讀得到目前的勾選狀態。
                    prior_selection_rows = (
                        st.session_state.get("candidates_table", {}).get("selection", {}).get("rows", [])
                    )
                    bulk_selected_ids = [
                        str(candidates_df.iloc[i]["stock_id"]) for i in prior_selection_rows if i < len(candidates_df)
                    ]

                    # 2026-08-06使用者反映按鈕用st.columns([1,1,1])平分整列寬度，PC大螢幕
                    # 解析度高時看起來很零散(按鈕本身寬度不變，但欄位之間空隙被拉得很開)。
                    # 改成窄欄位+一個吸收剩餘空間的spacer，讓按鈕都靠左緊貼在一起。
                    action_col1, action_col2, spacer_col = st.columns([1, 1.4, 4])
                    with action_col1:
                        if st.button("➕ 加入庫存", key="candidates_add_to_inventory"):
                            if not bulk_selected_ids:
                                st.warning("請至少勾選一檔股票。")
                            else:
                                added = portfolio_storage.add_stocks_to_inventory(portfolio_conn, bulk_selected_ids)
                                st.success(f"已加入{added}檔股票到庫存清單(空白批次，之後可自行填入成本價/持股數)；{len(bulk_selected_ids) - added}檔已有既存批次，未重複加入。")
                    with action_col2:
                        if st.button("➕ 加入觀察清單", key="candidates_add_to_watchlist"):
                            if not bulk_selected_ids:
                                st.warning("請至少勾選一檔股票。")
                            else:
                                _watchlist_group_picker_dialog(bulk_selected_ids)

                    st.caption("勾選左側checkbox可批次「加入庫存」/「加入觀察清單」；點「名稱」欄可直接查看個股資訊")

                    def _style_name_by_listing_type(row: pd.Series) -> list[str]:
                        # 依上市/上櫃/興櫃上色股票名稱，照抄桌面版main_window.py的listing_type_
                        # color()慣例；只對"name"欄位回傳實際樣式，其他欄位回傳空字串不受影響。
                        color = portfolio_data.listing_type_color(row.get("listing_type"))
                        return [f"color: {color}" if col == "name" else "" for col in row.index]

                    # ⚠️ 數字欄位先轉成「已格式化好的字串」("-"代表缺值)，不依賴column_config.
                    # NumberColumn自動格式化——見_fmt_or_dash()的說明。entry_price/stop_loss/
                    # sar_value/sar_distance_pct對「全市場但沒觸發過任何朱家泓規則」或「還沒
                    # 回補到daily_indicators」的股票可能是None，候選池改成全市場(見上面
                    # apply_candidate_filters的說明)後這種情況比只顯示daily_candidates時
                    # 更常見。candidates_df後面只用stock_id這個未受影響的欄位做選取查詢，
                    # 直接原地覆寫不需要另外保留原始數值版本。
                    candidates_df["close"] = candidates_df["close"].apply(lambda v: _fmt_or_dash(v, 2))
                    candidates_df["entry_price"] = candidates_df["entry_price"].apply(lambda v: _fmt_or_dash(v, 2))
                    candidates_df["stop_loss"] = candidates_df["stop_loss"].apply(lambda v: _fmt_or_dash(v, 2))
                    candidates_df["pct_change"] = candidates_df["pct_change"].apply(lambda v: _fmt_or_dash(v, 2, suffix="%"))
                    candidates_df["volume"] = candidates_df["volume"].apply(lambda v: _fmt_or_dash(v, 0))
                    candidates_df["sar_value"] = candidates_df["sar_value"].apply(lambda v: _fmt_or_dash(v, 2))
                    candidates_df["sar_distance_pct"] = candidates_df["sar_distance_pct"].apply(lambda v: _fmt_or_dash(v, 2, suffix="%"))

                    event = st.dataframe(
                        candidates_df.style.apply(_style_name_by_listing_type, axis=1),
                        use_container_width=True, hide_index=True,
                        on_select="rerun", selection_mode=["multi-row", "single-cell"], key="candidates_table",
                        column_order=[
                            "stock_id", "name", "industry", "signal_name", "close", "entry_price",
                            "stop_loss", "pct_change", "volume", "sar_value", "sar_status", "sar_distance_pct",
                        ],
                        column_config={
                            "stock_id": "股票代號", "name": "名稱", "industry": "產業別",
                            # 2026-08-09曾拆成獨立「警示」欄，使用者反映會撐開版面，改回
                            # 合併顯示在同一欄(欄位標頭改名「訊號(信心%)/警示」)——警示
                            # 內容本身仍帶daily_screener.WARNING_SIGNAL_PREFIX("⚠️排除：")
                            # 前綴，肉眼還是分得出來。
                            "signal_name": "訊號(信心%)/警示",
                            "close": "收盤價", "entry_price": "進場價", "stop_loss": "停損價",
                            "pct_change": "漲跌幅(%)", "volume": "成交量",
                            "sar_value": "SAR值", "sar_status": "SAR狀態", "sar_distance_pct": "SAR距離%",
                        },
                    )
                    # 點「名稱」欄的儲存格(single-cell selection，跟上面批次動作用的
                    # multi-row勾選欄各自獨立、互不干擾)就跳轉個股資訊——event.selection.
                    # cells是[(row_idx, column_name)]清單，column_name=="name"代表點到
                    # 名稱欄，不是勾選欄(勾選欄不算資料欄，不會出現在cells裡)。
                    name_clicked_row = next(
                        (row for row, col in event.selection.cells if col == "name"), None,
                    )
                    if name_clicked_row is not None:
                        selected_stock_id = str(candidates_df.iloc[name_clicked_row]["stock_id"])
                        # 記錄來源候選清單日期，供「個股資訊」分頁右上角顯示「來源：X月X日的
                        # 選股策略」。2026-08-12修正：搜尋框改成同步顯示股票代號(不是清空)，
                        # 同時設定_detail_query_last_seen，讓下面TAB_STOCK_DETAIL分支判斷
                        # 「query沒變」而不會重新觸發resolve_stock_id()/清掉來源標籤——完整
                        # 說明見那個分支開頭的註解。
                        st.session_state["detail_stock_id"] = selected_stock_id
                        st.session_state["detail_stock_source"] = selected_date or latest_date
                        st.session_state["detail_query_input"] = selected_stock_id
                        st.session_state["_detail_query_last_seen"] = selected_stock_id
                        st.session_state["_pending_active_tab"] = TAB_STOCK_DETAIL
                        # 順便排定下一輪把這個cell selection清掉(見main()開頭的說明)，
                        # 不然使用者之後切回「選股」分頁時，st.dataframe()還記得上次點過
                        # 的「名稱」儲存格，會被誤判成「又點了一次」再度跳轉，卡在「選股」
                        # 分頁待不住。
                        st.session_state["_pending_candidates_table_reset"] = True
                        st.rerun()

    with tab_stock_detail:
        if tab_stock_detail.open:
            query_col, source_col = st.columns([3, 1])
            with query_col:
                query = st.text_input(
                    "輸入股票代號或名稱（例如 2330 或 台積電）", value="", key="detail_query_input",
                )
            # 2026-08-12修正：使用者反映從產業輪動/庫存清單點過去後，搜尋框顯示是空的，
            # 跟畫面上實際顯示的股票對不起來，容易搞混——原本的做法是導覽時把這個欄位
            # 清成""(見candidates_table/watchlist_table/inventory_summary_table三處
            # 點名稱跳轉的程式碼)，是刻意避免「text_input帶著上次殘留的查詢文字，把
            # 剛設定好的detail_stock_id蓋掉」，但代價就是搜尋框看起來像沒查過。
            #
            # 改成導覽時把這個欄位設成股票代號本身(不是清空)，同時記一份「上次已經
            # 處理過的文字」(_detail_query_last_seen)：只有query真的變成「跟上次處理
            # 過的不一樣」時，才視為使用者剛打的新查詢、觸發resolve_stock_id()＋清掉
            # 來源標籤；如果query沒變(不管是導覽剛帶進來的股票代號、還是使用者之前
            # 手動查過的舊文字)，就不重複觸發，detail_stock_id/detail_stock_source
            # 維持原本(可能是導覽時明確設定的來源標籤)不被覆蓋。
            if query and query != st.session_state.get("_detail_query_last_seen"):
                st.session_state["detail_stock_id"] = resolve_stock_id(conn, query) or query.strip()
                st.session_state["detail_stock_source"] = None
            st.session_state["_detail_query_last_seen"] = query
            # detail_stock_id要先算好(不是等source_col畫完才算)，因為「資料更新至」
            # 要跟「來源」標籤一起放在source_col，需要先知道是哪一檔股票才能查
            # get_stock_update_time()。
            detail_stock_id = st.session_state.get("detail_stock_id")
            with source_col:
                source_date = st.session_state.get("detail_stock_source")
                if source_date:
                    st.caption(f"來源：{_format_month_day(source_date)}的選股策略")
                if detail_stock_id:
                    # 「資料更新至」：比照桌面版desktop/main_window.py的stock_detail_
                    # update_label，用get_stock_update_time(conn, stock_id)——這檔股票
                    # 自己的updated_at，不是get_latest_update_time()的全DB最新時間，
                    # 理由是查已下市/久未更新的股票時要如實反映「這檔資料其實很舊」，
                    # 不能被其他股票同一天的更新誤導。
                    update_ts = get_stock_update_time(conn, detail_stock_id)
                    update_label = datetime.fromisoformat(update_ts).strftime("%Y-%m-%d %H:%M") if update_ts else "尚無資料"
                    st.caption(f"資料更新至　{update_label}")

            if detail_stock_id:
                render_chart_fn, render_analysis_fn = render_price_chart(detail_stock_id, widget_key="detail")
                if render_chart_fn is not None:
                    # 2026-08-05改版：拆成「圖表」／「個股分析」／「個股明細」／「產出報表」
                    # 4個橫向分頁(st.tabs())，取代原本圖表+分析+明細+報表全部往下疊的單頁
                    # 版面，比照桌面版desktop/main_window.py的self.detail_inner_tabs
                    # (QTabWidget)結構。
                    chart_tab, analysis_tab, detail_tab, report_tab, q3_tab = st.tabs(
                        ["圖表", "個股分析", "個股明細", "產出報表", "三維過濾法"],
                    )
                    with chart_tab:
                        render_chart_fn()
                    with analysis_tab:
                        render_analysis_fn()
                    with detail_tab:
                        render_stock_overview_section(detail_stock_id)
                    with report_tab:
                        render_stock_report_section(detail_stock_id)
                    with q3_tab:
                        render_q3_analysis_section(detail_stock_id)
            else:
                st.info("請輸入股票代號或名稱查詢，或到「選股」分頁點選候選股票。")

    with tab_industry:
        if tab_industry.open:
            # 「產業輪動」：某一天各產業別的成交量加總/平均漲跌幅/股票數，看資金比較集中往
            # 哪個產業移動——照抄桌面版desktop/main_window.py的_build_industry_rotation_
            # tab()/_refresh_industry_rotation_tab()，底層查詢函式(chart_data.list_price_
            # dates()/load_industry_rotation())兩前端共用。日期選單不受daily_candidates
            # 限制(跟「選股」分頁的候選清單日期選單不同)，只要有股價資料就能選。
            # 2026-08-05調整：日期選單跟「資料更新至」改成同一列(比照桌面版desktop/
            # main_window.py的_build_industry_rotation_tab()的date_bar：QLabel+combo+
            # stretch+靠右的industry_update_label放在同一個QHBoxLayout)，取代原本各自
            # 佔一整列的排法；表格上方也拿掉多出來的「產業輪動（日期）」小標題——桌面版
            # 從date_bar直接接表格，沒有對應的標題文字，那個日期已經顯示在上面的選單裡，
            # 重複顯示。
            date_col, update_col = st.columns([3, 1])
            price_dates = list_price_dates(conn)
            # 2026-08-11修正：使用者反映「產業輪動」沒有更新到今天——查證是st.selectbox
            # 帶了固定key="industry_rotation_date_select"，Streamlit對有key的widget，
            # 只有session_state裡還沒有這個key時才會採用index=0；同一個瀏覽器session開著
            # 跨過盤中新資料進來的時間點，session_state早就存了「當時最新」的那個日期，
            # 之後即使price_dates已經多了今天，selectbox還是會沿用session_state裡的舊值，
            # index=0形同虛設。這裡額外記錄「上一次看到的最新日期」，只在使用者當時選的
            # 剛好就是「那時候的最新一天」(沒有刻意切去看某個歷史日期)時，才跟著往前推進
            # 到新的最新日期；使用者若刻意選了某個歷史日期，不會被強制打斷。
            _LATEST_SEEN_KEY = "industry_rotation_latest_seen_date"
            _SELECT_KEY = "industry_rotation_date_select"
            if price_dates:
                current_latest = price_dates[0]
                prev_latest = st.session_state.get(_LATEST_SEEN_KEY)
                if (
                    prev_latest is not None
                    and prev_latest != current_latest
                    and st.session_state.get(_SELECT_KEY) == prev_latest
                ):
                    st.session_state[_SELECT_KEY] = current_latest
                st.session_state[_LATEST_SEEN_KEY] = current_latest
            with date_col:
                # 2026-08-06改版：label跟下拉改成同一列的緊湊排法(見_inline_field()的
                # 說明)，不要滿版拉寬。
                rotation_date = (
                    _inline_field(
                        st, "日期",
                        lambda: st.selectbox(
                            "日期", price_dates, index=0, key=_SELECT_KEY,
                            label_visibility="collapsed", width=160,
                        ),
                        label_width=1, widget_width=5,
                    )
                    if price_dates else None
                )
            with update_col:
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
                # 2026-08-06第二版：改成每個產業別各自一個st.expander()，取代第一版「一張
                # 可排序表格+點列後在整張表格結束才接明細表格」的做法——使用者反映那樣看
                # 起來像是明細「疊加在最下面」，因為st.dataframe()是一整塊固定的表格元件，
                # Streamlit沒辦法把明細插進表格中間某一列正下方，不管點的是第一列還是
                # 最後一列，明細都只能接在整張表格結束之後。改成expander後點哪個產業，
                # 個股明細就直接展開在那個產業自己的標題下方，是真正的「就地展開」，
                # 跟桌面版QTreeWidget母子列的視覺效果一致。代價是產業別清單本身不能再
                # 點欄位標題互動式重新排序(改成固定依平均漲跌幅降冪排列，維持跟桌面版
                # 一致的預設順序)——個股明細表格本身還是獨立的st.dataframe()，排序不受
                # 影響。`key`+`on_change="rerun"`讓expander的開闔狀態可以用`.open`讀取，
                # 沒展開的產業不會白白查一次個股明細(streamlit>=1.60才支援，見st.tabs()
                # 同一天的說明)。
                for _, industry_row in rotation_df.iterrows():
                    industry = str(industry_row["industry"])
                    avg_pct_text = (
                        f"{industry_row['avg_pct_change']:+.2f}%" if pd.notna(industry_row["avg_pct_change"]) else "-"
                    )
                    label = (
                        f"{industry}　成交量合計 {int(industry_row['total_volume_lots']):,}張　"
                        f"平均漲跌幅 {avg_pct_text}　股票數 {int(industry_row['stock_count'])}"
                    )
                    with st.expander(label, key=f"industry_expander_{industry}", on_change="rerun") as industry_expander:
                        if industry_expander.open:
                            stocks_df = load_industry_rotation_stocks(conn, industry, latest_date)
                            if stocks_df.empty:
                                st.info("查無個股明細。")
                            else:
                                stocks_display = stocks_df.copy()
                                stocks_display["volume_lots"] = (stocks_df["volume"] // 1000).astype(int)
                                # 2026-08-12新增：點「名稱」欄跳轉個股資訊，邏輯跟候選清單/
                                # 觀察清單/庫存清單一致(見TAB_SCREENER分支的說明)。這裡每個
                                # 產業各自一張表格(在for迴圈裡)，key要帶上industry字串本身
                                # 避免widget key衝突；reset用的中介key也要記住是哪個industry
                                # 的表格，不能像候選清單那樣是固定的單一key。
                                st.caption("點「名稱」欄可直接查看個股資訊")
                                stocks_table_key = f"industry_stocks_table_{industry}"
                                stocks_event = st.dataframe(
                                    stocks_display, use_container_width=True, hide_index=True,
                                    on_select="rerun", selection_mode="single-cell", key=stocks_table_key,
                                    column_order=["stock_id", "name", "close", "open", "high", "low", "change", "pct_change", "volume_lots"],
                                    column_config={
                                        "stock_id": "股票代號", "name": "名稱",
                                        "close": st.column_config.NumberColumn("成交", format="%.2f"),
                                        "open": st.column_config.NumberColumn("開盤", format="%.2f"),
                                        "high": st.column_config.NumberColumn("最高", format="%.2f"),
                                        "low": st.column_config.NumberColumn("最低", format="%.2f"),
                                        "change": st.column_config.NumberColumn("漲跌", format="%+.2f"),
                                        "pct_change": st.column_config.NumberColumn("漲跌幅(%)", format="%+.2f%%"),
                                        "volume_lots": st.column_config.NumberColumn("總成交張數", format="%d"),
                                    },
                                )
                                name_clicked_row = next(
                                    (row for row, col in stocks_event.selection.cells if col == "name"), None,
                                )
                                if name_clicked_row is not None:
                                    stock_id = str(stocks_display.iloc[name_clicked_row]["stock_id"])
                                    st.session_state["detail_stock_id"] = stock_id
                                    st.session_state["detail_stock_source"] = None
                                    st.session_state["detail_query_input"] = stock_id
                                    st.session_state["_detail_query_last_seen"] = stock_id
                                    st.session_state["_pending_active_tab"] = TAB_STOCK_DETAIL
                                    st.session_state["_pending_industry_stocks_table_reset_key"] = stocks_table_key
                                    st.rerun()
                                # 使用者明確要求驗證：個股明細的總成交張數加總，理論上要等於
                                # 展開標題裡的「成交量合計」——這裡兩者都是volume//1000後才
                                # 加總/顯示，逐股先捨去小數再加總，理論上會小於等於「先加總
                                # 原始股數再捨去一次」的產業合計數字(捨去誤差最多(股票數-1)
                                # 張)，不是bug，只是張數轉換本身的無條件捨去特性；真正的
                                # 不變量在原始股數層級精確成立(見chart_data.load_industry_
                                # rotation_stocks()的docstring跟tests/test_chart_data.py的
                                # 對應測試)。這裡如實顯示兩個數字，不特別加解釋文字混淆版面，
                                # 使用者自己比對得出來。
                                stocks_total_lots = int(stocks_display["volume_lots"].sum())
                                st.caption(f"個股總成交張數加總　{stocks_total_lots:,}　／　產業合計　{int(industry_row['total_volume_lots']):,}")

    with tab_inventory:
        if tab_inventory.open:
            render_inventory_tab()

    with tab_watchlist:
        if tab_watchlist.open:
            render_watchlist_tab()

    with tab_backfill:
        if tab_backfill.open:
            render_backfill_tab()


if __name__ == "__main__":
    main()
