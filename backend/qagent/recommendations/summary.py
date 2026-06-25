from qagent.domain.models import OpportunityCard, RecommendationSummary


ACTION_LABELS_ZH = {
    "candidate_entry": "可候选买入",
    "watch_trigger": "等待触发",
    "wait_pullback": "等待回踩",
    "avoid": "暂不参与",
    "pending": "观察",
}


def build_recommendation_summary(card: OpportunityCard) -> RecommendationSummary:
    label = card.instrument_label or card.instrument_id
    decision = card.decision
    action = decision.action if decision else "pending"
    stance = ACTION_LABELS_ZH.get(action, "观察")
    trigger = _price(card.entry_plan.trigger_price)
    no_chase = _price(card.entry_plan.no_chase_above)
    stop = _price(card.exit_plan.initial_stop)
    target = _price(card.exit_plan.target_1)
    board = card.trading_constraints.board if card.trading_constraints else "市场"
    context = card.market_context.summary if card.market_context else board

    return RecommendationSummary(
        headline=_headline(label, stance, trigger, no_chase),
        stance=stance,
        buy_timing=_buy_timing(card, trigger, no_chase),
        sell_timing=_sell_timing(stop, target),
        position_note=_position_note(card),
        risk_note=_risk_note(card),
        context_note=f"所处方向：{context}。",
        checklist=_checklist(card),
    )


def _headline(label: str, stance: str, trigger: str, no_chase: str) -> str:
    if trigger != "-":
        return f"{label}：{stance}，重点看触发价 {trigger}，不追高于 {no_chase}。"
    return f"{label}：{stance}，先观察信号是否补齐。"


def _buy_timing(card: OpportunityCard, trigger: str, no_chase: str) -> str:
    if card.entry_plan.entry_type == "pullback":
        return f"买点：回踩不破支撑后重新转强，触发价参考 {trigger}，高于 {no_chase} 不追。"
    if card.entry_plan.entry_type == "pead":
        return f"买点：业绩后趋势继续站稳关键价位，触发价参考 {trigger}，高于 {no_chase} 不追。"
    return f"买点：放量突破或收盘站稳触发价 {trigger} 后再考虑，高于 {no_chase} 不追。"


def _sell_timing(stop: str, target: str) -> str:
    return f"卖出：跌破止损 {stop} 说明假设失效；接近目标 {target} 后分批止盈或上移止损。"


def _position_note(card: OpportunityCard) -> str:
    if not card.decision or card.decision.suggested_risk_pct <= 0:
        return "仓位：当前不建议新开仓，先放入观察。"
    constraints = card.trading_constraints
    lot_note = "按100股整数倍取整" if constraints and constraints.min_lot else "按账户规则取整"
    return (
        f"仓位：单笔风险预算参考 {card.decision.suggested_risk_pct:.2f}% ，"
        f"最大仓位参考 {card.decision.max_position_pct:.2f}% ，{lot_note}。"
    )


def _risk_note(card: OpportunityCard) -> str:
    notes = []
    constraints = card.trading_constraints
    if constraints:
        if constraints.permission_required:
            notes.append(f"{constraints.board}需要确认交易权限")
        if constraints.t_plus_one:
            notes.append("A股T+1会带来隔夜风险")
        if constraints.price_limit_pct:
            notes.append(f"常规涨跌幅按{constraints.price_limit_pct}%处理")
    if card.decision and card.decision.risk_vetoes:
        notes.append("存在风险否决项，需先处理")
    return "风险：" + "；".join(notes or ["暂无硬性风险否决，但仍需复核数据源和成交流动性"]) + "。"


def _checklist(card: OpportunityCard) -> list[str]:
    checks = []
    if card.decision:
        checks.extend(card.decision.verification_checks[:2])
    if card.trading_constraints:
        checks.extend(item.message for item in card.trading_constraints.constraints[:2])
    if card.market_context:
        checks.append(f"确认是否仍跟随{card.market_context.industry}方向。")
    return _dedupe(checks)


def _price(value) -> str:
    return "-" if value is None else str(value)


def _dedupe(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
