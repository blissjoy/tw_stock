"""Persistence for SAR simulation snapshots, isolated from market and real portfolio data."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS sar_simulation_runs (
    run_id TEXT PRIMARY KEY,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    initial_capital REAL NOT NULL,
    cash REAL NOT NULL,
    market_value REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    total_assets REAL NOT NULL,
    total_pnl REAL NOT NULL,
    strategy_params_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sar_simulation_positions (
    run_id TEXT NOT NULL REFERENCES sar_simulation_runs(run_id) ON DELETE CASCADE,
    position_kind TEXT NOT NULL CHECK (position_kind IN ('open', 'closed')),
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
    exit_date TEXT,
    exit_price REAL,
    exit_reason TEXT,
    PRIMARY KEY (run_id, position_kind, position_index)
);

CREATE TABLE IF NOT EXISTS sar_simulation_events (
    run_id TEXT NOT NULL REFERENCES sar_simulation_runs(run_id) ON DELETE CASCADE,
    event_index INTEGER NOT NULL,
    date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    stock_id TEXT NOT NULL,
    name TEXT NOT NULL,
    shares INTEGER NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL,
    tax REAL NOT NULL,
    cash_change REAL NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (run_id, event_index)
);

CREATE TABLE IF NOT EXISTS sar_historical_validations (
    validation_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    result_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sar_forward_scenarios (
    scenario_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    base_run_id TEXT NOT NULL,
    result_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sar_simulation_runs_created_at
    ON sar_simulation_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sar_simulation_events_date
    ON sar_simulation_events(run_id, date, event_index);
"""


def init_sar_simulation_db(db_path: str | Path, check_same_thread: bool = True) -> sqlite3.Connection:
    """Create or open the dedicated local simulation database."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    position_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(sar_simulation_positions)")
    }
    for column, sql_type in (
        ("exit_date", "TEXT"),
        ("exit_price", "REAL"),
        ("exit_reason", "TEXT"),
    ):
        if column not in position_columns:
            conn.execute(f"ALTER TABLE sar_simulation_positions ADD COLUMN {column} {sql_type}")
    conn.commit()
    return conn


def save_historical_validation(
    conn: sqlite3.Connection,
    result: HistoricalValidationResult,
) -> str:
    """Persist a validation summary and holdout equity curve atomically."""
    payload = json.dumps(asdict(result), ensure_ascii=False, sort_keys=True)
    with conn:
        conn.execute(
            """
            INSERT INTO sar_historical_validations (validation_id, created_at, result_json)
            VALUES (?, ?, ?)
            """,
            (result.validation_id, result.created_at, payload),
        )
    return result.validation_id


def load_latest_historical_validation(
    conn: sqlite3.Connection,
) -> HistoricalValidationResult | None:
    row = conn.execute(
        """
        SELECT result_json FROM sar_historical_validations
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    payload["reference"] = ValidationPeriodMetrics(**payload["reference"])
    payload["holdout"] = ValidationPeriodMetrics(**payload["holdout"])
    payload["holdout_equity_curve"] = [
        PortfolioSnapshot(**snapshot)
        for snapshot in payload["holdout_equity_curve"]
    ]
    return HistoricalValidationResult(**payload)


def save_forward_scenario_result(
    conn: sqlite3.Connection,
    result: ForwardScenarioResult,
) -> str:
    """Persist one set of forward scenario outcomes atomically."""
    payload = json.dumps(asdict(result), ensure_ascii=False, sort_keys=True)
    with conn:
        conn.execute(
            """
            INSERT INTO sar_forward_scenarios (
                scenario_id, created_at, base_run_id, result_json
            ) VALUES (?, ?, ?, ?)
            """,
            (result.scenario_id, result.created_at, result.base_run_id, payload),
        )
    return result.scenario_id


def load_latest_forward_scenario_result(
    conn: sqlite3.Connection,
) -> ForwardScenarioResult | None:
    row = conn.execute(
        """
        SELECT result_json FROM sar_forward_scenarios
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    for scenario in payload["scenarios"]:
        scenario["daily_snapshots"] = [
            PortfolioSnapshot(**snapshot)
            for snapshot in scenario["daily_snapshots"]
        ]
        scenario["entry_candidates"] = [
            ForwardEntryCandidate(**candidate)
            for candidate in scenario.get("entry_candidates", [])
        ]
    payload["scenarios"] = [
        ForwardScenarioMetrics(**scenario)
        for scenario in payload["scenarios"]
    ]
    payload["excluded_stale_positions"] = [
        ExcludedStalePosition(**position)
        for position in payload.get("excluded_stale_positions", [])
    ]
    return ForwardScenarioResult(**payload)


def save_simulation_result(
    conn: sqlite3.Connection,
    result: SarSimulationResult,
    strategy_params: dict | None = None,
) -> str:
    """Persist one immutable calculation run and all its positions/events atomically."""
    params = dict(strategy_params or {})
    params.setdefault("version", result.strategy_version)
    params_json = json.dumps(params, ensure_ascii=False, sort_keys=True)
    created_at = datetime.now().astimezone().isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO sar_simulation_runs (
                run_id, start_date, end_date, created_at, initial_capital, cash,
                market_value, realized_pnl, unrealized_pnl, total_assets, total_pnl,
                strategy_params_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.run_id, result.start_date, result.end_date, created_at,
                result.initial_capital, result.cash, result.market_value,
                result.realized_pnl, result.unrealized_pnl, result.total_assets,
                result.total_pnl, params_json,
            ),
        )
        for kind, positions in (
            ("open", result.positions),
            ("closed", result.closed_positions),
        ):
            conn.executemany(
                """
                INSERT INTO sar_simulation_positions (
                    run_id, position_kind, position_index, stock_id, name,
                    signal_date, entry_date, entry_price, shares, cost_basis,
                    add_attempted, added, trailing_active, peak_price, last_price,
                    realized_pnl, exit_date, exit_price, exit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        result.run_id, kind, index, position.stock_id, position.name,
                        position.signal_date, position.entry_date, position.entry_price,
                        position.shares, position.cost_basis, int(position.add_attempted),
                        int(position.added), int(position.trailing_active),
                        position.peak_price, position.last_price, position.realized_pnl,
                        position.exit_date, position.exit_price, position.exit_reason,
                    )
                    for index, position in enumerate(positions)
                ],
            )
        conn.executemany(
            """
            INSERT INTO sar_simulation_events (
                run_id, event_index, date, event_type, stock_id, name, shares,
                price, fee, tax, cash_change, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    result.run_id, index, event.date, event.event_type,
                    event.stock_id, event.name, event.shares, event.price,
                    event.fee, event.tax, event.cash_change, event.detail,
                )
                for index, event in enumerate(result.events)
            ],
        )
    return result.run_id


def load_simulation_result(
    conn: sqlite3.Connection,
    run_id: str | None = None,
) -> SarSimulationResult | None:
    """Load one stored run, defaulting to the most recently saved run."""
    if run_id is None:
        row = conn.execute(
            "SELECT run_id FROM sar_simulation_runs ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        run_id = row[0]

    run = conn.execute(
        """
        SELECT run_id, start_date, end_date, initial_capital, cash,
               strategy_params_json
        FROM sar_simulation_runs WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if run is None:
        return None
    strategy_params = json.loads(run[5])
    result = SarSimulationResult(
        start_date=run[1],
        end_date=run[2],
        initial_capital=run[3],
        cash=run[4],
        strategy_version=int(strategy_params.get("version", 1)),
        max_close_price=float(strategy_params.get("max_close_price", 100.0)),
        whole_lot_trading=bool(strategy_params.get("whole_lot_trading", False)),
        run_id=run[0],
    )
    position_rows = conn.execute(
        """
        SELECT position_kind, stock_id, name, signal_date, entry_date,
               entry_price, shares, cost_basis, add_attempted, added,
               trailing_active, peak_price, last_price, realized_pnl,
               exit_date, exit_price, exit_reason
        FROM sar_simulation_positions
        WHERE run_id = ?
        ORDER BY position_kind, position_index
        """,
        (run_id,),
    ).fetchall()
    for row in position_rows:
        position = SimulatedPosition(
            stock_id=row[1],
            name=row[2],
            signal_date=row[3],
            entry_date=row[4],
            entry_price=row[5],
            shares=row[6],
            cost_basis=row[7],
            add_attempted=bool(row[8]),
            added=bool(row[9]),
            trailing_active=bool(row[10]),
            peak_price=row[11],
            last_price=row[12],
            realized_pnl=row[13],
            exit_date=row[14],
            exit_price=row[15],
            exit_reason=row[16],
        )
        if row[0] == "open":
            result.positions.append(position)
        else:
            result.closed_positions.append(position)
    event_rows = conn.execute(
        """
        SELECT date, event_type, stock_id, name, shares, price, fee, tax,
               cash_change, detail
        FROM sar_simulation_events
        WHERE run_id = ?
        ORDER BY event_index
        """,
        (run_id,),
    ).fetchall()
    result.events = [SimulationEvent(*row) for row in event_rows]
    return result


def list_simulation_runs(conn: sqlite3.Connection) -> list[dict]:
    """List saved run metadata for history selection or diagnostics."""
    rows = conn.execute(
        """
        SELECT run_id, start_date, end_date, created_at, total_assets, total_pnl
        FROM sar_simulation_runs
        ORDER BY created_at DESC, rowid DESC
        """
    ).fetchall()
    return [
        {
            "run_id": row[0], "start_date": row[1], "end_date": row[2],
            "created_at": row[3], "total_assets": row[4], "total_pnl": row[5],
        }
        for row in rows
    ]
