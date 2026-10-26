import pandas as pd
import pytest
import sqlite3

from src.backtest.sar_portfolio import (
    SarSimulationResult,
    SarStrategyParams,
    SimulatedPosition,
    SimulationEvent,
    exclude_stale_positions,
    load_sar_market_data,
    select_daily_sar_candidates,
    run_sar_portfolio_simulation,
)


def test_exclude_stale_positions_changes_display_copy_not_original():
    result = SarSimulationResult(
        start_date="2025-01-01",
        end_date="2025-01-03",
        initial_capital=100_000,
        cash=40_000,
        positions=[
            SimulatedPosition(
                stock_id="2809",
                name="京城銀",
                signal_date="2025-01-01",
                entry_date="2025-01-02",
                entry_price=50,
                shares=1_000,
                cost_basis=50_000,
                last_price=56.6,
            ),
            SimulatedPosition(
                stock_id="2330",
                name="台積電",
                signal_date="2025-01-01",
                entry_date="2025-01-02",
                entry_price=60,
                shares=1_000,
                cost_basis=60_000,
                last_price=62,
            ),
        ],
    )

    display_result, excluded = exclude_stale_positions(
        result,
        {"2809": "2025-01-02", "2330": "2025-01-03"},
    )

    assert [position.stock_id for position in display_result.positions] == ["2330"]
    assert display_result.market_value == 62_000
    assert display_result.total_assets == 102_000
    assert excluded[0].stock_id == "2809"
    assert excluded[0].market_value == 56_600
    assert [position.stock_id for position in result.positions] == ["2809", "2330"]
    assert result.total_assets == 158_600


def market_row(date, stock_id="2330", **overrides):
    row = {
        "date": date,
        "stock_id": stock_id,
        "name": f"股票{stock_id}",
        "open": 49.0,
        "high": 50.0,
        "low": 48.0,
        "close": 50.0,
        "volume": 1_000_000,
        "ma5": 51.0,
        "ma10": 50.5,
        "ma20": 50.2,
        "ma60": 47.0,
        "ma120": 46.0,
        "ma200": 45.0,
        "sar_value": 47.5,
        "sar_is_bull": 1,
        "sar_flip_days_ago": 1,
    }
    row.update(overrides)
    return row


def test_selector_picks_closest_to_zero_sar_distance_once_per_day():
    data = pd.DataFrame([
        market_row("2026-01-05", "2330", sar_value=47.5),
        market_row("2026-01-05", "2454", sar_value=49.0),
        market_row("2026-01-06", "2317", sar_value=48.0),
    ])

    candidates = select_daily_sar_candidates(data)

    assert candidates[["date", "stock_id"]].values.tolist() == [
        ["2026-01-05", "2454"],
        ["2026-01-06", "2317"],
    ]
    assert candidates.iloc[0]["sar_distance_pct"] == pytest.approx(-2.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"ma60": 51.5},  # The moving average is above close, so there is no upside clearance.
        {"ma120": 51.5},
        {"ma60": 48.5},  # Exactly 3% below close is not more than 3%.
        {"ma120": 48.5},
        {"volume": 999_999},
        {"close": 100.01},
        {"sar_flip_days_ago": 2},
        {"sar_is_bull": 0},
        {"close": 50.0, "open": 50.0},
    ],
)
def test_selector_rejects_rows_that_fail_a_required_filter(overrides):
    assert select_daily_sar_candidates(
        pd.DataFrame([market_row("2026-01-05", **overrides)])
    ).empty


def test_selector_handles_missing_indicator_cache_without_type_error():
    row = market_row("2026-01-05", ma200=None, ma60=None, sar_value=None, sar_is_bull=None)

    assert select_daily_sar_candidates(pd.DataFrame([row])).empty


def test_simulation_records_missing_indicator_cache_warning():
    data = pd.DataFrame([
        market_row("2026-01-05", ma200=None, ma60=None, sar_value=None, sar_is_bull=None),
    ])

    result = run_sar_portfolio_simulation(data, "2026-01-05", "2026-01-05")

    warning = next(event for event in result.events if event.event_type == "指標缺資料")
    assert "1 列行情" in warning.detail


def test_simulation_enters_next_day_adds_once_then_stops_out():
    data = pd.DataFrame([
        market_row("2026-01-05"),
        market_row("2026-01-06", high=51.0, low=48.0, close=50.0, sar_flip_days_ago=2),
        market_row("2026-01-07", high=45.0, low=42.0, close=44.0, sar_flip_days_ago=2),
        market_row("2026-01-08", high=40.0, low=30.0, close=35.0, sar_flip_days_ago=2),
    ])

    result = run_sar_portfolio_simulation(data, "2026-01-05", "2026-01-08")

    trade_events = [event for event in result.events if event.event_type != "每日選股"]
    assert [event.event_type for event in trade_events] == ["進場", "加碼", "停損出場"]
    entry, add, stop = trade_events
    assert (entry.date, entry.price, entry.shares) == ("2026-01-06", 51.0, 196)
    assert add.date == "2026-01-07"
    assert add.price == pytest.approx(51.0 * 0.85)
    assert add.shares == int(5000 / add.price)
    assert stop.date == "2026-01-08"
    assert result.positions == []
    assert result.closed_positions[0].added is True
    assert result.closed_positions[0].exit_date == "2026-01-08"
    assert result.closed_positions[0].exit_price == pytest.approx(stop.price)
    assert result.closed_positions[0].exit_reason == "停損出場"
    assert result.cash == pytest.approx(100_000 + stop.cash_change + entry.cash_change + add.cash_change)
    assert result.realized_pnl == pytest.approx(stop.cash_change - result.closed_positions[0].cost_basis)
    assert stop.tax == pytest.approx(stop.shares * stop.price * 0.003)
    assert stop.fee == pytest.approx(max(20, stop.shares * stop.price * 0.001425))


def test_trailing_exit_uses_previous_day_peak_before_updating_current_high():
    data = pd.DataFrame([
        market_row("2026-01-05"),
        market_row("2026-01-06", high=51.0, low=48.0, close=50.0, sar_flip_days_ago=2),
        market_row("2026-01-07", high=58.0, low=55.0, close=57.0, sar_flip_days_ago=2),
        market_row("2026-01-08", high=60.0, low=57.5, close=59.0, sar_flip_days_ago=2),
        market_row("2026-01-09", high=59.0, low=57.0, close=58.0, sar_flip_days_ago=2),
    ])

    result = run_sar_portfolio_simulation(data, "2026-01-05", "2026-01-09")

    activation = next(event for event in result.events if event.event_type == "移動停利啟動")
    exit_event = next(event for event in result.events if event.event_type == "移動停利出場")
    assert activation.date == "2026-01-08"
    assert exit_event.date == "2026-01-09"
    assert exit_event.price == pytest.approx(60.0 - result.closed_positions[0].average_cost * 0.03)
    assert result.positions == []


def test_open_position_is_marked_to_market_not_force_closed():
    data = pd.DataFrame([
        market_row("2026-01-05"),
        market_row("2026-01-06", high=51.0, low=48.0, close=50.0, sar_flip_days_ago=2),
    ])

    result = run_sar_portfolio_simulation(data, "2026-01-05", "2026-01-06")

    assert len(result.positions) == 1
    assert result.closed_positions == []
    assert result.positions[0].last_price == 50.0
    assert result.total_assets == pytest.approx(result.cash + 196 * 50.0)
    assert [snapshot.date for snapshot in result.daily_snapshots] == [
        "2026-01-05", "2026-01-06",
    ]
    assert result.daily_snapshots[-1].total_assets == pytest.approx(result.total_assets)


def test_simulation_result_keeps_configured_max_close_price():
    data = pd.DataFrame([market_row("2026-01-05")])

    result = run_sar_portfolio_simulation(
        data,
        "2026-01-05",
        "2026-01-05",
        initial_capital=250_000,
        params=SarStrategyParams(max_close_price=75.5),
    )

    assert result.initial_capital == 250_000
    assert result.max_close_price == 75.5


def test_whole_lot_mode_buys_one_lot_and_adds_one_lot():
    data = pd.DataFrame([
        market_row("2026-01-05", high=50.0),
        market_row("2026-01-06", high=50.0, low=49.0, close=50.0, sar_flip_days_ago=2),
        market_row("2026-01-07", high=44.0, low=42.0, close=43.0, sar_flip_days_ago=2),
    ])

    result = run_sar_portfolio_simulation(
        data,
        "2026-01-05",
        "2026-01-07",
        initial_capital=100_000,
        params=SarStrategyParams(whole_lot_trading=True),
    )

    entry = next(event for event in result.events if event.event_type == "進場")
    add = next(event for event in result.events if event.event_type == "加碼")
    assert entry.shares == 1_000
    assert add.shares == 1_000
    assert result.whole_lot_trading is True
    assert len(result.positions) == 1
    assert result.positions[0].shares == 2_000


def test_whole_lot_mode_skips_entry_when_cash_cannot_cover_one_lot():
    data = pd.DataFrame([
        market_row("2026-01-05"),
        market_row("2026-01-06", high=50.0, low=49.0, sar_flip_days_ago=2),
    ])

    result = run_sar_portfolio_simulation(
        data,
        "2026-01-05",
        "2026-01-06",
        initial_capital=10_000,
        params=SarStrategyParams(whole_lot_trading=True),
    )

    assert result.positions == []
    assert any(event.event_type == "資金不足跳過" for event in result.events)


def test_insufficient_entry_cash_skips_signal_without_queueing():
    data = pd.DataFrame([
        market_row("2026-01-05"),
        market_row("2026-01-06", high=51.0, low=48.0, sar_flip_days_ago=2),
    ])

    result = run_sar_portfolio_simulation(
        data, "2026-01-05", "2026-01-06", initial_capital=10_010,
    )

    assert [event.event_type for event in result.events if event.event_type != "每日選股"] == ["資金不足跳過"]
    assert result.cash == 10_010
    assert result.positions == []


def test_simulation_can_continue_seed_account_and_enter_pending_signal():
    data = pd.DataFrame([
        market_row("2026-01-06", high=51.0, low=49.0, close=50.0),
    ])
    seed = SarSimulationResult(
        start_date="2025-01-01",
        end_date="2026-01-05",
        initial_capital=100_000,
        cash=80_000,
    )
    signal = SimulationEvent(
        date="2026-01-05",
        event_type="每日選股",
        stock_id="2330",
        name="台積電",
        shares=0,
        price=50,
        fee=0,
        tax=0,
        cash_change=0,
    )

    result = run_sar_portfolio_simulation(
        data,
        "2026-01-06",
        "2026-01-06",
        initial_state=seed,
        initial_signal=signal,
    )

    assert result.initial_capital == 100_000
    assert result.cash < seed.cash
    assert len(result.positions) == 1
    assert result.positions[0].entry_date == "2026-01-06"
    entry = next(event for event in result.events if event.event_type == "進場")
    assert entry.detail == "訊號日 2026-01-05"


def test_failed_add_attempt_is_not_retried_later():
    data = pd.DataFrame([
        market_row("2026-01-05"),
        market_row("2026-01-06", high=51.0, low=48.0, sar_flip_days_ago=2),
        market_row("2026-01-07", high=45.0, low=42.0, close=44.0, sar_flip_days_ago=2),
        market_row("2026-01-08", high=45.0, low=42.0, close=44.0, sar_flip_days_ago=2),
    ])

    result = run_sar_portfolio_simulation(data, "2026-01-05", "2026-01-08", initial_capital=10_200)

    skipped_adds = [event for event in result.events if event.event_type == "加碼資金不足"]
    assert len(skipped_adds) == 1
    assert result.positions[0].add_attempted is True
    assert result.positions[0].added is False


def test_fee_floor_applies_to_entry_and_sale():
    data = pd.DataFrame([
        market_row("2026-01-05"),
        market_row("2026-01-06", high=51.0, low=48.0, close=50.0, sar_flip_days_ago=2),
    ])

    result = run_sar_portfolio_simulation(data, "2026-01-05", "2026-01-06")

    entry_event = next(event for event in result.events if event.event_type == "進場")
    assert entry_event.fee == 20.0


def test_cash_from_same_day_exit_can_fund_new_signal():
    data = pd.DataFrame([
        market_row("2026-01-05", "2330"),
        market_row("2026-01-06", "2330", high=51.0, low=48.0, sar_flip_days_ago=2),
        market_row("2026-01-06", "2454"),
        market_row("2026-01-07", "2330", high=40.0, low=30.0, close=35.0, sar_flip_days_ago=2),
        market_row("2026-01-07", "2454", sar_flip_days_ago=2),
    ])

    result = run_sar_portfolio_simulation(data, "2026-01-05", "2026-01-07", initial_capital=13_000)

    assert any(
        event.date == "2026-01-07" and event.event_type == "停損出場"
        for event in result.events
    )
    assert any(
        event.date == "2026-01-07" and event.event_type == "進場" and event.stock_id == "2454"
        for event in result.events
    )


def test_database_loader_joins_ohlcv_to_cached_indicators():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE stocks (stock_id TEXT, name TEXT, market TEXT);
        CREATE TABLE stock_prices (
            date TEXT, stock_id TEXT, open REAL, high REAL, low REAL,
            close REAL, volume INTEGER
        );
        CREATE TABLE daily_indicators (
            date TEXT, stock_id TEXT, ma5 REAL, ma10 REAL, ma20 REAL,
            ma60 REAL, ma120 REAL, ma200 REAL, sar_value REAL,
            sar_is_bull INTEGER, sar_flip_days_ago INTEGER
        );
        INSERT INTO stocks VALUES ('2330', '台積電', 'TWSE');
        INSERT INTO stock_prices VALUES ('2026-01-05', '2330', 49, 50, 48, 50, 1000000);
        INSERT INTO daily_indicators VALUES
            ('2026-01-05', '2330', 51, 50.5, 50.2, 52, 52, 45, 47.5, 1, 1);
    """)

    result = load_sar_market_data(conn, "2026-01-01", "2026-01-31")

    assert result.loc[0, "stock_id"] == "2330"
    assert result.loc[0, "sar_value"] == 47.5
    assert result.loc[0, "volume"] == 1_000_000
    conn.close()


def test_result_with_no_market_data_has_zero_balances_except_cash():
    result = run_sar_portfolio_simulation(
        pd.DataFrame(columns=[
            "date", "stock_id", "name", "open", "high", "low", "close", "volume",
            "ma5", "ma10", "ma20", "ma60", "ma120", "ma200",
            "sar_value", "sar_is_bull", "sar_flip_days_ago",
        ]),
        "2026-01-05",
        "2026-01-06",
    )

    assert isinstance(result, SarSimulationResult)
    assert result.cash == 100_000
    assert result.total_assets == 100_000
    assert result.events == []
