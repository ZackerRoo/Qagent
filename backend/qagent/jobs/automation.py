from datetime import date, timedelta

from pydantic import BaseModel

from qagent.backtesting.engine import BacktestResult, run_historical_backtest
from qagent.briefing.daily import build_daily_brief
from qagent.briefing.export import render_daily_brief_markdown
from qagent.catalysts.hypotheses import build_catalyst_hypotheses
from qagent.catalysts.providers import FreeCatalystProvider
from qagent.jobs.alert_runner import AlertRunResult, run_alert_rules
from qagent.jobs.daily_scan import run_daily_scan
from qagent.paper_trading.engine import (
    PaperUpdateResult,
    seed_paper_trades_from_snapshots,
    update_paper_trades,
)
from qagent.providers.base import MarketDataProvider
from qagent.providers.status import build_provider_status
from qagent.storage.paper import PaperTradingRepository
from qagent.storage.repository import QagentRepository
from qagent.strategy_data.providers import StrategyDataProvider


class AutomationSummary(BaseModel):
    provider: str
    symbols: int
    scanned: int
    cards: int
    brief_queued: bool
    alerts_triggered: int
    backtest_signals: int
    paper_created: int
    paper_total: int
    paper_closed: int


class AutomationRunResult(BaseModel):
    summary: AutomationSummary
    scan_run_id: str
    brief_id: str
    brief_delivery_id: str | None = None
    alert_delivery_id: str | None = None
    backtest: BacktestResult | None = None
    alert_run: AlertRunResult | None = None
    paper_update: PaperUpdateResult | None = None
    data_health: dict[str, str]


def run_research_automation(
    repo: QagentRepository,
    provider: MarketDataProvider,
    provider_mode: str,
    symbols: list[str],
    include_news: bool = True,
    queue_brief: bool = True,
    run_alerts: bool = False,
    queue_alerts: bool = True,
    run_backtest: bool = True,
    seed_paper: bool = True,
    update_paper: bool = True,
    recipient: str | None = None,
    limit: int = 5,
    strategy_data_provider: StrategyDataProvider | None = None,
) -> AutomationRunResult:
    mode = provider_mode.strip().lower()
    scan_result = run_daily_scan(
        symbols,
        provider,
        mode=mode,
        strategy_data_provider=strategy_data_provider,
    )
    scan_run = repo.save_scan_run(provider=mode, mode=mode, symbols=symbols, result=scan_result)
    paper_seed_created = 0
    paper_update = None
    paper_repo = PaperTradingRepository(repo.session_factory)
    if seed_paper and scan_result.cards:
        snapshots = repo.list_opportunity_snapshots(limit=len(scan_result.cards))
        seed_result = seed_paper_trades_from_snapshots(
            paper_repo,
            snapshots,
            provider=mode,
            max_created=len(scan_result.cards),
        )
        paper_seed_created = seed_result.created
    if update_paper:
        paper_update = update_paper_trades(paper_repo, provider=provider)
    start_date, end_date = _backtest_dates(mode)
    backtest_result = None
    if run_backtest:
        backtest_result = run_historical_backtest(
            instrument_ids=symbols,
            provider=provider,
            start=start_date,
            end=end_date,
            step_days=5,
            max_signals=100,
        )
    catalysts = []
    data_health = {
        "automation_provider": mode,
        "automation_symbols": str(len(symbols)),
        "automation_news": "skipped",
    }
    if include_news:
        catalyst_provider = FreeCatalystProvider()
        news = catalyst_provider.get_news(symbols, limit=limit)
        catalysts = build_catalyst_hypotheses(news)
        data_health["automation_news"] = str(len(news))
        if catalyst_provider.last_errors:
            data_health["automation_news_errors"] = " | ".join(catalyst_provider.last_errors[:3])

    brief = build_daily_brief(
        provider=mode,
        symbols=symbols,
        scan_result=scan_result,
        backtest_result=backtest_result,
        catalyst_hypotheses=catalysts,
        provider_statuses=build_provider_status(),
        limit=limit,
        data_health=data_health,
    )
    saved_brief = repo.save_brief_run(brief)
    brief_delivery = None
    if queue_brief:
        brief_delivery = repo.enqueue_brief_delivery(
            brief_run=saved_brief,
            channel="markdown",
            recipient=recipient,
            markdown=render_daily_brief_markdown(brief),
        )

    alert_result = None
    if run_alerts:
        alert_result = run_alert_rules(
            repo=repo,
            provider=provider,
            queue_delivery=queue_alerts,
            recipient=recipient,
        )

    return AutomationRunResult(
        summary=AutomationSummary(
            provider=mode,
            symbols=len(symbols),
            scanned=len(scan_result.items),
            cards=len(scan_result.cards),
            brief_queued=brief_delivery is not None,
            alerts_triggered=alert_result.summary.triggered if alert_result else 0,
            backtest_signals=len(backtest_result.signals) if backtest_result else 0,
            paper_created=paper_seed_created,
            paper_total=paper_update.summary.total if paper_update else len(paper_repo.list_trades()),
            paper_closed=paper_update.summary.closed if paper_update else 0,
        ),
        scan_run_id=scan_run.run_id,
        brief_id=saved_brief.brief_id,
        brief_delivery_id=brief_delivery.delivery_id if brief_delivery else None,
        alert_delivery_id=alert_result.delivery.delivery_id
        if alert_result and alert_result.delivery
        else None,
        backtest=backtest_result,
        alert_run=alert_result,
        paper_update=paper_update,
        data_health={**scan_result.data_health, **data_health},
    )


def _backtest_dates(mode: str) -> tuple[date, date]:
    end_date = date(2026, 3, 20) if mode == "fixture" else date.today()
    start_date = date(2026, 1, 15) if mode == "fixture" else end_date - timedelta(days=180)
    return start_date, end_date
