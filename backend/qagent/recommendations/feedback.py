from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from pydantic import ValidationError

from qagent.domain.models import (
    OpportunityCard,
    PreTradeRiskCheck,
    PreTradeRiskProfile,
    RecommendationQualityCheck,
    RiskVeto,
)
from qagent.monitoring.outcomes import compute_opportunity_outcome
from qagent.monitoring.recommendation_calibration import (
    RecommendationCalibrationCenter,
    build_recommendation_calibration_center,
)
from qagent.providers.base import MarketDataProvider
from qagent.storage.repository import QagentRepository


def build_recent_recommendation_feedback_center(
    *,
    repo: QagentRepository,
    provider: str,
    market_provider: MarketDataProvider,
    limit: int = 150,
    as_of: date | None = None,
) -> RecommendationCalibrationCenter | None:
    snapshots = repo.list_opportunity_snapshots(provider=provider, limit=limit)
    if not snapshots:
        return None
    authenticated_sources, authentication_health = (
        authenticated_ranking_v3_snapshot_sources(
            repo,
            [snapshot.snapshot_id for snapshot in snapshots],
        )
    )
    instrument_ids = list(dict.fromkeys(snapshot.instrument_id for snapshot in snapshots))
    feedback_as_of = as_of or date.today()
    eligible_signal_dates = [
        snapshot.signal_date
        for snapshot in snapshots
        if snapshot.signal_date is not None and snapshot.signal_date <= feedback_as_of
    ]
    # A 1900-2100 sentinel range makes every current cache look incomplete and
    # forces a slow network request per instrument. Outcomes only need bars from
    # the earliest recorded signal through the real evaluation date.
    feedback_start = min(eligible_signal_dates, default=feedback_as_of)
    bars = market_provider.get_daily_bars(
        instrument_ids,
        start=feedback_start,
        end=feedback_as_of,
    )
    pairs = []
    for snapshot in snapshots:
        if not bars.empty and "instrument_id" in bars.columns:
            instrument_bars = bars.loc[bars["instrument_id"] == snapshot.instrument_id]
        else:
            instrument_bars = bars
        pairs.append((snapshot, compute_opportunity_outcome(snapshot, instrument_bars)))
    return build_recommendation_calibration_center(
        pairs,
        data_health={
            "feedback_provider": provider,
            "feedback_snapshots": str(len(snapshots)),
            "feedback_bar_start": feedback_start.isoformat(),
            "feedback_bar_end": feedback_as_of.isoformat(),
            "feedback_bar_range_policy": "earliest_signal_to_evaluation_date",
            **authentication_health,
        },
        authenticated_admission_sources=authenticated_sources,
    )


def apply_recommendation_feedback_calibration(
    cards: list[OpportunityCard],
    center: RecommendationCalibrationCenter | None,
) -> list[OpportunityCard]:
    if (
        center is None
        or not _is_authenticated_official_calibration(center)
        or not center.signal_effects
    ):
        return cards
    effects = {
        (effect.dimension, effect.signal_key): effect
        for effect in center.signal_effects
        if effect.completed_count >= 2 and abs(effect.suggested_weight_delta) > 0
        and effect.signal_key != "unknown"
    }
    if not effects:
        return cards
    for card in cards:
        if _card_has_feedback_marker(card, "推荐反馈校准"):
            continue
        matched = [
            effects[key]
            for key in card_validation_dimension_keys(card)
            if key in effects
        ]
        if not matched:
            continue
        raw_delta = _dimension_normalized_adjustment(matched, _action_delta)
        reliability_scale = 0.45 + center.reliability_score * 0.55
        delta = _clamp(raw_delta * reliability_scale, -0.08, 0.08)
        if abs(delta) < 0.001:
            continue
        card.rank_score = round(_clamp(card.rank_score + delta, 0.0, 1.0), 4)
        card.dynamic_score = card.rank_score
        if card.recommendation_score is not None:
            card.recommendation_score.final_score = card.rank_score
            card.recommendation_score.summary = (
                f"推荐分 {card.rank_score:.0%}：已纳入推荐后闭环反馈 {delta:+.1%}。"
            )
        labels = "、".join(effect.label for effect in matched[:3])
        note = f"推荐反馈校准：{labels} 根据历史推荐后表现调整 {delta:+.1%}。"
        if note not in card.rank_reasons:
            card.rank_reasons.append(note)
        if note not in card.calibration_notes:
            card.calibration_notes.append(note)
    return cards


def apply_recommendation_feedback_quality_gate(
    cards: list[OpportunityCard],
    center: RecommendationCalibrationCenter | None,
) -> list[OpportunityCard]:
    if (
        center is None
        or not _is_authenticated_official_calibration(center)
        or not center.signal_effects
    ):
        return cards
    weak_effects = {
        (effect.dimension, effect.signal_key): effect
        for effect in center.signal_effects
        if effect.signal_key != "unknown" and _is_blocking_effect(effect)
    }
    if not weak_effects:
        return cards
    for card in cards:
        if _card_has_feedback_marker(card, "推荐反馈门禁"):
            continue
        matched = [
            weak_effects[key]
            for key in card_validation_dimension_keys(card)
            if key in weak_effects
        ]
        if not matched:
            continue
        _apply_feedback_block(card, matched)
    return cards


def recommendation_feedback_data_health(cards: list[OpportunityCard]) -> dict[str, str]:
    adjusted = sum(
        1 for card in cards if any("推荐反馈校准" in reason for reason in card.rank_reasons)
    )
    blocked = sum(
        1 for card in cards if any("推荐反馈门禁" in reason for reason in card.rank_reasons)
    )
    return {
        "recommendation_feedback_cards": str(len(cards)),
        "recommendation_feedback_adjusted": str(adjusted),
        "recommendation_feedback_blocked": str(blocked),
    }


def apply_paper_trading_feedback(
    cards: list[OpportunityCard],
    report: object | None,
) -> list[OpportunityCard]:
    if not _is_authenticated_official_paper_report(report):
        return cards
    drag_effects = _paper_drag_effects(report)
    contributor_effects = _paper_contributor_effects(report)
    if not drag_effects and not contributor_effects:
        return cards
    risk_gate = getattr(report, "risk_gate", None)
    paused = bool(risk_gate and getattr(risk_gate, "can_add_entries", True) is False)
    for card in cards:
        if _card_has_feedback_marker(card, "模拟盘反馈"):
            continue
        matched_drags = _matched_paper_effects(card, drag_effects)
        if matched_drags:
            raw_delta = -_dimension_normalized_adjustment(
                matched_drags,
                lambda effect: _paper_effect_strength(effect, paused),
            )
            delta = _clamp(raw_delta, -0.12, -0.02)
            card.rank_score = round(_clamp(card.rank_score + delta, 0.0, 1.0), 4)
            card.dynamic_score = card.rank_score
            if card.recommendation_score is not None:
                card.recommendation_score.final_score = card.rank_score
                card.recommendation_score.summary = (
                    f"推荐分 {card.rank_score:.0%}：已纳入模拟盘闭环反馈 {delta:+.1%}。"
                )
            labels = "、".join(
                str(getattr(effect, "label", getattr(effect, "key", "")))
                for effect in matched_drags[:3]
            )
            note = f"模拟盘反馈降权：{labels} 在模拟盘中近期拖累收益，推荐分调整 {delta:+.1%}。"
            if note not in card.rank_reasons:
                card.rank_reasons.append(note)
            if note not in card.calibration_notes:
                card.calibration_notes.append(note)
            continue

        matched_contributors = _matched_paper_effects(card, contributor_effects)
        if not matched_contributors:
            continue
        raw_delta = _dimension_normalized_adjustment(
            matched_contributors,
            lambda effect: _paper_contributor_strength(effect, paused),
        )
        delta = _clamp(raw_delta, 0.015, 0.06)
        card.rank_score = round(_clamp(card.rank_score + delta, 0.0, 1.0), 4)
        card.dynamic_score = card.rank_score
        if card.recommendation_score is not None:
            card.recommendation_score.final_score = card.rank_score
            card.recommendation_score.summary = (
                f"推荐分 {card.rank_score:.0%}：已纳入模拟盘闭环反馈 {delta:+.1%}。"
            )
        labels = "、".join(
            str(getattr(effect, "label", getattr(effect, "key", "")))
            for effect in matched_contributors[:3]
        )
        note = f"模拟盘反馈加权：{labels} 在模拟盘中近期贡献收益，推荐分调整 {delta:+.1%}。"
        if note not in card.rank_reasons:
            card.rank_reasons.append(note)
        if note not in card.calibration_notes:
            card.calibration_notes.append(note)
    return cards


def authenticated_ranking_v3_snapshot_sources(
    repo: QagentRepository,
    snapshot_ids: list[str],
) -> tuple[dict[str, str], dict[str, str]]:
    from qagent.backtesting.ranking_v3_production import (
        RankingV3ProductionBatch,
        RankingV3ProductionAuthorizationError,
        RankingV3ProductionIntegrityError,
        require_current_ranking_v3_production_batch,
        require_ranking_v3_production_batch_integrity,
    )
    from qagent.security.ranking_v3_attestation import (
        RankingV3Attestor,
        load_attestation_key,
    )
    from qagent.storage.tables import (
        RankingV3ProductionBatchRow,
        RankingV3ProductionSelectionRow,
    )

    requested = sorted(set(snapshot_ids))
    if not requested:
        return {}, {
            "feedback_production_authentication": "empty",
            "feedback_production_authenticated": "0",
            "feedback_production_authentication_errors": "0",
        }

    try:
        attestor = RankingV3Attestor(load_attestation_key())
        with repo.session_factory() as session:
            selection_rows = (
                session.query(
                    RankingV3ProductionSelectionRow.source_snapshot_id,
                    RankingV3ProductionSelectionRow.batch_fact_digest,
                )
                .filter(RankingV3ProductionSelectionRow.source_snapshot_id.in_(requested))
                .all()
            )
            batch_digests = sorted({row.batch_fact_digest for row in selection_rows})
            batch_rows = (
                session.query(RankingV3ProductionBatchRow)
                .filter(RankingV3ProductionBatchRow.fact_digest.in_(batch_digests))
                .all()
                if batch_digests
                else []
            )
    except Exception:
        return {}, {
            "feedback_production_authentication": "unavailable",
            "feedback_production_authenticated": "0",
            "feedback_production_authentication_errors": "1",
        }

    authenticated: dict[str, str] = {}
    authentication_errors = 0
    requested_set = set(requested)
    for row in batch_rows:
        try:
            batch = RankingV3ProductionBatch.model_validate(json.loads(row.payload_json))
            require_ranking_v3_production_batch_integrity(batch, attestor)
            require_current_ranking_v3_production_batch(batch)
        except (
            json.JSONDecodeError,
            TypeError,
            ValidationError,
            RankingV3ProductionAuthorizationError,
            RankingV3ProductionIntegrityError,
            ValueError,
        ):
            authentication_errors += 1
            continue
        for selection in batch.selections:
            if selection.source_snapshot_id in requested_set:
                authenticated[selection.source_snapshot_id] = "ranking_v3_production"

    return authenticated, {
        "feedback_production_authentication": (
            "verified" if authenticated else "no_official_samples"
        ),
        "feedback_production_authenticated": str(len(authenticated)),
        "feedback_production_authentication_errors": str(authentication_errors),
    }


def authenticated_ranking_v3_paper_trade_ids(
    repo: QagentRepository,
    trades: list[object],
) -> tuple[set[str], dict[str, str]]:
    """Authenticate persisted paper rows against signed current-schema batches."""

    from qagent.backtesting.ranking_v3_production import (
        RankingV3ProductionAuthorizationError,
        RankingV3ProductionBatch,
        RankingV3ProductionIntegrityError,
        require_current_ranking_v3_production_batch,
        require_ranking_v3_production_batch_integrity,
    )
    from qagent.security.ranking_v3_attestation import (
        RankingV3Attestor,
        load_attestation_key,
    )
    from qagent.storage.tables import RankingV3ProductionBatchRow

    candidates = [
        trade
        for trade in trades
        if getattr(trade, "admission_source", None) == "ranking_v3_production"
        and getattr(trade, "production_batch_fact_digest", None)
    ]
    if not candidates:
        return set(), {
            "paper_production_authentication": "no_official_claims",
            "paper_production_authenticated": "0",
            "paper_production_authentication_errors": "0",
        }

    batch_digests = sorted(
        {
            str(getattr(trade, "production_batch_fact_digest"))
            for trade in candidates
        }
    )
    try:
        attestor = RankingV3Attestor(load_attestation_key())
        with repo.session_factory() as session:
            rows = (
                session.query(RankingV3ProductionBatchRow)
                .filter(RankingV3ProductionBatchRow.fact_digest.in_(batch_digests))
                .all()
            )
    except Exception:
        return set(), {
            "paper_production_authentication": "unavailable",
            "paper_production_authenticated": "0",
            "paper_production_authentication_errors": str(len(candidates)),
        }

    batches: dict[str, RankingV3ProductionBatch] = {}
    errors = 0
    for row in rows:
        try:
            batch = RankingV3ProductionBatch.model_validate(json.loads(row.payload_json))
            require_ranking_v3_production_batch_integrity(batch, attestor)
            require_current_ranking_v3_production_batch(batch)
        except (
            json.JSONDecodeError,
            TypeError,
            ValidationError,
            RankingV3ProductionAuthorizationError,
            RankingV3ProductionIntegrityError,
            ValueError,
        ):
            errors += 1
            continue
        batches[batch.fact_digest] = batch

    authenticated: set[str] = set()
    for trade in candidates:
        batch = batches.get(str(getattr(trade, "production_batch_fact_digest")))
        if batch is None:
            errors += 1
            continue
        selection = next(
            (
                item
                for item in batch.selections
                if item.item_digest
                == getattr(trade, "production_selection_item_digest", None)
            ),
            None,
        )
        if selection is None or not _paper_trade_matches_signed_selection(
            trade,
            batch,
            selection,
        ):
            errors += 1
            continue
        authenticated.add(str(getattr(trade, "trade_id")))

    return authenticated, {
        "paper_production_authentication": (
            "verified" if authenticated else "no_authenticated_official_trades"
        ),
        "paper_production_authenticated": str(len(authenticated)),
        "paper_production_authentication_errors": str(errors),
    }


def _paper_trade_matches_signed_selection(
    trade: object,
    batch: object,
    selection: object,
) -> bool:
    return (
        getattr(trade, "production_identity_digest", None)
        == getattr(getattr(batch, "identity"), "identity_digest", None)
        and getattr(trade, "release_proof_digest", None)
        == getattr(getattr(batch, "identity"), "release_proof_digest", None)
        and getattr(trade, "source_snapshot_id", None)
        == getattr(selection, "source_snapshot_id", None)
        and getattr(trade, "instrument_id", None)
        == getattr(selection, "instrument_id", None)
        and getattr(trade, "strategy_id", None)
        == getattr(selection, "strategy_id", None)
        and getattr(trade, "signal_date", None)
        == getattr(batch, "session_date", None)
        and _decimal_equal(
            getattr(trade, "rank_score", None),
            getattr(selection, "source_rank_score", None),
        )
        and _decimal_equal(
            getattr(trade, "trigger_price", None),
            getattr(selection, "trigger_price", None),
        )
        and _decimal_equal(
            getattr(trade, "initial_stop", None),
            getattr(selection, "initial_stop", None),
        )
        and _decimal_equal(
            getattr(trade, "target_1", None),
            getattr(selection, "target_1", None),
        )
        and _decimal_equal(
            getattr(trade, "allocation_multiplier", None),
            getattr(selection, "allocation_multiplier", None),
        )
    )


def _decimal_equal(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is right
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (ArithmeticError, ValueError):
        return False


def _is_authenticated_official_calibration(
    center: RecommendationCalibrationCenter,
) -> bool:
    health = center.data_health
    if health.get("recommendation_calibration_scope") != "ranking_v3_production":
        return False
    if health.get("recommendation_calibration_fail_closed") != "true":
        return False
    try:
        return int(health.get("recommendation_calibration_source_official", "0")) > 0
    except ValueError:
        return False


def _is_authenticated_official_paper_report(report: object | None) -> bool:
    if report is None:
        return False
    health = getattr(report, "data_health", None)
    if not isinstance(health, Mapping):
        return False
    if health.get("paper_reporting_scope") != "ranking_v3_production":
        return False
    if health.get("paper_reporting_fail_closed") != "true":
        return False
    try:
        return int(str(health.get("paper_reporting_official", "0"))) > 0
    except ValueError:
        return False


def paper_trading_feedback_data_health(cards: list[OpportunityCard]) -> dict[str, str]:
    adjusted = sum(
        1
        for card in cards
        if any(
            "模拟盘反馈降权" in reason or "模拟盘反馈加权" in reason for reason in card.rank_reasons
        )
    )
    blocked = sum(
        1 for card in cards if any("模拟盘反馈门禁" in reason for reason in card.rank_reasons)
    )
    return {
        "paper_feedback_cards": str(len(cards)),
        "paper_feedback_adjusted": str(adjusted),
        "paper_feedback_blocked": str(blocked),
    }


def apply_walk_forward_validation_feedback(
    cards: list[OpportunityCard],
    validation: Mapping[str, object] | None,
) -> list[OpportunityCard]:
    if not validation:
        return cards
    center_status = str(validation.get("status", "insufficient"))
    raw_metrics = [
        *list(validation.get("strategies", []) or []),
        *list(validation.get("factors", []) or []),
    ]
    metrics = [
        item
        for item in raw_metrics
        if isinstance(item, Mapping)
        and int(item.get("out_of_sample_count", 0) or 0) >= 30
        and str(item.get("action", "observe")) != "observe"
    ]
    if not metrics:
        return cards
    for card in cards:
        if _card_has_feedback_marker(card, "样本外校准") or _card_has_feedback_marker(
            card, "样本外门禁"
        ):
            continue
        matched = _matched_walk_forward_metrics(card, metrics)
        if not matched:
            continue
        disabled = [item for item in matched if str(item.get("action")) == "disable"]
        if disabled:
            labels = "、".join(str(item.get("label") or item.get("key")) for item in disabled[:3])
            _apply_walk_forward_block(card, labels)
            continue
        raw_delta = sum(float(item.get("suggested_weight_delta", 0) or 0) for item in matched)
        if center_status != "accepted":
            raw_delta = min(raw_delta, 0.0)
        delta = _clamp(raw_delta, -0.10, 0.06)
        if abs(delta) < 0.001:
            continue
        card.rank_score = round(_clamp(card.rank_score + delta), 4)
        card.dynamic_score = card.rank_score
        if card.recommendation_score is not None:
            card.recommendation_score.final_score = card.rank_score
            card.recommendation_score.summary = (
                f"推荐分 {card.rank_score:.0%}：已纳入全市场样本外验证 {delta:+.1%}。"
            )
        labels = "、".join(str(item.get("label") or item.get("key")) for item in matched[:3])
        note = f"样本外校准：{labels} 根据 walk-forward 结果调整 {delta:+.1%}。"
        if note not in card.rank_reasons:
            card.rank_reasons.append(note)
        if note not in card.calibration_notes:
            card.calibration_notes.append(note)
    return cards


def walk_forward_feedback_data_health(
    cards: list[OpportunityCard],
    validation: Mapping[str, object] | None,
) -> dict[str, str]:
    adjusted = sum(
        1 for card in cards if any("样本外校准" in reason for reason in card.rank_reasons)
    )
    blocked = sum(
        1 for card in cards if any("样本外门禁" in reason for reason in card.rank_reasons)
    )
    return {
        "walk_forward_feedback_source": "latest_saved_validation" if validation else "missing",
        "walk_forward_feedback_gate": str((validation or {}).get("status", "missing")),
        "walk_forward_feedback_cards": str(len(cards)),
        "walk_forward_feedback_adjusted": str(adjusted),
        "walk_forward_feedback_blocked": str(blocked),
    }


def card_validation_signal_keys(card: OpportunityCard) -> list[str]:
    keys = list(card.factor_flags)
    keys.extend(
        exposure.factor_id
        for exposure in card.factor_exposures
        if exposure.score >= 0.65
    )
    a_share_enhanced = getattr(card, "a_share_enhanced", None)
    if a_share_enhanced is not None:
        keys.extend(a_share_enhanced.signals)
    if card.recommendation_quality is not None:
        keys.append(f"quality_{card.recommendation_quality.tier}")
    if card.primary_strategy_id:
        keys.append(card.primary_strategy_id)
    return sorted(set(key for key in keys if key))


def card_validation_dimension_keys(card: OpportunityCard) -> list[tuple[str, str]]:
    keys: set[tuple[str, str]] = {
        ("signal", key) for key in card_validation_signal_keys(card)
    }
    if card.primary_strategy_id:
        keys.add(("strategy", card.primary_strategy_id))
    factor_ids = set(card.factor_flags)
    factor_ids.update(
        exposure.factor_id
        for exposure in card.factor_exposures
        if exposure.factor_id
    )
    a_share_enhanced = getattr(card, "a_share_enhanced", None)
    if a_share_enhanced is not None:
        factor_ids.update(a_share_enhanced.signals)
    keys.update(("factor", key) for key in factor_ids if key)
    if card.market_context is not None:
        if card.market_context.industry:
            keys.add(("industry", card.market_context.industry))
        keys.update(("theme", key) for key in card.market_context.themes if key)
    market_regime = _card_signal_market_regime(card)
    if market_regime != "unknown":
        keys.add(("market_regime", market_regime))
    return sorted(keys)


def _paper_drag_effects(report: object | None) -> dict[tuple[str, str], object]:
    if report is None:
        return {}
    items = getattr(report, "failure_attribution", []) or []
    effects: dict[tuple[str, str], object] = {}
    for item in items:
        if not _is_actionable_paper_drag(item):
            continue
        dimension = str(getattr(item, "dimension", "")).strip().lower()
        key = str(getattr(item, "key", "")).strip()
        if not dimension or not key or key == "unknown":
            continue
        effects[(dimension, key)] = item
    return effects


def _paper_contributor_effects(report: object | None) -> dict[tuple[str, str], object]:
    if report is None:
        return {}
    items = getattr(report, "failure_attribution", []) or []
    effects: dict[tuple[str, str], object] = {}
    for item in items:
        if not _is_actionable_paper_contributor(item):
            continue
        dimension = str(getattr(item, "dimension", "")).strip().lower()
        key = str(getattr(item, "key", "")).strip()
        if not dimension or not key or key == "unknown":
            continue
        effects[(dimension, key)] = item
    return effects


def _is_actionable_paper_drag(item: object) -> bool:
    if str(getattr(item, "verdict", "")).lower() != "drag":
        return False
    evaluated = int(getattr(item, "evaluated_trades", 0) or 0)
    if evaluated < 3:
        return False
    total_return = getattr(item, "total_return_pct", None)
    win_rate = getattr(item, "win_rate", None)
    weak_return = total_return is not None and float(total_return) <= -2.0
    weak_win_rate = win_rate is not None and float(win_rate) <= 0.25
    return weak_return or weak_win_rate


def _is_actionable_paper_contributor(item: object) -> bool:
    if str(getattr(item, "verdict", "")).lower() != "contributor":
        return False
    evaluated = int(getattr(item, "evaluated_trades", 0) or 0)
    if evaluated < 3:
        return False
    total_return = getattr(item, "total_return_pct", None)
    win_rate = getattr(item, "win_rate", None)
    target_hits = int(getattr(item, "target_hit_trades", 0) or 0)
    stopped = int(getattr(item, "stopped_trades", 0) or 0)
    strong_return = total_return is not None and float(total_return) >= 2.0
    strong_win_rate = win_rate is not None and float(win_rate) >= 0.6
    return (strong_return or strong_win_rate) and target_hits >= stopped


def _matched_paper_effects(
    card: OpportunityCard,
    effects: dict[tuple[str, str], object],
) -> list[object]:
    matched: list[object] = []
    for dimension_key in card_validation_dimension_keys(card):
        effect = effects.get(dimension_key)
        if effect is not None:
            matched.append(effect)
    asset_key = _card_asset_key(card)
    effect = effects.get(("asset", asset_key))
    if effect is not None:
        matched.append(effect)
    unique: list[object] = []
    seen: set[tuple[str, str]] = set()
    for effect in matched:
        identity = (
            str(getattr(effect, "dimension", "")),
            str(getattr(effect, "key", "")),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(effect)
    return unique


def _matched_walk_forward_metrics(
    card: OpportunityCard,
    metrics: list[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    strategy_id = card.primary_strategy_id
    signal_keys = set(card_validation_signal_keys(card))
    matched = []
    for item in metrics:
        dimension = str(item.get("dimension", ""))
        key = str(item.get("key", ""))
        if dimension == "strategy" and strategy_id == key:
            matched.append(item)
        elif dimension == "factor" and key in signal_keys:
            matched.append(item)
    return matched


def _apply_walk_forward_block(card: OpportunityCard, labels: str) -> None:
    detail = f"{labels} 的全市场样本外验证为负，达到停用门槛。"
    check = RecommendationQualityCheck(
        code="walk_forward_validation_gate",
        status="block",
        label="样本外验证未通过",
        detail=detail,
        score_impact=-0.30,
    )
    if card.recommendation_quality is not None:
        checks = [
            item for item in card.recommendation_quality.checks if item.code != check.code
        ] + [check]
        card.recommendation_quality.checks = checks
        card.recommendation_quality.block_count = sum(
            1 for item in checks if item.status == "block"
        )
        card.recommendation_quality.warn_count = sum(1 for item in checks if item.status == "warn")
        card.recommendation_quality.pass_count = sum(1 for item in checks if item.status == "pass")
        card.recommendation_quality.score = round(
            _clamp(card.recommendation_quality.score - 0.20), 4
        )
        card.recommendation_quality.tier = "risk_filtered"
        card.recommendation_quality.summary = "风险过滤：全市场样本外验证未通过。"
    card.rank_score = round(min(card.rank_score, 0.35), 4)
    card.dynamic_score = card.rank_score
    if card.recommendation_score is not None:
        card.recommendation_score.final_score = card.rank_score
        card.recommendation_score.tier = "risk_filtered"
        card.recommendation_score.summary = (
            f"推荐分 {card.rank_score:.0%}：样本外验证为负，已停止进入买入候选。"
        )
    _block_pre_trade_risk(card, detail)
    _block_decision(card, detail)
    note = f"样本外门禁：{labels} 未通过全市场历史验证，暂不进入买入候选。"
    if note not in card.rank_reasons:
        card.rank_reasons.append(note)
    if note not in card.calibration_notes:
        card.calibration_notes.append(note)


def _card_asset_key(card: OpportunityCard) -> str:
    symbol = card.instrument_id.split(":", 1)[-1].split(".", 1)[0]
    if card.instrument_id.startswith("CN:") and symbol.startswith(("15", "16", "51", "56", "58")):
        return "etf"
    return "stock"


def _paper_effect_strength(effect: object, paused: bool) -> float:
    total_return = getattr(effect, "total_return_pct", None)
    stopped = int(getattr(effect, "stopped_trades", 0) or 0)
    target_hits = int(getattr(effect, "target_hit_trades", 0) or 0)
    strength = 0.025
    if total_return is not None:
        strength += min(abs(float(total_return)) / 100.0, 0.05)
    if stopped >= 3 and target_hits == 0:
        strength += 0.02
    if paused:
        strength += 0.015
    return strength


def _paper_contributor_strength(effect: object, paused: bool) -> float:
    total_return = getattr(effect, "total_return_pct", None)
    win_rate = getattr(effect, "win_rate", None)
    target_hits = int(getattr(effect, "target_hit_trades", 0) or 0)
    strength = 0.015
    if total_return is not None:
        strength += min(float(total_return) / 220.0, 0.035)
    if win_rate is not None and float(win_rate) >= 0.6:
        strength += 0.01
    if target_hits >= 2:
        strength += 0.008
    if paused:
        strength *= 0.75
    return strength


def _action_delta(effect) -> float:
    action = str(effect.weight_action)
    if action in {"提高", "raise", "increase", "promote"}:
        return abs(effect.suggested_weight_delta)
    if action in {"降低", "lower", "decrease", "demote"}:
        return -abs(effect.suggested_weight_delta)
    return effect.suggested_weight_delta


def _dimension_normalized_adjustment(
    effects: list[object],
    adjustment,
) -> float:
    by_dimension: dict[str, list[float]] = {}
    for effect in effects:
        dimension = str(getattr(effect, "dimension", "signal") or "signal")
        by_dimension.setdefault(dimension, []).append(float(adjustment(effect)))
    if not by_dimension:
        return 0.0
    representative = [
        max(values, key=abs)
        for values in by_dimension.values()
        if values
    ]
    return sum(representative) / len(representative) if representative else 0.0


def _card_signal_market_regime(card: OpportunityCard) -> str:
    market_context = card.market_context
    candidates = [
        getattr(card, "market_regime", None),
        getattr(card, "market_environment", None),
        getattr(card, "market_state", None),
        getattr(market_context, "market_regime", None),
        getattr(market_context, "regime", None),
        getattr(market_context, "market_state", None),
    ]
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            candidate = (
                candidate.get("regime")
                or candidate.get("state")
                or candidate.get("CN")
            )
            if isinstance(candidate, Mapping):
                candidate = candidate.get("regime") or candidate.get("state")
        value = str(candidate or "").strip()
        if value and value.lower() not in {"none", "null", "unknown", "未分类"}:
            return value
    return "unknown"


def _is_blocking_effect(effect) -> bool:
    if effect.completed_count < 3:
        return False
    if effect.reliability_score < 0.25:
        return False
    if effect.avg_return_10d is None or effect.avg_return_10d > -1.0:
        return False
    weak_win_rate = effect.win_rate_10d is not None and effect.win_rate_10d <= 0.35
    weak_lift = effect.lift_vs_baseline_10d is not None and effect.lift_vs_baseline_10d <= -1.0
    return weak_win_rate or weak_lift


def _apply_feedback_block(card: OpportunityCard, effects: list[object]) -> None:
    labels = "、".join(str(effect.label) for effect in effects[:3])
    worst_return = min(
        (effect.avg_return_10d for effect in effects if effect.avg_return_10d is not None),
        default=None,
    )
    worst_win_rate = min(
        (effect.win_rate_10d for effect in effects if effect.win_rate_10d is not None),
        default=None,
    )
    detail = _feedback_block_detail(labels, worst_return, worst_win_rate)
    check = RecommendationQualityCheck(
        code="feedback_quality_gate",
        status="block",
        label="推荐反馈转弱",
        detail=detail,
        score_impact=-0.28,
    )
    if card.recommendation_quality is not None:
        checks = [
            item
            for item in card.recommendation_quality.checks
            if item.code != "feedback_quality_gate"
        ]
        checks.append(check)
        card.recommendation_quality.checks = checks
        card.recommendation_quality.block_count = sum(
            1 for item in checks if item.status == "block"
        )
        card.recommendation_quality.warn_count = sum(1 for item in checks if item.status == "warn")
        card.recommendation_quality.pass_count = sum(1 for item in checks if item.status == "pass")
        card.recommendation_quality.score = round(
            _clamp(card.recommendation_quality.score - 0.18), 4
        )
        card.recommendation_quality.tier = "risk_filtered"
        card.recommendation_quality.summary = "风险过滤：推荐后验证转弱，暂不进入买入候选。"
    card.rank_score = round(min(card.rank_score, 0.38), 4)
    card.dynamic_score = card.rank_score
    if card.recommendation_score is not None:
        card.recommendation_score.final_score = card.rank_score
        card.recommendation_score.tier = "risk_filtered"
        card.recommendation_score.summary = (
            f"推荐分 {card.rank_score:.0%}：历史推荐闭环显示该信号近期失效，已降级过滤。"
        )
    _block_pre_trade_risk(card, detail)
    _block_decision(card, detail)
    note = f"推荐反馈门禁：{labels} 最近推荐后表现转弱，暂不进入买入候选。"
    if note not in card.rank_reasons:
        card.rank_reasons.append(note)
    if note not in card.calibration_notes:
        card.calibration_notes.append(note)


def _feedback_block_detail(
    labels: str,
    avg_return_10d: float | None,
    win_rate_10d: float | None,
) -> str:
    pieces = [f"{labels} 的历史推荐后表现明显走弱"]
    if avg_return_10d is not None:
        pieces.append(f"10 日均值 {avg_return_10d:+.2f}%")
    if win_rate_10d is not None:
        pieces.append(f"10 日胜率 {win_rate_10d:.0%}")
    return "，".join(pieces) + "，需要等待重新走强后再考虑。"


def _block_pre_trade_risk(card: OpportunityCard, detail: str) -> None:
    check = PreTradeRiskCheck(
        code="feedback_quality_gate",
        severity="block",
        title="推荐反馈转弱",
        message=detail,
        action="暂不买入；等待后续推荐样本重新验证。",
    )
    if card.pre_trade_risk is None:
        card.pre_trade_risk = PreTradeRiskProfile(
            status="blocked",
            label="不可买",
            can_buy=False,
            can_size_up=False,
            risk_budget_pct=0.0,
            max_position_pct=0.0,
            next_action=check.action,
            summary=detail,
            checks=[check],
        )
        return
    checks = [item for item in card.pre_trade_risk.checks if item.code != check.code]
    checks.append(check)
    card.pre_trade_risk.status = "blocked"
    card.pre_trade_risk.label = "不可买"
    card.pre_trade_risk.can_buy = False
    card.pre_trade_risk.can_size_up = False
    card.pre_trade_risk.risk_budget_pct = 0.0
    card.pre_trade_risk.max_position_pct = 0.0
    card.pre_trade_risk.next_action = check.action
    card.pre_trade_risk.summary = detail
    card.pre_trade_risk.checks = checks


def _block_decision(card: OpportunityCard, detail: str) -> None:
    if card.decision is None:
        return
    card.decision.action = "avoid"
    card.decision.action_label = "暂不买"
    card.decision.risk_status = "blocked"
    card.decision.suggested_risk_pct = 0.0
    card.decision.max_position_pct = 0.0
    veto = RiskVeto(
        code="feedback_quality_gate",
        severity="block",
        title="推荐反馈转弱",
        message=detail,
    )
    card.decision.risk_vetoes = [
        item for item in card.decision.risk_vetoes if item.code != veto.code
    ] + [veto]


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def _card_has_feedback_marker(card: OpportunityCard, marker: str) -> bool:
    return any(
        marker in value
        for value in [*card.rank_reasons, *card.calibration_notes]
    )
