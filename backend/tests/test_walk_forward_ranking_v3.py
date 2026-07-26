from datetime import date
from decimal import Decimal

from qagent.backtesting.portfolio import (
    CandidateOutcomeLedgerResult,
    CandidateOutcomeStatus,
    CandidateSignalOutcome,
)
from qagent.backtesting.ranking_v3 import RankingV3FeatureVector
from qagent.backtesting.walk_forward import (
    WalkForwardSelection,
    WalkForwardSnapshot,
    _apply_ranking_v3,
    _build_ranking_v3_observations,
    _ranking_v3_common_return_observations,
    _ranking_v3_historical_audit_last_decision_date,
)
from qagent.market.calendars import trading_sessions_in_range


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
        strategy_count = sum(
            item.primary_strategy_id == "same-strategy" for item in selections
        )
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
                return_pct=2.5,
            ),
            CandidateSignalOutcome(
                snapshot_id="challenger-untriggered",
                instrument_id=challenger[1].instrument_id,
                strategy_id=challenger[1].primary_strategy_id,
                signal_date=decision_date,
                status=CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE,
                status_detail="not_triggered",
                nominal_amount=Decimal("100000"),
            ),
        ],
        status_counts={
            CandidateOutcomeStatus.RESOLVED.value: 2,
            CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE.value: 1,
        },
        data_health={},
    )

    baseline_rows, challenger_rows, completed = (
        _ranking_v3_common_return_observations([snapshot], ledger=ledger)
    )

    assert len(baseline_rows) == len(challenger_rows) == 5
    assert {item.rebalance_date for item in baseline_rows} == {decision_date}
    assert {item.rebalance_date for item in challenger_rows} == {decision_date}
    assert [item.net_return_pct for item in baseline_rows] == [1.5, 0, 0, 0, 0]
    assert [item.net_return_pct for item in challenger_rows] == [2.5, 0, 0, 0, 0]
    assert completed == 1


def test_ranking_v3_validation_does_not_count_all_cash_dates_as_evidence():
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

    baseline_rows, challenger_rows, completed = (
        _ranking_v3_common_return_observations([snapshot], ledger=ledger)
    )

    assert baseline_rows == []
    assert challenger_rows == []
    assert completed == 0


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


def test_ranking_v3_observations_use_explicit_resolution_date_and_skip_censored():
    decision_date = date(2025, 1, 24)
    resolved_at = date(2025, 2, 11)
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
                status_detail="entry_not_triggered_or_fill_blocked",
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
        ],
        status_counts={},
        data_health={},
    )

    class EmptyMarketProvider:
        def get_daily_bars(self, instrument_ids, start, end):
            import pandas as pd

            return pd.DataFrame()

    observations = _build_ranking_v3_observations(
        [snapshot],
        ledger=ledger,
        market_provider=EmptyMarketProvider(),
        start=decision_date,
        end=resolved_at,
    )

    assert len(observations) == 1
    assert observations[0].available_at == resolved_at
    assert observations[0].triggered is False
