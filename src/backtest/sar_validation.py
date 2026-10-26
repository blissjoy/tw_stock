"""Chronological in-sample and out-of-sample checks for the fixed SAR strategy."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd

from src.backtest.sar_portfolio import (
    PortfolioSnapshot,
    SarStrategyParams,
    run_sar_portfolio_simulation,
)


@dataclass(frozen=True)
class ValidationPeriodMetrics:
    label: str
    start_date: str
    end_date: str
    trading_days: int
    signal_count: int
    closed_trade_count: int
    win_rate_pct: float
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    total_return_pct: float
    cash_benchmark_return_pct: float
    max_drawdown_pct: float
    average_invested_pct: float


@dataclass
class HistoricalValidationResult:
    validation_id: str
    created_at: str
    initial_capital: float
    params: dict
    reference: ValidationPeriodMetrics
    holdout: ValidationPeriodMetrics
    holdout_equity_curve: list[PortfolioSnapshot] = field(default_factory=list)


def _calculate_period_metrics(
    label: str,
    market_data: pd.DataFrame,
    start_date: str,
    end_date: str,
    initial_capital: float,
    params: SarStrategyParams,
) -> tuple[ValidationPeriodMetrics, list[PortfolioSnapshot]]:
    result = run_sar_portfolio_simulation(
        market_data,
        start_date,
        end_date,
        initial_capital=initial_capital,
        params=params,
    )
    snapshots = result.daily_snapshots
    if not snapshots:
        raise ValueError(f"{label}在指定日期區間沒有可用交易日行情。")

    equity_values = [initial_capital, *(snapshot.total_assets for snapshot in snapshots)]
    peak = equity_values[0]
    max_drawdown_pct = 0.0
    for equity in equity_values[1:]:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak - equity) / peak * 100)

    closed_positions = result.closed_positions
    win_count = sum(position.realized_pnl > 0 for position in closed_positions)
    win_rate_pct = win_count / len(closed_positions) * 100 if closed_positions else 0.0
    average_invested_pct = (
        sum(snapshot.market_value for snapshot in snapshots)
        / len(snapshots)
        / initial_capital
        * 100
    )
    metrics = ValidationPeriodMetrics(
        label=label,
        start_date=snapshots[0].date,
        end_date=snapshots[-1].date,
        trading_days=len(snapshots),
        signal_count=sum(event.event_type == "每日選股" for event in result.events),
        closed_trade_count=len(closed_positions),
        win_rate_pct=win_rate_pct,
        realized_pnl=result.realized_pnl,
        unrealized_pnl=result.unrealized_pnl,
        total_pnl=result.total_pnl,
        total_return_pct=result.total_pnl / initial_capital * 100,
        cash_benchmark_return_pct=0.0,
        max_drawdown_pct=max_drawdown_pct,
        average_invested_pct=average_invested_pct,
    )
    return metrics, snapshots


def run_sar_historical_validation(
    market_data: pd.DataFrame,
    reference_start_date: str,
    reference_end_date: str,
    holdout_start_date: str,
    holdout_end_date: str,
    initial_capital: float,
    params: SarStrategyParams,
) -> HistoricalValidationResult:
    """Compare fixed rules across two strictly chronological, independent portfolios."""
    if reference_start_date > reference_end_date:
        raise ValueError("參考期起日不可晚於迄日。")
    if holdout_start_date > holdout_end_date:
        raise ValueError("樣本外起日不可晚於迄日。")
    if reference_end_date >= holdout_start_date:
        raise ValueError("參考期必須完整早於樣本外期，兩者不可重疊。")
    if initial_capital <= 0:
        raise ValueError("模擬本金必須大於 0。")

    reference, _ = _calculate_period_metrics(
        "前段期間",
        market_data,
        reference_start_date,
        reference_end_date,
        initial_capital,
        params,
    )
    holdout, equity_curve = _calculate_period_metrics(
        "後段期間",
        market_data,
        holdout_start_date,
        holdout_end_date,
        initial_capital,
        params,
    )
    return HistoricalValidationResult(
        validation_id=str(uuid.uuid4()),
        created_at=datetime.now().astimezone().isoformat(),
        initial_capital=initial_capital,
        params=asdict(params),
        reference=reference,
        holdout=holdout,
        holdout_equity_curve=equity_curve,
    )
