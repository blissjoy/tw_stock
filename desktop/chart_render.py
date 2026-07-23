"""PySide6桌面版專用的圖表HTML產生：在共用的`src/presentation/chart_data.build_candlestick_figure()`
回傳的Figure物件上，疊加「滑鼠十字線貫穿價格/成交量兩個子圖 + 左上角動態資訊框(取代預設浮動
tooltip)」的效果，仿TradingView超級圖表的畫法。

⚠️ 這裡的自訂JS(post_script)只有透過`QWebEngineView`直接載入原始HTML才會執行——Streamlit的
`st.plotly_chart()`是用自己的React元件重新渲染Figure的JSON規格，不會執行任何額外注入的JS，
所以這個模組只給`desktop/`使用，不動`src/presentation/chart_data.py`共用的部分（那邊維持
Plotly原生的hover/spike設定，兩個前端都適用，只是效果沒有這裡完整）。

實測踩過的坑：
- Plotly原生x軸spike line的"across"模式，在上下堆疊子圖(價格/成交量分屬不同y軸domain)的
  情況下，垂直線只會畫在滑鼠所在那一格、不會真的貫穿到另一個子圖——這裡改用JS在
  `plotly_hover`時透過`Plotly.relayout()`動態畫一條`yref='paper'`(y0=0到y1=1，貫穿整張
  圖紙面高度)的shape線，才能真正同時穿過價格圖跟成交量圖。
- `QWebEngineView.setHtml()`對內容大小有~2MB的隱性限制(Chromium的data: URL限制)，
  這裡沿用desktop/main_window.py既有的做法，回傳HTML字串由呼叫端寫進暫存檔案再用
  `load(QUrl.fromLocalFile(...))`開啟，不在這個模組處理檔案I/O。
"""

from __future__ import annotations

import json

import pandas as pd

SPIKE_COLOR = "rgba(120,120,120,0.6)"


def render_chart_html(fig, price_df: pd.DataFrame, div_id: str = "tw-stock-chart") -> str:
    """就地調整fig的hover/spike相關設定(呼叫端傳入的fig預期是每次重繪都新建的，這裡直接
    修改不做防禦性複製)，回傳可以直接載入QWebEngineView的完整HTML字串。

    price_df: 對應fig畫的那份OHLCV資料(index為日期)，用來組出hover時要顯示的customdata。
    """
    fig.update_xaxes(showspikes=False)  # 關掉共用層的原生x軸spike，改用下面的JS自訂線
    fig.update_yaxes(
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikecolor=SPIKE_COLOR, spikethickness=1, spikedash="solid",
    )
    fig.update_layout(hovermode="x", margin=dict(t=70))

    customdata = [
        [str(idx.date()), row["open"], row["high"], row["low"], row["close"], int(row["volume"])]
        for idx, row in price_df.iterrows()
    ]
    for trace in fig.data:
        if trace.type in ("candlestick", "bar"):
            # hoverinfo="none"：hover事件照常觸發(拿得到customdata)，只是不顯示Plotly
            # 預設的浮動tooltip內容——資訊改由下面注入的JS畫在左上角。
            trace.customdata = customdata
            trace.hoverinfo = "none"
        else:
            # 均線/切線/支撐壓力線一律"skip"：完全不參與hover偵測，避免它們各自跳出
            # 自己的小標籤跟左上角資訊框搶畫面。
            trace.hoverinfo = "skip"

    customdata_json = json.dumps(customdata)
    post_script = f"""
    var customdata_json = {customdata_json};
    var gd = document.getElementById('{div_id}');
    var infoBox = document.createElement('div');
    infoBox.id = 'tw-stock-info-box';
    infoBox.style.position = 'absolute';
    infoBox.style.top = '6px';
    infoBox.style.left = '52px';
    infoBox.style.zIndex = 1000;
    infoBox.style.fontSize = '13px';
    infoBox.style.fontFamily = 'sans-serif';
    infoBox.style.color = '#222';
    infoBox.style.background = 'rgba(255,255,255,0.88)';
    infoBox.style.padding = '3px 10px';
    infoBox.style.borderRadius = '4px';
    infoBox.style.pointerEvents = 'none';
    infoBox.style.whiteSpace = 'nowrap';
    gd.parentElement.style.position = 'relative';
    gd.parentElement.insertBefore(infoBox, gd);

    function fmtRow(d) {{
        var color = d[4] >= d[1] ? '#c0392b' : '#1a1a1a';
        return '<b>' + d[0] + '</b>&nbsp;&nbsp;開<span style="color:' + color + '">' + d[1].toFixed(2)
            + '</span>&nbsp;高<span style="color:' + color + '">' + d[2].toFixed(2)
            + '</span>&nbsp;低<span style="color:' + color + '">' + d[3].toFixed(2)
            + '</span>&nbsp;收<span style="color:' + color + '">' + d[4].toFixed(2)
            + '</span>&nbsp;&nbsp;量 ' + d[5].toLocaleString();
    }}

    // 預設(未hover時)顯示最後一根K棒
    infoBox.innerHTML = fmtRow(customdata_json[customdata_json.length - 1]);

    function drawVerticalLine(xValue) {{
        Plotly.relayout(gd, {{
            shapes: [{{
                type: 'line', xref: 'x', x0: xValue, x1: xValue,
                yref: 'paper', y0: 0, y1: 1,
                line: {{color: '{SPIKE_COLOR}', width: 1}},
            }}],
        }});
    }}

    gd.on('plotly_hover', function(evt) {{
        for (var i = 0; i < evt.points.length; i++) {{
            if (evt.points[i].customdata) {{
                infoBox.innerHTML = fmtRow(evt.points[i].customdata);
                drawVerticalLine(evt.points[i].x);
                break;
            }}
        }}
    }});
    gd.on('plotly_unhover', function(evt) {{
        infoBox.innerHTML = fmtRow(customdata_json[customdata_json.length - 1]);
        Plotly.relayout(gd, {{shapes: []}});
    }});
    """

    return fig.to_html(include_plotlyjs=True, full_html=True, div_id=div_id, post_script=post_script)
