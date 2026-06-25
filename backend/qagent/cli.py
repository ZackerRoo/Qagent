import argparse
from datetime import date, timedelta
from pathlib import Path

from qagent.backtesting.engine import run_historical_backtest
from qagent.briefing.daily import build_daily_brief
from qagent.briefing.export import render_daily_brief_markdown
from qagent.catalysts.hypotheses import build_catalyst_hypotheses
from qagent.catalysts.providers import FreeCatalystProvider
from qagent.delivery.senders import send_pending_deliveries
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.automation import run_research_automation
from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.a_share_universe import ResolvedSymbols, resolve_symbol_tokens
from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE
from qagent.providers.factory import build_market_data_provider
from qagent.providers.status import build_provider_status
from qagent.storage.repository import QagentRepository
from qagent.strategy_data.providers import EmptyStrategyDataProvider


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
    args = parser.parse_args(argv)

    if args.command == "daily-brief":
        return _daily_brief_command(args)
    if args.command == "send-outbox":
        return _send_outbox_command(args)
    if args.command == "run-all":
        return _run_all_command(args)

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
