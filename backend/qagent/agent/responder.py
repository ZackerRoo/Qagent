RISK_TERMS = ["definitely", "guarantee", "sure win", "稳赚", "必涨"]


def answer_question(question: str, context: dict[str, object]) -> str:
    lowered = question.lower()
    if any(term in lowered for term in RISK_TERMS):
        return (
            "I cannot guarantee returns. I can explain the setup, trigger, "
            "invalidation, and risks."
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
