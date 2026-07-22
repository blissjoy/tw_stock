import pandas as pd
import pytest

from src.indicators.volume_price import (
    basic_volume,
    bear_decline_big_black_role,
    bull_price_flat_volume_expand_signal,
    bull_price_up_volume_flat_stall_signal,
    bull_price_up_volume_shrink_divergence,
    bear_decline_retro_accumulation_label,
    bear_exhaustion_reversal_signal,
    bear_low_divergence_signal,
    bear_low_key_point_rebound_signal,
    bear_start_decline_key_point_signal,
    bear_start_decline_stop_loss_signal,
    bull_high_key_point_pullback_signal,
    classify_big_volume_bar,
    classify_high_volume_bar,
    is_accumulation_volume,
    is_attack_volume,
    is_big_volume_vs_ma5,
    is_big_volume_vs_prev_day,
    evaluate_volume_signal,
    is_pothole_volume_pattern,
    is_stop_fall_volume,
    is_suffocation_volume,
    wash_trading_risk_flag,
    rally_start_attack_signal,
    rally_start_bull_trap_signal,
    rally_start_fake_breakout_distribution_signal,
    rally_start_low_volume_healthy_signal,
    rally_start_washout_signal,
    resistance_zone_big_volume_next_day_response,
    support_zone_big_volume_next_day_response,
)


def test_basic_volume_matches_rolling_mean():
    volume = pd.Series([100.0, 100.0, 100.0, 100.0, 100.0])
    ma5 = basic_volume(volume, n=5)
    assert ma5.iloc[3:].isna().all() == False
    assert ma5.iloc[4] == pytest.approx(100.0)


def test_attack_volume_and_big_volume_vs_ma5():
    # ma5固定為100，量125/100=1.25落在1.2~1.3之間且股價上漲 -> 攻擊量
    ma5 = pd.Series([100.0, 100.0, 100.0])
    volume = pd.Series([100.0, 125.0, 250.0])
    close = pd.Series([10.0, 11.0, 9.0])
    attack = is_attack_volume(volume, ma5, close)
    assert attack.iloc[1] == True

    big = is_big_volume_vs_ma5(volume, ma5)
    assert big.tolist() == [False, False, True]  # 250/100=2.5 >= 2倍


def test_is_stop_fall_volume_and_is_accumulation_volume():
    volume = pd.Series([100.0] * 5 + [40.0])
    ma5 = pd.Series([100.0] * 6)
    no_new_low = pd.Series([False] * 5 + [True])
    assert is_stop_fall_volume(volume, ma5, no_new_low).iloc[5] == True

    close = pd.Series([10.0] * 5 + [11.0])
    volume2 = pd.Series([100.0] * 5 + [250.0])
    assert is_accumulation_volume(volume2, ma5, close).iloc[5] == True


def test_is_big_volume_vs_prev_day():
    volume = pd.Series([100.0, 250.0])
    assert is_big_volume_vs_prev_day(volume).tolist() == [False, True]


def test_classify_high_volume_bar_three_categories():
    assert classify_high_volume_bar(True, True, True, True, False, False, False) == "調節量（主力洗盤，非真出貨）"
    assert classify_high_volume_bar(False, False, False, False, True, True, True) == "換手量（偏多延續）"
    assert classify_high_volume_bar(False, False, False, False, False, False, False, late_stage_range_broken_down=True) == "出貨量（空頭風險訊號）"
    assert classify_high_volume_bar(False, False, False, False, False, False, False) == "尚無法判定，持續觀察"


def test_rally_start_signals():
    assert rally_start_attack_signal(True, True) == "起漲攻擊量，偏多"
    assert rally_start_attack_signal(True, False) is None

    assert rally_start_bull_trap_signal(True, True, True, is_at_high_or_late_stage=True) == "誘多出貨量（高檔/末升段起漲更容易出現此情形）"
    assert rally_start_bull_trap_signal(True, True, True) == "誘多出貨量"

    assert rally_start_low_volume_healthy_signal(True, True, True) == "量縮起漲、次日補量續漲，健康多頭延續"
    assert rally_start_washout_signal(True, True, True, True) == "前段下跌回溯認定為主力洗盤，非出貨"
    assert rally_start_fake_breakout_distribution_signal(True, True, True) == "假突破，兩根K線合計大量標記為出貨量"


def test_bear_decline_big_black_role_and_retro_label():
    assert bear_decline_big_black_role(broke_above_high=True, broke_below_low=False) == "多方力量轉強，反彈確認"
    assert bear_decline_big_black_role(broke_above_high=False, broke_below_low=True) == "空方換手失敗，持續下跌"
    assert bear_decline_big_black_role(False, False) is None
    assert bear_decline_retro_accumulation_label(True, True, True) == "主力進貨量（打底第1支腳）"


def test_suffocation_and_pothole_volume():
    assert is_suffocation_volume(is_big_black_candle=True, next_close_down=True, next_volume=40, big_black_volume=100) is True
    assert is_suffocation_volume(is_big_black_candle=True, next_close_down=True, next_volume=60, big_black_volume=100) is False
    assert is_pothole_volume_pattern(True, True, True, True) is True


def test_classify_big_volume_bar_retro_labeling():
    label, price = classify_big_volume_bar("多頭", bar_high=110, bar_low=100, broke_out_above=True, broke_down_below=False)
    assert label == "攻擊進貨量／未來支撐"
    assert price == 100

    label2, price2 = classify_big_volume_bar("空頭", bar_high=110, bar_low=100, broke_out_above=False, broke_down_below=True)
    assert label2 == "恐慌出貨量／未來壓力"
    assert price2 == 110

    label3, price3 = classify_big_volume_bar("盤整", bar_high=110, bar_low=100, broke_out_above=False, broke_down_below=False)
    assert label3 == "待突破/跌破結果確認"
    assert price3 is None


def test_bull_high_key_point_pullback_signal():
    assert bull_high_key_point_pullback_signal(True, "紅", True, next_close=95, bar_low=100) == "跌破紅K最低點，回檔"
    assert bull_high_key_point_pullback_signal(True, "黑", True, next_open=98, bar_close=100, next_close=95) == "回檔"
    assert bull_high_key_point_pullback_signal(False, "紅", True, next_close=95, bar_low=100) is None


def test_bear_signals_group():
    assert bear_start_decline_stop_loss_signal(True, True, True) == "做空者停損出場"
    assert bear_start_decline_key_point_signal(True, True, True, day3_close=90, day2_low=95, day1_high=120) == "續跌，紅K視為誘多出貨/殺低洗盤騙線"
    assert bear_start_decline_key_point_signal(True, True, True, day3_close=125, day2_low=95, day1_high=120) == "突破黑K高點，停損出場"
    assert bear_low_key_point_rebound_signal(True, True, next_close=115, bar_high=110) == "反彈"
    assert bear_exhaustion_reversal_signal(True, True, True, True) == "末跌段暴大量出現紅K，容易急漲或反轉趨勢"
    assert bear_low_divergence_signal(True, True, True, True) == "容易反彈或打底"
    assert bear_low_divergence_signal(True, True, False, False) == "量價背離（低檔）"
    assert bear_low_divergence_signal(False, True, True, True) is None


def test_support_resistance_zone_next_day_response():
    assert resistance_zone_big_volume_next_day_response(True, True, True) == "回檔"
    assert resistance_zone_big_volume_next_day_response(False, True, True) is None
    assert support_zone_big_volume_next_day_response(True, True, True) == "反彈"


def test_bull_volume_price_divergence_three_patterns():
    # R-VOLPRICE-04
    assert bull_price_up_volume_shrink_divergence(price_new_high=True, volume_shrink=True) is True
    assert bull_price_up_volume_shrink_divergence(price_new_high=False, volume_shrink=True) is False

    assert bull_price_flat_volume_expand_signal(True, True, "底部") == "主力進貨訊號（偏多）"
    assert bull_price_flat_volume_expand_signal(True, True, "高檔") == "潛在出貨訊號（偏空），待後續下跌後價量關係確認"
    assert bull_price_flat_volume_expand_signal(True, True, "末升段") == "潛在出貨訊號（偏空），待後續下跌後價量關係確認"
    assert bull_price_flat_volume_expand_signal(False, True, "底部") is None

    assert bull_price_up_volume_flat_stall_signal(price_new_high=True, volume_flat=True) is True


def test_evaluate_volume_signal_requires_all_four_dimensions():
    # R-VOLPRICE-02: 4維度缺一則暫緩判定
    assert evaluate_volume_signal(None, "起漲", 0.02, 0.5) == "資訊不足，暫緩判定"
    assert evaluate_volume_signal("多頭", "起漲", 0.02, 0.5) == "趨勢=多頭, 位置=起漲, 價格變化=0.02, 量能變化=0.5"


def test_wash_trading_risk_flag():
    assert wash_trading_risk_flag(is_bottom_zone=True, is_breakout_with_big_volume=True) == "留意主力對敲可能，等待後續股價是否延續上漲驗證"
    assert wash_trading_risk_flag(is_bottom_zone=False, is_breakout_with_big_volume=True) is None
