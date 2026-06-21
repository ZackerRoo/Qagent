RISK_TERMS = ["definitely", "guarantee", "sure win", "稳赚", "必涨"]


def answer_question(question: str, context: dict[str, object]) -> str:
    lowered = question.lower()
    if any(term in lowered for term in RISK_TERMS):
        return (
            "I cannot guarantee returns. I can explain the setup, trigger, "
            "invalidation, and risks."
        )

    if any(term in lowered for term in ["buy", "entry", "scenario", "risk"]) or any(
        term in question for term in ["买", "买入", "风险", "情况"]
    ):
        trigger = context.get("trigger_price")
        stop = context.get("initial_stop")
        target = context.get("target_1")
        downside = context.get("downside_pct")
        upside = context.get("target_1_pct")
        no_chase = context.get("no_chase_above")
        if all(value is not None for value in [trigger, stop, target, downside, upside]):
            extra = f" Do not chase above {no_chase}." if no_chase else ""
            return (
                f"If entered at trigger {trigger}, the initial invalidation is {stop} "
                f"({float(downside):.2f}% downside) and target 1 is {target} "
                f"(+{float(upside):.2f}% upside).{extra} This is a scenario, not advice."
            )

    if "why" in lowered or "为什么" in question:
        instrument_id = context.get("instrument_id", "this instrument")
        score = context.get("score")
        signal_summary = context.get("signal_summary")
        primary_strategy_id = context.get("primary_strategy_id")
        strategy_score = context.get("strategy_score")
        strategy_summary = context.get("strategy_summary")
        if strategy_summary:
            return (
                f"{instrument_id} is on the list because the strategy stack includes "
                f"{strategy_summary}. Primary strategy is {primary_strategy_id}; strategy score is "
                f"{strategy_score}. Signal evidence includes {signal_summary}. Review trigger, stop, "
                "targets, missing data, and caveats before making any decision."
            )
        if signal_summary:
            return (
                f"{instrument_id} is on the list because the signal stack includes "
                f"{signal_summary}. Composite score is {score}. Review trigger, stop, "
                "targets, and caveats before making any decision."
            )

    if "stop" in lowered or "止损" in question:
        stop = context.get("initial_stop")
        if stop:
            return f"The current initial stop is {stop}. Treat it as an invalidation/risk level, not advice."

    instrument_id = context.get("instrument_id", "this instrument")
    status = context.get("status", "unknown")
    score = context.get("score")
    if score is not None:
        return (
            f"{instrument_id} is currently in status {status} with score {score}. "
            "Review the trigger, stop, targets, and data caveats before making any decision."
        )
    return f"{instrument_id} is currently in status {status}. Review the card context and caveats."
