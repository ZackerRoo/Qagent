from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException

from qagent.agent.responder import answer_question
from qagent.api.schemas import AgentQueryRequest, AgentQueryResponse, AlertEvaluationRequest
from qagent.backtesting.engine import run_historical_backtest
from qagent.backtesting.portfolio import run_portfolio_backtest
from qagent.briefing.daily import DailyBrief, build_daily_brief
from qagent.briefing.export import render_daily_brief_markdown
from qagent.catalysts.hypotheses import build_catalyst_hypotheses
from qagent.catalysts.providers import FreeCatalystProvider
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.daily_scan import run_daily_scan
from qagent.jobs.alert_runner import run_alert_rules
from qagent.jobs.intraday_check import evaluate_snapshot_alerts
from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE
from qagent.market.universes import UniverseCreate, builtin_universes, merge_universes
from qagent.monitoring.outcomes import compute_opportunity_outcome, summarize_strategy_performance
from qagent.monitoring.portfolio import PositionInput, analyze_position_risk
from qagent.monitoring.alerts import AlertRule, suggest_alert_rules
from qagent.providers.factory import build_market_data_provider
from qagent.providers.status import build_provider_status
from qagent.storage.repository import (
    AlertRuleCreate,
    PositionCreate,
    QagentRepository,
    WatchlistCreate,
)
from qagent.storage.market_cache import MarketDataCacheRepository

router = APIRouter()


def _signal_summary(card) -> str:
    return "; ".join(
        f"{signal.signal_type.value} {signal.direction.value} {signal.score:.2f}"
        for signal in card.signals[:4]
    )


def _strategy_summary(card) -> str:
    return "; ".join(
        f"{strategy.strategy_id} {strategy.status} {strategy.score:.2f}"
        for strategy in card.strategy_evaluations[:5]
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/provider-status")
def provider_status() -> dict[str, list[object]]:
    return {"providers": [status.model_dump(mode="json") for status in build_provider_status()]}


@router.get("/data-cache")
def data_cache(
    provider: str | None = None,
    instrument_id: str | None = None,
) -> dict[str, list[object]]:
    summaries = _market_cache_repo().list_summaries(
        provider_mode=provider.strip().lower() if provider else None,
        instrument_id=instrument_id.strip().upper() if instrument_id else None,
    )
    return {"summaries": [summary.model_dump(mode="json") for summary in summaries]}


@router.delete("/data-cache")
def clear_data_cache(
    provider: str | None = None,
    instrument_id: str | None = None,
) -> dict[str, int]:
    deleted = _market_cache_repo().delete(
        provider_mode=provider.strip().lower() if provider else None,
        instrument_id=instrument_id.strip().upper() if instrument_id else None,
    )
    return {"deleted": deleted}


def _parse_symbols(symbols: str | None, default_universe: list[str]) -> list[str]:
    if not symbols:
        return default_universe
    return [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]


def _scan(provider_mode: str = "fixture", symbols: str | None = None):
    mode = provider_mode.strip().lower()
    default_universe = DEFAULT_FREE_UNIVERSE if mode == "free" else DEFAULT_DEV_UNIVERSE
    instrument_ids = _parse_symbols(symbols, default_universe)
    try:
        provider = build_market_data_provider(mode)
        return run_daily_scan(instrument_ids, provider, mode=mode), mode, instrument_ids
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _backtest_dates(mode: str, start: date | None, end: date | None) -> tuple[date, date]:
    end_date = end or (date(2026, 3, 20) if mode == "fixture" else date.today())
    start_date = start or (
        date(2026, 1, 15) if mode == "fixture" else end_date - timedelta(days=180)
    )
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    return start_date, end_date


def _repo() -> QagentRepository:
    initialize_database()
    return QagentRepository(create_session_factory())


def _market_cache_repo() -> MarketDataCacheRepository:
    initialize_database()
    return MarketDataCacheRepository(create_session_factory())


@router.get("/opportunities")
def opportunities(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result, mode, instrument_ids = _scan(provider, symbols)
    _repo().save_scan_run(provider=mode, mode=mode, symbols=instrument_ids, result=result)
    return {
        "cards": [card.model_dump(mode="json") for card in result.cards],
        "items": [item.model_dump(mode="json") for item in result.items],
        "strategy_health": [item.model_dump(mode="json") for item in result.strategy_health],
        "data_health": result.data_health,
    }


@router.get("/overview")
def overview(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result, _, _ = _scan(provider, symbols)
    return {
        "market_regime": {
            "US": "development_fixture",
            "CN": "development_fixture",
        },
        "top_cards": [card.model_dump(mode="json") for card in result.cards[:5]],
        "strategy_health": [item.model_dump(mode="json") for item in result.strategy_health[:6]],
        "data_health": result.data_health,
    }


@router.get("/daily-brief")
def daily_brief(
    provider: str = "fixture",
    symbols: str | None = None,
    limit: int = 5,
    include_news: bool = True,
) -> dict[str, object]:
    brief = _build_daily_brief_response(provider, symbols, limit, include_news)
    return brief.model_dump(mode="json")


@router.post("/daily-brief/runs")
def save_daily_brief_run(
    provider: str = "fixture",
    symbols: str | None = None,
    limit: int = 5,
    include_news: bool = True,
) -> dict[str, object]:
    brief = _build_daily_brief_response(provider, symbols, limit, include_news)
    saved = _repo().save_brief_run(brief)
    return saved.model_dump(mode="json")


@router.get("/daily-brief/runs")
def daily_brief_runs(limit: int = 20) -> dict[str, list[object]]:
    return {"runs": [run.model_dump(mode="json") for run in _repo().list_brief_runs(limit=limit)]}


@router.get("/daily-brief/runs/{brief_id}")
def daily_brief_run(brief_id: str) -> dict[str, object]:
    run = _repo().get_brief_run(brief_id)
    if run is None:
        raise HTTPException(status_code=404, detail="brief run not found")
    return {"run": run.model_dump(mode="json"), "brief": run.payload}


@router.get("/daily-brief/runs/{brief_id}/markdown")
def daily_brief_run_markdown(brief_id: str) -> dict[str, str]:
    run = _repo().get_brief_run(brief_id)
    if run is None:
        raise HTTPException(status_code=404, detail="brief run not found")
    brief = DailyBrief.model_validate(run.payload)
    return {"markdown": render_daily_brief_markdown(brief)}


@router.post("/daily-brief/runs/{brief_id}/deliveries")
def queue_daily_brief_delivery(
    brief_id: str,
    channel: str = "markdown",
    recipient: str | None = None,
) -> dict[str, object]:
    repo = _repo()
    run = repo.get_brief_run(brief_id)
    if run is None:
        raise HTTPException(status_code=404, detail="brief run not found")
    if channel not in {"markdown", "email", "webhook"}:
        raise HTTPException(status_code=400, detail="unsupported delivery channel")
    brief = DailyBrief.model_validate(run.payload)
    delivery = repo.enqueue_brief_delivery(
        brief_run=run,
        channel=channel,
        recipient=recipient,
        markdown=render_daily_brief_markdown(brief),
    )
    return delivery.model_dump(mode="json")


@router.get("/deliveries")
def deliveries(status: str | None = None, limit: int = 20) -> dict[str, list[object]]:
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    return {
        "deliveries": [
            delivery.model_dump(mode="json")
            for delivery in _repo().list_delivery_outbox(status=status, limit=limit)
        ]
    }


@router.post("/deliveries/{delivery_id}/mark-sent")
def mark_delivery_sent(delivery_id: str) -> dict[str, object]:
    delivery = _repo().mark_delivery_sent(delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    return delivery.model_dump(mode="json")


def _build_daily_brief_response(
    provider: str,
    symbols: str | None,
    limit: int,
    include_news: bool,
):
    mode = provider.strip().lower()
    if limit <= 0 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    default_universe = DEFAULT_FREE_UNIVERSE if mode == "free" else DEFAULT_DEV_UNIVERSE
    instrument_ids = _parse_symbols(symbols, default_universe)
    start_date, end_date = _backtest_dates(mode, None, None)
    try:
        market_provider = build_market_data_provider(mode)
        scan_result = run_daily_scan(instrument_ids, market_provider, mode=mode)
        backtest_result = run_historical_backtest(
            instrument_ids=instrument_ids,
            provider=market_provider,
            start=start_date,
            end=end_date,
            step_days=5,
            max_signals=100,
        )
        position_risks = _position_risks(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    catalyst_hypotheses = []
    brief_health = {
        "brief_provider": mode,
        "brief_symbols": str(len(instrument_ids)),
        "brief_news": "skipped",
    }
    if include_news:
        catalyst_provider = FreeCatalystProvider()
        news = catalyst_provider.get_news(instrument_ids, limit=limit)
        catalyst_hypotheses = build_catalyst_hypotheses(news)
        brief_health["brief_news"] = str(len(news))
        brief_health["brief_catalysts"] = str(len(catalyst_hypotheses))
        if catalyst_provider.last_errors:
            brief_health["brief_news_errors"] = " | ".join(catalyst_provider.last_errors[:3])

    brief = build_daily_brief(
        provider=mode,
        symbols=instrument_ids,
        scan_result=scan_result,
        backtest_result=backtest_result,
        catalyst_hypotheses=catalyst_hypotheses,
        position_risks=position_risks,
        provider_statuses=build_provider_status(),
        limit=limit,
        data_health=brief_health,
    )
    return brief


@router.get("/backtest")
def backtest(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 5,
    limit: int = 100,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if step_days <= 0 or step_days > 60:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 60")
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    default_universe = DEFAULT_FREE_UNIVERSE if mode == "free" else DEFAULT_DEV_UNIVERSE
    instrument_ids = _parse_symbols(symbols, default_universe)
    start_date, end_date = _backtest_dates(mode, start, end)
    try:
        market_provider = build_market_data_provider(mode)
        result = run_historical_backtest(
            instrument_ids=instrument_ids,
            provider=market_provider,
            start=start_date,
            end=end_date,
            step_days=step_days,
            max_signals=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "summary": result.summary.model_dump(mode="json"),
        "performance": [item.model_dump(mode="json") for item in result.performance],
        "signals": [item.model_dump(mode="json") for item in result.signals],
        "data_health": result.data_health,
    }


@router.get("/portfolio-backtest")
def portfolio_backtest(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 5,
    initial_capital: Decimal = Decimal("100000"),
    risk_per_trade_pct: Decimal = Decimal("1"),
    max_positions: int = 5,
    transaction_cost_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("5"),
) -> dict[str, object]:
    mode = provider.strip().lower()
    if step_days <= 0 or step_days > 60:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 60")
    if initial_capital <= 0:
        raise HTTPException(status_code=400, detail="initial_capital must be positive")
    if risk_per_trade_pct <= 0 or risk_per_trade_pct > 10:
        raise HTTPException(status_code=400, detail="risk_per_trade_pct must be between 0 and 10")
    if max_positions <= 0 or max_positions > 20:
        raise HTTPException(status_code=400, detail="max_positions must be between 1 and 20")

    default_universe = DEFAULT_FREE_UNIVERSE if mode == "free" else DEFAULT_DEV_UNIVERSE
    instrument_ids = _parse_symbols(symbols, default_universe)
    start_date, end_date = _backtest_dates(mode, start, end)
    try:
        market_provider = build_market_data_provider(mode)
        result = run_portfolio_backtest(
            instrument_ids=instrument_ids,
            provider=market_provider,
            start=start_date,
            end=end_date,
            step_days=step_days,
            initial_capital=initial_capital,
            risk_per_trade_pct=risk_per_trade_pct,
            max_positions=max_positions,
            transaction_cost_bps=transaction_cost_bps,
            slippage_bps=slippage_bps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "summary": result.summary.model_dump(mode="json"),
        "trades": [trade.model_dump(mode="json") for trade in result.trades],
        "equity_curve": [point.model_dump(mode="json") for point in result.equity_curve],
        "data_health": result.data_health,
    }


def _position_risks(provider: str):
    positions = _repo().list_positions()
    if not positions:
        return []
    market_provider = build_market_data_provider(provider)
    snapshot = market_provider.get_snapshot([position.instrument_id for position in positions])
    latest_prices = {
        row["instrument_id"]: Decimal(str(row["close"]))
        for _, row in snapshot.iterrows()
    }
    risks = []
    for position in positions:
        latest_price = latest_prices.get(position.instrument_id)
        if latest_price is None:
            continue
        risks.append(
            analyze_position_risk(
                PositionInput(**position.model_dump()),
                current_price=latest_price,
            )
        )
    return risks


@router.get("/alerts")
def alerts() -> dict[str, list[object]]:
    return {"alerts": []}


@router.get("/catalysts")
def catalysts(symbols: str | None = None, limit: int = 5) -> dict[str, object]:
    instrument_ids = _parse_symbols(symbols, DEFAULT_FREE_UNIVERSE)
    provider = FreeCatalystProvider()
    news = provider.get_news(instrument_ids, limit=limit)
    hypotheses = build_catalyst_hypotheses(news)
    data_health = {
        "provider": "free",
        "scanned": str(len(instrument_ids)),
        "news": str(len(news)),
        "hypotheses": str(len(hypotheses)),
    }
    if provider.last_errors:
        data_health["errors"] = " | ".join(provider.last_errors[:3])
    return {
        "news": [item.model_dump(mode="json") for item in news],
        "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
        "data_health": data_health,
    }


@router.get("/alert-rules")
def alert_rules() -> dict[str, list[object]]:
    return {"rules": [rule.model_dump(mode="json") for rule in _repo().list_alert_rules()]}


@router.post("/alert-rules")
def upsert_alert_rule(rule: AlertRuleCreate) -> dict[str, object]:
    saved = _repo().upsert_alert_rule(rule)
    return saved.model_dump(mode="json")


@router.get("/universes")
def universes() -> dict[str, list[object]]:
    repo = _repo()
    return {
        "universes": [
            universe.model_dump(mode="json")
            for universe in merge_universes(repo.list_custom_universes())
        ]
    }


@router.post("/universes")
def upsert_universe(universe: UniverseCreate) -> dict[str, object]:
    saved = _repo().upsert_universe(universe)
    return saved.model_dump(mode="json")


@router.get("/universes/{universe_id}")
def universe_detail(universe_id: str) -> dict[str, object]:
    repo = _repo()
    custom = repo.get_universe(universe_id)
    if custom is not None:
        return {"universe": custom.model_dump(mode="json")}
    builtin = next(
        (universe for universe in builtin_universes() if universe.universe_id == universe_id),
        None,
    )
    if builtin is None:
        raise HTTPException(status_code=404, detail="universe not found")
    return {"universe": builtin.model_dump(mode="json")}


@router.post("/alerts/evaluate")
def evaluate_alerts(request: AlertEvaluationRequest) -> dict[str, list[object]]:
    prices = {instrument_id: Decimal(price) for instrument_id, price in request.prices.items()}
    rules = [
        AlertRule(
            rule_id=rule.rule_id,
            instrument_id=rule.instrument_id,
            kind=rule.kind,
            operator=rule.operator,
            threshold=rule.threshold,
        )
        for rule in _repo().list_alert_rules()
    ]
    alerts = evaluate_snapshot_alerts(prices, rules)
    return {"alerts": [alert.model_dump(mode="json") for alert in alerts]}


@router.post("/alerts/run")
def run_alerts(
    provider: str = "fixture",
    queue: bool = False,
    recipient: str | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    try:
        market_provider = build_market_data_provider(mode)
        result = run_alert_rules(
            repo=_repo(),
            provider=market_provider,
            queue_delivery=queue,
            recipient=recipient,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "summary": result.summary.model_dump(mode="json"),
        "alerts": [alert.model_dump(mode="json") for alert in result.alerts],
        "latest_prices": {key: str(value) for key, value in result.latest_prices.items()},
        "delivery": result.delivery.model_dump(mode="json") if result.delivery else None,
        "data_health": result.data_health,
    }


@router.get("/alert-suggestions")
def alert_suggestions(limit: int = 50) -> dict[str, list[object]]:
    snapshots = _repo().list_opportunity_snapshots(limit=limit)
    suggestions = suggest_alert_rules(snapshots)
    return {"suggestions": [item.model_dump(mode="json") for item in suggestions]}


@router.get("/scan-runs")
def scan_runs(limit: int = 20) -> dict[str, list[object]]:
    return {"runs": [run.model_dump(mode="json") for run in _repo().list_scan_runs(limit=limit)]}


@router.get("/opportunity-history")
def opportunity_history(
    instrument_id: str | None = None,
    limit: int = 50,
) -> dict[str, list[object]]:
    snapshots = _repo().list_opportunity_snapshots(instrument_id=instrument_id, limit=limit)
    return {"snapshots": [snapshot.model_dump(mode="json") for snapshot in snapshots]}


@router.get("/outcomes")
def outcomes(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    return {
        "outcomes": [outcome.model_dump(mode="json") for outcome in replayed],
        "data_health": data_health,
    }


def _replay_outcomes(provider: str, instrument_id: str | None, limit: int):
    repo = _repo()
    snapshots = repo.list_opportunity_snapshots(instrument_id=instrument_id, limit=limit)
    try:
        market_provider = build_market_data_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    replayed = []
    for snapshot in snapshots:
        bars = market_provider.get_daily_bars(
            [snapshot.instrument_id],
            start=date(1900, 1, 1),
            end=date(2100, 1, 1),
        )
        replayed.append(compute_opportunity_outcome(snapshot, bars))
    data_health = {
        "provider": provider,
        "snapshots": str(len(snapshots)),
        "outcomes": str(len(replayed)),
    }
    provider_errors = getattr(market_provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return replayed, data_health


@router.get("/strategy-performance")
def strategy_performance(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 100,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    return {
        "performance": [
            item.model_dump(mode="json") for item in summarize_strategy_performance(replayed)
        ],
        "data_health": data_health,
    }


@router.get("/portfolio")
def portfolio(provider: str = "fixture") -> dict[str, object]:
    positions = _repo().list_positions()
    risks = []
    data_health = {"provider": provider, "positions": str(len(positions)), "risk": "0"}
    if positions:
        try:
            market_provider = build_market_data_provider(provider)
            instrument_ids = [position.instrument_id for position in positions]
            snapshot = market_provider.get_snapshot(instrument_ids)
            latest_prices = {
                row["instrument_id"]: Decimal(str(row["close"]))
                for _, row in snapshot.iterrows()
            }
            for position in positions:
                latest_price = latest_prices.get(position.instrument_id)
                if latest_price is None:
                    continue
                risks.append(
                    analyze_position_risk(
                        PositionInput(**position.model_dump()),
                        current_price=latest_price,
                    )
                )
            data_health["risk"] = str(len(risks))
            provider_errors = getattr(market_provider, "last_errors", [])
            if provider_errors:
                data_health["errors"] = " | ".join(provider_errors[:3])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "positions": [position.model_dump(mode="json") for position in positions],
        "risk": [risk.model_dump(mode="json") for risk in risks],
        "data_health": data_health,
    }


@router.get("/watchlist")
def watchlist() -> dict[str, list[object]]:
    return {"items": [item.model_dump(mode="json") for item in _repo().list_watchlist_items()]}


@router.post("/watchlist")
def upsert_watchlist_item(item: WatchlistCreate) -> dict[str, object]:
    saved = _repo().upsert_watchlist_item(item)
    return saved.model_dump(mode="json")


@router.get("/positions")
def positions() -> dict[str, list[object]]:
    return {"positions": [position.model_dump(mode="json") for position in _repo().list_positions()]}


@router.post("/positions")
def upsert_position(position: PositionCreate) -> dict[str, object]:
    saved = _repo().upsert_position(position)
    return saved.model_dump(mode="json")


@router.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(request: AgentQueryRequest) -> AgentQueryResponse:
    result, _, _ = _scan()
    selected = None
    if request.instrument_id:
        selected = next((card for card in result.cards if card.instrument_id == request.instrument_id), None)
    if selected is None and result.cards:
        selected = result.cards[0]
    if selected is None:
        return AgentQueryResponse(answer="No opportunity context is available yet.")

    answer = answer_question(
        request.question,
        context={
            "instrument_id": selected.instrument_id,
            "status": selected.status.value,
            "score": selected.score,
            "initial_stop": str(selected.exit_plan.initial_stop),
            "trigger_price": str(selected.entry_plan.trigger_price),
            "target_1": str(selected.exit_plan.target_1),
            "downside_pct": selected.scenario.downside_pct,
            "target_1_pct": selected.scenario.target_1_pct,
            "no_chase_above": str(selected.entry_plan.no_chase_above),
            "signal_summary": _signal_summary(selected),
            "primary_strategy_id": selected.primary_strategy_id,
            "strategy_score": selected.strategy_score,
            "strategy_summary": _strategy_summary(selected),
        },
    )
    return AgentQueryResponse(answer=answer)
