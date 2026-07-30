from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from qagent.monitoring.outcomes import OpportunityOutcome
from qagent.storage.repository import OpportunitySnapshotRecord


_RESEARCH_SHADOW_ADMISSION_SOURCES = frozenset({"ranking_v4_shadow"})


class RecommendationCalibrationSample(BaseModel):
    snapshot_id: str
    instrument_id: str
    instrument_label: str | None = None
    signal_date: date | None = None
    score: float
    score_band: str
    primary_strategy_id: str | None = None
    signals: list[str] = Field(default_factory=list)
    factor_ids: list[str] = Field(default_factory=list)
    industry: str = "unknown"
    themes: list[str] = Field(default_factory=list)
    market_regime: str = "unknown"
    admission_source: str = "legacy_unknown"
    outcome_status: str
    return_5d: float | None = None
    return_10d: float | None = None
    return_20d: float | None = None
    max_drawdown_pct: float | None = None
    max_runup_pct: float | None = None


class RecommendationCalibrationBand(BaseModel):
    band: str
    label: str
    min_score: float
    max_score: float
    sample_count: int
    completed_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None
    max_drawdown_pct: float | None = None
    best_runup_pct: float | None = None
    reliability_score: float = Field(ge=0, le=1)
    verdict: str


class RecommendationSignalEffect(BaseModel):
    dimension: str = "signal"
    signal_key: str
    label: str
    sample_count: int
    completed_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    baseline_avg_return_10d: float | None = None
    lift_vs_baseline_10d: float | None = None
    reliability_score: float = Field(ge=0, le=1)
    weight_action: str
    suggested_weight_delta: float
    reason: str


class RecommendationWeightSuggestion(BaseModel):
    dimension: str = "signal"
    key: str
    label: str
    action: str
    delta: float
    reason: str


class RecommendationCalibrationCurvePoint(BaseModel):
    date: date
    sample_count: int
    completed_count: int
    cumulative_win_rate_10d: float | None = None
    cumulative_avg_return_10d: float | None = None


class RecommendationCalibrationCenter(BaseModel):
    as_of: date
    headline: str
    verdict: str
    reliability_score: float = Field(ge=0, le=1)
    baseline_win_rate_10d: float | None = None
    baseline_avg_return_10d: float | None = None
    score_bands: list[RecommendationCalibrationBand] = Field(default_factory=list)
    signal_effects: list[RecommendationSignalEffect] = Field(default_factory=list)
    weight_suggestions: list[RecommendationWeightSuggestion] = Field(default_factory=list)
    curve_points: list[RecommendationCalibrationCurvePoint] = Field(default_factory=list)
    recent_samples: list[RecommendationCalibrationSample] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def build_recommendation_calibration_center(
    pairs: list[tuple[OpportunitySnapshotRecord, OpportunityOutcome]],
    *,
    as_of: date | None = None,
    recent_limit: int = 12,
    data_health: dict[str, str] | None = None,
    authenticated_admission_sources: Mapping[str, str] | None = None,
    reporting_scope: Literal["official", "legacy"] = "official",
) -> RecommendationCalibrationCenter:
    admission_sources = authenticated_admission_sources or {}
    all_samples = [
        _sample(
            snapshot,
            outcome,
            admission_source=_authenticated_admission_source(
                snapshot.snapshot_id,
                admission_sources,
            ),
        )
        for snapshot, outcome in pairs
    ]
    official_samples = [
        sample for sample in all_samples if sample.admission_source == "ranking_v3_production"
    ]
    legacy_samples = [
        sample for sample in all_samples if sample.admission_source != "ranking_v3_production"
    ]
    samples = official_samples if reporting_scope == "official" else legacy_samples
    completed = [item for item in samples if _is_completed(item)]
    baseline_win = _ratio(
        sum(1 for item in completed if (item.return_10d or 0) > 0), len(completed)
    )
    baseline_avg = _average([item.return_10d for item in completed if item.return_10d is not None])
    bands = _score_bands(samples)
    effects = _signal_effects(samples, baseline_win, baseline_avg)
    suggestions = _weight_suggestions(effects, bands)
    curve = _curve_points(samples)
    reliability = _center_score(bands, effects, len(samples), len(completed))
    verdict = _verdict(reliability, len(samples), len(completed), baseline_avg)
    effective_as_of = as_of or max(
        (item.signal_date for item in samples if item.signal_date), default=date.today()
    )
    recent = sorted(
        samples,
        key=lambda item: (item.signal_date or date.min, item.score),
        reverse=True,
    )[:recent_limit]
    return RecommendationCalibrationCenter(
        as_of=effective_as_of,
        headline=_headline(verdict, len(samples), len(completed), baseline_win, baseline_avg),
        verdict=verdict,
        reliability_score=reliability,
        baseline_win_rate_10d=baseline_win,
        baseline_avg_return_10d=baseline_avg,
        score_bands=bands,
        signal_effects=effects,
        weight_suggestions=suggestions,
        curve_points=curve,
        recent_samples=recent,
        action_items=_action_items(verdict, bands, effects),
        data_health={
            **(data_health or {}),
            "recommendation_calibration_scope": (
                "ranking_v3_production" if reporting_scope == "official" else "legacy_only"
            ),
            "recommendation_calibration_fail_closed": "true",
            "recommendation_calibration_source_total": str(len(all_samples)),
            "recommendation_calibration_source_official": str(len(official_samples)),
            "recommendation_calibration_source_research_shadow": str(
                sum(
                    item.admission_source in _RESEARCH_SHADOW_ADMISSION_SOURCES
                    for item in all_samples
                )
            ),
            "recommendation_calibration_source_legacy_manual": str(
                sum(item.admission_source == "legacy_manual" for item in all_samples)
            ),
            "recommendation_calibration_source_legacy_unknown": str(
                sum(
                    item.admission_source
                    not in {
                        "ranking_v3_production",
                        "legacy_manual",
                        *_RESEARCH_SHADOW_ADMISSION_SOURCES,
                    }
                    for item in all_samples
                )
            ),
            "recommendation_calibration_source_excluded": str(len(all_samples) - len(samples)),
            "recommendation_calibration_samples": str(len(samples)),
            "recommendation_calibration_completed": str(len(completed)),
            "recommendation_calibration_score_bands": str(len(bands)),
            "recommendation_calibration_signal_effects": str(len(effects)),
            "recommendation_calibration_strategy_effects": str(
                sum(item.dimension == "strategy" for item in effects)
            ),
            "recommendation_calibration_factor_effects": str(
                sum(item.dimension == "factor" for item in effects)
            ),
            "recommendation_calibration_industry_effects": str(
                sum(item.dimension == "industry" for item in effects)
            ),
            "recommendation_calibration_market_regime_effects": str(
                sum(item.dimension == "market_regime" for item in effects)
            ),
            "recommendation_calibration_curve_points": str(len(curve)),
        },
    )


def _sample(
    snapshot: OpportunitySnapshotRecord,
    outcome: OpportunityOutcome,
    *,
    admission_source: str,
) -> RecommendationCalibrationSample:
    score = _snapshot_score(snapshot)
    card = snapshot.card if isinstance(snapshot.card, dict) else {}
    industry, themes, market_regime, factor_ids = _context_dimensions(card)
    return RecommendationCalibrationSample(
        snapshot_id=snapshot.snapshot_id,
        instrument_id=snapshot.instrument_id,
        instrument_label=_instrument_label(snapshot, outcome),
        signal_date=snapshot.signal_date,
        score=round(score, 4),
        score_band=_score_band(score),
        primary_strategy_id=snapshot.primary_strategy_id,
        signals=_signals_from_card(card),
        factor_ids=factor_ids,
        industry=industry,
        themes=themes,
        market_regime=market_regime,
        admission_source=admission_source,
        outcome_status=outcome.outcome_status,
        return_5d=outcome.return_5d,
        return_10d=outcome.return_10d,
        return_20d=outcome.return_20d,
        max_drawdown_pct=outcome.max_drawdown_pct,
        max_runup_pct=outcome.max_runup_pct,
    )


def _score_bands(
    samples: list[RecommendationCalibrationSample],
) -> list[RecommendationCalibrationBand]:
    definitions = [
        ("80+", "80 分以上", 0.8, 1.01),
        ("70-80", "70-80 分", 0.7, 0.8),
        ("60-70", "60-70 分", 0.6, 0.7),
        ("<60", "60 分以下", 0.0, 0.6),
    ]
    rows = []
    for key, label, low, high in definitions:
        items = [item for item in samples if low <= item.score < high]
        if not items:
            continue
        completed = [item for item in items if _is_completed(item)]
        returns_10d = [item.return_10d for item in completed if item.return_10d is not None]
        returns_20d = [item.return_20d for item in completed if item.return_20d is not None]
        drawdowns = [
            item.max_drawdown_pct for item in completed if item.max_drawdown_pct is not None
        ]
        runups = [item.max_runup_pct for item in completed if item.max_runup_pct is not None]
        win_rate = _ratio(sum(1 for value in returns_10d if value > 0), len(returns_10d))
        avg_10d = _average(returns_10d)
        reliability = _segment_score(
            len(items), len(completed), win_rate, avg_10d, min(drawdowns) if drawdowns else None
        )
        rows.append(
            RecommendationCalibrationBand(
                band=key,
                label=label,
                min_score=low,
                max_score=min(high, 1.0),
                sample_count=len(items),
                completed_count=len(completed),
                win_rate_10d=win_rate,
                avg_return_10d=avg_10d,
                avg_return_20d=_average(returns_20d),
                max_drawdown_pct=min(drawdowns) if drawdowns else None,
                best_runup_pct=max(runups) if runups else None,
                reliability_score=reliability,
                verdict=_segment_verdict(len(completed), win_rate, avg_10d),
            )
        )
    return rows


def _signal_effects(
    samples: list[RecommendationCalibrationSample],
    baseline_win: float | None,
    baseline_avg: float | None,
) -> list[RecommendationSignalEffect]:
    grouped: dict[tuple[str, str], list[RecommendationCalibrationSample]] = {}
    for sample in samples:
        for dimension, key in _sample_dimension_keys(sample):
            grouped.setdefault((dimension, key), []).append(sample)
    effects = []
    baseline_avg_value = baseline_avg or 0
    for (dimension, key), items in grouped.items():
        completed = [item for item in items if _is_completed(item)]
        returns_10d = [item.return_10d for item in completed if item.return_10d is not None]
        win_rate = _ratio(sum(1 for value in returns_10d if value > 0), len(returns_10d))
        avg_10d = _average(returns_10d)
        lift = None if avg_10d is None or baseline_avg is None else round(avg_10d - baseline_avg, 4)
        reliability = _signal_score(
            len(items), len(completed), win_rate, avg_10d, baseline_win, baseline_avg
        )
        label = _dimension_label(dimension, key)
        action, delta, reason = _signal_action(
            key,
            label,
            len(completed),
            win_rate,
            avg_10d,
            baseline_avg_value,
        )
        effects.append(
            RecommendationSignalEffect(
                dimension=dimension,
                signal_key=key,
                label=label,
                sample_count=len(items),
                completed_count=len(completed),
                win_rate_10d=win_rate,
                avg_return_10d=avg_10d,
                baseline_avg_return_10d=baseline_avg,
                lift_vs_baseline_10d=lift,
                reliability_score=reliability,
                weight_action=action,
                suggested_weight_delta=delta,
                reason=reason,
            )
        )
    return sorted(
        effects,
        key=lambda item: (
            item.signal_key != "unknown",
            item.reliability_score,
            item.completed_count,
        ),
        reverse=True,
    )


def _weight_suggestions(
    effects: list[RecommendationSignalEffect],
    bands: list[RecommendationCalibrationBand],
) -> list[RecommendationWeightSuggestion]:
    suggestions = [
        RecommendationWeightSuggestion(
            dimension=item.dimension,
            key=item.signal_key,
            label=item.label,
            action=item.weight_action,
            delta=item.suggested_weight_delta,
            reason=item.reason,
        )
        for item in effects
        if item.weight_action != "保持"
    ]
    high = next((band for band in bands if band.band == "80+"), None)
    low = next((band for band in bands if band.band == "<60"), None)
    if high and low and high.avg_return_10d is not None and low.avg_return_10d is not None:
        spread = high.avg_return_10d - low.avg_return_10d
        if spread > 1:
            suggestions.insert(
                0,
                RecommendationWeightSuggestion(
                    dimension="score",
                    key="rank_score",
                    label="推荐总分",
                    action="提高",
                    delta=0.04,
                    reason=f"80 分以上样本 10 日均值比 60 分以下高 {spread:.2f} 个百分点。",
                ),
            )
    if not suggestions:
        suggestions.append(
            RecommendationWeightSuggestion(
                dimension="system",
                key="sample_collection",
                label="样本积累",
                action="保持",
                delta=0,
                reason="当前样本还不足以自动调整权重，先继续记录推荐后的表现。",
            )
        )
    return suggestions[:8]


def _curve_points(
    samples: list[RecommendationCalibrationSample],
) -> list[RecommendationCalibrationCurvePoint]:
    ordered = sorted(
        [item for item in samples if item.signal_date is not None],
        key=lambda item: item.signal_date or date.min,
    )
    rows = []
    completed_so_far: list[RecommendationCalibrationSample] = []
    for index, item in enumerate(ordered, start=1):
        if _is_completed(item):
            completed_so_far.append(item)
        returns_10d = [
            sample.return_10d for sample in completed_so_far if sample.return_10d is not None
        ]
        rows.append(
            RecommendationCalibrationCurvePoint(
                date=item.signal_date or date.today(),
                sample_count=index,
                completed_count=len(completed_so_far),
                cumulative_win_rate_10d=_ratio(
                    sum(1 for value in returns_10d if value > 0), len(returns_10d)
                ),
                cumulative_avg_return_10d=_average(returns_10d),
            )
        )
    return rows[-40:]


def _is_completed(sample: RecommendationCalibrationSample) -> bool:
    return sample.outcome_status != "pending" and sample.return_10d is not None


def _snapshot_score(snapshot: OpportunitySnapshotRecord) -> float:
    card = snapshot.card if isinstance(snapshot.card, dict) else {}
    recommendation_score = card.get("recommendation_score")
    if isinstance(recommendation_score, dict):
        value = recommendation_score.get("final_score")
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    value = card.get("rank_score")
    parsed = _float_or_none(value)
    if parsed is not None:
        return parsed
    return float(snapshot.rank_score)


def _signals_from_card(card: dict[str, object]) -> list[str]:
    signals: list[str] = []
    factor_flags = card.get("factor_flags")
    if isinstance(factor_flags, list):
        signals.extend(str(item) for item in factor_flags if item)
    enhanced = card.get("a_share_enhanced")
    if isinstance(enhanced, dict):
        enhanced_signals = enhanced.get("signals")
        if isinstance(enhanced_signals, list):
            signals.extend(str(item) for item in enhanced_signals if item)
    quality = card.get("recommendation_quality")
    if isinstance(quality, dict) and quality.get("tier"):
        signals.append(f"quality_{quality.get('tier')}")
    return sorted(set(signals))


def _context_dimensions(
    card: dict[str, object],
) -> tuple[str, list[str], str, list[str]]:
    market_context = card.get("market_context")
    market_context = market_context if isinstance(market_context, dict) else {}
    industry = _normalized_dimension_value(
        market_context.get("industry") or card.get("industry") or card.get("sector")
    )
    themes = _normalized_text_list(
        market_context.get("themes") or card.get("themes") or card.get("theme")
    )
    factor_ids = _normalized_text_list(card.get("factor_flags"))
    exposures = card.get("factor_exposures")
    if isinstance(exposures, list):
        factor_ids.extend(
            str(item.get("factor_id")).strip()
            for item in exposures
            if isinstance(item, dict) and item.get("factor_id")
        )
    enhanced = card.get("a_share_enhanced")
    enhanced = enhanced if isinstance(enhanced, dict) else {}
    factor_ids.extend(_normalized_text_list(enhanced.get("signals")))
    market_regime = _market_regime_from_card(card)
    return industry, themes, market_regime, sorted(set(factor_ids))


def _authenticated_admission_source(
    snapshot_id: str,
    authenticated_sources: Mapping[str, str],
) -> str:
    value = str(authenticated_sources.get(snapshot_id) or "").strip()
    if value == "ranking_v3_production":
        return value
    if value == "legacy_manual":
        return value
    if value in _RESEARCH_SHADOW_ADMISSION_SOURCES:
        return value
    return "legacy_unknown"


def _sample_dimension_keys(
    sample: RecommendationCalibrationSample,
) -> list[tuple[str, str]]:
    rows = [("strategy", sample.primary_strategy_id or "unknown")]
    rows.extend(("factor", key) for key in sample.factor_ids or ["unknown"])
    rows.append(("industry", sample.industry))
    rows.extend(("theme", key) for key in sample.themes or ["unknown"])
    rows.append(("market_regime", sample.market_regime))
    rows.extend(("signal", key) for key in sample.signals if key not in set(sample.factor_ids))
    return sorted(set(rows))


def _market_regime_from_card(card: dict[str, object]) -> str:
    for candidate in (
        card.get("market_regime"),
        card.get("market_environment"),
        card.get("market_state"),
    ):
        if isinstance(candidate, dict):
            candidate = candidate.get("regime") or candidate.get("state") or candidate.get("CN")
            if isinstance(candidate, dict):
                candidate = candidate.get("regime") or candidate.get("state")
        value = _normalized_dimension_value(candidate)
        if value != "unknown":
            return value
    return "unknown"


def _normalized_text_list(value: object) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    normalized = []
    for item in values:
        text = str(item).strip()
        if text and text.lower() not in {"none", "null", "unknown", "未分类"}:
            normalized.append(text)
    return sorted(set(normalized))


def _normalized_dimension_value(value: object) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "unknown", "未分类"}:
        return "unknown"
    return text


def _instrument_label(
    snapshot: OpportunitySnapshotRecord,
    outcome: OpportunityOutcome,
) -> str | None:
    if outcome.instrument_label:
        return outcome.instrument_label
    card = snapshot.card if isinstance(snapshot.card, dict) else {}
    value = card.get("instrument_label")
    return str(value) if value else None


def _score_band(score: float) -> str:
    if score >= 0.8:
        return "80+"
    if score >= 0.7:
        return "70-80"
    if score >= 0.6:
        return "60-70"
    return "<60"


def _segment_score(
    sample_count: int,
    completed_count: int,
    win_rate: float | None,
    avg_return: float | None,
    max_drawdown: float | None,
) -> float:
    if sample_count == 0:
        return 0
    completion = completed_count / sample_count
    score = completion * 0.25
    score += (win_rate or 0) * 0.3
    score += _clamp(0.5 + ((avg_return or 0) / 12), 0, 1) * 0.3
    score += _clamp(1 - abs(max_drawdown or 0) / 20, 0, 1) * 0.15
    if completed_count < 2:
        score = min(score, 0.48)
    return round(_clamp(score, 0, 1), 4)


def _signal_score(
    sample_count: int,
    completed_count: int,
    win_rate: float | None,
    avg_return: float | None,
    baseline_win: float | None,
    baseline_avg: float | None,
) -> float:
    if sample_count == 0:
        return 0
    lift = (avg_return or 0) - (baseline_avg or 0)
    win_lift = (win_rate or 0) - (baseline_win or 0)
    score = 0.45 + _clamp(lift / 8, -0.25, 0.25) + _clamp(win_lift * 0.3, -0.15, 0.15)
    score += min(0.15, completed_count * 0.04)
    if completed_count < 2:
        score = min(score, 0.5)
    return round(_clamp(score, 0, 1), 4)


def _center_score(
    bands: list[RecommendationCalibrationBand],
    effects: list[RecommendationSignalEffect],
    sample_count: int,
    completed_count: int,
) -> float:
    if sample_count == 0:
        return 0
    high_band = next((band for band in bands if band.band == "80+"), None)
    high_score = high_band.reliability_score if high_band else 0.35
    signal_score = _average([effect.reliability_score for effect in effects[:5]]) or 0.35
    completion = completed_count / sample_count
    score = high_score * 0.45 + signal_score * 0.35 + completion * 0.2
    if completed_count < 3:
        score = min(score, 0.5)
    return round(_clamp(score, 0, 1), 4)


def _verdict(
    reliability: float,
    sample_count: int,
    completed_count: int,
    baseline_avg: float | None,
) -> str:
    if sample_count < 3 or completed_count < 2:
        return "样本不足"
    if reliability >= 0.62 and (baseline_avg or 0) >= 0:
        return "可信度提升"
    if reliability < 0.42 or (baseline_avg is not None and baseline_avg < -1):
        return "需要降权"
    return "继续观察"


def _segment_verdict(
    completed_count: int,
    win_rate: float | None,
    avg_return: float | None,
) -> str:
    if completed_count < 2:
        return "样本不足"
    if (win_rate or 0) >= 0.55 and (avg_return or 0) >= 0:
        return "有效"
    if (avg_return or 0) < -1:
        return "失效"
    return "观察"


def _signal_action(
    key: str,
    label: str,
    completed_count: int,
    win_rate: float | None,
    avg_return: float | None,
    baseline_avg: float,
) -> tuple[str, float, str]:
    if key == "unknown":
        return "保持", 0, f"{label}，不允许据此自动调整权重。"
    if completed_count < 2:
        return "保持", 0, "样本不足，暂不调整权重。"
    lift = (avg_return or 0) - baseline_avg
    if lift >= 1 and (win_rate or 0) >= 0.5:
        return "提高", 0.03, f"{label}样本 10 日均值高于基准 {lift:.2f} 个百分点。"
    if lift <= -1:
        return "降低", -0.03, f"{label}样本 10 日均值低于基准 {abs(lift):.2f} 个百分点。"
    return "保持", 0, f"{label}暂未显示显著超额。"


def _headline(
    verdict: str,
    sample_count: int,
    completed_count: int,
    win_rate: float | None,
    avg_return: float | None,
) -> str:
    if sample_count == 0:
        return "还没有推荐快照，先完成一次扫描再做推荐质量校准。"
    return (
        f"推荐校准：{verdict}，已完成 {completed_count}/{sample_count} 个样本，"
        f"10 日胜率 {_format_rate(win_rate)}，10 日均值 {_format_pct(avg_return)}。"
    )


def _action_items(
    verdict: str,
    bands: list[RecommendationCalibrationBand],
    effects: list[RecommendationSignalEffect],
) -> list[str]:
    items: list[str] = []
    if verdict == "可信度提升":
        items.append("优先使用 80 分以上且有正向增强信号的推荐，维持原有止损纪律。")
    elif verdict == "需要降权":
        items.append("近期推荐后表现偏弱，降低自动推荐权重，优先只做触发明确的机会。")
    else:
        items.append("继续积累推荐样本，先把分数分层和信号增益当作观察指标。")
    if effects:
        best = effects[0]
        items.append(f"当前最值得观察的信号是 {best.label}：{best.reason}")
    if any(band.verdict == "失效" for band in bands):
        items.append("存在失效分层，建议复盘该分层的入场价格和市场环境。")
    items.append("每次推荐后都保留快照，并用 5/10/20 日表现更新校准结果。")
    return items[:4]


def _dimension_label(dimension: str, key: str) -> str:
    if key == "unknown":
        return {
            "strategy": "策略未知",
            "factor": "因子未知",
            "industry": "行业未知",
            "theme": "主题未知",
            "market_regime": "信号时点市场环境未知",
        }.get(dimension, "信号未知")
    if dimension in {"factor", "signal"}:
        return _signal_label(key)
    return key


def _signal_label(signal: str) -> str:
    labels = {
        "fund_flow_positive": "资金净流入",
        "dragon_tiger_net_buy": "龙虎榜净买入",
        "limit_up_member": "涨停池成员",
        "risk_event_watch": "事件风险观察",
        "research_coverage": "研报覆盖",
        "quality_high_quality": "高质量推荐",
        "quality_quality_candidate": "质量候选",
        "quality_low_quality": "低质量推荐",
        "quality_watchlist": "观察推荐",
        "quality_risk_filtered": "风险过滤",
        "insufficient_history": "历史不足",
        "overextended": "短线过热",
        "weak_data_quality": "数据质量偏弱",
        "poor_risk_reward": "盈亏比不足",
        "low_liquidity": "流动性偏弱",
        "high_volatility": "波动偏高",
        "incomplete_trade_plan": "交易计划不完整",
        "too_close_to_no_chase": "接近不追高位",
    }
    return labels.get(signal, signal)


def _format_rate(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _format_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}%"


def _average(values: list[float | None]) -> float | None:
    real = [value for value in values if value is not None]
    if not real:
        return None
    return round(sum(real) / len(real), 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
