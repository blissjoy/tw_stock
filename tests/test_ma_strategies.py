import pandas as pd
import pytest

from src.strategies.ma_strategies import (
    dual_ma_long_term_long_strategy,
    long_term_long_entry_signal,
    long_term_long_watch_exit_signal,
    long_term_short_entry_signal,
    long_term_short_watch_exit_signal,
    ma_tangle_breakdown_hold_exit_signal,
    ma_tangle_breakdown_short_entry,
    ma_tangle_breakdown_stop_loss_short,
    ma_tangle_breakout_hold_exit_signal,
    ma_tangle_breakout_long_entry,
    ma_tangle_breakout_reentry_signal,
    ma_tangle_breakout_stop_loss_long,
    should_downgrade_stop_basis_to_ma5,
    should_downgrade_stop_basis_to_short_term,
    should_upgrade_to_long_term_stage,
    should_upgrade_to_long_term_stage_short,
    single_ma_short_term_long_strategy,
    swing_profit_take_exit_via_ma10_long,
    swing_profit_take_exit_via_ma5_short,
    triple_ma_batch_exit_thirds_long,
)


def test_single_ma_short_term_long_strategy_entry_exit_and_profit_take():
    # R-MA-22: 進場守回檔後突破MA5+前一日高點；出場守跌破MA5；停利需先達10%波段獲利門檻
    close = pd.Series([10.0, 11.0, 12.0, 10.5])
    high = pd.Series([9.5, 10.5, 11.5, 12.0])
    ma5 = pd.Series([9.0, 10.0, 11.0, 11.0])
    ma20 = pd.Series([8.0, 8.0, 8.0, 8.0])
    is_bull_trend = pd.Series([True, True, True, True])

    result = single_ma_short_term_long_strategy(close, high, ma5, ma20, is_bull_trend, entry_price=10.0)

    assert result["entry_signal"].tolist() == [False, True, True, False]
    assert result["exit_signal"].tolist() == [False, False, False, True]
    # 第4天(index3)：波段最高收盤12已達10%門檻(11)，且當日收盤10.5跌破MA5(11) -> 停利觸發
    assert result["profit_take_exit"].tolist() == [False, False, False, True]
    # 20%波段獲利保護門檻(12.0)：cummax在第3天(index2)才達到12，且close(12)未跌破MA5(11) -> 尚未觸發；
    # 第4天才跌破MA5，此時才觸發
    assert result["swing_profit_guard"].tolist() == [False, False, False, True]


def test_single_ma_short_term_long_strategy_no_profit_take_before_target_reached():
    # 波段獲利未達10%門檻前，即使跌破MA5也不因此規則出場停利（仍可能因exit_signal出場，但profit_take_exit應為False）
    close = pd.Series([10.0, 10.2, 9.8])
    high = pd.Series([9.5, 10.1, 10.3])
    ma5 = pd.Series([9.0, 10.0, 10.0])
    ma20 = pd.Series([8.0, 8.0, 8.0])
    is_bull_trend = pd.Series([True, True, True])

    result = single_ma_short_term_long_strategy(close, high, ma5, ma20, is_bull_trend, entry_price=10.0)
    assert not result["profit_take_exit"].any()


def test_dual_ma_long_term_long_strategy_entry_on_golden_cross_multi_aligned():
    # R-MA-28: MA10/MA20黃金交叉且多排向上進場；死亡交叉且空排向下出場
    ma5 = pd.Series([9.0, 9.0, 9.0, 9.0])
    ma10 = pd.Series([9.0, 10.0, 11.0, 9.0])
    ma20 = pd.Series([10.0, 10.0, 10.0, 10.0])
    close = pd.Series([10.0, 10.0, 10.0, 10.0])

    result = dual_ma_long_term_long_strategy(close, ma5, ma10, ma20)
    assert result["entry_signal"].tolist() == [False, False, True, False]
    assert result["exit_signal"].tolist() == [False, False, False, True]


def test_triple_ma_batch_exit_thirds_long_accumulates_as_price_breaks_each_line():
    # R-MA-30(9a)：依序跌破MA5/MA10/MA20，應付出清比例分別是1/3, 2/3, 1.0
    close = pd.Series([12.0, 10.5, 9.5, 8.5])
    ma5 = pd.Series([11.0] * 4)
    ma10 = pd.Series([10.0] * 4)
    ma20 = pd.Series([9.0] * 4)

    fraction = triple_ma_batch_exit_thirds_long(close, ma5, ma10, ma20)
    assert fraction.tolist() == pytest.approx([0.0, 1 / 3, 2 / 3, 1.0])


def test_ma_tangle_breakout_long_entry_requires_volume_spike():
    # R-MA-17: 前一日仍糾結+收盤突破糾結區間高點+放量(相對前一日)+中長紅K
    close = pd.Series([105.0, 106.0])
    volume = pd.Series([100.0, 200.0])
    was_converged = pd.Series([True, True])
    convergence_high = pd.Series([104.0, 104.0])
    is_long_red = pd.Series([True, True])
    result = ma_tangle_breakout_long_entry(close, volume, was_converged, convergence_high, is_long_red)
    assert result.tolist() == [False, True]  # index0無前一日量能可比較，不成立


def test_ma_tangle_breakout_stop_loss_long_small_vs_big_candle():
    # 小紅K(漲幅<3.5%)改用收盤下跌5%停損；否則用進場K線最低點
    assert ma_tangle_breakout_stop_loss_long(entry_open=100, entry_close=102, entry_low=98) == pytest.approx(102 * 0.95)
    assert ma_tangle_breakout_stop_loss_long(entry_open=100, entry_close=106, entry_low=98) == 98


def test_ma_tangle_breakout_hold_exit_and_reentry():
    close = pd.Series([100.0, 95.0, 105.0])
    low = pd.Series([98.0, 90.0, 100.0])
    high = pd.Series([104.0, 104.0, 104.0])
    assert ma_tangle_breakout_hold_exit_signal(close, low).tolist() == [False, True, False]
    reentry_close = pd.Series([100.0, 106.0])
    reentry_high = pd.Series([104.0, 104.0])
    assert ma_tangle_breakout_reentry_signal(reentry_close, reentry_high).tolist() == [False, True]


def test_ma_tangle_breakdown_short_entry_and_stop_loss_mirror_breakout():
    close = pd.Series([95.0, 94.0])
    volume = pd.Series([100.0, 200.0])
    was_converged = pd.Series([True, True])
    convergence_low = pd.Series([96.0, 96.0])
    is_long_black = pd.Series([True, True])
    result = ma_tangle_breakdown_short_entry(close, volume, was_converged, convergence_low, is_long_black)
    assert result.tolist() == [False, True]

    assert ma_tangle_breakdown_stop_loss_short(entry_open=100, entry_close=98, entry_high=102) == pytest.approx(98 * 1.05)
    assert ma_tangle_breakdown_stop_loss_short(entry_open=100, entry_close=94, entry_high=102) == 102


def test_ma_tangle_breakdown_hold_exit_signal():
    close = pd.Series([100.0, 105.0, 95.0])
    high = pd.Series([102.0, 108.0, 100.0])
    assert ma_tangle_breakdown_hold_exit_signal(close, high).tolist() == [False, True, False]


def test_long_term_long_entry_and_watch_exit():
    # R-MA-26 戰法5：站上MA20且在MA60之上、MA10/MA20已黃金交叉多排上揚才進場
    close = pd.Series([21.0, 19.5, 20.0])
    ma20 = pd.Series([20.0, 20.0, 20.5])
    ma60 = pd.Series([18.0, 18.0, 18.0])
    golden_cross_bullish = pd.Series([True, True, True])
    entry = long_term_long_entry_signal(close, ma20, ma60, golden_cross_bullish)
    assert entry.tolist() == [True, False, False]  # 第2、3天皆未站上MA20

    # 跌破MA20但仍在MA60之上 -> 賣出觀望
    exit_signal = long_term_long_watch_exit_signal(close, ma20, ma60)
    assert exit_signal.tolist() == [False, True, True]


def test_should_upgrade_to_long_term_stage():
    assert should_upgrade_to_long_term_stage(True, True, True, True) is True
    assert should_upgrade_to_long_term_stage(True, True, False, True) is False


def test_swing_profit_take_exit_via_ma10_long_requires_profit_and_bias():
    close = pd.Series([100.0, 121.0, 118.0])
    ma10 = pd.Series([110.0, 119.0, 119.0])
    bias_over = pd.Series([False, True, True])
    result = swing_profit_take_exit_via_ma10_long(close, ma10, entry_price=100.0, bias_over_threshold=bias_over)
    # 第2天(index1)：cummax=121已達20%門檻，乖離達標，但收盤121未跌破ma10(119) -> False
    # 第3天(index2)：收盤118跌破ma10(119)，且已達獲利門檻與乖離門檻 -> True
    assert result.tolist() == [False, False, True]


def test_should_downgrade_stop_basis_to_ma5():
    assert should_downgrade_stop_basis_to_ma5(price_doubled_from_base=True, in_late_stage_rally=False) is True
    assert should_downgrade_stop_basis_to_ma5(False, False) is False


def test_long_term_short_entry_and_watch_exit_mirrors_long():
    # R-MA-27 戰法6：跌破MA20且在MA60之下、MA10/MA20已死亡交叉空排下彎才做空
    close = pd.Series([19.0, 20.5, 20.0])
    ma20 = pd.Series([20.0, 20.0, 19.5])
    ma60 = pd.Series([22.0, 22.0, 22.0])
    death_cross_bearish = pd.Series([True, True, True])
    entry = long_term_short_entry_signal(close, ma20, ma60, death_cross_bearish)
    assert entry.tolist() == [True, False, False]

    exit_signal = long_term_short_watch_exit_signal(close, ma20, ma60)
    assert exit_signal.tolist() == [False, True, True]


def test_should_upgrade_to_long_term_stage_short():
    assert should_upgrade_to_long_term_stage_short(True, True, True, True) is True
    assert should_upgrade_to_long_term_stage_short(False, True, True, True) is False


def test_swing_profit_take_exit_via_ma5_short_requires_profit_threshold():
    close = pd.Series([100.0, 79.0, 82.0])
    ma5 = pd.Series([90.0, 80.0, 80.0])
    result = swing_profit_take_exit_via_ma5_short(close, ma5, entry_price=100.0)
    # 第2天(index1)：cummin=79已達20%獲利門檻，但收盤79未突破ma5(80) -> False
    # 第3天(index2)：收盤82突破ma5(80)，且已達獲利門檻 -> True
    assert result.tolist() == [False, False, True]


def test_should_downgrade_stop_basis_to_short_term():
    assert should_downgrade_stop_basis_to_short_term(price_halved_from_top=True, in_late_stage_decline=False) is True
    assert should_downgrade_stop_basis_to_short_term(False, False) is False
