"""前端無關的圖表資料組裝層：把「從DB撈資料」跟「畫成Plotly Figure」這兩件事，從Streamlit
(`dashboard/app.py`)搬到這裡，讓PySide6桌面版(`desktop/`)也能呼叫同一套函式、算出完全相同
的`plotly.graph_objects.Figure`，不用在兩個UI框架裡各寫一份K線/均線/切線/支撐壓力的畫圖邏輯。

這裡的函式全部是純函式（吃`conn`/`DataFrame`，回傳`DataFrame`/`Figure`），完全沒有任何UI
框架呼叫——這正是2026-07-23架構調整（改回本機優先、PySide6桌面版）時，從`dashboard/app.py`
搬出來的部分（搬移前它們本來就已經不依賴`st.*`，只是定義位置卡在Streamlit檔案裡，其他前端
沒辦法import）。
"""

from __future__ import annotations

import pandas as pd

from src.data import trading_calendar
from src.indicators.moving_average import FULL_PERIODS, compute_ma_set
from src.patterns import chart_overlays

MA_COLORS = {
    5: "#2e86de", 10: "#e67e22", 20: "#8e44ad",
    60: "#16a085", 120: "#7f8c8d", 240: "#b8860b",
}

TRENDLINE_LABELS = {
    "up_tangent": "上升切線", "down_tangent": "下降切線",
    "up_channel": "上升軌道線", "down_channel": "下降軌道線",
}
TRENDLINE_STYLES = {
    "up_tangent": {"color": "#1565c0", "dash": "solid"},
    "down_tangent": {"color": "#d84315", "dash": "solid"},
    "up_channel": {"color": "#64b5f6", "dash": "dash"},
    "down_channel": {"color": "#ffab40", "dash": "dash"},
}
# 每種切線預設的role(未被跌破/突破時)，用來偵測R-LINE-11/12的角色互換是否發生過
# （up_channel/down_channel目前的實作沒有另外套用跌破/突破檢查，role固定不變）。
TRENDLINE_DEFAULT_ROLE = {
    "up_tangent": "support", "down_tangent": "resistance",
    "up_channel": "resistance", "down_channel": "support",
}
SR_ROLE_COLORS = {"支撐": "#16a085", "壓力": "#c0392b"}


def load_latest_candidates(conn) -> tuple[pd.DataFrame, str | None]:
    """回傳 (最新一天的候選清單DataFrame, 該日期字串)；尚無任何紀錄時回傳(空DataFrame, None)。

    同一檔股票如果同時符合多條規則(daily_candidates裡有多筆同stock_id、不同signal_name
    的紀錄，例如同時觸發R-TREND-14跟R-SCREEN-15)，這裡會合併成一列顯示，不是一條規則
    一列——signal_name/note欄位都用「\n」換行字元分隔多條規則的內容(而不是逗號/頓號
    這類同一行內的分隔符)，讓桌面版(desktop/main_window.py開了word wrap +
    resizeRowsToContents)能在同一格內分成好幾行顯示，一條規則一行，比擠在同一行裡
    好讀。⚠️ Streamlit的`st.dataframe`不支援儲存格內換行(實測\n會被吃成空白、
    HTML的<br>則會顯示成字面文字)，這是該元件本身的限制；用\n分隔在那邊會退化成
    單行、規則之間用空白隔開，不是bug，是目前Streamlit這個次要前端能接受的降級效果。
    entry_price/stop_loss取合併前第一筆的值：目前已接上的規則都是用同一天的收盤價/同一套
    停損公式(bull_short_term_stop_loss)，理論上同一檔股票不管觸發幾條規則，算出來的值
    本來就會相同；之後如果加入用不同公式的規則導致同一天算出不同的進場價/停損價，這裡
    仍然只顯示第一筆，不特別提示「多個不同數值」，避免為了目前用不到的情境過度設計UI。
    """
    latest_date = conn.execute("SELECT MAX(date) FROM daily_candidates").fetchone()[0]
    if latest_date is None:
        return pd.DataFrame(), None
    cur = conn.execute(
        """
        SELECT dc.stock_id, s.name, dc.signal_name, dc.entry_price, dc.stop_loss, dc.note
        FROM daily_candidates dc LEFT JOIN stocks s ON dc.stock_id = s.stock_id
        WHERE dc.date = ? ORDER BY dc.stock_id, dc.created_at
        """,
        (latest_date,),
    )
    columns = [d[0] for d in cur.description]
    raw_df = pd.DataFrame(cur.fetchall(), columns=columns)
    if raw_df.empty:
        return raw_df, latest_date

    merged_rows = []
    for stock_id, group in raw_df.groupby("stock_id", sort=False):
        first = group.iloc[0]
        # note在DB裡是NULL時，pandas讀回來會變成float NaN(不是Python None)，而NaN在布林
        # 判斷下是truthy的(bool(float('nan')) is True)——用pd.notna()才能正確排除掉，
        # 否則合併結果會混進字面上的"nan"字樣。
        notes = [f"{row.signal_name}：{row.note}" for row in group.itertuples() if pd.notna(row.note)]
        merged_rows.append({
            "stock_id": stock_id,
            "name": first["name"],
            "signal_name": "\n".join(group["signal_name"]),
            "entry_price": first["entry_price"],
            "stop_loss": first["stop_loss"],
            "note": "\n".join(notes),
        })
    merged_df = pd.DataFrame(merged_rows, columns=["stock_id", "name", "signal_name", "entry_price", "stop_loss", "note"])
    merged_df = merged_df.sort_values("stock_id").reset_index(drop=True)
    return merged_df, latest_date


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
            line=dict(color=MA_COLORS.get(n, "#999999"), width=1.3),
        ), row=1, col=1)

    for key in show_trendline_keys:
        if not trendlines or key not in trendlines:
            continue
        line = trendlines[key]
        dates, prices = chart_overlays.trendline_to_xy(line, df)
        style = TRENDLINE_STYLES.get(key, {"color": "#999999", "dash": "solid"})
        label = TRENDLINE_LABELS.get(key, key)
        color = style["color"]
        # R-LINE-11/12：這條線如果已經被跌破(上升切線)或突破(下降切線)，role會被
        # compute_trendlines()就地互換過，不再是預設角色——用支撐/壓力的顏色改標示，
        # 不要讓使用者誤以為它還在發揮原本的作用(這正是使用者回報的問題：舊切線畫得
        # 好像還在支撐現在的股價，但其實早就跌破、對「現在」已經沒有意義)。
        if line.role != TRENDLINE_DEFAULT_ROLE.get(key, line.role):
            swapped_to = "壓力" if line.role == "resistance" else "支撐"
            label = f"{label}（已{'跌破' if swapped_to == '壓力' else '突破'}，轉{swapped_to}）"
            color = SR_ROLE_COLORS.get(swapped_to, color)
        fig.add_trace(go.Scatter(
            x=dates, y=prices, mode="lines", name=label,
            line=dict(color=color, dash=style["dash"], width=1.5),
        ), row=1, col=1)

    if show_support_resistance and sr_levels:
        for level in sr_levels:
            color = SR_ROLE_COLORS.get(level["role"], "#999999")
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

    # 淡灰色十字線(hover時顯示滑鼠對應的X/Y位置)，仿TradingView的畫法。這裡只設定Plotly
    # 原生的spike line，兩個前端(Streamlit/PySide6)都能用；但實測發現原生x軸spike的
    # "across"模式在上下堆疊子圖(價格/成交量)的情況下，垂直線只會畫在滑鼠所在那一格，
    # 不會真的貫穿到另一個子圖——這是PySide6桌面版才需要的「垂直線貫穿兩個子圖」效果，
    # desktop/chart_render.py會在桌面版另外用自訂JS覆蓋掉這裡的x軸spike設定來達成；
    # Streamlit版沒有這個機制，維持這裡的原生效果(垂直線只在單一子圖內顯示)已經是不錯的
    # 折衷，不需要為了它額外做什麼。
    spike_style = dict(
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikecolor="rgba(120,120,120,0.6)", spikethickness=1, spikedash="solid",
    )
    fig.update_xaxes(**spike_style)
    fig.update_yaxes(**spike_style)
    fig.update_layout(hovermode="x")
    return fig
