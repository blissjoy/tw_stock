from src.data import mops_client

# 截取自2026-08-09實測抓到的真實IRB160報表(115年01月，上市)，只留前3列＋表頭列，
# 驗證parse_capital_change_html()能正確跳過表頭、抽出公司代號/名稱。原始報表是舊式
# HTML(缺少完整的<TR>開合標籤)，這裡刻意保留這個「缺標籤」的樣子，不要在測試樣本裡
# 幫忙補好，才能真正驗證parse函式對真實網站格式的容錯能力。
_SAMPLE_HTML = """
<html>
<head>
<meta http-equiv='Content-Type' content='text/html; charset=x-x-big5'></head>
<body background='back.gif'>
<pre>
<H3>[IRB160]       公 司 增 減 資 表                   日 期 :115/02/23
                  ( 不 含 金 控 子 公 司 )
                      資 料 年 月 : 11412 - 11501</pre>
<TABLE BORDER align=center>
<TR>
<TD NOWRAP>公  司  代  號</TD>
<TD NOWRAP>名                          稱 </TD>
<TR><TD>1101</TD>
<TD>臺灣水泥股份有限公司          </TD>
</TR>
<TR><TD>1316</TD>
<TD>上曜建設開發股份有限公司      </TD>
</TR>
<TR><TD>1342</TD>
<TD>八貫企業股份有限公司          </TD>
</TR>
</TABLE>
</body>
</html>
"""


def test_parse_capital_change_html_extracts_companies_and_skips_header_row():
    result = mops_client.parse_capital_change_html(_SAMPLE_HTML)

    assert result == [
        {"stock_id": "1101", "name": "臺灣水泥股份有限公司"},
        {"stock_id": "1316", "name": "上曜建設開發股份有限公司"},
        {"stock_id": "1342", "name": "八貫企業股份有限公司"},
    ]


def test_parse_capital_change_html_returns_empty_list_when_no_table():
    assert mops_client.parse_capital_change_html("<html><body>查無資料</body></html>") == []


def test_parse_capital_change_html_returns_empty_list_for_empty_string():
    assert mops_client.parse_capital_change_html("") == []
