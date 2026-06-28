from datetime import date
from decimal import Decimal, ROUND_DOWN

import pandas as pd
from pydantic import BaseModel

from qagent.providers.base import MarketDataProvider
from qagent.storage.paper import PaperTradeRecord, PaperTradingRepository
from qagent.storage.repository import OpportunitySnapshotRecord


OPEN_STATUSES = {"pending", "open"}
CLOSED_STATUSES = {"target_1_hit", "stopped", "time_exit"}


class PaperSeedResult(BaseModel):
    scanned: int
    created: int
    skipped: int


class PaperTradingSummary(BaseModel):
    total: int
    pending: int
    open: int
    closed: int
    target_hit_count: int
    stopped_count: int
    time_exit_count: int
    win_rate: float | None
    average_realized_return_pct: float | None
    average_unrealized_return_pct: float | None


class PaperUpdateResult(BaseModel):
    summary: PaperTradingSummary
    trades: list[PaperTradeRecord]
    data_health: dict[str, str]


class PaperLedgerSummary(BaseModel):
    initial_capital: Decimal
    allocation_per_trade_pct: float
    allocation_per_trade: Decimal
    total_trades: int
    pending_trades: int
    open_trades: int
    closed_trades: int
    target_hit_count: int
    stopped_count: int
    time_exit_count: int
    planned_capital: Decimal
    allocated_capital: Decimal
    market_value: Decimal
    cash_available: Decimal
    total_equity: Decimal
    total_pnl: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_return_pct: float
    open_exposure_pct: float
    win_rate: float | None
    average_return_pct: float | None
    best_return_pct: float | None
    worst_return_pct: float | None
    max_drawdown_pct: float
    total_fees: Decimal
    total_slippage: Decimal
    turnover: Decimal
    transaction_cost_bps: float
    slippage_bps: float
    take_profit_pct: float


class PaperLedgerPoint(BaseModel):
    date: date
    equity: Decimal
    pnl: Decimal
    drawdown_pct: float
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    event_count: int


class PaperLedgerItem(BaseModel):
    trade_id: str
    instrument_id: str
    strategy_id: str | None
    status: str
    outcome: str
    signal_date: date
    entry_date: date | None
    exit_date: date | None
    latest_date: date | None
    trigger_price: Decimal
    entry_price: Decimal | None
    exit_price: Decimal | None
    latest_price: Decimal | None
    capital_allocated: Decimal
    shares: Decimal
    market_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal
    return_pct: float | None
    risk_pct: float | None
    reward_pct: float | None
    holding_days: int
    notes: str


class PaperLedgerTransaction(BaseModel):
    transaction_id: str
    trade_id: str
    instrument_id: str
    action: str
    side: str
    trade_date: date
    price: Decimal
    shares: Decimal
    gross_amount: Decimal
    fee: Decimal
    slippage: Decimal
    cash_flow: Decimal
    cash_balance: Decimal
    notes: str


class PaperLedgerPosition(BaseModel):
    trade_id: str
    instrument_id: str
    strategy_id: str | None
    entry_date: date
    latest_date: date | None
    shares: Decimal
    cost_basis: Decimal
    latest_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    return_pct: float
    weight_pct: float


class PaperLedger(BaseModel):
    summary: PaperLedgerSummary
    curve: list[PaperLedgerPoint]
    items: list[PaperLedgerItem]
    transactions: list[PaperLedgerTransaction]
    positions: list[PaperLedgerPosition]
    data_health: dict[str, str]


def seed_paper_trades_from_snapshots(
    repo: PaperTradingRepository,
    snapshots: list[OpportunitySnapshotRecord],
    provider: str,
) -> PaperSeedResult:
    created = 0
    skipped = 0
    existing = {trade.source_snapshot_id for trade in repo.list_trades(limit=1000)}
    for snapshot in snapshots:
        if snapshot.snapshot_id in existing:
            skipped += 1
            continue
        if snapshot.signal_date is None or snapshot.trigger_price is None:
            skipped += 1
            continue
        repo.create_trade(
            source_snapshot_id=snapshot.snapshot_id,
            provider=provider,
            instrument_id=snapshot.instrument_id,
            strategy_id=snapshot.primary_strategy_id,
            signal_date=snapshot.signal_date,
            trigger_price=snapshot.trigger_price,
            initial_stop=snapshot.initial_stop,
            target_1=snapshot.target_1,
            rank_score=snapshot.rank_score,
        )
        created += 1
    return PaperSeedResult(scanned=len(snapshots), created=created, skipped=skipped)


def update_paper_trades(
    repo: PaperTradingRepository,
    provider: MarketDataProvider,
    max_holding_days: int = 20,
    max_entry_wait_days: int = 10,
) -> PaperUpdateResult:
    trades = repo.list_trades(limit=1000)
    active = [trade for trade in trades if trade.status in OPEN_STATUSES]
    for trade in active:
        bars = provider.get_daily_bars(
            [trade.instrument_id],
            start=trade.signal_date,
            end=date(2100, 1, 1),
        )
        if bars.empty:
            continue
        updated = _evaluate_trade(trade, bars, max_holding_days, max_entry_wait_days)
        repo.update_trade(trade.trade_id, **updated)
    refreshed = repo.list_trades(limit=1000)
    provider_errors = getattr(provider, "last_errors", [])
    data_health = {
        "provider": provider.name,
        "trades": str(len(refreshed)),
        "active_checked": str(len(active)),
    }
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return PaperUpdateResult(
        summary=summarize_paper_trades(refreshed),
        trades=refreshed,
        data_health=data_health,
    )


def summarize_paper_trades(trades: list[PaperTradeRecord]) -> PaperTradingSummary:
    closed = [trade for trade in trades if trade.status in CLOSED_STATUSES]
    winning = [
        trade
        for trade in closed
        if trade.realized_return_pct is not None and trade.realized_return_pct > 0
    ]
    realized = [
        trade.realized_return_pct
        for trade in closed
        if trade.realized_return_pct is not None
    ]
    unrealized = [
        trade.unrealized_return_pct
        for trade in trades
        if trade.status == "open" and trade.unrealized_return_pct is not None
    ]
    return PaperTradingSummary(
        total=len(trades),
        pending=sum(1 for trade in trades if trade.status == "pending"),
        open=sum(1 for trade in trades if trade.status == "open"),
        closed=len(closed),
        target_hit_count=sum(1 for trade in trades if trade.status == "target_1_hit"),
        stopped_count=sum(1 for trade in trades if trade.status == "stopped"),
        time_exit_count=sum(1 for trade in trades if trade.status == "time_exit"),
        win_rate=round(len(winning) / len(closed), 4) if closed else None,
        average_realized_return_pct=round(sum(realized) / len(realized), 4)
        if realized
        else None,
        average_unrealized_return_pct=round(sum(unrealized) / len(unrealized), 4)
        if unrealized
        else None,
    )


def build_paper_ledger(
    trades: list[PaperTradeRecord],
    initial_capital: Decimal = Decimal("100000"),
    allocation_per_trade_pct: Decimal = Decimal("10"),
    transaction_cost_bps: Decimal = Decimal("0"),
    slippage_bps: Decimal = Decimal("0"),
    take_profit_pct: Decimal = Decimal("100"),
) -> PaperLedger:
    if initial_capital <= 0:
        raise ValueError("initial_capital must be greater than zero")
    if allocation_per_trade_pct <= 0 or allocation_per_trade_pct > 100:
        raise ValueError("allocation_per_trade_pct must be between 0 and 100")
    if transaction_cost_bps < 0 or slippage_bps < 0:
        raise ValueError("transaction_cost_bps and slippage_bps must be non-negative")
    if take_profit_pct <= 0 or take_profit_pct > 100:
        raise ValueError("take_profit_pct must be between 0 and 100")

    allocation_per_trade = _money(initial_capital * allocation_per_trade_pct / Decimal("100"))
    items: list[PaperLedgerItem] = []
    planned_capital = Decimal("0")

    for trade in trades:
        item = _ledger_item(trade, allocation_per_trade)
        items.append(item)
        planned_capital += allocation_per_trade if trade.status == "pending" else Decimal("0")

    account = _build_account_ledger(
        trades=trades,
        initial_capital=initial_capital,
        allocation_per_trade=allocation_per_trade,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        take_profit_pct=take_profit_pct,
    )

    returns = [item.return_pct for item in items if item.return_pct is not None]
    closed_returns = [
        item.return_pct
        for item in items
        if item.status in CLOSED_STATUSES and item.return_pct is not None
    ]
    max_drawdown_pct = min((point.drawdown_pct for point in account["curve"]), default=0.0)

    return PaperLedger(
        summary=PaperLedgerSummary(
            initial_capital=_money(initial_capital),
            allocation_per_trade_pct=round(float(allocation_per_trade_pct), 4),
            allocation_per_trade=allocation_per_trade,
            total_trades=len(trades),
            pending_trades=sum(1 for trade in trades if trade.status == "pending"),
            open_trades=sum(1 for trade in trades if trade.status == "open"),
            closed_trades=sum(1 for trade in trades if trade.status in CLOSED_STATUSES),
            target_hit_count=sum(1 for trade in trades if trade.status == "target_1_hit"),
            stopped_count=sum(1 for trade in trades if trade.status == "stopped"),
            time_exit_count=sum(1 for trade in trades if trade.status == "time_exit"),
            planned_capital=_money(planned_capital),
            allocated_capital=account["allocated_capital"],
            market_value=account["market_value"],
            cash_available=account["cash_available"],
            total_equity=account["total_equity"],
            total_pnl=account["total_pnl"],
            realized_pnl=account["realized_pnl"],
            unrealized_pnl=account["unrealized_pnl"],
            total_return_pct=_pct(account["total_pnl"], initial_capital),
            open_exposure_pct=_pct(account["market_value"], account["total_equity"]),
            win_rate=round(
                sum(1 for value in closed_returns if value > 0) / len(closed_returns),
                4,
            )
            if closed_returns
            else None,
            average_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
            best_return_pct=round(max(returns), 4) if returns else None,
            worst_return_pct=round(min(returns), 4) if returns else None,
            max_drawdown_pct=max_drawdown_pct,
            total_fees=account["total_fees"],
            total_slippage=account["total_slippage"],
            turnover=account["turnover"],
            transaction_cost_bps=round(float(transaction_cost_bps), 4),
            slippage_bps=round(float(slippage_bps), 4),
            take_profit_pct=round(float(take_profit_pct), 4),
        ),
        curve=account["curve"],
        items=items,
        transactions=account["transactions"],
        positions=account["positions"],
        data_health={
            "ledger_method": "chronological_cash_ledger",
            "allocation_per_trade_pct": str(allocation_per_trade_pct),
            "transaction_cost_bps": str(transaction_cost_bps),
            "slippage_bps": str(slippage_bps),
            "take_profit_pct": str(take_profit_pct),
            "price_source": "paper_trade_latest_fields",
        },
    )


def _build_account_ledger(
    trades: list[PaperTradeRecord],
    initial_capital: Decimal,
    allocation_per_trade: Decimal,
    transaction_cost_bps: Decimal,
    slippage_bps: Decimal,
    take_profit_pct: Decimal,
) -> dict[str, object]:
    fee_rate = _bps_rate(transaction_cost_bps)
    slippage_rate = _bps_rate(slippage_bps)
    active_lots: list[dict[str, object]] = []
    transactions: list[PaperLedgerTransaction] = []
    positions: list[PaperLedgerPosition] = []
    cash = initial_capital
    total_fees = Decimal("0")
    total_slippage = Decimal("0")
    turnover = Decimal("0")
    realized_pnl = Decimal("0")
    dates = {
        trade.signal_date
        for trade in trades
        if trade.signal_date is not None
    }
    for trade in trades:
        if trade.entry_date is not None:
            dates.add(trade.entry_date)
        if trade.exit_date is not None:
            dates.add(trade.exit_date)
        if trade.latest_date is not None:
            dates.add(trade.latest_date)
    if not dates:
        dates.add(date.today())

    entries_by_date: dict[date, list[PaperTradeRecord]] = {}
    for trade in sorted(
        trades,
        key=lambda item: (item.entry_date or item.signal_date, item.trade_id),
    ):
        if trade.entry_date is None or trade.entry_price is None:
            continue
        if trade.status not in {"open", *CLOSED_STATUSES}:
            continue
        entries_by_date.setdefault(trade.entry_date, []).append(trade)

    curve = [
        PaperLedgerPoint(
            date=min(dates),
            equity=_money(initial_capital),
            pnl=Decimal("0.00"),
            drawdown_pct=0.0,
            realized_pnl=Decimal("0.00"),
            unrealized_pnl=Decimal("0.00"),
            event_count=0,
        )
    ]
    high_watermark = initial_capital

    for current_date in sorted(dates):
        event_count = 0
        exiting_lots = [
            lot
            for lot in active_lots
            if lot["exit_date"] == current_date and lot["status"] in CLOSED_STATUSES
        ]
        for lot in exiting_lots:
            generated = _sell_lot_transactions(
                lot=lot,
                cash=cash,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
                take_profit_pct=take_profit_pct,
            )
            for transaction, pnl, fee, slippage, gross in generated:
                cash = transaction.cash_balance
                realized_pnl += pnl
                total_fees += fee
                total_slippage += slippage
                turnover += gross
                transactions.append(transaction)
                event_count += 1
            active_lots.remove(lot)

        for trade in entries_by_date.get(current_date, []):
            buy = _buy_lot(
                trade=trade,
                cash=cash,
                allocation_per_trade=allocation_per_trade,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
            if buy is None:
                continue
            lot, transaction, fee, slippage, gross = buy
            cash = transaction.cash_balance
            total_fees += fee
            total_slippage += slippage
            turnover += gross
            transactions.append(transaction)
            active_lots.append(lot)
            event_count += 1

        market_value, unrealized_pnl = _active_lot_market_value(active_lots, current_date)
        equity = cash + market_value
        high_watermark = max(high_watermark, equity)
        drawdown_pct = _pct(equity - high_watermark, high_watermark)
        if current_date != curve[0].date or event_count:
            curve.append(
                PaperLedgerPoint(
                    date=current_date,
                    equity=_money(equity),
                    pnl=_money(equity - initial_capital),
                    drawdown_pct=drawdown_pct,
                    realized_pnl=_money(realized_pnl),
                    unrealized_pnl=_money(unrealized_pnl),
                    event_count=event_count,
                )
            )

    final_market_value, final_unrealized_pnl = _active_lot_market_value(
        active_lots,
        max(dates),
    )
    total_equity = _money(cash + final_market_value)
    for lot in active_lots:
        latest_price = _lot_mark_price(lot, max(dates))
        market_value = _money(Decimal(str(lot["shares"])) * latest_price)
        cost_basis = Decimal(str(lot["cost_basis"]))
        positions.append(
            PaperLedgerPosition(
                trade_id=str(lot["trade_id"]),
                instrument_id=str(lot["instrument_id"]),
                strategy_id=lot["strategy_id"] if isinstance(lot["strategy_id"], str) else None,
                entry_date=lot["entry_date"],
                latest_date=lot["latest_date"],
                shares=Decimal(str(lot["shares"])),
                cost_basis=_money(cost_basis),
                latest_price=latest_price,
                market_value=market_value,
                unrealized_pnl=_money(market_value - cost_basis),
                return_pct=_pct(market_value - cost_basis, cost_basis),
                weight_pct=_pct(market_value, total_equity),
            )
        )

    return {
        "allocated_capital": _money(sum((position.cost_basis for position in positions), Decimal("0"))),
        "market_value": _money(final_market_value),
        "cash_available": _money(cash),
        "total_equity": total_equity,
        "total_pnl": _money(total_equity - initial_capital),
        "realized_pnl": _money(realized_pnl),
        "unrealized_pnl": _money(final_unrealized_pnl),
        "total_fees": _money(total_fees),
        "total_slippage": _money(total_slippage),
        "turnover": _money(turnover),
        "curve": curve,
        "transactions": transactions,
        "positions": positions,
    }


def _buy_lot(
    trade: PaperTradeRecord,
    cash: Decimal,
    allocation_per_trade: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
) -> tuple[dict[str, object], PaperLedgerTransaction, Decimal, Decimal, Decimal] | None:
    if trade.entry_date is None or trade.entry_price is None or trade.entry_price <= 0:
        return None
    all_in_rate = Decimal("1") + fee_rate + slippage_rate
    affordable_gross = cash / all_in_rate if all_in_rate > 0 else cash
    gross_target = min(allocation_per_trade, affordable_gross)
    if gross_target <= Decimal("1"):
        return None
    shares = _shares(gross_target / trade.entry_price)
    if shares <= 0:
        return None
    gross = _money(shares * trade.entry_price)
    fee = _money(gross * fee_rate)
    slippage = _money(gross * slippage_rate)
    cash_flow = -(gross + fee + slippage)
    cash_balance = _money(cash + cash_flow)
    lot = {
        "trade_id": trade.trade_id,
        "instrument_id": trade.instrument_id,
        "strategy_id": trade.strategy_id,
        "status": trade.status,
        "entry_date": trade.entry_date,
        "entry_price": trade.entry_price,
        "exit_date": trade.exit_date,
        "exit_price": trade.exit_price,
        "latest_date": trade.latest_date,
        "latest_price": trade.latest_price,
        "shares": shares,
        "cost_basis": gross + fee + slippage,
    }
    transaction = PaperLedgerTransaction(
        transaction_id=f"{trade.trade_id}-buy",
        trade_id=trade.trade_id,
        instrument_id=trade.instrument_id,
        action="entry_buy",
        side="buy",
        trade_date=trade.entry_date,
        price=trade.entry_price,
        shares=shares,
        gross_amount=gross,
        fee=fee,
        slippage=slippage,
        cash_flow=_money(cash_flow),
        cash_balance=cash_balance,
        notes="按推荐触发价模拟买入。",
    )
    return lot, transaction, fee, slippage, gross


def _sell_lot_transactions(
    lot: dict[str, object],
    cash: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    take_profit_pct: Decimal,
) -> list[tuple[PaperLedgerTransaction, Decimal, Decimal, Decimal, Decimal]]:
    status = str(lot["status"])
    exit_date = lot["exit_date"]
    exit_price = lot["exit_price"]
    if not isinstance(exit_date, date) or not isinstance(exit_price, Decimal):
        return []
    remaining_shares = Decimal(str(lot["shares"]))
    cost_basis = Decimal(str(lot["cost_basis"]))
    cost_per_share = cost_basis / remaining_shares if remaining_shares > 0 else Decimal("0")
    if remaining_shares <= 0:
        return []

    portions: list[tuple[str, Decimal]]
    if status == "target_1_hit" and take_profit_pct < 100:
        first = _shares(remaining_shares * take_profit_pct / Decimal("100"))
        portions = [
            ("partial_take_profit", first),
            ("final_take_profit", remaining_shares - first),
        ]
    else:
        action = (
            "take_profit_exit"
            if status == "target_1_hit"
            else "stop_loss_exit"
            if status == "stopped"
            else "time_exit"
        )
        portions = [(action, remaining_shares)]

    results: list[tuple[PaperLedgerTransaction, Decimal, Decimal, Decimal, Decimal]] = []
    cash_balance = cash
    for index, (action, shares) in enumerate(portions):
        if shares <= 0:
            continue
        if index == len(portions) - 1:
            shares = remaining_shares
        gross = _money(shares * exit_price)
        fee = _money(gross * fee_rate)
        slippage = _money(gross * slippage_rate)
        cash_flow = gross - fee - slippage
        cash_balance = _money(cash_balance + cash_flow)
        pnl = cash_flow - (cost_per_share * shares)
        transaction = PaperLedgerTransaction(
            transaction_id=f"{lot['trade_id']}-{action}",
            trade_id=str(lot["trade_id"]),
            instrument_id=str(lot["instrument_id"]),
            action=action,
            side="sell",
            trade_date=exit_date,
            price=exit_price,
            shares=shares,
            gross_amount=gross,
            fee=fee,
            slippage=slippage,
            cash_flow=_money(cash_flow),
            cash_balance=cash_balance,
            notes=_transaction_note(action),
        )
        results.append((transaction, pnl, fee, slippage, gross))
        remaining_shares -= shares
    return results


def _active_lot_market_value(
    lots: list[dict[str, object]],
    current_date: date,
) -> tuple[Decimal, Decimal]:
    market_value = Decimal("0")
    unrealized_pnl = Decimal("0")
    for lot in lots:
        shares = Decimal(str(lot["shares"]))
        cost_basis = Decimal(str(lot["cost_basis"]))
        mark_price = _lot_mark_price(lot, current_date)
        value = shares * mark_price
        market_value += value
        unrealized_pnl += value - cost_basis
    return _money(market_value), _money(unrealized_pnl)


def _lot_mark_price(lot: dict[str, object], current_date: date) -> Decimal:
    latest_date = lot.get("latest_date")
    latest_price = lot.get("latest_price")
    if isinstance(latest_date, date) and latest_date <= current_date and isinstance(latest_price, Decimal):
        return latest_price
    return Decimal(str(lot["entry_price"]))


def _transaction_note(action: str) -> str:
    notes = {
        "partial_take_profit": "到达目标价，按分批止盈规则卖出一部分。",
        "final_take_profit": "到达目标价后剩余仓位模拟退出。",
        "take_profit_exit": "到达目标价，模拟止盈退出。",
        "stop_loss_exit": "跌破止损价，模拟纪律退出。",
        "time_exit": "超过持有窗口，模拟时间退出。",
    }
    return notes.get(action, "模拟交易流水。")


def _ledger_item(
    trade: PaperTradeRecord,
    allocation_per_trade: Decimal,
) -> PaperLedgerItem:
    shares = Decimal("0")
    market_value = Decimal("0")
    realized_pnl = Decimal("0")
    unrealized_pnl = Decimal("0")
    return_pct: float | None = None
    capital_allocated = Decimal("0")

    if trade.entry_price and trade.entry_price > 0:
        shares = (allocation_per_trade / trade.entry_price).quantize(Decimal("0.0001"))
        if trade.status in CLOSED_STATUSES and trade.exit_price is not None:
            exit_value = shares * trade.exit_price
            realized_pnl = exit_value - allocation_per_trade
            return_pct = _return_pct(trade.entry_price, trade.exit_price)
        elif trade.status == "open":
            latest_price = trade.latest_price or trade.entry_price
            market_value = shares * latest_price
            unrealized_pnl = market_value - allocation_per_trade
            return_pct = _return_pct(trade.entry_price, latest_price)
            capital_allocated = allocation_per_trade

    risk_pct = (
        _signed_return_pct(trade.trigger_price, trade.initial_stop)
        if trade.initial_stop is not None
        else None
    )
    reward_pct = (
        _signed_return_pct(trade.trigger_price, trade.target_1)
        if trade.target_1 is not None
        else None
    )
    total_pnl = realized_pnl + unrealized_pnl

    return PaperLedgerItem(
        trade_id=trade.trade_id,
        instrument_id=trade.instrument_id,
        strategy_id=trade.strategy_id,
        status=trade.status,
        outcome=_outcome_label(trade.status, return_pct),
        signal_date=trade.signal_date,
        entry_date=trade.entry_date,
        exit_date=trade.exit_date,
        latest_date=trade.latest_date,
        trigger_price=trade.trigger_price,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        latest_price=trade.latest_price,
        capital_allocated=_money(capital_allocated),
        shares=shares,
        market_value=_money(market_value),
        realized_pnl=_money(realized_pnl),
        unrealized_pnl=_money(unrealized_pnl),
        total_pnl=_money(total_pnl),
        return_pct=return_pct,
        risk_pct=risk_pct,
        reward_pct=reward_pct,
        holding_days=trade.holding_days,
        notes=trade.notes,
    )


def _ledger_curve(
    trades: list[PaperTradeRecord],
    initial_capital: Decimal,
    events: dict[date, dict[str, object]],
) -> list[PaperLedgerPoint]:
    if trades:
        start_date = min(trade.signal_date for trade in trades)
    else:
        start_date = date.today()
    points = [
        PaperLedgerPoint(
            date=start_date,
            equity=_money(initial_capital),
            pnl=Decimal("0.00"),
            drawdown_pct=0.0,
            realized_pnl=Decimal("0.00"),
            unrealized_pnl=Decimal("0.00"),
            event_count=0,
        )
    ]
    equity = initial_capital
    running_realized = Decimal("0")
    running_unrealized = Decimal("0")
    high_watermark = initial_capital
    for event_date in sorted(events):
        event = events[event_date]
        running_realized += event["realized"]
        running_unrealized += event["unrealized"]
        equity = initial_capital + running_realized + running_unrealized
        high_watermark = max(high_watermark, equity)
        drawdown_pct = (
            _pct(equity - high_watermark, high_watermark)
            if high_watermark > 0
            else 0.0
        )
        points.append(
            PaperLedgerPoint(
                date=event_date,
                equity=_money(equity),
                pnl=_money(equity - initial_capital),
                drawdown_pct=drawdown_pct,
                realized_pnl=_money(running_realized),
                unrealized_pnl=_money(running_unrealized),
                event_count=int(event["count"]),
            )
        )
    return points


def _outcome_label(status: str, return_pct: float | None) -> str:
    if status == "pending":
        return "等待触发"
    if status == "open":
        if return_pct is not None and return_pct >= 0:
            return "浮盈跟踪"
        return "浮亏跟踪"
    if status == "target_1_hit":
        return "止盈达成"
    if status == "stopped":
        return "止损离场"
    if status == "time_exit":
        return "时间退出"
    return "已跟踪"


def _signed_return_pct(base: Decimal, value: Decimal) -> float:
    if base <= 0:
        return 0.0
    return round(float((value / base - Decimal("1")) * Decimal("100")), 4)


def _pct(value: Decimal, denominator: Decimal) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(value / denominator * Decimal("100")), 4)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _money_down(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _shares(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)


def _bps_rate(value: Decimal) -> Decimal:
    return value / Decimal("10000")


def _evaluate_trade(
    trade: PaperTradeRecord,
    bars: pd.DataFrame,
    max_holding_days: int,
    max_entry_wait_days: int,
) -> dict[str, object]:
    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    if pd.api.types.is_datetime64_any_dtype(ordered["trade_date"]):
        ordered["trade_date"] = ordered["trade_date"].dt.date
    entry_date = trade.entry_date
    entry_price = trade.entry_price
    status = trade.status
    notes = trade.notes

    for _, row in ordered.iterrows():
        trade_date = row["trade_date"]
        high = Decimal(str(row["high"]))
        low = Decimal(str(row["low"]))
        close = Decimal(str(row["close"]))
        if status == "pending":
            wait_days = max((trade_date - trade.signal_date).days, 0)
            if high >= trade.trigger_price:
                status = "open"
                entry_date = trade_date
                entry_price = trade.trigger_price
                notes = "Entry triggered by daily high crossing trigger."
            elif wait_days > max_entry_wait_days:
                return {
                    "status": "time_exit",
                    "latest_date": trade_date,
                    "latest_price": close,
                    "exit_date": trade_date,
                    "exit_price": close,
                    "realized_return_pct": Decimal("0"),
                    "holding_days": 0,
                    "notes": "Entry trigger expired before execution.",
                }
            else:
                continue

        if status == "open" and entry_date is not None and entry_price is not None:
            holding_days = max((trade_date - entry_date).days, 0)
            if trade.initial_stop is not None and low <= trade.initial_stop:
                return _closed_update(
                    status="stopped",
                    entry_date=entry_date,
                    entry_price=entry_price,
                    exit_date=trade_date,
                    exit_price=trade.initial_stop,
                    latest_price=close,
                    holding_days=holding_days,
                    notes="Initial stop touched.",
                )
            if trade.target_1 is not None and high >= trade.target_1:
                return _closed_update(
                    status="target_1_hit",
                    entry_date=entry_date,
                    entry_price=entry_price,
                    exit_date=trade_date,
                    exit_price=trade.target_1,
                    latest_price=close,
                    holding_days=holding_days,
                    notes="Target 1 touched.",
                )
            if holding_days >= max_holding_days:
                return _closed_update(
                    status="time_exit",
                    entry_date=entry_date,
                    entry_price=entry_price,
                    exit_date=trade_date,
                    exit_price=close,
                    latest_price=close,
                    holding_days=holding_days,
                    notes="Maximum holding window reached.",
                )

    latest = ordered.iloc[-1]
    latest_date = latest["trade_date"]
    latest_price = Decimal(str(latest["close"]))
    if status == "open" and entry_date is not None and entry_price is not None:
        return {
            "status": "open",
            "entry_date": entry_date,
            "entry_price": entry_price,
            "latest_date": latest_date,
            "latest_price": latest_price,
            "unrealized_return_pct": Decimal(str(_return_pct(entry_price, latest_price))),
            "holding_days": max((latest_date - entry_date).days, 0),
            "notes": notes,
        }
    return {
        "status": "pending",
        "latest_date": latest_date,
        "latest_price": latest_price,
        "holding_days": 0,
        "notes": notes,
    }


def _closed_update(
    status: str,
    entry_date: date,
    entry_price: Decimal,
    exit_date: date,
    exit_price: Decimal,
    latest_price: Decimal,
    holding_days: int,
    notes: str,
) -> dict[str, object]:
    return {
        "status": status,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "latest_date": exit_date,
        "latest_price": latest_price,
        "realized_return_pct": Decimal(str(_return_pct(entry_price, exit_price))),
        "unrealized_return_pct": None,
        "holding_days": holding_days,
        "notes": notes,
    }


def _return_pct(entry_price: Decimal, exit_price: Decimal) -> float:
    if entry_price <= 0:
        return 0
    return round(float((exit_price / entry_price - Decimal("1")) * Decimal("100")), 4)
