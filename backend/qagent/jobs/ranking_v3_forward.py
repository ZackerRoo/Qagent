from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol

import pandas as pd

from qagent.backtesting.engine import BacktestSignal
from qagent.backtesting.execution import VersionedAshareExecutionResolver
from qagent.backtesting.portfolio import (
    CandidateOutcomeStatus,
    CandidateSignalOutcome,
    resolve_candidate_outcome_ledger,
    run_signal_portfolio_backtest,
)
from qagent.backtesting.ranking_v3 import RankingV3FeatureVector
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardEquityPoint,
    RankingV3ForwardIdentity,
    RankingV3ForwardPortfolioInput,
    RankingV3ShadowCandidate,
    forward_candidate_source_digest,
    stable_digest,
)
from qagent.backtesting.ranking_v3_forward_runtime import (
    RankingV3CandidateSnapshotRequest,
    RankingV3ForwardResolutionRequest,
    RankingV3ProductionForwardFactAuthority,
    RankingV3ResolvedForwardDay,
    RankingV3ServerCandidateRecord,
    RankingV3ServerCandidateSnapshot,
    RankingV3ServerForwardResolver,
)
from qagent.backtesting.ranking_v3_forward_service import (
    RankingV3ForwardDayResult,
    RankingV3ForwardOutcomeFact,
    RankingV3ForwardService,
)
from qagent.backtesting.ranking_v3_protocol import RankingV3Protocol
from qagent.domain.enums import Market
from qagent.domain.models import OpportunityCard
from qagent.market.calendars import trading_sessions_in_range
from qagent.providers.base import MarketDataProvider
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository
from qagent.storage.repository import OpportunitySnapshotRecord, QagentRepository


_INITIAL_EQUITY = Decimal("100000")
_NORMAL_TRANSACTION_COST_BPS = Decimal("5")
_REQUIRED_FACTOR_IDS = (
    "valuation",
    "size",
    "quality",
    "momentum",
    "trend_quality",
    "liquidity",
    "low_risk",
    "risk_filter",
    "reversal",
)
_EXECUTION_FLAG_PENALTIES = {
    "low_liquidity": Decimal("0.10"),
    "high_volatility": Decimal("0.08"),
    "deep_drawdown_risk": Decimal("0.08"),
    "volume_spike_overheat": Decimal("0.06"),
    "shell_size_risk": Decimal("0.12"),
    "overextended": Decimal("0.08"),
}


class RankingV3OpportunityRepository(Protocol):
    def list_top_daily_opportunity_snapshots(
        self,
        *,
        start: date,
        end: date,
        top_n: int = 5,
        provider: str | None = None,
    ) -> list[OpportunitySnapshotRecord]: ...

    def get_opportunity_snapshot(
        self,
        snapshot_id: str,
    ) -> OpportunitySnapshotRecord | None: ...

    def opportunity_snapshots_belong_to_provider(
        self,
        snapshot_ids: Sequence[str],
        *,
        provider: str,
    ) -> bool: ...

    def get_walk_forward_run(self, run_id: str) -> object | None: ...


class QagentRankingV3CandidateLoader:
    """Load one immutable, server-owned A-share opportunity cross-section."""

    def __init__(
        self,
        repository: RankingV3OpportunityRepository,
        forward_store: RankingV3ForwardRepository | None = None,
    ):
        self.repository = repository
        self.forward_store = forward_store

    def load_candidate_snapshot(
        self,
        request: RankingV3CandidateSnapshotRequest,
    ) -> RankingV3ServerCandidateSnapshot:
        protocol = self._load_protocol(request)
        benchmark_id = protocol.benchmark_definition.forward_release_benchmark_id
        if not self._collection_accepts_candidates(request, protocol):
            return RankingV3ServerCandidateSnapshot.create(
                request=request,
                benchmark_id=benchmark_id,
                candidates=(),
            )
        snapshots = self.repository.list_top_daily_opportunity_snapshots(
            start=request.session_date,
            end=request.session_date,
            top_n=protocol.candidate_pool_limit,
            provider="free",
        )
        snapshot_ids = [item.snapshot_id for item in snapshots]
        if len(set(snapshot_ids)) != len(snapshot_ids):
            raise ValueError("candidate repository returned a duplicate source snapshot")
        if not self.repository.opportunity_snapshots_belong_to_provider(
            snapshot_ids,
            provider="free",
        ):
            raise ValueError("candidate repository returned a snapshot outside free scan runs")
        candidates: list[RankingV3ServerCandidateRecord] = []
        seen_snapshot_ids: set[str] = set()
        seen_instruments: set[str] = set()
        for snapshot in snapshots:
            if snapshot.signal_date != request.session_date:
                raise ValueError("candidate repository returned a non-requested session snapshot")
            if snapshot.snapshot_id in seen_snapshot_ids:
                raise ValueError("candidate repository returned a duplicate source snapshot")
            if snapshot.instrument_id in seen_instruments:
                raise ValueError("candidate repository returned duplicate instruments")
            seen_snapshot_ids.add(snapshot.snapshot_id)
            seen_instruments.add(snapshot.instrument_id)
            candidate = _candidate_from_snapshot(snapshot)
            if candidate is not None:
                candidates.append(candidate)
        return RankingV3ServerCandidateSnapshot.create(
            request=request,
            benchmark_id=benchmark_id,
            candidates=candidates,
        )

    def _collection_accepts_candidates(
        self,
        request: RankingV3CandidateSnapshotRequest,
        protocol: RankingV3Protocol,
    ) -> bool:
        if self.forward_store is None:
            return True
        ledger = self.forward_store.load_snapshot(RankingV3ForwardIdentity.from_protocol(protocol))
        if ledger is None:
            return True
        sessions = sorted(ledger.sessions, key=lambda item: item.session_date)
        minimum = protocol.thresholds.minimum_forward_shadow_sessions
        if len(sessions) < minimum:
            return True
        collection_end = sessions[minimum - 1].session_date
        return request.session_date <= collection_end

    def _load_protocol(
        self,
        request: RankingV3CandidateSnapshotRequest,
    ) -> RankingV3Protocol:
        run = self.repository.get_walk_forward_run(request.validation_run_id)
        if run is None:
            raise LookupError("authoritative walk-forward run does not exist")
        if str(getattr(run, "provider", "")).strip().lower() != "free":
            raise ValueError("Ranking V3 forward candidates require the free provider run")
        payload = getattr(run, "payload", None)
        ranking = payload.get("ranking_v3") if isinstance(payload, Mapping) else None
        protocol_payload = ranking.get("protocol") if isinstance(ranking, Mapping) else None
        if not isinstance(protocol_payload, Mapping):
            raise ValueError("walk-forward run has no frozen Ranking V3 protocol")
        protocol = RankingV3Protocol.model_validate(protocol_payload)
        bindings = (
            (protocol.protocol_id, request.protocol_id),
            (protocol.protocol_digest, request.protocol_digest),
            (protocol.model_version, request.model_version),
        )
        if any(actual != expected for actual, expected in bindings):
            raise ValueError("candidate request does not match the frozen protocol")
        return protocol


class QagentRankingV3MarketResolver(RankingV3ServerForwardResolver):
    """Resolve mature forward candidates from the authoritative store and bars."""

    def __init__(
        self,
        repository: RankingV3OpportunityRepository,
        forward_store: RankingV3ForwardRepository,
        provider: MarketDataProvider,
    ):
        self.repository = repository
        self.forward_store = forward_store
        self.provider = provider

    def resolve_forward_day(
        self,
        request: RankingV3ForwardResolutionRequest,
    ) -> RankingV3ResolvedForwardDay:
        protocol = self._load_protocol(request)
        if request.benchmark_id != protocol.benchmark_definition.forward_release_benchmark_id:
            raise ValueError("forward request benchmark does not match the frozen protocol")
        execution_resolver = self._execution_resolver(request)
        identity = RankingV3ForwardIdentity.from_protocol(protocol)
        ledger = self.forward_store.load_snapshot(identity)
        existing_session = next(
            (
                item
                for item in (ledger.sessions if ledger is not None else ())
                if item.session_date == request.session_date
            ),
            None,
        )
        previous_session = _previous_session(
            ledger.sessions if ledger is not None else (), request.session_date
        )
        candidates = ledger.candidates if ledger is not None else []
        pending = [
            item
            for item in candidates
            if item.outcome_status == "pending"
            and item.maturity_session_date <= request.session_date
        ]
        replayed = [
            item
            for item in candidates
            if item.outcome_status != "pending" and item.resolved_on == request.session_date
        ]
        mature_outcomes = tuple(
            self._resolve_candidate(
                item,
                protocol,
                request.session_date,
                request.benchmark_id,
                execution_resolver,
            )
            for item in sorted(pending, key=lambda value: value.candidate_id)
        )
        if existing_session is not None:
            benchmark_return = existing_session.benchmark_return_pct
            portfolio_equity = existing_session.portfolio_equity
            stress_portfolio_equity = existing_session.stress_portfolio_equity
            benchmark_equity = existing_session.benchmark_equity
        else:
            benchmark_return = self._benchmark_return(
                previous_session.session_date if previous_session is not None else None,
                request.session_date,
                request.benchmark_id,
            )
            portfolio_equity = (
                previous_session.portfolio_equity
                if previous_session is not None
                else _INITIAL_EQUITY
            )
            stress_portfolio_equity = (
                previous_session.stress_portfolio_equity
                if previous_session is not None
                else _INITIAL_EQUITY
            )
            prior_benchmark = (
                previous_session.benchmark_equity
                if previous_session is not None
                else _INITIAL_EQUITY
            )
            benchmark_equity = _apply_return(prior_benchmark, benchmark_return)

        portfolio_evidence = self._stored_portfolio_evidence(
            ledger,
            request.session_date,
        )
        if portfolio_evidence is None and self._portfolio_is_ready(
            ledger,
            request,
            protocol,
            mature_outcomes,
        ):
            portfolio_evidence = self._build_portfolio_evidence(
                request,
                protocol,
                ledger,
                execution_resolver,
            )
            portfolio_equity = portfolio_evidence.final_equity
            stress_portfolio_equity = portfolio_evidence.stress_final_equity
            benchmark_equity = portfolio_evidence.benchmark_final_equity

        replayed_facts = tuple(_outcome_fact_from_stored(item) for item in replayed)
        resolved_by_id = {item.candidate_id: item for item in replayed_facts}
        resolved_by_id.update({item.candidate_id: item for item in mature_outcomes})
        return RankingV3ResolvedForwardDay.create(
            request=request,
            benchmark_return_pct=benchmark_return,
            portfolio_equity=portfolio_equity,
            stress_portfolio_equity=stress_portfolio_equity,
            benchmark_equity=benchmark_equity,
            mature_outcomes=tuple(resolved_by_id[key] for key in sorted(resolved_by_id)),
            portfolio_evidence=portfolio_evidence,
        )

    def recompute_portfolio_evidence(
        self,
        request: RankingV3ForwardResolutionRequest,
        ledger,
    ) -> RankingV3ForwardPortfolioInput:
        protocol = self._load_protocol(request)
        expected_benchmark = protocol.benchmark_definition.forward_release_benchmark_id
        if request.benchmark_id != expected_benchmark:
            raise ValueError(
                "portfolio recomputation benchmark does not match the frozen protocol"
            )
        if (
            ledger.ledger.identity != RankingV3ForwardIdentity.from_protocol(protocol)
            or ledger.ledger.data_revision != request.data_revision
        ):
            raise ValueError("portfolio recomputation ledger identity is mismatched")
        return self._build_portfolio_evidence(
            request,
            protocol,
            ledger,
            self._execution_resolver(request),
        )

    def _stored_portfolio_evidence(
        self,
        ledger,
        session_date: date,
    ) -> RankingV3ForwardPortfolioInput | None:
        if ledger is None:
            return None
        matching = [
            item
            for item in ledger.evidence
            if item.evidence_kind == "portfolio"
            and item.payload.get("as_of_session_date") == session_date.isoformat()
        ]
        if not matching:
            return None
        latest = max(matching, key=lambda item: item.sequence)
        return RankingV3ForwardPortfolioInput.model_validate(latest.payload)

    @staticmethod
    def _portfolio_is_ready(
        ledger,
        request: RankingV3ForwardResolutionRequest,
        protocol: RankingV3Protocol,
        mature_outcomes: Sequence[RankingV3ForwardOutcomeFact],
    ) -> bool:
        if ledger is None:
            return False
        sessions = sorted(ledger.sessions, key=lambda item: item.session_date)
        minimum = protocol.thresholds.minimum_forward_shadow_sessions
        if len(sessions) < minimum:
            return False
        session_count_after = len(sessions) + (
            0 if any(item.session_date == request.session_date for item in sessions) else 1
        )
        required_window = (
            minimum
            + protocol.statistics_definition.entry_wait_sessions
            + protocol.statistics_definition.holding_sessions
        )
        if session_count_after < required_window:
            return False
        collection_end = sessions[minimum - 1].session_date
        if request.session_date <= collection_end:
            return False
        resolved_ids = {item.candidate_id for item in mature_outcomes}
        pending_after = [
            item
            for item in ledger.candidates
            if item.outcome_status == "pending" and item.candidate_id not in resolved_ids
        ]
        return bool(ledger.candidates) and not pending_after and not request.selected_candidates

    def _build_portfolio_evidence(
        self,
        request: RankingV3ForwardResolutionRequest,
        protocol: RankingV3Protocol,
        ledger,
        execution_resolver: VersionedAshareExecutionResolver,
    ) -> RankingV3ForwardPortfolioInput:
        signals: list[BacktestSignal] = []
        for candidate in sorted(
            ledger.candidates,
            key=lambda item: (item.session_date, item.rank, item.candidate_id),
        ):
            snapshot = self.repository.get_opportunity_snapshot(candidate.source_snapshot_id)
            if snapshot is None:
                raise LookupError(
                    f"source opportunity snapshot {candidate.source_snapshot_id!r} does not exist"
                )
            if (
                snapshot.instrument_id != candidate.instrument_id
                or snapshot.signal_date != candidate.session_date
            ):
                raise ValueError("forward portfolio source snapshot does not match its candidate")
            signal = _backtest_signal(snapshot).model_copy(update={"rank_score": candidate.score})
            signals.append(signal)
        if not signals:
            raise ValueError("forward portfolio evidence requires selected candidates")

        run = self.repository.get_walk_forward_run(request.validation_run_id)
        if run is None:
            raise LookupError("authoritative walk-forward run does not exist")
        base_scenario = next(
            (item for item in protocol.cost_definition.sensitivity_scenarios if item.key == "base"),
            None,
        )
        if base_scenario is None:
            raise ValueError("frozen Ranking V3 protocol has no base cost scenario")
        stress_scenario = protocol.cost_definition.audit_stress
        start = min(item.session_date for item in ledger.sessions)
        end = request.session_date
        instrument_ids = sorted({item.instrument_id for item in signals})
        common = {
            "signals": signals,
            "instrument_ids": instrument_ids,
            "provider": self.provider,
            "start": start,
            "end": end,
            "initial_capital": _INITIAL_EQUITY,
            "max_positions": protocol.max_positions,
            "transaction_cost_bps": _NORMAL_TRANSACTION_COST_BPS,
            "max_entry_wait_days": protocol.statistics_definition.entry_wait_sessions,
            "max_holding_days": protocol.statistics_definition.holding_sessions,
            "execution_rule_resolver": execution_resolver,
        }
        portfolio = run_signal_portfolio_backtest(
            **common,
            slippage_bps=Decimal(base_scenario.slippage_bps),
            fee_multiplier=Decimal(base_scenario.fee_multiplier),
        )
        stress = run_signal_portfolio_backtest(
            **common,
            slippage_bps=Decimal(stress_scenario.slippage_bps),
            fee_multiplier=Decimal(stress_scenario.fee_multiplier),
        )
        benchmark_return = self._benchmark_interval_return(
            start,
            end,
            request.benchmark_id,
        )
        if benchmark_return is None:
            raise ValueError("authoritative CSI 300 full-period return is unavailable")
        benchmark_final_equity = _apply_return(_INITIAL_EQUITY, benchmark_return)
        net_return = _pct(portfolio.summary.final_equity / _INITIAL_EQUITY - Decimal("1"))
        stress_return = _pct(stress.summary.final_equity / _INITIAL_EQUITY - Decimal("1"))
        equity_curve = _canonical_equity_curve(portfolio.equity_curve)
        stress_equity_curve = _canonical_equity_curve(stress.equity_curve)
        if not equity_curve or not stress_equity_curve:
            raise ValueError("forward portfolio evidence requires complete equity curves")
        return RankingV3ForwardPortfolioInput(
            validation_run_id=request.validation_run_id,
            data_revision=request.data_revision,
            as_of_session_date=request.session_date,
            benchmark_id=request.benchmark_id,
            provider=self.provider.name,
            execution_profile=(
                f"{portfolio.data_health.get('execution_profile', 'default')}:"
                f"dataset-revision-{getattr(run, 'dataset_revision')}"
            ),
            initial_equity=_INITIAL_EQUITY,
            final_equity=portfolio.summary.final_equity,
            stress_final_equity=stress.summary.final_equity,
            benchmark_final_equity=benchmark_final_equity,
            net_return_pct=net_return,
            stress_net_return_pct=stress_return,
            benchmark_return_pct=benchmark_return,
            benchmark_excess_pct=net_return - benchmark_return,
            stress_benchmark_excess_pct=stress_return - benchmark_return,
            maximum_drawdown_pct=min(item.drawdown_pct for item in equity_curve),
            stress_maximum_drawdown_pct=min(item.drawdown_pct for item in stress_equity_curve),
            completed_trade_count=min(
                portfolio.summary.trade_count,
                stress.summary.trade_count,
            ),
            equity_curve=equity_curve,
            stress_equity_curve=stress_equity_curve,
            equity_curve_digest=stable_digest(
                [item.model_dump(mode="json") for item in equity_curve]
            ),
            stress_equity_curve_digest=stable_digest(
                [item.model_dump(mode="json") for item in stress_equity_curve]
            ),
            final_open_positions=equity_curve[-1].open_positions,
            stress_final_open_positions=stress_equity_curve[-1].open_positions,
            source_candidate_digest=forward_candidate_source_digest(ledger.candidates),
        )

    def _load_protocol(
        self,
        request: RankingV3ForwardResolutionRequest,
    ) -> RankingV3Protocol:
        run = self.repository.get_walk_forward_run(request.validation_run_id)
        if run is None:
            raise LookupError("authoritative walk-forward run does not exist")
        payload = getattr(run, "payload", None)
        ranking = payload.get("ranking_v3") if isinstance(payload, Mapping) else None
        protocol_payload = ranking.get("protocol") if isinstance(ranking, Mapping) else None
        if not isinstance(protocol_payload, Mapping):
            raise ValueError("walk-forward run has no frozen Ranking V3 protocol")
        protocol = RankingV3Protocol.model_validate(protocol_payload)
        bindings = (
            (protocol.protocol_id, request.protocol_id),
            (protocol.protocol_digest, request.protocol_digest),
            (protocol.model_version, request.model_version),
        )
        if any(actual != expected for actual, expected in bindings):
            raise ValueError("forward resolution request does not match the frozen protocol")
        return protocol

    def _resolve_candidate(
        self,
        candidate: RankingV3ShadowCandidate,
        protocol: RankingV3Protocol,
        session_date: date,
        benchmark_id: str,
        execution_resolver: VersionedAshareExecutionResolver,
    ) -> RankingV3ForwardOutcomeFact:
        snapshot = self.repository.get_opportunity_snapshot(candidate.source_snapshot_id)
        if snapshot is None:
            raise LookupError(
                f"source opportunity snapshot {candidate.source_snapshot_id!r} does not exist"
            )
        if (
            snapshot.snapshot_id != candidate.source_snapshot_id
            or snapshot.instrument_id != candidate.instrument_id
            or snapshot.signal_date != candidate.session_date
        ):
            raise ValueError("source opportunity snapshot does not match the forward candidate")
        signal = _backtest_signal(snapshot)
        calendar_error = self._candidate_calendar_error(signal, session_date)
        if calendar_error is not None:
            return RankingV3ForwardOutcomeFact(
                candidate_id=candidate.candidate_id,
                status="censored",
                reason=calendar_error,
            )
        base_scenario = next(
            (item for item in protocol.cost_definition.sensitivity_scenarios if item.key == "base"),
            None,
        )
        if base_scenario is None:
            raise ValueError("frozen Ranking V3 protocol has no base cost scenario")
        stress_scenario = protocol.cost_definition.audit_stress
        raw = self._resolve_signal(
            signal,
            session_date=session_date,
            entry_wait_sessions=protocol.statistics_definition.entry_wait_sessions,
            holding_sessions=protocol.statistics_definition.holding_sessions,
            slippage_bps=Decimal("0"),
            fee_multiplier=Decimal("1"),
            transaction_cost_bps=Decimal("0"),
            execution_resolver=execution_resolver,
        )
        base = self._resolve_signal(
            signal,
            session_date=session_date,
            entry_wait_sessions=protocol.statistics_definition.entry_wait_sessions,
            holding_sessions=protocol.statistics_definition.holding_sessions,
            slippage_bps=Decimal(base_scenario.slippage_bps),
            fee_multiplier=Decimal(base_scenario.fee_multiplier),
            transaction_cost_bps=_NORMAL_TRANSACTION_COST_BPS,
            execution_resolver=execution_resolver,
        )
        stress = self._resolve_signal(
            signal,
            session_date=session_date,
            entry_wait_sessions=protocol.statistics_definition.entry_wait_sessions,
            holding_sessions=protocol.statistics_definition.holding_sessions,
            slippage_bps=Decimal(stress_scenario.slippage_bps),
            fee_multiplier=Decimal(stress_scenario.fee_multiplier),
            transaction_cost_bps=_NORMAL_TRANSACTION_COST_BPS,
            execution_resolver=execution_resolver,
        )
        opportunity_benchmark_return = self._benchmark_interval_return(
            snapshot.signal_date,
            session_date,
            benchmark_id,
        )
        completed_benchmark_return = (
            self._benchmark_interval_return(
                base.entry_date,
                base.exit_date or session_date,
                benchmark_id,
            )
            if base.status == CandidateOutcomeStatus.RESOLVED
            else None
        )
        return self._outcome_fact(
            candidate,
            raw=raw,
            base=base,
            stress=stress,
            benchmark_return=completed_benchmark_return,
            opportunity_benchmark_return=opportunity_benchmark_return,
            session_date=session_date,
        )

    def _resolve_signal(
        self,
        signal: BacktestSignal,
        *,
        session_date: date,
        entry_wait_sessions: int,
        holding_sessions: int,
        slippage_bps: Decimal,
        fee_multiplier: Decimal,
        transaction_cost_bps: Decimal,
        execution_resolver: VersionedAshareExecutionResolver,
    ) -> CandidateSignalOutcome:
        result = resolve_candidate_outcome_ledger(
            signals=[signal],
            provider=self.provider,
            start=signal.signal_date,
            end=session_date,
            nominal_amount=_INITIAL_EQUITY,
            transaction_cost_bps=transaction_cost_bps,
            slippage_bps=slippage_bps,
            fee_multiplier=fee_multiplier,
            max_entry_wait_days=entry_wait_sessions,
            max_holding_days=holding_sessions,
            execution_rule_resolver=execution_resolver,
        )
        if len(result.outcomes) != 1:
            raise RuntimeError("candidate outcome resolver returned an unexpected result count")
        return result.outcomes[0]

    def _outcome_fact(
        self,
        candidate: RankingV3ShadowCandidate,
        *,
        raw: CandidateSignalOutcome,
        base: CandidateSignalOutcome,
        stress: CandidateSignalOutcome,
        benchmark_return: Decimal | None,
        opportunity_benchmark_return: Decimal | None,
        session_date: date,
    ) -> RankingV3ForwardOutcomeFact:
        if base.status == CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE:
            if (
                base.status_detail == "entry_not_triggered"
                and opportunity_benchmark_return is not None
            ):
                return RankingV3ForwardOutcomeFact(
                    candidate_id=candidate.candidate_id,
                    status="not_triggered",
                    benchmark_return_pct=opportunity_benchmark_return,
                    reason=base.status_detail,
                )
            return RankingV3ForwardOutcomeFact(
                candidate_id=candidate.candidate_id,
                status="invalid",
                reason=base.status_detail,
            )
        if base.status == CandidateOutcomeStatus.INSUFFICIENT_FUTURE_DATA:
            return RankingV3ForwardOutcomeFact(
                candidate_id=candidate.candidate_id,
                status="censored",
                reason=base.status_detail,
            )
        if base.status != CandidateOutcomeStatus.RESOLVED:
            return RankingV3ForwardOutcomeFact(
                candidate_id=candidate.candidate_id,
                status="invalid",
                reason=base.status_detail,
            )
        if (
            raw.status != CandidateOutcomeStatus.RESOLVED
            or stress.status != CandidateOutcomeStatus.RESOLVED
            or benchmark_return is None
            or raw.entry_value is None
            or raw.gross_pnl is None
            or base.return_pct is None
            or stress.return_pct is None
        ):
            return RankingV3ForwardOutcomeFact(
                candidate_id=candidate.candidate_id,
                status="censored",
                reason="cost_or_benchmark_resolution_incomplete",
            )
        gross_return = _pct(raw.gross_pnl / raw.entry_value)
        base_return = Decimal(str(base.return_pct))
        stress_return = Decimal(str(stress.return_pct))
        transaction_cost = max(gross_return - base_return, Decimal("0"))
        stress_cost = max(gross_return - stress_return, transaction_cost)
        max_drawdown = self._candidate_drawdown(base, session_date)
        if max_drawdown is None:
            return RankingV3ForwardOutcomeFact(
                candidate_id=candidate.candidate_id,
                status="censored",
                reason="candidate_drawdown_bars_missing",
            )
        return RankingV3ForwardOutcomeFact(
            candidate_id=candidate.candidate_id,
            status="completed",
            gross_return_pct=gross_return,
            transaction_cost_pct=transaction_cost,
            stress_transaction_cost_pct=stress_cost,
            benchmark_return_pct=benchmark_return,
            max_drawdown_pct=max_drawdown,
            reason=base.exit_reason or "resolved",
        )

    def _candidate_drawdown(
        self,
        outcome: CandidateSignalOutcome,
        session_date: date,
    ) -> Decimal | None:
        if outcome.entry_date is None or outcome.entry_price is None:
            return None
        end = outcome.exit_date or session_date
        bars = self.provider.get_daily_bars(
            [outcome.instrument_id],
            start=outcome.entry_date,
            end=end,
        )
        frame = _normalized_bars(bars, outcome.instrument_id)
        if frame.empty or "low" not in frame.columns:
            return None
        lows = [Decimal(str(value)) for value in frame["low"].tolist()]
        if not lows or outcome.entry_price <= 0:
            return None
        return min(
            Decimal("0"),
            _pct(min(lows) / outcome.entry_price - Decimal("1")),
        )

    def _benchmark_interval_return(
        self,
        start: date | None,
        end: date,
        benchmark_id: str,
    ) -> Decimal | None:
        if start is None:
            return None
        bars = self.provider.get_daily_bars([benchmark_id], start=start, end=end)
        frame = _normalized_bars(bars, benchmark_id)
        return _exact_close_return(frame, start, end)

    def _benchmark_return(
        self,
        previous_session_date: date | None,
        session_date: date,
        benchmark_id: str,
    ) -> Decimal:
        if previous_session_date is None:
            return Decimal("0")
        value = self._benchmark_interval_return(
            previous_session_date,
            session_date,
            benchmark_id,
        )
        if value is None:
            raise ValueError("authoritative CSI 300 session return is unavailable")
        return value

    def _execution_resolver(
        self,
        request: RankingV3ForwardResolutionRequest,
    ) -> VersionedAshareExecutionResolver:
        run = self.repository.get_walk_forward_run(request.validation_run_id)
        if run is None:
            raise LookupError("authoritative walk-forward run does not exist")
        return VersionedAshareExecutionResolver(
            self.repository,  # type: ignore[arg-type]
            dataset_revision=int(getattr(run, "dataset_revision")),
        )

    def _candidate_calendar_error(
        self,
        signal: BacktestSignal,
        session_date: date,
    ) -> str | None:
        try:
            expected = trading_sessions_in_range(
                signal.signal_date,
                session_date,
                market=Market.CN,
            )
        except Exception:
            return "authoritative_trading_calendar_unavailable"
        expected = [item for item in expected if item > signal.signal_date]
        if not expected:
            return "authoritative_trading_calendar_unavailable"
        bars = self.provider.get_daily_bars(
            [signal.instrument_id],
            start=signal.signal_date,
            end=session_date,
        )
        frame = _normalized_bars(bars, signal.instrument_id)
        observed = set(frame["trade_date"].tolist()) if not frame.empty else set()
        if any(item not in observed for item in expected):
            return "authoritative_trading_calendar_rows_missing"
        return None


def run_ranking_v3_forward_day(
    repo: QagentRepository,
    provider: MarketDataProvider,
    run_id: str,
    session_date: date,
) -> RankingV3ForwardDayResult:
    """Run one server-authoritative Ranking V3 prospective session."""

    forward_store = RankingV3ForwardRepository(repo.session_factory)
    loader = QagentRankingV3CandidateLoader(repo, forward_store)
    resolver = QagentRankingV3MarketResolver(repo, forward_store, provider)
    authority = RankingV3ProductionForwardFactAuthority(loader, resolver)
    service = RankingV3ForwardService(
        forward_store,
        repo,
        authority,
    )
    return service.process_day(run_id, session_date)


def _candidate_from_snapshot(
    snapshot: OpportunitySnapshotRecord,
) -> RankingV3ServerCandidateRecord | None:
    if not isinstance(snapshot.card, Mapping):
        raise ValueError("opportunity snapshot card payload is not an object")
    try:
        card = OpportunityCard.model_validate(snapshot.card)
    except ValueError:
        return None
    if card.market != Market.CN:
        return None
    if (
        card.instrument_id != snapshot.instrument_id
        or card.primary_strategy_id != snapshot.primary_strategy_id
        or Decimal(str(card.rank_score)) != snapshot.rank_score
        or Decimal(str(card.strategy_score)) != snapshot.strategy_score
    ):
        raise ValueError("opportunity snapshot card identity does not match its row")
    if card.data_quality_audit is None or not card.data_quality_audit.can_recommend:
        return None
    if snapshot.signal_date is None or not (card.primary_strategy_id or "").strip():
        return None
    context = card.market_context
    if context is None or not context.industry.strip():
        return None
    asset_type = card.asset_type.strip().lower()
    index_memberships = tuple(sorted(set(context.index_memberships)))
    if asset_type in {"etf", "fund", "index_fund"} and not index_memberships:
        return None
    features = _feature_vector(card)
    if features is None:
        return None
    factor_signals = sorted(
        {
            *(str(value).strip() for value in card.factor_flags if str(value).strip()),
            *(exposure.factor_id for exposure in card.factor_exposures if exposure.score >= 0.65),
        }
    )
    market_regime = _market_regime(snapshot.card, card)
    if not market_regime:
        return None
    return RankingV3ServerCandidateRecord(
        source_snapshot_id=snapshot.snapshot_id,
        observed_on=snapshot.signal_date,
        instrument_id=snapshot.instrument_id,
        baseline_rank_score=float(snapshot.rank_score),
        primary_strategy_id=card.primary_strategy_id,
        factor_signals=tuple(factor_signals),
        market_regime=market_regime,
        asset_type=asset_type,
        industry=context.industry,
        index_memberships=index_memberships,
        features=features,
    )


def _feature_vector(card: OpportunityCard) -> RankingV3FeatureVector | None:
    exposures: dict[str, float] = {}
    for exposure in card.factor_exposures:
        if exposure.factor_id in exposures:
            return None
        exposures[exposure.factor_id] = float(exposure.score)
    if any(factor_id not in exposures for factor_id in _REQUIRED_FACTOR_IDS):
        return None
    execution_penalty = min(
        sum(
            (_EXECUTION_FLAG_PENALTIES.get(str(flag), Decimal("0")) for flag in card.factor_flags),
            Decimal("0"),
        ),
        Decimal("1"),
    )
    return RankingV3FeatureVector(
        strategy_score=float(card.strategy_score),
        factor_score=float(card.factor_score),
        valuation=exposures["valuation"],
        size=exposures["size"],
        quality=exposures["quality"],
        momentum=exposures["momentum"],
        trend_quality=exposures["trend_quality"],
        liquidity=exposures["liquidity"],
        low_risk=exposures["low_risk"],
        risk_filter=exposures["risk_filter"],
        reversal=exposures["reversal"],
        execution_penalty=float(execution_penalty),
        data_completeness=(
            float(card.data_quality_audit.score) if card.data_quality_audit is not None else 0.0
        ),
    )


def _market_regime(raw_card: Mapping[str, object], card: OpportunityCard) -> str:
    values: list[object] = [
        raw_card.get("market_regime"),
        raw_card.get("regime"),
        card.signal_hub.rotation_context if card.signal_hub is not None else None,
        card.rotation_note,
        card.market_context.summary if card.market_context is not None else None,
    ]
    for value in values:
        text = str(value or "").strip()
        if text:
            return text[:96]
    return ""


def _backtest_signal(snapshot: OpportunitySnapshotRecord) -> BacktestSignal:
    if snapshot.signal_date is None:
        raise ValueError("source opportunity snapshot has no signal date")
    card = OpportunityCard.model_validate(snapshot.card)
    return BacktestSignal(
        snapshot_id=snapshot.snapshot_id,
        instrument_id=snapshot.instrument_id,
        signal_date=snapshot.signal_date,
        primary_strategy_id=snapshot.primary_strategy_id,
        status=snapshot.status,
        rank_score=snapshot.rank_score,
        trigger_price=snapshot.trigger_price,
        initial_stop=snapshot.initial_stop,
        target_1=snapshot.target_1,
        outcome_status="pending",
        no_chase_above=card.entry_plan.no_chase_above,
    )


def _outcome_fact_from_stored(
    candidate: RankingV3ShadowCandidate,
) -> RankingV3ForwardOutcomeFact:
    return RankingV3ForwardOutcomeFact(
        candidate_id=candidate.candidate_id,
        status=candidate.outcome_status,
        gross_return_pct=candidate.gross_return_pct,
        transaction_cost_pct=candidate.transaction_cost_pct,
        stress_transaction_cost_pct=candidate.stress_transaction_cost_pct,
        benchmark_return_pct=candidate.benchmark_return_pct,
        max_drawdown_pct=candidate.max_drawdown_pct,
        reason=candidate.outcome_reason,
    )


def _previous_session(
    sessions: Sequence[object],
    session_date: date,
):
    eligible = [
        item for item in sessions if getattr(item, "session_date", session_date) < session_date
    ]
    return max(eligible, key=lambda item: item.session_date) if eligible else None


def _apply_return(equity: Decimal, return_pct: Decimal) -> Decimal:
    result = equity * (Decimal("1") + return_pct / Decimal("100"))
    if not result.is_finite() or result <= 0:
        raise ValueError("authoritative forward equity became non-positive or non-finite")
    return result.quantize(Decimal("0.00000001"))


def _canonical_equity_curve(
    points: Sequence[object],
) -> tuple[RankingV3ForwardEquityPoint, ...]:
    peak: Decimal | None = None
    canonical: list[RankingV3ForwardEquityPoint] = []
    for item in points:
        equity = Decimal(str(getattr(item, "equity")))
        peak = equity if peak is None else max(peak, equity)
        drawdown = (equity / peak - Decimal("1")) * Decimal("100")
        canonical.append(
            RankingV3ForwardEquityPoint(
                date=getattr(item, "date"),
                equity=equity,
                cash=Decimal(str(getattr(item, "cash"))),
                market_value=Decimal(str(getattr(item, "market_value"))),
                open_positions=int(getattr(item, "open_positions")),
                drawdown_pct=drawdown,
            )
        )
    return tuple(canonical)


def _normalized_bars(bars: pd.DataFrame, instrument_id: str) -> pd.DataFrame:
    if bars.empty:
        return bars
    required = {"instrument_id", "trade_date", "close"}
    if not required.issubset(bars.columns):
        return pd.DataFrame()
    frame = bars.loc[bars["instrument_id"].astype(str) == instrument_id].copy()
    if frame.empty:
        return frame
    if pd.api.types.is_datetime64_any_dtype(frame["trade_date"]):
        frame["trade_date"] = frame["trade_date"].dt.date
    return frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def _exact_close_return(
    frame: pd.DataFrame,
    start: date,
    end: date,
) -> Decimal | None:
    if frame.empty:
        return None
    price_column = "adjusted_close" if "adjusted_close" in frame.columns else "close"
    start_rows = frame.loc[frame["trade_date"] == start, price_column]
    end_rows = frame.loc[frame["trade_date"] == end, price_column]
    if start_rows.empty or end_rows.empty:
        return None
    first = Decimal(str(start_rows.iloc[-1]))
    last = Decimal(str(end_rows.iloc[-1]))
    if not first.is_finite() or not last.is_finite() or first <= 0 or last <= 0:
        return None
    return _pct(last / first - Decimal("1"))


def _pct(value: Decimal) -> Decimal:
    result = value * Decimal("100")
    if not result.is_finite() or not math.isfinite(float(result)):
        raise ValueError("computed percentage is non-finite")
    return result.quantize(Decimal("0.00000001"))
