from src.indicators.ownership_distribution import (
    chip_flow_direction,
    classify_holder_identity,
    ownership_concentration,
    retail_percent,
)


def test_classify_holder_identity_retail():
    assert classify_holder_identity("1-999") == "散戶"
    assert classify_holder_identity("15,001-20,000") == "散戶"


def test_classify_holder_identity_mid():
    assert classify_holder_identity("20,001-30,000") == "中實戶或法人"
    assert classify_holder_identity("600,001-800,000") == "中實戶或法人"


def test_classify_holder_identity_ambiguous_and_whale():
    assert classify_holder_identity("800,001-1,000,000") == "模糊地帶"
    assert classify_holder_identity("more than 1,000,001") == "大股東或主力"


def test_classify_holder_identity_unknown():
    assert classify_holder_identity("total") == "不明"
    assert classify_holder_identity("差異數調整（說明4）") == "不明"


def test_ownership_concentration_returns_whale_percent():
    rows = [
        {"holding_shares_level": "1-999", "percent": 5.0},
        {"holding_shares_level": "more than 1,000,001", "percent": 77.85},
    ]
    assert ownership_concentration(rows) == 77.85


def test_ownership_concentration_none_when_missing():
    rows = [{"holding_shares_level": "1-999", "percent": 5.0}]
    assert ownership_concentration(rows) is None


def test_retail_percent_sums_five_levels():
    rows = [
        {"holding_shares_level": "1-999", "percent": 1.0},
        {"holding_shares_level": "1,000-5,000", "percent": 2.0},
        {"holding_shares_level": "5,001-10,000", "percent": 0.5},
        {"holding_shares_level": "10,001-15,000", "percent": 0.3},
        {"holding_shares_level": "15,001-20,000", "percent": 0.2},
        {"holding_shares_level": "20,001-30,000", "percent": 10.0},  # 不算散戶，不應被加總
    ]
    assert retail_percent(rows) == 4.0


def test_chip_flow_direction_none_when_less_than_two_dates():
    rows_by_date = {"2026-08-01": [{"holding_shares_level": "more than 1,000,001", "percent": 70.0}]}
    assert chip_flow_direction(rows_by_date) is None


def test_chip_flow_direction_bullish_when_whale_up_retail_down():
    rows_by_date = {
        "2026-08-08": [
            {"holding_shares_level": "more than 1,000,001", "percent": 72.0},
            {"holding_shares_level": "1-999", "percent": 3.0},
        ],
        "2026-08-01": [
            {"holding_shares_level": "more than 1,000,001", "percent": 70.0},
            {"holding_shares_level": "1-999", "percent": 4.0},
        ],
    }
    result = chip_flow_direction(rows_by_date)
    assert result["direction"] == "籌碼從散戶流向大股東，股價多半續漲"
    assert result["whale_diff"] == 2.0
    assert result["retail_diff"] == -1.0
    assert result["latest_date"] == "2026-08-08"
    assert result["prev_date"] == "2026-08-01"


def test_chip_flow_direction_bearish_when_whale_down_retail_up():
    rows_by_date = {
        "2026-08-08": [
            {"holding_shares_level": "more than 1,000,001", "percent": 68.0},
            {"holding_shares_level": "1-999", "percent": 5.0},
        ],
        "2026-08-01": [
            {"holding_shares_level": "more than 1,000,001", "percent": 70.0},
            {"holding_shares_level": "1-999", "percent": 4.0},
        ],
    }
    result = chip_flow_direction(rows_by_date)
    assert result["direction"] == "籌碼從大股東流向散戶，股價多半續跌"


def test_chip_flow_direction_no_clear_direction_when_both_increase():
    rows_by_date = {
        "2026-08-08": [
            {"holding_shares_level": "more than 1,000,001", "percent": 71.0},
            {"holding_shares_level": "1-999", "percent": 4.5},
        ],
        "2026-08-01": [
            {"holding_shares_level": "more than 1,000,001", "percent": 70.0},
            {"holding_shares_level": "1-999", "percent": 4.0},
        ],
    }
    result = chip_flow_direction(rows_by_date)
    assert result["direction"] == "無明確方向"


def test_chip_flow_direction_none_when_whale_level_missing():
    rows_by_date = {
        "2026-08-08": [{"holding_shares_level": "1-999", "percent": 4.5}],
        "2026-08-01": [{"holding_shares_level": "1-999", "percent": 4.0}],
    }
    assert chip_flow_direction(rows_by_date) is None
