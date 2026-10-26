import pandas as pd
import pytest

from src.backtest.sar_portfolio import (
    SarSimulationResult,
    SimulatedPosition,
    SimulationEvent,
)
from src.backtest.sar_scenario import (
    build_historical_price_path,
    generate_future_trading_dates,
    run_sar_historical_path_scenarios,
    select_historical_scenario_windows,
)


def _historical_prices(dates):
    rows = []
    for index, date in enumerate(dates):
        close = 40 + index * 0.2
        rows.append({
            "date": date,
            "stock_id": "2330",
            "name": "台積電",
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 2_000_000,
        })
    return pd.DataFrame(rows)


def _index_data(dates):
    return pd.DataFrame({
        "date": dates,
        "close": [100, 101, 102, 103, 110, 110, 110, 90, 89, 88, 88, 88, 100, 101, 102],
    })


def test_scenario_windows_choose_disjoint_equal_length_market_paths():
    dates = pd.bdate_range("2025-01-01", periods=15).strftime("%Y-%m-%d").tolist()

    windows = select_historical_scenario_windows(
        _index_data(dates),
        dates[-1],
        horizon_days=2,
    )

    assert [window.name for window in windows] == [
        "大盤漲幅較大", "大盤接近平盤", "大盤跌幅較大",
    ]
    assert all(len(window.trading_dates) == 2 for window in windows)
    ranges = [
        set(dates.index(date) for date in window.trading_dates)
        for window in windows
    ]
    assert not ranges[0] & ranges[1]
    assert not ranges[0] & ranges[2]
    assert not ranges[1] & ranges[2]
    assert windows[0].index_return_pct > windows[1].index_return_pct
    assert windows[2].index_return_pct < windows[1].index_return_pct


def test_future_dates_skip_weekends_and_known_exchange_holidays():
    dates = generate_future_trading_dates(
        "2026-12-31",
        3,
        {"2027-01-01", "2027-01-04"},
    )
    assert dates == ["2027-01-05", "2027-01-06", "2027-01-07"]


def test_historical_price_path_scales_ohlc_and_indicators_to_current_quote():
    dates = pd.bdate_range("2024-01-01", periods=260).strftime("%Y-%m-%d").tolist()
    history = _historical_prices(dates)
    window = select_historical_scenario_windows(
        pd.DataFrame({"date": dates, "close": [100 + index for index in range(260)]}),
        dates[-1],
        horizon_days=2,
    )[0]

    projected = build_historical_price_path(
        history.loc[history["date"] <= window.end_date],
        pd.DataFrame([{"stock_id": "2330", "name": "台積電", "close": 100.0}]),
        window,
        "2026-01-05",
        {"2026-01-06"},
    )
    first_source_day = history.loc[history["date"] == window.trading_dates[0]].iloc[0]
    previous_close = history.loc[history["date"] < window.start_date].iloc[-1]["close"]

    assert projected.iloc[0]["date"] == "2026-01-07"
    assert projected.iloc[0]["close"] == pytest.approx(
        100.0 * first_source_day["close"] / previous_close,
    )
    assert projected.iloc[0]["volume"] == first_source_day["volume"]
    assert projected.iloc[0]["sar_value"] > 0


def test_scenario_comparison_continues_saved_cash_and_enters_pending_signal():
    dates = pd.bdate_range("2025-01-01", periods=15).strftime("%Y-%m-%d").tolist()
    index_data = _index_data(dates)
    history = _historical_prices(dates)
    windows = select_historical_scenario_windows(index_data, dates[-1], horizon_days=2)
    source_data = {
        (window.start_date, window.end_date): history.loc[
            history["date"].between(window.warmup_start_date, window.end_date)
        ].copy()
        for window in windows
    }
    base = SarSimulationResult(
        start_date="2024-01-01",
        end_date=dates[-1],
        initial_capital=100_000,
        cash=80_000,
        run_id="seed-run",
    )
    base.events.append(SimulationEvent(
        date=dates[-1],
        event_type="每日選股",
        stock_id="2330",
        name="台積電",
        shares=0,
        price=history.iloc[-1]["close"],
        fee=0,
        tax=0,
        cash_change=0,
    ))

    result = run_sar_historical_path_scenarios(
        index_data,
        source_data,
        pd.DataFrame([{
            "stock_id": "2330",
            "name": "台積電",
            "close": 60.0,
            "quote_date": dates[-1],
        }]),
        base,
        horizon_days=2,
    )

    assert result.base_run_id == "seed-run"
    assert result.base_end_date == dates[-1]
    assert result.starting_assets == 80_000
    assert len(result.scenarios) == 3
    assert all(scenario.new_entry_count == 1 for scenario in result.scenarios)
    assert all(len(scenario.daily_snapshots) == 2 for scenario in result.scenarios)
    assert all(len(scenario.entry_candidates) == 1 for scenario in result.scenarios)
    for scenario in result.scenarios:
        candidate = scenario.entry_candidates[0]
        assert candidate.stock_id == "2330"
        assert candidate.signal_date == dates[-1]
        assert candidate.entry_date == scenario.daily_snapshots[0].date
        assert candidate.entry_price is not None
        assert candidate.shares > 0
        assert candidate.funding_status == "資金足夠，情境中可進場"
    assert base.cash == 80_000
    assert base.positions == []


def test_selected_scenario_returns_only_requested_scenario():
    dates = pd.bdate_range("2025-01-01", periods=15).strftime("%Y-%m-%d").tolist()
    index_data = _index_data(dates)
    history = _historical_prices(dates)
    windows = select_historical_scenario_windows(index_data, dates[-1], horizon_days=2)
    source_data = {
        (window.start_date, window.end_date): history.loc[
            history["date"].between(window.warmup_start_date, window.end_date)
        ].copy()
        for window in windows
    }
    base = SarSimulationResult(
        start_date="2024-01-01",
        end_date=dates[-1],
        initial_capital=100_000,
        cash=80_000,
        run_id="seed-run",
    )

    result = run_sar_historical_path_scenarios(
        index_data,
        source_data,
        pd.DataFrame([{
            "stock_id": "2330",
            "name": "台積電",
            "close": 60.0,
            "quote_date": dates[-1],
        }]),
        base,
        horizon_days=2,
        selected_scenario="大盤接近平盤",
    )

    assert result.selected_scenario == "大盤接近平盤"
    assert [scenario.name for scenario in result.scenarios] == ["大盤接近平盤"]


def test_scenario_excludes_stale_positions_without_mutating_base_run():
    dates = pd.bdate_range("2025-01-01", periods=15).strftime("%Y-%m-%d").tolist()
    index_data = _index_data(dates)
    history = _historical_prices(dates)
    windows = select_historical_scenario_windows(index_data, dates[-1], horizon_days=2)
    source_data = {
        (window.start_date, window.end_date): history.loc[
            history["date"].between(window.warmup_start_date, window.end_date)
        ].copy()
        for window in windows
    }
    stale_position = SimulatedPosition(
        stock_id="2809",
        name="京城銀",
        signal_date="2025-07-01",
        entry_date="2025-07-02",
        entry_price=52.9,
        shares=1_000,
        cost_basis=52_975,
        last_price=56.6,
    )
    base = SarSimulationResult(
        start_date="2024-01-01",
        end_date=dates[-1],
        initial_capital=100_000,
        cash=80_000,
        run_id="seed-run",
        positions=[stale_position],
    )
    current_quotes = pd.DataFrame([
        {
            "stock_id": "2330",
            "name": "台積電",
            "close": 60.0,
            "quote_date": dates[-1],
        },
        {
            "stock_id": "2809",
            "name": "京城銀",
            "close": 56.6,
            "quote_date": "2025-09-17",
        },
    ])

    result = run_sar_historical_path_scenarios(
        index_data,
        source_data,
        current_quotes,
        base,
        horizon_days=2,
        selected_scenario="大盤接近平盤",
    )

    assert result.starting_assets == 80_000
    assert result.excluded_stale_positions[0].stock_id == "2809"
    assert result.excluded_stale_positions[0].last_quote_date == "2025-09-17"
    assert result.excluded_stale_positions[0].market_value == 56_600
    assert result.scenarios[0].start_assets == 80_000
    assert [position.stock_id for position in base.positions] == ["2809"]
