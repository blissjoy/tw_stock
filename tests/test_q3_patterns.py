import pandas as pd

from src.screener.q3_patterns import scan_q3_patterns


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="B")


def _flat_df(n: int, price: float = 100.0, vol: float = 1000.0) -> pd.DataFrame:
    dates = _dates(n)
    return pd.DataFrame(
        {
            "open": [price] * n,
            "high": [price + 1] * n,
            "low": [price - 1] * n,
            "close": [price] * n,
            "volume": [vol] * n,
        },
        index=dates,
    )


def test_scan_q3_patterns_returns_empty_when_not_enough_data():
    df = _flat_df(10)
    assert scan_q3_patterns(df, golden_tier_results=[]) == []


def test_scan_q3_patterns_reuses_wired_rule_for_pattern_01():
    df = _flat_df(40)
    golden_tier_results = [{"rule_id": "R-CLASSIC-30", "note": "均線糾結後帶量長紅突破"}]

    results = scan_q3_patterns(df, golden_tier_results)

    rule_ids = {r["rule_id"] for r in results}
    assert "R-Q3P-01" in rule_ids
    assert "R-Q3P-22" in rule_ids  # 01跟22共用同一個既有規則觸發結果


def test_scan_q3_patterns_pattern_04_only_triggers_on_breakout_up_direction():
    df = _flat_df(40)
    up_results = [{"rule_id": "R-CANDLE-04", "note": "中長紅K收盤突破盤整區上緣100.00，橫盤突破確認"}]
    down_results = [{"rule_id": "R-CANDLE-04", "note": "中長黑K收盤跌破盤整區下緣100.00，橫盤跌破確認"}]

    up_matches = {r["rule_id"] for r in scan_q3_patterns(df, up_results)}
    down_matches = {r["rule_id"] for r in scan_q3_patterns(df, down_results)}

    assert "R-Q3P-04" in up_matches
    assert "R-Q3P-04" not in down_matches
    assert "R-Q3P-16" in down_matches
    assert "R-Q3P-16" not in up_matches


def test_scan_q3_patterns_pattern_12_breakdown_no_volume():
    n = 65
    dates = _dates(n)
    close = [100.0 - i * 0.3 for i in range(n)]  # 持續緩跌，今天創新低
    close[-1] = min(close) - 1  # 確保今天跌破前60日低點
    volume = [2000.0] * (n - 1) + [500.0]  # 今天量縮
    df = pd.DataFrame(
        {"open": close, "high": [c + 1 for c in close], "low": [c - 1 for c in close], "close": close, "volume": volume},
        index=dates,
    )

    results = scan_q3_patterns(df, golden_tier_results=[])

    assert any(r["rule_id"] == "R-Q3P-12" for r in results)


def test_scan_q3_patterns_pattern_25_high_flat_with_big_volume_every_day():
    n = 40
    dates = _dates(n)
    close = [100.0] * n
    volume = [1000.0] * 35 + [5000.0] * 5  # 只有最近5天量能明顯放大，前面維持基準量
    df = pd.DataFrame(
        {"open": close, "high": [c + 0.5 for c in close], "low": [c - 0.5 for c in close], "close": close, "volume": volume},
        index=dates,
    )

    results = scan_q3_patterns(df, golden_tier_results=[])

    assert any(r["rule_id"] == "R-Q3P-25" for r in results)
