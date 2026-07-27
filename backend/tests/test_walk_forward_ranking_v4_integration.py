from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

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
    _ranking_v4_baseline_decision,
    _ranking_v4_beta,
    _ranking_v4_correlation,
    _ranking_v4_market_features,
    _ranking_v4_return_series,
    _ranking_v4_selected_return_observations,
)
from qagent.historical_evidence.models import (
    HistoricalIndexMembership,
    HistoricalIndustrySnapshot,
)


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
            return {selection.instrument_id: object()}

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


def test_validation_keeps_cash_date_and_counts_missing_stress_evidence():
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

    baseline, challenger, completed, valid, expected = _ranking_v4_selected_return_observations(
        [snapshot],
        normal_ledger=normal,
        stress_ledger=stress,
        baseline_portfolio=_portfolio(),
        challenger_portfolio=_portfolio(),
        stress_portfolio=_portfolio(),
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
