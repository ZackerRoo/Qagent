from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

import pandas as pd
from pydantic import BaseModel

from qagent.backtesting.engine import BacktestSignal, run_historical_backtest
from qagent.providers.base import MarketDataProvider
from qagent.strategy_data.providers import StrategyDataProvider


class PortfolioBacktestTrade(BaseModel):
    instrument_id: str
    strategy_id: str | None
    signal_date: date
    entry_date: date
    exit_date: date
    exit_reason: str
    entry_price: Decimal
    exit_price: Decimal
    shares: Decimal
    gross_pnl: Decimal
    costs: Decimal
    net_pnl: Decimal
    return_pct: float
    holding_days: int


class PortfolioEquityPoint(BaseModel):
    date: date
    equity: Decimal
    cash: Decimal
    open_positions: int
    drawdown_pct: float


class PortfolioBacktestSummary(BaseModel):
    provider: str
    symbols: list[str]
    start: date
    end: date
    initial_capital: Decimal
    final_equity: Decimal
    total_return_pct: float
    max_drawdown_pct: float
    trade_count: int
    win_rate: float | None
    profit_factor: float | None
    avg_trade_return_pct: float | None
    exposure_pct: float | None


class PortfolioMonthlyReturn(BaseModel):
    month: str
    starting_equity: Decimal
    ending_equity: Decimal
    return_pct: float


class PortfolioBacktestResult(BaseModel):
    summary: PortfolioBacktestSummary
    trades: list[PortfolioBacktestTrade]
    equity_curve: list[PortfolioEquityPoint]
    monthly_returns: list[PortfolioMonthlyReturn]
    data_health: dict[str, str]


@dataclass
class _TradeCandidate:
    signal: BacktestSignal
    entry_date: date
    exit_date: date
    exit_reason: str
    entry_price: Decimal
    exit_price: Decimal
    stop_price: Decimal
    holding_days: int


def run_portfolio_backtest(
    instrument_ids: list[str],
    provider: MarketDataProvider,
    start: date,
    end: date,
    step_days: int = 5,
    initial_capital: Decimal = Decimal("100000"),
    risk_per_trade_pct: Decimal = Decimal("1"),
    max_positions: int = 5,
    transaction_cost_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("5"),
    max_entry_wait_days: int = 5,
    max_holding_days: int = 20,
    strategy_data_provider: StrategyDataProvider | None = None,
) -> PortfolioBacktestResult:
    if start > end:
        raise ValueError("start must be on or before end")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if risk_per_trade_pct <= 0:
        raise ValueError("risk_per_trade_pct must be positive")
    if max_positions <= 0:
        raise ValueError("max_positions must be positive")

    signal_result = run_historical_backtest(
        instrument_ids=instrument_ids,
        provider=provider,
        start=start,
        end=end,
        step_days=step_days,
        max_signals=500,
        strategy_data_provider=strategy_data_provider,
    )
    bars = provider.get_daily_bars(
        instrument_ids,
        start=start,
        end=end + timedelta(days=(max_entry_wait_days + max_holding_days) * 3),
    )
    bars = _normalize_bars(bars)
    candidates = _build_candidates(
        signal_result.signals,
        bars,
        slippage_bps=slippage_bps,
        max_entry_wait_days=max_entry_wait_days,
        max_holding_days=max_holding_days,
    )
    trades, equity_curve = _simulate_portfolio(
        candidates,
        start=start,
        initial_capital=initial_capital,
        risk_per_trade_pct=risk_per_trade_pct,
        max_positions=max_positions,
        transaction_cost_bps=transaction_cost_bps,
    )
    summary = _build_summary(
        provider_name=provider.name,
        instrument_ids=instrument_ids,
        start=start,
        end=end,
        initial_capital=initial_capital,
        trades=trades,
        equity_curve=equity_curve,
    )
    data_health = {
        "provider": provider.name,
        "symbols": str(len(instrument_ids)),
        "source_signals": str(len(signal_result.signals)),
        "trade_candidates": str(len(candidates)),
        "trades": str(len(trades)),
        "lookahead_guard": "signals_generated_before_exits",
        "portfolio_model": "fixed_risk_stop_target_time_exit",
        "execution_rules": "fees_slippage,t_plus_one,limit_price_guard,round_lot_100",
        "cn_execution_rules": "enabled",
        "max_positions": str(max_positions),
        "risk_per_trade_pct": str(risk_per_trade_pct),
    }
    provider_errors = getattr(provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return PortfolioBacktestResult(
        summary=summary,
        trades=trades,
        equity_curve=equity_curve,
        monthly_returns=_build_monthly_returns(equity_curve),
        data_health=data_health,
    )


def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return bars
    frame = bars.copy()
    if pd.api.types.is_datetime64_any_dtype(frame["trade_date"]):
        frame["trade_date"] = frame["trade_date"].dt.date
    return frame.sort_values(["instrument_id", "trade_date"]).reset_index(drop=True)


def _build_candidates(
    signals: list[BacktestSignal],
    bars: pd.DataFrame,
    slippage_bps: Decimal,
    max_entry_wait_days: int,
    max_holding_days: int,
) -> list[_TradeCandidate]:
    candidates: list[_TradeCandidate] = []
    sorted_signals = sorted(
        signals,
        key=lambda signal: (signal.signal_date, Decimal(signal.rank_score)),
        reverse=False,
    )
    for signal in sorted_signals:
        candidate = _candidate_from_signal(
            signal,
            bars.loc[bars["instrument_id"] == signal.instrument_id].reset_index(drop=True),
            slippage_bps=slippage_bps,
            max_entry_wait_days=max_entry_wait_days,
            max_holding_days=max_holding_days,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _candidate_from_signal(
    signal: BacktestSignal,
    bars: pd.DataFrame,
    slippage_bps: Decimal,
    max_entry_wait_days: int,
    max_holding_days: int,
) -> _TradeCandidate | None:
    if bars.empty or signal.trigger_price is None:
        return None

    trigger = Decimal(signal.trigger_price)
    stop = Decimal(signal.initial_stop) if signal.initial_stop is not None else trigger * Decimal("0.95")
    target = Decimal(signal.target_1) if signal.target_1 is not None else None
    if trigger <= 0 or stop <= 0:
        return None

    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    future = ordered.loc[ordered["trade_date"] > signal.signal_date]
    if future.empty:
        return None

    entry_index = None
    for index, row in future.head(max_entry_wait_days).iterrows():
        previous = ordered.iloc[index - 1] if index > 0 else None
        if _is_cn(signal.instrument_id) and _is_limit_up_day(row, previous, signal.instrument_id):
            continue
        if Decimal(str(row["high"])) >= trigger:
            entry_index = index
            break
    if entry_index is None:
        return None

    slip = slippage_bps / Decimal("10000")
    entry_price = _money(trigger * (Decimal("1") + slip))
    exit_price = None
    exit_date = None
    exit_reason = "time_exit"
    exit_window = ordered.iloc[entry_index : entry_index + max_holding_days].reset_index(drop=True)
    entry_date = exit_window.iloc[0]["trade_date"]

    for _, row in exit_window.iterrows():
        trade_date = row["trade_date"]
        if _is_cn(signal.instrument_id) and trade_date == entry_date:
            continue
        low = Decimal(str(row["low"]))
        high = Decimal(str(row["high"]))
        previous = _previous_row(ordered, row)
        if low <= stop:
            if _is_cn(signal.instrument_id) and _is_limit_down_day(row, previous, signal.instrument_id):
                continue
            exit_date = trade_date
            exit_price = _money(stop * (Decimal("1") - slip))
            exit_reason = "stopped"
            break
        if target is not None and high >= target:
            exit_date = trade_date
            exit_price = _money(target * (Decimal("1") - slip))
            exit_reason = "target_1_hit"
            break

    if exit_price is None:
        final_row = exit_window.iloc[-1]
        exit_date = final_row["trade_date"]
        exit_price = _money(Decimal(str(final_row["close"])) * (Decimal("1") - slip))

    return _TradeCandidate(
        signal=signal,
        entry_date=entry_date,
        exit_date=exit_date,
        exit_reason=exit_reason,
        entry_price=entry_price,
        exit_price=exit_price,
        stop_price=stop,
        holding_days=max((exit_date - entry_date).days, 0),
    )


def _simulate_portfolio(
    candidates: list[_TradeCandidate],
    start: date,
    initial_capital: Decimal,
    risk_per_trade_pct: Decimal,
    max_positions: int,
    transaction_cost_bps: Decimal,
) -> tuple[list[PortfolioBacktestTrade], list[PortfolioEquityPoint]]:
    equity = initial_capital
    peak = equity
    open_trades: list[PortfolioBacktestTrade] = []
    closed_trades: list[PortfolioBacktestTrade] = []
    curve = [
        PortfolioEquityPoint(
            date=start,
            equity=equity,
            cash=equity,
            open_positions=0,
            drawdown_pct=0.0,
        )
    ]

    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.entry_date,
            -Decimal(item.signal.rank_score),
            item.signal.instrument_id,
        ),
    ):
        equity, peak = _close_due_trades(
            candidate.entry_date,
            open_trades,
            closed_trades,
            curve,
            equity,
            peak,
        )
        if len(open_trades) >= max_positions:
            continue
        trade = _size_trade(
            candidate,
            equity=equity,
            risk_per_trade_pct=risk_per_trade_pct,
            max_positions=max_positions,
            transaction_cost_bps=transaction_cost_bps,
        )
        if trade is not None:
            open_trades.append(trade)

    remaining_trades = sorted(open_trades, key=lambda item: (item.exit_date, item.instrument_id))
    for index, trade in enumerate(remaining_trades):
        equity = _money(equity + trade.net_pnl)
        peak = max(peak, equity)
        closed_trades.append(trade)
        curve.append(
            PortfolioEquityPoint(
                date=trade.exit_date,
                equity=equity,
                cash=equity,
                open_positions=len(remaining_trades) - index - 1,
                drawdown_pct=_pct((equity - peak) / peak) if peak else 0.0,
            )
        )

    closed_trades.sort(key=lambda item: (item.exit_date, item.instrument_id))
    if curve[-1].equity != equity:
        curve.append(
            PortfolioEquityPoint(
                date=closed_trades[-1].exit_date if closed_trades else start,
                equity=equity,
                cash=equity,
                open_positions=0,
                drawdown_pct=_pct((equity - peak) / peak) if peak else 0.0,
            )
        )
    return closed_trades, curve


def _close_due_trades(
    current_date: date,
    open_trades: list[PortfolioBacktestTrade],
    closed_trades: list[PortfolioBacktestTrade],
    curve: list[PortfolioEquityPoint],
    equity: Decimal,
    peak: Decimal,
) -> tuple[Decimal, Decimal]:
    due = [trade for trade in open_trades if trade.exit_date <= current_date]
    for trade in sorted(due, key=lambda item: (item.exit_date, item.instrument_id)):
        equity = _money(equity + trade.net_pnl)
        peak = max(peak, equity)
        closed_trades.append(trade)
        open_trades.remove(trade)
        curve.append(
            PortfolioEquityPoint(
                date=trade.exit_date,
                equity=equity,
                cash=equity,
                open_positions=len(open_trades),
                drawdown_pct=_pct((equity - peak) / peak) if peak else 0.0,
            )
        )
    return equity, peak


def _size_trade(
    candidate: _TradeCandidate,
    equity: Decimal,
    risk_per_trade_pct: Decimal,
    max_positions: int,
    transaction_cost_bps: Decimal,
) -> PortfolioBacktestTrade | None:
    per_share_risk = max(candidate.entry_price - candidate.stop_price, candidate.entry_price * Decimal("0.01"))
    if per_share_risk <= 0:
        return None
    risk_budget = equity * (risk_per_trade_pct / Decimal("100"))
    capital_budget = equity / Decimal(max_positions)
    shares_by_risk = risk_budget / per_share_risk
    shares_by_capital = capital_budget / candidate.entry_price
    shares = _shares(min(shares_by_risk, shares_by_capital), candidate.signal.instrument_id)
    if shares <= 0:
        return None

    gross_pnl = _money((candidate.exit_price - candidate.entry_price) * shares)
    traded_value = (candidate.entry_price + candidate.exit_price) * shares
    costs = _money(traded_value * (transaction_cost_bps / Decimal("10000")))
    net_pnl = _money(gross_pnl - costs)
    denominator = candidate.entry_price * shares
    return_pct = _pct(net_pnl / denominator) if denominator else 0.0
    return PortfolioBacktestTrade(
        instrument_id=candidate.signal.instrument_id,
        strategy_id=candidate.signal.primary_strategy_id,
        signal_date=candidate.signal.signal_date,
        entry_date=candidate.entry_date,
        exit_date=candidate.exit_date,
        exit_reason=candidate.exit_reason,
        entry_price=candidate.entry_price,
        exit_price=candidate.exit_price,
        shares=shares,
        gross_pnl=gross_pnl,
        costs=costs,
        net_pnl=net_pnl,
        return_pct=return_pct,
        holding_days=candidate.holding_days,
    )


def _build_summary(
    provider_name: str,
    instrument_ids: list[str],
    start: date,
    end: date,
    initial_capital: Decimal,
    trades: list[PortfolioBacktestTrade],
    equity_curve: list[PortfolioEquityPoint],
) -> PortfolioBacktestSummary:
    final_equity = equity_curve[-1].equity if equity_curve else initial_capital
    wins = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losses = [trade.net_pnl for trade in trades if trade.net_pnl < 0]
    returns = [trade.return_pct for trade in trades]
    period_days = max((end - start).days, 1)
    invested_days = sum(trade.holding_days for trade in trades)
    exposure_denominator = period_days * max(len(instrument_ids), 1)
    return PortfolioBacktestSummary(
        provider=provider_name,
        symbols=instrument_ids,
        start=start,
        end=end,
        initial_capital=initial_capital,
        final_equity=_money(final_equity),
        total_return_pct=_pct((final_equity - initial_capital) / initial_capital),
        max_drawdown_pct=min((point.drawdown_pct for point in equity_curve), default=0.0),
        trade_count=len(trades),
        win_rate=_ratio(len(wins), len(trades)),
        profit_factor=_profit_factor(wins, losses),
        avg_trade_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
        exposure_pct=round((invested_days / exposure_denominator) * 100, 4)
        if exposure_denominator
        else None,
    )


def _build_monthly_returns(equity_curve: list[PortfolioEquityPoint]) -> list[PortfolioMonthlyReturn]:
    if not equity_curve:
        return []
    ordered = sorted(equity_curve, key=lambda point: point.date)
    grouped: dict[str, list[PortfolioEquityPoint]] = {}
    for point in ordered:
        grouped.setdefault(point.date.strftime("%Y-%m"), []).append(point)

    monthly_returns: list[PortfolioMonthlyReturn] = []
    previous_equity = ordered[0].equity
    for month in sorted(grouped):
        points = grouped[month]
        starting_equity = previous_equity
        ending_equity = points[-1].equity
        return_pct = _pct((ending_equity - starting_equity) / starting_equity) if starting_equity else 0.0
        monthly_returns.append(
            PortfolioMonthlyReturn(
                month=month,
                starting_equity=_money(starting_equity),
                ending_equity=_money(ending_equity),
                return_pct=return_pct,
            )
        )
        previous_equity = ending_equity
    return monthly_returns


def _profit_factor(wins: list[Decimal], losses: list[Decimal]) -> float | None:
    if not wins and not losses:
        return None
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    if gross_loss == 0:
        return None
    return round(float(gross_profit / gross_loss), 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _pct(value: Decimal) -> float:
    return round(float(value * Decimal("100")), 4)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _shares(value: Decimal, instrument_id: str | None = None) -> Decimal:
    if instrument_id and _is_cn(instrument_id):
        lots = (value / Decimal("100")).to_integral_value(rounding=ROUND_DOWN)
        return lots * Decimal("100")
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _previous_row(ordered: pd.DataFrame, row) -> object | None:
    matches = ordered.index[ordered["trade_date"] == row["trade_date"]].tolist()
    if not matches:
        return None
    index = matches[0]
    return ordered.iloc[index - 1] if index > 0 else None


def _is_cn(instrument_id: str) -> bool:
    return instrument_id.startswith("CN:")


def _is_limit_up_day(row, previous, instrument_id: str) -> bool:
    limit_pct = _limit_pct(instrument_id)
    return _is_limit_day(row, previous, limit_pct, up=True)


def _is_limit_down_day(row, previous, instrument_id: str) -> bool:
    limit_pct = _limit_pct(instrument_id)
    return _is_limit_day(row, previous, limit_pct, up=False)


def _is_limit_day(row, previous, limit_pct: Decimal, *, up: bool) -> bool:
    if previous is None:
        return False
    previous_close = Decimal(str(previous["close"]))
    if previous_close <= 0:
        return False
    close = Decimal(str(row["close"]))
    change_pct = (close / previous_close - Decimal("1")) * Decimal("100")
    if up:
        limit_price = previous_close * (Decimal("1") + limit_pct / Decimal("100"))
        return change_pct >= limit_pct - Decimal("0.2") or close >= limit_price * Decimal("0.995")
    limit_price = previous_close * (Decimal("1") - limit_pct / Decimal("100"))
    return change_pct <= -limit_pct + Decimal("0.2") or close <= limit_price * Decimal("1.005")


def _limit_pct(instrument_id: str) -> Decimal:
    symbol = instrument_id.split(":", 1)[1]
    if symbol.startswith(("688", "300", "301")):
        return Decimal("20")
    if symbol.startswith(("4", "8", "920")):
        return Decimal("30")
    return Decimal("10")
