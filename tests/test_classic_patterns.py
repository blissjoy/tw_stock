from src.patterns.classic_patterns import (
    bear_rebound_consolidate_above_ma20_breakout,
    bear_to_bull_break_rebound_high,
    big_black_breaks_uptrend_line,
    black_red_black_decline,
    bottom_wash_out_then_breakout,
    break_above_two_day_low_volume_high,
    break_abc_correction_downtrend,
    break_abc_correction_uptrend,
    break_below_down_channel,
    break_below_two_day_high_volume_low,
    break_downtrend_line_then_new_high,
    breakout_above_big_black_candle,
    breakout_above_up_channel,
    breakout_prior_high_then_big_black_fakeout,
    bull_to_bear_break_last_low,
    chase_short_on_bounce_break,
    double_arc_bottom_breakout,
    double_bottom_platform_breakout,
    double_leg_bottom_breakout,
    double_top_neckline_break,
    gap_down_black_reversal_at_high,
    gap_down_continuation,
    gap_up_continuation,
    gradual_rally_breakout,
    high_zone_long_upper_shadow_reversal,
    island_reversal,
    low_zone_big_lower_shadow_reversal,
    low_zone_big_red_confirmation,
    ma_tangle_breakout,
    one_day_reversal_at_high,
    red_black_red_rally,
    resistance_big_black_immediate_exit,
    three_day_upper_shadow_distribution,
)


def test_one_day_reversal_at_high():
    assert one_day_reversal_at_high(True, True, True) == "先賣出持股二分之一"
    assert one_day_reversal_at_high(True, True, True, next_close=95, bar_low=98) == "剩餘部位全數賣出，確認一日反轉"
    assert one_day_reversal_at_high(False, True, True) is None


def test_big_black_breaks_uptrend_line():
    assert big_black_breaks_uptrend_line(True, True, True) == "空頭確認：上升切線轉壓力"
    assert big_black_breaks_uptrend_line(False, True, True) is None


def test_double_top_neckline_break():
    assert double_top_neckline_break(True, True, True) == "M頭頸線跌破，空頭確認"
    assert double_top_neckline_break(True, False, True) is None


def test_break_below_two_day_high_volume_low():
    assert break_below_two_day_high_volume_low(True, True, day1_low=100, day2_low=95, later_close=90, later_is_black=True) == "跌破高檔連2日大量低點，一日反轉停利"
    assert break_below_two_day_high_volume_low(True, False, 100, 95, 90, True) is None
    assert break_below_two_day_high_volume_low(True, True, 100, 95, later_close=96, later_is_black=True) is None


def test_gap_down_black_reversal_at_high():
    assert gap_down_black_reversal_at_high(True, True, True, True, kd_bearish_cross=True) == "高檔跳空黑K回檔反轉，空頭確認，KD同步走弱，確認強化"
    assert gap_down_black_reversal_at_high(True, True, True, True) == "高檔跳空黑K回檔反轉，空頭確認"
    assert gap_down_black_reversal_at_high(True, True, True, False) is None


def test_chase_short_on_bounce_break():
    assert chase_short_on_bounce_break(True, True, bounce_low=100, next_close=95) == "跌破反彈紅K低點，追空點確認"
    assert chase_short_on_bounce_break(True, True, bounce_low=100, next_close=105) is None
    assert chase_short_on_bounce_break(False, True, bounce_low=100, next_close=95) is None


def test_gap_down_continuation():
    assert gap_down_continuation(True, True, True) == "缺口下再破底，續空/加碼放空點"
    result = gap_down_continuation(True, True, True, ma20_broken_before_gap=True, island_reversal_detected=True)
    assert "月線先跌破" in result and "反轉循環" in result
    assert gap_down_continuation(True, False, True) is None


def test_bull_to_bear_break_last_low():
    assert bull_to_bear_break_last_low(close=90, last_bull_low=95, is_large_volume=True) == "跌破多頭最後低點，多頭趨勢終結，快速下跌警訊"
    assert bull_to_bear_break_last_low(close=96, last_bull_low=95, is_large_volume=True) is None


def test_break_below_down_channel():
    assert break_below_down_channel(close=90, channel_value=95, is_big_black=True) == "支撐轉壓力，跌勢由緩降轉為急跌"
    assert break_below_down_channel(close=96, channel_value=95, is_big_black=True) is None


def test_low_zone_big_red_confirmation():
    assert low_zone_big_red_confirmation(2) == "低檔大量長紅確認打底"
    assert low_zone_big_red_confirmation(1) is None


def test_break_downtrend_line_then_new_high():
    assert break_downtrend_line_then_new_high(True, True, True, True) == "破切＋過高＋爆量，多頭確認"
    assert break_downtrend_line_then_new_high(True, True, False, True) is None


def test_double_leg_bottom_breakout():
    result = double_leg_bottom_breakout(True, True, True, leg2_low=90, neckline=100)
    assert result["signal"] == "雙腳打底突破頸線，多頭確認"
    assert result["D"] == 10
    assert double_leg_bottom_breakout(True, False, True) is None


def test_gap_up_continuation():
    assert gap_up_continuation(True, True, True, True) == "缺口之上出現大量買點，續漲確認"
    assert gap_up_continuation(True, False, True, True) is None


def test_gradual_rally_breakout():
    assert gradual_rally_breakout(True, True, True, True) == "碎步緩漲後大量長紅突破，攻擊買進點"
    assert gradual_rally_breakout(True, True, True, False) is None


def test_bear_to_bull_break_rebound_high():
    assert bear_to_bull_break_rebound_high(True, True, close=110, bear_rebound_high=105, is_large_volume=True) == "突破空頭反彈高點，趨勢空轉多確認"
    assert bear_to_bull_break_rebound_high(True, True, close=100, bear_rebound_high=105, is_large_volume=True) is None


def test_breakout_above_big_black_candle():
    assert breakout_above_big_black_candle(True, True, True) == "突破大量黑K高點，非轉空、為續漲買進訊號"
    assert breakout_above_big_black_candle(True, False, True) is None


def test_break_above_two_day_low_volume_high():
    assert break_above_two_day_low_volume_high(True, True, day1_high=100, day2_high=105, later_close=110, breakout_volume_big=True) == "突破低檔連2日大量高點，一日反轉轉強"
    assert break_above_two_day_low_volume_high(True, True, 100, 105, later_close=102, breakout_volume_big=True) is None


def test_low_zone_big_lower_shadow_reversal():
    assert low_zone_big_lower_shadow_reversal(True, True) == "低檔大量長下影線，一日反轉買進候選"
    strong = low_zone_big_lower_shadow_reversal(True, True, above_ma20_or_morning_star=True)
    assert "多重確認強化" in strong
    assert low_zone_big_lower_shadow_reversal(False, True) is None


def test_bear_rebound_consolidate_above_ma20_breakout():
    assert bear_rebound_consolidate_above_ma20_breakout(True, True) == "反彈站穩月線盤整後突破買點"
    assert bear_rebound_consolidate_above_ma20_breakout(True, False) is None


def test_delegated_patterns():
    assert double_bottom_platform_breakout(True) == "雙盤底大量紅K突破進場"
    assert double_bottom_platform_breakout(False) is None
    assert ma_tangle_breakout(True) == "均線糾結轉多頭排列，強力多頭起漲訊號"
    assert ma_tangle_breakout(False) is None
    assert island_reversal(True) == "島型反轉，強烈低檔反轉訊號"
    assert island_reversal(False) is None


def test_breakout_above_up_channel():
    assert breakout_above_up_channel(close=110, channel_value=105, is_big_red=True) == "漲勢自緩步盤堅轉為加速噴出，全書最強力多頭訊號"
    assert breakout_above_up_channel(close=100, channel_value=105, is_big_red=True) is None


def test_resistance_big_black_immediate_exit():
    assert resistance_big_black_immediate_exit(True, True) == "遇壓爆量長黑，立即停利，不必等待更多確認"
    assert "空頭確認強化" in resistance_big_black_immediate_exit(True, True, is_lower_high_confirmed=True)
    assert resistance_big_black_immediate_exit(False, True) is None


def test_double_arc_bottom_breakout():
    assert double_arc_bottom_breakout(True, True) == "雙弧底突破，套用雙盤底大量紅K突破進場核心邏輯（底部形狀參數＝圓弧）"
    assert double_arc_bottom_breakout(False, True) is None


def test_high_zone_long_upper_shadow_reversal():
    # R-CLASSIC-06: 高檔爆大量長上影線，先賣一半；隔日跌破才賣剩餘
    assert high_zone_long_upper_shadow_reversal(True, True) == "先賣出持股二分之一"
    assert high_zone_long_upper_shadow_reversal(True, True, next_close_below_bar_low=True) == "賣出剩餘部位，確認一日反轉"
    assert high_zone_long_upper_shadow_reversal(False, True) is None


def test_three_day_upper_shadow_distribution():
    # R-CLASSIC-08: 直通R-CANDLE-36的連3日長上影判定
    assert three_day_upper_shadow_distribution(True) == "連3日長上影，大敵當前，主力出貨警訊"
    assert three_day_upper_shadow_distribution(True, bear_confirmed=True) == "連3日長上影，大敵當前，主力出貨警訊，空頭確認"
    assert three_day_upper_shadow_distribution(False) is None


def test_breakout_prior_high_then_big_black_fakeout():
    # R-CLASSIC-10: 突破前高後爆量長黑吞噬且收盤跌破前高，假突破空頭確認
    assert breakout_prior_high_then_big_black_fakeout(True, True, True) == "假突破：前高支撐測試失敗、反轉為壓力，空頭確認"
    assert breakout_prior_high_then_big_black_fakeout(True, True, False) is None
    assert breakout_prior_high_then_big_black_fakeout(False, True, True) is None


def test_black_red_black_decline():
    # R-CLASSIC-11: 直通R-CANDLE-37的黑紅黑空方夾擊判定
    assert black_red_black_decline(True) == "黑紅黑空方夾擊，續跌確認"
    assert black_red_black_decline(False) is None


def test_break_abc_correction_downtrend():
    # R-CLASSIC-14: 跌破ABC修正低點與上升切線，等幅測量法估算目標價
    result = break_abc_correction_downtrend(close_below_uptrend_line=True, close_below_c_low=True, c_low=90.0, prior_rally_d=20.0)
    assert result == "跌破ABC修正低點與上升切線，空頭確認，目標價=70.0"
    assert break_abc_correction_downtrend(False, True, 90.0, 20.0) is None


def test_bottom_wash_out_then_breakout():
    # R-CLASSIC-21: 假跌破洗盤+快速拉回，才進一步判斷是否突破前高
    assert bottom_wash_out_then_breakout(True, True, True, True, True) == "洗盤後突破前高，攻擊買進"
    assert bottom_wash_out_then_breakout(True, True, True, False, False) == "假跌破洗盤（非真跌破），持續觀察是否突破前高"
    assert bottom_wash_out_then_breakout(False, True, True, True, True) is None


def test_red_black_red_rally():
    # R-CLASSIC-23: 直通R-CANDLE-37的紅黑紅多方夾擊判定
    assert red_black_red_rally(True) == "紅黑紅多方夾擊，續漲訊號（非反轉警訊）"
    assert red_black_red_rally(False) is None


def test_break_abc_correction_uptrend():
    # R-CLASSIC-31: 突破ABC修正下降切線與A點高點，等幅測量法估算目標價
    result = break_abc_correction_uptrend(close_above_downtrend_line=True, close_above_a_high=True, a_high=100.0, ab_range_d=15.0)
    assert result == "突破ABC修正下降切線與A點高點，多頭續漲確認，目標價=115.0"
    assert break_abc_correction_uptrend(False, True, 100.0, 15.0) is None
