"""Streamlit 儀表板：顯示每日選股結果（daily_candidates 表最新一天，可點選清單中任一列
直接看該檔股票的價格走勢），也可以手動查詢任意股票代號。

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
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import trading_calendar  # noqa: E402
from src.indicators.moving_average import FULL_PERIODS, compute_ma_set  # noqa: E402
from src.patterns import chart_overlays, latest_day_summary  # noqa: E402

_MA_COLORS = {
    5: "#2e86de", 10: "#e67e22", 20: "#8e44ad",
    60: "#16a085", 120: "#7f8c8d", 240: "#b8860b",
}

_TRENDLINE_LABELS = {
    "up_tangent": "上升切線", "down_tangent": "下降切線",
    "up_channel": "上升軌道線", "down_channel": "下降軌道線",
}
_TRENDLINE_STYLES = {
    "up_tangent": {"color": "#1565c0", "dash": "solid"},
    "down_tangent": {"color": "#d84315", "dash": "solid"},
    "up_channel": {"color": "#64b5f6", "dash": "dash"},
    "down_channel": {"color": "#ffab40", "dash": "dash"},
}
_SR_ROLE_COLORS = {"支撐": "#16a085", "壓力": "#c0392b"}


def load_latest_candidates(conn) -> tuple[pd.DataFrame, str | None]:
    """回傳 (最新一天的候選清單DataFrame, 該日期字串)；尚無任何紀錄時回傳(空DataFrame, None)。"""
    latest_date = conn.execute("SELECT MAX(date) FROM daily_candidates").fetchone()[0]
    if latest_date is None:
        return pd.DataFrame(), None
    cur = conn.execute(
        """
        SELECT dc.stock_id, s.name, dc.signal_name, dc.entry_price, dc.stop_loss, dc.note
        FROM daily_candidates dc LEFT JOIN stocks s ON dc.stock_id = s.stock_id
        WHERE dc.date = ? ORDER BY dc.stock_id
        """,
        (latest_date,),
    )
    columns = [d[0] for d in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=columns), latest_date


def load_price_history(conn, stock_id: str, days: int = 120) -> pd.DataFrame:
    """回傳指定股票最近days天的OHLCV+均線(MA5/10/20/60/120/240，欄位名MA{n})，依date遞增
    排序、index為date；查無資料回傳空DataFrame。

    多抓 max(FULL_PERIODS) 天的歷史資料當計算緩衝，讓均線在整個顯示範圍內都有值
    （而不是從顯示視窗的第一天才開始算、前面一大段是NaN），抓完才裁切回實際要顯示的days天。
    """
    lookback_days = days + max(FULL_PERIODS)
    cur = conn.execute(
        "SELECT date, open, high, low, close, volume FROM stock_prices WHERE stock_id = ? ORDER BY date DESC LIMIT ?",
        (stock_id, lookback_days),
    )
    columns = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=columns).iloc[::-1].reset_index(drop=True)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df = df.join(compute_ma_set(df["close"], periods=FULL_PERIODS))
    return df.tail(days)


def load_holidays_for_chart(df: pd.DataFrame) -> tuple[list[str], bool]:
    """回傳(該圖表資料範圍內的休市日清單, 是否成功抓取)。TWSE假日曆這一步是畫圖路徑上
    新增的網路依賴，抓取失敗時回傳([], False)而不拋例外，呼叫端可以決定要不要提示使用者
    「假日清單暫時無法取得」，圖表仍能正常畫出來(退回只套用週末rangebreak)。
    """
    if df.empty:
        return [], True
    try:
        return trading_calendar.holidays_between(df.index.min().year, df.index.max().year), True
    except Exception:  # noqa: BLE001 - 不應該讓TWSE暫時打不通就讓整張圖表壞掉
        return [], False


def build_candlestick_figure(
    df: pd.DataFrame, title: str = "", holidays: list[str] | None = None, ma_periods: tuple[int, ...] = (),
    trendlines: dict | None = None, show_trendline_keys: tuple[str, ...] = (),
    sr_levels: list[dict] | None = None, show_support_resistance: bool = False,
):
    """把OHLC資料畫成K線圖(非線圖)+下方成交量子圖，可疊加均線/切線軌道線/支撐壓力。漲用紅、
    跌用黑，比照書中與規則庫(candles.py)一貫的紅K/黑K命名慣例(台股K線圖傳統配色，紅漲黑跌，
    與美股常見的綠漲紅跌相反)；成交量長條比照同一套配色，當天收紅用紅色、收黑用黑色。

    holidays: 該資料範圍內的休市日期清單("YYYY-MM-DD")，連同週末一起設成x軸的
    rangebreaks，避免非交易日在圖上留白間斷(維持真正的日期型x軸，不是改用category型)。
    ma_periods: 要疊加顯示的均線天期(例如(5,20,60))，對應df裡由load_price_history算好的
    MA{n}欄位；書中預設核心3線是MA5/10/20，可擴充至MA60(季線)/MA120(半年線)/MA240(年線)
    做4~6線多空排列判斷（不是MA200，書裡沒有這個天期）。
    trendlines/show_trendline_keys: src.patterns.chart_overlays.compute_trendlines()算出的
    切線/軌道線字典，與要實際畫出的key清單(例如("up_tangent","up_channel"))。
    sr_levels/show_support_resistance: src.patterns.chart_overlays.compute_support_resistance_levels()
    算出的支撐壓力清單，與是否要畫出來。
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.03,
    )
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        increasing_line_color="#c0392b", increasing_fillcolor="#c0392b",
        decreasing_line_color="#1a1a1a", decreasing_fillcolor="#1a1a1a",
        name="", showlegend=False,
    ), row=1, col=1)

    for n in ma_periods:
        col = f"MA{n}"
        if col not in df.columns:
            continue
        fig.add_trace(go.Scatter(
            x=df.index, y=df[col], mode="lines", name=col,
            line=dict(color=_MA_COLORS.get(n, "#999999"), width=1.3),
        ), row=1, col=1)

    for key in show_trendline_keys:
        if not trendlines or key not in trendlines:
            continue
        dates, prices = chart_overlays.trendline_to_xy(trendlines[key], df)
        style = _TRENDLINE_STYLES.get(key, {"color": "#999999", "dash": "solid"})
        fig.add_trace(go.Scatter(
            x=dates, y=prices, mode="lines", name=_TRENDLINE_LABELS.get(key, key),
            line=dict(color=style["color"], dash=style["dash"], width=1.5),
        ), row=1, col=1)

    if show_support_resistance and sr_levels:
        for level in sr_levels:
            color = _SR_ROLE_COLORS.get(level["role"], "#999999")
            fig.add_trace(go.Scatter(
                x=[df.index[0], df.index[-1]], y=[level["price"], level["price"]], mode="lines",
                name=f"{level['role']} {level['price']:.2f}",
                line=dict(color=color, dash="dot", width=1),
            ), row=1, col=1)

    volume_colors = ["#c0392b" if c >= o else "#1a1a1a" for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=df.index, y=df["volume"], marker_color=volume_colors, name="成交量", showlegend=False), row=2, col=1)

    fig.update_layout(
        title=title,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=40 if title else 10, b=10),
        height=560,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
    )
    fig.update_yaxes(title_text="價格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)

    rangebreaks = [dict(bounds=["sat", "mon"])]
    if holidays:
        rangebreaks.append(dict(values=holidays))
    fig.update_xaxes(rangebreaks=rangebreaks)
    return fig


def main() -> None:
    import streamlit as st
    from streamlit.errors import StreamlitSecretNotFoundError

    from src.data import storage, turso_client
    from src.screener.daily_screener import run_screen_and_store

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
        # 開發階段可設定 LOCAL_DB_PATH 環境變數，改連本機sqlite檔案而不連線Turso，
        # 讓「跑起來看畫面」不必依賴Turso帳號/網路，本機開發完全確定沒問題後再切回Turso。
        local_db_path = os.environ.get("LOCAL_DB_PATH")
        if local_db_path:
            # check_same_thread=False：@st.cache_resource 快取的連線在不同次rerun間可能被
            # 不同thread重用，sqlite3預設會拒絕跨thread共用同一個connection。
            return storage.init_db(local_db_path, check_same_thread=False)

        # Turso資料庫可能是全新、還沒被seed_turso_from_local.py或daily_pipeline.py建過表的狀態
        # （例如儀表板比每日pipeline更早部署），這裡確保schema一定存在，query才不會噴
        # "no such table" 的錯誤。
        conn = turso_client.get_connection()
        storage.ensure_schema(conn)
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
        trendline_options = [_TRENDLINE_LABELS[key] for key in _TRENDLINE_LABELS if key in trendlines]
        label_to_key = {v: k for k, v in _TRENDLINE_LABELS.items()}
        col1, col2 = st.columns([3, 1])
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
        selected_trendline_keys = tuple(label_to_key[label] for label in selected_trendline_labels)
        sr_levels = chart_overlays.compute_support_resistance_levels(price_df) if show_sr else []

        st.plotly_chart(
            build_candlestick_figure(
                price_df, holidays=holidays, ma_periods=selected_periods,
                trendlines=trendlines, show_trendline_keys=selected_trendline_keys,
                sr_levels=sr_levels, show_support_resistance=show_sr,
            ),
            use_container_width=True,
        )
        st.dataframe(price_df.tail(20), use_container_width=True)

        summary = latest_day_summary.summarize_latest_day(price_df)
        latest_date_label = price_df.index[-1].strftime("%Y-%m-%d")
        st.markdown(f"**📋 最新交易日分析（{latest_date_label}）**")
        st.write(f"K棒名稱：{summary['candle_name']}")
        st.write("型態訊號：" + ("、".join(summary["patterns"]) if summary["patterns"] else "無明顯型態"))
        st.write("量價訊號：" + ("、".join(summary["volume_signals"]) if summary["volume_signals"] else "無明顯訊號"))
        st.caption("⚠️ 型態訊號僅判斷幾何條件是否成立，尚未確認是否位於真正的高檔/低檔位置（趨勢位置模組尚未實作）。")

    st.title("📈 台股每日選股")
    st.caption("資料來源：TWSE / TPEx(透過FinMind) — 每日收盤後自動更新")

    if st.button("🔄 立即重新篩選"):
        # 只用資料庫裡目前已有的資料重算訊號，不重新對外抓取TWSE/TPEx資料(那個很慢，交給
        # 每日排程做)，所以這個按鈕通常幾秒內就能算完，可以隨時按而不用擔心額度或等待。
        with st.spinner("正在用目前資料庫裡的最新資料重新計算選股訊號..."):
            run_screen_and_store(conn)
        st.success("已重新計算完成，候選清單已更新。")

    candidates_df, latest_date = load_latest_candidates(conn)

    selected_stock_id = None
    if latest_date is None:
        st.info("目前 Turso 資料庫裡還沒有任何每日選股紀錄，點上方「立即重新篩選」或等 GitHub Actions 排程跑完後就會顯示。")
    else:
        st.subheader(f"最新候選清單（{latest_date}，共 {len(candidates_df)} 檔）")
        if candidates_df.empty:
            st.write("這一天沒有符合條件的候選股。")
        else:
            st.caption("點選任一列可在下方查看該檔股票的價格走勢")
            event = st.dataframe(
                candidates_df, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row", key="candidates_table",
            )
            if event.selection.rows:
                selected_stock_id = str(candidates_df.iloc[event.selection.rows[0]]["stock_id"])

    st.divider()

    if selected_stock_id:
        st.subheader(f"📊 {selected_stock_id} 價格走勢（點選自候選清單）")
        render_price_chart(selected_stock_id, widget_key="drilldown")
        st.divider()

    st.subheader("個股價格走勢查詢（手動輸入任意股票代號）")
    stock_id = st.text_input("輸入股票代號（例如 2330）", value="")
    if stock_id:
        render_price_chart(stock_id.strip(), widget_key="manual")


if __name__ == "__main__":
    main()
