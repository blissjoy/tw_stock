import pandas as pd

from src.presentation.q3_analysis import load_q3_analysis


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="B")


def _uptrend_price_df(n: int = 120) -> pd.DataFrame:
    """穩定緩步上漲的OHLCV，確保MA5>MA10>MA20多頭排列成立、資料量足夠所有指標暖身。"""
    dates = _dates(n)
    close = [100.0 + i * 0.5 for i in range(n)]
    df = pd.DataFrame(
        {
            "open": [c - 0.2 for c in close],
            "high": [c + 1.0 for c in close],
            "low": [c - 1.0 for c in close],
            "close": close,
            "volume": [1000.0 + (i % 5) * 20 for i in range(n)],
        },
        index=dates,
    )
    for period in (5, 10, 20, 60, 120, 240):
        df[f"MA{period}"] = df["close"].rolling(period, min_periods=period).mean()
    return df


def test_load_q3_analysis_returns_none_when_not_enough_data():
    df = _uptrend_price_df(10)
    assert load_q3_analysis(df) is None


def test_load_q3_analysis_returns_full_structure_for_uptrend():
    df = _uptrend_price_df(120)

    result = load_q3_analysis(df)

    assert result is not None
    assert set(result.keys()) == {"indicators", "matrix", "patterns", "verdict"}
    assert result["indicators"]["ma8"] is not None
    assert result["verdict"]["tier"] in ("強勢", "中性", "轉弱")
    assert result["verdict"]["support_price"] == result["indicators"]["ma8"]
    assert isinstance(result["verdict"]["bullets"], list)
    assert len(result["verdict"]["bullets"]) <= 3


def test_load_q3_analysis_bullish_ma_alignment_pushes_verdict_score_positive():
    df = _uptrend_price_df(120)

    result = load_q3_analysis(df)

    # 穩定上漲、均線多頭排列，KD/MACD在暖身後也應偏多，分數應該是正的
    assert result["verdict"]["score"] > 0
    assert any("多頭排列" in b for b in result["verdict"]["bullets"])
