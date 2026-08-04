"""觀察清單匯出Google Sheet的組裝層：把每個觀察清單群組的資料(含黃豐凱籌碼分析法
D~R欄位，見src/indicators/huang_chip_signals.py)組成`src/data/google_sheets_client.py`
的`write_formatted_table()`需要的values/顏色/合併儲存格清單，一個觀察清單群組對應
一個Google Sheet分頁(worksheet tab，用群組名稱當分頁名)。

跟`desktop/main_window.py`觀察清單表格是同一份資料來源(`portfolio_data.load_watchlist()`
+`huang_chip_data.load_huang_chip_row()`)、同一套欄位順序/顏色邏輯——使用者在桌面版
畫面看到的跟Google Sheet匯出的內容應該一致，只是這裡輸出成Sheets API要的格式，不是
Qt Widget。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.data import google_sheets_client, portfolio_storage
from src.data.config import get_google_sheet_id
from src.indicators.huang_chip_signals import COLOR_BUY, COLOR_SELL
from src.presentation import chart_data, huang_chip_data, portfolio_data

_BASE_HEADERS = [
    "股票代號", "名稱", "現價", "漲跌幅(%)", "參考成本價", "參考股數", "市值",
    "帳面損益", "報酬率(%)", "SAR狀態", "SAR距離%",
]
_CHIP_HEADERS = [
    "投信", "外資", "大戶週變化", "散戶週變化", "均線狀態", "週K型態",
    "40日外資", "40日投信", "20日外資", "20日投信", "10日外資", "10日投信", "5日外資", "5日投信",
]
_TOTAL_COLUMNS = len(_BASE_HEADERS) + len(_CHIP_HEADERS)

# 黃豐凱籌碼分析法欄位的分類群組(比照觀察清單畫面的雙列表頭跟原始參考截圖的分類方式)：
# (顯示文字, 底色, 起始欄, 結束欄)，欄位索引以_BASE_HEADERS(11欄)之後接續的_CHIP_HEADERS
# 為準，0-indexed。
_GROUP_LABEL_SPANS = [
    ("法人近期籌碼", "#FCEBEB", 11, 12),
    ("大戶/散戶持股變化(週)", "#EEEDFE", 13, 14),
    ("技術型態", "#E1F5EE", 15, 16),
    ("法人買賣超（張數）", "#FAEEDA", 17, 24),
]

_FLOW_KEYS = [
    "foreign_40d", "invest_40d", "foreign_20d", "invest_20d",
    "foreign_10d", "invest_10d", "foreign_5d", "invest_5d",
]


def _base_row_values(row: pd.Series) -> list[str]:
    return [
        row["stock_id"],
        row["name"] if pd.notna(row["name"]) else "-",
        f"{row['close']:.2f}" if pd.notna(row["close"]) else "-",
        f"{row['pct_change']:+.2f}" if pd.notna(row["pct_change"]) else "-",
        f"{row['cost_price']:.2f}" if pd.notna(row["cost_price"]) else "-",
        f"{int(row['shares']):,}" if pd.notna(row["shares"]) else "-",
        f"{row['market_value']:,.0f}" if pd.notna(row["market_value"]) else "-",
        f"{row['profit']:+,.0f}" if pd.notna(row["profit"]) else "-",
        f"{row['return_pct']:+.2f}" if pd.notna(row["return_pct"]) else "-",
        row["sar_status"] if pd.notna(row["sar_status"]) else "-",
        f"{row['sar_distance_pct']:+.2f}" if pd.notna(row["sar_distance_pct"]) else "-",
    ]


def _chip_row_values_and_text_colors(chip_row: dict, row_idx: int) -> tuple[list[str], dict[tuple[int, int], str]]:
    """回傳這一列的D~R欄位文字，以及{(row, col): 文字顏色}——顏色只標記真的有意義的
    儲存格(例如查無資料顯示"-"的儲存格不上色)，對應原始參考來源/桌面版UI都是用文字
    顏色(不是儲存格底色)標示紅漲綠跌。col從_BASE_HEADERS.length()開始(0-indexed)。
    """
    base_col = len(_BASE_HEADERS)
    values: list[str] = []
    text_colors: dict[tuple[int, int], str] = {}

    def add(text: str, color: str | None = None) -> None:
        col = base_col + len(values)
        values.append(text)
        if color:
            text_colors[(row_idx, col)] = color

    invest_streak = chip_row["invest_streak"]
    add(invest_streak["text"] or "-", invest_streak["color"] if invest_streak["text"] else None)
    foreign_streak = chip_row["foreign_streak"]
    add(foreign_streak["text"] or "-", foreign_streak["color"] if foreign_streak["text"] else None)

    holder = chip_row["holder_change"]
    add(holder["whale"]["text"] if holder else "-", holder["whale"]["color"] if holder else None)
    add(holder["retail"]["text"] if holder else "-", holder["retail"]["color"] if holder else None)

    ma = chip_row["ma_price_position"]
    add("\n".join(line["text"] for line in ma["lines"]) if ma else "-")

    weekly = chip_row["weekly_volume_pattern"]
    add(f"{weekly['pattern']}\n（{weekly['reference_week_start']}）" if weekly else "-")

    flow = chip_row["flow"]
    for key in _FLOW_KEYS:
        if flow is None:
            add("-")
        else:
            value = flow[key]
            add(f"{value:,}", COLOR_BUY if value >= 0 else COLOR_SELL)

    return values, text_colors


def build_group_table(main_conn, portfolio_conn, group_id: int) -> dict:
    """組出單一觀察清單群組要匯出的完整表格資料(values/背景色/文字色/粗體/合併範圍)。"""
    df = portfolio_data.load_watchlist(main_conn, portfolio_conn, group_id)

    header_row0 = [""] * _TOTAL_COLUMNS
    header_row1 = _BASE_HEADERS + _CHIP_HEADERS

    background_colors: dict[tuple[int, int], str] = {}
    bold_cells: set[tuple[int, int]] = set()
    merges: list[tuple[int, int, int, int]] = []
    for label, color, start_col, end_col in _GROUP_LABEL_SPANS:
        header_row0[start_col] = label
        background_colors[(0, start_col)] = color
        bold_cells.add((0, start_col))
        if end_col > start_col:
            merges.append((0, start_col, 0, end_col))
    for col in range(_TOTAL_COLUMNS):
        bold_cells.add((1, col))

    # A1(header_row0第一欄)固定放這次匯出的時間，跟_GROUP_LABEL_SPANS(從第11欄
    # 開始)不會衝突——2026-08-04新增，使用者才知道Google Sheet上的資料是什麼時候
    # 匯出的，不用回頭對照桌面版的「資料更新至」才能判斷是否過期。
    header_row0[0] = f"更新時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    values: list[list[str]] = [header_row0, header_row1]
    text_colors: dict[tuple[int, int], str] = {}

    name_col = _BASE_HEADERS.index("名稱")
    for i, row in enumerate(df.reset_index(drop=True).to_dict("records")):
        row_series = pd.Series(row)
        row_idx = i + 2  # 前2列是表頭
        chip_row = huang_chip_data.load_huang_chip_row(main_conn, row["stock_id"])
        chip_values, chip_text_colors = _chip_row_values_and_text_colors(chip_row, row_idx)
        values.append(_base_row_values(row_series) + chip_values)
        text_colors.update(chip_text_colors)
        # 名稱欄依市場別上色(上市藍/上櫃黑/興櫃灰)，跟desktop/main_window.py的
        # 觀察清單表格共用src.presentation.portfolio_data.listing_type_color()。
        text_colors[(row_idx, name_col)] = portfolio_data.listing_type_color(row_series.get("listing_type"))

    return {
        "values": values,
        "background_colors": background_colors,
        "text_colors": text_colors,
        "bold_cells": bold_cells,
        "merges": merges,
    }


def export_watchlist_group(
    main_conn, portfolio_conn, group_id: int, group_name: str, spreadsheet_id: str, interactive: bool = True,
) -> None:
    """匯出單一觀察清單群組到指定試算表的一個分頁(分頁名=群組名稱)。

    interactive：見src/data/google_sheets_client.py的get_authorized_client()說明，
    daily_pipeline.py自動排程呼叫時要傳False，避免背景執行卡在等待瀏覽器互動。
    """
    table = build_group_table(main_conn, portfolio_conn, group_id)
    google_sheets_client.write_formatted_table(
        spreadsheet_id=spreadsheet_id,
        worksheet_title=group_name,
        values=table["values"],
        background_colors=table["background_colors"],
        text_colors=table["text_colors"],
        bold_cells=table["bold_cells"],
        merges=table["merges"],
        interactive=interactive,
    )


def export_all_watchlist_groups(
    main_conn, portfolio_conn, spreadsheet_id: str | None = None, interactive: bool = True,
) -> int:
    """匯出全部觀察清單群組，每個群組各自一個分頁。回傳成功匯出的群組數。"""
    resolved_spreadsheet_id = spreadsheet_id or get_google_sheet_id()
    groups = portfolio_storage.list_watchlist_groups(portfolio_conn)
    for group in groups:
        export_watchlist_group(
            main_conn, portfolio_conn, group["id"], group["group_name"], resolved_spreadsheet_id,
            interactive=interactive,
        )
    return len(groups)


# 選股清單(候選清單)匯出——2026-08-04新增，使用者要求「更新觀察清單時，選股清單
# 也一起更新上去，分成不同的sheet」。跟觀察清單分頁是同一個Google試算表(spreadsheet)
# 底下另一個分頁(worksheet)，用固定名稱"選股清單"，不是每個觀察清單群組各自一頁。
CANDIDATE_LIST_WORKSHEET_TITLE = "選股清單"

_CANDIDATE_HEADERS = [
    "股票代號", "名稱", "產業別", "訊號", "收盤價", "進場價", "停損價",
    "漲跌幅(%)", "成交量(張)", "SAR值", "SAR狀態", "SAR距離%",
]


def _candidate_row_values(row: pd.Series) -> list[str]:
    volume = row["volume"]
    return [
        row["stock_id"],
        row["name"] if pd.notna(row["name"]) else "-",
        row["industry"] if pd.notna(row["industry"]) else "",
        row["signal_name"] if pd.notna(row["signal_name"]) else "-",
        f"{row['close']:.2f}" if pd.notna(row["close"]) else "-",
        f"{row['entry_price']:.2f}" if pd.notna(row["entry_price"]) else "-",
        f"{row['stop_loss']:.2f}" if pd.notna(row["stop_loss"]) else "-",
        f"{row['pct_change']:+.2f}" if pd.notna(row["pct_change"]) else "-",
        f"{int(volume) // 1000:,}" if pd.notna(volume) else "-",
        f"{row['sar_value']:.2f}" if pd.notna(row["sar_value"]) else "-",
        row["sar_status"] if pd.notna(row["sar_status"]) else "-",
        f"{row['sar_distance_pct']:+.2f}" if pd.notna(row["sar_distance_pct"]) else "-",
    ]


def build_candidate_list_table(main_conn, target_date: str | None = None) -> dict:
    """組出選股清單(候選清單)要匯出的表格資料。跟`scripts/daily_pipeline.py`的
    LINE/Email通知內容套用同一套「UI候選清單預設檢視」篩選條件
    (`chart_data.CANDIDATE_FILTER_DEFAULTS`/`CANDIDATE_SAR_FLIP_*_DEFAULT`/
    `CANDIDATE_ZHU_RULE_ONLY_DEFAULT`)，不是`run_screen_and_store()`原始未篩選的
    `daily_candidates`——確保Google Sheet上看到的跟桌面版「選股」分頁預設畫面、
    LINE/Email通知三處是同一個集合，不會互相對不上。
    """
    universe_df, latest_date, _ = chart_data.load_stock_universe_for_date(main_conn, target_date=target_date)
    if latest_date is not None:
        active_filter_labels = [
            label for label, default in chart_data.CANDIDATE_FILTER_DEFAULTS.items() if default
        ]
        universe_df = chart_data.apply_candidate_filters(
            main_conn, universe_df, active_filter_labels,
            sar_flip_option=(
                chart_data.CANDIDATE_SAR_FLIP_OPTION_DEFAULT
                if chart_data.CANDIDATE_SAR_FLIP_ENABLED_DEFAULT else None
            ),
            zhu_rule_only=chart_data.CANDIDATE_ZHU_RULE_ONLY_DEFAULT,
            as_of_date=latest_date,
        )

    header_row0 = [""] * len(_CANDIDATE_HEADERS)
    header_row0[0] = f"更新時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    if latest_date is not None:
        header_row0[1] = f"候選清單日期：{latest_date}"

    values: list[list[str]] = [header_row0, _CANDIDATE_HEADERS]
    text_colors: dict[tuple[int, int], str] = {}
    bold_cells: set[tuple[int, int]] = {(1, col) for col in range(len(_CANDIDATE_HEADERS))}
    name_col = _CANDIDATE_HEADERS.index("名稱")

    for i, row in enumerate(universe_df.reset_index(drop=True).to_dict("records")):
        row_series = pd.Series(row)
        row_idx = i + 2  # 前2列是表頭
        values.append(_candidate_row_values(row_series))
        # 名稱欄依市場別上色，跟觀察清單匯出/桌面版共用同一份對照表。
        text_colors[(row_idx, name_col)] = portfolio_data.listing_type_color(row_series.get("listing_type"))

    return {
        "values": values,
        "background_colors": {},
        "text_colors": text_colors,
        "bold_cells": bold_cells,
        "merges": [],
    }


def export_candidate_list(
    main_conn, spreadsheet_id: str | None = None, interactive: bool = True, target_date: str | None = None,
) -> bool:
    """匯出選股清單(候選清單)到固定名稱的分頁(`CANDIDATE_LIST_WORKSHEET_TITLE`)。
    回傳True代表有實際匯出資料；latest_date為None(DB裡完全沒有任何候選清單紀錄)
    時仍會寫入只有表頭的空表格，回傳False。
    """
    resolved_spreadsheet_id = spreadsheet_id or get_google_sheet_id()
    table = build_candidate_list_table(main_conn, target_date=target_date)
    google_sheets_client.write_formatted_table(
        spreadsheet_id=resolved_spreadsheet_id,
        worksheet_title=CANDIDATE_LIST_WORKSHEET_TITLE,
        values=table["values"],
        background_colors=table["background_colors"],
        text_colors=table["text_colors"],
        bold_cells=table["bold_cells"],
        merges=table["merges"],
        interactive=interactive,
    )
    return len(table["values"]) > 2
