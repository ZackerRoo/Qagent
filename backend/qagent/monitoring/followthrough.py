from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from qagent.monitoring.outcomes import (
    OpportunityOutcome,
    RecommendationClosureSummary,
    RecommendationClosureWindow,
)


class FollowThroughOutcomeAction(BaseModel):
    snapshot_id: str
    instrument_id: str
    instrument_label: str | None = None
    signal_date: date | None = None
    outcome_status: str
    triggered: bool | None = None
    return_5d: float | None = None
    return_10d: float | None = None
    return_20d: float | None = None
    max_drawdown_pct: float | None = None
    max_runup_pct: float | None = None
    trigger_price: Decimal | None = None
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None
    severity: str
    action: str
    reason: str


class RecommendationFollowThroughCenter(BaseModel):
    as_of: date
    headline: str
    verdict: str
    health_score: float = Field(ge=0, le=1)
    primary_window_days: int
    windows: list[RecommendationClosureWindow]
    focus_outcomes: list[FollowThroughOutcomeAction]
    action_items: list[str]
    data_health: dict[str, str] = Field(default_factory=dict)


def build_recommendation_followthrough_center(
    closure: RecommendationClosureSummary,
    *,
    data_health: dict[str, str] | None = None,
    focus_limit: int = 8,
) -> RecommendationFollowThroughCenter:
    primary_window = _select_primary_window(closure.windows)
    health_score = _health_score(primary_window)
    verdict = _center_verdict(primary_window, health_score)
    focus_outcomes = [
        _outcome_action(outcome) for outcome in closure.latest_outcomes[:focus_limit]
    ]
    return RecommendationFollowThroughCenter(
        as_of=closure.as_of,
        headline=_headline(primary_window, verdict),
        verdict=verdict,
        health_score=health_score,
        primary_window_days=primary_window.window_days,
        windows=closure.windows,
        focus_outcomes=focus_outcomes,
        action_items=_action_items(primary_window, verdict, len(focus_outcomes)),
        data_health={
            **(data_health or {}),
            "followthrough_windows": ",".join(str(item.window_days) for item in closure.windows),
            "as_of": str(closure.as_of),
            "focus_outcomes": str(len(focus_outcomes)),
        },
    )


def _select_primary_window(
    windows: list[RecommendationClosureWindow],
) -> RecommendationClosureWindow:
    if not windows:
        return RecommendationClosureWindow(
            window_days=30,
            sample_count=0,
            completed_count=0,
            pending_count=0,
            triggered_count=0,
            target_hit_count=0,
            stopped_count=0,
            win_count=0,
            completion_rate=None,
            trigger_rate=None,
            target_hit_rate=None,
            stop_rate=None,
            win_rate=None,
            avg_return_5d=None,
            avg_return_10d=None,
            avg_return_20d=None,
            avg_return_60d=None,
            max_drawdown_pct=None,
            best_runup_pct=None,
            verdict="样本不足",
        )
    for minimum_completed in (3, 2, 1):
        for window in windows:
            if window.completed_count >= minimum_completed:
                return window
    return windows[0]


def _health_score(window: RecommendationClosureWindow) -> float:
    if window.sample_count == 0:
        return 0
    win_component = _coalesce(window.win_rate, window.target_hit_rate, 0)
    avg_component = _clamp(0.5 + (_coalesce(window.avg_return_10d, 0) / 20), 0, 1)
    drawdown_component = _clamp(
        1 - (abs(_coalesce(window.max_drawdown_pct, 0)) / 20),
        0,
        1,
    )
    stop_component = 1 - _coalesce(window.stop_rate, 0)
    trigger_component = _coalesce(window.trigger_rate, 0)
    completion_component = _coalesce(window.completion_rate, 0)
    score = (
        win_component * 0.25
        + avg_component * 0.2
        + drawdown_component * 0.2
        + stop_component * 0.15
        + trigger_component * 0.1
        + completion_component * 0.1
    )
    if window.sample_count < 3 or window.completed_count < 2:
        score = min(score, 0.45)
    return round(_clamp(score, 0, 1), 4)


def _center_verdict(window: RecommendationClosureWindow, health_score: float) -> str:
    if window.sample_count < 3 or window.completed_count < 2:
        return "样本不足"
    avg_return = _coalesce(window.avg_return_10d, 0)
    if (
        (window.stop_rate is not None and window.stop_rate >= 0.45)
        or avg_return <= -1.5
        or (
            window.max_drawdown_pct is not None
            and window.max_drawdown_pct <= -8
            and _coalesce(window.win_rate, 0) < 0.5
        )
    ):
        return "需要降权"
    if health_score >= 0.62 and (
        (window.win_rate is not None and window.win_rate >= 0.5)
        or (window.target_hit_rate is not None and window.target_hit_rate >= 0.35)
    ):
        return "表现健康"
    return "需要观察"


def _headline(window: RecommendationClosureWindow, verdict: str) -> str:
    if window.sample_count == 0:
        return "还没有可复盘的推荐记录，先完成一次扫描或等待模拟盘产生信号。"
    win_rate = _format_rate(window.win_rate)
    stop_rate = _format_rate(window.stop_rate)
    avg_return = _format_pct(window.avg_return_10d)
    return (
        f"近 {window.window_days} 天推荐闭环：{verdict}，"
        f"胜率 {win_rate}，10 日均值 {avg_return}，止损触发 {stop_rate}，"
        f"已完成 {window.completed_count}/{window.sample_count} 个样本。"
    )


def _action_items(
    window: RecommendationClosureWindow,
    verdict: str,
    focus_count: int,
) -> list[str]:
    if window.sample_count == 0:
        return [
            "先在今日页完成一次机会扫描，系统才会记录推荐并跟踪后续表现。",
            "有新推荐后，把想验证的标的加入模拟盘，后续胜率和回撤会自动进入闭环。",
        ]
    items: list[str] = []
    if window.pending_count > window.completed_count:
        items.append("当前未完成样本偏多，先把结果当作跟踪面板，不要过早判断策略强弱。")
    if verdict == "表现健康":
        items.append("近期推荐验证较健康，可以维持当前仓位上限，但仍按触发价和止损执行。")
    elif verdict == "需要降权":
        items.append("近期命中止损或回撤偏高，建议降低同类信号权重并收紧单笔风险。")
    else:
        items.append("推荐质量还需要继续观察，优先选择触发明确、止损空间可控的机会。")
    if focus_count:
        items.append("重点查看下方每条推荐的状态动作：达标样本保留，止损样本复盘原因。")
    items.append("把已买入或准备买入的标的同步到模拟盘，形成现金、仓位和收益曲线闭环。")
    return items[:4]


def _outcome_action(outcome: OpportunityOutcome) -> FollowThroughOutcomeAction:
    severity, action, reason = _classify_outcome(outcome)
    return FollowThroughOutcomeAction(
        snapshot_id=outcome.snapshot_id,
        instrument_id=outcome.instrument_id,
        instrument_label=outcome.instrument_label,
        signal_date=outcome.signal_date,
        outcome_status=outcome.outcome_status,
        triggered=outcome.triggered,
        return_5d=outcome.return_5d,
        return_10d=outcome.return_10d,
        return_20d=outcome.return_20d,
        max_drawdown_pct=outcome.max_drawdown_pct,
        max_runup_pct=outcome.max_runup_pct,
        trigger_price=outcome.trigger_price,
        initial_stop=outcome.initial_stop,
        target_1=outcome.target_1,
        severity=severity,
        action=action,
        reason=reason,
    )


def _classify_outcome(outcome: OpportunityOutcome) -> tuple[str, str, str]:
    if outcome.outcome_status == "pending":
        return (
            "watch",
            "等待触发和后续 K 线",
            "推荐后还没有足够未来交易日，暂时只记录为待验证样本。",
        )
    if outcome.outcome_status == "target_1_hit":
        return (
            "positive",
            "目标达成，保留为成功样本",
            "后续价格触及第一目标，说明这条推荐的买点和目标有效。",
        )
    if outcome.outcome_status == "stopped":
        return (
            "risk",
            "命中止损，同类信号降权",
            "后续低点跌破初始止损，复盘时应检查入场位置和市场环境。",
        )
    if outcome.outcome_status == "lagging":
        return (
            "risk",
            "降级观察，严格执行止损",
            f"10 日收益为 {_format_pct(outcome.return_10d)}，走势没有兑现推荐假设。",
        )
    if _coalesce(outcome.return_10d, 0) >= 2 or _coalesce(outcome.max_runup_pct, 0) >= 5:
        return (
            "positive",
            "继续跟踪，可上移保护线",
            "推荐后已有正收益或明显冲高，适合把风险线从初始止损上移。",
        )
    return (
        "watch",
        "继续观察，等待确认",
        "推荐后暂未到目标也未止损，需要结合触发价和后续成交继续观察。",
    )


def _coalesce(*values: float | None) -> float:
    for value in values:
        if value is not None:
            return value
    return 0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _format_rate(value: float | None) -> str:
    if value is None:
        return "暂无"
    return f"{value * 100:.0f}%"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "暂无"
    return f"{value:+.2f}%"
