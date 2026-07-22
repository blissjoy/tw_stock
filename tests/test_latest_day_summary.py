import pandas as pd

from src.patterns.latest_day_summary import (
    classify_latest_candle_name,
    detect_latest_day_candle_patterns,
    detect_latest_day_volume_signals,
    summarize_latest_day,
)


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _flat_row(close: float = 100.0, volume: float = 1000.0) -> dict:
    return {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": volume}


def test_classify_latest_candle_name_long_red():
    df = _df([{"open": 100.0, "high": 115.0, "low": 95.0, "close": 107.0, "volume": 1000}])
    assert classify_latest_candle_name(df) == "長紅K"


def test_classify_latest_candle_name_long_black():
    df = _df([{"open": 107.0, "high": 112.0, "low": 92.0, "close": 100.0, "volume": 1000}])
    assert classify_latest_candle_name(df) == "長黑K"


def test_classify_latest_candle_name_doji():
    df = _df([{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000}])
    assert classify_latest_candle_name(df) == "十字線"


def test_classify_latest_candle_name_hammer():
    df = _df([{"open": 100.0, "high": 103.0, "low": 90.0, "close": 102.0, "volume": 1000}])
    assert classify_latest_candle_name(df) == "槌子線"


def test_classify_latest_candle_name_inverted_hammer():
    df = _df([{"open": 100.0, "high": 113.0, "low": 99.0, "close": 98.0, "volume": 1000}])
    assert classify_latest_candle_name(df) == "倒槌子線"


def test_detect_latest_day_candle_patterns_basic_reversal_at_high():
    # 沿用 tests/test_candle_patterns_2.py 已驗證過的資料(open_=[100,104], close=[104,100])，
    # 前面墊幾天平盤資料只是為了讓向量化函式(rolling等)有足夠資料可算，不影響這組判斷。
    rows = [_flat_row(100.0) for _ in range(5)] + [
        {"open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0, "volume": 1000},
        {"open": 104.0, "high": 105.0, "low": 99.0, "close": 100.0, "volume": 1000},
    ]
    df = _df(rows)
    hits = detect_latest_day_candle_patterns(df)
    assert "基本反轉（高檔）" in hits


def test_detect_latest_day_candle_patterns_falling_three_black_candles():
    rows = [_flat_row(120.0) for _ in range(5)] + [
        {"open": 110.0, "high": 111.0, "low": 104.0, "close": 105.0, "volume": 1000},
        {"open": 105.0, "high": 106.0, "low": 99.0, "close": 100.0, "volume": 1000},
        {"open": 100.0, "high": 101.0, "low": 94.0, "close": 95.0, "volume": 1000},
    ]
    df = _df(rows)
    hits = detect_latest_day_candle_patterns(df)
    assert "下跌連3黑" in hits


def test_detect_latest_day_candle_patterns_evening_star():
    rows = [_flat_row(100.0) for _ in range(3)] + [
        {"open": 100.0, "high": 109.0, "low": 99.0, "close": 108.0, "volume": 1000},   # 左：中長紅(>3.5%)
        {"open": 108.5, "high": 109.5, "low": 108.0, "close": 108.7, "volume": 1000},  # 中：小紅/星形
        {"open": 108.0, "high": 108.5, "low": 100.0, "close": 101.0, "volume": 1000},  # 右：中長黑(>3.5%)
    ]
    df = _df(rows)
    hits = detect_latest_day_candle_patterns(df)
    assert "夜星" in hits


def test_detect_latest_day_candle_patterns_morning_star():
    rows = [_flat_row(100.0) for _ in range(3)] + [
        {"open": 108.0, "high": 109.0, "low": 99.0, "close": 100.0, "volume": 1000},   # 左：中長黑(>3.5%)
        {"open": 99.5, "high": 100.0, "low": 99.0, "close": 99.7, "volume": 1000},     # 中：小紅/星形
        {"open": 100.0, "high": 108.5, "low": 99.5, "close": 107.0, "volume": 1000},   # 右：中長紅(>3.5%)
    ]
    df = _df(rows)
    hits = detect_latest_day_candle_patterns(df)
    assert "晨星" in hits


def test_detect_latest_day_candle_patterns_empty_when_too_short():
    df = _df([_flat_row()])
    assert detect_latest_day_candle_patterns(df) == []


def test_detect_latest_day_volume_signals_attack_volume():
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(5)] + [
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1300},  # 前5日均量1000的1.3倍
    ]
    df = _df(rows)
    hits = detect_latest_day_volume_signals(df)
    assert any("攻擊量" in h for h in hits)


def test_detect_latest_day_volume_signals_big_volume_vs_prev_day():
    rows = [_flat_row(100.0, volume=1000.0) for _ in range(3)] + [
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 2500},  # 前一日量的2倍以上
    ]
    df = _df(rows)
    hits = detect_latest_day_volume_signals(df)
    assert any("爆量" in h for h in hits)


def test_detect_latest_day_volume_signals_empty_when_too_short():
    df = _df([_flat_row()])
    assert detect_latest_day_volume_signals(df) == []


def test_summarize_latest_day_returns_empty_structure_for_empty_df():
    result = summarize_latest_day(pd.DataFrame())
    assert result == {"candle_name": None, "patterns": [], "volume_signals": []}


def test_summarize_latest_day_combines_all_three_parts():
    rows = [_flat_row(100.0) for _ in range(5)] + [
        {"open": 100.0, "high": 115.0, "low": 95.0, "close": 107.0, "volume": 1300},
    ]
    df = _df(rows)
    result = summarize_latest_day(df)
    assert result["candle_name"] == "長紅K"
    assert isinstance(result["patterns"], list)
    assert isinstance(result["volume_signals"], list)
