import pytest

from src.risk.money_management import (
    annual_profit_rate,
    capital_exposure_limit,
    check_stage_compliance,
    compound_growth_path,
    exceeds_position_concentration_limit,
    fixed_pct_stop_loss,
    hits_absolute_stop_loss,
    is_daily_warning_stock,
    is_high_win_rate_entry,
    is_weak_stock_needs_switch,
    max_invested_amount,
    required_win_rate,
    should_take_profit,
    stop_loss_and_reallocate,
    target_invested_amount,
    trapped_position_level,
)


def test_annual_profit_rate_matches_book_examples():
    # R-RISK-03: 書中5組範例，方案3已依正文算式訂正為15勝15敗
    assert annual_profit_rate(20, 0.50, 0.07, 0.05) == pytest.approx(0.20)
    assert annual_profit_rate(20, 0.60, 0.07, 0.05) == pytest.approx(0.44)
    assert annual_profit_rate(30, 0.50, 0.07, 0.05) == pytest.approx(0.30)
    assert annual_profit_rate(30, 0.60, 0.07, 0.05) == pytest.approx(0.66)
    assert annual_profit_rate(12, 7 / 12, 0.20, 0.05) == pytest.approx(1.15, rel=1e-2)


def test_required_win_rate_round_trips_with_annual_profit_rate():
    win_rate = required_win_rate(target_annual_profit_rate=0.20, total_trades=20, profit_pct=0.07, loss_pct=0.05)
    assert win_rate == pytest.approx(0.50)
    assert annual_profit_rate(20, win_rate, 0.07, 0.05) == pytest.approx(0.20)


def test_trapped_position_level_thresholds():
    assert trapped_position_level(entry_price=100, current_price=69) == "重度套牢"
    assert trapped_position_level(entry_price=100, current_price=79) == "中度套牢"
    assert trapped_position_level(entry_price=100, current_price=89) == "輕度套牢"
    assert trapped_position_level(entry_price=100, current_price=95) == "未套牢"


def test_is_daily_warning_stock():
    assert is_daily_warning_stock(prev_close=100, today_close=94) is True
    assert is_daily_warning_stock(prev_close=100, today_close=96) is False


def test_fixed_pct_stop_loss_clamps_to_2_10_pct_range():
    # R-RISK-01④固定比例停損法：2%~10%，硬性上限10%
    assert fixed_pct_stop_loss(entry_price=100, direction="多", pct=0.05) == 95
    assert fixed_pct_stop_loss(entry_price=100, direction="多", pct=0.15) == 90  # clamp到10%上限
    assert fixed_pct_stop_loss(entry_price=100, direction="空", pct=0.05) == 105


def test_hits_absolute_stop_loss_five_scenarios():
    assert hits_absolute_stop_loss(unrealized_loss_pct=0.12) is True
    assert hits_absolute_stop_loss(bull_high_reversed_to_bear=True) is True
    assert hits_absolute_stop_loss() is False


def test_capital_exposure_and_max_invested_limits():
    # R-RISK-02: 股市曝險上限=可運用資金50%，滿倉上限=曝險上限90%
    assert capital_exposure_limit(1_000_000) == 500_000
    assert max_invested_amount(500_000) == 450_000


def test_target_invested_amount_respects_max_cap():
    # 中期多頭50% * 50萬 = 25萬，未超過45萬滿倉上限
    assert target_invested_amount(1_000_000, "中期多頭") == pytest.approx(250_000)
    # 多頭確認且4線多排 70% * 50萬 = 35萬，仍未超過45萬上限
    assert target_invested_amount(1_000_000, "多頭確認且4線多排") == pytest.approx(350_000)
    assert target_invested_amount(1_000_000, "未知階段") == 0


def test_exceeds_position_concentration_limit():
    assert exceeds_position_concentration_limit(6) is True
    assert exceeds_position_concentration_limit(5) is False


def test_check_stage_compliance_flags_violations():
    # R-RISK-04: 初階持股上限2檔、不可用槓桿
    assert check_stage_compliance("初階", position_count=3, uses_leverage=False) == [
        "持股檔數超過本階段上限，違反集中操作原則"
    ]
    assert check_stage_compliance("初階", position_count=1, uses_leverage=True) == [
        "本階段不應使用槓桿(融資)"
    ]
    assert check_stage_compliance("終極", position_count=5, uses_leverage=True) == []


def test_compound_growth_path_reaches_target_by_year_7():
    path = compound_growth_path(principal=100_000, annual_rate=1.0, years=8)
    assert path[0] == 100_000
    assert path[7] == pytest.approx(12_800_000)


def test_is_weak_stock_needs_switch():
    assert is_weak_stock_needs_switch(0.03) is True
    assert is_weak_stock_needs_switch(0.05) is False
    assert is_weak_stock_needs_switch(0.08) is False


def test_is_high_win_rate_entry():
    # R-RISK-06 時機點1
    assert is_high_win_rate_entry(matches_high_win_pattern=True, hits_entry_taboos=False) is True
    assert is_high_win_rate_entry(matches_high_win_pattern=True, hits_entry_taboos=True) is False
    assert is_high_win_rate_entry(matches_high_win_pattern=False, hits_entry_taboos=False) is False


def test_should_take_profit_requires_resistance_plus_one_signal():
    # 時機點2：需已達壓力位置，且量能轉弱或轉折K線訊號至少一項成立
    assert should_take_profit(True, volume_weakening=True, reversal_candle_signal=False) is True
    assert should_take_profit(True, volume_weakening=False, reversal_candle_signal=True) is True
    assert should_take_profit(True, volume_weakening=False, reversal_candle_signal=False) is False
    assert should_take_profit(False, volume_weakening=True, reversal_candle_signal=True) is False


def test_stop_loss_and_reallocate():
    # 時機點3：停損後資金不閒置，立即尋找新標的
    assert stop_loss_and_reallocate(stop_loss_triggered=False, high_win_rate_candidate_available=True) == "續抱"
    assert stop_loss_and_reallocate(True, True) == "停損平倉並轉入新標的"
    assert stop_loss_and_reallocate(True, False) == "停損平倉，暫無高勝率標的，資金待命"
