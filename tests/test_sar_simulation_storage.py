import sqlite3

import pytest

from src.backtest.sar_portfolio import (
    PortfolioSnapshot,
    SarSimulationResult,
    SimulatedPosition,
    SimulationEvent,
)
from src.backtest.sar_validation import (
    HistoricalValidationResult,
    ValidationPeriodMetrics,
)
from src.backtest.sar_scenario import (
    ExcludedStalePosition,
    ForwardEntryCandidate,
    ForwardScenarioMetrics,
    ForwardScenarioResult,
)
from src.data.sar_simulation_storage import (
    init_sar_simulation_db,
    load_latest_forward_scenario_result,
    load_latest_historical_validation,
    list_simulation_runs,
    load_simulation_result,
    save_forward_scenario_result,
    save_historical_validation,
    save_simulation_result,
)


def sample_result(run_id="test-run-1"):
    result = SarSimulationResult(
        start_date="2026-01-01",
        end_date="2026-01-05",
        initial_capital=100_000,
        cash=89_980,
        run_id=run_id,
    )
    result.positions.append(SimulatedPosition(
        stock_id="2330",
        name="台積電",
        signal_date="2026-01-02",
        entry_date="2026-01-05",
        entry_price=50,
        shares=199,
        cost_basis=10_020,
        add_attempted=True,
        added=True,
        trailing_active=True,
        peak_price=60,
        last_price=58,
    ))
    result.closed_positions.append(SimulatedPosition(
        stock_id="2454",
        name="聯發科",
        signal_date="2026-01-01",
        entry_date="2026-01-02",
        entry_price=100,
        shares=100,
        cost_basis=10_020,
        realized_pnl=500,
        exit_date="2026-01-05",
        exit_price=115,
        exit_reason="移動停利出場",
    ))
    result.events.append(SimulationEvent(
        date="2026-01-05",
        event_type="進場",
        stock_id="2330",
        name="台積電",
        shares=199,
        price=50,
        fee=20,
        tax=0,
        cash_change=-10_020,
        detail="test",
    ))
    return result


def test_save_and_load_round_trip_full_result(tmp_path):
    conn = init_sar_simulation_db(tmp_path / "simulation.db")
    original = sample_result()

    saved_id = save_simulation_result(
        conn, original, {
            "version": original.strategy_version,
            "max_close_price": 87.5,
            "whole_lot_trading": True,
            "volume": 1000,
        },
    )
    restored = load_simulation_result(conn, saved_id)

    assert restored is not None
    assert restored.run_id == original.run_id
    assert restored.start_date == original.start_date
    assert restored.end_date == original.end_date
    assert restored.initial_capital == original.initial_capital
    assert restored.max_close_price == 87.5
    assert restored.whole_lot_trading is True
    assert restored.cash == original.cash
    assert restored.positions == original.positions
    assert restored.closed_positions == original.closed_positions
    assert restored.closed_positions[0].exit_date == "2026-01-05"
    assert restored.closed_positions[0].exit_price == 115
    assert restored.closed_positions[0].exit_reason == "移動停利出場"
    assert restored.events == original.events
    assert restored.market_value == original.market_value
    assert restored.unrealized_pnl == original.unrealized_pnl
    assert restored.realized_pnl == original.realized_pnl
    conn.close()


def test_latest_saved_run_loads_after_connection_reopen(tmp_path):
    db_path = tmp_path / "simulation.db"
    conn = init_sar_simulation_db(db_path)
    first = sample_result("first")
    second = sample_result("second")
    second.end_date = "2026-01-10"
    save_simulation_result(conn, first)
    save_simulation_result(conn, second)
    conn.close()

    reopened = init_sar_simulation_db(db_path)
    latest = load_simulation_result(reopened)
    runs = list_simulation_runs(reopened)

    assert latest is not None
    assert latest.run_id == "second"
    assert latest.end_date == "2026-01-10"
    assert [run["run_id"] for run in runs] == ["second", "first"]
    reopened.close()


def test_loaded_legacy_run_keeps_its_strategy_version(tmp_path):
    conn = init_sar_simulation_db(tmp_path / "simulation.db")
    result = sample_result()
    save_simulation_result(conn, result, {"version": 1})

    restored = load_simulation_result(conn, result.run_id)

    assert restored is not None
    assert restored.strategy_version == 1
    conn.close()


def test_duplicate_run_id_fails_without_partial_rows(tmp_path):
    conn = init_sar_simulation_db(tmp_path / "simulation.db")
    result = sample_result()
    save_simulation_result(conn, result)

    with pytest.raises(sqlite3.IntegrityError):
        save_simulation_result(conn, result)

    assert conn.execute("SELECT COUNT(*) FROM sar_simulation_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM sar_simulation_events").fetchone()[0] == 1
    conn.close()


def test_simulation_storage_is_independent_local_sqlite(tmp_path):
    conn = init_sar_simulation_db(tmp_path / "sar_simulation.db")

    table_names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    assert table_names == {
        "sar_simulation_runs",
        "sar_simulation_positions",
        "sar_simulation_events",
        "sar_historical_validations",
        "sar_forward_scenarios",
    }
    conn.close()


def test_historical_validation_round_trip_includes_equity_curve(tmp_path):
    conn = init_sar_simulation_db(tmp_path / "simulation.db")
    period = ValidationPeriodMetrics(
        label="樣本外期",
        start_date="2026-01-05",
        end_date="2026-01-06",
        trading_days=2,
        signal_count=1,
        closed_trade_count=0,
        win_rate_pct=0,
        realized_pnl=0,
        unrealized_pnl=-10,
        total_pnl=-10,
        total_return_pct=-0.01,
        cash_benchmark_return_pct=0,
        max_drawdown_pct=0.01,
        average_invested_pct=5,
    )
    original = HistoricalValidationResult(
        validation_id="validation-1",
        created_at="2026-01-07T12:00:00+08:00",
        initial_capital=100_000,
        params={"max_close_price": 100, "whole_lot_trading": False},
        reference=period,
        holdout=period,
        holdout_equity_curve=[
            PortfolioSnapshot("2026-01-05", 95_000, 5_000, 100_000),
            PortfolioSnapshot("2026-01-06", 94_990, 5_000, 99_990),
        ],
    )

    save_historical_validation(conn, original)
    restored = load_latest_historical_validation(conn)

    assert restored == original
    conn.close()


def test_forward_scenario_round_trip_includes_each_equity_curve(tmp_path):
    conn = init_sar_simulation_db(tmp_path / "simulation.db")
    scenario = ForwardScenarioMetrics(
        name="大盤接近平盤",
        source_start_date="2025-01-01",
        source_end_date="2025-01-20",
        source_index_return_pct=0.2,
        start_assets=100_000,
        ending_assets=102_000,
        scenario_pnl=2_000,
        scenario_return_pct=2,
        ending_cash=90_000,
        ending_market_value=12_000,
        new_entry_count=2,
        exit_count=1,
        ending_open_position_count=1,
        daily_snapshots=[PortfolioSnapshot("2026-01-05", 90_000, 10_000, 100_000)],
        entry_candidates=[ForwardEntryCandidate(
            signal_date="2026-01-02",
            entry_date="2026-01-05",
            stock_id="2330",
            name="台積電",
            signal_close=50,
            sar_distance_pct=-3.5,
            entry_price=51,
            shares=196,
            funding_status="資金足夠，情境中可進場",
        )],
    )
    original = ForwardScenarioResult(
        scenario_id="scenario-1",
        created_at="2026-01-21T12:00:00+08:00",
        base_run_id="base-run",
        base_end_date="2026-01-02",
        initial_capital=100_000,
        starting_assets=100_000,
        horizon_days=20,
        params={"max_close_price": 100, "whole_lot_trading": False},
        scenarios=[scenario],
        selected_scenario="大盤接近平盤",
        excluded_stale_positions=[ExcludedStalePosition(
            stock_id="2809",
            name="京城銀",
            shares=1_000,
            market_value=56_600,
            last_quote_date="2025-09-17",
            reason="最新行情日期早於推演起點 2026-10-22",
        )],
    )

    save_forward_scenario_result(conn, original)
    restored = load_latest_forward_scenario_result(conn)

    assert restored == original
    conn.close()


def test_init_sar_simulation_db_creates_parent_directory(tmp_path):
    db_path = tmp_path / "nested" / "simulation.db"
    conn = init_sar_simulation_db(db_path)

    assert db_path.is_file()
    conn.close()


def test_init_migrates_legacy_position_table_with_exit_fields(tmp_path):
    db_path = tmp_path / "legacy.db"
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.execute(
        """
        CREATE TABLE sar_simulation_positions (
            run_id TEXT NOT NULL,
            position_kind TEXT NOT NULL,
            position_index INTEGER NOT NULL,
            stock_id TEXT NOT NULL,
            name TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            shares INTEGER NOT NULL,
            cost_basis REAL NOT NULL,
            add_attempted INTEGER NOT NULL,
            added INTEGER NOT NULL,
            trailing_active INTEGER NOT NULL,
            peak_price REAL NOT NULL,
            last_price REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            PRIMARY KEY (run_id, position_kind, position_index)
        )
        """
    )
    legacy_conn.close()

    conn = init_sar_simulation_db(db_path)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(sar_simulation_positions)")}
    assert {"exit_date", "exit_price", "exit_reason"} <= columns
    conn.close()
