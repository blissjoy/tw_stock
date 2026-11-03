"""Build next-day SAR simulation watch items from the latest saved simulation state."""

from __future__ import annotations

import json
from dataclasses import fields

from src.backtest.sar_portfolio import (
    SAR_STRATEGY_VERSION,
    SarStrategyParams,
    load_sar_market_data_with_recomputed_indicators,
    run_sar_portfolio_simulation,
)
from src.data.sar_simulation_storage import load_simulation_result


def build_sar_next_day_preview(
    market_conn,
    simulation_conn,
    as_of_date: str,
) -> dict[str, list[dict]]:
    """Return today's buy candidates and open simulated positions with today's close."""
    latest_run = simulation_conn.execute(
        """
        SELECT run_id, end_date, strategy_params_json
        FROM sar_simulation_runs
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """
    ).fetchone()
    if latest_run is None:
        raise ValueError("尚無實戰模擬紀錄，無法產生隔日買賣提示。")

    run_id, end_date, params_json = latest_run
    result = load_simulation_result(simulation_conn, run_id)
    if result is None:
        raise ValueError(f"找不到最新實戰模擬紀錄：{run_id}")
    if result.strategy_version != SAR_STRATEGY_VERSION:
        raise ValueError(
            f"實戰模擬策略版本為 {result.strategy_version}，"
            f"目前版本為 {SAR_STRATEGY_VERSION}；請先重新執行模擬。"
        )
    if end_date > as_of_date:
        raise ValueError(f"實戰模擬日期 {end_date} 晚於提示日期 {as_of_date}。")

    parameters = json.loads(params_json)
    parameter_names = {item.name for item in fields(SarStrategyParams)}
    params = SarStrategyParams(**{
        name: value for name, value in parameters.items() if name in parameter_names
    })

    if end_date < as_of_date:
        market_data = load_sar_market_data_with_recomputed_indicators(market_conn, as_of_date)
        market_dates = market_data["date"].astype(str)
        if not market_dates.eq(as_of_date).any():
            raise ValueError(f"行情資料缺少 {as_of_date}，無法計算隔日提示。")
        extension_dates = market_dates[market_dates > end_date]
        if extension_dates.empty:
            raise ValueError(f"實戰模擬 {end_date} 後沒有可用行情。")

        initial_signal = next(
            (
                event for event in reversed(result.events)
                if event.date == end_date and event.event_type == "每日選股"
            ),
            None,
        )
        result = run_sar_portfolio_simulation(
            market_data,
            start_date=min(extension_dates),
            end_date=as_of_date,
            initial_capital=result.initial_capital,
            params=params,
            initial_state=result,
            initial_signal=initial_signal,
        )
        today_rows = market_data.loc[market_dates == as_of_date]
    else:
        today_rows = market_conn.execute(
            "SELECT stock_id, close FROM stock_prices WHERE date = ?",
            (as_of_date,),
        ).fetchall()
        today_rows = [
            {"stock_id": stock_id, "close": close}
            for stock_id, close in today_rows
        ]

    closes = {
        str(row["stock_id"]): float(row["close"])
        for row in (
            today_rows.to_dict("records") if hasattr(today_rows, "to_dict") else today_rows
        )
        if row["close"] is not None
    }
    buy_events = [
        event for event in result.events
        if event.date == as_of_date and event.event_type == "每日選股"
    ]
    buy_items = [
        {
            "stock_id": event.stock_id,
            "name": event.name,
            "close": closes.get(event.stock_id, event.price),
        }
        for event in buy_events
    ]
    sell_items = [
        {
            "stock_id": position.stock_id,
            "name": position.name,
            "close": closes.get(position.stock_id),
        }
        for position in result.positions
    ]
    return {"buy_items": buy_items, "sell_items": sell_items}
