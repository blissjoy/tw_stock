from src.screener.screening_rules import (
    base_consolidation_lock_and_breakout,
    bear_rebound_above_ma20_range_breakout,
    classify_limit_up_strength,
    classify_screening_mode,
    direction_by_trend_only,
    double_bottom_breakout_signal,
    double_bottom_no_new_low,
    excessive_rally_then_consolidation,
    frequent_big_volume_no_price_move,
    fundamental_data_completeness_check,
    institutional_sell_streak_or_black_k_cluster,
    intraday_consecutive_ask_side_large_trades,
    intraday_one_minute_surge,
    intraday_turns_red,
    is_daily_mover,
    limit_down_reopened,
    limit_up_reopened,
    liu_liu_da_shun_score,
    long_environment_check,
    ma_tangle_min_duration_lock,
    meets_capital_size_threshold,
    narrow_range_bottom_breakout,
    passes_initial_prefilter,
    repeated_big_black_at_resistance,
    second_wave_entry_signal,
    shortlist_top_n,
    short_environment_check,
    should_exclude_candidate,
    si_ji_shi_ji_score,
    slow_rally_attack_signal,
    slow_rally_channel_breakout,
    special_quote_prefilter,
)


def test_long_environment_check_requires_all_three():
    result = long_environment_check(True, True, True, has_hot_theme=True)
    assert result == {"做多環境成立": True, "題材加分": True}

    result2 = long_environment_check(True, True, False, has_hot_theme=False)
    assert result2["做多環境成立"] is False


def test_short_environment_check_mirrors_long():
    result = short_environment_check(True, True, True, is_priority_short_target=True)
    assert result == {"做空環境成立": True, "優先目標加分": True}


def test_liu_liu_da_shun_score():
    assert liu_liu_da_shun_score("盤整", True, True, True, True) == {"result": "退出觀察", "trend": "盤整"}

    candidate = liu_liu_da_shun_score("多頭", True, True, True, False)
    assert candidate["result"] == "候選"
    assert candidate["pass_count"] == 3

    watch = liu_liu_da_shun_score("多頭", True, False, False, False)
    assert watch["result"] == "觀察"
    assert watch["pass_count"] == 1


def test_narrow_range_bottom_breakout():
    assert narrow_range_bottom_breakout(duration_months=3, is_red_k=True, close=110, consolidation_upper=100, volume=250, range_avg_volume=100) is True
    assert narrow_range_bottom_breakout(duration_months=1, is_red_k=True, close=110, consolidation_upper=100, volume=250, range_avg_volume=100) is False
    assert narrow_range_bottom_breakout(duration_months=3, is_red_k=False, close=110, consolidation_upper=100, volume=250, range_avg_volume=100) is False


def test_slow_rally_channel_breakout():
    assert slow_rally_channel_breakout(close=110, channel_upper_value=105, is_long_red_k=True, volume=250, avg_volume_20=100) is True
    assert slow_rally_channel_breakout(close=100, channel_upper_value=105, is_long_red_k=True, volume=250, avg_volume_20=100) is False


def test_bear_rebound_above_ma20_range_breakout():
    assert bear_rebound_above_ma20_range_breakout(True, True, True, close=110, consolidation_upper=105, volume=250, range_avg_volume=100) is True
    assert bear_rebound_above_ma20_range_breakout(False, True, True, close=110, consolidation_upper=105, volume=250, range_avg_volume=100) is False


def test_slow_rally_attack_signal():
    assert slow_rally_attack_signal(bullish_aligned_line_count=3, is_gradual_uptrend=True, is_long_red_k=True, volume=350, avg_volume_20=100) is True
    assert slow_rally_attack_signal(bullish_aligned_line_count=2, is_gradual_uptrend=True, is_long_red_k=True, volume=350, avg_volume_20=100) is False


def test_classify_screening_mode():
    assert classify_screening_mode("投資") == "母體=台灣50/台灣中型100成分股，套用基本面查核清單"
    assert classify_screening_mode("投機") == "母體=中小型股，套用技術面選股法"


def test_direction_by_trend_only():
    assert direction_by_trend_only("多頭") == "允許做多，不因基本面差或股價創新高而排除"
    assert direction_by_trend_only("空頭") == "允許做空，不因股價已低而排除，也不因公司體質尚可而排除做空"
    assert direction_by_trend_only("盤整") == "盤整，暫不進場，等待趨勢明朗"


def test_fundamental_data_completeness_check():
    complete, missing = fundamental_data_completeness_check({
        "股本", "營業項目", "董事會結構", "營業額", "獲利率", "年增率", "淨值", "本益比", "ROE",
        "資產負債表", "損益表", "現金流量表", "歷年股利紀錄", "合理價值估算", "經營者相關揭露",
    })
    assert complete is True
    assert missing == []

    incomplete, missing2 = fundamental_data_completeness_check({"股本"})
    assert incomplete is False
    assert "營業項目" in missing2


def test_meets_capital_size_threshold():
    assert meets_capital_size_threshold(4_000_000_000) is True
    assert meets_capital_size_threshold(6_000_000_000) is False


def test_si_ji_shi_ji_score():
    result = si_ji_shi_ji_score(
        fund_checks={"股本": True, "營收成長": True, "法人籌碼": True, "產業熱度": False},
        tech_checks={"趨勢": True, "均線多排": True, "支撐壓力": True, "指標": True, "無背離": False},
    )
    assert result == {"基本面符合項數": 3, "技術面符合項數": 4, "建議進場": True}


def test_excessive_rally_then_consolidation():
    assert excessive_rally_then_consolidation(swing_gain_pct=1.2, is_consolidation=True) is True
    assert excessive_rally_then_consolidation(swing_gain_pct=0.5, is_consolidation=True) is False


def test_repeated_big_black_at_resistance():
    assert repeated_big_black_at_resistance(2) is True
    assert repeated_big_black_at_resistance(1) is False


def test_institutional_sell_streak_or_black_k_cluster():
    assert institutional_sell_streak_or_black_k_cluster(3, 0) is True
    assert institutional_sell_streak_or_black_k_cluster(0, 3) is True
    assert institutional_sell_streak_or_black_k_cluster(1, 1) is False


def test_frequent_big_volume_no_price_move():
    assert frequent_big_volume_no_price_move(big_volume_day_count=3, price_change_pct=0.01) is True
    assert frequent_big_volume_no_price_move(big_volume_day_count=2, price_change_pct=0.01) is False


def test_should_exclude_candidate():
    excluded, reasons = should_exclude_candidate({"沒有量能": True, "看不懂": False})
    assert excluded is True
    assert reasons == ["沒有量能"]
    not_excluded, reasons2 = should_exclude_candidate({"沒有量能": False})
    assert not_excluded is False
    assert reasons2 == []


def test_special_quote_prefilter():
    assert special_quote_prefilter(volume=1500, close_price=10) is True
    assert special_quote_prefilter(volume=500, close_price=10) is False
    assert special_quote_prefilter(volume=1500, close_price=3) is False


def test_intraday_signals():
    assert intraday_one_minute_surge(price_now=102, price_1min_ago=100) is True
    assert intraday_one_minute_surge(price_now=101, price_1min_ago=100) is False
    assert intraday_consecutive_ask_side_large_trades(3) is True
    assert intraday_consecutive_ask_side_large_trades(2) is False
    assert intraday_turns_red(current_price=101, prev_close=100, open_price=99) is True
    assert intraday_turns_red(current_price=99, prev_close=100, open_price=99) is False
    assert limit_up_reopened(was_limit_up_yesterday=True, current_price=108, limit_up_price=110) is True
    assert limit_down_reopened(was_limit_down_yesterday=True, current_price=92, limit_down_price=90) is True


def test_base_consolidation_lock_and_breakout():
    assert base_consolidation_lock_and_breakout(broke_prior_low=True, in_range_consolidation=True, big_red_breakout=True, bullish_aligned_line_count=4) is None
    assert base_consolidation_lock_and_breakout(False, True, False, 4) == "鎖股（底部盤整），等待大量紅K突破"
    assert base_consolidation_lock_and_breakout(False, True, True, 4) == "大量紅K突破底部盤整，4線多排向上，規劃做長多"
    assert base_consolidation_lock_and_breakout(False, True, True, 3) == "大量紅K突破底部盤整，3線多排向上，開始做短多"


def test_ma_tangle_min_duration_lock():
    assert ma_tangle_min_duration_lock(tangle_duration_months=1.0, big_red_breakout=True) is None
    assert ma_tangle_min_duration_lock(tangle_duration_months=2.5, big_red_breakout=False) == "鎖股（均線糾結已達2個月），等待突破"
    assert ma_tangle_min_duration_lock(tangle_duration_months=2.5, big_red_breakout=True) == "大量紅K突破盤整區，短多或長多"


def test_second_wave_entry_signal():
    assert second_wave_entry_signal(wave1_confirmed=False, pullback_detected=True, is_attack_volume=True, close_up=True) is None
    assert second_wave_entry_signal(True, True, True, True) == "第2波進場"
    assert second_wave_entry_signal(True, True, False, True) == "等待第2波確認"


def test_double_bottom_no_new_low_and_breakout_signal():
    assert double_bottom_no_new_low(bottom1_low=13.10, bottom2_low=13.50) is True
    assert double_bottom_no_new_low(bottom1_low=13.10, bottom2_low=12.90) is False

    assert double_bottom_breakout_signal(no_new_low=True, is_red_k=True, close=20, resistance=18, volume=300, range_avg_volume=100) is True
    assert double_bottom_breakout_signal(no_new_low=False, is_red_k=True, close=20, resistance=18, volume=300, range_avg_volume=100) is False


def test_is_daily_mover_and_passes_initial_prefilter():
    assert is_daily_mover(0.04) is True
    assert is_daily_mover(0.02) is False
    assert passes_initial_prefilter(is_consolidation=False, volume=1500, close_price=10) is True
    assert passes_initial_prefilter(is_consolidation=True, volume=1500, close_price=10) is False
    assert passes_initial_prefilter(is_consolidation=False, volume=500, close_price=10) is False


def test_classify_limit_up_strength():
    assert classify_limit_up_strength("09:05") == "最強/次強"
    assert classify_limit_up_strength("09:25") == "強"
    assert classify_limit_up_strength("13:25") == "待次日驗證"
    assert classify_limit_up_strength(None, is_limit_down_at_open=True) == "最弱"
    assert classify_limit_up_strength(None) is None


def test_shortlist_top_n():
    assert shortlist_top_n(["a", "b", "c", "d", "e", "f"], n=5) == ["a", "b", "c", "d", "e"]
    assert shortlist_top_n(["a", "b"], n=5) == ["a", "b"]
