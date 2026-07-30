import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from qagent.backtesting.engine import run_historical_backtest
from qagent.backtesting.a_share_rules import BrokerFeeRequest
from qagent.backtesting.ranking_v4_forward_evidence import (
    build_attempt_inventory_snapshot,
    build_prospective_definition,
    stable_digest,
)
from qagent.backtesting.ranking_v4_prospective_release import (
    REGISTERED_CHECKPOINTS,
    build_prospective_execution_summary,
    build_prospective_release_policy,
)
from qagent.backtesting.walk_forward import (
    WalkForwardSelectionResult,
    run_full_market_walk_forward_selection,
    walk_forward_selection_result_digest_is_valid,
)
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
from qagent.market.calendars import trading_day_offset
from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE
from qagent.providers.factory import build_market_data_provider
from qagent.providers.status import build_provider_status
from qagent.storage.repository import QagentRepository
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.storage.ranking_v4_forward_evidence import RankingV4EvidenceRepository
from qagent.storage.ranking_v4_prospective_release import (
    RankingV4ProspectiveReleaseRepository,
)
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
    evidence_freeze_parser = subparsers.add_parser("ranking-v4-evidence-freeze")
    evidence_freeze_parser.add_argument("--epoch-id", required=True)
    evidence_freeze_parser.add_argument("--code-revision", required=True)
    evidence_freeze_parser.add_argument("--provider", default="free", choices=["free"])
    evidence_freeze_parser.add_argument("--dataset-revision", type=int, default=None)
    evidence_freeze_parser.add_argument(
        "--evidence-start",
        type=date.fromisoformat,
        default=None,
    )
    evidence_status_parser = subparsers.add_parser("ranking-v4-evidence-status")
    evidence_status_parser.add_argument("--epoch-id", required=True)
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
    if args.command == "ranking-v4-evidence-freeze":
        return _ranking_v4_evidence_freeze_command(args)
    if args.command == "ranking-v4-evidence-status":
        return _ranking_v4_evidence_status_command(args)

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
    _append_ranking_v4_evidence_if_registered(
        result,
        session_factory=repository.session_factory,
    )
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


def _append_ranking_v4_evidence_if_registered(
    result: WalkForwardSelectionResult,
    *,
    session_factory,
) -> None:
    repository = RankingV4EvidenceRepository(session_factory)
    snapshot = repository.load_snapshot(result.owner_run_id)
    if snapshot is None:
        return
    if not walk_forward_selection_result_digest_is_valid(result):
        raise RuntimeError("walk-forward result digest is invalid")
    if result.ranking_v4 is None:
        raise RuntimeError("registered forward result has no Ranking V4 evidence")
    manifest = result.experiment_manifest
    identity = snapshot.definition.identity
    if manifest.code_dirty:
        raise RuntimeError("dirty code cannot enter the prospective evidence ledger")
    if set(manifest.runtime_revisions) != {identity.code_revision}:
        raise RuntimeError(
            "prospective evidence runtime revisions differ from the frozen code"
        )
    recorded_at = datetime.now(timezone.utc)
    repository.append_trial_ledger(
        identity.epoch_id,
        attempt_id=result.owner_run_id,
        code_revision=manifest.code_revision,
        protocol_digest=manifest.ranking_v4_protocol_digest,
        experiment_registry_digest=(
            manifest.ranking_v4_experiment_registry_digest
        ),
        dataset_revision=result.dataset_revision,
        execution_start_date=result.start_date,
        source_result_digest=result.reproducibility_digest,
        trial_ledger=result.ranking_v4.trial_ledger,
        recorded_at=recorded_at,
    )
    updated_snapshot = repository.load_snapshot(identity.epoch_id)
    if updated_snapshot is None or not updated_snapshot.return_records:
        raise RuntimeError("prospective evidence disappeared after append")
    release_repository = RankingV4ProspectiveReleaseRepository(
        session_factory,
        attestor=repository.attestor,
    )
    policy = release_repository.load_policy(snapshot.definition.definition_digest)
    if policy is None:
        # Superseded zero-return epochs deliberately have no promotion policy.
        return
    summaries = release_repository.load_execution_summaries(
        snapshot.definition.definition_digest
    )
    summary = next(
        (
            item
            for item in summaries
            if item.source_result_digest == result.reproducibility_digest
        ),
        None,
    )
    if summary is None:
        historical = result.ranking_v4.historical_validation
        raw_evidence = _ranking_v4_raw_execution_evidence(result)
        summary = release_repository.append_execution_summary(
            build_prospective_execution_summary(
                definition_digest=snapshot.definition.definition_digest,
                policy_digest=policy.policy_digest,
                sequence=len(summaries) + 1,
                source_result_digest=result.reproducibility_digest,
                dataset_revision=result.dataset_revision,
                execution_start_date=result.start_date,
                execution_end_date=result.end_date,
                latest_mature_rebalance_date=(
                    updated_snapshot.return_records[-1].rebalance_date
                ),
                common_date_count=len(updated_snapshot.return_records),
                completed_trade_count=historical.completed_trade_count,
                valid_outcome_count=historical.valid_outcome_count,
                expected_outcome_count=historical.expected_outcome_count,
                maximum_drawdown_pct=Decimal(
                    str(result.ranking_v4.metrics.max_drawdown_pct)
                ),
                previous_summary_digest=(
                    summaries[-1].summary_digest if summaries else None
                ),
                recorded_at=datetime.now(timezone.utc),
                attestor=release_repository.attestor,
                **raw_evidence,
            )
        )
    release_proofs = release_repository.load_release_proofs(
        snapshot.definition.definition_digest
    )
    next_checkpoint_index = len(release_proofs)
    next_checkpoint = (
        REGISTERED_CHECKPOINTS[next_checkpoint_index]
        if next_checkpoint_index < len(REGISTERED_CHECKPOINTS)
        else None
    )
    if next_checkpoint is not None and summary.common_date_count > next_checkpoint:
        raise RuntimeError(
            "prospective evidence skipped a preregistered release checkpoint"
        )
    if summary.common_date_count == next_checkpoint:
        release_repository.evaluate_checkpoint(
            identity.epoch_id,
            evaluated_at=datetime.now(timezone.utc),
        )


def _ranking_v4_raw_execution_evidence(
    result: WalkForwardSelectionResult,
) -> dict[str, str]:
    ranking_v4 = result.ranking_v4
    if ranking_v4 is None:
        raise RuntimeError("Ranking V4 execution evidence is missing")
    historical = ranking_v4.historical_validation
    return {
        "completed_trade_evidence_digest": stable_digest(
            {
                "source_result_digest": result.reproducibility_digest,
                "trades": [
                    item.model_dump(mode="json")
                    for item in ranking_v4.portfolio.trades
                ],
            }
        ),
        "outcome_coverage_evidence_digest": stable_digest(
            {
                "source_result_digest": result.reproducibility_digest,
                "valid_outcome_count": historical.valid_outcome_count,
                "expected_outcome_count": historical.expected_outcome_count,
                "evidence_coverage": ranking_v4.evidence_coverage,
                "snapshots": [
                    {
                        "decision_date": item.decision_date.isoformat(),
                        "ranking_v4_top_5": [
                            selection.model_dump(mode="json")
                            for selection in item.ranking_v4_top_5
                        ],
                    }
                    for item in result.snapshots
                ],
            }
        ),
        "cost_evidence_digest": stable_digest(
            {
                "source_result_digest": result.reproducibility_digest,
                "protocol": ranking_v4.protocol.model_dump(mode="json"),
                "normal_metrics": ranking_v4.metrics.model_dump(mode="json"),
                "stress_metrics": ranking_v4.stress_metrics.model_dump(mode="json"),
                "normal_trade_costs": [
                    str(item.costs) for item in ranking_v4.portfolio.trades
                ],
            }
        ),
        "benchmark_evidence_digest": stable_digest(
            {
                "source_result_digest": result.reproducibility_digest,
                "portfolio": (
                    ranking_v4.constraint_matched_baseline_portfolio.model_dump(
                        mode="json"
                    )
                ),
                "metrics": (
                    ranking_v4.constraint_matched_baseline_metrics.model_dump(
                        mode="json"
                    )
                ),
            }
        ),
        "capital_constraint_evidence_digest": stable_digest(
            {
                "source_result_digest": result.reproducibility_digest,
                "portfolio": ranking_v4.portfolio.model_dump(mode="json"),
                "completed_trade_count": historical.completed_trade_count,
            }
        ),
    }


def _ranking_v4_evidence_freeze_command(args: argparse.Namespace) -> int:
    initialize_database()
    session_factory = create_session_factory()
    replay = ReplayEvidenceRepository(session_factory, args.provider)
    frozen_at = datetime.now(timezone.utc)
    repository = RankingV4EvidenceRepository(session_factory)
    snapshot = repository.load_snapshot(args.epoch_id)
    created_definition = snapshot is None
    if snapshot is not None:
        definition = snapshot.definition
        if args.code_revision != definition.identity.code_revision:
            raise RuntimeError("epoch is frozen to a different code revision")
        if (
            args.dataset_revision is not None
            and args.dataset_revision != definition.identity.dataset_revision
        ):
            raise RuntimeError("epoch is frozen to a different dataset revision")
        if (
            args.evidence_start is not None
            and args.evidence_start != definition.identity.evidence_start_date
        ):
            raise RuntimeError("epoch is frozen to a different evidence start date")
    else:
        dataset_revision = (
            args.dataset_revision
            if args.dataset_revision is not None
            else replay.current_revision()
        )
        freeze_market_date = frozen_at.astimezone(
            ZoneInfo("Asia/Shanghai")
        ).date()
        evidence_start = args.evidence_start or trading_day_offset(
            freeze_market_date,
            1,
        )
        definition = repository.freeze_definition(
            build_prospective_definition(
                epoch_id=args.epoch_id,
                code_revision=args.code_revision,
                dataset_revision=dataset_revision,
                evidence_start_date=evidence_start,
                frozen_at=frozen_at,
                attestor=repository.attestor,
            )
        )
        snapshot = repository.load_snapshot(args.epoch_id)
        if snapshot is None:
            raise RuntimeError("frozen Ranking V4 evidence definition was not persisted")
    release_repository = RankingV4ProspectiveReleaseRepository(
        session_factory,
        attestor=repository.attestor,
    )
    if created_definition:
        release_repository.register_policy(
            build_prospective_release_policy(
                definition_digest=definition.definition_digest,
                model_protocol_digest=definition.identity.protocol_digest,
                experiment_registry_digest=(
                    definition.identity.experiment_registry_digest
                ),
                registered_at=frozen_at,
                attestor=repository.attestor,
            )
        )
    if not snapshot.inventories:
        market_date = frozen_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if market_date >= definition.identity.evidence_start_date:
            raise RuntimeError(
                "attempt inventory was not signed before the prospective epoch started"
            )
        prior_attempts = tuple(
            attempt_id
            for attempt_id in replay.walk_forward_research_attempt_ids()
            if attempt_id != args.epoch_id
        )
        repository.append_inventory(
            build_attempt_inventory_snapshot(
                definition=definition,
                sequence=1,
                as_of_date=market_date,
                pre_epoch_unverifiable_attempt_ids=prior_attempts,
                prospective_attempts={args.epoch_id: definition.definition_digest},
                previous_inventory_digest=None,
                recorded_at=frozen_at,
                attestor=repository.attestor,
            )
        )
    proof = repository.create_proof(args.epoch_id, generated_at=frozen_at)
    print(
        f"ranking-v4-evidence epoch={args.epoch_id} "
        f"start={definition.identity.evidence_start_date.isoformat()} "
        f"dataset_revision={definition.identity.dataset_revision} "
        f"definition={definition.definition_digest} proof={proof.proof_digest} "
        "scope=shadow_only"
    )
    return 0


def _ranking_v4_evidence_status_command(args: argparse.Namespace) -> int:
    initialize_database()
    snapshot = RankingV4EvidenceRepository(
        create_session_factory()
    ).load_snapshot(args.epoch_id)
    if snapshot is None:
        print(f"ranking-v4-evidence epoch={args.epoch_id} status=missing")
        return 1
    release_repository = RankingV4ProspectiveReleaseRepository(
        create_session_factory()
    )
    definition_digest = snapshot.definition.definition_digest
    policy = release_repository.load_policy(definition_digest)
    summaries = release_repository.load_execution_summaries(definition_digest)
    release_proofs = release_repository.load_release_proofs(definition_digest)
    latest_release = release_proofs[-1] if release_proofs else None
    print(
        f"ranking-v4-evidence epoch={args.epoch_id} status=frozen "
        f"start={snapshot.definition.identity.evidence_start_date.isoformat()} "
        f"inventories={len(snapshot.inventories)} "
        f"common_dates={len(snapshot.return_records)} proofs={len(snapshot.proofs)} "
        f"policy={'registered' if policy else 'none'} "
        f"execution_summaries={len(summaries)} "
        f"release_evaluations={len(release_proofs)} "
        f"scope={latest_release.release_scope if latest_release else 'shadow_only'} "
        f"official_release_allowed="
        f"{latest_release.official_release_allowed if latest_release else False}"
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
