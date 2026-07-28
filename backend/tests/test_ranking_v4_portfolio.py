from datetime import date

import pytest

from qagent.backtesting import ranking_v4_portfolio as portfolio_module
from qagent.backtesting.ranking_v4 import (
    RankingV4Candidate,
    RankingV4CandidateScore,
    RankingV4Decision,
    RankingV4FeatureVector,
)
from qagent.backtesting.ranking_v4_portfolio import select_ranking_v4_portfolio


def _candidate(
    instrument_id: str,
    *,
    strategy: str = "trend",
    industry: str = "电子",
    themes: list[str] | None = None,
    factors: list[str] | None = None,
    asset_type: str = "stock",
    underlying_ids: list[str] | None = None,
    index_memberships: list[str] | None = None,
    beta: float | None = 1.0,
    liquidity: float = 0.8,
    capacity: float = 0.8,
) -> RankingV4Candidate:
    exposures = {} if beta is None else {"beta": beta}
    return RankingV4Candidate(
        instrument_id=instrument_id,
        baseline_rank_score=0.8,
        primary_strategy_id=strategy,
        factor_signals=factors or [],
        market_regime="risk_on",
        asset_type=asset_type,
        industry=industry,
        themes=themes or [],
        underlying_ids=underlying_ids or [],
        index_memberships=index_memberships or [],
        factor_exposures=exposures,
        features=RankingV4FeatureVector(
            liquidity=liquidity,
            capacity=capacity,
            data_completeness=1.0,
        ),
        market_regime_features_complete=True,
        constraint_data_complete=True,
        constraint_evidence_mode="point_in_time_metadata",
        underlying_evidence_complete=True,
    )


def _score(instrument_id: str, position: int, utility: float = 1.0):
    return RankingV4CandidateScore(
        instrument_id=instrument_id,
        baseline_position=position,
        v4_position=position,
        baseline_rank_score=0.8,
        trigger_probability=0.8,
        trigger_probability_lower_bound=0.7,
        expected_triggered_net_excess_return_pct=2.0,
        expected_triggered_net_excess_lower_bound_pct=1.0,
        expected_utility_pct=utility + 0.5,
        expected_utility_lower_bound_pct=utility,
        win_probability=0.6,
        win_probability_lower_bound=0.52,
        replacement_cost_pct=0.0,
        benchmark_opportunity_cost_pct=0.0,
        liquidity_penalty_pct=0.0,
        tail_risk_penalty_pct=0.0,
        evidence_segment="global",
        evidence_date_count=30,
        eligible_for_position=True,
        reason="eligible",
    )


def _decision(
    candidates: list[RankingV4Candidate],
    *,
    utilities: dict[str, float] | None = None,
) -> RankingV4Decision:
    return RankingV4Decision(
        decision_date=date(2025, 1, 2),
        training_observation_count=120,
        training_triggered_observation_count=100,
        training_date_count=30,
        model_ready=True,
        eligible_position_count=len(candidates),
        cash_slot_count=max(5 - len(candidates), 0),
        candidates=[
            _score(
                candidate.instrument_id,
                position,
                (utilities or {}).get(candidate.instrument_id, 1.0),
            )
            for position, candidate in enumerate(candidates, start=1)
        ],
    )


def _correlations(candidates: list[RankingV4Candidate], value: float = 0.2):
    return {
        (left.instrument_id, right.instrument_id): value
        for index, left in enumerate(candidates)
        for right in candidates[index + 1 :]
    }


def test_v4_portfolio_allows_zero_to_five_positions_and_cash():
    candidates = [
        _candidate(
            f"CN:{index:06d}",
            strategy=f"strategy-{index}",
            industry=f"industry-{index}",
        )
        for index in range(3)
    ]
    result = select_ranking_v4_portfolio(
        _decision(candidates),
        candidates,
        pairwise_correlations=_correlations(candidates),
    )

    assert result.selected_count == 3
    assert result.cash_slot_count == 2
    assert [item.position for item in result.selected] == [1, 2, 3]


def test_v4_portfolio_fails_closed_for_missing_beta_or_correlation():
    candidates = [
        _candidate("CN:000001"),
        _candidate("CN:000002", beta=None),
        _candidate("CN:000003"),
    ]
    result = select_ranking_v4_portfolio(
        _decision(candidates),
        candidates,
        pairwise_correlations={},
    )
    blocked = {item.instrument_id: item.reasons for item in result.blocked}

    assert result.selected_count == 1
    assert "beta_evidence_missing" in blocked["CN:000002"]
    assert "correlation_evidence_missing" in blocked["CN:000003"]


def test_v4_portfolio_blocks_concentration_and_high_correlation():
    candidates = [
        _candidate("CN:000001", strategy="trend", industry="电子", themes=["AI"]),
        _candidate("CN:000002", strategy="trend", industry="电子", themes=["AI"]),
        _candidate("CN:000003", strategy="trend", industry="电子", themes=["AI"]),
        _candidate("CN:000004", strategy="quality", industry="医药"),
    ]
    correlations = _correlations(candidates)
    correlations[("CN:000001", "CN:000004")] = 0.9
    result = select_ranking_v4_portfolio(
        _decision(candidates),
        candidates,
        pairwise_correlations=correlations,
    )
    blocked = {item.instrument_id: item.reasons for item in result.blocked}

    assert [item.instrument_id for item in result.selected] == [
        "CN:000002",
        "CN:000003",
        "CN:000004",
    ]
    assert "strategy_concentration" in blocked["CN:000001"]
    assert "industry_concentration" in blocked["CN:000001"]
    assert "theme_concentration" in blocked["CN:000001"]
    assert "pairwise_correlation" in blocked["CN:000001"]


def test_v4_portfolio_blocks_stock_etf_overlap():
    candidates = [
        _candidate("CN:000001"),
        _candidate(
            "CN:510001",
            strategy="etf-one",
            industry="ETF",
            asset_type="etf",
            underlying_ids=["CN:000001", "CN:000002"],
            index_memberships=["CN:000300.IDX"],
        ),
        _candidate(
            "CN:510002",
            strategy="etf-two",
            industry="ETF",
            asset_type="etf",
            underlying_ids=["CN:000003"],
            index_memberships=["CN:000300.IDX"],
        ),
    ]
    result = select_ranking_v4_portfolio(
        _decision(candidates),
        candidates,
        pairwise_correlations=_correlations(candidates),
    )
    blocked = {item.instrument_id: item.reasons for item in result.blocked}

    assert "stock_etf_underlying_overlap" in blocked["CN:510001"]
    assert result.selected_count == 2


def test_v4_portfolio_allows_zero_shared_etf_underlyings_or_indexes():
    candidates = [
        _candidate(
            "CN:510001",
            strategy="etf-one",
            industry="ETF",
            asset_type="etf",
            underlying_ids=["CN:000001"],
            index_memberships=["CN:000300.IDX"],
        ),
        _candidate(
            "CN:510002",
            strategy="etf-two",
            industry="ETF",
            asset_type="etf",
            underlying_ids=["CN:000001"],
            index_memberships=["CN:000905.IDX"],
        ),
        _candidate(
            "CN:510003",
            strategy="etf-three",
            industry="ETF",
            asset_type="etf",
            underlying_ids=["CN:000003"],
            index_memberships=["CN:000300.IDX"],
        ),
    ]
    result = select_ranking_v4_portfolio(
        _decision(candidates),
        candidates,
        pairwise_correlations=_correlations(candidates),
    )
    blocked = {item.instrument_id: item.reasons for item in result.blocked}

    assert [item.instrument_id for item in result.selected] == [
        "CN:510002",
        "CN:510003",
    ]
    assert "etf_underlying_overlap" in blocked["CN:510001"]
    assert "etf_index_overlap" in blocked["CN:510001"]


def test_v4_portfolio_blocks_low_liquidity_capacity_and_beta():
    candidates = [
        _candidate("CN:000001", beta=1.1),
        _candidate("CN:000002", liquidity=0.49),
        _candidate("CN:000003", capacity=0.49),
        _candidate("CN:000004", beta=1.5),
    ]
    result = select_ranking_v4_portfolio(
        _decision(candidates),
        candidates,
        pairwise_correlations=_correlations(candidates),
    )
    blocked = {item.instrument_id: item.reasons for item in result.blocked}

    assert "liquidity_below_minimum" in blocked["CN:000002"]
    assert "capacity_below_minimum" in blocked["CN:000003"]
    assert "portfolio_beta" in blocked["CN:000004"]


def test_v4_portfolio_fails_closed_for_missing_constraint_evidence():
    stock = _candidate("CN:000001").model_copy(
        update={
            "constraint_data_complete": False,
            "constraint_evidence_mode": "incomplete",
        }
    )
    etf = _candidate(
        "CN:510001",
        asset_type="etf",
        underlying_ids=["CN:000002"],
    ).model_copy(
        update={
            "underlying_evidence_complete": False,
            "constraint_evidence_mode": "return_risk_proxy",
        }
    )

    result = select_ranking_v4_portfolio(
        _decision([stock, etf]),
        [stock, etf],
        pairwise_correlations={},
    )
    blocked = {item.instrument_id: item.reasons for item in result.blocked}

    assert result.selected_count == 1
    assert result.selected[0].instrument_id == "CN:510001"
    assert "constraint_evidence_incomplete" in blocked["CN:000001"]


def test_v41_etf_without_constituents_requires_return_correlation_for_second_position():
    first = _candidate(
        "CN:510001",
        asset_type="etf",
        underlying_ids=[],
    ).model_copy(
        update={
            "underlying_evidence_complete": False,
            "constraint_evidence_mode": "return_risk_proxy",
        }
    )
    second = _candidate(
        "CN:510002",
        asset_type="etf",
        underlying_ids=[],
    ).model_copy(
        update={
            "underlying_evidence_complete": False,
            "constraint_evidence_mode": "return_risk_proxy",
        }
    )

    missing = select_ranking_v4_portfolio(
        _decision([first, second]),
        [first, second],
        pairwise_correlations={},
    )
    proven = select_ranking_v4_portfolio(
        _decision([first, second]),
        [first, second],
        pairwise_correlations={(first.instrument_id, second.instrument_id): 0.4},
    )

    assert missing.selected_count == 1
    assert (
        "correlation_evidence_missing"
        in {item.instrument_id: item.reasons for item in missing.blocked}[second.instrument_id]
    )
    assert proven.selected_count == 2


def test_v4_portfolio_finds_global_optimum_that_greedy_misses():
    candidates = [
        _candidate("CN:000001", strategy="strategy-a", industry="industry-a"),
        _candidate("CN:000002", strategy="strategy-b", industry="industry-b"),
        _candidate("CN:000003", strategy="strategy-c", industry="industry-c"),
    ]
    correlations = {
        ("CN:000001", "CN:000002"): 0.90,
        ("CN:000001", "CN:000003"): 0.90,
        ("CN:000002", "CN:000003"): 0.20,
    }
    result = select_ranking_v4_portfolio(
        _decision(
            candidates,
            utilities={
                "CN:000001": 10.0,
                "CN:000002": 6.0,
                "CN:000003": 6.0,
            },
        ),
        candidates,
        pairwise_correlations=correlations,
        maximum_positions=2,
    )

    assert [item.instrument_id for item in result.selected] == [
        "CN:000002",
        "CN:000003",
    ]
    assert sum(item.expected_utility_lower_bound_pct for item in result.selected) == 12.0


def test_v4_portfolio_evaluates_beta_on_the_complete_combination():
    candidates = [
        _candidate(
            "CN:000001",
            strategy="strategy-a",
            industry="industry-a",
            beta=1.5,
        ),
        _candidate(
            "CN:000002",
            strategy="strategy-b",
            industry="industry-b",
            beta=0.5,
        ),
    ]
    result = select_ranking_v4_portfolio(
        _decision(
            candidates,
            utilities={"CN:000001": 10.0, "CN:000002": 1.0},
        ),
        candidates,
        pairwise_correlations=_correlations(candidates),
        maximum_positions=2,
    )

    assert [item.instrument_id for item in result.selected] == [
        "CN:000001",
        "CN:000002",
    ]
    assert result.average_beta == 1.0


def test_v4_portfolio_fails_closed_for_non_positive_utility():
    candidate = _candidate("CN:000001")
    result = select_ranking_v4_portfolio(
        _decision([candidate], utilities={"CN:000001": 0.0}),
        [candidate],
        pairwise_correlations={},
    )

    assert result.selected_count == 0
    assert result.blocked[0].reasons == ["conservative_utility_not_positive"]


def test_v4_portfolio_deterministically_handles_fifty_candidates():
    candidates = [
        _candidate(
            f"CN:{index:06d}",
            strategy=f"strategy-{index}",
            industry=f"industry-{index}",
        )
        for index in range(1, 51)
    ]
    utilities = {
        candidate.instrument_id: float(101 - index)
        for index, candidate in enumerate(candidates, start=1)
    }
    decision = _decision(candidates, utilities=utilities)
    correlations = _correlations(candidates)

    first = select_ranking_v4_portfolio(
        decision,
        candidates,
        pairwise_correlations=correlations,
    )
    second = select_ranking_v4_portfolio(
        decision,
        list(reversed(candidates)),
        pairwise_correlations=correlations,
    )

    expected = [f"CN:{index:06d}" for index in range(1, 6)]
    assert [item.instrument_id for item in first.selected] == expected
    assert [item.instrument_id for item in second.selected] == expected


def test_v4_portfolio_precomputes_each_pair_once(monkeypatch):
    candidates = [
        _candidate(
            f"CN:{index:06d}",
            strategy=f"strategy-{index}",
            industry=f"industry-{index}",
            themes=[f"theme-{index}"],
            factors=[f"factor-{index}"],
        )
        for index in range(1, 51)
    ]
    pair_calls = 0
    original = portfolio_module._pair_is_compatible

    def counted(*args, **kwargs):
        nonlocal pair_calls
        pair_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(portfolio_module, "_pair_is_compatible", counted)
    result = select_ranking_v4_portfolio(
        _decision(candidates),
        candidates,
        pairwise_correlations=_correlations(candidates, value=0.9),
    )

    assert result.selected_count == 1
    assert pair_calls == 50 * 49 // 2


def test_v4_portfolio_rejects_duplicates_and_invalid_limit():
    candidate = _candidate("CN:000001")
    with pytest.raises(ValueError, match="duplicate"):
        select_ranking_v4_portfolio(
            _decision([candidate]),
            [candidate, candidate],
            pairwise_correlations={},
        )
    with pytest.raises(ValueError, match="between"):
        select_ranking_v4_portfolio(
            _decision([candidate]),
            [candidate],
            pairwise_correlations={},
            maximum_positions=6,
        )
