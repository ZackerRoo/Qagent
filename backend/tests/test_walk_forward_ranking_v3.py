from datetime import date
from decimal import Decimal

import pandas as pd

from qagent.backtesting.portfolio import (
    CandidateOutcomeLedgerResult,
    CandidateOutcomeStatus,
    CandidateSignalOutcome,
)
from qagent.backtesting.ranking_v3 import (
    RankingV3FeatureVector,
    ResolvedRankingV3Observation,
)
from qagent.backtesting.ranking_v3_protocol import (
    RANKING_V3_ENTRY_WAIT_SESSIONS,
    RANKING_V3_HOLDING_SESSIONS,
    build_ranking_v3_protocol,
)
from qagent.backtesting.walk_forward import (
    WalkForwardSelection,
    WalkForwardSnapshot,
    _apply_ranking_v3,
    _build_ranking_v3_observations,
    _composite_benchmark_return,
    _ranking_v3_candidate_outcome_coverage,
    _ranking_v3_common_return_observations,
    _ranking_v3_historical_audit_last_decision_date,
    _ranking_v3_stratified_outcome_coverage,
    _ranking_v3_training_scope,
)
from qagent.historical_evidence.providers import REQUIRED_BENCHMARK_IDS
from qagent.market.calendars import trading_day_offset, trading_sessions_in_range


def _selection(
    index: int,
    *,
    rank_score: str,
    trend_quality: float,
    strategy: str = "trend_momentum_stage2",
) -> WalkForwardSelection:
    return WalkForwardSelection(
        instrument_id=f"CN:{index:06d}",
        status="setup_ready",
        primary_strategy_id=strategy,
        rank_score=Decimal(rank_score),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        asset_type="stock",
        industry=f"industry-{index % 4}",
        ranking_features=RankingV3FeatureVector(
            strategy_score=0.5,
            factor_score=0.5,
            valuation=0.5,
            size=0.5,
            quality=0.5,
            momentum=trend_quality,
            trend_quality=trend_quality,
            liquidity=0.5,
            low_risk=0.5,
            risk_filter=0.5,
            reversal=0.5,
            data_completeness=1.0,
        ),
    )


def test_ranking_v3_can_promote_candidate_outside_legacy_top_ten():
    legacy = [
        _selection(index, rank_score=f"0.{99 - index:02d}", trend_quality=0.2)
        for index in range(1, 11)
    ]
    outside_top_ten = _selection(
        11,
        rank_score="0.40",
        trend_quality=1.0,
        strategy="breakout_volume_confirmation",
    )
    snapshot = WalkForwardSnapshot(
        decision_date=date(2025, 1, 2),
        historical_universe_size=100,
        eligible_size=100,
        suspended_count=0,
        st_excluded_count=0,
        missing_tradability_count=0,
        candidate_pool=[*legacy, outside_top_ten],
        top_5=legacy[:5],
        top_10=legacy,
    )

    updated = _apply_ranking_v3([snapshot], observations=[])[0]

    assert outside_top_ten.instrument_id in {
        item.instrument_id for item in updated.ranking_v3_top_5
    }
    assert updated.top_5 == snapshot.top_5
    assert updated.top_10 == snapshot.top_10
    assert updated.ranking_v3_model_ready is False


def test_ranking_v3_and_baseline_share_strategy_and_industry_constraints():
    candidates = [
        _selection(
            index,
            rank_score=f"0.{99 - index:02d}",
            trend_quality=0.9 - index / 100,
            strategy="same-strategy" if index <= 4 else f"strategy-{index}",
        ).model_copy(update={"industry": "same-industry" if index <= 4 else f"industry-{index}"})
        for index in range(1, 9)
    ]
    snapshot = WalkForwardSnapshot(
        decision_date=date(2025, 1, 2),
        historical_universe_size=100,
        eligible_size=100,
        suspended_count=0,
        st_excluded_count=0,
        missing_tradability_count=0,
        candidate_pool=candidates,
        top_5=candidates[:5],
        top_10=candidates,
    )

    updated = _apply_ranking_v3([snapshot], observations=[])[0]

    for selections in (
        updated.constraint_matched_baseline_top_5,
        updated.ranking_v3_top_5,
    ):
        strategy_count = sum(item.primary_strategy_id == "same-strategy" for item in selections)
        industry_count = sum(item.industry == "same-industry" for item in selections)
        assert strategy_count <= 2
        assert industry_count <= 2


def test_ranking_v3_validation_rows_use_common_dates_and_cash_padding():
    decision_date = date(2025, 1, 2)
    baseline = [_selection(1, rank_score="0.90", trend_quality=0.4)]
    challenger = [
        _selection(2, rank_score="0.80", trend_quality=0.8),
        _selection(3, rank_score="0.70", trend_quality=0.7),
    ]
    snapshot = WalkForwardSnapshot(
        decision_date=decision_date,
        historical_universe_size=100,
        eligible_size=100,
        suspended_count=0,
        st_excluded_count=0,
        missing_tradability_count=0,
        constraint_matched_baseline_top_5=baseline,
        ranking_v3_top_5=challenger,
    )
    ledger = CandidateOutcomeLedgerResult(
        provider="test",
        start=decision_date,
        end=decision_date,
        nominal_amount=Decimal("100000"),
        outcomes=[
            CandidateSignalOutcome(
                snapshot_id="baseline",
                instrument_id=baseline[0].instrument_id,
                strategy_id=baseline[0].primary_strategy_id,
                signal_date=decision_date,
                status=CandidateOutcomeStatus.RESOLVED,
                status_detail="resolved",
                nominal_amount=Decimal("100000"),
                resolved_at=date(2025, 1, 20),
                return_pct=1.5,
            ),
            CandidateSignalOutcome(
                snapshot_id="challenger-resolved",
                instrument_id=challenger[0].instrument_id,
                strategy_id=challenger[0].primary_strategy_id,
                signal_date=decision_date,
                status=CandidateOutcomeStatus.RESOLVED,
                status_detail="resolved",
                nominal_amount=Decimal("100000"),
                resolved_at=date(2025, 1, 20),
                return_pct=2.5,
            ),
            CandidateSignalOutcome(
                snapshot_id="challenger-untriggered",
                instrument_id=challenger[1].instrument_id,
                strategy_id=challenger[1].primary_strategy_id,
                signal_date=decision_date,
                status=CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE,
                status_detail="entry_not_triggered",
                nominal_amount=Decimal("100000"),
                resolved_at=date(2025, 1, 10),
            ),
        ],
        status_counts={
            CandidateOutcomeStatus.RESOLVED.value: 2,
            CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE.value: 1,
        },
        data_health={},
    )

    baseline_rows, challenger_rows, completed, quality = _ranking_v3_common_return_observations(
        [snapshot], ledger=ledger
    )

    assert len(baseline_rows) == len(challenger_rows) == 5
    assert {item.rebalance_date for item in baseline_rows} == {decision_date}
    assert {item.rebalance_date for item in challenger_rows} == {decision_date}
    assert [item.net_return_pct for item in baseline_rows] == [1.5, 0, 0, 0, 0]
    assert [item.net_return_pct for item in challenger_rows] == [2.5, 0, 0, 0, 0]
    assert completed == 1
    assert quality["selected_outcome_count"] == 3
    assert quality["valid_outcome_count"] == 3
    assert quality["invalid_outcome_count"] == 0
    assert quality["valid_outcome_coverage_ratio"] == 1.0
    assert quality["considered_rebalance_date_count"] == 1
    assert quality["retained_rebalance_date_count"] == 1
    assert quality["paired_rebalance_date_coverage_ratio"] == 1.0


def test_ranking_v3_validation_keeps_market_gate_cash_dates_in_intent_to_treat_sample():
    decision_date = date(2025, 1, 2)
    snapshot = WalkForwardSnapshot(
        decision_date=decision_date,
        historical_universe_size=100,
        eligible_size=100,
        suspended_count=0,
        st_excluded_count=0,
        missing_tradability_count=0,
        market_entry_allowed=False,
    )
    ledger = CandidateOutcomeLedgerResult(
        provider="test",
        start=decision_date,
        end=decision_date,
        nominal_amount=Decimal("100000"),
        outcomes=[],
        status_counts={},
        data_health={},
    )

    baseline_rows, challenger_rows, completed, quality = _ranking_v3_common_return_observations(
        [snapshot], ledger=ledger
    )

    assert [item.net_return_pct for item in baseline_rows] == [0.0] * 5
    assert [item.net_return_pct for item in challenger_rows] == [0.0] * 5
    assert completed == 0
    assert quality["selected_outcome_count"] == 0
    assert quality["considered_rebalance_date_count"] == 1
    assert quality["retained_rebalance_date_count"] == 1
    assert quality["paired_rebalance_date_coverage_ratio"] == 1.0


def test_ranking_v3_validation_excludes_date_with_invalid_or_censored_outcome():
    decision_date = date(2025, 1, 2)
    baseline = [_selection(1, rank_score="0.90", trend_quality=0.4)]
    challenger = [_selection(2, rank_score="0.80", trend_quality=0.8)]
    snapshot = WalkForwardSnapshot(
        decision_date=decision_date,
        historical_universe_size=100,
        eligible_size=100,
        suspended_count=0,
        st_excluded_count=0,
        missing_tradability_count=0,
        constraint_matched_baseline_top_5=baseline,
        ranking_v3_top_5=challenger,
    )
    ledger = CandidateOutcomeLedgerResult(
        provider="test",
        start=decision_date,
        end=decision_date,
        nominal_amount=Decimal("100000"),
        outcomes=[
            CandidateSignalOutcome(
                snapshot_id="baseline",
                instrument_id=baseline[0].instrument_id,
                strategy_id=baseline[0].primary_strategy_id,
                signal_date=decision_date,
                status=CandidateOutcomeStatus.RESOLVED,
                status_detail="resolved",
                nominal_amount=Decimal("100000"),
                resolved_at=date(2025, 1, 20),
                return_pct=1.0,
            ),
            CandidateSignalOutcome(
                snapshot_id="challenger",
                instrument_id=challenger[0].instrument_id,
                strategy_id=challenger[0].primary_strategy_id,
                signal_date=decision_date,
                status=CandidateOutcomeStatus.EXIT_UNFILLABLE,
                status_detail="exit_order_unfillable",
                nominal_amount=Decimal("100000"),
                resolved_at=date(2025, 1, 20),
            ),
            CandidateSignalOutcome(
                snapshot_id="unselected-invalid",
                instrument_id="CN:999999",
                strategy_id="unselected",
                signal_date=decision_date,
                status=CandidateOutcomeStatus.INSUFFICIENT_FUTURE_DATA,
                status_detail="missing_adjusted_close",
                nominal_amount=Decimal("100000"),
                resolved_at=date(2025, 1, 20),
            ),
        ],
        status_counts={},
        data_health={},
    )

    baseline_rows, challenger_rows, completed, quality = _ranking_v3_common_return_observations(
        [snapshot], ledger=ledger
    )

    assert baseline_rows == []
    assert challenger_rows == []
    assert completed == 0
    assert quality == {
        "selected_outcome_count": 2,
        "valid_outcome_count": 1,
        "invalid_outcome_count": 1,
        "excluded_rebalance_date_count": 1,
        "valid_outcome_coverage_ratio": 0.5,
        "considered_rebalance_date_count": 1,
        "retained_rebalance_date_count": 0,
        "paired_rebalance_date_coverage_ratio": 0.0,
    }
    assert _ranking_v3_candidate_outcome_coverage(ledger) == (
        3,
        1,
        0.333333,
    )


def test_ranking_v3_stratified_coverage_exposes_concentrated_missing_outcomes():
    decision_date = date(2025, 1, 2)
    selections = [
        _selection(index, rank_score=str(1 - index / 100), trend_quality=0.5)
        for index in range(1, 21)
    ]
    snapshot = WalkForwardSnapshot(
        decision_date=decision_date,
        historical_universe_size=100,
        eligible_size=100,
        suspended_count=0,
        st_excluded_count=0,
        missing_tradability_count=0,
        candidate_pool=selections,
    )
    outcomes = [
        CandidateSignalOutcome(
            snapshot_id=f"snapshot-{index}",
            instrument_id=selection.instrument_id,
            strategy_id=selection.primary_strategy_id,
            signal_date=decision_date,
            status=(
                CandidateOutcomeStatus.INSUFFICIENT_FUTURE_DATA
                if index < 2
                else CandidateOutcomeStatus.RESOLVED
            ),
            status_detail="missing_adjusted_close" if index < 2 else "resolved",
            nominal_amount=Decimal("100000"),
            resolved_at=date(2025, 1, 20),
            return_pct=None if index < 2 else 1.0,
        )
        for index, selection in enumerate(selections)
    ]
    ledger = CandidateOutcomeLedgerResult(
        provider="test",
        start=decision_date,
        end=date(2025, 1, 20),
        nominal_amount=Decimal("100000"),
        outcomes=outcomes,
        status_counts={},
        data_health={},
    )

    slices = _ranking_v3_stratified_outcome_coverage(
        [snapshot],
        ledger=ledger,
        protocol=build_ranking_v3_protocol(),
    )

    strategy_slice = next(item for item in slices if item["dimension"] == "strategy")
    assert strategy_slice["total_count"] == 20
    assert strategy_slice["valid_count"] == 18
    assert strategy_slice["coverage_ratio"] == 0.9


def test_ranking_v3_historical_audit_reserves_full_outcome_window():
    audit_end = date(2025, 12, 31)

    last_decision = _ranking_v3_historical_audit_last_decision_date(
        date(2021, 11, 1),
        audit_end,
    )

    sessions_after_decision = trading_sessions_in_range(
        last_decision,
        audit_end,
    )[1:]
    assert len(sessions_after_decision) == 25


def test_ranking_v3_training_scope_purges_gap_observations_and_freezes_windows():
    protocol = build_ranking_v3_protocol()
    features = RankingV3FeatureVector()

    def observation(signal_date: date, available_at: date) -> ResolvedRankingV3Observation:
        return ResolvedRankingV3Observation(
            instrument_id=f"CN:{signal_date:%m%d}",
            signal_date=signal_date,
            available_at=available_at,
            outcome_status="resolved",
            triggered=True,
            return_pct=1.0,
            benchmark_return_pct=0.0,
            net_excess_return_pct=1.0,
            features=features,
        )

    train = observation(date(2023, 6, 30), date(2023, 7, 20))
    first_gap = observation(date(2023, 7, 31), date(2023, 8, 1))
    validation = observation(date(2024, 6, 28), date(2024, 7, 20))
    second_gap = observation(date(2024, 7, 29), date(2024, 7, 31))
    audit = observation(date(2025, 12, 31), date(2026, 1, 30))
    observations = [train, first_gap, validation, second_gap, audit]

    gap_key, gap_rows, gap_cutoff = _ranking_v3_training_scope(
        protocol,
        observations,
        decision_date=date(2023, 7, 31),
    )
    validation_key, validation_rows, validation_cutoff = _ranking_v3_training_scope(
        protocol,
        observations,
        decision_date=date(2023, 8, 7),
    )
    audit_key, audit_rows, audit_cutoff = _ranking_v3_training_scope(
        protocol,
        observations,
        decision_date=date(2024, 8, 5),
    )
    forward_key, forward_rows, forward_cutoff = _ranking_v3_training_scope(
        protocol,
        observations,
        decision_date=protocol.prospective_shadow_start,
    )

    assert (gap_key, gap_rows, gap_cutoff) == (None, [], None)
    assert validation_key == "validation"
    assert validation_rows == [train]
    assert validation_cutoff == date(2023, 8, 7)
    assert audit_key == "historical_reused_oos"
    assert audit_rows == [train, validation]
    assert audit_cutoff == date(2024, 8, 5)
    assert forward_key == "prospective_shadow"
    assert forward_rows == [train, validation, audit]
    assert forward_cutoff == protocol.prospective_shadow_start


def test_ranking_v3_observations_use_explicit_resolution_date_and_skip_censored():
    decision_date = date(2025, 1, 24)
    resolved_at = date(2025, 2, 11)
    maturity_date = trading_day_offset(
        decision_date,
        RANKING_V3_ENTRY_WAIT_SESSIONS + RANKING_V3_HOLDING_SESSIONS,
    )
    selection = _selection(1, rank_score="0.90", trend_quality=0.8)
    snapshot = WalkForwardSnapshot(
        decision_date=decision_date,
        historical_universe_size=100,
        eligible_size=100,
        suspended_count=0,
        st_excluded_count=0,
        missing_tradability_count=0,
        candidate_pool=[selection],
    )
    ledger = CandidateOutcomeLedgerResult(
        provider="test",
        start=decision_date,
        end=resolved_at,
        nominal_amount=Decimal("100000"),
        outcomes=[
            CandidateSignalOutcome(
                snapshot_id="resolved-window",
                instrument_id=selection.instrument_id,
                strategy_id=selection.primary_strategy_id,
                signal_date=decision_date,
                status=CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE,
                status_detail="entry_not_triggered",
                nominal_amount=Decimal("100000"),
                resolved_at=resolved_at,
            ),
            CandidateSignalOutcome(
                snapshot_id="censored-window",
                instrument_id=selection.instrument_id,
                strategy_id=selection.primary_strategy_id,
                signal_date=decision_date,
                status=CandidateOutcomeStatus.INSUFFICIENT_FUTURE_DATA,
                status_detail="entry_wait_window_incomplete",
                nominal_amount=Decimal("100000"),
            ),
            CandidateSignalOutcome(
                snapshot_id="triggered-unfillable",
                instrument_id=selection.instrument_id,
                strategy_id=selection.primary_strategy_id,
                signal_date=decision_date,
                status=CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE,
                status_detail="entry_triggered_but_unfillable",
                nominal_amount=Decimal("100000"),
                resolved_at=resolved_at,
            ),
        ],
        status_counts={},
        data_health={},
    )

    class BenchmarkMarketProvider:
        def get_daily_bars(self, instrument_ids, start, end):
            rows = []
            for instrument_id in REQUIRED_BENCHMARK_IDS:
                rows.extend(
                    [
                        {
                            "instrument_id": instrument_id,
                            "trade_date": decision_date,
                            "close": 100.0,
                            "adjusted_close": 100.0,
                        },
                        {
                            "instrument_id": instrument_id,
                            "trade_date": maturity_date,
                            "close": 120.0,
                            "adjusted_close": 120.0,
                        },
                    ]
                )
            return pd.DataFrame(rows)

    observations = _build_ranking_v3_observations(
        [snapshot],
        ledger=ledger,
        market_provider=BenchmarkMarketProvider(),
        start=decision_date,
        end=maturity_date,
    )

    assert len(observations) == 1
    assert observations[0].available_at == maturity_date
    assert observations[0].outcome_status == "not_triggered"
    assert observations[0].triggered is False
    assert observations[0].return_pct == 0.0
    assert observations[0].benchmark_return_pct == 20.0
    assert observations[0].net_excess_return_pct == -20.0


def test_candidate_benchmark_fails_closed_when_any_frozen_index_is_missing():
    start = date(2025, 1, 2)
    end = date(2025, 1, 10)
    series = {
        instrument_id: ([start, end], [100.0, 110.0])
        for instrument_id in REQUIRED_BENCHMARK_IDS[:-1]
    }

    assert _composite_benchmark_return(series, start=start, end=end) is None

    series[REQUIRED_BENCHMARK_IDS[-1]] = ([start, end], [100.0, 120.0])
    assert _composite_benchmark_return(series, start=start, end=end) == 10.0


def test_ranking_v3_completed_outcome_benchmark_uses_actual_entry_to_exit_interval():
    signal_date = date(2025, 1, 2)
    entry_date = date(2025, 1, 6)
    exit_date = date(2025, 1, 10)
    selection = _selection(1, rank_score="0.90", trend_quality=0.8)
    snapshot = WalkForwardSnapshot(
        decision_date=signal_date,
        historical_universe_size=100,
        eligible_size=100,
        suspended_count=0,
        st_excluded_count=0,
        missing_tradability_count=0,
        candidate_pool=[selection],
    )
    ledger = CandidateOutcomeLedgerResult(
        provider="test",
        start=signal_date,
        end=exit_date,
        nominal_amount=Decimal("100000"),
        outcomes=[
            CandidateSignalOutcome(
                snapshot_id="resolved",
                instrument_id=selection.instrument_id,
                strategy_id=selection.primary_strategy_id,
                signal_date=signal_date,
                status=CandidateOutcomeStatus.RESOLVED,
                status_detail="resolved",
                nominal_amount=Decimal("100000"),
                resolved_at=exit_date,
                entry_date=entry_date,
                exit_date=exit_date,
                return_pct=15.0,
            )
        ],
        status_counts={},
        data_health={},
    )

    class BenchmarkMarketProvider:
        def get_daily_bars(self, instrument_ids, start, end):
            rows = []
            for instrument_id in REQUIRED_BENCHMARK_IDS:
                rows.extend(
                    [
                        {
                            "instrument_id": instrument_id,
                            "trade_date": signal_date,
                            "close": 100.0,
                            "adjusted_close": 100.0,
                        },
                        {
                            "instrument_id": instrument_id,
                            "trade_date": entry_date,
                            "close": 200.0,
                            "adjusted_close": 200.0,
                        },
                        {
                            "instrument_id": instrument_id,
                            "trade_date": exit_date,
                            "close": 220.0,
                            "adjusted_close": 220.0,
                        },
                    ]
                )
            return pd.DataFrame(rows)

    observations = _build_ranking_v3_observations(
        [snapshot],
        ledger=ledger,
        market_provider=BenchmarkMarketProvider(),
        start=signal_date,
        end=exit_date,
    )

    assert len(observations) == 1
    assert observations[0].available_at == exit_date
    assert observations[0].benchmark_return_pct == 10.0
    assert observations[0].net_excess_return_pct == 5.0
