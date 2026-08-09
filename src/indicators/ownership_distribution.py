"""集保股權分散表判讀規則（籌碼流向＋身分分類＋股權集中度）：陳家豐版20/800/1000張
三級距分類，跟股價方向對照的籌碼流向判讀。

來源：陳家豐《看懂籌碼 股市賺大錢》第2篇第2章「籌碼流向與集保股權動態」
（筆記見`ai/ebook-summary-chen/P02-C2-籌碼流向與集保股權動態.md`，規則檔見
`ai/chen-rules/籌碼面/集保股權分散判讀規則.md`，R-CHIP-07）。

資料源跟`src/indicators/huang_chip_signals.py`共用同一張`holder_shares_distribution`
表（FinMind `TaiwanStockHoldingSharesPer`，底層是TDCC集保戶股權分散表），但**刻意獨立
成另一個模組、不重用該模組的函式**——`huang_chip_signals.py`是「一字不差」複刻使用者
朋友提供的外部JS程式碼，門檻/二分法都要跟原始來源保持一致，不能為了共用而調整；這裡是
陳家豐書中明確給出的20/800/1000張三級距分類，門檻設計不同（黃豐凱版本只分大戶/散戶
二類，這裡分四類），獨立維護才不會互相牽動彼此的既有驗證結果。

⚠️ `holder_shares_distribution`目前只對「觀察清單」裡的股票有資料（見`src/data/
holder_shares_sync.py`），不是全市場——查詢清單外的股票會查無資料，這是既有的資料
範圍限制，不是這個模組的bug。
"""

from __future__ import annotations

# 5個「散戶」級距(<=20張，即<=20,000股)：對應書中「持有≤20張=一般散戶」。
RETAIL_LEVELS = frozenset({
    "1-999", "1,000-5,000", "5,001-10,000", "10,001-15,000", "15,001-20,000",
})
# 8個「中實戶或法人」級距(20~800張)：對應書中「持有20~800張=中實戶或法人」。
MID_LEVELS = frozenset({
    "20,001-30,000", "30,001-40,000", "40,001-50,000", "50,001-100,000",
    "100,001-200,000", "200,001-400,000", "400,001-600,000", "600,001-800,000",
})
# 書中明確承認的800~1,000張模糊地帶，不歸類為散戶/中實戶/大股東任何一類。
AMBIGUOUS_LEVEL = "800,001-1,000,000"
# >1,000張=大股東或主力，同時也是「股權集中度」計算的分子。
WHALE_LEVEL = "more than 1,000,001"
# FinMind回傳資料裡的彙總/調整列(非持股級距本身)，計算佔比時必須排除，
# 否則所有級距percent加總會超過100%。
NON_LEVEL_ROWS = frozenset({"total", "差異數調整（說明4）"})


def classify_holder_identity(level: str) -> str:
    """把FinMind的持股級距字串換算成書中20/800/1000張的身分分類。"""
    if level in RETAIL_LEVELS:
        return "散戶"
    if level in MID_LEVELS:
        return "中實戶或法人"
    if level == AMBIGUOUS_LEVEL:
        return "模糊地帶"
    if level == WHALE_LEVEL:
        return "大股東或主力"
    return "不明"


def ownership_concentration(rows_for_date: list[dict]) -> float | None:
    """股權集中度：持股1,000張以上集保戶佔總體集保庫存數比重(%)。查無該級距回傳None。"""
    for row in rows_for_date:
        if row["holding_shares_level"] == WHALE_LEVEL:
            return row["percent"]
    return None


def retail_percent(rows_for_date: list[dict]) -> float:
    """5個散戶級距(<=20張)的percent加總，四捨五入到小數點後2位。"""
    total = sum(row["percent"] for row in rows_for_date if row["holding_shares_level"] in RETAIL_LEVELS)
    return round(total, 2)


def chip_flow_direction(rows_by_date: dict[str, list[dict]]) -> dict | None:
    """比較資料裡最新的2個日期(集保結算所實際公告日，不是「今天」跟「昨天」，這份資料
    週更新，中間平常日不會有新的一筆)，判讀籌碼流向方向。

    rows_by_date：{date_str: [{"holding_shares_level", "percent"}, ...]}。
    資料不足2個日期、或最新/次新任一日期缺少大戶級距資料，回傳None。

    回傳{"latest_date", "prev_date", "whale_pct", "whale_diff", "retail_pct",
    "retail_diff", "direction"}——direction依書中規則：大戶增+散戶減=續漲方向；
    大戶減+散戶增=續跌方向；其餘情況(含同向增減、其中一方持平)為「無明確方向」。
    """
    dates = sorted(rows_by_date.keys(), reverse=True)
    if len(dates) < 2:
        return None
    latest_date, prev_date = dates[0], dates[1]

    whale_now = ownership_concentration(rows_by_date[latest_date])
    whale_prev = ownership_concentration(rows_by_date[prev_date])
    if whale_now is None or whale_prev is None:
        return None

    retail_now = retail_percent(rows_by_date[latest_date])
    retail_prev = retail_percent(rows_by_date[prev_date])

    whale_diff = round(whale_now - whale_prev, 2)
    retail_diff = round(retail_now - retail_prev, 2)

    if whale_diff > 0 and retail_diff < 0:
        direction = "籌碼從散戶流向大股東，股價多半續漲"
    elif whale_diff < 0 and retail_diff > 0:
        direction = "籌碼從大股東流向散戶，股價多半續跌"
    else:
        direction = "無明確方向"

    return {
        "latest_date": latest_date,
        "prev_date": prev_date,
        "whale_pct": whale_now,
        "whale_diff": whale_diff,
        "retail_pct": retail_now,
        "retail_diff": retail_diff,
        "direction": direction,
    }
