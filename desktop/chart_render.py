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
股票名稱。桌面版原本同時有①這裡注入的資訊框(日期/OHLCV)跟②Plotly自己的title(股票代號)
兩層各自獨立定位的元素疊在同一塊區域——title用paper座標(跟整張圖高度成比例)定位，資訊框
用固定CSS像素定位，兩者沒有對齊機制，圖表越高(疊MACD/KD子圖時)title的實際pixel位置就
跟著飄動，容易撞在一起。改成桌面版乾脆不用Plotly自己的title(呼叫端不再需要傳title給
build_candlestick_figure，這裡也把它清空)，改成跟資訊框一樣的固定CSS像素定位，兩者用
相同機制、高度用相同基準，才不會隨圖表高度改變而錯位——`stock_label`(股票代號+名稱)是
新增的固定資訊框，畫在原本資訊框旁邊。

⚠️ 2026-08-02改版：使用者要求把股票標題列跟日期/OHLCV列的順序對調(標題在第一列)，並在
下方新增第三列顯示MA5/10/20/120/240目前數值+方向(↑/↓/=，由跟前一天的diff()判斷)，
版面調整同時把桌面版全域字級調大兩級(見desktop/main.py)。三列固定框由上到下依序是
labelBox(股票標題，top:6px，固定不隨hover變化)、infoBox(日期/OHLCV，top:28px，隨hover
更新)、maBox(MA方向，top:50px，隨hover更新)，`MA_ROW_PERIODS`常數控制第三列顯示的天期。

⚠️ 2026-07-29新增：使用者要求仿TradingView，滑鼠十字線要在Y軸顯示對應的價格數值——原生
的`yaxis.showspikes`(spikesnap="cursor")本來就會畫出跟著滑鼠實際高度走的水平線，但Plotly
沒有內建在軸上顯示對應數值的機制。這裡改用原生`mousemove`事件(不是`plotly_hover`——後者
在`hovermode="x"`下只回傳「離游標最近的資料點」，會被離散資料點snap住，不會是游標實際的
連續高度)取得游標在整個圖表容器裡的像素座標(`evt.offsetY`)，換算成價格子圖(row1)的資料
座標：用`gd._fullLayout._size`(繪圖區域相對整個容器的像素邊界)與`gd._fullLayout.yaxis.domain`
(row1的y軸在繪圖區域裡佔的比例)算出row1的像素上下界，再依`yaxis.range`線性內插得到價格。
這裡用到的`_fullLayout`/`_size`是plotly.js內部(非公開文件化)的屬性，但這個模組本來就只給
桌面版透過`include_plotlyjs=True`整包內嵌的固定版本使用(不會被外部CDN偷偷升級版本)，風險
可控；如果之後升級plotly套件版本導致這幾個內部屬性改名/消失，這個Y軸價格標籤會悄悄失效
(不影響其他功能，因為包在try/catch裡)，屆時需要重新對照新版plotly.js原始碼調整。
"""

from __future__ import annotations

import json

import pandas as pd

from src.presentation.chart_data import MA_COLORS

SPIKE_COLOR = "rgba(120,120,120,0.6)"

# 固定資訊框第3列(MA方向)要顯示的均線天期：跟src/presentation/chart_data.py裡
# CANDIDATE_FILTERS均線多頭排列篩選沿用的同一組天期一致，刻意跳過MA60。
MA_ROW_PERIODS = (5, 10, 20, 120, 240)
_MA_ROW_TRACE_NAMES = {f"MA{n}" for n in MA_ROW_PERIODS}


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
    # margin.t要夠高才能同時放下①②③三列固定資訊框＋④legend(見chart_data.py裡legend用
    # yanchor="bottom",y=1.01往上長的定位方式，位置跟著margin.t走，t不夠高legend會被①②③
    # 蓋住)。⚠️ 2026-08-02修正：字級調大兩級(16px)後，原本104px不夠——三個box的top間距
    # (6/28/50px，22px一列)是照13px字級時代抓的，16px字級+padding+line-height單一box
    # 實際渲染高度約25px，超過22px的間距，box彼此就會重疊；改成30px一列(6/36/66px)＋
    # margin.t放大到140，讓legend有足夠空間落在三列box下方。
    fig.update_layout(hovermode="x", margin=dict(t=140))

    customdata = [
        [str(idx.date()), row["open"], row["high"], row["low"], row["close"], int(row["volume"])]
        for idx, row in price_df.iterrows()
    ]
    # 第3列MA方向資訊框用：每個天期存(數值, 方向)，方向由跟前一天的diff()判斷，
    # 資料不足(NaN，例如MA240在剛上市股票的前段)時值/方向都是None，JS端會跳過不顯示。
    ma_diffs = {n: price_df[f"MA{n}"].diff() for n in MA_ROW_PERIODS if f"MA{n}" in price_df.columns}
    ma_customdata = []
    for i in range(len(price_df)):
        row_data: list = []
        for n in MA_ROW_PERIODS:
            diff_series = ma_diffs.get(n)
            value = price_df[f"MA{n}"].iloc[i] if diff_series is not None else None
            if diff_series is None or pd.isna(value):
                row_data.extend([None, None])
                continue
            diff_value = diff_series.iloc[i]
            if pd.isna(diff_value):
                direction = None
            elif diff_value > 0:
                direction = "up"
            elif diff_value < 0:
                direction = "down"
            else:
                direction = "flat"
            row_data.extend([float(value), direction])
        ma_customdata.append(row_data)
    has_macd = {"DIF", "MACD", "OSC"}.issubset(price_df.columns)
    has_kd = {"K", "D"}.issubset(price_df.columns)
    # 十字線Y軸浮動數值標籤(axisValueBox)要用：每個子圖(row)對應哪一個Plotly yaxis名稱、
    # 顯示用的軸標籤、小數位數——跟chart_data.py的macd_row/kd_row算法一致(row1固定是價格
    # /yaxis，row2固定是成交量/yaxis2，之後視show_macd/show_kd是否開啟依序往後排)。
    row_axes = [
        {"yaxis": "yaxis", "label": "價格", "decimals": 2, "thousands": False},
        {"yaxis": "yaxis2", "label": "成交量", "decimals": 0, "thousands": True},
    ]
    next_row = 3
    if has_macd:
        row_axes.append({"yaxis": f"yaxis{next_row}", "label": "MACD", "decimals": 2, "thousands": False})
        next_row += 1
    if has_kd:
        row_axes.append({"yaxis": f"yaxis{next_row}", "label": "KD", "decimals": 1, "thousands": False})
        next_row += 1
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
        # ⚠️ 2026-08-02修正：使用者截圖回報legend的MA5/10/20項目被第3列固定資訊框(maBox)
        # 蓋住看不到——maBox已經用文字顯示這幾條均線的數值+方向，Plotly原生legend裡這幾條
        # 的項目資訊完全重複，不是「不小心被蓋住」而是「這塊區域本來就被maBox佔用，底下的
        # legend項目沒有存在的必要」。改成直接把這幾條均線的legend項目關掉(showlegend=
        # False，只影響圖例顯示，線本身還是照樣畫在圖上)，legend項目數變少，剩下的项目
        # (MA60+SAR+切線/軌道線+支撐壓力+MACD/KD)才不會被三列固定框佔用的區域擋住。
        # MA60不在MA_ROW_PERIODS裡(maBox跳過MA60)，legend繼續保留它的項目。
        if trace.name in _MA_ROW_TRACE_NAMES:
            trace.showlegend = False

    customdata_json = json.dumps(customdata)
    macd_customdata_json = json.dumps(macd_customdata) if macd_customdata is not None else "null"
    kd_customdata_json = json.dumps(kd_customdata) if kd_customdata is not None else "null"
    ma_customdata_json = json.dumps(ma_customdata)
    ma_row_periods_json = json.dumps([f"MA{n}" for n in MA_ROW_PERIODS])
    # 第3列MA方向資訊框的標籤("MA5"等)改用跟圖上那條均線一樣的顏色(MA_COLORS)，取代
    # 被關掉的legend項目原本提供的「顏色→均線」對照功能(見上面showlegend=False的說明)。
    ma_row_colors_json = json.dumps([MA_COLORS.get(n, "#999999") for n in MA_ROW_PERIODS])
    row_axes_json = json.dumps(row_axes)
    stock_label_json = json.dumps(stock_label)
    post_script = f"""
    var customdata_json = {customdata_json};
    var macd_customdata_json = {macd_customdata_json};
    var kd_customdata_json = {kd_customdata_json};
    var ma_customdata_json = {ma_customdata_json};
    var ma_row_labels = {ma_row_periods_json};
    var ma_row_colors = {ma_row_colors_json};
    var row_axes = {row_axes_json};
    var gd = document.getElementById('{div_id}');
    gd.parentElement.style.position = 'relative';

    function makeFixedBox(top) {{
        var box = document.createElement('div');
        box.style.position = 'absolute';
        box.style.top = top;
        box.style.left = '52px';
        box.style.zIndex = 1000;
        box.style.fontSize = '16px';
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

    // 第1列：股票標題(固定不隨hover變化)；第2列：日期/OHLCV(隨hover更新)；
    // 第3列：MA5/10/20/120/240目前數值+方向(隨hover更新)。
    var labelBox = makeFixedBox('6px');
    var infoBox = makeFixedBox('36px');
    var maBox = makeFixedBox('66px');
    labelBox.innerHTML = '<b>' + {stock_label_json} + '</b>';

    // 仿TradingView的Y軸數值標籤：跟著滑鼠游標的實際高度顯示對應數值，不是snap到最近
    // 的資料點(customdata裡的值)。哪個子圖(價格/成交量/MACD/KD)都適用，只在游標落在
    // 對應子圖的垂直範圍內才顯示，離開圖表時隱藏——2026-08-02改版：原本只有價格子圖
    // (row1)有這個標籤，改成廣義化成每個子圖都有，跟著滑鼠所在的子圖顯示該圖的軸標籤
    // (價格/成交量/MACD/KD)+對應數值(見row_axes)。
    var axisValueBox = document.createElement('div');
    axisValueBox.style.position = 'absolute';
    axisValueBox.style.left = '2px';
    axisValueBox.style.zIndex = 1000;
    axisValueBox.style.fontSize = '15px';
    axisValueBox.style.fontFamily = 'sans-serif';
    axisValueBox.style.color = '#fff';
    axisValueBox.style.background = '#333333';
    axisValueBox.style.padding = '1px 5px';
    axisValueBox.style.borderRadius = '3px';
    axisValueBox.style.pointerEvents = 'none';
    axisValueBox.style.whiteSpace = 'nowrap';
    axisValueBox.style.display = 'none';
    gd.parentElement.insertBefore(axisValueBox, gd);

    // 把游標在整個圖表容器裡的像素高度(offsetY)換算成「游標所在那個子圖」的資料值：
    // 依序試row_axes每一列，用gd._fullLayout._size(繪圖區域相對容器的像素邊界)+該列
    // yaxis.domain(佔繪圖區域的比例)算出這個子圖的像素上下界，游標落在哪個子圖的範圍
    // 內就回傳那個子圖的軸標籤+換算出來的數值；不在任何子圖範圍內(例如落在子圖間的
    // 間距)回傳null。
    function getAxisValueAtCursorY(offsetY) {{
        try {{
            var size = gd._fullLayout._size;
            for (var i = 0; i < row_axes.length; i++) {{
                var axisInfo = row_axes[i];
                var yaxis = gd._fullLayout[axisInfo.yaxis];
                if (!yaxis) continue;
                var domain = yaxis.domain;
                var rowTopPx = size.t + size.h * (1 - domain[1]);
                var rowBottomPx = size.t + size.h * (1 - domain[0]);
                if (offsetY < rowTopPx || offsetY > rowBottomPx) continue;
                var frac = (offsetY - rowTopPx) / (rowBottomPx - rowTopPx);
                var range = yaxis.range;
                var value = range[1] - frac * (range[1] - range[0]);
                return {{value: value, label: axisInfo.label, decimals: axisInfo.decimals, thousands: axisInfo.thousands}};
            }}
            return null;
        }} catch (e) {{
            return null;
        }}
    }}

    function fmtRow(d) {{
        var color = d[4] >= d[1] ? '#c0392b' : '#27ae60';
        return '<b>' + d[0] + '</b>&nbsp;&nbsp;開<span style="color:' + color + '">' + d[1].toFixed(2)
            + '</span>&nbsp;高<span style="color:' + color + '">' + d[2].toFixed(2)
            + '</span>&nbsp;低<span style="color:' + color + '">' + d[3].toFixed(2)
            + '</span>&nbsp;收<span style="color:' + color + '">' + d[4].toFixed(2)
            + '</span>&nbsp;&nbsp;量 ' + d[5].toLocaleString();
    }}

    // MA5/10/20/120/240目前數值+方向(↑紅/↓綠/=灰，沿用本專案K棒漲紅跌綠的既有配色慣例，
    // 跟SAR用的綠/紅是另一套獨立慣例，只是剛好也用到綠色，語意不同)。d是ma_customdata_
    // json裡的一列，每個天期佔2個位置(數值, 方向)，資料不足時值是null，直接跳過不顯示。
    function fmtMa(d) {{
        var arrows = {{up: '<span style="color:#c0392b">↑</span>', down: '<span style="color:#27ae60">↓</span>', flat: '<span style="color:#888">=</span>'}};
        var parts = [];
        for (var i = 0; i < ma_row_labels.length; i++) {{
            var value = d[i * 2];
            var direction = d[i * 2 + 1];
            if (value === null) continue;
            // 標籤("MA5"等)用跟圖上那條均線一樣的顏色，取代被關掉的legend項目原本
            // 提供的顏色對照(見render_chart_html()裡showlegend=False那段的說明)。
            var label = '<span style="color:' + ma_row_colors[i] + '">' + ma_row_labels[i] + '</span>';
            parts.push(label + ' ' + value.toFixed(2) + (arrows[direction] || ''));
        }}
        return parts.join('&nbsp;&nbsp;');
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
    maBox.innerHTML = fmtMa(ma_customdata_json[ma_customdata_json.length - 1]);

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
        maBox.innerHTML = fmtMa(ma_customdata_json[idx]);
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
        maBox.innerHTML = fmtMa(ma_customdata_json[ma_customdata_json.length - 1]);
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

    // 原生mousemove(不是plotly_hover)：拿到游標在容器裡的連續像素高度，才能算出跟游標
    // 實際高度一致的數值(plotly_hover在hovermode="x"下只會回傳snap到最近資料點的值)。
    gd.addEventListener('mousemove', function(evt) {{
        var result = getAxisValueAtCursorY(evt.offsetY);
        if (result === null) {{
            axisValueBox.style.display = 'none';
            return;
        }}
        axisValueBox.style.display = 'block';
        axisValueBox.style.top = (evt.offsetY - 9) + 'px';
        var text = result.thousands ? Math.round(result.value).toLocaleString() : result.value.toFixed(result.decimals);
        axisValueBox.innerHTML = result.label + ' ' + text;
    }});
    gd.addEventListener('mouseleave', function(evt) {{
        axisValueBox.style.display = 'none';
    }});
    """

    return fig.to_html(include_plotlyjs=True, full_html=True, div_id=div_id, post_script=post_script)
