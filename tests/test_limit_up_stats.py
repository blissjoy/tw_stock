from src.indicators.limit_up_stats import is_limit_up, next_day_event_stats, summarize_events


def test_is_limit_up_true_at_ten_percent():
    assert is_limit_up(prev_close=100.0, close=110.0) is True


def test_is_limit_up_true_within_threshold_buffer():
    assert is_limit_up(prev_close=100.0, close=109.7) is True  # 受跳動單位影響略低於10%


def test_is_limit_up_false_below_threshold():
    assert is_limit_up(prev_close=100.0, close=105.0) is False


def test_is_limit_up_false_when_prev_close_missing_or_invalid():
    assert is_limit_up(prev_close=None, close=110.0) is False
    assert is_limit_up(prev_close=0.0, close=110.0) is False


def test_next_day_event_stats_computes_pct_relative_to_reference_close():
    stats = next_day_event_stats(open_=102.0, high=105.0, low=99.0, close=101.0, reference_close=100.0)

    assert stats["open_pct"] == 0.02
    assert stats["high_pct"] == 0.05
    assert stats["low_pct"] == -0.01
    assert stats["close_pct"] == 0.01
    assert stats["is_red"] is True  # close(101) < open(102)


def test_next_day_event_stats_is_red_when_close_below_open():
    stats = next_day_event_stats(open_=105.0, high=106.0, low=100.0, close=101.0, reference_close=100.0)
    assert stats["is_red"] is True


def test_summarize_events_empty_returns_none_fields():
    result = summarize_events([])
    assert result["n"] == 0
    assert result["open_higher_rate"] is None
    assert result["red_rate"] is None


def test_summarize_events_matches_manual_average():
    events = [
        {"open_pct": 0.02, "high_pct": 0.04, "low_pct": -0.01, "close_pct": 0.005, "is_red": True},
        {"open_pct": -0.01, "high_pct": 0.01, "low_pct": -0.03, "close_pct": -0.02, "is_red": True},
    ]
    result = summarize_events(events)

    assert result["n"] == 2
    assert result["open_higher_rate"] == 0.5
    assert round(result["avg_open_pct"], 4) == round((0.02 - 0.01) / 2, 4)
    assert round(result["avg_amplitude"], 4) == round(((0.04 - (-0.01)) + (0.01 - (-0.03))) / 2, 4)
    assert result["red_rate"] == 1.0
