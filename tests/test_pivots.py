import pandas as pd

from src.indicators.pivots import compute_turning_points


def test_compute_turning_points_matches_hand_traced_ma5_crossovers():
    # R-TREND-01：收盤跌破MA5取區間最高點(頭)、收盤突破MA5取區間最低點(底)
    # 這組資料的 MA5（滾動5日均線）已手算驗證如下（索引從0開始）：
    #   idx4=9.8  idx5=10.2 idx6=10.6 idx7=11.0 idx8=11.4 idx9=12.0
    #   idx10=11.2 idx11=10.4 idx12=9.6 idx13=8.8 idx14=8.0
    # 用平盤K棒（High=Low=Close）簡化，方便手動核對結果。
    closes = [10, 10, 10, 10, 9, 12, 12, 12, 12, 12, 8, 8, 8, 8, 8]
    close = pd.Series(closes, dtype=float)
    high = close.copy()
    low = close.copy()

    turning_points = compute_turning_points(high, low, close, n=5)

    # 手動追蹤：idx4 收盤9 < MA5(9.8) 為負價，idx5 收盤12 > MA5(10.2) 轉正
    #   -> 由負轉正，取 idx4~idx5 區間最低點 -> bottom @ idx4, price=9
    # idx5~idx9 持續正價，idx10 收盤8 < MA5(11.2) 轉負
    #   -> 由正轉負，取 idx5~idx10 區間最高點 -> head @ idx5, price=12（同高時取序列中最先出現者）
    assert len(turning_points) == 2

    bottom, head = turning_points
    assert bottom.type == "bottom"
    assert bottom.price == 9.0
    assert bottom.index == 4

    assert head.type == "head"
    assert head.price == 12.0
    assert head.index == 5


def test_compute_turning_points_empty_when_series_shorter_than_period():
    close = pd.Series([10.0, 11.0, 12.0])
    result = compute_turning_points(close, close, close, n=5)
    assert result == []
