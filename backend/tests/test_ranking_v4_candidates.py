from __future__ import annotations

import pytest

from qagent.backtesting.ranking_v4 import (
    RankingV4Candidate,
    RankingV4FeatureVector,
)
from qagent.backtesting.ranking_v4_candidates import (
    BREAKOUT_STRATEGY_BONUS,
    DEFAULT_RANKING_V4_CHANNEL_QUOTAS,
    MAX_RANKING_V4_CANDIDATE_POOL_SIZE,
    RANKING_V4_CHANNEL_ORDER,
    build_preregistered_ranking_v4_candidate_pool,
    build_ranking_v4_candidate_pool,
)


def _features(**updates: float) -> RankingV4FeatureVector:
    return RankingV4FeatureVector(
        strategy_score=0.5,
        factor_score=0.5,
        valuation=0.5,
        size=0.5,
        quality=0.5,
        momentum=0.5,
        trend_quality=0.5,
        breakout_quality=0.5,
        liquidity=0.5,
        low_risk=0.5,
        risk_filter=0.5,
        reversal=0.5,
        industry_strength=0.5,
        market_breadth=0.5,
        benchmark_slope=0.5,
        realized_volatility=0.5,
        cross_sectional_dispersion=0.5,
        capacity=0.5,
        tail_risk=0.5,
        execution_penalty=0.0,
        data_completeness=1.0,
    ).model_copy(update=updates)


def _candidate(
    instrument_id: str,
    *,
    baseline: float = 0.5,
    asset_type: str = "stock",
    strategy: str | None = None,
    features: RankingV4FeatureVector | None = None,
) -> RankingV4Candidate:
    return RankingV4Candidate(
        instrument_id=instrument_id,
        baseline_rank_score=baseline,
        primary_strategy_id=strategy,
        market_regime="risk_on",
        asset_type=asset_type,
        features=features or _features(),
        market_regime_features_complete=True,
        constraint_data_complete=True,
        constraint_evidence_mode="point_in_time_metadata",
        underlying_evidence_complete=True,
    )


def _quotas(**updates: int) -> dict[str, int]:
    quotas = {channel: 0 for channel in RANKING_V4_CHANNEL_ORDER}
    quotas.update(updates)
    return quotas


def _dump(entries) -> list[dict]:
    return [entry.model_dump(mode="json") for entry in entries]


def test_candidate_pool_is_invariant_to_input_order():
    candidates = [
        _candidate(
            "CN:000001",
            baseline=0.91,
            features=_features(momentum=0.2, trend_quality=0.1),
        ),
        _candidate(
            "CN:000002",
            baseline=0.45,
            features=_features(momentum=0.9, trend_quality=0.95),
        ),
        _candidate(
            "CN:000003",
            baseline=0.53,
            strategy="breakout_volume_confirmation",
            features=_features(breakout_quality=0.96, liquidity=0.91),
        ),
        _candidate(
            "CN:588000",
            baseline=0.58,
            asset_type="ETF",
            features=_features(industry_strength=0.93, trend_quality=0.88),
        ),
    ]

    forward = build_ranking_v4_candidate_pool(candidates, limit=4)
    reversed_input = build_ranking_v4_candidate_pool(
        list(reversed(candidates)),
        limit=4,
    )

    assert _dump(forward) == _dump(reversed_input)


def test_channel_quotas_are_ranked_independently_before_union():
    candidates = [
        _candidate(
            "CN:BASELINE-1",
            baseline=0.99,
            features=_features(momentum=0.1, trend_quality=0.1),
        ),
        _candidate(
            "CN:BASELINE-2",
            baseline=0.98,
            features=_features(momentum=0.2, trend_quality=0.2),
        ),
        _candidate(
            "CN:TREND-1",
            baseline=0.10,
            features=_features(
                momentum=1.0,
                trend_quality=1.0,
                industry_strength=1.0,
            ),
        ),
        _candidate(
            "CN:TREND-2",
            baseline=0.20,
            features=_features(
                momentum=0.9,
                trend_quality=0.9,
                industry_strength=0.9,
            ),
        ),
    ]

    entries = build_ranking_v4_candidate_pool(
        candidates,
        quotas=_quotas(baseline=2, trend=2),
        limit=4,
    )

    assert [entry.candidate.instrument_id for entry in entries] == [
        "CN:BASELINE-1",
        "CN:BASELINE-2",
        "CN:TREND-1",
        "CN:TREND-2",
    ]
    assert [entry.primary_channel for entry in entries] == [
        "baseline",
        "baseline",
        "trend",
        "trend",
    ]
    assert [entry.channel_position for entry in entries] == [1, 2, 1, 2]


def test_candidate_selected_by_multiple_channels_is_emitted_once():
    shared = _candidate(
        "CN:SHARED",
        baseline=1.0,
        features=_features(
            momentum=1.0,
            trend_quality=1.0,
            industry_strength=1.0,
        ),
    )
    entries = build_ranking_v4_candidate_pool(
        [
            shared,
            _candidate("CN:BASELINE", baseline=0.9),
            _candidate(
                "CN:TREND",
                baseline=0.1,
                features=_features(
                    momentum=0.9,
                    trend_quality=0.9,
                    industry_strength=0.9,
                ),
            ),
        ],
        quotas=_quotas(baseline=2, trend=2),
        limit=3,
    )

    shared_entries = [entry for entry in entries if entry.candidate.instrument_id == "CN:SHARED"]
    assert len(shared_entries) == 1
    assert shared_entries[0].primary_channel == "baseline"
    assert shared_entries[0].matched_channels == ("baseline", "trend")
    assert len({entry.candidate.instrument_id for entry in entries}) == len(entries)


def test_etf_industry_channel_never_contains_stock_candidates():
    stock = _candidate(
        "CN:STOCK",
        baseline=1.0,
        features=_features(
            trend_quality=1.0,
            industry_strength=1.0,
            liquidity=1.0,
        ),
    )
    etf_one = _candidate(
        "CN:ETF-1",
        baseline=0.2,
        asset_type="ETF",
        features=_features(
            trend_quality=0.8,
            industry_strength=0.8,
            liquidity=0.8,
        ),
    )
    etf_two = _candidate(
        "CN:ETF-2",
        baseline=0.1,
        asset_type="index_fund",
        features=_features(
            trend_quality=0.7,
            industry_strength=0.7,
            liquidity=0.7,
        ),
    )

    etf_only = build_ranking_v4_candidate_pool(
        [stock, etf_two, etf_one],
        quotas=_quotas(etf_industry=2),
        limit=2,
    )
    mixed = build_ranking_v4_candidate_pool(
        [stock, etf_two, etf_one],
        quotas=_quotas(baseline=1, etf_industry=1),
        limit=2,
    )

    assert [entry.candidate.instrument_id for entry in etf_only] == [
        "CN:ETF-1",
        "CN:ETF-2",
    ]
    stock_entry = next(entry for entry in mixed if entry.candidate.instrument_id == "CN:STOCK")
    assert "etf_industry" not in stock_entry.channel_scores
    assert "etf_industry" not in stock_entry.matched_channels


def test_pool_backfills_by_best_channel_score_then_baseline_then_id():
    candidates = [
        _candidate(
            "CN:BEST-FEATURE",
            baseline=0.4,
            features=_features(
                quality=1.0,
                valuation=1.0,
                low_risk=1.0,
            ),
        ),
        _candidate(
            "CN:BEST-BASELINE",
            baseline=0.9,
            features=_features(
                quality=0.1,
                valuation=0.1,
                low_risk=0.1,
                momentum=0.1,
                trend_quality=0.1,
                breakout_quality=0.1,
                liquidity=0.1,
                risk_filter=0.1,
                realized_volatility=0.9,
                industry_strength=0.1,
            ),
        ),
        _candidate(
            "CN:TIE-A",
            baseline=0.7,
            features=_features(
                momentum=0.8,
                trend_quality=0.8,
                industry_strength=0.8,
            ),
        ),
        _candidate(
            "CN:TIE-B",
            baseline=0.7,
            features=_features(
                momentum=0.8,
                trend_quality=0.8,
                industry_strength=0.8,
            ),
        ),
    ]

    entries = build_ranking_v4_candidate_pool(
        candidates,
        quotas=_quotas(),
        limit=4,
    )

    assert [entry.candidate.instrument_id for entry in entries] == [
        "CN:BEST-FEATURE",
        "CN:BEST-BASELINE",
        "CN:TIE-A",
        "CN:TIE-B",
    ]
    assert entries[0].primary_channel == "quality_value"
    assert entries[0].matched_channels == ("quality_value",)


def test_default_pool_is_capped_at_fifty_unique_candidates():
    candidates = [
        _candidate(
            f"CN:{index:06d}",
            baseline=(70 - index) / 70,
            features=_features(momentum=(index + 1) / 70),
        )
        for index in range(70)
    ]

    entries = build_ranking_v4_candidate_pool(candidates)

    assert DEFAULT_RANKING_V4_CHANNEL_QUOTAS == {
        "baseline": 10,
        "trend": 8,
        "breakout": 8,
        "quality_value": 8,
        "defensive_low_vol": 8,
        "etf_industry": 8,
    }
    assert len(entries) == MAX_RANKING_V4_CANDIDATE_POOL_SIZE
    assert len({entry.candidate.instrument_id for entry in entries}) == 50


def test_preregistered_pool_always_uses_frozen_protocol_configuration():
    candidates = [
        _candidate(
            f"CN:{index:06d}",
            baseline=(70 - index) / 70,
            features=_features(momentum=(index + 1) / 70),
        )
        for index in range(70)
    ]

    entries = build_preregistered_ranking_v4_candidate_pool(candidates)

    assert _dump(entries) == _dump(
        build_ranking_v4_candidate_pool(
            candidates,
            quotas=DEFAULT_RANKING_V4_CHANNEL_QUOTAS,
            limit=MAX_RANKING_V4_CANDIDATE_POOL_SIZE,
        )
    )
    assert len(entries) == 50


def test_channel_scores_use_frozen_formulas_and_bounded_breakout_bonus():
    candidate = _candidate(
        "CN:SCORES",
        baseline=0.73,
        asset_type="fund",
        strategy="breakout_volume_confirmation",
        features=_features(
            momentum=0.8,
            trend_quality=0.6,
            industry_strength=0.4,
            breakout_quality=0.9,
            liquidity=0.7,
            quality=0.75,
            valuation=0.65,
            low_risk=0.55,
            risk_filter=0.45,
            realized_volatility=0.35,
        ),
    )

    entry = build_ranking_v4_candidate_pool(
        [candidate],
        quotas=_quotas(baseline=1),
        limit=1,
    )[0]

    assert entry.channel_scores["baseline"] == pytest.approx(0.73)
    assert entry.channel_scores["trend"] == pytest.approx(0.64)
    assert entry.channel_scores["breakout"] == pytest.approx(
        min(1.0, 0.45 * 0.9 + 0.30 * 0.8 + 0.25 * 0.7 + BREAKOUT_STRATEGY_BONUS)
    )
    assert entry.channel_scores["quality_value"] == pytest.approx(0.665)
    assert entry.channel_scores["defensive_low_vol"] == pytest.approx(0.58)
    assert entry.channel_scores["etf_industry"] == pytest.approx(0.56)


@pytest.mark.parametrize("limit", [0, -1, 51, True, 1.5])
def test_invalid_limits_are_rejected(limit):
    with pytest.raises(ValueError, match="limit"):
        build_ranking_v4_candidate_pool([], limit=limit)


@pytest.mark.parametrize(
    "quotas",
    [
        {"not_a_channel": 1},
        {"baseline": -1},
        {"baseline": 51},
        {"baseline": True},
        {"baseline": 1.5},
        [("baseline", 1)],
    ],
)
def test_invalid_quotas_are_rejected(quotas):
    with pytest.raises(ValueError, match="channel|quota"):
        build_ranking_v4_candidate_pool([], quotas=quotas)


def test_duplicate_instrument_ids_are_rejected_deterministically():
    first = _candidate("CN:DUPLICATE", baseline=0.2)
    second = _candidate("CN:DUPLICATE", baseline=0.9)

    with pytest.raises(ValueError) as forward_error:
        build_ranking_v4_candidate_pool([first, second], limit=1)
    with pytest.raises(ValueError) as reverse_error:
        build_ranking_v4_candidate_pool([second, first], limit=1)

    assert str(forward_error.value) == str(reverse_error.value)
    assert "CN:DUPLICATE" in str(forward_error.value)
