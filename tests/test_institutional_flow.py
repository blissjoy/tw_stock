from src.indicators.institutional_flow import INSTITUTIONAL_STREAK_THRESHOLD, classify_flow_streak


def test_classify_flow_streak_detects_sell_warning_at_threshold():
    """最近3天(index0~2)都賣超(負值)，第4天以前轉買超——連續賣超天數應該剛好3天，
    達到朱家泓淘汰法R-SCREEN-06的門檻，觸發停損警示。"""
    net = [-100, -200, -50, 300]
    result = classify_flow_streak(net)
    assert result["direction"] == "sell"
    assert result["streak_days"] == 3
    assert result["is_sell_warning"] is True
    assert result["is_buy_watch"] is False


def test_classify_flow_streak_detects_buy_watch_at_threshold():
    """對稱案例：連續3天買超，達到陳家豐書中投信連續加碼3~5天的下限門檻。"""
    net = [100, 50, 200, -300]
    result = classify_flow_streak(net)
    assert result["direction"] == "buy"
    assert result["streak_days"] == 3
    assert result["is_buy_watch"] is True
    assert result["is_sell_warning"] is False


def test_classify_flow_streak_below_threshold_no_warning():
    net = [-100, -200, 300]  # 只連續賣超2天，還沒到3天門檻
    result = classify_flow_streak(net)
    assert result["streak_days"] == 2
    assert result["is_sell_warning"] is False


def test_classify_flow_streak_today_flat_resets_to_zero():
    net = [0, -100, -200, -300]
    result = classify_flow_streak(net)
    assert result["direction"] == "flat"
    assert result["streak_days"] == 0
    assert result["is_sell_warning"] is False
    assert result["is_buy_watch"] is False


def test_classify_flow_streak_empty_returns_none_direction():
    result = classify_flow_streak([])
    assert result["direction"] is None
    assert result["streak_days"] == 0


def test_classify_flow_streak_all_same_direction_full_history():
    net = [10, 20, 30, 40, 50]
    result = classify_flow_streak(net)
    assert result["streak_days"] == 5
    assert result["is_buy_watch"] is True


def test_classify_flow_streak_threshold_constant_is_three():
    """回歸測試：門檻常數本身要保持3(朱家泓明確3天、陳家豐3~5天取下限)，如果之後
    有人改了這個常數，至少要意識到同時牽動兩本書的引用依據。"""
    assert INSTITUTIONAL_STREAK_THRESHOLD == 3
