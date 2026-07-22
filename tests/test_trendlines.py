import pandas as pd

from src.indicators.trendlines import (
    LinePoint,
    TrendLine,
    angle_strength,
    angle_strength_down,
    check_channel_breakdown,
    check_channel_breakout,
    check_down_rapid_line_cover,
    check_up_rapid_line_exit,
    classify_break_and_role_swap,
    classify_down_line_breakout_and_role_swap,
    consolidation_watch_resolution,
    draw_down_channel_line,
    draw_down_rapid_tangent_line,
    draw_up_channel_line,
    draw_up_rapid_tangent_line,
    exit_observation_period_state,
    find_down_tangent_line,
    find_up_tangent_line,
    identify_original_down_tangent,
    identify_original_up_tangent,
    resistance_strength_by_touch_count,
    support_strength_by_touch_count,
    update_down_sub_tangent_lines,
    update_up_sub_tangent_lines,
)


def test_find_up_tangent_line_falls_back_to_older_pair_when_newest_pair_invalid():
    # R-LINE-01: 最新一組(P1,P2)不滿足「底底高」(11<=12)，須回退用較舊一組(P0,P1)
    bottoms = [LinePoint(0, 10), LinePoint(2, 12), LinePoint(4, 11)]
    low = pd.Series([10.0, 11.5, 12.0, 9.0, 11.0])
    high = pd.Series([12.0, 13.0, 14.0, 11.0, 13.0])
    line = find_up_tangent_line(bottoms, high, low)
    assert line is not None
    assert (line.a, line.b) == (LinePoint(0, 10), LinePoint(2, 12))
    assert line.role == "support"


def test_find_up_tangent_line_rejects_line_covering_bar():
    # 線不蓋線：A、B之間有K棒(低點)被切線切過，此配對不合法
    bottoms = [LinePoint(0, 10), LinePoint(2, 12)]
    low = pd.Series([10.0, 5.0, 12.0])  # index1的低點5遠低於切線值11，切線被壓穿
    high = pd.Series([12.0, 13.0, 14.0])
    assert find_up_tangent_line(bottoms, high, low) is None


def test_find_down_tangent_line_mirrors_up():
    # R-LINE-02: 與上升切線完全對稱
    tops = [LinePoint(0, 20), LinePoint(2, 18)]
    high = pd.Series([20.0, 18.5, 18.0])  # index1未突破切線(19)，合法
    low = pd.Series([18.0, 17.0, 16.0])
    line = find_down_tangent_line(tops, high, low)
    assert (line.a, line.b) == (LinePoint(0, 20), LinePoint(2, 18))
    assert line.role == "resistance"


def test_identify_original_up_tangent_bull_confirmed():
    bottoms = [LinePoint(0, 10.0), LinePoint(2, 12.0)]
    low = pd.Series([10.0, 11.0, 12.0, 14.0])
    high = pd.Series([12.0, 13.0, 14.0, 16.0])
    close = pd.Series([11.0, 11.5, 13.0, 15.0])
    result = identify_original_up_tangent(bottoms, high, low, close, trend_state="空頭")
    assert result["status"] == "多頭確認：盤整向上突破原始上升切線"


def test_identify_original_up_tangent_bear_continuation():
    bottoms = [LinePoint(0, 10.0), LinePoint(2, 12.0)]
    low = pd.Series([10.0, 11.0, 12.0, 8.0])
    high = pd.Series([12.0, 13.0, 14.0, 10.0])
    close = pd.Series([11.0, 11.5, 13.0, 9.0])
    result = identify_original_up_tangent(bottoms, high, low, close, trend_state="空頭")
    assert result["status"] == "空頭續跌：盤整向下跌破原始上升切線（下頸線失守）"


def test_identify_original_up_tangent_failed_reversal():
    bottoms = [LinePoint(0, 10.0), LinePoint(2, 12.0)]
    low = pd.Series([10.0, 11.0, 12.0, 14.0, 11.0])
    high = pd.Series([12.0, 13.0, 14.0, 16.0, 13.0])
    close = pd.Series([11.0, 11.5, 13.0, 15.0, 12.0])  # 先突破確認多頭，隨後又跌破原始上升切線
    result = identify_original_up_tangent(bottoms, high, low, close, trend_state="空頭")
    assert result["status"] == "反轉失敗：空頭ABC反彈修正結束，空頭續跌"


def test_identify_original_up_tangent_only_applies_during_bear_trend():
    bottoms = [LinePoint(0, 10.0), LinePoint(2, 12.0)]
    low = pd.Series([10.0, 11.0, 12.0])
    high = pd.Series([12.0, 13.0, 14.0])
    close = pd.Series([11.0, 11.5, 13.0])
    assert identify_original_up_tangent(bottoms, high, low, close, trend_state="多頭") is None


def test_identify_original_down_tangent_bear_confirmed():
    # R-LINE-04: 與R-LINE-03完全對稱
    tops = [LinePoint(0, 20.0), LinePoint(2, 18.0)]
    high = pd.Series([20.0, 19.0, 18.0, 16.0])
    low = pd.Series([18.0, 17.0, 16.0, 14.0])
    close = pd.Series([19.0, 18.5, 17.0, 15.0])
    result = identify_original_down_tangent(tops, high, low, close, trend_state="多頭")
    assert result["status"] == "空頭確認：盤整向下跌破原始下降切線（上頸線失守）"


def test_update_up_sub_tangent_lines_appends_new_valid_pair():
    # R-LINE-05: 每出現新的底底高配對就動態append一條新切線
    low = pd.Series([10.0, 11.0, 12.0])
    high = pd.Series([12.0, 13.0, 14.0])
    lines = update_up_sub_tangent_lines([LinePoint(0, 10), LinePoint(2, 12)], high, low, [])
    assert len(lines) == 1
    assert (lines[0].a, lines[0].b) == (LinePoint(0, 10), LinePoint(2, 12))

    # 新一組底底高出現，追加第2條
    low2 = pd.Series([10.0, 11.0, 12.0, 13.0, 13.0])
    high2 = pd.Series([12.0, 13.0, 14.0, 15.0, 15.0])
    lines = update_up_sub_tangent_lines([LinePoint(0, 10), LinePoint(2, 12), LinePoint(4, 13)], high2, low2, lines)
    assert len(lines) == 2
    assert (lines[1].a, lines[1].b) == (LinePoint(2, 12), LinePoint(4, 13))


def test_update_up_sub_tangent_lines_no_update_when_not_higher():
    low = pd.Series([10.0, 9.0, 8.0])
    high = pd.Series([12.0, 11.0, 10.0])
    lines = update_up_sub_tangent_lines([LinePoint(0, 10), LinePoint(2, 8)], high, low, [])
    assert lines == []


def test_update_down_sub_tangent_lines_mirrors_up():
    high = pd.Series([20.0, 19.0, 18.0])
    low = pd.Series([18.0, 17.0, 16.0])
    lines = update_down_sub_tangent_lines([LinePoint(0, 20), LinePoint(2, 18)], high, low, [])
    assert len(lines) == 1
    assert lines[0].role == "resistance"


def test_support_strength_by_touch_count():
    assert support_strength_by_touch_count(1) == "支撐最強"
    assert support_strength_by_touch_count(2) == "支撐約50%成功率"
    assert support_strength_by_touch_count(3) == "容易被跌破"
    assert support_strength_by_touch_count(5) == "容易被跌破"


def test_angle_strength_compares_consecutive_slopes():
    l1 = TrendLine(LinePoint(0, 10), LinePoint(2, 12))    # slope=1
    l2 = TrendLine(LinePoint(2, 12), LinePoint(3, 15))    # slope=3
    l3 = TrendLine(LinePoint(3, 15), LinePoint(4, 15.5))  # slope=0.5
    result = angle_strength([l1, l2, l3])
    assert result == ["走勢轉強（角度變陡）", "走勢轉弱（角度變平）"]


def test_classify_break_and_role_swap_escalates_with_more_broken_lines():
    close = pd.Series([0.0] * 11)

    l_old = TrendLine(LinePoint(0, 5), LinePoint(1, 5))
    l_mid = TrendLine(LinePoint(0, 8), LinePoint(1, 8))
    l_new = TrendLine(LinePoint(0, 12), LinePoint(1, 12))
    close.iloc[10] = 13.0
    result = classify_break_and_role_swap([l_old, l_mid, l_new], 10, close)
    assert result["status"] == "未跌破，多頭結構維持"
    assert l_new.role == "support"

    l_old2 = TrendLine(LinePoint(0, 5), LinePoint(1, 5))
    l_mid2 = TrendLine(LinePoint(0, 8), LinePoint(1, 8))
    l_new2 = TrendLine(LinePoint(0, 12), LinePoint(1, 12))
    close.iloc[10] = 6.0
    result2 = classify_break_and_role_swap([l_old2, l_mid2, l_new2], 10, close)
    assert result2["status"] == "連續跌破2條上升切線（含更早的切線）：趨勢反轉警訊，警示等級隨跌破條數升高"
    assert l_new2.role == "resistance"
    assert l_mid2.role == "resistance"
    assert l_old2.role == "support"  # 未被跌破，角色不變


def test_resistance_strength_by_touch_count():
    assert resistance_strength_by_touch_count(1) == "壓力最強"
    assert resistance_strength_by_touch_count(2) == "壓力約50%成功率"
    assert resistance_strength_by_touch_count(3) == "容易被突破"


def test_angle_strength_down_is_not_mirror_of_up():
    # R-LINE-10: 下降切線角度變陡(斜率絕對值變大)反而是「轉弱」，與上升切線邏輯相反，按書中原文字面實作
    l1 = TrendLine(LinePoint(0, 20), LinePoint(2, 16))   # slope=-2
    l2 = TrendLine(LinePoint(2, 16), LinePoint(3, 10))   # slope=-6，角度變陡
    l3 = TrendLine(LinePoint(3, 10), LinePoint(4, 9))    # slope=-1，角度趨緩
    result = angle_strength_down([l1, l2, l3])
    assert result == ["走勢轉弱（下降角度變陡，按書中原文字面）", "走勢轉強（下降角度趨緩，按書中原文字面）"]


def test_classify_down_line_breakout_and_role_swap_escalates():
    close = pd.Series([0.0] * 11)

    def make_lines():
        return (
            TrendLine(LinePoint(0, 20), LinePoint(1, 20), role="resistance"),
            TrendLine(LinePoint(0, 15), LinePoint(1, 15), role="resistance"),
            TrendLine(LinePoint(0, 10), LinePoint(1, 10), role="resistance"),
        )

    l_old, l_mid, l_new = make_lines()
    close.iloc[10] = 8.0
    result = classify_down_line_breakout_and_role_swap([l_old, l_mid, l_new], 10, close)
    assert result["status"] == "未突破，空頭結構維持"
    assert l_new.role == "resistance"

    l_old2, l_mid2, l_new2 = make_lines()
    close.iloc[10] = 17.0
    result2 = classify_down_line_breakout_and_role_swap([l_old2, l_mid2, l_new2], 10, close)
    assert "連續突破2條" in result2["status"]
    assert l_new2.role == "support"
    assert l_mid2.role == "support"
    assert l_old2.role == "resistance"


def test_exit_observation_period_state_priority():
    # R-LINE-13: 原方向確認 > 反轉確認 > 盤整 > 預設仍是退出觀察期
    assert exit_observation_period_state(resumes_original_direction=True) == "原方向確認，依原方向進場"
    assert exit_observation_period_state(reverses_trend_confirmed=True) == "反轉確認，依新方向進場"
    assert exit_observation_period_state(enters_consolidation=True) == "轉入盤整，退出觀察期延續"
    assert exit_observation_period_state() == "退出觀察期，尚無確認訊號，不可進場"


def test_consolidation_watch_resolution():
    assert consolidation_watch_resolution(breaks_upward=True) == "盤整向上突破，依多頭方向進場"
    assert consolidation_watch_resolution(breaks_downward=True) == "盤整向下跌破，依空頭方向進場"
    assert consolidation_watch_resolution() is None


def test_draw_up_channel_line_and_breakout():
    # R-LINE-14: 取切線A、B之間的相對最高點M，過M畫平行線
    tangent = TrendLine(LinePoint(0, 10), LinePoint(4, 14))  # slope=1
    high = pd.Series([0.0, 11.0, 15.0, 12.0, 0.0, 0.0])
    channel = draw_up_channel_line(tangent, high)
    assert channel.a == LinePoint(2, 15.0)
    assert channel.slope == 1.0

    close = pd.Series([0.0] * 5 + [19.0])
    assert check_channel_breakout(channel, 5, close) == True
    close_no_break = pd.Series([0.0] * 5 + [17.0])
    assert check_channel_breakout(channel, 5, close_no_break) == False


def test_draw_down_channel_line_and_breakdown():
    # R-LINE-15: 取切線A、B之間的相對最低點M，過M畫平行線
    tangent = TrendLine(LinePoint(0, 20), LinePoint(4, 16))  # slope=-1
    low = pd.Series([0.0, 19.0, 15.0, 18.0, 0.0, 0.0])
    channel = draw_down_channel_line(tangent, low)
    assert channel.a == LinePoint(2, 15.0)
    assert channel.slope == -1.0

    close = pd.Series([0.0] * 5 + [11.0])
    assert check_channel_breakdown(channel, 5, close) == True


def test_draw_up_rapid_tangent_line_and_exit():
    # R-LINE-07: 急漲區間內取第一個與最新低點連線，跌破即短線停利
    bottoms = [LinePoint(0, 10.0), LinePoint(1, 12.0), LinePoint(2, 14.0)]
    low = pd.Series([10.0, 12.0, 14.0])
    high = pd.Series([11.0, 13.0, 15.0])
    line = draw_up_rapid_tangent_line(bottoms, high, low)
    assert line is not None
    assert (line.a, line.b) == (LinePoint(0, 10.0), LinePoint(2, 14.0))
    assert line.role == "support"

    # line.at(3) = 10 + slope(2)*3 = 16
    close_below = pd.Series([0.0, 0.0, 0.0, 15.0])
    assert check_up_rapid_line_exit(line, 3, close_below) == True
    close_above = pd.Series([0.0, 0.0, 0.0, 18.0])
    assert check_up_rapid_line_exit(line, 3, close_above) == False


def test_draw_up_rapid_tangent_line_needs_at_least_two_points():
    assert draw_up_rapid_tangent_line([LinePoint(0, 10.0)], pd.Series([11.0]), pd.Series([10.0])) is None


def test_draw_down_rapid_tangent_line_and_cover():
    # R-LINE-08: 急跌區間內取第一個與最新高點連線，突破即空單短線回補
    tops = [LinePoint(0, 20.0), LinePoint(1, 18.0), LinePoint(2, 16.0)]
    high = pd.Series([20.0, 18.0, 16.0])
    low = pd.Series([19.0, 17.0, 15.0])
    line = draw_down_rapid_tangent_line(tops, high, low)
    assert line is not None
    assert (line.a, line.b) == (LinePoint(0, 20.0), LinePoint(2, 16.0))
    assert line.role == "resistance"

    # line.at(3) = 20 + slope(-2)*3 = 14
    close_above = pd.Series([0.0, 0.0, 0.0, 15.0])
    assert check_down_rapid_line_cover(line, 3, close_above) == True
    close_below = pd.Series([0.0, 0.0, 0.0, 13.0])
    assert check_down_rapid_line_cover(line, 3, close_below) == False


def test_draw_down_rapid_tangent_line_needs_at_least_two_points():
    assert draw_down_rapid_tangent_line([LinePoint(0, 20.0)], pd.Series([20.0]), pd.Series([19.0])) is None
