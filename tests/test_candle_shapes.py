import pandas as pd

from src.indicators.candles import (
    big_black_candle_half_price_tiers,
    big_red_candle_entry_filter,
    big_red_candle_half_price_tiers,
    black_red_black_continuation,
    channel_breakdown_strength_score,
    channel_breakout_strength_score,
    channel_pre_breakdown_long_signal,
    channel_pre_breakout_short_signal,
    classify_big_black_candle_resistance_test,
    classify_big_red_candle_support_test,
    consecutive_3day_upper_shadow_at_resistance,
    evening_star_invalidated,
    evening_star_pattern,
    high_black_meeting_pattern,
    is_doji,
    is_gravestone_line,
    is_hammer_candle,
    is_inverted_hammer_candle,
    is_limit_move,
    is_long_t_line,
    is_short_rebound_signal,
    is_spindle_candle,
    long_upper_shadow_at_high,
    low_red_meeting_pattern,
    morning_star_invalidated,
    morning_star_pattern,
    prev_bar_support_resistance_signal,
    red_black_red_continuation,
)


def test_prev_bar_support_resistance_signal():
    # R-CANDLE-01: 收盤突破前高=買方轉強；跌破前低=賣方轉強；否則多空未表態
    high = pd.Series([10.0, 11.0, 10.0, 10.0])
    low = pd.Series([9.0, 9.0, 9.0, 9.0])
    close = pd.Series([10.0, 12.0, 10.0, 8.0])
    result = prev_bar_support_resistance_signal(close, high, low)
    assert pd.isna(result.iloc[0])
    assert result.iloc[1] == "買方力量轉強"
    assert result.iloc[2] == "多空未表態"
    assert result.iloc[3] == "賣方力量轉強"


def test_is_spindle_candle_requires_both_shadows_within_half_body():
    # R-CANDLE-24: 上下影線都很短(<=實體1/2)，與西方Spinning Top方向相反
    open_ = pd.Series([100.0, 100.0])
    close = pd.Series([103.0, 103.0])
    high = pd.Series([104.0, 110.0])   # 第2根上影線過長
    low = pd.Series([99.0, 99.0])
    result = is_spindle_candle(open_, high, low, close)
    assert result.tolist() == [True, False]


def test_is_hammer_and_inverted_hammer():
    # R-CANDLE-25: 槌子=下影線>=實體2倍且上影線短；倒槌=上影線>=實體2倍且下影線短，顏色不拘
    open_ = pd.Series([100.0, 100.0])
    close = pd.Series([101.0, 99.0])
    high = pd.Series([101.5, 108.0])
    low = pd.Series([95.0, 98.5])

    hammer = is_hammer_candle(open_, high, low, close)
    inverted = is_inverted_hammer_candle(open_, high, low, close)
    assert hammer.tolist() == [True, False]
    assert inverted.tolist() == [False, True]


def test_big_red_candle_half_price_tiers_and_classification():
    tiers = big_red_candle_half_price_tiers(high=110, low=100)
    assert tiers == {"最強支撐": 110, "平均成本支撐": 105, "最弱支撐": 100}

    assert classify_big_red_candle_support_test(tiers, close_test=107) == "攻擊力道減弱，須3~5個交易日內站回最高點之上，否則注意轉折向下"
    assert classify_big_red_candle_support_test(tiers, close_test=102) == "跌破平均成本，容易產生大量賣壓，多方氣勢轉弱"
    assert "高檔做頭機率大增" in classify_big_red_candle_support_test(tiers, close_test=102, is_at_high=True)
    assert classify_big_red_candle_support_test(tiers, close_test=95) == "跌破最低點，多空易位，該長紅K轉為日後壓力"
    assert classify_big_red_candle_support_test(tiers, close_test=95, next_close=101) == "假跌破，不算真正轉弱（次日已收復）"
    assert classify_big_red_candle_support_test(tiers, close_test=112) == "支撐未破，多方氣勢維持"


def test_big_black_candle_half_price_tiers_and_classification():
    tiers = big_black_candle_half_price_tiers(high=100, low=90)
    assert tiers == {"最強壓力": 90, "平均成本壓力": 95, "最弱壓力": 100}

    assert classify_big_black_candle_resistance_test(tiers, close_test=93) == "向下力道減弱，注意是否轉折向上反彈"
    assert classify_big_black_candle_resistance_test(tiers, close_test=98) == "突破放空平均成本，容易產生大量回補買單，空方氣勢轉弱"
    assert classify_big_black_candle_resistance_test(tiers, close_test=102) == "突破最高點，多空易位，該長黑K轉為日後重要支撐"
    assert classify_big_black_candle_resistance_test(tiers, close_test=89) == "壓力未破，空方氣勢維持"


def test_is_short_rebound_signal():
    assert is_short_rebound_signal(True, today_close=105, prev_high=100) is True
    assert is_short_rebound_signal(True, today_close=95, prev_high=100) is False
    assert is_short_rebound_signal(False, today_close=105, prev_high=100) is False


def test_big_red_candle_entry_filter():
    # R-CANDLE-23：4種可買清單、5種不可買清單，避免清單同時命中時不可買優先
    assert big_red_candle_entry_filter(False) == "非大量長紅K，不適用本規則"
    assert big_red_candle_entry_filter(True, bear_to_bull_first_break_prior_high=True, ma_triple_bullish=True) == "符合進場條件的大量長紅K"
    assert big_red_candle_entry_filter(True, below_ma20=True) == "不建議進場的大量長紅K位置"
    assert big_red_candle_entry_filter(True) == "不在明列的可買清單內，保守觀望"
    # 避免清單優先於可買清單
    assert big_red_candle_entry_filter(True, bull_pullback_reversal=True, is_bear_rebound=True) == "不建議進場的大量長紅K位置"


def test_is_doji_and_variants():
    open_ = pd.Series([100.0, 100.0])
    close = pd.Series([100.3, 105.0])
    assert is_doji(open_, close).tolist() == [True, False]

    # 墓碑線：十字線+上影線長+下影線趨近於0
    grave_open = pd.Series([100.0])
    grave_high = pd.Series([103.0])
    grave_low = pd.Series([99.8])
    grave_close = pd.Series([100.2])
    assert is_gravestone_line(grave_open, grave_high, grave_low, grave_close).tolist() == [True]

    # 長T線：十字線+下影線長+上影線趨近於0
    t_open = pd.Series([100.0])
    t_high = pd.Series([100.15])
    t_low = pd.Series([97.0])
    t_close = pd.Series([100.1])
    assert is_long_t_line(t_open, t_high, t_low, t_close).tolist() == [True]


def test_is_limit_move_uses_10pct_tw_limit():
    close = pd.Series([110.0])
    prev_close = pd.Series([100.0])
    assert is_limit_move(close, prev_close, "up").tolist() == [True]

    close_down = pd.Series([90.0])
    assert is_limit_move(close_down, prev_close, "down").tolist() == [True]


def test_high_black_and_low_red_meeting_pattern():
    # R-CANDLE-07: 高檔長黑遭遇(一日封口)
    assert high_black_meeting_pattern(open_1=100, close_1=106, open_2=108, close_2=105.5) is True
    assert high_black_meeting_pattern(open_1=100, close_1=106, open_2=108, close_2=95) is False  # 封口太遠

    # R-CANDLE-15: 低檔長紅遭遇(一日封口)，鏡射
    assert low_red_meeting_pattern(open_1=106, close_1=100, open_2=98, close_2=100.5) is True


def test_evening_star_and_morning_star_patterns():
    assert evening_star_pattern(True, True, True, True) is True
    assert evening_star_invalidated(right_black_high=100, subsequent_highs=[98, 102]) is True
    assert evening_star_invalidated(right_black_high=100, subsequent_highs=[98, 99]) is False

    assert morning_star_pattern(True, True, True, True) is True
    assert morning_star_invalidated(right_red_low=100, subsequent_lows=[102, 98]) is True


def test_channel_breakout_strength_and_pre_breakout_signals():
    assert channel_breakout_strength_score(True, True, True) == 3
    assert channel_breakout_strength_score(False, True, False) == 1
    assert channel_pre_breakout_short_signal(True, True) is True
    assert channel_breakdown_strength_score(True, True, True) == 3
    assert channel_pre_breakdown_long_signal(True, True) is True


def test_long_upper_shadow_at_high_and_consecutive_3day():
    open_ = pd.Series([100.0])
    high = pd.Series([110.0])
    low = pd.Series([99.0])
    close = pd.Series([101.0])
    assert long_upper_shadow_at_high(open_, high, low, close).tolist() == [True]

    assert consecutive_3day_upper_shadow_at_resistance([True, True, True], [True, True, True]) is True
    assert consecutive_3day_upper_shadow_at_resistance([True, True, False], [True, True, True]) is False


def test_red_black_red_and_black_red_black_continuation():
    assert red_black_red_continuation(True, True, True, True, True) is True
    assert red_black_red_continuation(True, True, True, False, True) is False
    assert black_red_black_continuation(True, True, True, True, True) is True
