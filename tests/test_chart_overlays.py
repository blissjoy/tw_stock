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


def test_compute_trendlines_tracks_newest_valid_consecutive_pair_not_a_stale_one(monkeypatch):
    """R-LINE-05動態更新：只比較『相鄰』的轉折點配對，每次都用最新一組更新，不是回頭搜尋
    整段歷史找任一組合法的舊配對。這裡構造(b0,b1)合法、(b1,b2)不合法(未底底高)、(b2,b3)
    又合法的情境——結果應該是最新的(b2,b3)，不能停在很久以前的(b0,b1)。"""
    df = _flat_df(n=10)
    monkeypatch.setattr(
        chart_overlays, "compute_turning_points",
        lambda high, low, close, n=5: [
            TurningPoint("bottom", 90.0, 2),
            TurningPoint("bottom", 95.0, 4),  # (b0,b1)：95>90，合法
            TurningPoint("bottom", 92.0, 6),  # (b1,b2)：92<95，不是底底高，不合法
            TurningPoint("bottom", 98.0, 8),  # (b2,b3)：98>92，合法，應該取代(b0,b1)成為目前的線
        ],
    )

    lines = compute_trendlines(df)

    assert lines["up_tangent"].a == LinePoint(6, 92.0)
    assert lines["up_tangent"].b == LinePoint(8, 98.0)


def test_compute_trendlines_swaps_role_to_resistance_when_broken_by_latest_close(monkeypatch):
    """R-LINE-11：如果目前這條上升切線已經被最新收盤價跌破，代表它已經失去支撐作用、
    角色互換成壓力——不應該繼續被當成還在生效的支撐線呈現(這正是使用者回報的問題：
    2911案例裡6月初畫的切線，到7月下旬早就跌破了，卻還被畫得像現在仍是支撐)。"""
    df = _flat_df(n=10)
    df.loc[9, "close"] = 50.0  # 遠低於切線在x=9的延伸值(90+2.5*7=107.5)，明確跌破

    monkeypatch.setattr(
        chart_overlays, "compute_turning_points",
        lambda high, low, close, n=5: [
            TurningPoint("bottom", 90.0, 2),
            TurningPoint("bottom", 95.0, 4),  # 唯一一組合法配對，之後沒有更新的線
        ],
    )

    lines = compute_trendlines(df)

    assert lines["up_tangent"].role == "resistance"  # 角色已互換，不再是"support"


def test_compute_trendlines_keeps_support_role_when_not_broken(monkeypatch):
    df = _flat_df(n=10)
    df.loc[9, "close"] = 150.0  # 切線在x=9的延伸值是90+2.5*7=107.5，150遠高於此，不算跌破

    monkeypatch.setattr(
        chart_overlays, "compute_turning_points",
        lambda high, low, close, n=5: [
            TurningPoint("bottom", 90.0, 2),
            TurningPoint("bottom", 95.0, 4),
        ],
    )

    lines = compute_trendlines(df)
    assert lines["up_tangent"].role == "support"


def test_nearest_support_resistance_picks_closest_above_and_below():
    levels = [
        {"price": 80.0, "type": "bottom", "role": "支撐", "date": 1},
        {"price": 95.0, "type": "bottom", "role": "支撐", "date": 2},  # 現價(100)之下最近
        {"price": 110.0, "type": "head", "role": "壓力", "date": 3},  # 現價之上最近
        {"price": 130.0, "type": "head", "role": "壓力", "date": 4},
    ]

    nearest = chart_overlays.nearest_support_resistance(levels, current_price=100.0)

    prices = {lv["price"] for lv in nearest}
    assert prices == {95.0, 110.0}
    assert len(nearest) == 2


def test_nearest_support_resistance_handles_only_one_side_present():
    levels = [{"price": 80.0, "type": "bottom", "role": "支撐", "date": 1}]
    nearest = chart_overlays.nearest_support_resistance(levels, current_price=100.0)
    assert len(nearest) == 1
    assert nearest[0]["price"] == 80.0


def test_nearest_support_resistance_handles_empty_list():
    assert chart_overlays.nearest_support_resistance([], current_price=100.0) == []


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
