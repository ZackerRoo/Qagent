from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from qagent.backtesting.ranking_v4 import (
    RankingV4Candidate,
    RankingV4CandidateScore,
    RankingV4Decision,
)


V4_MAX_POSITIONS = 5
V4_MAX_CANDIDATES = 50
V4_MAX_PER_STRATEGY = 2
V4_MAX_PER_INDUSTRY = 2
V4_MAX_PER_THEME = 2
V4_MAX_PER_FACTOR = 3
V4_MAX_SHARED_ETF_UNDERLYING_IDS = 0
V4_MAX_SHARED_ETF_INDEX_MEMBERSHIPS = 0
V4_MAX_PAIRWISE_CORRELATION = 0.80
V4_MAX_PORTFOLIO_BETA = 1.20
V4_MINIMUM_LIQUIDITY_SCORE = 0.50
V4_MINIMUM_CAPACITY_SCORE = 0.50
V4_ETF_ASSET_TYPES = {"etf", "fund", "index_fund"}
V4_UNKNOWN_INDUSTRIES = {"", "unknown", "综合", "指数etf", "etf", "未知"}


class RankingV4SelectedPosition(BaseModel):
    instrument_id: str
    position: int
    expected_utility_lower_bound_pct: float
    expected_utility_pct: float
    primary_strategy_id: str | None = None
    industry: str | None = None
    themes: list[str] = Field(default_factory=list)
    factor_signals: list[str] = Field(default_factory=list)
    asset_type: str
    beta: float
    constraint_evidence_mode: str
    reason: str


class RankingV4BlockedPosition(BaseModel):
    instrument_id: str
    v4_position: int
    reasons: list[str]


class RankingV4PortfolioDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    maximum_positions: int
    selected_count: int
    cash_slot_count: int
    average_beta: float | None = None
    selected: tuple[RankingV4SelectedPosition, ...] = ()
    blocked: tuple[RankingV4BlockedPosition, ...] = ()


@dataclass(frozen=True)
class _EligiblePosition:
    score: RankingV4CandidateScore
    candidate: RankingV4Candidate
    beta: float
    conservative_utility: Decimal


def select_ranking_v4_portfolio(
    decision: RankingV4Decision,
    candidates: list[RankingV4Candidate],
    *,
    pairwise_correlations: dict[tuple[str, str], float],
    maximum_positions: int = V4_MAX_POSITIONS,
    require_pairwise_correlation_evidence: bool = True,
    require_beta_evidence: bool = True,
) -> RankingV4PortfolioDecision:
    if maximum_positions < 0 or maximum_positions > V4_MAX_POSITIONS:
        raise ValueError(f"maximum_positions must be between 0 and {V4_MAX_POSITIONS}")
    if len(decision.candidates) > V4_MAX_CANDIDATES:
        raise ValueError(f"Ranking V4 portfolio accepts at most {V4_MAX_CANDIDATES} candidates")
    if len(candidates) > V4_MAX_CANDIDATES:
        raise ValueError(
            f"Ranking V4 portfolio accepts at most {V4_MAX_CANDIDATES} candidate metadata rows"
        )
    candidate_by_id = _candidate_index(candidates)
    eligible: list[_EligiblePosition] = []
    blocked_by_id: dict[str, RankingV4BlockedPosition] = {}
    score_ids: set[str] = set()
    for score in decision.candidates:
        if score.instrument_id in score_ids:
            raise ValueError(f"duplicate Ranking V4 score {score.instrument_id}")
        score_ids.add(score.instrument_id)
        candidate = candidate_by_id.get(score.instrument_id)
        reasons: list[str] = []
        if candidate is None:
            reasons.append("candidate_metadata_missing")
        elif not score.eligible_for_position:
            reasons.extend(score.blocked_reasons or ["ranking_evidence_blocked"])
        else:
            beta = _candidate_beta(
                candidate,
                require_evidence=require_beta_evidence,
            )
            reasons.extend(
                _constraint_reasons(
                    candidate,
                    beta=beta,
                    selected=[],
                    pairwise_correlations=pairwise_correlations,
                    require_pairwise_correlation_evidence=(require_pairwise_correlation_evidence),
                )
            )
            if not reasons and beta is not None:
                utility = _conservative_utility(score, reasons)
                if utility is not None:
                    eligible.append(
                        _EligiblePosition(
                            score=score,
                            candidate=candidate,
                            beta=beta,
                            conservative_utility=utility,
                        )
                    )
            if not reasons:
                continue
        blocked_by_id[score.instrument_id] = RankingV4BlockedPosition(
            instrument_id=score.instrument_id,
            v4_position=score.v4_position,
            reasons=list(dict.fromkeys(reasons)),
        )

    selected_entries = _maximize_conservative_utility(
        eligible,
        maximum_positions=maximum_positions,
        pairwise_correlations=pairwise_correlations,
        require_pairwise_correlation_evidence=require_pairwise_correlation_evidence,
    )
    selected_ids = {item.candidate.instrument_id for item in selected_entries}
    selected_constraints = [(item.score, item.candidate, item.beta) for item in selected_entries]
    for item in eligible:
        if item.candidate.instrument_id in selected_ids:
            continue
        reasons = _constraint_reasons(
            item.candidate,
            beta=item.beta,
            selected=selected_constraints,
            pairwise_correlations=pairwise_correlations,
            require_pairwise_correlation_evidence=(require_pairwise_correlation_evidence),
        )
        if len(selected_entries) >= maximum_positions:
            reasons.append("position_limit")
        if not reasons:
            reasons.append("not_selected_by_global_utility")
        blocked_by_id[item.candidate.instrument_id] = RankingV4BlockedPosition(
            instrument_id=item.candidate.instrument_id,
            v4_position=item.score.v4_position,
            reasons=list(dict.fromkeys(reasons)),
        )

    selected_entries.sort(
        key=lambda item: (
            item.score.v4_position,
            item.candidate.instrument_id,
        )
    )
    positions = tuple(
        RankingV4SelectedPosition(
            instrument_id=item.candidate.instrument_id,
            position=position,
            expected_utility_lower_bound_pct=float(
                item.score.expected_utility_lower_bound_pct or 0.0
            ),
            expected_utility_pct=float(item.score.expected_utility_pct or 0.0),
            primary_strategy_id=item.candidate.primary_strategy_id,
            industry=item.candidate.industry,
            themes=sorted(set(item.candidate.themes)),
            factor_signals=sorted(set(item.candidate.factor_signals)),
            asset_type=item.candidate.asset_type,
            beta=round(item.beta, 6),
            constraint_evidence_mode=item.candidate.constraint_evidence_mode,
            reason=item.score.reason,
        )
        for position, item in enumerate(selected_entries, start=1)
    )
    blocked = tuple(
        blocked_by_id[score.instrument_id]
        for score in decision.candidates
        if score.instrument_id in blocked_by_id
    )
    return RankingV4PortfolioDecision(
        maximum_positions=maximum_positions,
        selected_count=len(positions),
        cash_slot_count=max(maximum_positions - len(positions), 0),
        average_beta=(
            round(sum(item.beta for item in positions) / len(positions), 6) if positions else None
        ),
        selected=positions,
        blocked=blocked,
    )


def _conservative_utility(
    score: RankingV4CandidateScore,
    reasons: list[str],
) -> Decimal | None:
    raw = score.expected_utility_lower_bound_pct
    if raw is None:
        reasons.append("conservative_utility_evidence_missing")
        return None
    value = float(raw)
    if not math.isfinite(value):
        reasons.append("conservative_utility_evidence_invalid")
        return None
    if value <= 0:
        reasons.append("conservative_utility_not_positive")
        return None
    return Decimal(str(value))


def _maximize_conservative_utility(
    candidates: list[_EligiblePosition],
    *,
    maximum_positions: int,
    pairwise_correlations: dict[tuple[str, str], float],
    require_pairwise_correlation_evidence: bool,
) -> list[_EligiblePosition]:
    if maximum_positions == 0 or not candidates:
        return []

    # Fixed ordering makes equal-utility solutions deterministic. Include-first
    # traversal then permits pruning equal upper bounds after the first optimum.
    ordered = sorted(
        candidates,
        key=lambda item: (
            -item.conservative_utility,
            item.score.v4_position,
            item.candidate.instrument_id,
        ),
    )
    best_utility = Decimal("0")
    best_selection: tuple[_EligiblePosition, ...] = ()
    utilities = [item.conservative_utility for item in ordered]
    utility_prefix = [Decimal("0")]
    for utility in utilities:
        utility_prefix.append(utility_prefix[-1] + utility)

    # Pair constraints are immutable for one decision. Computing them once
    # avoids rebuilding sets and normalizing strings at every search node.
    incompatible_masks = [0] * len(ordered)
    for left_index, left in enumerate(ordered):
        for right_index in range(left_index + 1, len(ordered)):
            right = ordered[right_index]
            if not _pair_is_compatible(
                left.candidate,
                right.candidate,
                pairwise_correlations=pairwise_correlations,
                require_pairwise_correlation_evidence=require_pairwise_correlation_evidence,
            ):
                incompatible_masks[right_index] |= 1 << left_index

    strategies: dict[str, int] = {}
    industries: dict[str, int] = {}
    themes: dict[str, int] = {}
    factors: dict[str, int] = {}
    selected_indexes: list[int] = []

    def search(
        index: int,
        selected_mask: int,
        total_utility: Decimal,
        beta_sum: float,
    ) -> None:
        nonlocal best_selection, best_utility
        selected_count = len(selected_indexes)
        if total_utility > best_utility and (
            selected_count == 0 or beta_sum / selected_count <= V4_MAX_PORTFOLIO_BETA
        ):
            best_utility = total_utility
            best_selection = tuple(ordered[item_index] for item_index in selected_indexes)
        if selected_count >= maximum_positions or index >= len(ordered):
            return

        remaining_slots = maximum_positions - selected_count
        optimistic_end = min(len(ordered), index + remaining_slots)
        optimistic_gain = utility_prefix[optimistic_end] - utility_prefix[index]
        if total_utility + optimistic_gain <= best_utility:
            return

        item = ordered[index]
        candidate = item.candidate
        strategy = candidate.primary_strategy_id
        industry = _constrained_industry(candidate.industry)
        candidate_themes = tuple(sorted(_normalized_values(candidate.themes)))
        candidate_factors = tuple(sorted(_normalized_values(candidate.factor_signals)))
        concentration_allowed = not (
            (strategy and strategies.get(strategy, 0) >= V4_MAX_PER_STRATEGY)
            or (industry and industries.get(industry, 0) >= V4_MAX_PER_INDUSTRY)
            or any(themes.get(theme, 0) >= V4_MAX_PER_THEME for theme in candidate_themes)
            or any(factors.get(factor, 0) >= V4_MAX_PER_FACTOR for factor in candidate_factors)
        )
        if (
            item.conservative_utility > 0
            and concentration_allowed
            and not (incompatible_masks[index] & selected_mask)
        ):
            selected_indexes.append(index)
            _adjust_constraint_count(strategies, strategy, 1)
            _adjust_constraint_count(industries, industry, 1)
            for theme in candidate_themes:
                _adjust_constraint_count(themes, theme, 1)
            for factor in candidate_factors:
                _adjust_constraint_count(factors, factor, 1)
            search(
                index + 1,
                selected_mask | (1 << index),
                total_utility + item.conservative_utility,
                beta_sum + item.beta,
            )
            for factor in candidate_factors:
                _adjust_constraint_count(factors, factor, -1)
            for theme in candidate_themes:
                _adjust_constraint_count(themes, theme, -1)
            _adjust_constraint_count(industries, industry, -1)
            _adjust_constraint_count(strategies, strategy, -1)
            selected_indexes.pop()
        search(index + 1, selected_mask, total_utility, beta_sum)

    search(0, 0, Decimal("0"), 0.0)
    return list(best_selection)


def _adjust_constraint_count(
    counts: dict[str, int],
    key: str | None,
    delta: int,
) -> None:
    if not key:
        return
    updated = counts.get(key, 0) + delta
    if updated:
        counts[key] = updated
    else:
        counts.pop(key, None)


def _pair_is_compatible(
    left: RankingV4Candidate,
    right: RankingV4Candidate,
    *,
    pairwise_correlations: dict[tuple[str, str], float],
    require_pairwise_correlation_evidence: bool,
) -> bool:
    if _overlap_reasons(right, [left]):
        return False
    correlation = _pairwise_value(
        pairwise_correlations,
        left.instrument_id,
        right.instrument_id,
    )
    if correlation is None:
        return not require_pairwise_correlation_evidence
    return bool(
        math.isfinite(correlation)
        and -1.0 <= correlation <= 1.0
        and correlation <= V4_MAX_PAIRWISE_CORRELATION
    )


def _constraint_reasons(
    candidate: RankingV4Candidate,
    *,
    beta: float | None,
    selected: list[tuple[RankingV4CandidateScore, RankingV4Candidate, float]],
    pairwise_correlations: dict[tuple[str, str], float],
    require_pairwise_correlation_evidence: bool,
    enforce_portfolio_beta: bool = True,
) -> list[str]:
    reasons: list[str] = []
    if not candidate.constraint_data_complete:
        reasons.append("constraint_evidence_incomplete")
    if candidate.features.liquidity < V4_MINIMUM_LIQUIDITY_SCORE:
        reasons.append("liquidity_below_minimum")
    if candidate.features.capacity < V4_MINIMUM_CAPACITY_SCORE:
        reasons.append("capacity_below_minimum")
    if beta is None:
        reasons.append("beta_evidence_missing")
    if not selected:
        return reasons

    selected_candidates = [item[1] for item in selected]
    strategies = Counter(
        item.primary_strategy_id for item in selected_candidates if item.primary_strategy_id
    )
    if (
        candidate.primary_strategy_id
        and strategies[candidate.primary_strategy_id] >= V4_MAX_PER_STRATEGY
    ):
        reasons.append("strategy_concentration")

    industry = _constrained_industry(candidate.industry)
    industries = Counter(
        constrained
        for item in selected_candidates
        if (constrained := _constrained_industry(item.industry)) is not None
    )
    if industry is not None and industries[industry] >= V4_MAX_PER_INDUSTRY:
        reasons.append("industry_concentration")

    theme_counts = Counter(
        theme for item in selected_candidates for theme in _normalized_values(item.themes)
    )
    if any(
        theme_counts[theme] >= V4_MAX_PER_THEME for theme in _normalized_values(candidate.themes)
    ):
        reasons.append("theme_concentration")

    factor_counts = Counter(
        factor for item in selected_candidates for factor in _normalized_values(item.factor_signals)
    )
    if any(
        factor_counts[factor] >= V4_MAX_PER_FACTOR
        for factor in _normalized_values(candidate.factor_signals)
    ):
        reasons.append("factor_concentration")

    reasons.extend(_overlap_reasons(candidate, selected_candidates))
    for other in selected_candidates:
        correlation = _pairwise_value(
            pairwise_correlations,
            candidate.instrument_id,
            other.instrument_id,
        )
        if correlation is None:
            if require_pairwise_correlation_evidence:
                reasons.append("correlation_evidence_missing")
            continue
        if not math.isfinite(correlation) or not -1.0 <= correlation <= 1.0:
            reasons.append("correlation_evidence_invalid")
        elif correlation > V4_MAX_PAIRWISE_CORRELATION:
            reasons.append("pairwise_correlation")

    if beta is not None and enforce_portfolio_beta:
        average_beta = (sum(item[2] for item in selected) + beta) / (len(selected) + 1)
        if average_beta > V4_MAX_PORTFOLIO_BETA:
            reasons.append("portfolio_beta")
    return reasons


def _overlap_reasons(
    candidate: RankingV4Candidate,
    selected: list[RankingV4Candidate],
) -> list[str]:
    reasons: list[str] = []
    candidate_is_etf = _is_etf(candidate)
    candidate_underlyings = set(candidate.underlying_ids)
    candidate_indexes = set(candidate.index_memberships)
    for other in selected:
        other_is_etf = _is_etf(other)
        other_underlyings = set(other.underlying_ids)
        if candidate_is_etf and other_is_etf:
            if candidate.underlying_evidence_complete and other.underlying_evidence_complete:
                if (
                    len(candidate_underlyings.intersection(other_underlyings))
                    > V4_MAX_SHARED_ETF_UNDERLYING_IDS
                ):
                    reasons.append("etf_underlying_overlap")
            if (
                len(candidate_indexes.intersection(other.index_memberships))
                > V4_MAX_SHARED_ETF_INDEX_MEMBERSHIPS
            ):
                reasons.append("etf_index_overlap")
        elif candidate_is_etf:
            if (
                candidate.underlying_evidence_complete
                and other.instrument_id in candidate_underlyings
            ):
                reasons.append("stock_etf_underlying_overlap")
        elif (
            other_is_etf
            and other.underlying_evidence_complete
            and candidate.instrument_id in other_underlyings
        ):
            reasons.append("stock_etf_underlying_overlap")
    return reasons


def _candidate_beta(
    candidate: RankingV4Candidate,
    *,
    require_evidence: bool,
) -> float | None:
    raw = candidate.factor_exposures.get("beta")
    if raw is None:
        return None if require_evidence else 1.0
    beta = float(raw)
    if not math.isfinite(beta) or beta < 0:
        return None
    return beta


def _candidate_index(
    candidates: list[RankingV4Candidate],
) -> dict[str, RankingV4Candidate]:
    result: dict[str, RankingV4Candidate] = {}
    for candidate in candidates:
        if candidate.instrument_id in result:
            raise ValueError(f"duplicate Ranking V4 candidate {candidate.instrument_id}")
        result[candidate.instrument_id] = candidate
    return result


def _pairwise_value(
    values: dict[tuple[str, str], float],
    left: str,
    right: str,
) -> float | None:
    if left == right:
        return 1.0
    return values.get((left, right), values.get((right, left)))


def _constrained_industry(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    return normalized if normalized not in V4_UNKNOWN_INDUSTRIES else None


def _normalized_values(values: list[str]) -> set[str]:
    return {
        value.strip().lower()
        for value in values
        if value and value.strip() and value.strip().lower() not in {"unknown", "未知"}
    }


def _is_etf(candidate: RankingV4Candidate) -> bool:
    return candidate.asset_type.strip().lower() in V4_ETF_ASSET_TYPES
