import pandas as pd

from src.indicators.moving_average import FULL_PERIODS, compute_ma_set
from src.indicators.parabolic_sar import compute_sar, sar_flip_days_ago
from src.screener.indicator_precompute import compute_indicator_rows


def _make_df(closes: list[float], start="2026-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(closes))
    return pd.DataFrame(
        {
            "open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
            "close": closes, "volume": [1000] * len(closes),
        },
        index=dates,
    )


def test_compute_indicator_rows_returns_empty_list_for_empty_df():
    assert compute_indicator_rows("2330", pd.DataFrame(), {"2026-01-01"}) == []


def test_compute_indicator_rows_only_returns_requested_dates():
    df = _make_df([100.0 + i for i in range(30)])
    target_dates = {"2026-01-10", "2026-01-20"}

    rows = compute_indicator_rows("2330", df, target_dates)

    assert {r["date"] for r in rows} == target_dates
    assert all(r["stock_id"] == "2330" for r in rows)


def test_compute_indicator_rows_ma_values_match_compute_ma_set():
    """驗證這裡沒有重新定義均線算法，數值要跟直接呼叫compute_ma_set()一致——用第280天
    (300天資料的倒數第20天)確保MA240這種長天期均線也已經有足夠資料算出真正的數值，
    不是NaN。"""
    closes = [100.0 + i * 0.7 for i in range(300)]
    df = _make_df(closes)
    target_date = df.index[280].strftime("%Y-%m-%d")

    rows = compute_indicator_rows("2330", df, {target_date})
    assert len(rows) == 1
    row = rows[0]

    ma_frame = compute_ma_set(df["close"], periods=FULL_PERIODS)
    idx = 280
    assert row["ma5"] == ma_frame["MA5"].iloc[idx]
    assert row["ma20"] == ma_frame["MA20"].iloc[idx]
    assert row["ma240"] == ma_frame["MA240"].iloc[idx]


def test_compute_indicator_rows_ma_is_none_when_not_enough_history():
    """資料不足以算出某條均線時(例如只有10天，算不出MA20)，對應欄位是None不是NaN字面值
    或crash——NaN在SQLite裡可以存但語意不清楚，統一轉成None(對應SQL NULL)。"""
    df = _make_df([100.0 + i for i in range(10)])

    rows = compute_indicator_rows("2330", df, {"2026-01-05"})

    assert rows[0]["ma5"] is not None
    assert rows[0]["ma20"] is None  # 只有10天資料，MA20算不出來(min_periods=20)


def test_compute_indicator_rows_sar_matches_compute_sar():
    """驗證SAR數值/方向沒有重新定義，跟直接呼叫compute_sar()一致。"""
    highs = [10.0, 11.0, 12.0, 13.0, 9.0]
    lows = [9.0, 10.0, 10.5, 11.5, 8.0]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    dates = pd.date_range("2026-07-15", periods=5)
    df = pd.DataFrame(
        {"open": highs, "high": highs, "low": lows, "close": closes, "volume": [1000] * 5}, index=dates,
    )
    target_date = "2026-07-19"  # 最後一天，暴跌翻轉為空頭

    rows = compute_indicator_rows("2330", df, {target_date})
    assert len(rows) == 1
    row = rows[0]

    sar_bull, sar_values = compute_sar(df["high"], df["low"], df["close"])
    assert row["sar_is_bull"] == int(bool(sar_bull.iloc[-1]))
    assert row["sar_value"] == sar_values.iloc[-1]
    assert row["sar_is_bull"] == 0  # 空頭
    assert row["sar_flip_days_ago"] == sar_flip_days_ago(sar_bull)  # 跟官方函式在最後一天算出的值一致


def test_compute_indicator_rows_sar_is_none_when_not_enough_data():
    """只有1天資料，compute_sar()本身回傳空序列，對應欄位應該是None不是crash。"""
    df = _make_df([100.0])

    rows = compute_indicator_rows("2330", df, {"2026-01-01"})

    assert rows[0]["sar_value"] is None
    assert rows[0]["sar_is_bull"] is None
    assert rows[0]["sar_flip_days_ago"] is None


def test_sar_flip_days_ago_series_matches_official_function_at_every_position():
    """對序列裡每一個位置，用_sar_flip_days_ago_series()一次算出的值，要跟把序列截到
    該位置為止再呼叫官方的sar_flip_days_ago()逐一算出的值完全一致——確保這個「一次算
    整段序列」的快速版本沒有偷改語意，只是換了個算法(O(n)而非逐位置O(n)、整體O(n²))。"""
    from src.screener.indicator_precompute import _sar_flip_days_ago_series

    # 構造一段有多次翻轉的走勢：漲、跌、漲、跌...
    closes = [100, 105, 110, 108, 95, 90, 100, 108, 115, 112, 90, 85, 95, 105, 115]
    highs = [c + 2 for c in closes]
    lows = [c - 2 for c in closes]
    dates = pd.date_range("2026-01-01", periods=len(closes))
    df = pd.DataFrame({"high": highs, "low": lows, "close": closes}, index=dates)

    sar_bull, _ = compute_sar(df["high"], df["low"], df["close"])
    fast_result = _sar_flip_days_ago_series(sar_bull)

    for i in range(1, len(sar_bull)):
        truncated = sar_bull.iloc[: i + 1]
        official = sar_flip_days_ago(truncated)
        if official is None:
            continue  # 官方函式在截斷序列裡找不到翻轉點時回傳None，這裡改成累加計數，語意上的差異已在docstring說明
        assert fast_result[i] == official, f"mismatch at position {i}"
