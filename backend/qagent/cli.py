import argparse
from datetime import date, timedelta

from qagent.backtesting.engine import run_historical_backtest
from qagent.briefing.daily import build_daily_brief
from qagent.briefing.export import render_daily_brief_markdown
from qagent.catalysts.hypotheses import build_catalyst_hypotheses
from qagent.catalysts.providers import FreeCatalystProvider
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE
from qagent.providers.factory import build_market_data_provider
from qagent.providers.status import build_provider_status
from qagent.storage.repository import QagentRepository


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
    args = parser.parse_args(argv)

    if args.command == "daily-brief":
        return _daily_brief_command(args)

    result = run_daily_scan(DEFAULT_DEV_UNIVERSE, build_market_data_provider("fixture"))
    for card in result.cards:
        print(f"{card.instrument_id} {card.status.value} score={card.score}")
    return 0


def _daily_brief_command(args: argparse.Namespace) -> int:
    mode = args.provider.strip().lower()
    default_universe = DEFAULT_FREE_UNIVERSE if mode == "free" else DEFAULT_DEV_UNIVERSE
    symbols = _parse_symbols(args.symbols, default_universe)
    provider = build_market_data_provider(mode)
    scan_result = run_daily_scan(symbols, provider, mode=mode)
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
    if not args.no_news:
        catalyst_provider = FreeCatalystProvider()
        news = catalyst_provider.get_news(symbols, limit=args.limit)
        catalysts = build_catalyst_hypotheses(news)
        data_health["brief_news"] = str(len(news))
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


if __name__ == "__main__":
    raise SystemExit(main())
