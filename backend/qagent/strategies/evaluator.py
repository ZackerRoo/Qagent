import pandas as pd

from qagent.domain.enums import SignalType
from qagent.domain.models import Signal
from qagent.strategy_data.models import AnalystInsight, EarningsEvent, FundamentalSnapshot
from qagent.strategies.models import StrategyDefinition, StrategyEvaluation
from qagent.strategies.registry import StrategyRegistry, default_strategy_registry


class StrategyEvaluator:
    def __init__(self, registry: StrategyRegistry | None = None):
        self.registry = registry or default_strategy_registry()

    def evaluate(
        self,
        instrument_id: str,
        signals: list[Signal],
        bars: pd.DataFrame,
        context: dict[str, object] | None = None,
    ) -> list[StrategyEvaluation]:
        signal_by_type = {signal.signal_type.value: signal for signal in signals}
        available_data = {"daily_ohlcv", "signals"}
        if context:
            available_data.update(str(item) for item in context.get("available_data", []))

        evaluations: list[StrategyEvaluation] = []
        for definition in self.registry.all():
            if definition.strategy_id == "trend_momentum_stage2":
                evaluations.append(self._trend_momentum(definition, signal_by_type))
            elif definition.strategy_id == "breakout_volume_confirmation":
                evaluations.append(self._breakout_volume(definition, signal_by_type, instrument_id))
            elif definition.strategy_id == "healthy_pullback":
                evaluations.append(self._healthy_pullback(definition, signal_by_type))
            elif definition.strategy_id == "gf_dma_health":
                evaluations.append(self._gf_dma_health(definition, signal_by_type))
            elif definition.strategy_id == "catalyst_financial_transmission":
                evaluations.append(self._catalyst(definition, context, available_data))
            elif definition.strategy_id == "pead_earnings_drift":
                evaluations.append(
                    self._pead_earnings_drift(definition, instrument_id, bars, context, available_data)
                )
            elif definition.strategy_id == "analyst_revision_momentum":
                evaluations.append(
                    self._analyst_revision_momentum(
                        definition, instrument_id, bars, context, available_data
                    )
                )
            elif definition.strategy_id == "tam_adj_peg_growth":
                evaluations.append(self._tam_adj_peg(definition, instrument_id, context, available_data))
            elif definition.strategy_id == "bayesian_intrinsic_growth":
                evaluations.append(
                    self._bayesian_intrinsic_growth(definition, instrument_id, context, available_data)
                )
            elif definition.strategy_id == "short_squeeze_risk":
                evaluations.append(self._short_squeeze(definition, signal_by_type, available_data))
            else:
                evaluations.append(self._missing_data(definition, available_data))
        return evaluations

    def _trend_momentum(
        self, definition: StrategyDefinition, signal_by_type: dict[str, Signal]
    ) -> StrategyEvaluation:
        trend = signal_by_type.get(SignalType.TREND_STRENGTH.value)
        confirmations = [
            name
            for name in [SignalType.BREAKOUT.value, SignalType.VOLUME_ANOMALY.value]
            if name in signal_by_type
        ]
        if trend is None:
            return self._inactive(definition, evidence={"reason": "trend_strength signal absent"})

        score = min(trend.score + 0.05 * len(confirmations), 1.0)
        status = "passed" if score >= 0.7 else "watch"
        return self._evaluation(
            definition,
            status=status,
            score=round(score, 4),
            triggers=[SignalType.TREND_STRENGTH.value],
            confirmations=confirmations,
            evidence=trend.evidence,
            score_components={"trend_strength": trend.score, "confirmations": 0.05 * len(confirmations)},
        )

    def _breakout_volume(
        self,
        definition: StrategyDefinition,
        signal_by_type: dict[str, Signal],
        instrument_id: str,
    ) -> StrategyEvaluation:
        breakout = signal_by_type.get(SignalType.BREAKOUT.value)
        volume = signal_by_type.get(SignalType.VOLUME_ANOMALY.value)
        limit_status = signal_by_type.get(SignalType.LIMIT_STATUS.value)
        confirmations = [SignalType.VOLUME_ANOMALY.value] if volume else []
        if instrument_id.startswith("CN:") and limit_status:
            confirmations.append(SignalType.LIMIT_STATUS.value)

        if breakout and volume:
            score = round(min(breakout.score * 0.6 + volume.score * 0.4, 1.0), 4)
            evidence = {**breakout.evidence, **{f"volume_{k}": v for k, v in volume.evidence.items()}}
            if limit_status:
                evidence.update({f"limit_{k}": v for k, v in limit_status.evidence.items()})
            return self._evaluation(
                definition,
                status="passed" if score >= 0.65 else "watch",
                score=score,
                triggers=[SignalType.BREAKOUT.value, SignalType.VOLUME_ANOMALY.value],
                confirmations=confirmations,
                evidence=evidence,
                score_components={"breakout": breakout.score, "volume": volume.score},
            )

        if breakout:
            return self._evaluation(
                definition,
                status="watch",
                score=round(breakout.score * 0.7, 4),
                triggers=[SignalType.BREAKOUT.value],
                confirmations=confirmations,
                evidence=breakout.evidence,
                score_components={"breakout": breakout.score, "volume": 0.0},
            )
        return self._inactive(definition, evidence={"reason": "breakout signal absent"})

    def _healthy_pullback(
        self, definition: StrategyDefinition, signal_by_type: dict[str, Signal]
    ) -> StrategyEvaluation:
        pullback = signal_by_type.get(SignalType.PULLBACK.value)
        trend = signal_by_type.get(SignalType.TREND_STRENGTH.value)
        if pullback and trend:
            score = round(min(pullback.score * 0.6 + trend.score * 0.4, 1.0), 4)
            return self._evaluation(
                definition,
                status="passed" if score >= 0.65 else "watch",
                score=score,
                triggers=[SignalType.PULLBACK.value],
                confirmations=[SignalType.TREND_STRENGTH.value],
                evidence={**pullback.evidence, "trend_score": trend.score},
                score_components={"pullback": pullback.score, "trend": trend.score},
            )
        if trend:
            return self._evaluation(
                definition,
                status="watch",
                score=round(trend.score * 0.45, 4),
                triggers=[],
                confirmations=[SignalType.TREND_STRENGTH.value],
                evidence={"reason": "trend exists but pullback has not formed", **trend.evidence},
                score_components={"pullback": 0.0, "trend": trend.score},
            )
        return self._inactive(definition, evidence={"reason": "trend and pullback signals absent"})

    def _gf_dma_health(
        self, definition: StrategyDefinition, signal_by_type: dict[str, Signal]
    ) -> StrategyEvaluation:
        trend = signal_by_type.get(SignalType.TREND_STRENGTH.value)
        if trend is None:
            return self._inactive(definition, evidence={"reason": "moving-average trend absent"})

        close = float(trend.evidence.get("close", 0))
        ma_20 = float(trend.evidence.get("ma_20", 0))
        ma_50 = float(trend.evidence.get("ma_50", 0))
        close_vs_ma_20 = _pct_distance(close, ma_20)
        close_vs_ma_50 = _pct_distance(close, ma_50)
        overextension_penalty = max(close_vs_ma_20 - 12, 0) / 40 + max(close_vs_ma_50 - 25, 0) / 60
        score = round(max(min(trend.score - overextension_penalty, 1.0), 0.0), 4)
        status = "passed" if score >= 0.65 else "watch"
        return self._evaluation(
            definition,
            status=status,
            score=score,
            triggers=[SignalType.TREND_STRENGTH.value],
            confirmations=[],
            evidence={
                "close": close,
                "ma_20": ma_20,
                "ma_50": ma_50,
                "close_vs_ma_20_pct": close_vs_ma_20,
                "close_vs_ma_50_pct": close_vs_ma_50,
                "overextension_penalty": round(overextension_penalty, 4),
            },
            score_components={"trend_health": trend.score, "overextension_penalty": -overextension_penalty},
        )

    def _catalyst(
        self,
        definition: StrategyDefinition,
        context: dict[str, object] | None,
        available_data: set[str],
    ) -> StrategyEvaluation:
        hypotheses = context.get("catalyst_hypotheses", []) if context else []
        if not hypotheses:
            return self._missing_data(definition, available_data)
        confidence = max(float(getattr(item, "confidence", 0)) for item in hypotheses)
        return self._evaluation(
            definition,
            status="passed" if confidence >= 0.7 else "watch",
            score=round(confidence, 4),
            triggers=["event_catalyst"],
            confirmations=["financial_transmission_review"],
            evidence={"hypotheses": len(hypotheses), "max_confidence": confidence},
            score_components={"catalyst_confidence": confidence},
        )

    def _pead_earnings_drift(
        self,
        definition: StrategyDefinition,
        instrument_id: str,
        bars: pd.DataFrame,
        context: dict[str, object] | None,
        available_data: set[str],
    ) -> StrategyEvaluation:
        event = _latest_earnings_event(instrument_id, context)
        missing = _pead_missing_data(definition, available_data, event)
        if missing:
            return self._evaluation(
                definition,
                status="missing_data",
                score=0.0,
                evidence={"reason": "earnings actuals, estimates, or announcement timing unavailable"},
                missing_data=missing,
            )

        metrics = _pead_metrics(bars, event)
        if metrics is None:
            return self._evaluation(
                definition,
                status="missing_data",
                score=0.0,
                evidence={"reason": "earnings date is not present in the price history"},
                missing_data=["announcement_price_reaction"],
            )

        earnings_surprise = min(
            max(metrics["eps_surprise_pct"], 0) / 25 * 0.6
            + max(metrics["revenue_surprise_pct"], 0) / 10 * 0.4,
            1.0,
        )
        reaction = _reasonable_reaction_score(metrics["announcement_return_pct"])
        volume = min(metrics["volume_ratio"] / 2.5, 1.0)
        drift = 1.0 if metrics["latest_close"] >= metrics["earnings_day_close"] else 0.35
        guidance = 1.0 if str(event.guidance).lower() == "raised" else 0.5
        score = round(
            min(
                earnings_surprise * 0.35
                + reaction * 0.2
                + volume * 0.15
                + drift * 0.2
                + guidance * 0.1,
                1.0,
            ),
            4,
        )
        confirmations = ["reasonable_initial_reaction", "volume_expansion"]
        if guidance == 1.0:
            confirmations.append("guidance_raised")
        if drift == 1.0:
            confirmations.append("post_earnings_hold")

        return self._evaluation(
            definition,
            status="passed" if score >= 0.7 else "watch",
            score=score,
            triggers=["earnings_surprise"],
            confirmations=confirmations,
            evidence={
                **metrics,
                "guidance": event.guidance,
                "announcement_date": event.announcement_date.isoformat(),
            },
            score_components={
                "earnings_surprise": round(earnings_surprise, 4),
                "initial_reaction": round(reaction, 4),
                "volume_expansion": round(volume, 4),
                "drift_confirmation": round(drift, 4),
                "guidance": round(guidance, 4),
            },
        )

    def _analyst_revision_momentum(
        self,
        definition: StrategyDefinition,
        instrument_id: str,
        bars: pd.DataFrame,
        context: dict[str, object] | None,
        available_data: set[str],
    ) -> StrategyEvaluation:
        insight = _latest_analyst_insight(instrument_id, context)
        missing = _missing_requirements(definition, available_data)
        if insight is None or not insight.has_revision_inputs:
            if insight is None:
                missing.extend(["analyst_estimates", "revision_timestamps"])
            else:
                if insight.revision_date is None:
                    missing.append("revision_timestamps")
                if not any(
                    value is not None
                    for value in [
                        insight.eps_revision_pct,
                        insight.revenue_revision_pct,
                        insight.target_revision_pct,
                    ]
                ):
                    missing.append("analyst_estimates")
            return self._evaluation(
                definition,
                status="missing_data",
                score=0.0,
                evidence={"reason": "analyst estimate or revision timestamp unavailable"},
                missing_data=sorted(set(missing)),
            )

        eps_revision = _float_or_zero(insight.eps_revision_pct)
        revenue_revision = _float_or_zero(insight.revenue_revision_pct)
        target_revision = _float_or_zero(insight.target_revision_pct)
        revision_strength = min(max(eps_revision, revenue_revision, target_revision, 0) / 25, 1.0)
        rating_balance = _float_or_default(insight.bullish_rating_ratio, 0.5)
        target_upside = _float_or_zero(insight.target_upside_pct)
        target_upside_score = min(max(target_upside, 0) / 30, 1.0)
        recency_score = _revision_recency_score(bars, insight)
        score = round(
            min(
                revision_strength * 0.4
                + rating_balance * 0.25
                + target_upside_score * 0.2
                + recency_score * 0.15,
                1.0,
            ),
            4,
        )
        return self._evaluation(
            definition,
            status="passed" if score >= 0.7 else "watch",
            score=score,
            triggers=["estimate_revision"],
            confirmations=["analyst_rating_balance", "target_price_context"],
            evidence={
                "revision_date": insight.revision_date.isoformat() if insight.revision_date else None,
                "eps_revision_pct": round(eps_revision, 4),
                "revenue_revision_pct": round(revenue_revision, 4),
                "target_revision_pct": round(target_revision, 4),
                "target_upside_pct": round(target_upside, 4),
                "bullish_rating_ratio": round(rating_balance, 4),
                "provider": insight.provider,
            },
            score_components={
                "revision_strength": round(revision_strength, 4),
                "rating_balance": round(rating_balance, 4),
                "target_upside": round(target_upside_score, 4),
                "revision_recency": round(recency_score, 4),
            },
        )

    def _tam_adj_peg(
        self,
        definition: StrategyDefinition,
        instrument_id: str,
        context: dict[str, object] | None,
        available_data: set[str],
    ) -> StrategyEvaluation:
        snapshot = _latest_fundamental(instrument_id, context)
        missing = _fundamental_missing_data(definition, available_data, snapshot)
        if missing:
            return self._evaluation(
                definition,
                status="missing_data",
                score=0.0,
                evidence={"reason": "fundamental growth, valuation, or TAM proxy unavailable"},
                missing_data=missing,
            )

        growth_pct = _max_metric(snapshot.revenue_growth_pct, snapshot.earnings_growth_pct)
        margin_pct = _max_metric(snapshot.net_margin_pct, snapshot.operating_margin_pct)
        growth_score = min(max(growth_pct, 0) / 40, 1.0)
        margin_score = min(max(margin_pct, 0) / 20, 1.0)
        valuation_score = _valuation_sanity_score(snapshot)
        tam_score = _market_room_score(snapshot.market_cap)
        score = round(
            min(
                growth_score * 0.3
                + margin_score * 0.2
                + valuation_score * 0.25
                + tam_score * 0.25,
                1.0,
            ),
            4,
        )
        return self._evaluation(
            definition,
            status="passed" if score >= 0.7 else "watch",
            score=score,
            triggers=["free_fundamental_growth"],
            confirmations=["valuation_multiples", "tam_proxy"],
            evidence={
                "as_of_date": snapshot.as_of_date.isoformat(),
                "growth_pct": round(growth_pct, 4),
                "margin_pct": round(margin_pct, 4),
                "market_cap": _float_or_zero(snapshot.market_cap),
                "peg_ratio": _float_or_default(snapshot.peg_ratio, 0.0),
                "tam_assumption_source": "free_fundamental_proxy",
                "provider": snapshot.provider,
            },
            score_components={
                "growth": round(growth_score, 4),
                "margin_quality": round(margin_score, 4),
                "valuation_sanity": round(valuation_score, 4),
                "market_room": round(tam_score, 4),
            },
        )

    def _bayesian_intrinsic_growth(
        self,
        definition: StrategyDefinition,
        instrument_id: str,
        context: dict[str, object] | None,
        available_data: set[str],
    ) -> StrategyEvaluation:
        snapshot = _latest_fundamental(instrument_id, context)
        missing = _fundamental_missing_data(definition, available_data, snapshot)
        if missing:
            return self._evaluation(
                definition,
                status="missing_data",
                score=0.0,
                evidence={"reason": "fundamental growth, valuation, or growth prior unavailable"},
                missing_data=missing,
            )

        growth_pct = _max_metric(snapshot.revenue_growth_pct, snapshot.earnings_growth_pct)
        margin_pct = _max_metric(snapshot.net_margin_pct, snapshot.operating_margin_pct)
        growth_score = min(max(growth_pct, 0) / 35, 1.0)
        margin_score = min(max(margin_pct, 0) / 22, 1.0)
        valuation_score = _valuation_sanity_score(snapshot)
        prior = _durable_growth_prior(growth_pct, snapshot.market_cap)
        posterior = round(
            min(prior * 0.35 + growth_score * 0.3 + margin_score * 0.25 + valuation_score * 0.1, 1.0),
            4,
        )
        return self._evaluation(
            definition,
            status="passed" if posterior >= 0.65 else "watch",
            score=posterior,
            triggers=["growth_probability_update"],
            confirmations=["fundamental_growth", "valuation_multiples"],
            evidence={
                "as_of_date": snapshot.as_of_date.isoformat(),
                "prior_growth_probability": round(prior, 4),
                "posterior_growth_probability": posterior,
                "growth_pct": round(growth_pct, 4),
                "margin_pct": round(margin_pct, 4),
                "valuation_score": round(valuation_score, 4),
                "growth_prior_source": "free_fundamental_proxy",
                "provider": snapshot.provider,
            },
            score_components={
                "growth_prior": round(prior, 4),
                "growth_evidence": round(growth_score, 4),
                "margin_support": round(margin_score, 4),
                "valuation_pressure": round(valuation_score, 4),
            },
        )

    def _short_squeeze(
        self,
        definition: StrategyDefinition,
        signal_by_type: dict[str, Signal],
        available_data: set[str],
    ) -> StrategyEvaluation:
        limit_status = signal_by_type.get(SignalType.LIMIT_STATUS.value)
        missing = _missing_requirements(definition, available_data)
        if limit_status:
            return self._evaluation(
                definition,
                status="watch",
                score=round(min(limit_status.score * 0.6, 0.6), 4),
                triggers=[SignalType.LIMIT_STATUS.value],
                confirmations=[],
                evidence=limit_status.evidence,
                score_components={"limit_status": limit_status.score, "short_interest": 0.0},
                missing_data=missing,
            )
        return self._missing_data(definition, available_data)

    def _missing_data(
        self, definition: StrategyDefinition, available_data: set[str]
    ) -> StrategyEvaluation:
        return self._evaluation(
            definition,
            status="missing_data",
            score=0.0,
            evidence={"reason": "required data is not available in the current free-data scan"},
            missing_data=_missing_requirements(definition, available_data),
        )

    def _inactive(self, definition: StrategyDefinition, evidence: dict[str, object]) -> StrategyEvaluation:
        return self._evaluation(definition, status="inactive", score=0.0, evidence=evidence)

    def _evaluation(
        self,
        definition: StrategyDefinition,
        status: str,
        score: float,
        triggers: list[str] | None = None,
        confirmations: list[str] | None = None,
        evidence: dict[str, object] | None = None,
        score_components: dict[str, float] | None = None,
        missing_data: list[str] | None = None,
    ) -> StrategyEvaluation:
        return StrategyEvaluation(
            strategy_id=definition.strategy_id,
            name=definition.name,
            family=definition.family,
            role=definition.role,
            status=status,
            score=score,
            horizon=definition.horizon,
            preconditions=[f"{item} available" for item in definition.required_data if item == "daily_ohlcv"],
            triggers=triggers or [],
            confirmations=confirmations or [],
            invalidation=definition.invalidation_template,
            evidence=evidence or {},
            score_components=score_components or {},
            missing_data=missing_data or [],
            data_requirements=definition.required_data,
        )


def _missing_requirements(definition: StrategyDefinition, available_data: set[str]) -> list[str]:
    return [item for item in definition.required_data if item not in available_data]


def _pct_distance(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator - 1) * 100, 4)


def _latest_earnings_event(
    instrument_id: str,
    context: dict[str, object] | None,
) -> EarningsEvent | None:
    if not context:
        return None
    events = [
        event
        for event in context.get("earnings_events", [])
        if isinstance(event, EarningsEvent) and event.instrument_id == instrument_id
    ]
    if not events:
        return None
    return max(events, key=lambda item: item.announcement_date)


def _latest_fundamental(
    instrument_id: str,
    context: dict[str, object] | None,
) -> FundamentalSnapshot | None:
    if not context:
        return None
    snapshots = [
        item
        for item in context.get("fundamentals", [])
        if isinstance(item, FundamentalSnapshot) and item.instrument_id == instrument_id
    ]
    if not snapshots:
        return None
    return max(snapshots, key=lambda item: item.as_of_date)


def _latest_analyst_insight(
    instrument_id: str,
    context: dict[str, object] | None,
) -> AnalystInsight | None:
    if not context:
        return None
    insights = [
        item
        for item in context.get("analyst_insights", [])
        if isinstance(item, AnalystInsight) and item.instrument_id == instrument_id
    ]
    if not insights:
        return None
    return max(insights, key=lambda item: item.revision_date or item.as_of_date)


def _fundamental_missing_data(
    definition: StrategyDefinition,
    available_data: set[str],
    snapshot: FundamentalSnapshot | None,
) -> list[str]:
    missing = _missing_requirements(definition, available_data)
    if snapshot is None:
        missing.extend(["fundamentals", "valuation_multiples"])
        return sorted(set(missing))
    if not snapshot.has_growth_inputs:
        missing.append("fundamentals")
    if not snapshot.has_valuation_inputs:
        missing.append("valuation_multiples")
    return sorted(set(missing))


def _pead_missing_data(
    definition: StrategyDefinition,
    available_data: set[str],
    event: EarningsEvent | None,
) -> list[str]:
    missing = _missing_requirements(definition, available_data)
    if event is None:
        return missing
    if event.actual_eps is None or event.actual_revenue is None:
        missing.append("earnings_actuals")
    if event.estimated_eps is None or event.estimated_revenue is None:
        missing.append("earnings_estimates")
    if event.announcement_time not in {"bmo", "amc", "intraday"}:
        missing.append("announcement_timestamp")
    return sorted(set(missing))


def _pead_metrics(bars: pd.DataFrame, event: EarningsEvent) -> dict[str, float] | None:
    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    matches = ordered.index[ordered["trade_date"] == event.announcement_date].tolist()
    if not matches:
        return None
    event_index = matches[0]
    if event_index == 0:
        return None

    event_row = ordered.loc[event_index]
    prior_row = ordered.loc[event_index - 1]
    latest_row = ordered.iloc[-1]
    prior_volume_window = ordered.iloc[max(event_index - 20, 0) : event_index]["volume"]
    average_volume = float(prior_volume_window.mean()) if not prior_volume_window.empty else 0.0
    volume_ratio = float(event_row["volume"]) / average_volume if average_volume > 0 else 0.0

    return {
        "eps_surprise_pct": _surprise_pct(event.actual_eps, event.estimated_eps),
        "revenue_surprise_pct": _surprise_pct(event.actual_revenue, event.estimated_revenue),
        "announcement_return_pct": _pct_distance(float(event_row["close"]), float(prior_row["close"])),
        "volume_ratio": round(volume_ratio, 4),
        "earnings_day_low": float(event_row["low"]),
        "earnings_day_high": float(event_row["high"]),
        "earnings_day_close": float(event_row["close"]),
        "latest_close": float(latest_row["close"]),
        "days_since_earnings": float(len(ordered) - event_index - 1),
    }


def _surprise_pct(actual, estimate) -> float:
    if actual is None or estimate is None or estimate == 0:
        return 0.0
    return round(float((actual - estimate) / abs(estimate) * 100), 4)


def _reasonable_reaction_score(announcement_return_pct: float) -> float:
    if announcement_return_pct < -2:
        return 0.2
    if announcement_return_pct <= 12:
        return 0.9
    if announcement_return_pct <= 20:
        return 0.55
    return 0.25


def _float_or_zero(value) -> float:
    return _float_or_default(value, 0.0)


def _float_or_default(value, default: float) -> float:
    if value is None:
        return default
    return float(value)


def _max_metric(*values) -> float:
    numbers = [float(value) for value in values if value is not None]
    return max(numbers) if numbers else 0.0


def _valuation_sanity_score(snapshot: FundamentalSnapshot) -> float:
    if snapshot.peg_ratio is not None:
        peg = float(snapshot.peg_ratio)
        if peg <= 1:
            return 1.0
        if peg <= 1.5:
            return 0.8
        if peg <= 2.5:
            return 0.55
        return 0.25
    growth_pct = _max_metric(snapshot.revenue_growth_pct, snapshot.earnings_growth_pct)
    forward_pe = _float_or_default(snapshot.forward_pe or snapshot.pe_ratio, 0.0)
    if growth_pct <= 0 or forward_pe <= 0:
        return 0.4
    implied_peg = forward_pe / growth_pct
    if implied_peg <= 1:
        return 0.9
    if implied_peg <= 1.5:
        return 0.7
    return 0.4


def _market_room_score(market_cap) -> float:
    market_cap_float = _float_or_default(market_cap, 0.0)
    if market_cap_float <= 0:
        return 0.45
    if market_cap_float <= 10_000_000_000:
        return 0.9
    if market_cap_float <= 50_000_000_000:
        return 0.72
    if market_cap_float <= 200_000_000_000:
        return 0.52
    return 0.32


def _durable_growth_prior(growth_pct: float, market_cap) -> float:
    room_score = _market_room_score(market_cap)
    if growth_pct >= 30:
        base = 0.68
    elif growth_pct >= 20:
        base = 0.58
    elif growth_pct >= 10:
        base = 0.48
    else:
        base = 0.35
    return min(base * 0.75 + room_score * 0.25, 0.85)


def _revision_recency_score(bars: pd.DataFrame, insight: AnalystInsight) -> float:
    if insight.revision_date is None or bars.empty:
        return 0.5
    latest_trade_date = bars.sort_values("trade_date").iloc[-1]["trade_date"]
    days = (latest_trade_date - insight.revision_date).days
    if days <= 30:
        return 1.0
    if days <= 90:
        return 0.7
    return 0.4
