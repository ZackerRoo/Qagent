ACTION_LABELS_ZH = {
    "candidate_entry": "候选买入",
    "watch_trigger": "等待触发",
    "wait_pullback": "等待回调",
    "avoid": "暂不追/规避",
}

STRATEGY_LABELS_ZH = {
    "trend_momentum_stage2": "二阶段趋势动量",
    "breakout_volume_confirmation": "放量突破确认",
    "healthy_pullback": "健康回调",
    "gf_dma_health": "GF-DMA 趋势健康",
    "catalyst_financial_transmission": "催化到财务传导",
    "pead_earnings_drift": "业绩公告后漂移",
    "analyst_revision_momentum": "分析师上修动量",
    "tam_adj_peg_growth": "TAM 调整 PEG 成长估值",
    "bayesian_intrinsic_growth": "贝叶斯内生成长估值",
    "sector_rotation_regime": "行业轮动与市场环境",
    "short_squeeze_risk": "逼空风险监控",
    "options_flow_confirmation": "期权流确认",
    "insider_institutional_confirmation": "内部人与机构确认",
}

STATUS_LABELS_ZH = {
    "new_idea": "新机会",
    "watch": "观察",
    "setup_ready": "准备",
    "triggered": "已触发",
    "extended": "偏高",
    "active": "进行中",
    "risk_elevated": "风险升高",
    "invalidated": "失效",
    "closed": "关闭",
    "postmortem_done": "已复盘",
    "no_data": "无数据",
    "no_setup": "未形成机会",
    "passed": "通过",
    "missing_data": "缺数据",
    "inactive": "未触发",
    "limited_sample": "样本有限",
    "insufficient_history": "历史不足",
    "pending": "待定",
    "open": "持仓中",
    "target_1_hit": "目标 1 命中",
    "stopped": "止损",
    "time_exit": "时间退出",
}

FACTOR_FLAG_LABELS_ZH = {
    "insufficient_history": "历史不足",
    "overextended": "短线过热",
    "high_volatility": "波动偏高",
    "low_liquidity": "流动性偏弱",
}


def localize_action(value: object) -> str:
    return _lookup(ACTION_LABELS_ZH, value)


def localize_strategy(value: object) -> str:
    return _lookup(STRATEGY_LABELS_ZH, value)


def localize_status(value: object) -> str:
    return _lookup(STATUS_LABELS_ZH, value)


def localize_factor_flag(value: object) -> str:
    return _lookup(FACTOR_FLAG_LABELS_ZH, value)


def localize_caveat(value: object) -> str:
    text = _to_text(value)
    if text == "-":
        return text
    if text == "fixture data":
        return "样例数据"
    if text.startswith("provider: "):
        return f"数据源：{text.removeprefix('provider: ')}"
    if text == "provider: unknown":
        return "数据源：未知"
    return FACTOR_FLAG_LABELS_ZH.get(text, text)


def _lookup(mapping: dict[str, str], value: object) -> str:
    text = _to_text(value)
    if text == "-":
        return text
    return mapping.get(text, text.replace("_", " "))


def _to_text(value: object) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text or "-"
