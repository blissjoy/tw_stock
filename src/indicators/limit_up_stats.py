"""短線券商鎖漲停後隔日走勢統計：陳家豐書中給出的回測式統計基準(R-CHIP-15)，含漲停
偵測與隔日OHLC相對報酬率計算，供`scripts/validate_limit_up_next_day_stats.py`用近期
真實資料重新驗證書中2014年前的數字是否還成立。

來源：陳家豐《看懂籌碼 股市賺大錢》第6篇第1章「要命短線客 人氣股由多翻空」
(筆記見`ai/ebook-summary-chen/P06-C1-要命短線客人氣股由多翻空.md`，規則檔見
`ai/chen-rules/籌碼面/鎖漲停後隔日走勢統計.md`，R-CHIP-15)。書中樣本(2,000多筆短線
券商進出紀錄，2014年前)：隔天開高機率75.7%、平均開高+1.91%、開盤後再上攻至最高
+2.04%(累計約+3.95%相對前收)、盤中最低-0.78%、日內振幅4.73%、收盤僅+0.7%、隔日
收黑K或留上影線機率80%。書中自己提醒證所稅後市場結構已改變，這組數字可能已過時。

⚠️「漲停」判定用漲跌幅>=LIMIT_UP_THRESHOLD(9.5%)近似——台股實際漲停是10%，但受
股價跳動單位(tick size)影響，實際收盤漲幅常落在9.7%~10.0%之間，用9.5%留一點緩衝，
避免嚴格用10%漏掉因跳動單位而略低於10%的真實鎖漲停案例，這是工程近似，不是官方
公式的精確重現。

⚠️書中「隔日收黑或留上影線」是一個複合質性描述，這裡用「收盤價<開盤價」(隔日K棒
本身是黑K)當量化代理指標，跟「留上影線」(高點明顯高於開盤/收盤但收盤未破前高)不
完全相同，這是工程簡化，算出的比例可能無法跟書中80%直接一對一比較，只能當方向性
參考。
"""

from __future__ import annotations

LIMIT_UP_THRESHOLD = 0.095  # 近似漲停判定門檻，見模組docstring


def is_limit_up(prev_close: float | None, close: float | None, threshold: float = LIMIT_UP_THRESHOLD) -> bool:
    """判斷close相對prev_close是否達到(近似)漲停幅度。"""
    if prev_close is None or prev_close <= 0 or close is None:
        return False
    return (close - prev_close) / prev_close >= threshold


def next_day_event_stats(open_: float, high: float, low: float, close: float, reference_close: float) -> dict:
    """單一事件(某次鎖漲停的隔一個交易日)相對reference_close(鎖漲停當天收盤價)的OHLC
    百分比變化，欄位對應書中benchmark的4個數字(open_pct/high_pct/low_pct/close_pct)，
    另外附上is_red(收盤是否低於開盤，收黑K的代理指標，見模組docstring)。
    """
    return {
        "open_pct": (open_ - reference_close) / reference_close,
        "high_pct": (high - reference_close) / reference_close,
        "low_pct": (low - reference_close) / reference_close,
        "close_pct": (close - reference_close) / reference_close,
        "is_red": close < open_,
    }


def summarize_events(events: list[dict]) -> dict:
    """彙整多筆next_day_event_stats()結果，回傳跟書中benchmark同格式的統計摘要。
    events為空list時回傳n=0、其餘欄位皆為None(樣本不足，不是「沒有訊號」)。
    """
    n = len(events)
    if n == 0:
        return {
            "n": 0, "open_higher_rate": None, "avg_open_pct": None, "avg_high_pct": None,
            "avg_low_pct": None, "avg_amplitude": None, "avg_close_pct": None, "red_rate": None,
        }
    return {
        "n": n,
        "open_higher_rate": sum(1 for e in events if e["open_pct"] > 0) / n,
        "avg_open_pct": sum(e["open_pct"] for e in events) / n,
        "avg_high_pct": sum(e["high_pct"] for e in events) / n,
        "avg_low_pct": sum(e["low_pct"] for e in events) / n,
        "avg_amplitude": sum(e["high_pct"] - e["low_pct"] for e in events) / n,
        "avg_close_pct": sum(e["close_pct"] for e in events) / n,
        "red_rate": sum(1 for e in events if e["is_red"]) / n,
    }
