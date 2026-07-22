from src.indicators.gaps import (
    Gap,
    build_down_gap_tiers,
    build_up_gap_tiers,
    classify_common_gap_in_range,
    classify_gap_cause,
    classify_gap_long_black_consolidation,
    classify_gap_long_red_consolidation,
    classify_island_reversal_bottom_subtype,
    detect_3day_2gap_down,
    detect_3day_2gap_up,
    detect_breakaway_gap_down,
    detect_breakaway_gap_up,
    detect_crossfire_zone,
    detect_exhaustion_gap_down,
    detect_exhaustion_gap_up,
    detect_gap,
    detect_invisible_gap,
    detect_island_reversal_bottom,
    detect_island_reversal_top,
    downward_runaway_gap_signal,
    false_fill_reasons,
    gap_long_black_hold_condition,
    gap_long_red_hold_condition,
    gap_volume_strength,
    is_battleground_zone,
    is_breakout_watch_day,
    is_true_fill,
    measure_escape_gap_down_target,
    measure_escape_gap_up_target,
    pullback_gap_down_signal,
    pullback_short_scalp_signal,
    rebound_gap_up_signal,
    rebound_short_scalp_signal,
    role_flip_on_breach,
    update_down_gap_state,
    update_up_gap_state,
    upward_runaway_gap_signal,
)


def test_detect_gap_up_down_and_none():
    # R-GAP-01: 向上跳空=當日最低>前一日最高；向下跳空=當日最高<前一日最低
    up = detect_gap(prev_high=100, prev_low=95, curr_high=110, curr_low=105)
    assert up == Gap("up_gap", 100, 105, 5)

    down = detect_gap(prev_high=100, prev_low=95, curr_high=90, curr_low=85)
    assert down == Gap("down_gap", 90, 95, 5)

    assert detect_gap(prev_high=100, prev_low=95, curr_high=102, curr_low=98) is None


def test_is_true_fill_requires_all_three_elements():
    gap = Gap("up_gap", lower_edge=100, upper_edge=105, size=5)
    # 大量+黑K實體(無長影線)+收盤越界 -> 真封口
    assert is_true_fill(gap, candidate_is_black=True, candidate_is_red=False, candidate_has_long_shadow=False,
                         candidate_volume=200, avg_volume=100, candidate_close=99, k_fill=1.5) is True
    # 有長下影線 -> 不是真封口
    assert is_true_fill(gap, candidate_is_black=True, candidate_is_red=False, candidate_has_long_shadow=True,
                         candidate_volume=200, avg_volume=100, candidate_close=99, k_fill=1.5) is False


def test_false_fill_reasons_long_shadow_and_volume_shrink():
    gap = Gap("up_gap", lower_edge=100, upper_edge=105, size=5)
    # 情境a：黑K長下影線
    reasons1 = false_fill_reasons(gap, candidate_is_black=True, candidate_is_red=False, candidate_has_long_shadow=True,
                                   candidate_volume=200, avg_volume=100, candidate_close=99, candidate_low=98, candidate_high=101)
    assert "黑K留長下影線，收盤未真正棄守" in reasons1

    # 情境c：量縮
    reasons2 = false_fill_reasons(gap, candidate_is_black=True, candidate_is_red=False, candidate_has_long_shadow=False,
                                   candidate_volume=50, avg_volume=100, candidate_close=99, candidate_low=98, candidate_high=101)
    assert "量縮，跌破力道不足" in reasons2

    # 根本沒觸及缺口下沿 -> 不算假封口(也不算真封口)
    reasons3 = false_fill_reasons(gap, candidate_is_black=True, candidate_is_red=False, candidate_has_long_shadow=True,
                                   candidate_volume=200, avg_volume=100, candidate_close=99, candidate_low=110, candidate_high=112)
    assert reasons3 == []


def test_up_gap_four_tiers_and_state_ladder():
    tiers = build_up_gap_tiers(k_before_high=100, k_before_low=95, k_after_high=115, k_after_low=105, k_after_volume=300, avg_volume=100)
    assert tiers.上高 == 115 and tiers.上沿 == 105 and tiers.下沿 == 100 and tiers.下底 == 95

    assert update_up_gap_state(tiers, close_t=120, is_true_filled=False) == "強力多頭（四層皆未破）"
    assert update_up_gap_state(tiers, close_t=110, is_true_filled=False) == "多頭轉弱警訊（仍屬多頭）"
    assert update_up_gap_state(tiers, close_t=102, is_true_filled=False) == "缺口內拉鋸震盪"
    assert update_up_gap_state(tiers, close_t=102, is_true_filled=True) == "降級為一般多頭（缺口已真封口，非強力多頭，但未翻空）"
    assert update_up_gap_state(tiers, close_t=98, is_true_filled=False) == "氣勢完全轉弱（提防下底不可再破）"
    assert update_up_gap_state(tiers, close_t=90, is_true_filled=False) == "多空易位（正式翻空）"

    assert build_up_gap_tiers(100, 95, 115, 105, k_after_volume=50, avg_volume=100) is None


def test_down_gap_four_tiers_mirror_up():
    tiers = build_down_gap_tiers(k_before_high=105, k_before_low=100, k_after_high=95, k_after_low=85, k_after_volume=300, avg_volume=100)
    assert tiers.下底 == 85 and tiers.下沿 == 95 and tiers.上沿 == 100 and tiers.上高 == 105
    assert update_down_gap_state(tiers, close_t=80, is_true_filled=False) == "強力空頭（四層皆未破）"
    assert update_down_gap_state(tiers, close_t=110, is_true_filled=False) == "空多易位（正式翻多）"


def test_gap_volume_strength_three_tiers():
    assert "無量" in gap_volume_strength(gap_volume=50, avg_volume=100)
    assert "普通量能" in gap_volume_strength(gap_volume=120, avg_volume=100, k_vol=1.5)
    assert "放量缺口" in gap_volume_strength(gap_volume=200, avg_volume=100, k_vol=1.5)


def test_role_flip_and_battleground_zone():
    assert role_flip_on_breach("up_gap", "down") == "轉為未來反彈的壓力（缺口過壓）"
    assert role_flip_on_breach("down_gap", "up") == "轉為未來回檔的支撐"
    assert role_flip_on_breach("up_gap", "up") is None

    gap = Gap("up_gap", 100, 105, 5)
    assert is_battleground_zone(102, gap, was_formed_with_large_volume=True) is not None
    assert is_battleground_zone(110, gap, was_formed_with_large_volume=True) is None
    assert is_battleground_zone(102, gap, was_formed_with_large_volume=False) is None


def test_detect_breakaway_gap_up_and_down():
    gap_up = Gap("up_gap", lower_edge=100, upper_edge=105, size=5)
    result = detect_breakaway_gap_up(gap_up, consolidation_upper=98, is_large_volume=True, gap_filled_within_3_days=False)
    assert result["category"] == "突破缺口（打底完成）"
    assert result["support"] == 100
    assert "warning" not in result

    result2 = detect_breakaway_gap_up(gap_up, consolidation_upper=98, is_large_volume=True, gap_filled_within_3_days=True)
    assert "warning" in result2

    assert detect_breakaway_gap_up(gap_up, consolidation_upper=110, is_large_volume=True, gap_filled_within_3_days=False) is None

    gap_down = Gap("down_gap", lower_edge=90, upper_edge=95, size=5)
    result3 = detect_breakaway_gap_down(gap_down, topping_pattern_confirmed=True, gap_filled_within_3_days=False)
    assert result3["volume_requirement"] == "不需要大量配合（與突破缺口不同，無量亦成立）"
    assert detect_breakaway_gap_down(gap_down, topping_pattern_confirmed=False, gap_filled_within_3_days=False) is None


def test_detect_exhaustion_gap_up_and_down():
    gap_up = Gap("up_gap", 100, 105, 5)
    confirmed = detect_exhaustion_gap_up(gap_up, is_late_stage_rally=True, had_huge_volume=True, volume_shrinking_after=True, price_stalling=True, is_filled_within_3_days=True)
    assert confirmed["category"] == "向上竭盡缺口"
    assert confirmed["support_reliable"] is False
    assert "戒備頭部反轉" in confirmed["signal"]

    incomplete = detect_exhaustion_gap_up(gap_up, is_late_stage_rally=True, had_huge_volume=False, volume_shrinking_after=True, price_stalling=True, is_filled_within_3_days=True)
    assert incomplete["category"] == "疑似竭盡缺口（條件不完整，需持續觀察）"

    assert detect_exhaustion_gap_up(gap_up, is_late_stage_rally=False, had_huge_volume=True, volume_shrinking_after=True, price_stalling=True, is_filled_within_3_days=True) is None

    gap_down = Gap("down_gap", 90, 95, 5)
    confirmed_down = detect_exhaustion_gap_down(gap_down, is_late_stage_decline=True, had_huge_volume=True, volume_shrinking_after=True, price_stalling=True, is_filled_within_3_days=True)
    assert confirmed_down["resistance_reliable"] is False
    assert "止跌/翻多" in confirmed_down["signal"]


def test_island_reversal_bottom_requires_non_overlapping_gaps():
    down_gap = Gap("down_gap", lower_edge=90, upper_edge=95, size=5)
    up_gap = Gap("up_gap", lower_edge=100, upper_edge=105, size=5)
    result = detect_island_reversal_bottom(down_gap, up_gap, consolidation_days=3)
    assert result["category"] == "低檔島型反轉"
    assert result["support"] == 100

    overlapping_down = Gap("down_gap", lower_edge=90, upper_edge=102, size=12)
    assert detect_island_reversal_bottom(overlapping_down, up_gap, consolidation_days=3) is None


def test_classify_island_reversal_bottom_subtype():
    assert classify_island_reversal_bottom_subtype(True, True, is_bull_confirmed=True) == "趨勢反轉型（型態最強）"
    assert classify_island_reversal_bottom_subtype(True, True, is_gradual_climb=True) == "碎步上漲型"
    assert classify_island_reversal_bottom_subtype(True, True, tests_bottom_high=True) == "低檔反彈型"
    assert classify_island_reversal_bottom_subtype(False, True, is_bull_confirmed=True) is None


def test_island_reversal_top_mirrors_bottom():
    up_gap = Gap("up_gap", lower_edge=100, upper_edge=110, size=10)
    down_gap = Gap("down_gap", lower_edge=90, upper_edge=95, size=5)
    result = detect_island_reversal_top(up_gap, down_gap, topping_days=2)
    assert result["category"] == "高檔島型反轉"
    assert result["resistance"] == 95

    overlapping_down = Gap("down_gap", lower_edge=115, upper_edge=120, size=5)
    assert detect_island_reversal_top(up_gap, overlapping_down, topping_days=2) is None


def test_detect_3day_2gap_up_by_position():
    gap1 = Gap("up_gap", 100, 105, 5)
    gap2 = Gap("up_gap", 108, 112, 4)
    assert "強力續漲" in detect_3day_2gap_up(gap1, gap2, True, "打底")["signal"]
    assert detect_3day_2gap_up(gap1, gap2, True, "高檔", is_huge_volume=False) == {"category": "向上3日2缺口"}
    warned = detect_3day_2gap_up(gap1, gap2, True, "高檔", is_huge_volume=True, next_is_hanging_man=True)
    assert "吊人線" in warned["warning"]
    down_gap = Gap("down_gap", 1, 2, 1)
    assert detect_3day_2gap_up(gap1, down_gap, True, "打底") is None


def test_detect_3day_2gap_down_mirrors_up():
    gap1 = Gap("down_gap", 95, 100, 5)
    gap2 = Gap("down_gap", 88, 92, 4)
    assert "強力續跌" in detect_3day_2gap_down(gap1, gap2, True, "做頭")["signal"]
    assert "容易反彈" in detect_3day_2gap_down(gap1, gap2, True, "低檔", is_huge_volume=True)["warning"]


def test_gap_long_red_functions():
    gap = Gap("up_gap", lower_edge=100, upper_edge=105, size=5)
    assert gap_long_red_hold_condition(106, gap) is True
    assert gap_long_red_hold_condition(104, gap) is False

    assert classify_gap_long_red_consolidation(108, long_red_high=110, long_red_low=100, long_red_close=107) == "強勢整理型態B（收盤價之上，可追價）"
    assert classify_gap_long_red_consolidation(106, long_red_high=110, long_red_low=100, long_red_close=107) == "強勢整理型態A（二分之一價之上、收盤價之下）"
    assert classify_gap_long_red_consolidation(104, long_red_high=110, long_red_low=100, long_red_close=107) is None

    assert is_breakout_watch_day(3) is True
    assert is_breakout_watch_day(4) is False


def test_gap_long_black_functions_mirror_red():
    gap = Gap("down_gap", lower_edge=90, upper_edge=95, size=5)
    assert gap_long_black_hold_condition(89, gap) is True
    assert gap_long_black_hold_condition(91, gap) is False

    assert classify_gap_long_black_consolidation(92, long_black_high=100, long_black_low=90, long_black_close=93) == "弱勢整理型態B（收盤價之下，可追價放空）"
    assert classify_gap_long_black_consolidation(94, long_black_high=100, long_black_low=90, long_black_close=93) == "弱勢整理型態A（二分之一價之下、收盤價之上）"
    assert classify_gap_long_black_consolidation(96, long_black_high=100, long_black_low=90, long_black_close=93) is None


def test_upward_and_downward_runaway_gap_signal():
    gap_up = Gap("up_gap", 100, 105, 5)
    assert upward_runaway_gap_signal(gap_up, True, False) == "缺口未回補，強勢上漲持續，續漲機率高"
    assert upward_runaway_gap_signal(gap_up, True, True, bull_structure_unchanged=True) == "缺口已回補，攻擊功能喪失，趨勢轉弱但未反轉"
    assert upward_runaway_gap_signal(gap_up, True, True, bull_structure_unchanged=False) == "缺口已回補，且多頭架構已改變，需重新評估趨勢"
    assert upward_runaway_gap_signal(gap_up, False, False) is None
    assert measure_escape_gap_up_target(gap_up, prior_leg_start_price=90) == 115

    gap_down = Gap("down_gap", 90, 95, 5)
    assert downward_runaway_gap_signal(gap_down, True, False) == "缺口未回補，強勢下跌持續，續跌機率高"
    assert measure_escape_gap_down_target(gap_down, prior_leg_start_price=110) == 75


def test_classify_common_gap_in_range():
    inside_gap = Gap("up_gap", 100, 102, 2)
    result = classify_common_gap_in_range(inside_gap, consolidation_upper=110, filled_within_2_days=True)
    assert result["significance"] == "低，不宜作為進出場依據"

    breakout_gap = Gap("up_gap", 100, 115, 15)
    assert classify_common_gap_in_range(breakout_gap, consolidation_upper=110, filled_within_2_days=True) is None


def test_pullback_gap_down_and_scalp_signal():
    gap = Gap("down_gap", 95, 98, 3)
    assert pullback_gap_down_signal(gap, True, close_t=100, swing_low=95) == "未跌破前低，視為多頭正常回檔，不可做空"
    assert pullback_gap_down_signal(gap, True, close_t=90, swing_low=95, rebound_fails_prior_high=True) == "底底低+頭頭低同時成立，空頭反轉確認"
    assert pullback_gap_down_signal(gap, True, close_t=90, swing_low=95) == "跌破前低，形成底底低"

    assert pullback_short_scalp_signal(3, True, close_t=90, prev_low=95) == "可少量搶空單，停損設於當天黑K高點"
    assert pullback_short_scalp_signal(2, True, close_t=90, prev_low=95) is None


def test_rebound_gap_up_and_scalp_signal():
    gap = Gap("up_gap", 100, 103, 3)
    assert rebound_gap_up_signal(gap, True, close_t=105, swing_high=104) == "突破月線或前高，列入打底觀察股名單"
    assert rebound_gap_up_signal(gap, True, close_t=102, swing_high=104) == "未過前高，僅視為空頭反彈，不可做多"

    assert rebound_short_scalp_signal(3, True, close_t=105, prev_high=100) == "可少量搶反彈（需設停損停利，短線操作）"
    assert rebound_short_scalp_signal(2, True, close_t=105, prev_high=100) is None


def test_detect_crossfire_zone():
    upper_gap = Gap("down_gap", 110, 115, 5)
    lower_gap = Gap("up_gap", 90, 95, 5)
    result = detect_crossfire_zone(upper_gap, lower_gap, False, False, price_t=100)
    assert result["category"] == "多空交鋒（雙缺口區間震盪）"
    assert result["resistance"] == 110
    assert result["support"] == 95

    assert detect_crossfire_zone(upper_gap, lower_gap, True, False, price_t=100) is None
    assert detect_crossfire_zone(upper_gap, lower_gap, False, False, price_t=200) is None


def test_detect_invisible_gap():
    assert detect_invisible_gap(prev_close=100, open_t=102, close_t=101) == {"type": "up_invisible_gap", "boundary": 100}
    assert detect_invisible_gap(prev_close=100, open_t=98, close_t=99) == {"type": "down_invisible_gap", "boundary": 100}
    assert detect_invisible_gap(prev_close=100, open_t=102, close_t=99) is None  # 開盤跳空但收盤已回補
    assert detect_invisible_gap(prev_close=100, open_t=100, close_t=100) is None


def test_classify_gap_cause():
    # R-GAP-02: 除權息優先判定，其餘依量能是否顯著放大(預設2倍均量)區分主力發動/消息面缺口
    assert classify_gap_cause(is_ex_dividend_or_capital_change=True, gap_day_volume=5000, avg_volume_n=1000) == "除權息或增減資缺口（技術性，意義不大，建議排除於型態規則統計樣本）"
    assert classify_gap_cause(is_ex_dividend_or_capital_change=False, gap_day_volume=3000, avg_volume_n=1000) == "疑似主力發動缺口（訊號權重最高，優先關注）"
    assert classify_gap_cause(is_ex_dividend_or_capital_change=False, gap_day_volume=1200, avg_volume_n=1000) == "疑似消息面缺口（預期偏短期震盪；若可比對新聞為經濟政策面消息，則放寬持續觀察天數）"
