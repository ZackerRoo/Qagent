RISK_TERMS = ["definitely", "guarantee", "sure win", "稳赚", "必涨"]


def answer_question(question: str, context: dict[str, object]) -> str:
    lowered = question.lower()
    if any(term in lowered for term in RISK_TERMS):
        return (
            "I cannot guarantee returns. I can explain the setup, trigger, "
            "invalidation, and risks."
        )

    if _is_recommendation_question(question, lowered):
        cards = context.get("cards")
        if isinstance(cards, list):
            return _answer_recommendations(question, cards)

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


def _is_recommendation_question(question: str, lowered: str) -> bool:
    english_terms = ["recommend", "what stocks", "which stocks", "top picks", "what should i buy"]
    chinese_terms = ["推荐", "买什么", "哪些股票", "什么股票", "今天看什么", "可以买"]
    return any(term in lowered for term in english_terms) or any(term in question for term in chinese_terms)


def _answer_recommendations(question: str, cards: list[object]) -> str:
    actionable = [card for card in cards if isinstance(card, dict) and card.get("action") != "avoid"]
    ranked = actionable[:3]
    if not ranked:
        return (
            "当前扫描没有可执行机会。可以继续观察触发价、数据缺失和策略健康度；这不是投资建议。"
            if _looks_chinese(question)
            else "The current scan has no actionable opportunity. Keep watching triggers, data gaps, and strategy health; this is not advice."
        )
    if _looks_chinese(question):
        lines = ["当前扫描里优先看这些候选机会："]
        for index, card in enumerate(ranked, start=1):
            lines.append(f"{index}. {_format_cn_recommendation(card)}")
        lines.append("这些是研究型候选买卖计划，不是投资建议；真正执行前要重新确认成交量、触发价和止损。")
        return "\n".join(lines)

    lines = ["Current scan candidates:"]
    for index, card in enumerate(ranked, start=1):
        lines.append(f"{index}. {_format_en_recommendation(card)}")
    lines.append("These are research scenarios, not investment advice. Recheck volume, trigger, and stop before any decision.")
    return "\n".join(lines)


def _format_cn_recommendation(card: dict[str, object]) -> str:
    symbol = card.get("instrument_id", "-")
    action = card.get("action", "watch")
    conviction = _format_float(card.get("conviction_score"))
    trigger = card.get("trigger_price") or "-"
    stop = card.get("initial_stop") or "-"
    target_1 = card.get("target_1") or "-"
    target_2 = card.get("target_2") or "-"
    no_chase = card.get("no_chase_above") or "-"
    risk_reward = _format_float(card.get("risk_reward"))
    factor_score = _format_float(card.get("factor_score"))
    factor_rank = card.get("factor_rank") or "-"
    factor_flags = ", ".join(str(item) for item in card.get("factor_flags") or []) or "-"
    strategy = card.get("primary_strategy_id") or "-"
    caveats = ", ".join(str(item) for item in card.get("data_caveats") or []) or "-"
    return (
        f"{symbol}：动作 {action}，信心 {conviction}，策略 {strategy}。"
        f"因子分 {factor_score}，因子排名 {factor_rank}，因子标签 {factor_flags}。"
        f"买点/触发 {trigger}；不追高 {no_chase}；止损 {stop}；"
        f"目标 {target_1}/{target_2}；盈亏比 {risk_reward}；数据 {caveats}。"
    )


def _format_en_recommendation(card: dict[str, object]) -> str:
    symbol = card.get("instrument_id", "-")
    action = card.get("action", "watch")
    conviction = _format_float(card.get("conviction_score"))
    trigger = card.get("trigger_price") or "-"
    stop = card.get("initial_stop") or "-"
    target_1 = card.get("target_1") or "-"
    target_2 = card.get("target_2") or "-"
    no_chase = card.get("no_chase_above") or "-"
    risk_reward = _format_float(card.get("risk_reward"))
    factor_score = _format_float(card.get("factor_score"))
    factor_rank = card.get("factor_rank") or "-"
    factor_flags = ", ".join(str(item) for item in card.get("factor_flags") or []) or "-"
    strategy = card.get("primary_strategy_id") or "-"
    caveats = ", ".join(str(item) for item in card.get("data_caveats") or []) or "-"
    return (
        f"{symbol}: action {action}, conviction {conviction}, strategy {strategy}. "
        f"Factor score {factor_score}, factor rank {factor_rank}, factor flags {factor_flags}. "
        f"Trigger {trigger}; no chase above {no_chase}; stop {stop}; "
        f"targets {target_1}/{target_2}; risk/reward {risk_reward}; data {caveats}."
    )


def _format_float(value: object) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _looks_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)
