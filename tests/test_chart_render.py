import json

import pandas as pd
import pytest

from src.presentation import chart_data
from src.presentation.chart_render import render_chart_html


def _sample_df(n: int = 5) -> pd.DataFrame:
    dates = pd.date_range("2026-07-01", periods=n)
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)], "high": [105.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)], "close": [102.0 + i for i in range(n)],
            "volume": [1000 + i * 10 for i in range(n)],
        },
        index=dates,
    )


def test_render_chart_html_disables_native_x_spikes_and_keeps_y_spikes():
    """桌面版改用JS自訂畫的十字線貫穿兩個子圖，原生x軸spike必須關掉，避免兩條線疊在一起；
    y軸spike(各子圖獨立的水平線)維持用Plotly原生的，不需要替換。"""
    df = _sample_df()
    fig = chart_data.build_candlestick_figure(df)

    render_chart_html(fig, df)

    assert fig.layout.xaxis.showspikes is False
    assert fig.layout.yaxis.showspikes is True


def test_render_chart_html_sets_hoverinfo_none_on_candlestick_and_bar_only():
    df = _sample_df()
    fig = chart_data.build_candlestick_figure(df, ma_periods=(5,))
    # 塞一條假的MA線讓ma_periods=(5,)這種情況下也至少有一條scatter(即使df裡沒有MA5欄位
    # 會被跳過，這裡直接手動加一條驗證非candlestick/bar的trace被設成skip)
    import plotly.graph_objects as go
    fig.add_trace(go.Scatter(x=df.index, y=df["close"], mode="lines", name="MA5"))

    render_chart_html(fig, df)

    hoverinfo_by_type = {}
    for trace in fig.data:
        hoverinfo_by_type.setdefault(trace.type, set()).add(trace.hoverinfo)

    assert hoverinfo_by_type["candlestick"] == {"none"}
    assert hoverinfo_by_type["bar"] == {"none"}
    assert hoverinfo_by_type["scatter"] == {"skip"}


def test_render_chart_html_attaches_customdata_matching_price_df_rows():
    df = _sample_df(n=3)
    fig = chart_data.build_candlestick_figure(df)

    render_chart_html(fig, df)

    candlestick = next(t for t in fig.data if t.type == "candlestick")
    assert len(candlestick.customdata) == 3
    first_row = candlestick.customdata[0]
    assert first_row[0] == "2026-07-01"
    assert first_row[1] == 100.0  # open
    assert first_row[4] == 102.0  # close
    assert first_row[5] == 1000  # volume


def test_render_chart_html_embeds_div_id_and_hover_js_hooks():
    df = _sample_df()
    fig = chart_data.build_candlestick_figure(df)

    html = render_chart_html(fig, df, div_id="my-custom-div")

    assert 'id="my-custom-div"' in html
    assert "plotly_hover" in html
    assert "plotly_unhover" in html
    assert "drawVerticalLine" in html


def test_render_chart_html_embeds_y_axis_price_crosshair_js_hooks():
    """仿TradingView：滑鼠十字線要在Y軸顯示游標對應的數值，見chart_render.py
    2026-07-29新增、2026-08-02廣義化成每個子圖都適用的getAxisValueAtCursorY()+
    mousemove監聽(見axisValueBox，原本叫priceAxisBox/getPriceAtCursorY，只支援
    價格子圖)。"""
    df = _sample_df()
    fig = chart_data.build_candlestick_figure(df)

    html = render_chart_html(fig, df)

    assert "getAxisValueAtCursorY" in html
    assert "axisValueBox" in html
    assert "mousemove" in html
    assert "mouseleave" in html


def test_render_chart_html_customdata_json_is_valid_and_matches_row_count():
    df = _sample_df(n=4)
    fig = chart_data.build_candlestick_figure(df)

    html = render_chart_html(fig, df)

    start = html.index("var customdata_json = ") + len("var customdata_json = ")
    end = html.index(";", start)
    embedded = json.loads(html[start:end])
    assert len(embedded) == 4


def test_render_chart_html_customdata_includes_pct_change_as_seventh_field():
    """2026-08-12新增：使用者要求第2列(日期/OHLCV)加上漲跌幅，這裡驗證customdata每列
    多了一個(今日收盤-前一日收盤)/前一日收盤*100的欄位，第一天沒有前一日資料是None，
    不是0(0%代表「跟昨天平盤」，語意不同，見render_chart_html()裡的說明)。"""
    df = _sample_df(n=3)  # close: 102, 103, 104
    fig = chart_data.build_candlestick_figure(df)

    render_chart_html(fig, df)

    candlestick = next(t for t in fig.data if t.type == "candlestick")
    assert candlestick.customdata[0][6] is None
    assert candlestick.customdata[1][6] == pytest.approx((103 - 102) / 102 * 100)
    assert candlestick.customdata[2][6] == pytest.approx((104 - 103) / 103 * 100)


def test_render_chart_html_ma_row_defaults_to_ma60_not_ma120():
    """2026-08-12改版：第3列(MA方向)預設要包含MA60、不包含MA120(除非show_ma120=True)，
    見MA_ROW_BASE_PERIODS的說明。"""
    df = _sample_df()
    fig = chart_data.build_candlestick_figure(df)

    html = render_chart_html(fig, df)

    start = html.index("var ma_row_labels = ") + len("var ma_row_labels = ")
    end = html.index(";", start)
    ma_row_labels = json.loads(html[start:end])
    assert ma_row_labels == ["MA5", "MA10", "MA20", "MA60", "MA240"]


def test_render_chart_html_show_ma120_true_inserts_ma120_between_ma60_and_ma240():
    df = _sample_df()
    fig = chart_data.build_candlestick_figure(df)

    html = render_chart_html(fig, df, show_ma120=True)

    start = html.index("var ma_row_labels = ") + len("var ma_row_labels = ")
    end = html.index(";", start)
    ma_row_labels = json.loads(html[start:end])
    assert ma_row_labels == ["MA5", "MA10", "MA20", "MA60", "MA120", "MA240"]
