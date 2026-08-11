import pandas as pd

from src.indicators.volume_price_matrix import format_volume_price_relation
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


def test_load_q3_analysis_volume_price_relation_matches_pdf_style_and_is_consistent_with_q1_q2():
    """使用者2026-08-10拿合晶(6182)的「價跌量增(背離)」實例反映：這個PDF風格的
    「今日量價關係」欄位要能直接顯示，不能只算出Q1/Q2卻沒有組成這句話。"""
    df = _uptrend_price_df(120)

    result = load_q3_analysis(df)
    ind = result["indicators"]

    assert ind["volume_price_relation"] == format_volume_price_relation(ind["q1_price"], ind["q2_volume"])


def test_load_q3_analysis_exposes_volume_ratio_vs_ma5_for_ui_transparency():
    """使用者2026-08-10拿中光電(5371)實測發現「量平」判斷結果跟PDF原文「量縮」
    對不上，追查是外部工具用的量能判斷基準不明(黑盒子)，不是bug。使用者要求
    把「今日量/5日均量」實際比例數字顯示出來，方便之後自己判斷，這裡驗證這個
    欄位確實跟volume/ma5_vol的實際比例一致。"""
    df = _uptrend_price_df(120)

    result = load_q3_analysis(df)
    ind = result["indicators"]

    ma5_vol = df["volume"].rolling(5, min_periods=5).mean()
    expected_ratio = float(df["volume"].iloc[-1] / ma5_vol.iloc[-1] * 100)
    assert ind["volume_ratio_vs_ma5_pct"] == expected_ratio


def test_load_q3_analysis_flags_divergence_when_price_falls_on_rising_volume():
    n = 60
    dates = _dates(n)
    close = [100.0] * (n - 1) + [95.0]  # 今天大跌
    volume = [1000.0] * (n - 1) + [5000.0]  # 今天爆量
    df = pd.DataFrame(
        {"open": close, "high": [c + 1 for c in close], "low": [c - 1 for c in close], "close": close, "volume": volume},
        index=dates,
    )
    for period in (5, 10, 20, 60, 120, 240):
        df[f"MA{period}"] = pd.Series(close, index=dates).rolling(period, min_periods=period).mean()

    result = load_q3_analysis(df)

    assert result["indicators"]["volume_price_relation"] == "價跌量增(背離)"
