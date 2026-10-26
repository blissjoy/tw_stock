"""Forward what-if simulations using replayed historical market paths."""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd

from src.backtest.sar_portfolio import (
    MARKET_DATA_COLUMNS,
    PortfolioSnapshot,
    SarSimulationResult,
    SarStrategyParams,
    SimulationEvent,
    run_sar_portfolio_simulation,
)
from src.screener.indicator_precompute import compute_indicator_rows


@dataclass(frozen=True)
class HistoricalScenarioWindow:
    name: str
    start_date: str
    end_date: str
    index_return_pct: float
    trading_dates: tuple[str, ...]
    warmup_start_date: str


@dataclass
class ForwardScenarioMetrics:
    name: str
    source_start_date: str
    source_end_date: str
    source_index_return_pct: float
    start_assets: float
    ending_assets: float
    scenario_pnl: float
    scenario_return_pct: float
    ending_cash: float
    ending_market_value: float
    new_entry_count: int
    exit_count: int
    ending_open_position_count: int
    daily_snapshots: list[PortfolioSnapshot]
    entry_candidates: list[ForwardEntryCandidate] = field(default_factory=list)

@dataclass
class ForwardEntryCandidate:
    signal_date: str
    entry_date: str
    stock_id: str
    name: str
    signal_close: float
    sar_distance_pct: float
    entry_price: float | None
    shares: int
    funding_status: str


@dataclass(frozen=True)
class ExcludedStalePosition:
    stock_id: str
    name: str
    shares: int
    market_value: float
    last_quote_date: str | None
    reason: str


@dataclass
class ForwardScenarioResult:
    scenario_id: str
    created_at: str
    base_run_id: str
    base_end_date: str
    initial_capital: float
    starting_assets: float
    horizon_days: int
    params: dict
    scenarios: list[ForwardScenarioMetrics]
    selected_scenario: str = ""
    excluded_stale_positions: list[ExcludedStalePosition] = field(default_factory=list)


def _build_entry_candidates(
    events: list[SimulationEvent],
    trading_dates: list[str],
    anchor_date: str,
    holidays: set[str] | None,
) -> list[ForwardEntryCandidate]:
    date_indices = {date: index for index, date in enumerate(trading_dates)}
    candidate_rows: list[ForwardEntryCandidate] = []
    signal_events = [
        event for event in events
        if event.event_type == "每日選股"
    ]
    for signal in signal_events:
        signal_date = signal.date
        signal_index = date_indices.get(signal_date)
        if signal_index is None:
            entry_index = 0
        else:
            entry_index = signal_index + 1

        execution = next(
            (
                event for event in events
                if event.stock_id == signal.stock_id
                and event.event_type in {"進場", "資金不足跳過", "進場資料缺失"}
                and event.detail.startswith(f"訊號日 {signal_date}")
            ),
            None,
        )
        if execution is not None:
            status_by_event = {
                "進場": "資金足夠，情境中可進場",
                "資金不足跳過": "資金不足，情境中略過",
                "進場資料缺失": "缺少進場行情，略過",
            }
            entry_date = execution.date
            entry_price = execution.price if execution.price > 0 else None
            shares = execution.shares
            funding_status = status_by_event[execution.event_type]
        elif entry_index < len(trading_dates):
            entry_date = trading_dates[entry_index]
            entry_price = None
            shares = 0
            funding_status = "進場日超出行情資料"
        else:
            entry_date = generate_future_trading_dates(
                trading_dates[-1] if trading_dates else anchor_date,
                1,
                holidays,
            )[0]
            entry_price = None
            shares = 0
            funding_status = "進場日在推演區間外"

        try:
            sar_distance_pct = float(signal.detail.rsplit(" ", 1)[-1].rstrip("%"))
        except ValueError:
            sar_distance_pct = 0.0
        candidate_rows.append(ForwardEntryCandidate(
            signal_date=signal_date,
            entry_date=entry_date,
            stock_id=signal.stock_id,
            name=signal.name,
            signal_close=signal.price,
            sar_distance_pct=sar_distance_pct,
            entry_price=entry_price,
            shares=shares,
            funding_status=funding_status,
        ))
    return candidate_rows


def select_historical_scenario_windows(
    index_data: pd.DataFrame,
    as_of_date: str,
    horizon_days: int,
    warmup_days: int = 240,
) -> list[HistoricalScenarioWindow]:
    """Select disjoint strong, near-flat, and weak index windows of equal length."""
    if horizon_days <= 0:
        raise ValueError("情境交易日數必須大於 0。")
    missing = {"date", "close"} - set(index_data.columns)
    if missing:
        raise ValueError(f"大盤資料缺少欄位：{', '.join(sorted(missing))}")
    frame = index_data.loc[index_data["date"] <= as_of_date].sort_values("date")
    frame = frame.dropna(subset=["close"])
    dates = frame["date"].astype(str).tolist()
    closes = pd.to_numeric(frame["close"], errors="coerce").to_numpy()

    candidates: list[tuple[int, int, float]] = []
    for start in range(1, len(dates) - horizon_days + 1):
        end = start + horizon_days - 1
        prior_close = closes[start - 1]
        end_close = closes[end]
        if prior_close <= 0 or end_close <= 0:
            continue
        candidates.append((start, end, (end_close / prior_close - 1) * 100))
    if len(candidates) < 3:
        raise ValueError(
            f"截至 {as_of_date} 的大盤資料不足，無法選出 3 段各 {horizon_days} 個交易日的行情。"
        )

    def overlaps(left: tuple[int, int, float], right: tuple[int, int, float]) -> bool:
        return left[0] <= right[1] and right[0] <= left[1]

    strong = max(candidates, key=lambda item: item[2])
    available = [item for item in candidates if not overlaps(item, strong)]
    if not available:
        raise ValueError("無法找到互不重疊的歷史行情區間。")
    weak = min(available, key=lambda item: item[2])
    available = [item for item in available if not overlaps(item, weak)]
    if not available:
        raise ValueError("無法找到第三段互不重疊的歷史行情區間。")
    near_flat = min(available, key=lambda item: abs(item[2]))

    selected = (
        ("大盤漲幅較大", strong),
        ("大盤接近平盤", near_flat),
        ("大盤跌幅較大", weak),
    )
    windows: list[HistoricalScenarioWindow] = []
    for name, (start, end, index_return_pct) in selected:
        warmup_index = max(0, start - warmup_days)
        windows.append(HistoricalScenarioWindow(
            name=name,
            start_date=dates[start],
            end_date=dates[end],
            index_return_pct=float(index_return_pct),
            trading_dates=tuple(dates[start : end + 1]),
            warmup_start_date=dates[warmup_index],
        ))
    return windows


def generate_future_trading_dates(
    anchor_date: str,
    trading_days: int,
    holidays: set[str] | None = None,
) -> list[str]:
    """Generate weekdays after the anchor, excluding any known exchange holidays."""
    if trading_days <= 0:
        raise ValueError("情境交易日數必須大於 0。")
    holiday_dates = holidays or set()
    date = pd.Timestamp(anchor_date).normalize() + pd.Timedelta(days=1)
    future_dates: list[str] = []
    while len(future_dates) < trading_days:
        date_text = date.strftime("%Y-%m-%d")
        if date.weekday() < 5 and date_text not in holiday_dates:
            future_dates.append(date_text)
        date += pd.Timedelta(days=1)
    return future_dates


def build_historical_price_path(
    historical_market_data: pd.DataFrame,
    current_quotes: pd.DataFrame,
    window: HistoricalScenarioWindow,
    anchor_date: str,
    holidays: set[str] | None = None,
) -> pd.DataFrame:
    """Scale a historical OHLCV path to current prices and recompute its indicators."""
    missing = set(MARKET_DATA_COLUMNS[:8]) - set(historical_market_data.columns)
    if missing:
        raise ValueError(f"歷史行情缺少欄位：{', '.join(sorted(missing))}")
    current_required = {"stock_id", "name", "close"}
    current_missing = current_required - set(current_quotes.columns)
    if current_missing:
        raise ValueError(f"目前行情缺少欄位：{', '.join(sorted(current_missing))}")

    quotes = current_quotes.drop_duplicates("stock_id", keep="last").set_index("stock_id")
    source_dates = list(window.trading_dates)
    future_dates = generate_future_trading_dates(anchor_date, len(source_dates), holidays)
    future_date_by_source = dict(zip(source_dates, future_dates))
    projected_rows: list[dict] = []

    for stock_id, stock_history in historical_market_data.groupby("stock_id", sort=False):
        if stock_id not in quotes.index:
            continue
        stock_history = stock_history.sort_values("date", kind="stable")
        prior_rows = stock_history.loc[stock_history["date"] < window.start_date]
        source_rows = stock_history.loc[
            stock_history["date"].between(window.start_date, window.end_date)
        ]
        if prior_rows.empty or source_rows.empty:
            continue
        previous_source_close = float(prior_rows.iloc[-1]["close"])
        current_close = float(quotes.loc[stock_id]["close"])
        if previous_source_close <= 0 or current_close <= 0:
            continue
        price_scale = current_close / previous_source_close

        indexed_history = stock_history.set_index(pd.to_datetime(stock_history["date"]))[
            ["open", "high", "low", "close", "volume"]
        ]
        indicator_rows = compute_indicator_rows(
            str(stock_id),
            indexed_history,
            set(source_dates),
            include_trend_position=False,
        )
        indicators_by_date = {row["date"]: row for row in indicator_rows}
        current_name = str(quotes.loc[stock_id]["name"])
        for source_row in source_rows.itertuples(index=False):
            source_date = str(source_row.date)
            indicator = indicators_by_date.get(source_date)
            if indicator is None:
                continue
            projected = {
                "date": future_date_by_source[source_date],
                "stock_id": str(stock_id),
                "name": current_name,
                "open": float(source_row.open) * price_scale,
                "high": float(source_row.high) * price_scale,
                "low": float(source_row.low) * price_scale,
                "close": float(source_row.close) * price_scale,
                "volume": float(source_row.volume),
            }
            for column in ("ma5", "ma10", "ma20", "ma60", "ma120", "ma200", "sar_value"):
                value = indicator[column]
                projected[column] = float(value) * price_scale if value is not None else float("nan")
            projected["sar_is_bull"] = indicator["sar_is_bull"]
            projected["sar_flip_days_ago"] = indicator["sar_flip_days_ago"]
            projected_rows.append(projected)

    if not projected_rows:
        raise ValueError(
            f"{window.start_date}～{window.end_date} 無法與目前持股行情對齊，沒有可推演的股票資料。"
        )
    return pd.DataFrame(projected_rows, columns=MARKET_DATA_COLUMNS).sort_values(
        ["date", "stock_id"], kind="stable",
    ).reset_index(drop=True)


def _next_trading_signal(base_result: SarSimulationResult) -> SimulationEvent | None:
    for event in reversed(base_result.events):
        if event.date == base_result.end_date and event.event_type == "每日選股":
            return event
    return None


def run_sar_historical_path_scenarios(
    index_data: pd.DataFrame,
    source_market_data: dict[tuple[str, str], pd.DataFrame],
    current_quotes: pd.DataFrame,
    base_result: SarSimulationResult,
    horizon_days: int,
    holidays: set[str] | None = None,
    selected_scenario: str | None = None,
) -> ForwardScenarioResult:
    """Continue a saved portfolio along selected historical price path(s)."""
    if "quote_date" not in current_quotes.columns:
        raise ValueError("目前行情缺少 quote_date，無法確認持倉報價是否過期。")
    quotes = current_quotes.copy()
    quotes["quote_date"] = quotes["quote_date"].astype(str)
    current_quotes = quotes.loc[quotes["quote_date"] == base_result.end_date].copy()
    current_quote_dates = quotes.drop_duplicates("stock_id", keep="last").set_index("stock_id")[
        "quote_date"
    ]
    projection_base = deepcopy(base_result)
    excluded_stale_positions: list[ExcludedStalePosition] = []
    valid_positions = []
    for position in projection_base.positions:
        quote_date = current_quote_dates.get(position.stock_id)
        if quote_date == base_result.end_date:
            valid_positions.append(position)
            continue
        excluded_stale_positions.append(ExcludedStalePosition(
            stock_id=position.stock_id,
            name=position.name,
            shares=position.shares,
            market_value=position.market_value,
            last_quote_date=str(quote_date) if quote_date is not None else None,
            reason=(
                f"最新行情日期早於推演起點 {base_result.end_date}"
                if quote_date is not None else "找不到推演起點當日或之前的行情"
            ),
        ))
    projection_base.positions = valid_positions

    windows = select_historical_scenario_windows(
        index_data,
        projection_base.end_date,
        horizon_days,
    )
    if selected_scenario is not None:
        windows = [window for window in windows if window.name == selected_scenario]
        if not windows:
            raise ValueError(f"未知的推演情境：{selected_scenario}")
    params = SarStrategyParams(
        max_close_price=base_result.max_close_price,
        whole_lot_trading=base_result.whole_lot_trading,
    )
    pending_signal = _next_trading_signal(projection_base)
    scenarios: list[ForwardScenarioMetrics] = []

    for window in windows:
        source_key = (window.start_date, window.end_date)
        if source_key not in source_market_data:
            raise ValueError(f"缺少 {window.name} 所需的歷史行情。")
        projected_market = build_historical_price_path(
            source_market_data[source_key],
            current_quotes,
            window,
            projection_base.end_date,
            holidays,
        )
        projected_dates = sorted(projected_market["date"].unique())
        projected_result = run_sar_portfolio_simulation(
            projected_market,
            projected_dates[0],
            projected_dates[-1],
            initial_capital=            projection_base.initial_capital,
            params=params,
            initial_state=projection_base,
            initial_signal=pending_signal,
        )
        candidate_events = list(projected_result.events)
        if pending_signal is not None:
            candidate_events.append(pending_signal)
        start_assets = projection_base.total_assets
        if start_assets <= 0:
            raise ValueError("策略模擬期末總資產必須大於 0，才能進行情境推演。")
        scenario_pnl = projected_result.total_assets - start_assets
        scenarios.append(ForwardScenarioMetrics(
            name=window.name,
            source_start_date=window.start_date,
            source_end_date=window.end_date,
            source_index_return_pct=window.index_return_pct,
            start_assets=start_assets,
            ending_assets=projected_result.total_assets,
            scenario_pnl=scenario_pnl,
            scenario_return_pct=scenario_pnl / start_assets * 100,
            ending_cash=projected_result.cash,
            ending_market_value=projected_result.market_value,
            new_entry_count=sum(event.event_type == "進場" for event in projected_result.events),
            exit_count=sum(
                event.event_type in {"停損出場", "移動停利出場"}
                for event in projected_result.events
            ),
            ending_open_position_count=len(projected_result.positions),
            daily_snapshots=projected_result.daily_snapshots,
            entry_candidates=_build_entry_candidates(
                candidate_events,
                projected_dates,
                projection_base.end_date,
                holidays,
            ),
        ))

    return ForwardScenarioResult(
        scenario_id=str(uuid.uuid4()),
        created_at=datetime.now().astimezone().isoformat(),
        base_run_id=projection_base.run_id,
        base_end_date=projection_base.end_date,
        initial_capital=projection_base.initial_capital,
        starting_assets=projection_base.total_assets,
        horizon_days=horizon_days,
        params=asdict(params),
        scenarios=scenarios,
        selected_scenario=selected_scenario or "",
        excluded_stale_positions=excluded_stale_positions,
    )
