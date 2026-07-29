from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from qagent.backtesting.ranking_v4 import RankingV4Candidate


RankingV4Channel = Literal[
    "baseline",
    "trend",
    "breakout",
    "quality_value",
    "defensive_low_vol",
    "etf_industry",
]

RANKING_V4_CHANNEL_ORDER: tuple[RankingV4Channel, ...] = (
    "baseline",
    "trend",
    "breakout",
    "quality_value",
    "defensive_low_vol",
    "etf_industry",
)
DEFAULT_RANKING_V4_CHANNEL_QUOTAS: dict[RankingV4Channel, int] = {
    "baseline": 10,
    "trend": 8,
    "breakout": 8,
    "quality_value": 8,
    "defensive_low_vol": 8,
    "etf_industry": 8,
}
MAX_RANKING_V4_CANDIDATE_POOL_SIZE = 50
RANKING_V43_STOCK_POOL_LIMIT = 42
RANKING_V43_ETF_POOL_LIMIT = 8
BREAKOUT_STRATEGY_BONUS = 0.08
_STOCK_ASSET_TYPES = frozenset({"stock", "equity", "1"})
_ETF_ASSET_TYPES = frozenset({"etf", "fund", "index_fund", "5"})


class RankingV4CandidatePoolEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: RankingV4Candidate
    primary_channel: RankingV4Channel
    matched_channels: tuple[RankingV4Channel, ...]
    channel_scores: dict[RankingV4Channel, float]
    channel_position: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_channel_metadata(self) -> RankingV4CandidatePoolEntry:
        if not self.matched_channels:
            raise ValueError("matched_channels cannot be empty")
        if self.primary_channel not in self.matched_channels:
            raise ValueError("primary_channel must be included in matched_channels")
        if self.primary_channel not in self.channel_scores:
            raise ValueError("primary_channel must have a channel score")
        expected_order = tuple(
            channel for channel in RANKING_V4_CHANNEL_ORDER if channel in self.matched_channels
        )
        if self.matched_channels != expected_order:
            raise ValueError("matched_channels must follow the frozen channel order")
        return self


def build_preregistered_ranking_v4_candidate_pool(
    candidates: list[RankingV4Candidate],
) -> list[RankingV4CandidatePoolEntry]:
    """Build the production-comparable V4 pool with the frozen protocol settings."""

    stocks = [
        candidate
        for candidate in candidates
        if _normalized_asset_type(candidate.asset_type) == "stock"
    ]
    etfs = [
        candidate
        for candidate in candidates
        if _normalized_asset_type(candidate.asset_type) == "etf"
    ]
    stock_entries = build_ranking_v4_candidate_pool(
        stocks,
        quotas=DEFAULT_RANKING_V4_CHANNEL_QUOTAS,
        limit=min(RANKING_V43_STOCK_POOL_LIMIT, max(len(stocks), 1)),
    ) if stocks else []
    etf_entries = build_ranking_v4_candidate_pool(
        etfs,
        quotas=DEFAULT_RANKING_V4_CHANNEL_QUOTAS,
        limit=min(RANKING_V43_ETF_POOL_LIMIT, max(len(etfs), 1)),
    ) if etfs else []
    return [
        *stock_entries,
        *etf_entries,
    ]


def _normalized_asset_type(asset_type: str) -> str:
    normalized = asset_type.strip().lower().replace("-", "_")
    if normalized in _STOCK_ASSET_TYPES:
        return "stock"
    if normalized in _ETF_ASSET_TYPES:
        return "etf"
    return "unknown"


def build_ranking_v4_candidate_pool(
    candidates: list[RankingV4Candidate],
    *,
    quotas: Mapping[str, int] | None = None,
    limit: int = MAX_RANKING_V4_CANDIDATE_POOL_SIZE,
) -> list[RankingV4CandidatePoolEntry]:
    """Build the frozen multi-channel Ranking V4 candidate union.

    Each channel is ranked independently. Its quota is applied before the
    channel lists are unioned in ``RANKING_V4_CHANNEL_ORDER``. Remaining pool
    slots are filled by the candidate's best eligible channel score, baseline
    score, and instrument id. No field produced after the decision date is
    consulted by this module.
    """

    effective_quotas = _validate_pool_configuration(quotas=quotas, limit=limit)
    candidates_by_id = _validate_and_index_candidates(candidates)
    if not candidates_by_id:
        return []

    scores_by_id = {
        instrument_id: _channel_scores(candidate)
        for instrument_id, candidate in candidates_by_id.items()
    }
    rankings = {
        channel: _rank_channel(
            channel,
            candidates_by_id=candidates_by_id,
            scores_by_id=scores_by_id,
        )
        for channel in RANKING_V4_CHANNEL_ORDER
    }
    positions = {
        channel: {
            instrument_id: position for position, instrument_id in enumerate(ranking, start=1)
        }
        for channel, ranking in rankings.items()
    }
    quota_members = {
        channel: set(rankings[channel][: effective_quotas[channel]])
        for channel in RANKING_V4_CHANNEL_ORDER
    }
    quota_matches = {
        instrument_id: tuple(
            channel
            for channel in RANKING_V4_CHANNEL_ORDER
            if instrument_id in quota_members[channel]
        )
        for instrument_id in candidates_by_id
    }

    selected_ids: list[str] = []
    selected_set: set[str] = set()
    for channel in RANKING_V4_CHANNEL_ORDER:
        for instrument_id in rankings[channel][: effective_quotas[channel]]:
            if instrument_id in selected_set:
                continue
            selected_ids.append(instrument_id)
            selected_set.add(instrument_id)
            if len(selected_ids) == limit:
                break
        if len(selected_ids) == limit:
            break

    backfill_primary: dict[str, RankingV4Channel] = {}
    if len(selected_ids) < min(limit, len(candidates_by_id)):
        remaining = [
            instrument_id for instrument_id in candidates_by_id if instrument_id not in selected_set
        ]
        remaining.sort(
            key=lambda instrument_id: (
                -_best_channel_score(scores_by_id[instrument_id]),
                -float(candidates_by_id[instrument_id].baseline_rank_score),
                instrument_id,
            )
        )
        for instrument_id in remaining:
            primary_channel = _best_channel(scores_by_id[instrument_id])
            backfill_primary[instrument_id] = primary_channel
            selected_ids.append(instrument_id)
            selected_set.add(instrument_id)
            if len(selected_ids) == limit:
                break

    entries: list[RankingV4CandidatePoolEntry] = []
    for instrument_id in selected_ids:
        matched_channels = quota_matches[instrument_id]
        if matched_channels:
            primary_channel = matched_channels[0]
        else:
            primary_channel = backfill_primary[instrument_id]
            matched_channels = (primary_channel,)
        entries.append(
            RankingV4CandidatePoolEntry(
                candidate=candidates_by_id[instrument_id],
                primary_channel=primary_channel,
                matched_channels=matched_channels,
                channel_scores={
                    channel: round(score, 8)
                    for channel, score in scores_by_id[instrument_id].items()
                },
                channel_position=positions[primary_channel][instrument_id],
            )
        )
    return entries


def _validate_pool_configuration(
    *,
    quotas: Mapping[str, int] | None,
    limit: int,
) -> dict[RankingV4Channel, int]:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    if limit < 1 or limit > MAX_RANKING_V4_CANDIDATE_POOL_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_RANKING_V4_CANDIDATE_POOL_SIZE}")

    effective = dict(DEFAULT_RANKING_V4_CHANNEL_QUOTAS)
    if quotas is None:
        return effective
    if not isinstance(quotas, Mapping):
        raise ValueError("quotas must be a channel-to-integer mapping")
    unknown = sorted(str(channel) for channel in quotas if channel not in RANKING_V4_CHANNEL_ORDER)
    if unknown:
        raise ValueError(f"unknown Ranking V4 channels: {', '.join(unknown)}")
    for channel, quota in quotas.items():
        if isinstance(quota, bool) or not isinstance(quota, int):
            raise ValueError(f"quota for {channel} must be an integer")
        if quota < 0 or quota > MAX_RANKING_V4_CANDIDATE_POOL_SIZE:
            raise ValueError(
                f"quota for {channel} must be between 0 and {MAX_RANKING_V4_CANDIDATE_POOL_SIZE}"
            )
        effective[channel] = quota
    return effective


def _validate_and_index_candidates(
    candidates: list[RankingV4Candidate],
) -> dict[str, RankingV4Candidate]:
    grouped: dict[str, list[RankingV4Candidate]] = {}
    for candidate in candidates:
        instrument_id = candidate.instrument_id.strip()
        if not instrument_id:
            raise ValueError("Ranking V4 candidate instrument_id cannot be empty")
        if not math.isfinite(float(candidate.baseline_rank_score)):
            raise ValueError(
                f"Ranking V4 candidate {instrument_id} has a non-finite baseline score"
            )
        grouped.setdefault(instrument_id, []).append(candidate)
    duplicates = sorted(instrument_id for instrument_id, items in grouped.items() if len(items) > 1)
    if duplicates:
        raise ValueError(
            "duplicate Ranking V4 candidate instrument_id values: " + ", ".join(duplicates)
        )
    return {instrument_id: items[0] for instrument_id, items in sorted(grouped.items())}


def _channel_scores(
    candidate: RankingV4Candidate,
) -> dict[RankingV4Channel, float]:
    features = candidate.features
    scores: dict[RankingV4Channel, float] = {
        "baseline": _clamp01(float(candidate.baseline_rank_score)),
        "trend": (
            0.40 * features.momentum
            + 0.40 * features.trend_quality
            + 0.20 * features.industry_strength
        ),
        "breakout": min(
            1.0,
            0.45 * features.breakout_quality
            + 0.30 * features.momentum
            + 0.25 * features.liquidity
            + (
                BREAKOUT_STRATEGY_BONUS
                if _is_breakout_strategy(candidate.primary_strategy_id)
                else 0.0
            ),
        ),
        "defensive_low_vol": (
            0.30 * features.low_risk
            + 0.25 * features.risk_filter
            + 0.20 * features.liquidity
            + 0.25 * (1.0 - features.realized_volatility)
        ),
    }
    if _is_etf(candidate):
        scores["etf_industry"] = (
            0.35 * features.trend_quality
            + 0.35 * features.industry_strength
            + 0.30 * features.liquidity
        )
    else:
        scores["quality_value"] = (
            0.40 * features.quality + 0.35 * features.valuation + 0.25 * features.low_risk
        )
    return scores


def _rank_channel(
    channel: RankingV4Channel,
    *,
    candidates_by_id: dict[str, RankingV4Candidate],
    scores_by_id: dict[str, dict[RankingV4Channel, float]],
) -> list[str]:
    eligible = [
        instrument_id
        for instrument_id in candidates_by_id
        if channel in scores_by_id[instrument_id]
    ]
    return sorted(
        eligible,
        key=lambda instrument_id: (
            -scores_by_id[instrument_id][channel],
            -float(candidates_by_id[instrument_id].baseline_rank_score),
            instrument_id,
        ),
    )


def _best_channel(
    scores: dict[RankingV4Channel, float],
) -> RankingV4Channel:
    return min(
        scores,
        key=lambda channel: (
            -scores[channel],
            RANKING_V4_CHANNEL_ORDER.index(channel),
        ),
    )


def _best_channel_score(scores: dict[RankingV4Channel, float]) -> float:
    return scores[_best_channel(scores)]


def _is_etf(candidate: RankingV4Candidate) -> bool:
    asset_type = candidate.asset_type.strip().lower().replace("-", "_")
    return asset_type in _ETF_ASSET_TYPES


def _is_breakout_strategy(strategy_id: str | None) -> bool:
    normalized = (strategy_id or "").strip().lower()
    return "breakout" in normalized or "突破" in normalized


def _clamp01(value: float) -> float:
    return max(0.0, min(value, 1.0))
