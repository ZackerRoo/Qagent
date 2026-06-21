import pandas as pd

from qagent.domain.enums import SignalType
from qagent.domain.models import Signal
from qagent.strategy_data.models import EarningsEvent
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
