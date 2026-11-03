import pandas as pd

from src.backtest import sar_next_day_preview
from src.backtest.sar_portfolio import SarSimulationResult, SimulatedPosition
from src.data.sar_simulation_storage import init_sar_simulation_db, save_simulation_result


def _market_row(date, stock_id, **overrides):
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


def test_build_sar_next_day_preview_continues_latest_run_and_uses_today_close(monkeypatch, tmp_path):
    simulation_conn = init_sar_simulation_db(tmp_path / "simulation.db")
    prior_result = SarSimulationResult(
        start_date="2026-01-01",
        end_date="2026-01-05",
        initial_capital=100_000,
        cash=90_000,
        run_id="baseline",
        positions=[
            SimulatedPosition(
                stock_id="2454",
                name="聯發科",
                signal_date="2026-01-02",
                entry_date="2026-01-05",
                entry_price=50.0,
                shares=100,
                cost_basis=5_020.0,
                last_price=50.0,
            ),
        ],
    )
    save_simulation_result(simulation_conn, prior_result, {"version": 2})
    market_data = pd.DataFrame([
        _market_row(
            "2026-01-06",
            "2330",
            close=80.0,
            open=79.0,
            high=81.0,
            low=78.0,
            sar_value=78.4,
        ),
        _market_row(
            "2026-01-06",
            "2454",
            close=52.0,
            open=51.0,
            high=54.0,
            low=50.0,
            sar_is_bull=0,
        ),
    ])
    monkeypatch.setattr(
        sar_next_day_preview,
        "load_sar_market_data_with_recomputed_indicators",
        lambda conn, end_date: market_data,
    )

    preview = sar_next_day_preview.build_sar_next_day_preview(
        object(), simulation_conn, "2026-01-06",
    )

    assert preview["buy_items"] == [
        {"stock_id": "2330", "name": "股票2330", "close": 80.0},
    ]
    assert preview["sell_items"] == [
        {"stock_id": "2454", "name": "聯發科", "close": 52.0},
    ]
    simulation_conn.close()


def test_build_sar_next_day_preview_requires_a_saved_simulation(tmp_path):
    simulation_conn = init_sar_simulation_db(tmp_path / "empty.db")

    try:
        sar_next_day_preview.build_sar_next_day_preview(
            object(), simulation_conn, "2026-01-06",
        )
    except ValueError as exc:
        assert "尚無實戰模擬紀錄" in str(exc)
    else:
        raise AssertionError("應拒絕沒有模擬紀錄的隔日提示")
    finally:
        simulation_conn.close()
