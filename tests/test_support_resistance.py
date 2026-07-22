import pandas as pd
import pytest

from src.indicators.gaps import Gap
from src.indicators.support_resistance import (
    bear_rebound_resistance_levels,
    bear_trend_change_escape_wave,
    bear_trend_strength,
    bearish_pattern_target_price,
    bearish_resistance_short_signal,
    bull_pullback_support_levels,
    bull_trend_change_escape_wave,
    bull_trend_strength,
    bullish_pattern_target_price,
    bullish_support_buy_signal,
    classify_bottom_role,
    classify_breakdown_holds,
    classify_breakout_holds,
    classify_candle_type,
    classify_head_role,
    confirm_resistance,
    confirm_support,
    consolidation_zone_role_strength,
    consolidation_zone_role_transition,
    detect_wash_out_breakdown,
    equal_move_target,
    evaluate_breakdown_window,
    evaluate_breakout_window,
    find_key_volume_level,
    gap_resistance_zone_reaction,
    gap_support_zone_reaction,
    is_big_volume,
    is_bearish_reversal_candle,
    is_bullish_reversal_candle,
    is_near_psychological_level,
    key_volume_level_breakout_signal,
    ma_resistance_conversion_short,
    ma_support_conversion_long,
    nearest_round_level,
    neckline_touch_signal,
)


def test_classify_head_and_bottom_role():
    # R-SR-01: 未突破前是壓力，突破後回測反過來變支撐
    assert classify_head_role(head_price=100, current_price=95, has_broken_above=False) == "壓力"
    assert classify_head_role(head_price=100, current_price=105, has_broken_above=True) == "支撐"
    # R-SR-02: 與R-SR-01鏡射
    assert classify_bottom_role(bottom_price=100, current_price=105, has_broken_below=False) == "支撐"
    assert classify_bottom_role(bottom_price=100, current_price=95, has_broken_below=True) == "壓力"


def test_ma_support_conversion_long_within_and_beyond_window():
    # R-SR-08: 3日內站回=支撐有效；逾3日未站回=支撐轉壓力
    close = pd.Series([101.0, 99.0, 98.0, 101.0, 100.0])
    ma = pd.Series([100.0] * 5)
    ma_dir = pd.Series(["上揚"] * 5)
    result = ma_support_conversion_long(close, ma, ma_dir, window_days=3)
    assert result.iloc[3] == "月線支撐依然有效，多頭趨勢未變"

    close2 = pd.Series([101.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0])
    ma2 = pd.Series([100.0] * 8)
    ma_dir2 = pd.Series(["上揚"] * 8)
    result2 = ma_support_conversion_long(close2, ma2, ma_dir2, window_days=3)
    assert result2.iloc[5] == "逾期未站回，月線支撐轉為壓力，多頭趨勢轉弱/反轉"


def test_ma_resistance_conversion_short_mirrors_long():
    close = pd.Series([99.0, 101.0, 102.0, 99.0, 100.0])
    ma = pd.Series([100.0] * 5)
    ma_dir = pd.Series(["下彎"] * 5)
    result = ma_resistance_conversion_short(close, ma, ma_dir, window_days=3)
    assert result.iloc[3] == "月線壓力依然有效，空頭趨勢未變"


def test_classify_candle_type():
    open_ = pd.Series([100.0, 106.0, 100.0, 101.0, 100.0])
    close = pd.Series([106.0, 100.0, 101.0, 100.0, 100.5])
    high = pd.Series([107.0, 107.0, 110.0, 101.5, 101.0])
    low = pd.Series([99.0, 99.0, 99.5, 90.0, 99.7])
    result = classify_candle_type(open_, high, low, close)
    assert result.tolist() == ["中長紅K", "中長黑K", "長上影線K", "長下影線K", "變盤線"]


def test_is_big_volume():
    result = is_big_volume(pd.Series([100.0, 200.0]), pd.Series([100.0, 100.0]), k=1.5)
    assert result.tolist() == [False, True]


def test_confirm_resistance_four_scenarios():
    # 情境1：大量中長紅未過壓，次日仍收紅 -> 尚未確認
    assert confirm_resistance("中長紅K", 100, 105, "中長紅K", True, False) == "尚未確認，持續觀察"
    # 情境1確認：次日收黑 -> 確認遇壓回檔
    assert confirm_resistance("中長紅K", 100, 105, "中長黑K", False, True) == "確認遇壓回檔（闖關前爆大量，股價不漲要回檔）"
    # 情境2：長上影線觸壓未站穩
    assert confirm_resistance("長上影線K", 100, 105, "其他", False, False) == "遇壓，次日容易下跌"
    # 情境4：突破後次日爆量長黑 -> 疑似假突破
    assert confirm_resistance("中長紅K", 110, 105, "中長黑K", False, True) == "高機率假突破，執行多單停損準備"
    assert confirm_resistance("中長紅K", 110, 105, "中長紅K", True, False) == "突破有效，壓力轉支撐"


def test_confirm_support_mirrors_resistance():
    assert confirm_support("中長黑K", 100, 95, "中長紅K", True, True) == "確認遇撐反彈（過撐爆大量，股價不跌要反彈）"
    assert confirm_support("中長黑K", 90, 95, "中長紅K", True, True) == "高機率假跌破，執行空單停損準備"


def test_reversal_candle_helpers():
    # R-SR-15: 紅K或長下影線(>=實體1倍)為止跌訊號
    assert is_bullish_reversal_candle(open_=100, high=103, low=99, close=102) is True  # 紅K
    assert is_bullish_reversal_candle(open_=100, high=100.5, low=90, close=98) is True  # 長下影線
    assert is_bullish_reversal_candle(open_=100, high=100.5, low=97, close=98) is False  # 影線不夠長

    # R-SR-16: 黑K或長上影線為止漲訊號
    assert is_bearish_reversal_candle(open_=100, high=101, low=98, close=99) is True  # 黑K
    assert is_bearish_reversal_candle(open_=100, high=110, low=99.5, close=101) is True  # 長上影線


def test_bullish_support_and_bearish_resistance_signal():
    assert bullish_support_buy_signal("多頭趨勢", True, "月線", True) == "月線支撐＋止跌訊號K棒 → 次日買進訊號候選"
    assert bullish_support_buy_signal("空頭趨勢", True, "月線", True) is None
    assert bullish_support_buy_signal("多頭趨勢", False, "月線", True) is None

    assert bearish_resistance_short_signal("空頭趨勢", True, "前高", True) == "前高壓力＋止漲訊號K棒 → 次日放空訊號候選"
    assert bearish_resistance_short_signal("多頭趨勢", True, "前高", True) is None


def test_bull_trend_strength_priority_chain():
    assert bull_trend_strength(110, 100, "上揚", 90, 120, True, False) == "強勢多頭"
    assert bull_trend_strength(110, 100, "上揚", 90, 120, False, False) == "多頭趨勢不變，可續做多"
    assert bull_trend_strength(125, 100, "下彎", 90, 120, False, True) == "多頭沒有改變"
    assert bull_trend_strength(115, 100, "下彎", 90, 120, False, True) == "多頭進入盤整"
    assert bull_trend_strength(85, 100, "下彎", 90, 120, False, False) == "多頭趨勢改變"
    assert "轉弱" in bull_trend_strength(95, 100, "下彎", 90, 120, False, False)


def test_bull_trend_change_escape_wave():
    assert bull_trend_change_escape_wave(True, 115, 120) == "多單逃命波賣點"
    assert bull_trend_change_escape_wave(True, 125, 120) is None
    assert bull_trend_change_escape_wave(False, 115, 120) is None


def test_bear_trend_strength_and_escape_wave_mirror_bull():
    assert bear_trend_strength(90, 100, "下彎", 90, 120, True, False) == "弱勢空頭"
    assert bear_trend_change_escape_wave(True, 95, 90) == "空單逃命波回補點"
    assert bear_trend_change_escape_wave(True, 85, 90) is None


def test_gap_support_zone_reaction_permanent_invalidation():
    gap = Gap("up_gap", lower_edge=100, upper_edge=105, size=5)
    assert gap_support_zone_reaction(gap, close_t=102, already_breached=False) == ("股價回檔進入缺口區間，具支撐作用", False)
    assert gap_support_zone_reaction(gap, close_t=98, already_breached=False) == ("缺口支撐已被跌破，空方力道轉強，缺口永久失效", True)
    # 一旦已失效，即使收盤又回到缺口區間內，也不再具支撐(不因回補恢復)
    assert gap_support_zone_reaction(gap, close_t=102, already_breached=True) == ("缺口已失效，不再具支撐作用（即使已回補）", True)


def test_gap_resistance_zone_reaction_mirrors_support():
    gap = Gap("down_gap", lower_edge=90, upper_edge=95, size=5)
    assert gap_resistance_zone_reaction(gap, close_t=92, already_breached=False) == ("股價反彈進入缺口區間，具壓力作用", False)
    assert gap_resistance_zone_reaction(gap, close_t=97, already_breached=False) == ("缺口壓力已被突破，多方力道轉強，缺口永久失效", True)


def test_classify_breakout_and_breakdown_holds():
    assert classify_breakout_holds(True, 1, hold_threshold=2) == "假突破（未站穩）— 前高角色不變，仍為壓力"
    assert classify_breakout_holds(True, 2, hold_threshold=2) == "已站穩後才小幅拉回，視為真突破後的正常回測，非假突破"
    assert classify_breakout_holds(False, 0) == "真突破，前高轉支撐"

    assert classify_breakdown_holds(True, 1, hold_threshold=2) == "假跌破（未站穩）— 前低角色不變，仍為支撐"
    assert classify_breakdown_holds(False, 0) == "真跌破，前低轉壓力"


def test_evaluate_breakout_and_breakdown_window():
    assert evaluate_breakout_window(prior_high=100, window_closes=[99, 98, 97]) == "假突破（未站穩）— 前高角色不變，仍為壓力"
    assert evaluate_breakout_window(prior_high=100, window_closes=[101, 102, 103]) == "真突破，前高轉支撐"
    assert evaluate_breakdown_window(prior_low=100, window_closes=[101, 102, 103]) == "假跌破（未站穩）— 前低角色不變，仍為支撐"


def test_fibonacci_support_and_resistance_levels():
    support = bull_pullback_support_levels(low_a=100, high_b=200, ratios=(0.382, 0.5, 0.618))
    assert support[0.5] == 150
    assert support[0.382] == pytest.approx(161.8)

    resistance = bear_rebound_resistance_levels(high_a=200, low_b=100, ratios=(0.382, 0.5))
    assert resistance[0.5] == 150
    assert resistance[0.382] == pytest.approx(138.2)


def test_consolidation_zone_role_strength_and_transition():
    assert consolidation_zone_role_strength(price_in_zone=90, zone_low=80, zone_high=100, direction="回測支撐") == 0.5
    assert consolidation_zone_role_strength(price_in_zone=85, zone_low=80, zone_high=100, direction="回測壓力") == 0.75

    assert consolidation_zone_role_transition(close_price=75, zone_low=80, zone_high=100) == "整個盤整區轉為壓力區"
    assert consolidation_zone_role_transition(close_price=105, zone_low=80, zone_high=100) == "整個盤整區轉為支撐區"
    assert consolidation_zone_role_transition(close_price=90, zone_low=80, zone_high=100) is None


def test_neckline_touch_signal():
    assert neckline_touch_signal(low_t=79, high_t=95, lower_neckline=80, upper_neckline=100) == "觸及下頸線，具支撐參考"
    assert neckline_touch_signal(low_t=85, high_t=101, lower_neckline=80, upper_neckline=100) == "觸及上頸線，具壓力參考"
    assert neckline_touch_signal(low_t=85, high_t=95, lower_neckline=80, upper_neckline=100) is None


def test_pattern_target_price_formulas():
    assert bullish_pattern_target_price(neckline_price=100, pattern_low=90) == 110
    assert bearish_pattern_target_price(neckline_price=100, pattern_high=110) == 90


def test_find_key_volume_level_top2_and_breakout_signal():
    # R-SR-05: 頭部區間取成交量最大2根K棒的最高價為關鍵壓力
    high = pd.Series([10.0, 12.0, 11.0, 9.0])
    low = pd.Series([8.0, 9.0, 8.5, 7.0])
    volume = pd.Series([100.0, 500.0, 400.0, 50.0])
    key_resistance = find_key_volume_level(high, low, volume, zone_type="頭部", top_n=2)
    assert key_resistance == 12.0  # 量最大的index1,2對應high取max=12.0

    assert key_volume_level_breakout_signal(13.0, key_resistance, "頭部") == "突破頭部大量壓力關卡，做多買點；原壓力轉為支撐"
    assert key_volume_level_breakout_signal(11.0, key_resistance, "頭部") is None

    key_support = find_key_volume_level(high, low, volume, zone_type="底部", top_n=2)
    assert key_support == 8.5  # 量最大的index1,2對應low取min=8.5
    assert key_volume_level_breakout_signal(8.0, key_support, "底部") == "跌破底部大量支撐關卡，做空賣點；原支撐轉為壓力"


def test_nearest_round_level_and_psychological_proximity():
    # R-SR-13: 距離百分比書中未給精確門檻，工程補充預設2%
    assert nearest_round_level(price=97.0, round_unit=100) == 100.0
    assert nearest_round_level(price=920.0, round_unit=100) == 900.0

    near, level = is_near_psychological_level(price=98.5, round_unit=100, distance_pct_threshold=0.02)
    assert near is True
    assert level == 100.0

    near_far, level_far = is_near_psychological_level(price=90.0, round_unit=100, distance_pct_threshold=0.02)
    assert near_far is False
    assert level_far is None


def test_equal_move_target_bullish_and_bearish():
    # R-SR-19: D=|B-A|，目標價=C±D
    target, role = equal_move_target(a_price=80.0, b_price=100.0, c_price=110.0, direction="多頭突破")
    assert target == 130.0
    assert role == "壓力"

    target2, role2 = equal_move_target(a_price=100.0, b_price=80.0, c_price=70.0, direction="空頭跌破")
    assert target2 == 50.0
    assert role2 == "支撐"


def test_detect_wash_out_breakdown_requires_base_and_confirms_via_rebound():
    # R-SR-20: 無打底架構前置條件時不適用
    assert detect_wash_out_breakdown(
        base_confirmed=False, breakdown_close=95, support_price=100,
        breakdown_volume=100, ma5_volume_at_breakdown=200,
        has_long_lower_shadow=False, prior_bush_volume_seen=False,
        rebound_back_above=True, subsequent_breakout_with_volume=True,
    ) == "不適用洗盤判讀（尚無打底/盤整架構前置條件），依一般跌破處理"

    # 未跌破支撐時不適用
    assert detect_wash_out_breakdown(
        base_confirmed=True, breakdown_close=105, support_price=100,
        breakdown_volume=100, ma5_volume_at_breakdown=200,
        has_long_lower_shadow=False, prior_bush_volume_seen=False,
        rebound_back_above=True, subsequent_breakout_with_volume=True,
    ) == "尚未跌破支撐，不適用"

    # 已跌破但尚未拉回
    assert detect_wash_out_breakdown(
        base_confirmed=True, breakdown_close=95, support_price=100,
        breakdown_volume=100, ma5_volume_at_breakdown=200,
        has_long_lower_shadow=False, prior_bush_volume_seen=False,
        rebound_back_above=False, subsequent_breakout_with_volume=False,
    ) == "尚未拉回，暫視為真跌破，支撐轉壓力"

    # 跌破量縮+拉回+後續攻擊性突破 -> 洗盤進貨意圖確認
    assert detect_wash_out_breakdown(
        base_confirmed=True, breakdown_close=95, support_price=100,
        breakdown_volume=100, ma5_volume_at_breakdown=200,
        has_long_lower_shadow=False, prior_bush_volume_seen=False,
        rebound_back_above=True, subsequent_breakout_with_volume=True,
    ) == "疑似洗盤（假跌破誘空）：跌破區間下緣但量縮/收長下影線/打底期已見草叢量，主力可能低接承接；且拉回後爆量突破前高，洗盤進貨意圖確認"

    # 跌破量縮+拉回但尚未後續突破 -> 僅列為觀察
    assert detect_wash_out_breakdown(
        base_confirmed=True, breakdown_close=95, support_price=100,
        breakdown_volume=100, ma5_volume_at_breakdown=200,
        has_long_lower_shadow=False, prior_bush_volume_seen=False,
        rebound_back_above=True, subsequent_breakout_with_volume=False,
    ) == "疑似洗盤（假跌破誘空）：跌破區間下緣但量縮/收長下影線/打底期已見草叢量，主力可能低接承接；尚未出現後續攻擊性突破，洗盤意圖未完全驗證，僅列為觀察"

    # 跌破爆量、無主力承接特徵 -> 一般假跌破
    assert detect_wash_out_breakdown(
        base_confirmed=True, breakdown_close=95, support_price=100,
        breakdown_volume=300, ma5_volume_at_breakdown=200,
        has_long_lower_shadow=False, prior_bush_volume_seen=False,
        rebound_back_above=True, subsequent_breakout_with_volume=False,
    ) == "跌破後雖有拉回，但無明顯主力承接量能特徵，列為一般假跌破，不預設為洗盤"
