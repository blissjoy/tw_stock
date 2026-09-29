"""大產業別分類：把`stocks.industry`欄位裡50餘種TWSE/TPEx細項產業別，手工歸納成
幾個大分類(電子類/傳產/製造類/生技醫療類/金融類/服務消費類/基金憑證類)，供「選股」
分頁的產業別篩選改成樹狀勾選——使用者反映細項產業別太多(50幾種)，一個一個勾很麻煩，
想要「勾電子類就自動勾好底下所有電子相關細項」。

⚠️ 這份分類是手工歸納，不是任何官方(TWSE/TPEx/FinMind)資料來源——FinMind的
`industry_category`欄位(見src/data/finmind_client.py的fetch_stock_info())本來就
只有細項分類，沒有更上層的大分類欄位可以直接用，這裡的分組純粹是依照台股市場慣例
(電子/傳產/金融/生技等常見大分類)人工歸類，之後DB裡如果出現新的細項產業別名稱，
需要手動補進這份清單，不會自動涵蓋。

TWSE/TPEx對同一種產業別常有些微不同的命名(例如上市用「其他電子業」、上櫃用「其他
電子類」)，這裡把兩種寫法都各自列出來，不強行合併成同一個字串(避免跟`stocks.industry`
欄位裡實際儲存的原始字串對不起來，篩選時比對失敗)。
"""

from __future__ import annotations

INDUSTRY_GROUPS: dict[str, list[str]] = {
    "電子類": [
        "半導體業", "電腦及週邊設備業", "光電業", "通信網路業", "電子零組件業",
        "電子通路業", "資訊服務業", "數位雲端", "數位雲端類", "其他電子業",
        "其他電子類", "電子工業",
    ],
    "傳產/製造類": [
        "水泥工業", "食品工業", "塑膠工業", "紡織纖維", "電機機械", "電器電纜",
        "化學工業", "玻璃陶瓷", "造紙工業", "鋼鐵工業", "橡膠工業", "汽車工業",
        "建材營造", "農業科技", "農業科技業", "油電燃氣業",
    ],
    "生技醫療類": ["生技醫療業", "化學生技醫療"],
    "金融類": ["金融保險", "金融業"],
    "服務/消費類": [
        "貿易百貨", "觀光事業", "觀光餐旅", "運動休閒", "運動休閒類",
        "居家生活", "居家生活類", "文化創意業", "航運業", "綠能環保", "綠能環保類",
    ],
    "基金/憑證/其他": [
        "ETF", "上櫃ETF", "上櫃指數股票型基金(ETF)", "存託憑證",
        "指數投資證券(ETN)", "創新板股票", "所有證券", "其他",
    ],
}


def group_industries(all_industries: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """把`chart_data.list_industries()`回傳的實際細項清單，依INDUSTRY_GROUPS分組。

    回傳(分組後的{大分類: [細項...]}, 沒被任何分類收錄的細項清單)——後者理論上應該
    是空list(INDUSTRY_GROUPS已經涵蓋目前DB裡看得到的所有細項)，除非DB出現新的細項
    產業別名稱、這份分類清單還沒更新，這種情況下呼叫端可以把「未分類」的細項照樣
    平鋪顯示，不會讓使用者漏掉篩選這些股票的機會，也不會直接crash。
    """
    known = set(all_industries)
    grouped: dict[str, list[str]] = {}
    covered: set[str] = set()
    for group_name, members in INDUSTRY_GROUPS.items():
        matched = [m for m in members if m in known]
        if matched:
            grouped[group_name] = matched
            covered.update(matched)
    ungrouped = [i for i in all_industries if i not in covered]
    return grouped, ungrouped
