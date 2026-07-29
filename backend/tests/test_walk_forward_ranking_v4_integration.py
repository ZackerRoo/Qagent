from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from qagent.backtesting import walk_forward
from qagent.backtesting.portfolio import (
    CandidateOutcomeLedgerResult,
    CandidateOutcomeStatus,
    CandidateSignalOutcome,
    PortfolioBacktestResult,
    PortfolioBacktestSummary,
    PortfolioEquityPoint,
)
from qagent.backtesting.ranking_v4 import (
    RankingV4Candidate,
    RankingV4FeatureVector,
)
from qagent.backtesting.walk_forward import (
    WalkForwardSelection,
    WalkForwardSnapshot,
    _build_ranking_v4_observations,
    _enrich_selection_constraints,
    _ranking_v4_candidate_from_selection,
    _ranking_v4_evidence_coverage,
    _ranking_v4_gate_decomposition,
    _ranking_v4_baseline_decision,
    _ranking_v4_beta,
    _ranking_v4_correlation,
    _ranking_v4_market_features,
    _ranking_v4_risk_evidence,
    _ranking_v4_return_series,
    _ranking_v4_selected_return_observations,
)
from qagent.historical_evidence.models import (
    HistoricalIndexMembership,
    HistoricalIndustrySnapshot,
)
from qagent.strategy_data.models import FundamentalSnapshot


def _selection(instrument_id: str = "CN:000001") -> WalkForwardSelection:
    return WalkForwardSelection(
        instrument_id=instrument_id,
        status="watch",
        primary_strategy_id="trend",
        rank_score=Decimal("0.8"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        asset_type="stock",
        industry="银行",
        ranking_v4_temporal_evidence_complete=True,
        ranking_v4_constraint_data_complete=True,
        ranking_v4_constraint_evidence_mode="point_in_time_metadata",
        ranking_v4_features=RankingV4FeatureVector(data_completeness=0.9),
    )


def _snapshot(selection: WalkForwardSelection) -> WalkForwardSnapshot:
    return WalkForwardSnapshot(
        decision_date=date(2025, 1, 2),
        historical_universe_size=1,
        eligible_size=1,
        suspended_count=0,
        st_excluded_count=0,
        missing_tradability_count=0,
        ranking_v4_candidate_pool=[selection],
    )


def _usable_fundamental(
    instrument_id: str,
    *,
    as_of_date: date,
) -> FundamentalSnapshot:
    return FundamentalSnapshot(
        instrument_id=instrument_id,
        as_of_date=as_of_date,
        market_cap=Decimal("1000000000"),
        provider="fixture",
    )


def _risk_rows(instrument_id: str, decision_date: date) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    candidate_price = 10.0
    benchmark_price = 100.0
    start = decision_date - timedelta(days=62)
    for offset in range(62):
        trade_date = start + timedelta(days=offset)
        candidate_price *= 1.002 if offset % 2 else 0.999
        benchmark_price *= 1.001 if offset % 2 else 0.9995
        rows.extend(
            (
                {
                    "instrument_id": instrument_id,
                    "trade_date": trade_date,
                    "close": candidate_price,
                    "adjusted_close": candidate_price,
                },
                {
                    "instrument_id": "CN:000300.IDX",
                    "trade_date": trade_date,
                    "close": benchmark_price,
                    "adjusted_close": benchmark_price,
                },
            )
        )
    return rows


def _portfolio(
    *,
    start_equity: Decimal = Decimal("100000"),
    end_equity: Decimal = Decimal("101000"),
) -> PortfolioBacktestResult:
    start = date(2025, 1, 2)
    end = date(2025, 3, 1)
    return PortfolioBacktestResult(
        summary=PortfolioBacktestSummary(
            provider="fixture",
            symbols=[],
            start=start,
            end=end,
            initial_capital=start_equity,
            final_equity=end_equity,
            total_return_pct=float((end_equity / start_equity - 1) * 100),
            max_drawdown_pct=0,
            trade_count=0,
            win_rate=None,
            profit_factor=None,
            avg_trade_return_pct=None,
            exposure_pct=0,
        ),
        trades=[],
        equity_curve=[
            PortfolioEquityPoint(
                date=start,
                equity=start_equity,
                cash=start_equity,
                open_positions=0,
                drawdown_pct=0,
            ),
            PortfolioEquityPoint(
                date=end,
                equity=end_equity,
                cash=end_equity,
                open_positions=0,
                drawdown_pct=0,
            ),
        ],
        monthly_returns=[],
        data_health={},
    )


def test_market_features_fail_closed_with_finite_neutral_values():
    features = _ranking_v4_market_features(
        [],
        benchmark_valid_count=0,
        benchmark_required_count=4,
        benchmark_above_count=0,
    )

    assert features == {
        "market_breadth": 0.5,
        "benchmark_slope": 0.5,
        "realized_volatility": 0.5,
        "cross_sectional_dispersion": 0.5,
        "market_features_complete": False,
    }


def test_risk_series_requires_adjusted_candidate_prices_and_is_point_in_time():
    start = date(2024, 1, 1)
    frame = pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=index) for index in range(70)],
            "close": [100 + index for index in range(70)],
            "adjusted_close": [None] * 70,
        }
    )

    assert _ranking_v4_return_series(frame, adjusted_required=True) == {}
    benchmark = _ranking_v4_return_series(frame, adjusted_required=False)
    assert len(benchmark) == 69
    assert max(benchmark) <= start + timedelta(days=69)


def test_beta_and_correlation_require_sixty_common_observations():
    start = date(2024, 1, 1)
    benchmark = {start + timedelta(days=index): index / 10_000 for index in range(60)}
    candidate = {key: value * 1.1 for key, value in benchmark.items()}

    assert _ranking_v4_beta(candidate, benchmark) == 1.1
    assert _ranking_v4_correlation(candidate, benchmark) == 1.0
    assert _ranking_v4_beta(dict(list(candidate.items())[:59]), benchmark) is None


def test_not_triggered_candidate_is_stage_one_evidence_not_zero_return(
    monkeypatch,
):
    selection = _selection()
    snapshot = _snapshot(selection)
    outcome = CandidateSignalOutcome(
        snapshot_id="signal",
        instrument_id=selection.instrument_id,
        strategy_id=selection.primary_strategy_id,
        signal_date=snapshot.decision_date,
        status=CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE,
        status_detail="entry_not_triggered",
        nominal_amount=Decimal("100000"),
        resolved_at=date(2025, 2, 7),
    )
    ledger = CandidateOutcomeLedgerResult(
        provider="historical_replay",
        start=date(2025, 1, 1),
        end=date(2025, 3, 1),
        nominal_amount=Decimal("100000"),
        outcomes=[outcome],
        status_counts={outcome.status.value: 1},
        data_health={},
    )
    monkeypatch.setattr(
        "qagent.backtesting.walk_forward._benchmark_price_series",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "qagent.backtesting.walk_forward._composite_benchmark_return",
        lambda *_args, **_kwargs: 2.5,
    )

    observations = _build_ranking_v4_observations(
        [snapshot],
        ledger=ledger,
        market_provider=object(),
        start=ledger.start,
        end=ledger.end,
    )

    assert len(observations) == 1
    assert observations[0].outcome_status == "not_triggered"
    assert observations[0].triggered is False
    assert observations[0].return_pct is None
    assert observations[0].cost_adjusted_net_excess_return_pct is None


def test_constraint_matched_baseline_fails_closed_without_temporal_evidence():
    incomplete = RankingV4Candidate(
        instrument_id="CN:000001",
        baseline_rank_score=0.8,
        features=RankingV4FeatureVector(data_completeness=0.9),
    )

    decision = _ranking_v4_baseline_decision(
        [incomplete],
        decision_date=date(2025, 1, 2),
    )

    assert decision.eligible_position_count == 0
    assert decision.candidates[0].eligible_for_position is False
    assert "temporal_evidence_incomplete" in decision.candidates[0].blocked_reasons
    assert "point_in_time_evidence_incomplete" in decision.candidates[0].blocked_reasons


def test_constraint_enrichment_never_reuses_current_industry_or_membership():
    selection = _selection().model_copy(
        update={
            "industry": "当前行业",
            "index_memberships": ["CN:CURRENT.IDX"],
        }
    )
    snapshot = _snapshot(selection).model_copy(
        update={
            "candidate_pool": [selection],
            "top_10": [selection],
            "top_5": [selection],
        }
    )

    class PointInTimeRepository:
        def fundamentals_as_of(self, *_args, **_kwargs):
            return {
                selection.instrument_id: _usable_fundamental(
                    selection.instrument_id,
                    as_of_date=date(2024, 12, 31),
                )
            }

        def industries_as_of(self, *_args, **_kwargs):
            return {
                selection.instrument_id: HistoricalIndustrySnapshot(
                    instrument_id=selection.instrument_id,
                    snapshot_date=snapshot.decision_date,
                    industry="历史行业",
                    provider="fixture",
                )
            }

        def available_memberships_as_of(self, *_args, **_kwargs):
            return (
                {
                    selection.instrument_id: [
                        HistoricalIndexMembership(
                            index_id="CN:HISTORICAL.IDX",
                            snapshot_date=snapshot.decision_date,
                            instrument_id=selection.instrument_id,
                            provider="fixture",
                        )
                    ]
                },
                [],
            )

        def adjusted_bar_vintage_counts(self, *_args, **_kwargs):
            return {selection.instrument_id: 61}

    enriched = _enrich_selection_constraints(
        [snapshot],
        repository=PointInTimeRepository(),
        revision=1,
        asset_types={selection.instrument_id: "stock"},
    )[0]

    assert enriched.ranking_v4_candidate_pool[0].industry == "历史行业"
    assert enriched.ranking_v4_candidate_pool[0].index_memberships == ["CN:HISTORICAL.IDX"]
    assert enriched.ranking_v4_candidate_pool[0].ranking_v4_temporal_evidence_complete is True
    assert enriched.ranking_v4_candidate_pool[0].themes == []


def test_v41_missing_historical_industry_does_not_erase_price_feature_timeline():
    selection = _selection().model_copy(
        update={
            "industry": "当前行业",
            "index_memberships": ["CN:CURRENT.IDX"],
        }
    )
    snapshot = _snapshot(selection)

    class PriceAndFundamentalRepository:
        def fundamentals_as_of(self, *_args, **_kwargs):
            return {
                selection.instrument_id: _usable_fundamental(
                    selection.instrument_id,
                    as_of_date=date(2024, 12, 31),
                )
            }

        def industries_as_of(self, *_args, **_kwargs):
            return {}

        def available_memberships_as_of(self, *_args, **_kwargs):
            return ({}, [])

        def adjusted_bar_vintage_counts(self, *_args, **_kwargs):
            return {selection.instrument_id: 61}

    enriched = _enrich_selection_constraints(
        [snapshot],
        repository=PriceAndFundamentalRepository(),
        revision=1,
        asset_types={selection.instrument_id: "stock"},
    )[0].ranking_v4_candidate_pool[0]

    assert enriched.industry is None
    assert enriched.index_memberships == []
    assert enriched.ranking_v4_industry_evidence_complete is False
    assert enriched.ranking_v4_temporal_evidence_complete is True


def test_empty_fundamental_row_is_not_complete_evidence():
    selection = _selection()
    snapshot = _snapshot(selection)
    empty_fundamental = FundamentalSnapshot(
        instrument_id=selection.instrument_id,
        as_of_date=date(2024, 9, 30),
        provider="fixture",
    )

    class EmptyFundamentalRepository:
        def fundamentals_as_of(self, *_args, **_kwargs):
            return {selection.instrument_id: empty_fundamental}

        def industries_as_of(self, *_args, **_kwargs):
            return {
                selection.instrument_id: HistoricalIndustrySnapshot(
                    instrument_id=selection.instrument_id,
                    snapshot_date=date(2024, 12, 15),
                    industry="银行",
                    provider="fixture",
                )
            }

        def available_memberships_as_of(self, *_args, **_kwargs):
            return ({selection.instrument_id: []}, [])

        def adjusted_bar_vintage_counts(self, *_args, **_kwargs):
            return {selection.instrument_id: 61}

    enriched = _enrich_selection_constraints(
        [snapshot],
        repository=EmptyFundamentalRepository(),
        revision=1,
        asset_types={selection.instrument_id: "stock"},
    )[0].ranking_v4_candidate_pool[0]

    assert enriched.ranking_v4_fundamental_as_of == date(2024, 9, 30)
    assert enriched.ranking_v4_fundamental_evidence_complete is False
    assert enriched.ranking_v4_temporal_evidence_complete is False


def test_candidate_preserves_actual_historical_evidence_dates():
    selection = _selection()
    snapshot = _snapshot(selection)

    class DatedEvidenceRepository:
        def fundamentals_as_of(self, *_args, **_kwargs):
            return {
                selection.instrument_id: _usable_fundamental(
                    selection.instrument_id,
                    as_of_date=date(2024, 9, 30),
                )
            }

        def industries_as_of(self, *_args, **_kwargs):
            return {
                selection.instrument_id: HistoricalIndustrySnapshot(
                    instrument_id=selection.instrument_id,
                    snapshot_date=date(2024, 12, 15),
                    industry="银行",
                    provider="fixture",
                )
            }

        def available_memberships_as_of(self, *_args, **_kwargs):
            return (
                {
                    selection.instrument_id: [
                        HistoricalIndexMembership(
                            index_id="CN:000300.IDX",
                            snapshot_date=date(2024, 12, 20),
                            instrument_id=selection.instrument_id,
                            provider="fixture",
                        )
                    ]
                },
                [],
            )

        def adjusted_bar_vintage_counts(self, *_args, **_kwargs):
            return {selection.instrument_id: 62}

        def instrument_rule_metadata_on(self, *_args, **_kwargs):
            return SimpleNamespace(
                effective_from=date(2022, 1, 1),
                fee_schedule_version="fees-v1",
                fee_rule_key="cn-stock",
            )

        def fee_rules_on(self, **_kwargs):
            return [
                SimpleNamespace(effective_from=date(2023, 8, 28)),
                SimpleNamespace(effective_from=date(2023, 8, 28)),
            ]

    class RiskProvider:
        def get_daily_bars(self, *_args, **_kwargs):
            rows = _risk_rows(selection.instrument_id, snapshot.decision_date)
            rows.extend(
                (
                    {
                        "instrument_id": selection.instrument_id,
                        "trade_date": snapshot.decision_date + timedelta(days=1),
                        "close": 999.0,
                        "adjusted_close": 999.0,
                    },
                    {
                        "instrument_id": "CN:000300.IDX",
                        "trade_date": snapshot.decision_date + timedelta(days=1),
                        "close": 999.0,
                        "adjusted_close": 999.0,
                    },
                )
            )
            return pd.DataFrame(rows)

    enriched_snapshot = _enrich_selection_constraints(
        [snapshot],
        repository=DatedEvidenceRepository(),
        revision=1,
        asset_types={selection.instrument_id: "stock"},
    )[0]
    risk_pool, _ = _ranking_v4_risk_evidence(
        enriched_snapshot,
        market_provider=RiskProvider(),
    )
    enriched = risk_pool[0]
    candidate = _ranking_v4_candidate_from_selection(
        enriched,
        decision_date=snapshot.decision_date,
        market_regime="neutral",
        market_features_complete=True,
    )

    assert enriched.ranking_v4_fundamental_as_of == date(2024, 9, 30)
    assert enriched.ranking_v4_industry_snapshot_date == date(2024, 12, 15)
    assert enriched.ranking_v4_index_membership_snapshot_date == date(2024, 12, 20)
    assert enriched.ranking_v4_bar_end_date == date(2025, 1, 1)
    assert enriched.ranking_v4_cost_effective_date == date(2023, 8, 28)
    assert candidate.feature_as_of == date(2025, 1, 1)
    assert candidate.market_regime_as_of == date(2025, 1, 1)
    assert candidate.constraint_as_of == date(2025, 1, 1)
    assert candidate.cost_as_of == date(2023, 8, 28)
    assert candidate.replacement_cost_pct == 0.15
    assert candidate.stage_two_embedded_cost_pct == 0.0
    assert candidate.replacement_cost_evidence_complete is True
    assert {
        candidate.feature_as_of,
        candidate.market_regime_as_of,
        candidate.constraint_as_of,
        candidate.cost_as_of,
    } == {date(2025, 1, 1), date(2023, 8, 28)}
    assert snapshot.decision_date not in {
        candidate.feature_as_of,
        candidate.market_regime_as_of,
        candidate.constraint_as_of,
        candidate.cost_as_of,
    }


def test_etf_index_membership_is_not_underlying_evidence():
    selection = _selection("CN:510300").model_copy(
        update={
            "asset_type": "etf",
            "industry": "指数ETF",
            "index_memberships": ["CURRENT:沪深300"],
        }
    )
    snapshot = _snapshot(selection)

    class EtfEvidenceRepository:
        def fundamentals_as_of(self, *_args, **_kwargs):
            return {}

        def industries_as_of(self, *_args, **_kwargs):
            return {
                selection.instrument_id: HistoricalIndustrySnapshot(
                    instrument_id=selection.instrument_id,
                    snapshot_date=date(2024, 12, 15),
                    industry="指数ETF",
                    provider="fixture",
                )
            }

        def available_memberships_as_of(self, *_args, **_kwargs):
            return (
                {
                    selection.instrument_id: [
                        HistoricalIndexMembership(
                            index_id="CN:000300.IDX",
                            snapshot_date=date(2024, 12, 20),
                            instrument_id=selection.instrument_id,
                            provider="fixture",
                        )
                    ]
                },
                [],
            )

        def adjusted_bar_vintage_counts(self, *_args, **_kwargs):
            return {selection.instrument_id: 62}

    class RiskProvider:
        def get_daily_bars(self, *_args, **_kwargs):
            return pd.DataFrame(_risk_rows(selection.instrument_id, snapshot.decision_date))

    enriched_snapshot = _enrich_selection_constraints(
        [snapshot],
        repository=EtfEvidenceRepository(),
        revision=1,
        asset_types={selection.instrument_id: "etf"},
    )[0]
    metadata = enriched_snapshot.ranking_v4_candidate_pool[0]

    assert metadata.index_memberships == ["CN:000300.IDX"]
    assert metadata.underlying_ids == []
    assert metadata.ranking_v4_underlying_evidence_complete is False
    assert metadata.ranking_v4_underlying_evidence_mode == "unknown_no_holdings"
    assert metadata.ranking_v4_raw_metadata_complete is False

    risk_pool, _ = _ranking_v4_risk_evidence(
        enriched_snapshot,
        market_provider=RiskProvider(),
    )
    risk_evidence = risk_pool[0]
    candidate = _ranking_v4_candidate_from_selection(
        risk_evidence,
        decision_date=snapshot.decision_date,
        market_regime="neutral",
        market_features_complete=True,
    )

    assert risk_evidence.ranking_v4_constraint_data_complete is True
    assert risk_evidence.ranking_v4_constraint_evidence_mode == "return_risk_proxy"
    assert risk_evidence.ranking_v4_combined_constraint_evidence_complete is False
    assert candidate.underlying_evidence_complete is False
    assert candidate.underlying_ids == []


def test_v41_return_history_can_prove_risk_without_industry_metadata():
    selection = _selection().model_copy(
        update={
            "industry": None,
            "ranking_v4_industry_evidence_complete": False,
            "ranking_v4_constraint_data_complete": False,
            "ranking_v4_constraint_evidence_mode": "incomplete",
        }
    )
    snapshot = _snapshot(selection)
    start = date(2024, 9, 1)
    rows = []
    candidate_price = 10.0
    benchmark_price = 100.0
    for offset in range(90):
        trade_date = start + timedelta(days=offset)
        candidate_price *= 1.0 + (0.002 if offset % 3 else -0.001)
        benchmark_price *= 1.0 + (0.001 if offset % 3 else -0.0005)
        rows.extend(
            (
                {
                    "instrument_id": selection.instrument_id,
                    "trade_date": trade_date,
                    "close": candidate_price,
                    "adjusted_close": candidate_price,
                },
                {
                    "instrument_id": "CN:000300.IDX",
                    "trade_date": trade_date,
                    "close": benchmark_price,
                    "adjusted_close": benchmark_price,
                },
            )
        )

    class RiskProvider:
        def get_daily_bars(self, *_args, **_kwargs):
            return pd.DataFrame(rows)

    enriched, correlations = _ranking_v4_risk_evidence(
        snapshot,
        market_provider=RiskProvider(),
    )

    assert correlations == {}
    assert enriched[0].ranking_v4_return_observation_count >= 60
    assert enriched[0].ranking_v4_beta is not None
    assert enriched[0].ranking_v4_constraint_data_complete is True
    assert enriched[0].ranking_v4_constraint_evidence_mode == "return_risk_proxy"


def test_evidence_coverage_separates_grain_and_constraint_source():
    first_date = date(2025, 1, 2)
    second_date = date(2025, 1, 9)
    combined = _selection("CN:000001").model_copy(
        update={
            "ranking_v4_raw_metadata_complete": True,
            "ranking_v4_return_risk_evidence_complete": True,
            "ranking_v4_combined_constraint_evidence_complete": True,
            "ranking_v4_constraint_evidence_mode": "point_in_time_metadata",
        }
    )
    proxy = _selection("CN:000002").model_copy(
        update={
            "ranking_v4_raw_metadata_complete": False,
            "ranking_v4_return_risk_evidence_complete": True,
            "ranking_v4_combined_constraint_evidence_complete": False,
            "ranking_v4_constraint_evidence_mode": "return_risk_proxy",
        }
    )
    incomplete = _selection("CN:000001").model_copy(
        update={
            "ranking_v4_raw_metadata_complete": False,
            "ranking_v4_return_risk_evidence_complete": False,
            "ranking_v4_combined_constraint_evidence_complete": False,
            "ranking_v4_constraint_evidence_mode": "incomplete",
        }
    )
    snapshots = [
        _snapshot(combined).model_copy(
            update={
                "decision_date": first_date,
                "ranking_v4_candidate_pool": [combined, proxy],
            }
        ),
        _snapshot(incomplete).model_copy(
            update={
                "decision_date": second_date,
                "ranking_v4_candidate_pool": [incomplete],
            }
        ),
    ]

    coverage = _ranking_v4_evidence_coverage(snapshots)

    assert coverage["candidate_date_count"] == 3
    assert coverage["distinct_instrument_count"] == 2
    assert coverage["distinct_decision_date_count"] == 2
    assert coverage["raw_metadata_candidate_date_count"] == 1
    assert coverage["raw_metadata_distinct_instrument_count"] == 1
    assert coverage["raw_metadata_distinct_decision_date_count"] == 1
    assert coverage["return_risk_candidate_date_count"] == 2
    assert coverage["return_risk_distinct_instrument_count"] == 2
    assert coverage["return_risk_distinct_decision_date_count"] == 1
    assert coverage["combined_constraint_candidate_date_count"] == 1
    assert coverage["combined_constraint_distinct_instrument_count"] == 1
    assert coverage["combined_constraint_distinct_decision_date_count"] == 1
    assert coverage["constraint_mode_return_risk_proxy_candidate_date_count"] == 1
    assert coverage["constraint_mode_incomplete_candidate_date_count"] == 1


def test_gate_decomposition_reconciles_all_and_first_reasons_post_selection():
    selected_ids = {"CN:000001"}

    (
        reasons_by_instrument,
        all_reason_counts,
        first_reason_counts,
        reconciliation,
    ) = _ranking_v4_gate_decomposition(
        candidate_ids=["CN:000001", "CN:000002", "CN:000003"],
        selected_ids=selected_ids,
        scoring_reasons={
            "CN:000002": ["model_not_ready", "data_incomplete"],
        },
        portfolio_reasons={
            "CN:000002": ["beta_evidence_missing", "data_incomplete"],
            "CN:000003": ["position_limit"],
        },
    )

    assert selected_ids == {"CN:000001"}
    assert reasons_by_instrument == {
        "CN:000001": [],
        "CN:000002": [
            "model_not_ready",
            "data_incomplete",
            "beta_evidence_missing",
        ],
        "CN:000003": ["position_limit"],
    }
    assert all_reason_counts == {
        "model_not_ready": 1,
        "data_incomplete": 1,
        "beta_evidence_missing": 1,
        "position_limit": 1,
    }
    assert first_reason_counts == {
        "model_not_ready": 1,
        "position_limit": 1,
    }
    assert reconciliation == {
        "candidate_count": 3,
        "selected_candidate_count": 1,
        "blocked_candidate_count": 2,
        "selected_blocked_overlap_count": 0,
        "accounted_candidate_count": 3,
        "unaccounted_candidate_count": 0,
        "all_blocked_reason_assignment_count": 4,
        "first_blocked_reason_assignment_count": 2,
    }


def test_validation_keeps_cash_date_and_counts_missing_stress_evidence(monkeypatch):
    selection = _selection()
    snapshot = _snapshot(selection).model_copy(
        update={
            "ranking_v4_constraint_matched_baseline_top_5": [selection],
            "ranking_v4_top_5": [selection],
        }
    )
    outcome = CandidateSignalOutcome(
        snapshot_id="signal",
        instrument_id=selection.instrument_id,
        strategy_id=selection.primary_strategy_id,
        signal_date=snapshot.decision_date,
        status=CandidateOutcomeStatus.RESOLVED,
        status_detail="resolved",
        nominal_amount=Decimal("100000"),
        resolved_at=date(2025, 2, 7),
        return_pct=2.0,
    )
    normal = CandidateOutcomeLedgerResult(
        provider="historical_replay",
        start=date(2025, 1, 1),
        end=date(2025, 3, 1),
        nominal_amount=Decimal("100000"),
        outcomes=[outcome],
        status_counts={outcome.status.value: 1},
        data_health={},
    )
    stress = normal.model_copy(update={"outcomes": [], "status_counts": {}})
    cached_models = {
        snapshot.decision_date: {
            "constraint_matched_baseline": [selection],
            "ranking_v43_full": [selection],
        }
    }
    monkeypatch.setattr(
        walk_forward,
        "_ranking_v4_model_selections",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached channel selections must be reused")
        ),
    )

    baseline, challenger, completed, valid, expected = _ranking_v4_selected_return_observations(
        [snapshot],
        normal_ledger=normal,
        stress_ledger=stress,
        baseline_portfolio=_portfolio(),
        challenger_portfolio=_portfolio(),
        stress_portfolio=_portfolio(),
        model_selections_by_date=cached_models,
    )

    assert baseline[0].net_return_pct == 1.0
    assert challenger[0].net_return_pct == 1.0
    assert challenger[0].stress_net_return_pct == 1.0
    assert completed == 1
    assert valid == 2
    assert expected == 3


def test_validation_uses_capital_constrained_portfolio_returns():
    selection = _selection()
    snapshot = _snapshot(selection).model_copy(
        update={
            "ranking_v4_constraint_matched_baseline_top_5": [selection],
            "ranking_v4_top_5": [selection],
        }
    )
    outcome = CandidateSignalOutcome(
        snapshot_id="signal",
        instrument_id=selection.instrument_id,
        strategy_id=selection.primary_strategy_id,
        signal_date=snapshot.decision_date,
        status=CandidateOutcomeStatus.RESOLVED,
        status_detail="resolved",
        nominal_amount=Decimal("100000"),
        resolved_at=date(2025, 2, 7),
        return_pct=99.0,
    )
    ledger = CandidateOutcomeLedgerResult(
        provider="historical_replay",
        start=date(2025, 1, 1),
        end=date(2025, 3, 1),
        nominal_amount=Decimal("100000"),
        outcomes=[outcome],
        status_counts={outcome.status.value: 1},
        data_health={},
    )

    baseline, challenger, completed, valid, expected = _ranking_v4_selected_return_observations(
        [snapshot],
        normal_ledger=ledger,
        stress_ledger=ledger,
        baseline_portfolio=_portfolio(end_equity=Decimal("101000")),
        challenger_portfolio=_portfolio(end_equity=Decimal("102000")),
        stress_portfolio=_portfolio(end_equity=Decimal("100500")),
    )

    assert baseline[0].net_return_pct == 1.0
    assert challenger[0].net_return_pct == 2.0
    assert challenger[0].stress_net_return_pct == 0.5
    assert completed == 1
    assert valid == expected == 3
