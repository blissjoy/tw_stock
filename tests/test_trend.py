import pandas as pd

from src.indicators.trend import (
    base_building_leg1_signal,
    base_building_leg2_confirmed,
    base_building_ma_lock_signal,
    bear_confirm_short_timing,
    bear_consolidation_breakdown_signal,
    bear_rebound_short_signal,
    bear_short_term_entry_ready,
    bear_short_term_exit_action,
    bear_short_term_stop_loss,
    bear_trend_change_warning,
    bull_consolidation_breakout_signal,
    bull_high_volume_exhaustion_signal,
    bull_pullback_buy_signal,
    bull_short_term_entry_ready,
    bull_short_term_exit_action,
    bull_short_term_stop_loss,
    bull_trend_change_warning,
    classify_consolidation_shape,
    classify_pullback_correction,
    consolidation_trading_direction,
    is_bear_trend,
    is_bull_trend,
    is_correction_classification_applicable,
    is_range_worth_trading,
    k_line_range_simplify,
    long_entry_taboo_check,
    mid_wave_simplify,
    one_day_reversal_high,
    short_entry_taboo_check,
    topping_leg1_signal,
    topping_leg2_confirmed,
    topping_ma_lock_signal,
    trend_wave_pivot,
)


def test_is_bull_trend_requires_both_head_and_bottom_to_rise():
    # R-TREND-03：頭頭高 且 底底高，兩個條件缺一不可
    assert is_bull_trend(heads=[10, 12], bottoms=[5, 7]) is True
    assert is_bull_trend(heads=[12, 10], bottoms=[5, 7]) is False  # 頭沒有更高
    assert is_bull_trend(heads=[10, 12], bottoms=[7, 5]) is False  # 底沒有更高
    assert is_bull_trend(heads=[10], bottoms=[5, 7]) is False      # 頭的資料不足2組


def test_is_bear_trend_requires_both_head_and_bottom_to_fall():
    # R-TREND-04：頭頭低 且 底底低，與多頭判定鏡射對稱
    assert is_bear_trend(heads=[12, 10], bottoms=[7, 5]) is True
    assert is_bear_trend(heads=[10, 12], bottoms=[7, 5]) is False
    assert is_bear_trend(heads=[12, 10], bottoms=[5, 7]) is False


def test_bull_trend_change_warning_fires_on_lower_low_or_lower_high():
    # R-TREND-08：多頭確認後，出現「底底低」或「頭頭低」任一種即示警
    assert bull_trend_change_warning([10, 12], [5, 3], bull_trend_previously_confirmed=True) is not None
    assert "底底低" in bull_trend_change_warning([10, 12], [5, 3], True)

    assert bull_trend_change_warning([12, 10], [5, 7], bull_trend_previously_confirmed=True) is not None
    assert "頭頭低" in bull_trend_change_warning([12, 10], [5, 7], True)

    # 多頭架構仍維持 -> 無警訊
    assert bull_trend_change_warning([10, 12], [5, 7], bull_trend_previously_confirmed=True) is None

    # 尚未先前確認過多頭 -> 不評估
    assert bull_trend_change_warning([10, 12], [5, 3], bull_trend_previously_confirmed=False) is None


def test_bear_trend_change_warning_fires_on_higher_high_or_higher_low():
    # R-TREND-09：空頭確認後，出現「頭頭高」或「底底高」任一種即示警
    assert bear_trend_change_warning([10, 12], [7, 5], bear_trend_previously_confirmed=True) is not None
    assert "頭頭高" in bear_trend_change_warning([10, 12], [7, 5], True)

    assert bear_trend_change_warning([12, 10], [3, 5], bear_trend_previously_confirmed=True) is not None
    assert "底底高" in bear_trend_change_warning([12, 10], [3, 5], True)

    assert bear_trend_change_warning([12, 10], [7, 5], bear_trend_previously_confirmed=True) is None
    assert bear_trend_change_warning([10, 12], [7, 5], bear_trend_previously_confirmed=False) is None


def test_bull_high_volume_exhaustion_scenario1_single_day_huge_volume_long_black():
    # R-TREND-12 情境①：單日量 >= 前一日量3倍 + 當天長黑K + 位於多頭高檔
    volume = pd.Series([1000, 1000, 3500])
    open_ = pd.Series([50, 50, 55])
    close = pd.Series([50, 51, 50])   # 第3天收黑(50<55)
    volume_ma5 = pd.Series([1000, 1000, 1000])
    is_at_bull_high = pd.Series([False, False, True])

    signal = bull_high_volume_exhaustion_signal(volume, open_, close, volume_ma5, is_at_bull_high)
    assert signal.tolist() == [False, False, True]


def test_bull_high_volume_exhaustion_scenario2_consecutive_big_volume_no_rise():
    # R-TREND-12 情境②：近3天內至少2天量>=5日均量2倍，且股價不漲或下跌
    volume = pd.Series([1000, 2200, 2200, 2200])
    open_ = pd.Series([50, 50, 50, 50])
    close = pd.Series([50, 51, 51, 50.5])  # 逐日收盤未再創高，最後一天下跌
    volume_ma5 = pd.Series([1000, 1000, 1000, 1000])
    is_at_bull_high = pd.Series([True, True, True, True])

    signal = bull_high_volume_exhaustion_signal(volume, open_, close, volume_ma5, is_at_bull_high)
    # 第4天：近3天(index1,2,3)有3天爆量(>=2倍)，達到min_hits=2門檻，且收盤(50.5)<=前一天(51)
    assert signal.iloc[3] == True


def test_bull_high_volume_exhaustion_ignored_outside_bull_high_position():
    volume = pd.Series([1000, 1000, 3500])
    open_ = pd.Series([50, 50, 55])
    close = pd.Series([50, 51, 50])
    volume_ma5 = pd.Series([1000, 1000, 1000])
    is_at_bull_high = pd.Series([False, False, False])  # 不在多頭高檔，即使數字條件符合也不觸發

    signal = bull_high_volume_exhaustion_signal(volume, open_, close, volume_ma5, is_at_bull_high)
    assert not signal.any()


def test_bull_pullback_buy_signal():
    # R-TREND-06訊號1：回後買上漲
    assert bull_pullback_buy_signal(True, True, open_t=100, close_t=103, ma5_t=101, high_prev=102, volume_t=200, volume_prev=150) is True
    assert bull_pullback_buy_signal(False, True, open_t=100, close_t=103, ma5_t=101, high_prev=102, volume_t=200, volume_prev=150) is False


def test_bull_consolidation_breakout_signal():
    # R-TREND-06訊號2：盤整的突破，需帶量(前均量1.3倍以上)
    assert bull_consolidation_breakout_signal(True, True, open_t=100, close_t=103, upper_neckline=102, volume_t=200, avg_volume_prev=150) is True
    assert bull_consolidation_breakout_signal(True, True, open_t=100, close_t=103, upper_neckline=102, volume_t=190, avg_volume_prev=150) is False


def test_bear_rebound_short_signal_mirrors_bull():
    # R-TREND-07訊號1：彈後空下跌
    assert bear_rebound_short_signal(True, True, open_t=100, close_t=97, ma5_t=98, low_prev=98.5, volume_t=200, volume_prev=150) is True


def test_bear_consolidation_breakdown_signal_mirrors_bull():
    # R-TREND-07訊號2：盤整的跌破
    assert bear_consolidation_breakdown_signal(True, True, open_t=100, close_t=97, lower_neckline=98, volume_t=200, avg_volume_prev=150) is True


def test_is_range_worth_trading_and_trading_direction():
    # R-TREND-05: 盤整寬度須達15%才適合區間內進出
    assert is_range_worth_trading(upper_neckline=115, lower_neckline=100) is True
    assert is_range_worth_trading(upper_neckline=110, lower_neckline=100) is False

    assert consolidation_trading_direction("多頭") == "先買（近下頸線）後賣（近上頸線）"
    assert consolidation_trading_direction("空頭") == "先賣（近上頸線）後買（近下頸線）"
    assert consolidation_trading_direction("盤整") is None


def test_classify_consolidation_shape_four_types():
    assert classify_consolidation_shape(upper_slope=-0.5, lower_slope=0.5, price_level=100) == "三角收斂"
    assert classify_consolidation_shape(upper_slope=0.05, lower_slope=-0.05, price_level=100) == "矩形"
    assert classify_consolidation_shape(upper_slope=0.05, lower_slope=0.5, price_level=100) == "上升直角三角"
    assert classify_consolidation_shape(upper_slope=-0.5, lower_slope=0.05, price_level=100) == "下降直角三角"


def test_bear_confirm_short_timing():
    # R-TREND-13：已跌破下彎月線但未跌破季線可做短空，否則等下次彈後空下跌訊號
    assert bear_confirm_short_timing(True, True, True, close_t=95, ma20_t=100, ma20_slope=-0.5, ma60_t=90) == "立刻賣出多單；可做短空"
    assert bear_confirm_short_timing(True, True, True, close_t=102, ma20_t=100, ma20_slope=-0.5, ma60_t=90) == "立刻賣出多單；尚未跌破月線，等待下一次彈後空下跌訊號再進場"
    assert bear_confirm_short_timing(False, True, True, close_t=95, ma20_t=100, ma20_slope=-0.5, ma60_t=90) is None


def test_mid_wave_simplify():
    # R-TREND-02：反轉只取代表性1點，續勢盤整取高低點各1組
    assert mid_wave_simplify(zone_high=110, zone_low=100, reversed_from_prior_trend=True, prior_trend="多頭") == [110]
    assert mid_wave_simplify(zone_high=110, zone_low=100, reversed_from_prior_trend=True, prior_trend="空頭") == [100]
    assert mid_wave_simplify(zone_high=110, zone_low=100, reversed_from_prior_trend=False, prior_trend="多頭") == [110, 100]


def test_k_line_range_simplify():
    # R-TREND-02：MA5上下頻繁穿梭時只取1個代表性轉折點
    assert k_line_range_simplify(is_whipsaw_around_ma5=False, reversed_upward=True, representative_high=110, representative_low=100) is None
    assert k_line_range_simplify(True, reversed_upward=True, representative_high=110, representative_low=100) == 100
    assert k_line_range_simplify(True, reversed_upward=False, representative_high=110, representative_low=100) == 110


def test_trend_wave_pivot():
    # R-TREND-02：只在多頭破前低/空頭破前高的關鍵事件才取轉折點
    assert trend_wave_pivot("多頭", close_t=95, prior_confirmed_low=100, prior_confirmed_high=120) == "多頭回檔跌破前低，取前一個轉折高點為頭部轉折點"
    assert trend_wave_pivot("空頭", close_t=125, prior_confirmed_low=100, prior_confirmed_high=120) == "空頭反彈突破前高，取前一個轉折低點為底部轉折點"
    assert trend_wave_pivot("多頭", close_t=105, prior_confirmed_low=100, prior_confirmed_high=120) is None


def test_base_building_leg1_and_leg2_and_ma_lock():
    # R-TREND-10：第1支腳
    assert base_building_leg1_signal(leg1_low=90, pullback_low=92, rebound_broke_ma20_and_prior_high=False) == "立刻回補空單，留意反轉"
    assert base_building_leg1_signal(leg1_low=90, pullback_low=88, rebound_broke_ma20_and_prior_high=True) == "趨勢改變，不宜再做空"
    assert base_building_leg1_signal(leg1_low=90, pullback_low=88, rebound_broke_ma20_and_prior_high=False) is None

    # 第2支腳(黃金右腳)
    assert base_building_leg2_confirmed(leg1_low=90, leg2_low=91, close_t=105, resistance=100) is True
    assert base_building_leg2_confirmed(leg1_low=90, leg2_low=89, close_t=105, resistance=100) is False

    # 均線鎖股
    assert base_building_ma_lock_signal(ma10=21, ma10_slope=0.1, ma20=20, ma20_slope=0.1, close_t=25, ma60_t=22) == "鎖股準備做短中長多"
    assert base_building_ma_lock_signal(ma10=21, ma10_slope=0.1, ma20=20, ma20_slope=0.1, close_t=21.5, ma60_t=22) == "打底接近完成，鎖股準備做短多"
    assert base_building_ma_lock_signal(ma10=19, ma10_slope=-0.1, ma20=20, ma20_slope=0.1, close_t=21.5, ma60_t=22) is None


def test_topping_leg1_and_leg2_and_ma_lock_mirrors_base_building():
    # R-TREND-11：與R-TREND-10鏡射對稱
    assert topping_leg1_signal(leg1_high=110, rebound_high=108, breakdown_ma20_and_prior_low=False) == "立刻出場，留意反轉"
    assert topping_leg1_signal(leg1_high=110, rebound_high=112, breakdown_ma20_and_prior_low=True) == "趨勢改變，不宜再做多"
    assert topping_leg1_signal(leg1_high=110, rebound_high=112, breakdown_ma20_and_prior_low=False) is None

    assert topping_leg2_confirmed(leg1_high=110, leg2_high=109, close_t=95, support=100) is True
    assert topping_leg2_confirmed(leg1_high=110, leg2_high=111, close_t=95, support=100) is False

    assert topping_ma_lock_signal(ma10=19, ma10_slope=-0.1, ma20=20, ma20_slope=-0.1, close_t=17, ma60_t=18) == "均線4線空排，鎖股準備做短中長空"
    assert topping_ma_lock_signal(ma10=19, ma10_slope=-0.1, ma20=20, ma20_slope=-0.1, close_t=19.5, ma60_t=18) == "做頭接近完成，鎖股準備做短空"


def test_one_day_reversal_high():
    # R-TREND-11：一日反轉，次日跌破當日最低點
    assert one_day_reversal_high(day_low=100, next_day_low=98) is True
    assert one_day_reversal_high(day_low=100, next_day_low=101) is False


def test_bull_short_term_entry_ready_and_stop_loss_and_exit():
    # R-TREND-14：多頭短線6要件與停損停利SOP
    assert bull_short_term_entry_ready(
        is_bull_trend=True, ma10=21, ma20=20, ma10_slope=0.1, ma20_slope=0.1,
        close_t=22, open_t=21.5, volume_t=1300, volume_prev=1000,
    ) is True  # 漲幅=(22-21.5)/21.5≈2.33%>2%
    assert bull_short_term_entry_ready(
        is_bull_trend=True, ma10=21, ma20=20, ma10_slope=0.1, ma20_slope=0.1,
        close_t=21.6, open_t=21.5, volume_t=1300, volume_prev=1000,
    ) is False  # 漲幅不足2%

    assert bull_short_term_stop_loss(entry_bar_low=100, stop_pct=0.05) == 95
    assert bull_short_term_stop_loss(entry_bar_low=100, stop_pct=0.10) == 93  # 夾回7%上限

    assert bull_short_term_exit_action(close_t=94, stop_loss=95, has_lower_high=False, profit_pct=0.0, ma5_t=96) == "跌破停損，出場"
    assert bull_short_term_exit_action(close_t=96, stop_loss=95, has_lower_high=True, profit_pct=0.0, ma5_t=96) == "收盤出現頭頭低，出場"
    assert bull_short_term_exit_action(close_t=125, stop_loss=95, has_lower_high=False, profit_pct=0.25, ma5_t=120) == "獲利超過20%或連續急漲後大量長黑K強覆蓋/吞噬，當天出場"
    assert bull_short_term_exit_action(close_t=108, stop_loss=95, has_lower_high=False, profit_pct=0.11, ma5_t=109) == "獲利超過10%且跌破MA5，出場"
    assert bull_short_term_exit_action(close_t=108, stop_loss=95, has_lower_high=False, profit_pct=0.03, ma5_t=100) == "續抱"


def test_bear_short_term_entry_ready_and_stop_loss_and_exit_mirrors_bull():
    # R-TREND-15：與R-TREND-14鏡射對稱
    assert bear_short_term_entry_ready(
        is_bear_trend=True, ma10=19, ma20=20, ma10_slope=-0.1, ma20_slope=-0.1,
        close_t=18, open_t=18.5, volume_t=1300, volume_prev=1000,
        ma5_t=18.5, low_prev=18.2,
    ) is True  # 跌幅=(18.5-18)/18.5≈2.7%>2%，且跌破MA5與前一日低點

    assert bear_short_term_stop_loss(entry_bar_high=100, stop_pct=0.05) == 105
    assert bear_short_term_stop_loss(entry_bar_high=100, stop_pct=0.10) == 107  # 夾回7%上限

    assert bear_short_term_exit_action(close_t=106, stop_loss=105, has_higher_low=False, profit_pct=0.0, ma5_t=104) == "突破停損，回補"
    assert bear_short_term_exit_action(close_t=104, stop_loss=105, has_higher_low=True, profit_pct=0.0, ma5_t=104) == "收盤出現底底高，回補"
    assert bear_short_term_exit_action(close_t=75, stop_loss=105, has_higher_low=False, profit_pct=0.25, ma5_t=80) == "獲利超過20%或連續急跌後大量長紅K強覆蓋/吞噬，當天回補"
    assert bear_short_term_exit_action(close_t=90, stop_loss=105, has_higher_low=False, profit_pct=0.12, ma5_t=89) == "獲利超過10%且突破MA5，回補"


def test_long_entry_taboo_check():
    # R-TREND-16：命中任一戒律即不可進場
    can_enter, reasons = long_entry_taboo_check(
        base_not_above_ma60=False, third_or_later_up_bar=True, divergence_overheated=False,
        weekly_resistance_nearby=False, pulled_back_below_ma20_not_reclaimed=False,
        broke_prior_low_then_rallied=False, is_consolidation=False, is_bear_rebound=False,
        rapid_rally_high_volume_at_high=False, price_up_but_black_candle=False,
    )
    assert can_enter is False
    assert reasons == ["追高風險（上漲第3根以上）"]

    can_enter_clean, reasons_clean = long_entry_taboo_check(
        False, False, False, False, False, False, False, False, False, False,
    )
    assert can_enter_clean is True
    assert reasons_clean == []


def test_short_entry_taboo_check_mirrors_long():
    # R-TREND-17：與R-TREND-16鏡射對稱
    can_enter, reasons = short_entry_taboo_check(
        top_not_below_ma60=False, third_or_later_down_bar=False, divergence_overheated=False,
        weekly_support_nearby=True, rebounded_above_ma20_not_broken=False,
        broke_prior_high_then_declined=False, is_consolidation=False, is_bull_pullback=False,
        rapid_decline_high_volume_at_low=False, price_down_but_red_candle=False,
    )
    assert can_enter is False
    assert reasons == ["週線支撐"]


def test_is_correction_classification_applicable():
    # R-TREND-18：修正分類僅在波段幅度達10%以上才適用
    assert is_correction_classification_applicable(0.12) is True
    assert is_correction_classification_applicable(0.08) is False
    assert is_correction_classification_applicable(-0.15) is True


def test_classify_pullback_correction_four_scenarios():
    assert classify_pullback_correction(pullback_pct=0.3, broke_ma20_or_prior_extreme=False, is_consolidation_breakout=False, is_abc_correction_resolved=False) == "情況1 弱勢回檔：回後買上漲/彈後空下跌，原趨勢繼續"
    assert classify_pullback_correction(pullback_pct=0.6, broke_ma20_or_prior_extreme=True, is_consolidation_breakout=False, is_abc_correction_resolved=False) == "情況2 強勢回檔：容易進入頭頭低/底底高盤整，需等重新符合原趨勢架構再進場"
    assert classify_pullback_correction(pullback_pct=0.6, broke_ma20_or_prior_extreme=False, is_consolidation_breakout=True, is_abc_correction_resolved=False) == "情況3 盤整突破：原趨勢繼續"
    assert classify_pullback_correction(pullback_pct=0.6, broke_ma20_or_prior_extreme=False, is_consolidation_breakout=False, is_abc_correction_resolved=True) == "情況4 ABC修正結束：原趨勢繼續"
