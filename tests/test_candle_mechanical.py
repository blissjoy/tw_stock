import pandas as pd

from src.strategies.candle_mechanical import mechanical_long_trading_rule, mechanical_short_trading_rule


def test_mechanical_long_trading_rule_entry_hold_stop_loss():
    # R-CANDLE-32: 收盤突破前一日高點進場；停損=max(進場當日低點, 7%)；觸及停損或跌破前一日低點出場
    high = pd.Series([10.0, 10.6, 11.0, 11.0])
    low = pd.Series([9.0, 10.2, 10.5, 9.5])
    close = pd.Series([9.5, 10.5, 10.8, 9.8])

    result = mechanical_long_trading_rule(high, low, close)

    assert result["state"].tolist() == ["空手", "持有多單", "持有多單", "空手"]
    assert pd.isna(result["action"].iloc[0])
    assert result["action"].iloc[1] == "進場"
    assert pd.isna(result["action"].iloc[2])
    assert result["action"].iloc[3] == "停損出場"
    assert pd.isna(result["entry_price"].iloc[0])
    assert result["entry_price"].iloc[1] == 10.5
    assert result["entry_price"].iloc[2] == 10.5
    assert pd.isna(result["entry_price"].iloc[3])
    # max(進場當日低點=10.2, entry*0.93=9.765) = 10.2
    assert result["stop_loss"].iloc[1] == 10.2
    assert result["stop_loss"].iloc[2] == 10.2


def test_mechanical_short_trading_rule_mirrors_long():
    # R-CANDLE-33: 收盤跌破前一日低點放空，與多頭版完全鏡射
    high = pd.Series([10.5, 9.9, 9.7, 9.8])
    low = pd.Series([10.0, 9.4, 9.2, 9.5])
    close = pd.Series([10.2, 9.8, 9.5, 10.0])

    result = mechanical_short_trading_rule(high, low, close)

    assert result["state"].tolist() == ["空手", "持有空單", "持有空單", "空手"]
    assert pd.isna(result["action"].iloc[0])
    assert result["action"].iloc[1] == "進場"
    assert pd.isna(result["action"].iloc[2])
    assert result["action"].iloc[3] == "停損出場"
    assert pd.isna(result["entry_price"].iloc[0])
    assert result["entry_price"].iloc[1] == 9.8
    assert result["entry_price"].iloc[2] == 9.8
    assert pd.isna(result["entry_price"].iloc[3])
    # min(進場當日高點=9.9, entry*1.07=10.486) = 9.9
    assert result["stop_loss"].iloc[1] == 9.9
    assert result["stop_loss"].iloc[2] == 9.9
