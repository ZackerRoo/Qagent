from datetime import date, timedelta

import pandas as pd
import pytest
from pydantic import ValidationError

from qagent.factors.engine import (
    RESEARCH_FACTOR_WEIGHTS,
    build_factor_feature_snapshot,
    build_factor_rankings,
    rerank_factor_rankings,
)
from qagent.features import build_feature_snapshot


def _bars(
    instrument_id: str,
    closes: list[float],
    *,
    volume: int,
) -> pd.DataFrame:
    start = date(2026, 1, 1)
    return pd.DataFrame(
        [
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": volume + index * 100,
                "provider": "fixture",
            }
            for index, close in enumerate(closes)
        ]
    )


def _ranking_summary(rankings):
    return [
        (
            ranking.instrument_id,
            ranking.factor_score,
            ranking.factor_rank,
            ranking.percentile,
            tuple(
                (exposure.factor_id, exposure.raw_value, exposure.score)
                for exposure in ranking.factor_exposures
                if exposure.factor_id not in RESEARCH_FACTOR_WEIGHTS
            ),
        )
        for ranking in rankings
    ]


def test_feature_snapshot_is_deeply_immutable_and_digest_is_order_invariant():
    first = build_feature_snapshot(
        as_of=date(2026, 6, 30),
        feature_set_version="factor-v2",
        dataset_revision=7,
        universe_ids=["CN:000002", "CN:000001"],
        raw_scores={
            "CN:000002": {"momentum": 0.2, "quality": None},
            "CN:000001": {"momentum": 0.1, "quality": 0.8},
        },
        cross_sectional_scores={
            "CN:000002": {"momentum": 1.0, "quality": 0.35},
            "CN:000001": {"momentum": 0.0, "quality": 0.5},
        },
        input_metadata={"provider": "fixture"},
    )
    reordered = build_feature_snapshot(
        as_of=date(2026, 6, 30),
        feature_set_version="factor-v2",
        dataset_revision=7,
        universe_ids=["CN:000001", "CN:000002"],
        raw_scores={
            "CN:000001": {"quality": 0.8, "momentum": 0.1},
            "CN:000002": {"quality": None, "momentum": 0.2},
        },
        cross_sectional_scores={
            "CN:000001": {"quality": 0.5, "momentum": 0.0},
            "CN:000002": {"quality": 0.35, "momentum": 1.0},
        },
        input_metadata={"provider": "fixture"},
    )

    assert first.universe_digest == reordered.universe_digest
    assert first.input_digest == reordered.input_digest
    assert first.model_dump(mode="json") == reordered.model_dump(mode="json")
    assert first.coverage == {"momentum": 1.0, "quality": 0.5, "overall": 0.75}

    with pytest.raises(ValidationError):
        first.feature_set_version = "changed"
    with pytest.raises(TypeError):
        first.raw_scores["CN:000001"]["momentum"] = 0.9
    with pytest.raises(TypeError):
        first.coverage["overall"] = 0.0


def test_global_factor_ranking_is_independent_of_batch_size_and_order():
    frames = {
        "CN:000001": _bars(
            "CN:000001",
            [10 + index * 0.08 for index in range(140)],
            volume=2_000_000,
        ),
        "CN:000002": _bars(
            "CN:000002",
            [11 + index * 0.035 for index in range(140)],
            volume=1_300_000,
        ),
        "CN:000003": _bars(
            "CN:000003",
            [18 - index * 0.035 for index in range(140)],
            volume=800_000,
        ),
        "CN:000004": _bars(
            "CN:000004",
            [12 + ((-1) ** index) * 0.7 + index * 0.01 for index in range(140)],
            volume=500_000,
        ),
    }
    universe = list(frames)
    one_pass = build_factor_rankings(pd.concat(frames.values(), ignore_index=True))

    single_symbol_batches = [
        ranking
        for instrument_id in reversed(universe)
        for ranking in reversed(build_factor_rankings(frames[instrument_id]))
    ]
    two_symbol_batches = [
        *build_factor_rankings(
            pd.concat([frames["CN:000003"], frames["CN:000001"]], ignore_index=True)
        ),
        *build_factor_rankings(
            pd.concat([frames["CN:000004"], frames["CN:000002"]], ignore_index=True)
        ),
    ]

    reranked_single = rerank_factor_rankings(
        single_symbol_batches,
        instrument_ids=reversed(universe),
    )
    reranked_pairs = rerank_factor_rankings(
        reversed(two_symbol_batches),
        instrument_ids=["CN:000002", "CN:000004", "CN:000001", "CN:000003"],
    )

    assert _ranking_summary(reranked_single) == _ranking_summary(one_pass)
    assert _ranking_summary(reranked_pairs) == _ranking_summary(one_pass)

    first_snapshot = build_factor_feature_snapshot(
        reranked_single,
        as_of=date(2026, 6, 30),
        dataset_revision="fixture:2026-06-30",
        instrument_ids=universe,
    )
    reordered_snapshot = build_factor_feature_snapshot(
        reversed(reranked_pairs),
        as_of=date(2026, 6, 30),
        dataset_revision="fixture:2026-06-30",
        instrument_ids=reversed(universe),
    )
    assert first_snapshot.universe_digest == reordered_snapshot.universe_digest
    assert first_snapshot.input_digest == reordered_snapshot.input_digest
    assert first_snapshot.cross_sectional_scores == reordered_snapshot.cross_sectional_scores
    assert not (
        set(RESEARCH_FACTOR_WEIGHTS)
        & set(first_snapshot.cross_sectional_scores["CN:000001"])
    )


def test_factor_ranking_uses_instrument_id_as_deterministic_tie_breaker():
    same_path = [10 + index * 0.04 for index in range(140)]
    bars = pd.concat(
        [
            _bars("CN:000002", same_path, volume=1_000_000),
            _bars("CN:000001", same_path, volume=1_000_000),
        ],
        ignore_index=True,
    )

    rankings = build_factor_rankings(bars)

    assert rankings[0].factor_score == rankings[1].factor_score
    assert [ranking.instrument_id for ranking in rankings] == ["CN:000001", "CN:000002"]
    assert [ranking.factor_rank for ranking in rankings] == [1, 2]
