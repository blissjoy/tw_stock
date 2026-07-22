import pandas as pd

from src.indicators.crossovers import interpret_cross, is_death_cross, is_golden_cross


def test_golden_cross_fires_when_short_ma_crosses_above_long_ma():
    # R-MA-13: 前一日短<=長，當日短已上穿長，才是黃金交叉那一天
    # index0: 無前一日資料 -> False；index1: 短均線=10剛好追平長均線10，尚未「上穿」-> False
    # index2: 前一日短(10)<=長(10) 且當日短(11)>長(10) -> 這天才是黃金交叉
    ma_short = pd.Series([9.0, 10.0, 11.0, 12.0])
    ma_long = pd.Series([10.0, 10.0, 10.0, 10.0])
    result = is_golden_cross(ma_short, ma_long)
    assert result.tolist() == [False, False, True, False]


def test_death_cross_fires_when_short_ma_crosses_below_long_ma():
    # R-MA-14: 前一日短>=長，當日短已下穿長，鏡射對稱
    ma_short = pd.Series([11.0, 10.0, 9.0, 8.0])
    ma_long = pd.Series([10.0, 10.0, 10.0, 10.0])
    result = is_death_cross(ma_short, ma_long)
    assert result.tolist() == [False, False, True, False]


def test_interpret_cross_depends_on_main_trend():
    # R-MA-15: 同一交叉事件，在多頭/空頭主趨勢下意義相反
    assert "買進" in interpret_cross("多頭", "黃金交叉")
    assert "賣出" in interpret_cross("多頭", "死亡交叉")
    assert "做空" in interpret_cross("空頭", "死亡交叉")
    assert "回補" in interpret_cross("空頭", "黃金交叉")
