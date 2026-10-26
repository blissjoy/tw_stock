"""Cash-aware portfolio simulation for the daily SAR strategy."""

from __future__ import annotations

import math
import uuid
from copy import deepcopy
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.screener.indicator_precompute import compute_indicator_rows

INITIAL_CAPITAL = 100_000.0
ENTRY_BUDGET = 10_000.0
ADD_BUDGET = 5_000.0
BUY_FEE_RATE = 0.001425
SELL_FEE_RATE = 0.001425
MINIMUM_FEE = 20.0
SELL_TAX_RATE = 0.003
SAR_STRATEGY_VERSION = 2
LOT_SIZE_SHARES = 1_000

MARKET_DATA_COLUMNS = [
    "date", "stock_id", "name", "open", "high", "low", "close", "volume",
    "ma5", "ma10", "ma20", "ma60", "ma120", "ma200",
    "sar_value", "sar_is_bull", "sar_flip_days_ago",
]


@dataclass(frozen=True)
class SarStrategyParams:
    """Fixed strategy thresholds, intentionally separate from global screener settings."""

    max_flip_days_ago: int = 1
    max_bias_200_pct: float = 999.0
    min_pressure_distance_pct: float = 3.0
    max_abs_sar_distance_pct: float = 35.0
    min_volume_shares: int = 10_000
    min_strategy_volume_shares: int = 1_000_000
    min_sar_distance_pct: float = -15.0
    max_sar_distance_pct: float = 0.0
    max_close_price: float = 100.0
    whole_lot_trading: bool = False


@dataclass
class SimulatedPosition:
    stock_id: str
    name: str
    signal_date: str
    entry_date: str
    entry_price: float
    shares: int
    cost_basis: float
    add_attempted: bool = False
    added: bool = False
    trailing_active: bool = False
    peak_price: float = 0.0
    last_price: float = 0.0
    realized_pnl: float = 0.0
    exit_date: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None

    @property
    def average_cost(self) -> float:
        return self.cost_basis / self.shares

    @property
    def market_value(self) -> float:
        return self.last_price * self.shares

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.cost_basis


@dataclass(frozen=True)
class SimulationEvent:
    date: str
    event_type: str
    stock_id: str
    name: str
    shares: int
    price: float
    fee: float
    tax: float
    cash_change: float
    detail: str = ""


@dataclass(frozen=True)
class PortfolioSnapshot:
    date: str
    cash: float
    market_value: float
    total_assets: float


@dataclass(frozen=True)
class ExcludedStalePosition:
    stock_id: str
    name: str
    shares: int
    market_value: float
    last_quote_date: str | None


@dataclass
class SarSimulationResult:
    start_date: str
    end_date: str
    initial_capital: float
    cash: float
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    positions: list[SimulatedPosition] = field(default_factory=list)
    closed_positions: list[SimulatedPosition] = field(default_factory=list)
    events: list[SimulationEvent] = field(default_factory=list)
    strategy_version: int = SAR_STRATEGY_VERSION
    max_close_price: float = 100.0
    whole_lot_trading: bool = False
    daily_snapshots: list[PortfolioSnapshot] = field(default_factory=list)

    @property
    def market_value(self) -> float:
        return sum(position.market_value for position in self.positions)

    @property
    def unrealized_pnl(self) -> float:
        return sum(position.unrealized_pnl for position in self.positions)

    @property
    def realized_pnl(self) -> float:
        return sum(position.realized_pnl for position in self.closed_positions)

    @property
    def total_assets(self) -> float:
        return self.cash + self.market_value

    @property
    def total_pnl(self) -> float:
        return self.total_assets - self.initial_capital


def exclude_stale_positions(
    result: SarSimulationResult,
    latest_quote_dates: dict[str, str],
) -> tuple[SarSimulationResult, list[ExcludedStalePosition]]:
    """Return a display-safe copy without open positions lacking an end-date quote."""
    display_result = deepcopy(result)
    current_positions = []
    excluded_positions = []
    for position in display_result.positions:
        quote_date = latest_quote_dates.get(position.stock_id)
        if quote_date == result.end_date:
            current_positions.append(position)
            continue
        excluded_positions.append(ExcludedStalePosition(
            stock_id=position.stock_id,
            name=position.name,
            shares=position.shares,
            market_value=position.market_value,
            last_quote_date=quote_date,
        ))
    display_result.positions = current_positions
    return display_result, excluded_positions


def select_daily_sar_candidates(
    market_data: pd.DataFrame,
    params: SarStrategyParams = SarStrategyParams(),
) -> pd.DataFrame:
    """Apply the two SAR filters and return at most one early-signal candidate per day.

    SAR distance follows chart_data's existing convention:
    (SAR - close) / close * 100. The no-pressure check requires close to be more than
    3% above both MA60 and MA120.
    """
    missing = set(MARKET_DATA_COLUMNS) - set(market_data.columns)
    if missing:
        raise ValueError(f"market_data missing required columns: {', '.join(sorted(missing))}")
    if market_data.empty:
        return market_data.loc[:, MARKET_DATA_COLUMNS].copy()

    frame = market_data.copy()
    numeric_columns = [
        "open", "high", "low", "close", "volume", "ma5", "ma10", "ma20",
        "ma60", "ma120", "ma200", "sar_value", "sar_flip_days_ago",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["sar_distance_pct"] = (frame["sar_value"] - frame["close"]) / frame["close"] * 100
    frame["bias_200_pct"] = (frame["close"] - frame["ma200"]) / frame["ma200"] * 100
    frame["ma60_pressure_pct"] = (frame["close"] - frame["ma60"]) / frame["close"] * 100
    frame["ma120_pressure_pct"] = (frame["close"] - frame["ma120"]) / frame["close"] * 100

    valid = frame[[
        "close", "open", "volume", "ma5", "ma10", "ma20", "ma60", "ma120",
        "ma200", "sar_value", "sar_is_bull", "sar_flip_days_ago",
        "sar_distance_pct", "bias_200_pct", "ma60_pressure_pct", "ma120_pressure_pct",
    ]].notna().all(axis=1)
    basic_filter = (
        valid
        & frame["sar_is_bull"].astype("boolean").fillna(False)
        & (frame["sar_flip_days_ago"] <= params.max_flip_days_ago)
        & (frame["close"] > frame["ma200"])
        & (frame["ma5"] > frame["ma10"])
        & (frame["ma10"] > frame["ma20"])
        & (frame["bias_200_pct"] <= params.max_bias_200_pct)
        & (frame["ma60_pressure_pct"] > params.min_pressure_distance_pct)
        & (frame["ma120_pressure_pct"] > params.min_pressure_distance_pct)
        & (frame["sar_distance_pct"].abs() < params.max_abs_sar_distance_pct)
        & (frame["volume"] >= params.min_volume_shares)
    )
    strategy_filter = (
        basic_filter
        & frame["sar_distance_pct"].between(
            params.min_sar_distance_pct, params.max_sar_distance_pct, inclusive="both",
        )
        & (frame["volume"] >= params.min_strategy_volume_shares)
        & (frame["close"] <= params.max_close_price)
        & (frame["close"] > frame["open"])
    )
    candidates = frame.loc[strategy_filter].copy()
    candidates = candidates.sort_values(
        ["date", "sar_distance_pct", "stock_id"],
        ascending=[True, False, True],
        kind="stable",
    )
    candidates = candidates.drop_duplicates("date", keep="first")
    return candidates[MARKET_DATA_COLUMNS + ["sar_distance_pct"]].reset_index(drop=True)


def load_sar_market_data(
    conn,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Load OHLCV plus the existing daily SAR/MA cache for the simulation range."""
    cursor = conn.execute(
        """
        SELECT sp.date, sp.stock_id, s.name, sp.open, sp.high, sp.low, sp.close, sp.volume,
               di.ma5, di.ma10, di.ma20, di.ma60, di.ma120, di.ma200,
               di.sar_value, di.sar_is_bull, di.sar_flip_days_ago
        FROM stock_prices sp
        JOIN stocks s ON s.stock_id = sp.stock_id
        LEFT JOIN daily_indicators di ON di.stock_id = sp.stock_id AND di.date = sp.date
        WHERE sp.date BETWEEN ? AND ?
          AND s.market IN ('TWSE', 'TPEx')
        ORDER BY sp.date, sp.stock_id
        """,
        (start_date, end_date),
    )
    return pd.DataFrame(cursor.fetchall(), columns=[description[0] for description in cursor.description])


def load_sar_market_data_with_recomputed_indicators(
    conn,
    end_date: str,
) -> pd.DataFrame:
    """Load available OHLCV history and recompute MA/SAR fields without changing the DB."""
    cursor = conn.execute(
        """
        SELECT sp.date, sp.stock_id, s.name, sp.open, sp.high, sp.low, sp.close, sp.volume
        FROM stock_prices sp
        JOIN stocks s ON s.stock_id = sp.stock_id
        WHERE sp.date <= ?
          AND s.market IN ('TWSE', 'TPEx')
        ORDER BY sp.stock_id, sp.date
        """,
        (end_date,),
    )
    market_data = pd.DataFrame(
        cursor.fetchall(),
        columns=[description[0] for description in cursor.description],
    )
    if market_data.empty:
        for column in (
            "ma5", "ma10", "ma20", "ma60", "ma120", "ma200",
            "sar_value", "sar_is_bull", "sar_flip_days_ago",
        ):
            market_data[column] = pd.Series(dtype="float64")
        return market_data

    indicator_columns = [
        "ma5", "ma10", "ma20", "ma60", "ma120", "ma200",
        "sar_value", "sar_is_bull", "sar_flip_days_ago",
    ]
    indicator_values = {
        column: np.full(len(market_data), np.nan, dtype=np.float64)
        for column in indicator_columns
    }
    offset = 0
    for stock_id, stock_data in market_data.groupby("stock_id", sort=False):
        indexed_data = stock_data.set_index(pd.to_datetime(stock_data["date"]))[
            ["open", "high", "low", "close", "volume"]
        ]
        rows = compute_indicator_rows(
            str(stock_id),
            indexed_data,
            set(stock_data["date"].astype(str)),
            include_trend_position=False,
        )
        if len(rows) != len(stock_data):
            raise ValueError(f"{stock_id} 的指標列數與 OHLCV 行情列數不一致。")
        indicator_frame = pd.DataFrame.from_records(rows)
        end_offset = offset + len(stock_data)
        for column in indicator_columns:
            indicator_values[column][offset:end_offset] = pd.to_numeric(
                indicator_frame[column],
                errors="coerce",
            ).to_numpy(dtype=np.float64)
        offset = end_offset
    for column, values in indicator_values.items():
        market_data[column] = values
    return market_data


def _fee(notional: float, rate: float) -> float:
    return max(MINIMUM_FEE, notional * rate)


def _sell_position(
    position: SimulatedPosition,
    date: str,
    price: float,
    event_type: str,
    cash: float,
) -> tuple[float, SimulationEvent]:
    notional = position.shares * price
    fee = _fee(notional, SELL_FEE_RATE)
    tax = notional * SELL_TAX_RATE
    proceeds = notional - fee - tax
    event = SimulationEvent(
        date=date,
        event_type=event_type,
        stock_id=position.stock_id,
        name=position.name,
        shares=position.shares,
        price=price,
        fee=fee,
        tax=tax,
        cash_change=proceeds,
        detail=f"已實現損益 {proceeds - position.cost_basis:+,.2f}",
    )
    return cash + proceeds, event


def run_sar_portfolio_simulation(
    market_data: pd.DataFrame,
    start_date: str,
    end_date: str,
    initial_capital: float = INITIAL_CAPITAL,
    params: SarStrategyParams = SarStrategyParams(),
    initial_state: SarSimulationResult | None = None,
    initial_signal: SimulationEvent | None = None,
) -> SarSimulationResult:
    """Simulate the strategy across dates with one shared cash balance.

    Existing positions are managed first each day. The prior trading day's signal is
    entered after exits, so same-day proceeds are available for the new entry.
    """
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    missing = set(MARKET_DATA_COLUMNS) - set(market_data.columns)
    if missing:
        raise ValueError(f"market_data missing required columns: {', '.join(sorted(missing))}")

    frame = market_data.loc[
        market_data["date"].between(start_date, end_date)
    ].sort_values(["date", "stock_id"], kind="stable")
    starting_capital = initial_state.initial_capital if initial_state else initial_capital
    starting_cash = initial_state.cash if initial_state else initial_capital
    result = SarSimulationResult(
        start_date, end_date, starting_capital, starting_cash,
        strategy_version=SAR_STRATEGY_VERSION,
        max_close_price=params.max_close_price,
        whole_lot_trading=params.whole_lot_trading,
    )
    if initial_state is not None:
        result.positions = deepcopy(initial_state.positions)
        result.closed_positions = deepcopy(initial_state.closed_positions)
    if frame.empty:
        return result

    signals = select_daily_sar_candidates(frame, params)
    signal_by_date = {
        row.date: row
        for row in signals.itertuples(index=False)
    }
    selection_events = [
        SimulationEvent(
            row.date, "每日選股", row.stock_id, row.name, 0,
            float(row.close), 0.0, 0.0, 0.0,
            f"SAR 距離 {row.sar_distance_pct:.2f}%",
        )
        for row in signals.itertuples(index=False)
    ]
    missing_indicator_rows = int(frame[[
        "ma5", "ma10", "ma20", "ma60", "ma120", "ma200",
        "sar_value", "sar_is_bull", "sar_flip_days_ago",
    ]].isna().any(axis=1).sum())
    if missing_indicator_rows:
        selection_events.append(SimulationEvent(
            end_date,
            "指標缺資料",
            "全市場",
            "行情未計入選股",
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            f"{missing_indicator_rows:,} 列行情缺少必要的均線或 SAR 快取，已排除於選股。",
        ))
    daily_rows = {
        date: day.set_index("stock_id", drop=False)
        for date, day in frame.groupby("date", sort=True)
    }
    dates = list(daily_rows)

    def record_daily_snapshot(date: str) -> None:
        market_value = result.market_value
        result.daily_snapshots.append(PortfolioSnapshot(
            date=date,
            cash=result.cash,
            market_value=market_value,
            total_assets=result.cash + market_value,
        ))

    for index, date in enumerate(dates):
        day = daily_rows[date]
        if index == 0 and initial_state is not None:
            signal = initial_signal
            signal_date = initial_signal.date if initial_signal is not None else ""
        elif index == 0:
            record_daily_snapshot(date)
            continue
        else:
            signal_date = dates[index - 1]
            signal = signal_by_date.get(signal_date)

        remaining_positions: list[SimulatedPosition] = []

        for position in result.positions:
            quote = day.loc[position.stock_id] if position.stock_id in day.index else None
            if quote is None:
                remaining_positions.append(position)
                continue
            position.last_price = float(quote.close)
            low = float(quote.low)
            high = float(quote.high)

            if date == position.entry_date:
                position.peak_price = max(position.peak_price, high)
                remaining_positions.append(position)
                continue

            if not position.add_attempted and low <= position.entry_price * 0.85:
                add_price = position.entry_price * 0.85
                add_shares = (
                    LOT_SIZE_SHARES if params.whole_lot_trading
                    else math.floor(ADD_BUDGET / add_price)
                )
                add_notional = add_shares * add_price
                add_fee = _fee(add_notional, BUY_FEE_RATE) if add_shares > 0 else 0.0
                position.add_attempted = True
                if add_shares > 0 and result.cash >= add_notional + add_fee:
                    result.cash -= add_notional + add_fee
                    position.shares += add_shares
                    position.cost_basis += add_notional + add_fee
                    position.added = True
                    result.events.append(SimulationEvent(
                        date, "加碼", position.stock_id, position.name, add_shares,
                        add_price, add_fee, 0.0, -(add_notional + add_fee),
                    ))
                else:
                    result.events.append(SimulationEvent(
                        date, "加碼資金不足", position.stock_id, position.name,
                        add_shares, add_price, add_fee, 0.0, 0.0,
                    ))

            stop_price = position.average_cost * 0.80
            if low <= stop_price:
                result.cash, event = _sell_position(
                    position, date, stop_price, "停損出場", result.cash,
                )
                position.realized_pnl = event.cash_change - position.cost_basis
                position.exit_date = date
                position.exit_price = stop_price
                position.exit_reason = event.event_type
                result.events.append(event)
                result.closed_positions.append(position)
                continue

            if position.trailing_active:
                peak_return = position.peak_price / position.average_cost - 1
                trailing_price = position.average_cost * (1 + peak_return - 0.03)
                if low <= trailing_price:
                    result.cash, event = _sell_position(
                        position, date, trailing_price, "移動停利出場", result.cash,
                    )
                    position.realized_pnl = event.cash_change - position.cost_basis
                    position.exit_date = date
                    position.exit_price = trailing_price
                    position.exit_reason = event.event_type
                    result.events.append(event)
                    result.closed_positions.append(position)
                    continue

            position.peak_price = max(position.peak_price, high)
            if not position.trailing_active and position.peak_price >= position.average_cost * 1.16:
                position.trailing_active = True
                result.events.append(SimulationEvent(
                    date, "移動停利啟動", position.stock_id, position.name,
                    position.shares, position.peak_price, 0.0, 0.0, 0.0,
                ))
            remaining_positions.append(position)

        result.positions = remaining_positions

        if signal is None:
            record_daily_snapshot(date)
            continue

        entry_price = float(day.loc[signal.stock_id].high) if signal.stock_id in day.index else None
        if entry_price is None or not math.isfinite(entry_price) or entry_price <= 0:
            result.events.append(SimulationEvent(
                date, "進場資料缺失", signal.stock_id, signal.name,
                0, 0.0, 0.0, 0.0, 0.0,
                f"訊號日 {signal_date}；下一交易日缺少有效行情，訊號略過",
            ))
            record_daily_snapshot(date)
            continue

        shares = (
            LOT_SIZE_SHARES if params.whole_lot_trading
            else math.floor(ENTRY_BUDGET / entry_price)
        )
        notional = shares * entry_price
        fee = _fee(notional, BUY_FEE_RATE) if shares > 0 else 0.0
        total_cost = notional + fee
        if shares <= 0 or result.cash < total_cost:
            result.events.append(SimulationEvent(
                date, "資金不足跳過", signal.stock_id, signal.name,
                shares, entry_price, fee, 0.0, 0.0,
                f"訊號日 {signal_date}；需要 {total_cost:,.2f}，"
                f"可用現金 {result.cash:,.2f}",
            ))
            record_daily_snapshot(date)
            continue

        result.cash -= total_cost
        position = SimulatedPosition(
            stock_id=signal.stock_id,
            name=signal.name,
            signal_date=signal_date,
            entry_date=date,
            entry_price=entry_price,
            shares=shares,
            cost_basis=total_cost,
            peak_price=entry_price,
            last_price=float(day.loc[signal.stock_id].close),
        )
        result.positions.append(position)
        result.events.append(SimulationEvent(
            date, "進場", position.stock_id, position.name, shares,
            entry_price, fee, 0.0, -total_cost,
            f"訊號日 {signal_date}",
        ))
        record_daily_snapshot(date)

    result.events.extend(selection_events)
    event_priority = {"每日選股": 0, "指標缺資料": 0}
    result.events.sort(key=lambda event: (event.date, event_priority.get(event.event_type, 1)))
    return result
