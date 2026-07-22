import pandas as pd

from src.indicators.consolidation import detect_consolidation, detect_consolidation_breakout


def _bars(rows):
    """rows: list of (open, high, low, close) tuples."""
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    return df["open"], df["high"], df["low"], df["close"]


def test_detect_consolidation_requires_three_non_expanding_bars():
    # bar0 是錨點；bar1、bar2 都沒有突破/跌破累積區間 -> 第3根(index2)才滿足 >=3 根門檻
    open_, high, low, close = _bars(
        [
            (100, 102, 98, 101),
            (101, 101, 99, 100),
            (100, 100, 99, 99.5),
        ]
    )
    box = detect_consolidation(high, low, min_bars=3)
    assert box["group_len"].tolist() == [1, 2, 3]
    assert box["is_consolidating"].tolist() == [False, False, True]
    assert box["upper_neckline"].tolist() == [102, 102, 102]
    assert box["lower_neckline"].tolist() == [98, 98, 98]


def test_breakout_up_uses_prior_established_box_not_todays_own_range():
    # 這是R-CANDLE-04實作最容易踩到的陷阱：突破當天自己的高點不能拿來墊高當天比較的區間，
    # 必須用「前一天已經確立」的頸線來判斷，否則永遠測不到突破。
    open_, high, low, close = _bars(
        [
            (100, 102, 98, 101),    # bar0：錨點
            (101, 101, 99, 100),    # bar1：延續區間
            (100, 100, 99, 99.5),   # bar2：延續區間，第3根 -> 確立橫盤(上頸線102/下頸線98)
            (99.5, 110, 99, 108),   # bar3：中長紅K，收盤108 > 前一天頸線102 -> 突破
        ]
    )
    result = detect_consolidation_breakout(open_, high, low, close, min_bars=3)

    assert result["breakout_up"].tolist() == [False, False, False, True]
    assert result["breakout_down"].tolist() == [False, False, False, False]


def test_breakout_down_symmetric_case():
    open_, high, low, close = _bars(
        [
            (100, 102, 98, 99),
            (99, 101, 99, 100),
            (100, 100, 98, 99.5),
            (99.5, 100, 90, 91),   # 中長黑K，收盤91 < 前一天下頸線98 -> 跌破
        ]
    )
    result = detect_consolidation_breakout(open_, high, low, close, min_bars=3)

    assert result["breakout_down"].tolist() == [False, False, False, True]
    assert result["breakout_up"].tolist() == [False, False, False, False]


def test_no_breakout_flagged_when_box_not_yet_established():
    # 只有2根非擴張K棒，尚未達到 min_bars 門檻，即使第3根收盤創高也不算突破
    open_, high, low, close = _bars(
        [
            (100, 102, 98, 101),
            (101, 101, 99, 100),
            (100, 110, 99, 108),  # 尚未確立橫盤，不能判定突破
        ]
    )
    result = detect_consolidation_breakout(open_, high, low, close, min_bars=3)
    assert not result["breakout_up"].any()
