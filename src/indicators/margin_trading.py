"""融資維持率（Layer 0）：逐日估算個股融資維持率，判斷斷頭風險與超跌反彈時機。

來源：陳家豐《看懂籌碼 股市賺大錢》第2篇第4章「融資維持率揭秘 勿買斷頭股」
（筆記見`ai/ebook-summary-chen/P02-C4-融資維持率揭秘勿買斷頭股.md`）。

書中定義：融資維持率 = 股票市值 ÷ 融資金額（借款金額）。借款金額是「原始融資買進當天」
就固定下來的金額（買進價格 × 融資成數），之後不會隨股價變動而改變——這是本模組計算邏輯
的核心：融資維持率不是每天用「今天股價」除以「今天股價的一部分」這種同義反覆，而是要
追蹤「目前尚未平倉的融資部位，加權平均是在什麼價位買進的」。

但TWSE公開的`margin_trading`表只有「每日融資買進/賣出/現金償還/餘額」的加總張數，沒有
個別融資戶的原始買進價位，因此這裡採用加權平均成本的近似算法：
- 每日融資餘額增加(融資買進)時，用「當天收盤價」當作新增部位的成本（無法取得逐筆委託
  實際成交價，收盤價是最貼近書中範例邏輯的代理值），跟舊部位的加權平均成本合併。
- 融資餘額減少(融資賣出/現金償還)時，加權平均成本不變（賣掉的部位退出，剩下部位的
  平均成本不會因為賣出而改變，這是加權平均法的標準性質）。
- 融資成數採用可調參數`margin_pct`，書中範例用6成（即`margin_pct=0.6`），但書中自己
  也提醒「不同個股實際融資成數不同，不能一律套用100÷60=166%這組數字」（見筆記待補充
  第2點），呼叫端如果之後拿到個股實際融資成數規定，應該覆寫這個預設值。
- 加權平均成本的計算是從資料庫現有歷史的第一天開始累加，不是從這檔股票「有史以來第一次
  有人融資買進」開始——如果資料庫歷史起點當天已經有融資餘額，這裡會把當天的餘額全部視為
  用當天收盤價買進，這會讓計算出來的維持率在資料早期偏保守（可能高估或低估早期的維持率），
  但隨著時間累積，權重會被之後的新增/剔除部位稀釋，误差會逐漸收斂，不是永久性的偏誤。
"""

from __future__ import annotations

import pandas as pd

MARGIN_WARNING_RATIO = 1.35  # 「爹不疼、娘不愛」警戒區，書中：100元跌到81元(跌約2成)
MARGIN_LIQUIDATION_RATIO = 1.20  # 斷頭線，書中：100元跌到72元(跌約3成)
DEFAULT_MARGIN_PCT = 0.6  # 書中範例假設的6成融資，書中自承並非所有個股都適用
OVERSOLD_MIN_CONSECUTIVE_DAYS = 3  # 「連續幾個交易日都在120%以下」書中未給精確天數，
# 工程估計值，取跟本專案其他「連續N日」規則(如R-TREND多日確認)相近的下限


def compute_margin_maintenance_ratio(
    close: pd.Series,
    margin_buy: pd.Series,
    margin_sell: pd.Series,
    margin_cash_repayment: pd.Series,
    margin_today_balance: pd.Series,
    margin_pct: float = DEFAULT_MARGIN_PCT,
) -> pd.Series:
    """逐日估算融資維持率。五個輸入Series須為同一檔股票、依date遞增排序、index對齊。

    `margin_today_balance`是TWSE官方公布的每日融資餘額(股數)，直接採信作為真實餘額
    (不用buy/sell/repayment自己累加，避免資料缺漏日造成餘額累積誤差)；buy/sell/
    repayment只用來判斷「今天餘額的變動方向」，決定加權平均成本要不要更新。
    """
    n = len(close)
    close_arr = close.to_numpy()
    buy_arr = margin_buy.fillna(0).to_numpy()
    balance_arr = margin_today_balance.fillna(0).to_numpy()

    avg_cost = 0.0
    prev_balance = 0.0
    ratio_arr: list[float | None] = [None] * n

    for i in range(n):
        today_balance = balance_arr[i]
        today_buy = buy_arr[i]

        if today_balance <= 0:
            avg_cost = 0.0
        elif today_buy > 0 and today_balance > prev_balance:
            # 新增部位以當天收盤價計入，跟舊部位做加權平均
            new_qty = today_balance - prev_balance
            old_qty = prev_balance
            if old_qty > 0 and avg_cost > 0:
                avg_cost = (avg_cost * old_qty + close_arr[i] * new_qty) / today_balance
            else:
                avg_cost = close_arr[i]
        # 餘額減少(賣出/現金償還)或持平：avg_cost不變

        if avg_cost > 0:
            loan_per_share = avg_cost * margin_pct
            ratio_arr[i] = float(close_arr[i] / loan_per_share) if loan_per_share > 0 else None

        prev_balance = today_balance

    return pd.Series(ratio_arr, index=close.index, dtype="float64")


def classify_margin_maintenance_state(ratio: float | None) -> str:
    """依融資維持率數值分類目前狀態，門檻見書中P02-C4：166%(初始)/135%(警戒)/120%(斷頭)。"""
    if ratio is None or pd.isna(ratio):
        return "無融資部位"
    if ratio < MARGIN_LIQUIDATION_RATIO:
        return "已跌破斷頭線"
    if ratio < MARGIN_WARNING_RATIO:
        return "警戒區(爹不疼娘不愛)"
    return "正常"


def margin_oversold_rebound_signal(
    ratio: pd.Series, min_consecutive_days: int = OVERSOLD_MIN_CONSECUTIVE_DAYS
) -> pd.Series:
    """逐日判斷是否符合「融資維持率連續N個交易日都在120%以下」的超跌反彈訊號(書中P02-C4)。

    今天是否觸發：從今天往回數，最近min_consecutive_days天(含今天)的維持率是否都
    非空值且都低於MARGIN_LIQUIDATION_RATIO。
    """
    below = ((ratio < MARGIN_LIQUIDATION_RATIO) & ratio.notna()).astype(int)
    rolling_min = below.rolling(window=min_consecutive_days, min_periods=min_consecutive_days).min()
    return (rolling_min == 1).fillna(False)
