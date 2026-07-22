import pytest

from src.strategies.operation_sop import (
    bad_news_no_fall_accumulation,
    bear_high_volume_no_fall,
    bear_no_rebound_strength,
    bear_to_bull_reversal_signal,
    bull_high_volume_no_rise,
    bull_no_pullback_strength,
    bull_to_bear_reversal_signal,
    can_add_position,
    good_news_no_rise_distribution,
    long_consolidation_breakout_signal,
    long_swing_exit_action,
    long_swing_second_wave_check,
    one_star_two_bear_fakeout,
    one_star_two_bull_fakeout,
    resistance_high_volume_no_rise,
    short_swing_entry_ready,
    short_swing_exit_action,
    short_swing_small_pullback_tolerance,
    star_dominance_signal,
    surge_stock_hold_conditions,
    surge_stock_volume_tier_action,
)


def test_short_swing_entry_ready_requires_all_conditions():
    # R-STRATEGY-01 第1條：多頭+突破MA5/前高+2%紅K+MA20/KD_K向上+量增
    assert short_swing_entry_ready(
        is_bull_trend=True, close_t=22, open_t=21.5, ma5_t=21, high_prev=21.8,
        ma20_slope=0.1, kd_k_slope=0.5, volume_t=1300, volume_avg=1000, volume_prev=1000,
    ) is True
    assert short_swing_entry_ready(
        is_bull_trend=False, close_t=22, open_t=21.5, ma5_t=21, high_prev=21.8,
        ma20_slope=0.1, kd_k_slope=0.5, volume_t=1300, volume_avg=1000, volume_prev=1000,
    ) is False


def test_short_swing_exit_action_priority_order():
    assert short_swing_exit_action(close_t=94, stop_loss=95, has_lower_high=False, profit_pct=0.0, ma5_t=96) == "跌破停損，出場"
    assert short_swing_exit_action(close_t=96, stop_loss=95, has_lower_high=True, profit_pct=0.0, ma5_t=96) == "頭頭低，趨勢改變出場"
    assert short_swing_exit_action(close_t=120, stop_loss=95, has_lower_high=False, profit_pct=0.25, ma5_t=118, big_black_signal=True, close_below_prior_low=True) == "跌破前一日低點，全部出場"
    assert short_swing_exit_action(close_t=120, stop_loss=95, has_lower_high=False, profit_pct=0.25, ma5_t=118, big_black_signal=True, close_below_prior_low=False) == "減碼二分之一"
    assert short_swing_exit_action(close_t=120, stop_loss=95, has_lower_high=False, profit_pct=0.25, ma5_t=118, big_black_signal=True, close_below_prior_low=False, next_day_gap_down_continue=True) == "出清剩餘部位"
    assert short_swing_exit_action(close_t=108, stop_loss=95, has_lower_high=False, profit_pct=0.11, ma5_t=109) == "獲利≥10%跌破MA5，停利出場"
    assert short_swing_exit_action(close_t=108, stop_loss=95, has_lower_high=False, profit_pct=0.03, ma5_t=100) == "續抱"


def test_short_swing_small_pullback_tolerance():
    assert short_swing_small_pullback_tolerance(close_t=99.5, ma5_t=100, is_volume_shrinking=True, ma20_slope=0.1) == "跌幅<1%且量縮，續抱觀察次日"
    assert short_swing_small_pullback_tolerance(close_t=98, ma5_t=100, is_volume_shrinking=True, ma20_slope=0.1) is None


def test_can_add_position():
    assert can_add_position(profit_pct=0.05, is_range_bound_or_small_dip=True, is_volume_up=True, is_red_k=True) is True
    assert can_add_position(profit_pct=0.12, is_range_bound_or_small_dip=True, is_volume_up=True, is_red_k=True) is False


def test_long_swing_exit_action_uses_ma20_not_ma5():
    assert long_swing_exit_action(close_t=94, stop_loss=95, has_lower_high=False, profit_pct=0.0, ma20_t=96) == "跌破停損，出場"
    assert long_swing_exit_action(close_t=108, stop_loss=95, has_lower_high=False, profit_pct=0.11, ma20_t=109) == "獲利≥10%跌破MA20，停利出場"
    assert long_swing_exit_action(close_t=125, stop_loss=95, has_lower_high=False, profit_pct=0.25, ma20_t=120, big_black_signal=True, close_below_prior_low=True) == "全部出場"


def test_long_swing_second_wave_check():
    assert long_swing_second_wave_check(cumulative_gain_from_launch_pct=1.1, entry_ready=True) == "累計漲幅達1倍，不再進行長線新進場/加碼"
    assert long_swing_second_wave_check(cumulative_gain_from_launch_pct=0.6, entry_ready=True) == "重新進場做長線第2波，重複第2～7條邏輯"
    assert long_swing_second_wave_check(cumulative_gain_from_launch_pct=0.3, entry_ready=True) is None


def test_surge_stock_hold_conditions_requires_all_four():
    assert surge_stock_hold_conditions(not_broke_uptrend_line=True, close_t=105, low_prev=100, low_prev2=98, ma3_t=103, is_red_or_flat=True) is True
    assert surge_stock_hold_conditions(not_broke_uptrend_line=False, close_t=105, low_prev=100, low_prev2=98, ma3_t=103, is_red_or_flat=True) is False


def test_surge_stock_volume_tier_action():
    assert surge_stock_volume_tier_action("無量飆漲") == "續抱"
    assert surge_stock_volume_tier_action("量放大價格大幅震盪") == "出脫二分之一"
    assert surge_stock_volume_tier_action("量大增開高走低收黑K") == "迅速賣出"
    with pytest.raises(ValueError):
        surge_stock_volume_tier_action("未知級距")


def test_bull_high_volume_no_rise_and_bear_high_volume_no_fall():
    assert bull_high_volume_no_rise(is_bull_trend=True, is_big_volume=True, price_change_pct=-0.01, is_black_candle=True) == "大量不漲，預期當日或後數日回檔"
    assert bull_high_volume_no_rise(is_bull_trend=False, is_big_volume=True, price_change_pct=-0.01, is_black_candle=True) is None
    assert bear_high_volume_no_fall(is_bear_trend=True, is_big_volume=True, price_change_pct=0.01, is_red_candle=True) == "大量不跌，預期當日或後數日反彈"


def test_good_news_no_rise_and_bad_news_no_fall():
    assert good_news_no_rise_distribution(is_at_bull_high=True, news_is_good=True, close_t=100, close_prev=101) == "利多不漲，疑似主力出貨"
    assert good_news_no_rise_distribution(is_at_bull_high=True, news_is_good=True, close_t=105, close_prev=101) is None
    assert bad_news_no_fall_accumulation(is_at_bear_low=True, news_is_bad=True, close_t=100, close_prev=99) == "利空不跌，疑似主力進場築底（鏡射推論，書中無獨立圖例）"


def test_bull_no_pullback_and_bear_no_rebound_strength():
    assert bull_no_pullback_strength(is_bull_trend=True, should_pullback=True, no_pullback_observed=True, close_after_window=110, prior_high=105) == "該回不回，過高要大漲，可續抱或加碼（非停利訊號）"
    assert bull_no_pullback_strength(is_bull_trend=True, should_pullback=False, no_pullback_observed=True, close_after_window=110, prior_high=105) is None
    assert bear_no_rebound_strength(is_bear_trend=True, should_rebound=True, no_rebound_observed=True, close_after_window=90, prior_low=95) == "該彈不彈，破低要大跌"


def test_reversal_signals():
    assert bull_to_bear_reversal_signal("多頭確認", "空頭確認") == "多頭完成反轉，預期初期出現大跌"
    assert bull_to_bear_reversal_signal("多頭確認", "多頭確認") is None
    assert bear_to_bull_reversal_signal("空頭確認", "多頭確認") == "空頭完成反轉，預期初期出現大漲"


def test_star_dominance_signal():
    assert star_dominance_signal(is_morning_star=True, is_evening_star=False) == "晨星反彈，多方主控"
    assert star_dominance_signal(is_morning_star=False, is_evening_star=True) == "夜星下跌，空方主控"
    assert star_dominance_signal(False, False) is None


def test_one_star_two_bull_and_bear_fakeout():
    assert one_star_two_bull_fakeout(is_one_star_two_bull_pattern=True, third_candle_is_red=True, third_candle_close=90, prior_support=95) == "一星二陽後跌破長紅，空頭確認，近日易大跌（騙線）"
    assert one_star_two_bull_fakeout(is_one_star_two_bull_pattern=True, third_candle_is_red=True, third_candle_close=100, prior_support=95) is None
    assert one_star_two_bear_fakeout(is_one_star_two_bear_pattern=True, third_candle_is_black=True, third_candle_close=110, prior_resistance=105) == "一星二陰後長黑突破壓力，近日易大漲（騙線）"


def test_resistance_high_volume_no_rise():
    assert resistance_high_volume_no_rise(close_t=99, prior_high=100, is_big_volume=True) == "關前爆量不漲，先回檔"
    assert resistance_high_volume_no_rise(close_t=90, prior_high=100, is_big_volume=True) is None  # 距離太遠


def test_long_consolidation_breakout_signal():
    assert long_consolidation_breakout_signal(consolidation_days=45, position="高檔", close_t=95, zone_upper=105, zone_lower=100) == "跌破久盤，預期大跌"
    assert long_consolidation_breakout_signal(consolidation_days=45, position="低檔", close_t=110, zone_upper=105, zone_lower=100) == "突破久盤，預期大漲"
    assert long_consolidation_breakout_signal(consolidation_days=20, position="高檔", close_t=95, zone_upper=105, zone_lower=100) is None
