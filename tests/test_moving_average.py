import pandas as pd

from src.indicators.moving_average import (
    aligned_line_count,
    bias_extreme_warning,
    compute_ma_set,
    holder_profit_state,
    is_bearish_aligned,
    is_bearish_aligned_strict,
    is_bias_unreliable_for_stock,
    is_bullish_aligned,
    is_bullish_aligned_strict,
    is_short_term_bearish_setup,
    is_short_term_bullish_setup,
    bias_ratio,
    classify_bias,
    is_ma_converged,
    is_ma_tangled,
    ma_convergence_line_count,
    ma_direction,
    ma_influence_strength,
    ma_resistance_state,
    ma_strategy_stop_loss_long,
    ma_strategy_stop_loss_short,
    ma_support_state,
    ma_weight,
    offset_values,
    predict_ma_turn,
    select_ma_periods,
    sma,
)


def test_sma_matches_hand_calculation():
    # R-MA-01: MA(N,t) = SUM(Close[t-N+1..t]) / N，明確用收盤價，非OHLC平均
    close = pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
    ma5 = sma(close, 5)

    assert ma5.iloc[:4].isna().all()  # 前4天資料不足5天，應為 NaN
    assert ma5.iloc[4] == (10 + 11 + 12 + 13 + 14) / 5  # = 12.0
    assert ma5.iloc[-1] == (16 + 17 + 18 + 19 + 20) / 5  # = 18.0


def test_bullish_aligned_requires_all_shorter_above_longer():
    # R-MA-08: MA5 > MA10 > MA20 才算多頭排列，缺一不可
    ma_frame = pd.DataFrame(
        {
            "MA5": [12, 12, 8],
            "MA10": [11, 13, 9],
            "MA20": [10, 10, 10],
        }
    )
    result = is_bullish_aligned(ma_frame, periods=(5, 10, 20))
    assert result.tolist() == [True, False, False]


def test_bearish_aligned_requires_all_shorter_below_longer():
    # R-MA-09: MA5 < MA10 < MA20 才算空頭排列
    ma_frame = pd.DataFrame(
        {
            "MA5": [8, 12, 8],
            "MA10": [9, 11, 13],
            "MA20": [10, 10, 10],
        }
    )
    result = is_bearish_aligned(ma_frame, periods=(5, 10, 20))
    assert result.tolist() == [True, False, False]


def test_bullish_aligned_strict_requires_upward_direction_too():
    # 加強版多頭排列：順序正確 + 每條均線方向皆向上
    ma_frame = pd.DataFrame(
        {
            "MA5": [10, 12, 11],   # 第3天(index2)方向轉下
            "MA10": [8, 9, 8.5],
            "MA20": [5, 6, 6.5],
        }
    )
    loose = is_bullish_aligned(ma_frame)
    strict = is_bullish_aligned_strict(ma_frame)
    assert loose.tolist() == [True, True, True]  # 順序皆正確
    # index0 沒有前一天可比較方向 -> shift(1) NaN -> False；index1方向向上皆True；index2 MA5方向轉下 -> False
    assert strict.tolist() == [False, True, False]


def test_bearish_aligned_strict_requires_downward_direction_too():
    ma_frame = pd.DataFrame(
        {
            "MA5": [12, 10, 11],   # 第3天(index2)方向轉上
            "MA10": [13, 12, 12.5],
            "MA20": [15, 14, 14.5],
        }
    )
    strict = is_bearish_aligned_strict(ma_frame)
    assert strict.tolist() == [False, True, False]


def test_aligned_line_count_counts_consecutive_bullish_lines_from_short_end():
    # MA5>MA10>MA20 成立(3線)，但 MA20<MA60 不成立 -> 應停在3線多排，不繼續數MA60
    ma_frame = pd.DataFrame(
        {
            "MA5": [40],
            "MA10": [30],
            "MA20": [20],
            "MA60": [25],  # MA20(20) < MA60(25)，多排在此中斷
        }
    )
    count = aligned_line_count(ma_frame, periods=(5, 10, 20, 60), direction="bullish")
    assert count.iloc[0] == 3


def test_compute_ma_set_returns_expected_columns():
    close = pd.Series(range(1, 31), dtype=float)
    ma_set = compute_ma_set(close, periods=(5, 10, 20))
    assert list(ma_set.columns) == ["MA5", "MA10", "MA20"]
    assert ma_set["MA20"].iloc[19] == pd.Series(range(1, 21)).mean()


def test_holder_profit_state_compares_close_to_ma():
    # R-MA-02: 均線代表平均持有成本，收盤價高於/低於/等於均線 -> 獲利/虧損/損平
    close = pd.Series([12.0, 8.0, 10.0, None])
    ma = pd.Series([10.0, 10.0, 10.0, 10.0])
    state = holder_profit_state(close, ma)
    assert state.iloc[0] == "獲利"
    assert state.iloc[1] == "虧損"
    assert state.iloc[2] == "損平"
    assert pd.isna(state.iloc[3])


def test_ma_weight_is_inverse_of_period():
    # R-MA-03: 單日漲跌對N日均線的影響權重 = 1/N，天期越短權重越大
    assert ma_weight(5) == 0.2
    assert ma_weight(20) == 0.05
    assert ma_weight(5) > ma_weight(20)


def test_ma_direction_classifies_up_down_flat():
    ma = pd.Series([10.0, 12.0, 12.0, 9.0])
    direction = ma_direction(ma)
    assert pd.isna(direction.iloc[0])
    assert direction.iloc[1] == "上揚"
    assert direction.iloc[2] == "走平"
    assert direction.iloc[3] == "下彎"


def test_offset_values_only_returns_known_history():
    # R-MA-05: 扣抵值 = Close[as_of + k - n]，只有來源日期 <= as_of 才算「今天已知」
    close = pd.Series([10, 11, 12, 13, 14, 15, 16], dtype=float)  # index 0..6
    # as_of=4 (今天), n=5: k=1 -> source=4-5+1=0(已知,=10); k=2 -> source=1(已知,=11)
    # k=5 -> source=4(=as_of自己,已知,=14); k=6 -> source=5(未來,不應回傳)
    offsets = offset_values(close, n=5, as_of=4, max_k=6)
    assert offsets[1] == 10
    assert offsets[2] == 11
    assert offsets[5] == 14
    assert 6 not in offsets


def test_predict_ma_turn_compares_assumed_close_to_offset_value():
    assert predict_ma_turn(assumed_close=15, offset_value=10) == "上彎"
    assert predict_ma_turn(assumed_close=8, offset_value=10) == "下彎"
    assert predict_ma_turn(assumed_close=10, offset_value=10) == "走平"


def test_ma_strategy_stop_loss_long_uses_5pct_threshold():
    # R-MA-21: 進場紅K漲幅>=5% -> 用當根低點；<5% -> 用進場後轉折低點
    big_gain_stop = ma_strategy_stop_loss_long(
        entry_open=100, entry_close=106, entry_low=98, swing_low_after_entry=101
    )
    assert big_gain_stop == 98  # 漲幅6% >= 5%，用當根低點，忽略轉折低點

    small_gain_stop = ma_strategy_stop_loss_long(
        entry_open=100, entry_close=102, entry_low=98, swing_low_after_entry=101
    )
    assert small_gain_stop == 101  # 漲幅2% < 5%，用轉折低點

    no_swing_yet = ma_strategy_stop_loss_long(
        entry_open=100, entry_close=102, entry_low=98, swing_low_after_entry=None
    )
    assert no_swing_yet == 98  # 尚未走出轉折點，退回進場當根低點


def test_ma_strategy_stop_loss_short_mirrors_long():
    big_loss_stop = ma_strategy_stop_loss_short(
        entry_open=100, entry_close=94, entry_high=102, swing_high_after_entry=99
    )
    assert big_loss_stop == 102  # 跌幅6% >= 5%，用當根高點

    small_loss_stop = ma_strategy_stop_loss_short(
        entry_open=100, entry_close=98, entry_high=102, swing_high_after_entry=99
    )
    assert small_loss_stop == 99  # 跌幅2% < 5%，用轉折高點


def test_is_ma_converged_and_line_count():
    close = pd.Series([100.0, 100.0])
    ma_frame = pd.DataFrame({"MA5": [99.0, 99.0], "MA10": [100.0, 100.0], "MA20": [101.0, 150.0]})
    result = is_ma_converged(ma_frame, close, periods=(5, 10, 20))
    assert result.tolist() == [True, False]

    close2 = pd.Series([100.0])
    ma_frame2 = pd.DataFrame({"MA5": [100.0], "MA10": [100.5], "MA20": [101.0], "MA60": [110.0]})
    count = ma_convergence_line_count(ma_frame2, close2, periods=(5, 10, 20, 60))
    assert count.iloc[0] == 3  # MA5/MA10/MA20糾結，MA60偏離超過門檻不列入


def test_is_ma_tangled_neither_bullish_nor_bearish():
    ma_frame = pd.DataFrame({"MA5": [12.0, 8.0, 10.0], "MA10": [11.0, 9.0, 10.0], "MA20": [10.0, 10.0, 10.0]})
    result = is_ma_tangled(ma_frame, periods=(5, 10, 20))
    assert result.tolist() == [False, False, True]


def test_bias_ratio_and_classify_bias():
    close = pd.Series([110.0, 90.0, 100.0])
    ma_n = pd.Series([100.0, 100.0, 100.0])
    assert bias_ratio(close, ma_n).tolist() == [10.0, -10.0, 0.0]
    assert classify_bias(close, ma_n).tolist() == ["正乖離", "負乖離", "無乖離（貼近均線）"]


def test_select_ma_periods_by_horizon():
    # R-MA-04: 依交易策略時間長度選用均線天期
    assert select_ma_periods("長期策略") == ("週線", (10, 20))
    assert select_ma_periods("中期策略") == ("日線", (20, 60))
    assert select_ma_periods("短期策略") == ("日線", (3, 5, 10))
    assert select_ma_periods("當沖策略") == ("分線", (1, 5, 15, 60))


def test_ma_support_state_transitions():
    # R-MA-06: 均線上彎的支撐/助漲判定
    close = pd.Series([10.0, 10.5, 9.5, 10.6])
    ma_n = pd.Series([9.0, 9.5, 10.0, 10.5])  # 全程上揚
    result = ma_support_state(close, ma_n)
    assert result.iloc[1] == "支撐：回檔止跌回升機率較高"  # close(10.5)>=ma(9.5)
    assert result.iloc[2] == "助漲：跌破後可望被拉回站上均線"  # 前一日close(10.5)>=ma(9.5)、本日跌破ma(10.0)
    assert result.iloc[3] == "支撐：回檔止跌回升機率較高"  # 本日close(10.6)>=ma(10.5)，供supported覆蓋
    ma_flat = pd.Series([10.0, 10.0, 10.0, 10.0])
    assert ma_support_state(close, ma_flat).iloc[1] == "無作用（均線非上揚）"


def test_ma_influence_strength_scales_with_period_and_slope():
    # 天期越長、斜率越大，強度越高（僅供相對比較）
    assert ma_influence_strength(20, 0.5) > ma_influence_strength(5, 0.5)
    assert ma_influence_strength(20, 1.0) > ma_influence_strength(20, 0.5)


def test_ma_resistance_state_transitions():
    # R-MA-07: 均線下彎的壓力/助跌判定，與R-MA-06鏡射對稱
    close = pd.Series([10.0, 9.5, 10.5, 9.4])
    ma_n = pd.Series([11.0, 10.5, 10.0, 9.5])  # 全程下彎
    result = ma_resistance_state(close, ma_n)
    assert result.iloc[1] == "壓力：反彈再度轉跌機率較高"  # close(9.5)<=ma(10.5)
    ma_flat = pd.Series([10.0, 10.0, 10.0, 10.0])
    assert ma_resistance_state(close, ma_flat).iloc[1] == "無作用（均線非下彎）"


def test_is_short_term_bullish_setup_requires_wave_pattern_and_3line_bullish():
    # R-MA-10: 波浪頭頭高底底高 + 3線多排皆向上 + 收盤站上MA20
    close = pd.Series([19.0, 21.0])
    ma5 = pd.Series([15.0, 16.0])
    ma10 = pd.Series([14.0, 15.0])
    ma20 = pd.Series([13.0, 14.0])
    wave = pd.Series([True, True])
    result = is_short_term_bullish_setup(close, ma5, ma10, ma20, wave)
    assert result.tolist() == [False, True]  # 第0天無前一日可比較方向，第1天成立

    wave_false = pd.Series([False, False])
    assert is_short_term_bullish_setup(close, ma5, ma10, ma20, wave_false).tolist() == [False, False]


def test_is_short_term_bullish_setup_strong_requires_4line():
    close = pd.Series([19.0, 21.0])
    ma5 = pd.Series([15.0, 16.0])
    ma10 = pd.Series([14.0, 15.0])
    ma20 = pd.Series([13.0, 14.0])
    ma60 = pd.Series([12.0, 13.0])
    wave = pd.Series([True, True])
    result = is_short_term_bullish_setup(close, ma5, ma10, ma20, wave, ma60=ma60)
    assert result.tolist() == [False, True]

    ma60_falling = pd.Series([13.0, 12.0])
    assert is_short_term_bullish_setup(close, ma5, ma10, ma20, wave, ma60=ma60_falling).tolist() == [False, False]


def test_is_short_term_bearish_setup_mirrors_bullish():
    # R-MA-11: 與R-MA-10完全對稱
    close = pd.Series([16.0, 14.0])
    ma5 = pd.Series([18.0, 17.0])
    ma10 = pd.Series([19.0, 18.0])
    ma20 = pd.Series([20.0, 19.0])
    wave = pd.Series([True, True])
    result = is_short_term_bearish_setup(close, ma5, ma10, ma20, wave)
    assert result.tolist() == [False, True]


def test_bias_extreme_warning_uses_ma_channel_threshold():
    # R-INDICATOR-19: 借用R-INDICATOR-18的MA通道12~15%上緣，預設15%
    bias = pd.Series([5.0, -16.0, 14.9, 15.0])
    assert bias_extreme_warning(bias).tolist() == [False, True, False, True]


def test_is_bias_unreliable_for_stock():
    assert is_bias_unreliable_for_stock(True) is True
    assert is_bias_unreliable_for_stock(False) is False
