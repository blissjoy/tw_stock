"""PySide6桌面版專用的圖表HTML產生：在共用的`src/presentation/chart_data.build_candlestick_figure()`
回傳的Figure物件上，疊加「滑鼠十字線貫穿價格/成交量/MACD/KD子圖 + 左上角動態資訊框(取代預設
浮動tooltip)」的效果，仿TradingView超級圖表的畫法。

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

⚠️ 2026-07-25新增：使用者回報左上角的股票代號(Plotly title)跟上方legend重疊、且沒有顯示
股票名稱。桌面版原本同時有①這裡注入的資訊框(row1:日期/OHLCV)跟②Plotly自己的title
(row2:股票代號)兩層各自獨立定位的元素疊在同一塊區域——title用paper座標(跟整張圖高度成
比例)定位，資訊框用固定CSS像素定位，兩者沒有對齊機制，圖表越高(疊MACD/KD子圖時)title
的實際pixel位置就跟著飄動，容易撞在一起。改成桌面版乾脆不用Plotly自己的title(呼叫端
不再需要傳title給build_candlestick_figure，這裡也把它清空)，改成跟資訊框一樣的固定CSS
像素定位，兩者用相同機制、高度用相同基準，才不會隨圖表高度改變而錯位——`stock_label`
(股票代號+名稱)是新的第二列固定資訊框，畫在原本資訊框下方。
"""

from __future__ import annotations

import json

import pandas as pd

SPIKE_COLOR = "rgba(120,120,120,0.6)"


def render_chart_html(fig, price_df: pd.DataFrame, stock_label: str = "", div_id: str = "tw-stock-chart") -> str:
    """就地調整fig的hover/spike相關設定(呼叫端傳入的fig預期是每次重繪都新建的，這裡直接
    修改不做防禦性複製)，回傳可以直接載入QWebEngineView的完整HTML字串。

    price_df: 對應fig畫的那份OHLCV(+DIF/MACD/OSC/K/D，若有)資料(index為日期)，用來組出
    hover時要顯示的customdata。
    stock_label: 股票代號+名稱(例如"2330 台積電")，顯示在資訊框正下方的固定第二列，取代
    Plotly自己的title機制(見模組docstring說明原因)。
    """
    fig.update_layout(title=dict(text=""))  # 桌面版改用下面的固定CSS列顯示股票代號+名稱，不用Plotly自己的title
    fig.update_xaxes(showspikes=False)  # 關掉共用層的原生x軸spike，改用下面的JS自訂線
    fig.update_yaxes(
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikecolor=SPIKE_COLOR, spikethickness=1, spikedash="solid",
    )
    # margin.t要夠高才能同時放下①②兩列固定資訊框(各約20px高)＋③legend(見chart_data.py
    # 裡legend用yanchor="bottom",y=1.01往上長的定位方式，跟這裡的t=80搭配才不會被①②遮住)。
    fig.update_layout(hovermode="x", margin=dict(t=80))

    customdata = [
        [str(idx.date()), row["open"], row["high"], row["low"], row["close"], int(row["volume"])]
        for idx, row in price_df.iterrows()
    ]
    has_macd = {"DIF", "MACD", "OSC"}.issubset(price_df.columns)
    has_kd = {"K", "D"}.issubset(price_df.columns)
    macd_customdata = (
        [[str(idx.date()), row["DIF"], row["MACD"], row["OSC"]] for idx, row in price_df.iterrows()]
        if has_macd else None
    )
    kd_customdata = (
        [[str(idx.date()), row["K"], row["D"]] for idx, row in price_df.iterrows()]
        if has_kd else None
    )

    for trace in fig.data:
        # OSC柱狀體/K線是MACD/KD子圖裡各自專門「攜帶」該天完整數值的carrier trace(見下面
        # customdata)，DIF/MACD訊號線/D線本身的數值已經包含在carrier的customdata裡一起
        # 顯示，不需要各自再觸發一次hover事件，一律"skip"跟均線/切線一樣。
        if trace.name == "OSC" and macd_customdata is not None:
            trace.customdata = macd_customdata
            trace.hoverinfo = "none"
        elif trace.name == "K" and kd_customdata is not None:
            trace.customdata = kd_customdata
            trace.hoverinfo = "none"
        elif trace.type in ("candlestick", "bar"):
            # hoverinfo="none"：hover事件照常觸發(拿得到customdata)，只是不顯示Plotly
            # 預設的浮動tooltip內容——資訊改由下面注入的JS畫在左上角。
            trace.customdata = customdata
            trace.hoverinfo = "none"
        else:
            # 均線/切線/支撐壓力線/DIF/MACD訊號線/D線一律"skip"：完全不參與hover偵測，
            # 避免它們各自跳出自己的小標籤跟資訊框搶畫面。
            trace.hoverinfo = "skip"

    customdata_json = json.dumps(customdata)
    macd_customdata_json = json.dumps(macd_customdata) if macd_customdata is not None else "null"
    kd_customdata_json = json.dumps(kd_customdata) if kd_customdata is not None else "null"
    stock_label_json = json.dumps(stock_label)
    post_script = f"""
    var customdata_json = {customdata_json};
    var macd_customdata_json = {macd_customdata_json};
    var kd_customdata_json = {kd_customdata_json};
    var gd = document.getElementById('{div_id}');
    gd.parentElement.style.position = 'relative';

    function makeFixedBox(top) {{
        var box = document.createElement('div');
        box.style.position = 'absolute';
        box.style.top = top;
        box.style.left = '52px';
        box.style.zIndex = 1000;
        box.style.fontSize = '13px';
        box.style.fontFamily = 'sans-serif';
        box.style.color = '#222';
        box.style.background = 'rgba(255,255,255,0.88)';
        box.style.padding = '3px 10px';
        box.style.borderRadius = '4px';
        box.style.pointerEvents = 'none';
        box.style.whiteSpace = 'nowrap';
        gd.parentElement.insertBefore(box, gd);
        return box;
    }}

    var infoBox = makeFixedBox('6px');
    var labelBox = makeFixedBox('28px');
    labelBox.innerHTML = '<b>' + {stock_label_json} + '</b>';

    function fmtRow(d) {{
        var color = d[4] >= d[1] ? '#c0392b' : '#1a1a1a';
        return '<b>' + d[0] + '</b>&nbsp;&nbsp;開<span style="color:' + color + '">' + d[1].toFixed(2)
            + '</span>&nbsp;高<span style="color:' + color + '">' + d[2].toFixed(2)
            + '</span>&nbsp;低<span style="color:' + color + '">' + d[3].toFixed(2)
            + '</span>&nbsp;收<span style="color:' + color + '">' + d[4].toFixed(2)
            + '</span>&nbsp;&nbsp;量 ' + d[5].toLocaleString();
    }}

    // 找出chart_data.py裡用name="macd-hover-value"/"kd-hover-value"標記的annotation
    // 在annotations陣列裡實際的index，才能用Plotly.relayout()精準更新那一則的文字內容
    // (不能假設固定index，annotations清單的組成順序會隨show_macd/show_kd是否開啟而變)。
    function findAnnotationIndex(name) {{
        var annotations = gd.layout.annotations || [];
        for (var i = 0; i < annotations.length; i++) {{
            if (annotations[i].name === name) return i;
        }}
        return -1;
    }}
    var macdAnnotationIdx = findAnnotationIndex('macd-hover-value');
    var kdAnnotationIdx = findAnnotationIndex('kd-hover-value');

    function fmtMacd(d) {{
        return 'DIF ' + d[1].toFixed(2) + '\\u3000MACD ' + d[2].toFixed(2) + '\\u3000OSC ' + d[3].toFixed(2);
    }}
    function fmtKd(d) {{
        return 'K ' + d[1].toFixed(1) + '\\u3000D ' + d[2].toFixed(1);
    }}

    // 預設(未hover時)顯示最後一天的數值
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
        // ⚠️ hovermode="x"在上下堆疊的多個子圖(價格/成交量/MACD/KD各自獨立y軸)下，
        // 只會回傳「滑鼠當下實際所在那個子圖」的hover points，不會像同一子圖裡那樣自動
        // 收集其他子圖的對應點——一開始嘗試用customdata長度分辨price/macd/kd分別更新，
        // 結果滑鼠停在價格圖時MACD/KD框完全不會動，就是因為MACD/KD子圖根本沒有觸發到
        // 自己的hover point。改成不管滑鼠實際在哪個子圖，只取第一個有pointNumber的
        // point，這個數字是該筆資料在原始df裡的位置索引——因為customdata_json/
        // macd_customdata_json/kd_customdata_json三者都是用同一份price_df、依相同
        // 順序組出來的，同一個index天生就對應同一天，不需要真的從對應子圖拿到hover
        // point，直接用index去查三份陣列即可，三個資訊框才能一次同步更新。
        var idx = null, xValue = null;
        for (var i = 0; i < evt.points.length; i++) {{
            var p = evt.points[i];
            if (p.pointNumber !== undefined && p.pointNumber !== null) {{
                idx = p.pointNumber;
                xValue = p.x;
                break;
            }}
        }}
        if (idx === null) return;
        infoBox.innerHTML = fmtRow(customdata_json[idx]);
        var relayoutUpdate = {{}};
        if (macdAnnotationIdx >= 0 && macd_customdata_json && macd_customdata_json[idx]) {{
            relayoutUpdate['annotations[' + macdAnnotationIdx + '].text'] = fmtMacd(macd_customdata_json[idx]);
        }}
        if (kdAnnotationIdx >= 0 && kd_customdata_json && kd_customdata_json[idx]) {{
            relayoutUpdate['annotations[' + kdAnnotationIdx + '].text'] = fmtKd(kd_customdata_json[idx]);
        }}
        drawVerticalLine(xValue);
        if (Object.keys(relayoutUpdate).length > 0) {{
            Plotly.relayout(gd, relayoutUpdate);
        }}
    }});
    gd.on('plotly_unhover', function(evt) {{
        infoBox.innerHTML = fmtRow(customdata_json[customdata_json.length - 1]);
        var relayoutUpdate = {{}};
        if (macdAnnotationIdx >= 0 && macd_customdata_json) {{
            relayoutUpdate['annotations[' + macdAnnotationIdx + '].text'] = fmtMacd(macd_customdata_json[macd_customdata_json.length - 1]);
        }}
        if (kdAnnotationIdx >= 0 && kd_customdata_json) {{
            relayoutUpdate['annotations[' + kdAnnotationIdx + '].text'] = fmtKd(kd_customdata_json[kd_customdata_json.length - 1]);
        }}
        relayoutUpdate.shapes = [];
        Plotly.relayout(gd, relayoutUpdate);
    }});
    """

    return fig.to_html(include_plotlyjs=True, full_html=True, div_id=div_id, post_script=post_script)
