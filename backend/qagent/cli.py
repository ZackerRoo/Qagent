import argparse
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from qagent.backtesting.engine import run_historical_backtest
from qagent.backtesting.a_share_rules import BrokerFeeRequest
from qagent.backtesting.walk_forward import run_full_market_walk_forward_selection
from qagent.briefing.daily import build_daily_brief
from qagent.briefing.export import render_daily_brief_markdown
from qagent.catalysts.hypotheses import build_catalyst_hypotheses
from qagent.catalysts.providers import FreeCatalystProvider
from qagent.data_management import HistoricalBackfillFailed, run_historical_backfill
from qagent.delivery.senders import send_pending_deliveries
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.automation import run_research_automation
from qagent.jobs.daily_scan import run_daily_scan
from qagent.historical_evidence.providers import (
    build_historical_evidence_provider,
    build_historical_fundamental_provider,
)
from qagent.market.a_share_universe import ResolvedSymbols, resolve_symbol_tokens
from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE
from qagent.providers.factory import build_market_data_provider
from qagent.providers.status import build_provider_status
from qagent.storage.repository import QagentRepository
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.strategy_data.providers import EmptyStrategyDataProvider, build_strategy_data_provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qagent")
    subparsers = parser.add_subparsers(dest="command")
    brief_parser = subparsers.add_parser("daily-brief")
    brief_parser.add_argument("--provider", default="fixture", choices=["fixture", "free"])
    brief_parser.add_argument("--symbols", default=None)
    brief_parser.add_argument("--limit", type=int, default=5)
    brief_parser.add_argument("--no-news", action="store_true")
    brief_parser.add_argument("--save", action="store_true")
    brief_parser.add_argument("--queue", action="store_true")
    brief_parser.add_argument("--channel", default="markdown")
    brief_parser.add_argument("--recipient", default=None)
    brief_parser.add_argument("--print-markdown", action="store_true")
    send_parser = subparsers.add_parser("send-outbox")
    send_parser.add_argument("--channel", default=None)
    send_parser.add_argument("--output-dir", default=None)
    send_parser.add_argument("--webhook-url", default=None)
    send_parser.add_argument("--dry-run", action="store_true")
    send_parser.add_argument("--limit", type=int, default=20)
    run_all_parser = subparsers.add_parser("run-all")
    run_all_parser.add_argument("--provider", default="fixture", choices=["fixture", "free"])
    run_all_parser.add_argument("--symbols", default=None)
    run_all_parser.add_argument("--limit", type=int, default=5)
    run_all_parser.add_argument("--no-news", action="store_true")
    run_all_parser.add_argument("--queue-brief", action="store_true")
    run_all_parser.add_argument("--run-alerts", action="store_true")
    run_all_parser.add_argument("--queue-alerts", action="store_true")
    run_all_parser.add_argument("--run-backtest", action="store_true")
    run_all_parser.add_argument("--recipient", default=None)
    run_all_parser.add_argument("--send-outbox", action="store_true")
    run_all_parser.add_argument("--output-dir", default=None)
    history_parser = subparsers.add_parser("backfill-history")
    history_parser.add_argument("--provider", default="free", choices=["fixture", "free"])
    history_parser.add_argument("--symbols", default=None)
    history_parser.add_argument(
        "--scope", default="symbols", choices=["symbols", "full-a-share"]
    )
    history_parser.add_argument("--start", type=date.fromisoformat, required=True)
    history_parser.add_argument("--end", type=date.fromisoformat, required=True)
    history_parser.add_argument("--batch-size", type=int, default=100)
    history_parser.add_argument("--resume", default=None, metavar="JOB_ID")
    history_parser.add_argument("--manifest-output", type=Path, default=None)
    history_parser.add_argument("--commission-bps", type=Decimal, default=Decimal("3"))
    history_parser.add_argument(
        "--minimum-commission", type=Decimal, default=Decimal("5")
    )
    walk_parser = subparsers.add_parser("walk-forward")
    walk_parser.add_argument("--provider", default="free", choices=["free"])
    walk_parser.add_argument("--start", type=date.fromisoformat, required=True)
    walk_parser.add_argument("--end", type=date.fromisoformat, required=True)
    walk_parser.add_argument("--step-sessions", type=int, default=5)
    walk_parser.add_argument("--lookback-days", type=int, default=400)
    walk_parser.add_argument("--run-id", required=True)
    walk_parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.command == "daily-brief":
        return _daily_brief_command(args)
    if args.command == "send-outbox":
        return _send_outbox_command(args)
    if args.command == "run-all":
        return _run_all_command(args)
    if args.command == "backfill-history":
        return _backfill_history_command(args)
    if args.command == "walk-forward":
        return _walk_forward_command(args)

    result = run_daily_scan(DEFAULT_DEV_UNIVERSE, build_market_data_provider("fixture"))
    for card in result.cards:
        print(f"{card.instrument_id} {card.status.value} score={card.score}")
    return 0


def _send_outbox_command(args: argparse.Namespace) -> int:
    initialize_database()
    repo = QagentRepository(create_session_factory())
    result = send_pending_deliveries(
        repo=repo,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        channel=args.channel,
        webhook_url=args.webhook_url,
        dry_run=args.dry_run,
        limit=args.limit,
    )
    for item in result.items:
        if item.status == "sent":
            print(f"sent {item.delivery_id} {item.destination}")
        elif item.status == "dry_run":
            print(f"dry-run {item.delivery_id}")
        else:
            print(f"failed {item.delivery_id} {item.error}")
    print(
        f"summary scanned={result.scanned} sent={result.sent} "
        f"failed={result.failed} dry_run={result.dry_run}"
    )
    return 1 if result.failed else 0


def _run_all_command(args: argparse.Namespace) -> int:
    mode = args.provider.strip().lower()
    resolved = _resolve_symbols(mode, args.symbols)
    symbols = resolved.symbols
    initialize_database()
    repo = QagentRepository(create_session_factory())
    result = run_research_automation(
        repo=repo,
        provider=build_market_data_provider(mode),
        provider_mode=mode,
        symbols=symbols,
        include_news=False if resolved.is_dynamic else not args.no_news,
        queue_brief=args.queue_brief,
        run_alerts=args.run_alerts,
        queue_alerts=args.queue_alerts,
        run_backtest=args.run_backtest,
        recipient=args.recipient,
        limit=args.limit,
        strategy_data_provider=EmptyStrategyDataProvider() if resolved.is_dynamic else None,
    )
    result.data_health.update(resolved.data_health)
    print(
        f"automation provider={result.summary.provider} symbols={result.summary.symbols} "
        f"cards={result.summary.cards} scan={result.scan_run_id} brief={result.brief_id}"
    )
    if result.brief_delivery_id:
        print(f"queued-brief {result.brief_delivery_id}")
    if result.alert_delivery_id:
        print(f"queued-alerts {result.alert_delivery_id}")
    if result.backtest:
        print(f"backtest signals={len(result.backtest.signals)}")
    if args.send_outbox:
        send_result = send_pending_deliveries(
            repo=repo,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            channel="markdown",
        )
        print(f"sent-outbox sent={send_result.sent} failed={send_result.failed}")
        return 1 if send_result.failed else 0
    return 0


def _daily_brief_command(args: argparse.Namespace) -> int:
    mode = args.provider.strip().lower()
    resolved = _resolve_symbols(mode, args.symbols)
    symbols = resolved.symbols
    provider = build_market_data_provider(mode)
    scan_result = run_daily_scan(
        symbols,
        provider,
        mode=mode,
        strategy_data_provider=EmptyStrategyDataProvider() if resolved.is_dynamic else None,
    )
    end_date = date(2026, 3, 20) if mode == "fixture" else date.today()
    start_date = date(2026, 1, 15) if mode == "fixture" else end_date - timedelta(days=180)
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
        "brief_provider": mode,
        "brief_symbols": str(len(symbols)),
        "brief_news": "skipped",
    }
    data_health.update(resolved.data_health)
    if resolved.is_dynamic:
        data_health["strategy_data_skipped"] = "true"
    if not args.no_news:
        catalyst_provider = FreeCatalystProvider()
        news_symbols = [card.instrument_id for card in scan_result.cards[: args.limit]] or symbols[: args.limit]
        news = catalyst_provider.get_news(news_symbols, limit=args.limit)
        catalysts = build_catalyst_hypotheses(news)
        data_health["brief_news"] = str(len(news))
        data_health["brief_news_symbols"] = str(len(news_symbols))
        if catalyst_provider.last_errors:
            data_health["brief_news_errors"] = " | ".join(catalyst_provider.last_errors[:3])

    brief = build_daily_brief(
        provider=mode,
        symbols=symbols,
        scan_result=scan_result,
        backtest_result=backtest_result,
        catalyst_hypotheses=catalysts,
        provider_statuses=build_provider_status(),
        limit=args.limit,
        data_health=data_health,
    )
    markdown = render_daily_brief_markdown(brief)
    saved = None
    if args.save or args.queue:
        initialize_database()
        repo = QagentRepository(create_session_factory())
        saved = repo.save_brief_run(brief)
        print(f"saved {saved.brief_id}")
    if args.queue:
        if saved is None:
            initialize_database()
            repo = QagentRepository(create_session_factory())
            saved = repo.save_brief_run(brief)
        delivery = repo.enqueue_brief_delivery(
            brief_run=saved,
            channel=args.channel,
            recipient=args.recipient,
            markdown=markdown,
        )
        print(f"queued {delivery.delivery_id}")
    if args.print_markdown or not (args.save or args.queue):
        print(markdown, end="")
    return 0


def _backfill_history_command(args: argparse.Namespace) -> int:
    mode = args.provider.strip().lower()
    if args.scope == "symbols" and not args.symbols and not args.resume:
        raise ValueError("--symbols is required when --scope=symbols")
    symbols = (
        _resolve_symbols(mode, args.symbols).symbols
        if args.scope == "symbols" and args.symbols
        else []
    )
    initialize_database()
    session_factory = create_session_factory()
    repo = QagentRepository(session_factory)
    if args.resume and args.scope == "symbols" and not symbols:
        resumed = repo.get_historical_backfill_job(args.resume)
        if resumed is None:
            raise ValueError(f"historical backfill job not found: {args.resume}")
        symbols = resumed.symbols
    symbols = [symbol for symbol in symbols if symbol.startswith("CN:")]
    if args.scope == "symbols" and not symbols:
        raise ValueError("backfill-history currently supports A-share symbols only")
    historical_evidence_provider = build_historical_evidence_provider(mode)
    try:
        result = run_historical_backfill(
            repo=repo,
            cache=MarketDataCacheRepository(session_factory),
            provider=build_market_data_provider(mode),
            strategy_provider=(
                build_historical_fundamental_provider(mode)
                or build_strategy_data_provider(mode)
            ),
            provider_mode=mode,
            instrument_ids=symbols,
            start=args.start,
            end=args.end,
            job_id=args.resume,
            historical_evidence_provider=historical_evidence_provider,
            scope=args.scope,
            batch_size=args.batch_size,
            broker_fee_request=BrokerFeeRequest(
                commission_bps=args.commission_bps,
                minimum_commission=args.minimum_commission,
            ),
        )
    except HistoricalBackfillFailed as exc:
        result = exc.result
    if args.manifest_output is not None:
        args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_output.write_text(
            result.manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
    summary = result.manifest.summary
    print(
        f"history-backfill status={result.job.status} symbols={result.job.total_symbols} "
        f"rows={result.job.rows_written} coverage={summary.average_bar_coverage_ratio:.1%} "
        f"ready={summary.ready_instruments} partial={summary.partial_instruments} "
        f"missing={summary.missing_instruments}"
    )
    return 0 if result.job.status in {"succeeded", "succeeded_with_errors"} else 1


def _walk_forward_command(args: argparse.Namespace) -> int:
    initialize_database()
    repository = ReplayEvidenceRepository(
        create_session_factory(),
        args.provider,
    )
    result = run_full_market_walk_forward_selection(
        repository,
        owner_run_id=args.run_id,
        start=args.start,
        end=args.end,
        rebalance_step_sessions=args.step_sessions,
        lookback_days=args.lookback_days,
    )
    stored = QagentRepository(repository.session_factory).save_walk_forward_run(result)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(
        f"walk-forward revision={result.dataset_revision} "
        f"snapshots={len(result.snapshots)} "
        f"top5_trades={result.top_5_portfolio.summary.trade_count} "
        f"top5_return={result.top_5_portfolio.summary.total_return_pct:.2f}% "
        f"top10_trades={result.top_10_portfolio.summary.trade_count} "
        f"top10_return={result.top_10_portfolio.summary.total_return_pct:.2f}% "
        f"top5_oos={result.data_health['walk_forward_top_5_oos_trades']}/30 "
        f"top10_oos={result.data_health['walk_forward_top_10_oos_trades']}/30 "
        f"stress_top5={result.data_health['walk_forward_stress_top_5_return_pct']}% "
        f"stress_top10={result.data_health['walk_forward_stress_top_10_return_pct']}% "
        f"equal_weight={result.data_health['walk_forward_equal_weight_benchmark']} "
        f"persisted={stored.run_id} "
        f"digest={result.reproducibility_digest}"
    )
    return 0


def _parse_symbols(symbols: str | None, default_universe: list[str]) -> list[str]:
    if not symbols:
        return default_universe
    return [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]


def _resolve_symbols(mode: str, symbols: str | None) -> ResolvedSymbols:
    default_universe = DEFAULT_FREE_UNIVERSE if mode == "free" else DEFAULT_DEV_UNIVERSE
    parsed = _parse_symbols(symbols, default_universe)
    if mode == "free":
        return resolve_symbol_tokens(parsed)
    return ResolvedSymbols(symbols=parsed)


if __name__ == "__main__":
    raise SystemExit(main())
