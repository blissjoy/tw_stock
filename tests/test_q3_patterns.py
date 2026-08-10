import pandas as pd

from src.screener.q3_patterns import BASIS_EXISTING_RULE, BASIS_SIMPLIFIED, scan_q3_patterns


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


def test_scan_q3_patterns_basis_marks_existing_rule_vs_simplified():
    """使用者2026-08-10反映：「簡化版」不能只寫在文件裡，要能直接從資料標示出來，
    這裡驗證basis欄位確實區分了兩種型態的來源。"""
    df = _flat_df(65)
    golden_tier_results = [{"rule_id": "R-CLASSIC-30", "note": "均線糾結後帶量長紅突破"}]

    results = scan_q3_patterns(df, golden_tier_results)
    by_rule_id = {r["rule_id"]: r for r in results}

    assert by_rule_id["R-Q3P-01"]["basis"] == BASIS_EXISTING_RULE
    # 12號(破底無量陰跌不止)是本專案規則庫缺口的簡化版偵測——用持續緩跌+量縮觸發它
    n = 65
    dates = _dates(n)
    close = [100.0 - i * 0.3 for i in range(n)]
    close[-1] = min(close) - 1
    volume = [2000.0] * (n - 1) + [500.0]
    down_df = pd.DataFrame(
        {"open": close, "high": [c + 1 for c in close], "low": [c - 1 for c in close], "close": close, "volume": volume},
        index=dates,
    )
    down_results = scan_q3_patterns(down_df, golden_tier_results=[])
    by_rule_id_down = {r["rule_id"]: r for r in down_results}
    assert by_rule_id_down["R-Q3P-12"]["basis"] == BASIS_SIMPLIFIED


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
