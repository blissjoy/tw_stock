import pandas as pd

import src.patterns.chart_overlays as chart_overlays
from src.indicators.pivots import TurningPoint
from src.indicators.trendlines import LinePoint
from src.patterns.chart_overlays import (
    compute_support_resistance_levels,
    compute_trendlines,
    trendline_to_xy,
)


def _flat_df(n: int = 10, base: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({
        "open": [base] * n, "high": [base] * n, "low": [base] * n, "close": [base] * n, "volume": [1000] * n,
    })


def test_compute_trendlines_returns_empty_when_too_few_rows():
    df = _flat_df(n=3)
    assert compute_trendlines(df) == {}


def test_compute_trendlines_finds_up_tangent_and_channel_from_two_rising_bottoms(monkeypatch):
    df = _flat_df(n=10)
    df.loc[4, "high"] = 102.0  # 兩個底之間唯一的相對高點，供軌道線取M點用

    # 直接控制compute_turning_points的輸出，避免用真實資料湊出特定轉折點的脆弱性——
    # 這裡要測的是compute_trendlines「怎麼組裝」，不是重測pivots.py本身的轉折點演算法。
    monkeypatch.setattr(
        chart_overlays, "compute_turning_points",
        lambda high, low, close, n=5: [
            TurningPoint("bottom", 90.0, 2),
            TurningPoint("bottom", 95.0, 7),  # 底底高：95 > 90
        ],
    )

    lines = compute_trendlines(df)

    assert "up_tangent" in lines
    up_tangent = lines["up_tangent"]
    assert up_tangent.a == LinePoint(2, 90.0)
    assert up_tangent.b == LinePoint(7, 95.0)
    assert up_tangent.role == "support"

    assert "up_channel" in lines  # 兩底之間有中間K棒(idx3~6)，應該算得出軌道線
    assert lines["up_channel"].a == LinePoint(4, 102.0)  # idx4是中間唯一的相對高點


def test_compute_trendlines_skips_channel_when_bottoms_are_adjacent(monkeypatch):
    df = _flat_df(n=10)
    monkeypatch.setattr(
        chart_overlays, "compute_turning_points",
        lambda high, low, close, n=5: [
            TurningPoint("bottom", 90.0, 2),
            TurningPoint("bottom", 95.0, 3),  # 相鄰，兩底之間沒有K棒可取中間點
        ],
    )

    lines = compute_trendlines(df)

    assert "up_tangent" in lines
    assert "up_channel" not in lines


def test_compute_trendlines_no_tangent_when_bottoms_not_rising(monkeypatch):
    df = _flat_df(n=10)
    monkeypatch.setattr(
        chart_overlays, "compute_turning_points",
        lambda high, low, close, n=5: [
            TurningPoint("bottom", 95.0, 2),
            TurningPoint("bottom", 90.0, 7),  # 底底低，不符合上升切線條件
        ],
    )

    lines = compute_trendlines(df)
    assert lines == {}


def test_trendline_to_xy_extends_line_from_start_point_to_last_row():
    df = _flat_df(n=6)
    line = chart_overlays.TrendLine(a=LinePoint(1, 10.0), b=LinePoint(3, 20.0))

    dates, prices = trendline_to_xy(line, df)

    assert dates == list(df.index[1:6])
    assert prices[0] == 10.0
    assert prices[2] == 20.0  # x=3
    assert len(prices) == 5  # 從x=1畫到x=5(資料最後一天)


def test_compute_support_resistance_levels_classifies_role_by_current_price(monkeypatch):
    df = _flat_df(n=10, base=100.0)
    df.loc[9, "close"] = 105.0  # 目前價高於底部轉折點、低於頭部轉折點

    monkeypatch.setattr(
        chart_overlays, "compute_turning_points",
        lambda high, low, close, n=5: [
            TurningPoint("bottom", 90.0, 2),
            TurningPoint("head", 110.0, 5),
        ],
    )

    levels = compute_support_resistance_levels(df)

    by_type = {lv["type"]: lv for lv in levels}
    assert by_type["bottom"]["role"] == "支撐"  # 現價105 > 底部90，尚未跌破，支撐有效
    assert by_type["head"]["role"] == "壓力"    # 現價105 < 頭部110，尚未突破，仍是壓力


def test_compute_support_resistance_levels_returns_empty_for_empty_df():
    assert compute_support_resistance_levels(pd.DataFrame()) == []
