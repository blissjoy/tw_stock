import pandas as pd
import pytest

from src.indicators.candles import (
    black_candle_body_pct,
    classify_black_candle_size,
    classify_red_candle_size,
    is_mid_long_black_candle,
    is_mid_long_red_candle,
    red_candle_body_pct,
)


def test_red_candle_size_thresholds_use_pct_change_not_body_ratio():
    # R-CANDLE-21：長中小紅K是用「開盤到收盤漲幅%」門檻(6.5% / 3.5%)，不是實體占比
    open_ = pd.Series([100.0, 100.0, 100.0, 100.0])
    close = pd.Series([107.0, 104.0, 102.0, 99.0])  # 最後一根是黑K(跌)，非紅K

    sizes = classify_red_candle_size(open_, close)
    assert sizes.iloc[:3].tolist() == ["長紅K", "中紅K", "小紅K"]
    assert pd.isna(sizes.iloc[3])

    body_pct = red_candle_body_pct(open_, close)
    assert body_pct.iloc[0] == pytest.approx(0.07)
    assert pd.isna(body_pct.iloc[3])  # 黑K不算紅K，應為 NaN


def test_black_candle_size_thresholds_symmetric_with_red():
    # R-CANDLE-22：長中小黑K同樣用跌幅%門檻，方向相反
    open_ = pd.Series([100.0, 100.0, 100.0, 100.0])
    close = pd.Series([93.0, 96.0, 98.0, 101.0])  # 最後一根是紅K，非黑K

    sizes = classify_black_candle_size(open_, close)
    assert sizes.iloc[:3].tolist() == ["長黑K", "中黑K", "小黑K"]
    assert pd.isna(sizes.iloc[3])

    body_pct = black_candle_body_pct(open_, close)
    assert body_pct.iloc[0] == pytest.approx(0.07)
    assert pd.isna(body_pct.iloc[3])


def test_mid_long_candle_helpers_require_at_least_3_5_pct():
    open_ = pd.Series([100.0, 100.0, 100.0, 100.0])
    close = pd.Series([103.4, 103.5, 96.5, 96.6])  # 紅K剛好在3.5%門檻兩側、黑K同理

    red_flags = is_mid_long_red_candle(open_, close)
    assert red_flags.tolist() == [False, True, False, False]

    black_flags = is_mid_long_black_candle(open_, close)
    assert black_flags.tolist() == [False, False, True, False]
