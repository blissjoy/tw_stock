"""公開資訊觀測站(MOPS)客戶端：目前只接了「公司增減資表」(IRB160報表)，供陳家豐
《看懂籌碼》P05-C2「減資股第一天效應」等規則之後使用(見ai/ebook-summary-chen/
_待確認總表.md的🔴資料來源缺口)。

2026-08-09查證：`ai/ebook-summary-chen/`筆記原本記錄「MOPS是JS動態渲染頁面，
WebFetch讀不到」，這個結論已經過時——網站在這之後改版成Angular SPA，背後是乾淨的
JSON API(`POST /mops/api/redirectToIRB`)，`fileNoOfIRB`參數對應各種報表代碼
(IRB160=公司增減資表，其餘IRB110/130/140/150/170/180/190/200等董監事股權/質押
異動報表推測也走同一個端點，這次只驗證了IRB160，其餘還沒實測)。

⚠️ **必須透過真正的瀏覽器頁面呼叫，不能用requests/urllib直接打API**：實測直接對
`/mops/api/redirectToIRB`發送HTTP請求會被WAF擋下(回傳「FOR SECURITY REASONS,
THIS PAGE CAN NOT BE ACCESSED」的自訂封鎖頁，不是正常的403)，必須先用Playwright
載入`https://mops.twse.com.tw/mops/web/index`首頁建立正常的cookie/referer，
再用該分頁自己的`fetch()`(不是外部request context)呼叫API才會成功——這是本模組
使用Playwright而不是requests的唯一原因，不是任意選擇。

`redirectToIRB`本身不直接回傳資料，回傳的是一個靜態報表網址(例如
`https://siis.twse.com.tw/publish/sii/115IRB160_01.HTM`)，那個網址才是真正的
報表內容，是純HTML(Big5編碼，不是UTF-8)，不需要瀏覽器也能抓(這裡仍然用Playwright
的request context抓，理由見`tpex_client.py`docstring記錄過的同類問題：
`www.tpex.org.tw`憑證缺Subject Key Identifier擴充欄位，較新版OpenSSL會丟
SSLCertVerificationError，`siis.twse.com.tw`未實測是否有同樣問題，用Playwright
自帶的TLS堆疊一次解決，不需要像tpex_client.py那樣另外寫CustomSSLAdapter)。

⚠️ **已知限制**：IRB160報表目前只看得到「公司代號＋名稱」，沒有增資/減資方向、
金額、恢復交易日期——`_待確認總表.md`原本期待的「減資股第一天效應」規則需要「恢復
交易日期」，這份報表本身沒有，之後要嘛比對`stock_prices`自己找出交易中斷後的
恢復交易日(用這裡拿到的公司清單當範圍縮小), 要嘛找MOPS其他報表補上方向/日期，
這次還沒有解決，只先把「哪些股票在哪個年月有增減資公告」這個中性事實抓回來存
(`mops_capital_changes`表)。

Playwright是這個模組唯一新增的重量級依賴，2026-08-09前只在測試/驗證時臨時使用，
這裡是第一次真正寫進production程式碼——`requirements.txt`已同步新增，且需要另外
執行一次`playwright install chromium`下載瀏覽器執行檔(不是pip install就會自動
裝好，這點眼線README需要交代)。

刻意做成手動執行的獨立腳本(`scripts/fetch_mops_capital_changes.py`)，不接進
`scripts/daily_pipeline.py`的自動排程——IRB160報表本來就是月頻率更新，不需要跟著
現有pipeline一天跑8次；且這是全新、還沒長期驗證過穩定性的爬蟲邏輯，先不要讓它
影響到目前運作穩定的核心每日排程，之後累積幾次手動執行都正常再考慮併入自動化。
"""

from __future__ import annotations

from bs4 import BeautifulSoup

MOPS_INDEX_URL = "https://mops.twse.com.tw/mops/web/index"
CAPITAL_CHANGE_FILE_NO = "IRB160"


def parse_capital_change_html(html: str) -> list[dict]:
    """解析IRB160報表的HTML內容(已經decode成文字，呼叫端負責處理Big5編碼)，回傳
    [{"stock_id", "name"}, ...]。報表本身是舊式HTML(表格列缺少完整的開合標籤)，
    用BeautifulSoup的寬鬆解析處理，不能用嚴格XML parser。表頭列(公司代號/名稱兩個
    中文欄位標題)靠「代號欄不是以數字開頭」過濾掉，不是靠列數固定跳過第一列(避免
    報表格式微調時跳錯列)。查無資料或格式不符預期時回傳空list，不拋例外。
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", attrs={"border": True})
    if table is None:
        return []
    results: list[dict] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        stock_id = tds[0].get_text(strip=True)
        name = tds[1].get_text(strip=True)
        if not stock_id or not stock_id[0].isdigit():
            continue
        results.append({"stock_id": stock_id, "name": name})
    return results


def fetch_capital_change_companies(year: str, month: str, market: str = "sii") -> list[dict]:
    """整合fetch+parse，跑一次Playwright流程拿到某年月/市場別的增減資公司清單。

    year/month：民國年/月字串(例如"115"/"07")，對應MOPS查詢表單的「資料年度」／
    「月份」欄位。market：MOPS的marketKind參數，"sii"=上市，"otc"=上櫃。

    查無資料(該年月剛好沒有任何公司辦理增減資，或該年月尚未由MOPS公告)回傳空list，
    不拋例外——呼叫端(scripts/fetch_mops_capital_changes.py)自行決定要不要提示
    使用者「這個月剛好沒有資料」還是「抓取失敗」。

    ⚠️ 需要`playwright install chromium`先裝好瀏覽器執行檔，只`pip install
    playwright`不會自動下載，見本模組docstring。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(MOPS_INDEX_URL, timeout=30000)
            page.wait_for_timeout(1500)  # 等首頁的JS完成初始化，太快呼叫API有時會失敗

            redirect_result = page.evaluate(
                """
                async ({year, month, market, fileNoOfIRB}) => {
                    const resp = await fetch('/mops/api/redirectToIRB', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({year, marketKind: market, month, fileNoOfIRB})
                    });
                    return await resp.json();
                }
                """,
                {"year": year, "month": month, "market": market, "fileNoOfIRB": CAPITAL_CHANGE_FILE_NO},
            )
            if redirect_result.get("code") != 200 or not redirect_result.get("result"):
                return []
            report_url = redirect_result["result"]["url"]

            request_context = p.request.new_context()
            try:
                resp = request_context.get(report_url)
                if resp.status != 200:
                    return []
                html = resp.body().decode("big5", errors="replace")
            finally:
                request_context.dispose()
        finally:
            browser.close()

    return parse_capital_change_html(html)
