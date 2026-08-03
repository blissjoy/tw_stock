import pytest

from src.indicators.huang_chip_signals import (
    COLOR_BUY,
    COLOR_DEFAULT,
    COLOR_GRAY,
    COLOR_SELL,
    classify_holder_change,
    classify_institutional_streak,
    classify_ma_price_position,
    classify_weekly_volume_pattern,
    sum_institutional_flow_lots,
)

# ============================================================
# classify_institutional_streak (D/E)
# ============================================================


def test_classify_institutional_streak_empty_returns_blank():
    assert classify_institutional_streak([]) == {"text": "", "color": COLOR_DEFAULT}


def test_classify_institutional_streak_today_flat_returns_flat_label():
    assert classify_institutional_streak([0, 100, 100, 100]) == {"text": "持平", "color": COLOR_GRAY}


def test_classify_institutional_streak_n_ge_3_buy():
    assert classify_institutional_streak([10, 20, 30, -5, -5]) == {"text": "連買3天", "color": COLOR_BUY}


def test_classify_institutional_streak_n_ge_3_sell():
    assert classify_institutional_streak([-10, -20, -30, 5, 5]) == {"text": "連賣3天", "color": COLOR_SELL}


def test_classify_institutional_streak_n_lt_3_insufficient_history_falls_back_to_simple_label():
    """N<3但陣列已經沒有更早的資料可以判斷M，退回顯示簡單版(對應原JS的i>=dirs.length分支)。"""
    assert classify_institutional_streak([10, 20]) == {"text": "連買2天", "color": COLOR_BUY}


def test_classify_institutional_streak_n_lt_3_then_flat_is_undetermined():
    assert classify_institutional_streak([10, 0, -10, -10, -10]) == {"text": "方向未定", "color": COLOR_GRAY}


def test_classify_institutional_streak_reversal_m_ge_3_sell_then_buy():
    """今天+前2天買超(N=2<3)，緊接著3天賣超(M=3)→「連3賣後轉買」，顏色跟著今天的買方向(紅)。"""
    result = classify_institutional_streak([10, 20, -30, -40, -50])
    assert result == {"text": "連3賣後轉買", "color": COLOR_BUY}


def test_classify_institutional_streak_reversal_m_ge_3_buy_then_sell():
    result = classify_institutional_streak([-10, -20, 30, 40, 50])
    assert result == {"text": "連3買後轉賣", "color": COLOR_SELL}


def test_classify_institutional_streak_reversal_m_lt_3_is_undetermined():
    """N=2、M=2，兩段都不足3天門檻→方向未定。"""
    result = classify_institutional_streak([10, 20, -30, -40, 50, 50, 50])
    assert result == {"text": "方向未定", "color": COLOR_GRAY}


# ============================================================
# sum_institutional_flow_lots (K~R)
# ============================================================


def test_sum_institutional_flow_lots_basic_sum_converts_to_lots():
    # 40000+30000+20000+10000 = 100000股 / 1000 = 100張
    assert sum_institutional_flow_lots([40000, 30000, 20000, 10000], 4) == 100


def test_sum_institutional_flow_lots_negative_sum():
    assert sum_institutional_flow_lots([-40000, -30000], 2) == -70


def test_sum_institutional_flow_lots_insufficient_history_sums_whatever_is_available():
    """資料筆數不足n天時，原JS用slice(0,n)超出範圍不報錯，直接加總現有的——這裡故意
    保留這個寬容行為，不是回傳None或拋例外。"""
    assert sum_institutional_flow_lots([10000, 20000], 40) == 30


def test_sum_institutional_flow_lots_rounds_half_up_like_js_math_round():
    """500股=0.5張，JS的Math.round(0.5)=1(無條件進位到正無窮)，不是Python round()的
    banker's rounding(round(0.5)==0)。"""
    assert sum_institutional_flow_lots([500], 1) == 1
    assert sum_institutional_flow_lots([-500], 1) == 0  # floor(-0.5+0.5) = floor(0.0) = 0，對應JS Math.round(-0.5)===-0


def test_sum_institutional_flow_lots_only_uses_first_n_days():
    assert sum_institutional_flow_lots([100000, 999999999], 1) == 100


# ============================================================
# classify_ma_price_position (H)
# ============================================================


def test_classify_ma_price_position_none_when_any_ma_missing():
    assert classify_ma_price_position(None, 10.0, 10.0, 10.0, 10.0, 9.0) is None
    assert classify_ma_price_position(10.0, None, 10.0, 10.0, 10.0, 9.0) is None
    assert classify_ma_price_position(10.0, 10.0, None, 10.0, 10.0, 9.0) is None
    assert classify_ma_price_position(10.0, 10.0, 10.0, None, 10.0, 9.0) is None


def test_classify_ma_price_position_orders_lines_by_value_descending():
    """MA20=10(下彎)、MA60=13(上揚)、P=15(紅K)：由大到小應該是P、MA60、MA20。"""
    result = classify_ma_price_position(
        ma20_today=10.0, ma20_yesterday=11.0,
        ma60_today=13.0, ma60_yesterday=12.0,
        close_today=15.0, open_today=14.0,
    )
    assert [line["text"] for line in result["lines"]] == ["P(15)", "MA60 上揚", "MA20 下彎"]
    assert result["lines"][0]["color"] == COLOR_BUY  # 紅K
    assert result["lines"][1]["color"] == COLOR_BUY  # MA60上揚
    assert result["lines"][2]["color"] == COLOR_SELL  # MA20下彎


def test_classify_ma_price_position_equal_ma_counts_as_up():
    """原JS用>=判斷上揚，相等也算上揚，不是嚴格>。"""
    result = classify_ma_price_position(
        ma20_today=10.0, ma20_yesterday=10.0,
        ma60_today=20.0, ma60_yesterday=20.0,
        close_today=10.0, open_today=10.0,
    )
    ma20_line = next(line for line in result["lines"] if line["text"].startswith("MA20"))
    assert ma20_line["text"] == "MA20 上揚"


def test_classify_ma_price_position_close_price_formats_without_trailing_zero():
    result = classify_ma_price_position(
        ma20_today=1.0, ma20_yesterday=1.0, ma60_today=2.0, ma60_yesterday=2.0,
        close_today=698.0, open_today=700.0,
    )
    p_line = next(line for line in result["lines"] if line["text"].startswith("P("))
    assert p_line["text"] == "P(698)"
    assert p_line["color"] == COLOR_SELL  # 700開698收，黑K


def test_classify_ma_price_position_close_price_avoids_float_precision_artifact():
    """2026-08-04發現：從SQLite REAL欄位讀回來的股價可能是88.0999984741211這種浮點
    數表示誤差(6182那天實測踩到)，直接str()顯示會把誤差原樣秀出來——這裡驗證四捨
    五入到2位小數後正確顯示成"88.1"，不是"88.0999984741211"。"""
    result = classify_ma_price_position(
        ma20_today=1.0, ma20_yesterday=1.0, ma60_today=2.0, ma60_yesterday=2.0,
        close_today=88.0999984741211, open_today=87.0,
    )
    p_line = next(line for line in result["lines"] if line["text"].startswith("P("))
    assert p_line["text"] == "P(88.1)"


# ============================================================
# classify_weekly_volume_pattern (I)
# ============================================================


def _row(d: str, high: float, low: float, close: float, volume: float) -> dict:
    return {"date": d, "high": high, "low": low, "close": close, "volume": volume}


def test_classify_weekly_volume_pattern_none_when_not_enough_history():
    rows = [_row(f"2026-01-{d:02d}", 10, 9, 9.5, 100) for d in range(1, 9)]  # 8天，< 10
    assert classify_weekly_volume_pattern(rows) is None


def test_classify_weekly_volume_pattern_above_high_of_max_volume_week():
    """建構2週資料(各5天，滿足>=10筆的資料量門檻)：第1週(2026-01-05~09，週一到週五)
    有大量(高20/低18)，第2週(2026-01-12~16)價格漲到21(超過第1週的高)、成交量很小——
    目前收盤價21 > 大量週高20 → 大量高之上。"""
    rows = [
        _row("2026-01-05", 20, 18, 19, 100000),
        _row("2026-01-06", 19, 18, 18.5, 100000),
        _row("2026-01-07", 19, 18, 18.8, 100000),
        _row("2026-01-08", 20, 18, 19.5, 100000),
        _row("2026-01-09", 20, 19, 20, 100000),  # 第1週最後一天收盤=20(週收盤)
        _row("2026-01-12", 20.5, 20, 20.2, 10),
        _row("2026-01-13", 20.8, 20, 20.5, 10),
        _row("2026-01-14", 20.9, 20, 20.7, 10),
        _row("2026-01-15", 21, 20, 20.9, 10),
        _row("2026-01-16", 21, 20, 21, 10),  # 第2週最後一天收盤=21，量極小
    ]
    result = classify_weekly_volume_pattern(rows)
    assert result["pattern"] == "大量高之上"
    assert result["reference_week_start"] == "2026-01-05"


def test_classify_weekly_volume_pattern_above_mid_of_max_volume_week():
    rows = [
        _row("2026-01-05", 20, 10, 15, 100000),
        _row("2026-01-06", 18, 12, 15, 100000),
        _row("2026-01-07", 18, 12, 15, 100000),
        _row("2026-01-08", 18, 12, 15, 100000),
        _row("2026-01-09", 18, 12, 15, 100000),
        _row("2026-01-12", 16, 15, 15.5, 10),
        _row("2026-01-13", 16, 15, 15.8, 10),
        _row("2026-01-14", 17, 16, 16.5, 10),
        _row("2026-01-15", 17, 16, 16.8, 10),
        _row("2026-01-16", 17, 16, 17, 10),  # mid=(20+10)/2=15；17>15 → 大量中值之上，17<high(20)
    ]
    result = classify_weekly_volume_pattern(rows)
    assert result["pattern"] == "大量中值之上"


def test_classify_weekly_volume_pattern_below_mid_but_above_low():
    rows = [
        _row("2026-01-05", 20, 10, 15, 100000),
        _row("2026-01-06", 18, 12, 15, 100000),
        _row("2026-01-07", 18, 12, 15, 100000),
        _row("2026-01-08", 18, 12, 15, 100000),
        _row("2026-01-09", 18, 12, 15, 100000),
        _row("2026-01-12", 14, 12, 13, 10),
        _row("2026-01-13", 14, 12, 12.8, 10),
        _row("2026-01-14", 13, 11, 12.5, 10),
        _row("2026-01-15", 13, 11, 12.2, 10),
        _row("2026-01-16", 13, 11, 12, 10),  # mid=15；low=10；10<=12<15 → 大量中值之下
    ]
    result = classify_weekly_volume_pattern(rows)
    assert result["pattern"] == "大量中值之下"


def test_classify_weekly_volume_pattern_below_low_of_max_volume_week():
    rows = [
        _row("2026-01-05", 20, 10, 15, 100000),
        _row("2026-01-06", 18, 12, 15, 100000),
        _row("2026-01-07", 18, 12, 15, 100000),
        _row("2026-01-08", 18, 12, 15, 100000),
        _row("2026-01-09", 18, 12, 15, 100000),
        _row("2026-01-12", 9.5, 8, 9, 10),
        _row("2026-01-13", 9.3, 8, 8.8, 10),
        _row("2026-01-14", 9, 8, 8.6, 10),
        _row("2026-01-15", 9, 8, 8.5, 10),
        _row("2026-01-16", 9, 8, 8.5, 10),  # low=10；8.5<10 → 大量低之下
    ]
    result = classify_weekly_volume_pattern(rows)
    assert result["pattern"] == "大量低之下"


def test_classify_weekly_volume_pattern_tie_prefers_more_recent_week():
    """兩週成交量剛好相同時，原JS的reduce用嚴格>比較、陣列由新到舊排序，較新的週留在
    max——這裡驗證同成交量時保留較新一週當大量K參考。"""
    rows = [
        _row("2026-01-05", 30, 25, 27, 10000),
        _row("2026-01-06", 30, 25, 27, 10000),
        _row("2026-01-07", 30, 25, 27, 10000),
        _row("2026-01-08", 30, 25, 27, 10000),
        _row("2026-01-09", 30, 25, 27, 10000),  # 第1週：高30，總量50000
        _row("2026-01-12", 20, 15, 17, 10000),
        _row("2026-01-13", 20, 15, 17, 10000),
        _row("2026-01-14", 20, 15, 17, 10000),
        _row("2026-01-15", 20, 15, 17, 10000),
        _row("2026-01-16", 20, 15, 17, 10000),  # 第2週(較新)：高20，總量也是50000
    ]
    result = classify_weekly_volume_pattern(rows)
    assert result["reference_week_start"] == "2026-01-12"


# ============================================================
# classify_holder_change (F/G)
# ============================================================


def _holder_rows(whale_pct: float, retail_pct_each: float) -> list[dict]:
    rows = [{"holding_shares_level": "more than 1,000,001", "percent": whale_pct}]
    from src.indicators.huang_chip_signals import RETAIL_HOLDING_LEVELS
    for level in RETAIL_HOLDING_LEVELS:
        rows.append({"holding_shares_level": level, "percent": retail_pct_each})
    # 中間級距(不算大戶也不算散戶)，確認不會被誤算進去
    rows.append({"holding_shares_level": "100,001-200,000", "percent": 999.0})
    return rows


def test_classify_holder_change_none_when_fewer_than_2_dates():
    assert classify_holder_change({"2026-08-01": _holder_rows(10.0, 1.0)}) is None


def test_classify_holder_change_whale_increase_is_red_retail_increase_is_green():
    rows_by_date = {
        "2026-08-08": _holder_rows(whale_pct=12.0, retail_pct_each=1.0),  # 9*1.0=9.0%
        "2026-08-01": _holder_rows(whale_pct=10.0, retail_pct_each=0.8),  # 9*0.8=7.2%
    }
    result = classify_holder_change(rows_by_date)
    # whale diff = 12.0-10.0 = 2.0 → 爆買(>=2)，紅
    assert result["whale"]["text"] == "大戶爆買 +2.00%"
    assert result["whale"]["color"] == COLOR_BUY
    # retail diff = 9.0-7.2 = 1.8 → 大增(>=1,<2)，散戶增加是綠色(反指標)
    assert result["retail"]["text"] == "散戶大增 +1.80%"
    assert result["retail"]["color"] == COLOR_SELL


def test_classify_holder_change_whale_decrease_is_green_retail_decrease_is_red():
    rows_by_date = {
        "2026-08-08": _holder_rows(whale_pct=9.5, retail_pct_each=1.0),
        "2026-08-01": _holder_rows(whale_pct=10.0, retail_pct_each=1.0),
    }
    result = classify_holder_change(rows_by_date)
    # whale diff = -0.5 → 減持(>=0.5,<1)，綠
    assert result["whale"]["text"] == "大戶減持 -0.50%"
    assert result["whale"]["color"] == COLOR_SELL


def test_classify_holder_change_small_change_tier():
    rows_by_date = {
        "2026-08-08": _holder_rows(whale_pct=10.1, retail_pct_each=1.0),
        "2026-08-01": _holder_rows(whale_pct=10.0, retail_pct_each=1.0),
    }
    result = classify_holder_change(rows_by_date)
    assert result["whale"]["text"] == "大戶小增 +0.10%"
    assert result["whale"]["color"] == COLOR_BUY


def test_classify_holder_change_uses_latest_two_available_dates_not_calendar_today():
    """集保資料週更新，這裡確保是取「資料裡實際存在的最新兩個日期」，不是任何跟
    「今天」有關的日期概念。"""
    rows_by_date = {
        "2026-07-18": _holder_rows(whale_pct=8.0, retail_pct_each=1.0),
        "2026-07-25": _holder_rows(whale_pct=9.0, retail_pct_each=1.0),
        "2026-08-01": _holder_rows(whale_pct=11.0, retail_pct_each=1.0),
    }
    result = classify_holder_change(rows_by_date)
    # 應該比較最新的08-01(11.0) vs 次新的07-25(9.0)，diff=2.0，不是跟07-18比
    assert result["whale"]["text"] == "大戶爆買 +2.00%"
