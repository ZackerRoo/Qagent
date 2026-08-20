from __future__ import annotations

from datetime import date
from statistics import mean

import pandas as pd
from pydantic import BaseModel, Field

from qagent.market.benchmarks import CN_BENCHMARKS, benchmark_frames_from_bars
from qagent.paper_trading.engine import PaperLedger, PaperLedgerItem, PaperValidationResult
from qagent.storage.paper import (
    PaperAccountSettings,
    PaperResearchBaseline,
    PaperTradeRecord,
    PaperTradeSourceContext,
)
from qagent.storage.repository import WalkForwardRunRecord


CHECKPOINT_SESSIONS = (20, 40, 60)
EXECUTED_TERMINAL_STATUSES = frozenset({"target_1_hit", "stopped", "time_exit"})


class PaperResearchMetricPair(BaseModel):
    key: str
    label: str
    historical: float | int | None
    forward: float | int | None
    unit: str
    note: str


class PaperResearchCheckpoint(BaseModel):
    target_sessions: int
    observed_sessions: int
    progress_pct: float = Field(ge=0, le=100)
    status: str
    checkpoint_date: date | None
    trade_count: int
    closed_trade_count: int
    total_return_pct: float | None
    max_drawdown_pct: float | None
    win_rate: float | None
    average_return_pct: float | None


class PaperForwardFactorResult(BaseModel):
    key: str
    label: str
    sample_count: int
    completed_count: int
    win_rate: float | None
    average_return_pct: float | None
    status: str


class PaperForwardComparisonReport(BaseModel):
    as_of: date
    baseline: PaperResearchBaseline
    scope: str
    headline: str
    observed_sessions: int
    metrics: list[PaperResearchMetricPair]
    checkpoints: list[PaperResearchCheckpoint]
    forward_factors: list[PaperForwardFactorResult]
    market_regimes: list[PaperForwardFactorResult]
    warnings: list[str]
    data_health: dict[str, str]


class PaperCurrentModelMetric(BaseModel):
    key: str
    label: str
    value: float | int | None
    unit: str
    note: str


class PaperCurrentModelBenchmark(BaseModel):
    benchmark_id: str
    name: str
    compared_trades: int
    closed_compared_trades: int
    coverage_pct: float
    average_benchmark_return_pct: float | None
    average_excess_return_pct: float | None
    positive_excess_rate: float | None


class PaperCurrentModelEvaluation(BaseModel):
    as_of: date
    scope: str = "current_model_cohort"
    status: str
    headline: str
    cohort_id: str | None
    feature_set_version: str | None
    recommendation_policy: str | None
    observed_sessions: int
    metrics: list[PaperCurrentModelMetric]
    benchmark: PaperCurrentModelBenchmark | None
    checkpoints: list[PaperResearchCheckpoint]
    warnings: list[str]
    data_health: dict[str, str]


def build_paper_research_baseline_definition(
    *,
    account: PaperAccountSettings,
    walk_forward_run: WalkForwardRunRecord,
    trades: list[PaperTradeRecord],
) -> tuple[date, dict[str, object]]:
    payload = walk_forward_run.payload
    manifest = _object(payload.get("experiment_manifest"))
    metrics = _object(payload.get("top_5_metrics"))
    temporal = _object(payload.get("top_5_temporal_validation"))
    out_of_sample = _object(temporal.get("out_of_sample"))
    benchmarks = [
        item for item in payload.get("benchmarks", []) if isinstance(item, dict)
    ]
    equal_weight = next(
        (
            item
            for item in benchmarks
            if item.get("benchmark_id") == "CN:EQUAL_WEIGHT_ELIGIBLE"
        ),
        {},
    )
    cost_scenarios = [
        item for item in payload.get("cost_sensitivity", []) if isinstance(item, dict)
    ]
    stress = next((item for item in cost_scenarios if item.get("key") == "stress"), {})
    start_date = min((trade.signal_date for trade in trades), default=account.started_at.date())
    definition: dict[str, object] = {
        "schema_version": "paper-research-baseline-v1",
        "provider": walk_forward_run.provider,
        "paper_session": {
            "session_id": account.session_id,
            "started_at": account.started_at.isoformat(),
            "initial_capital": str(account.initial_capital),
            "allocation_per_trade_pct": str(account.allocation_per_trade_pct),
            "max_positions": account.max_positions,
            "transaction_cost_bps": str(account.transaction_cost_bps),
            "slippage_bps": str(account.slippage_bps),
            "take_profit_pct": str(account.take_profit_pct),
            "research_start_date": start_date.isoformat(),
        },
        "historical_reference": {
            "run_id": walk_forward_run.run_id,
            "start_date": walk_forward_run.start_date.isoformat(),
            "end_date": walk_forward_run.end_date.isoformat(),
            "dataset_revision": walk_forward_run.dataset_revision,
            "snapshot_count": walk_forward_run.snapshot_count,
            "trade_count": _integer(metrics.get("trade_count"), walk_forward_run.top_5_trade_count),
            "total_return_pct": _number(
                metrics.get("total_return_pct"), walk_forward_run.top_5_return_pct
            ),
            "out_of_sample_trade_count": _integer(
                out_of_sample.get("sample_count"), walk_forward_run.top_5_oos_trades
            ),
            "out_of_sample_average_return_pct": _number(
                out_of_sample.get("avg_return_pct")
            ),
            "win_rate": _number(metrics.get("win_rate")),
            "max_drawdown_pct": _number(metrics.get("max_drawdown_pct")),
            "turnover_pct": _number(metrics.get("turnover_pct")),
            "total_costs": _number(metrics.get("total_costs")),
            "equal_weight_excess_return_pct": _number(
                equal_weight.get("top_5_excess_return_pct")
            ),
            "stress_return_pct": _number(stress.get("top_5_return_pct")),
            "reproducibility_digest": walk_forward_run.reproducibility_digest,
        },
        "model_identity": {
            "code_revision": str(manifest.get("code_revision", "")),
            "code_dirty": bool(manifest.get("code_dirty", False)),
            "selection_algorithm_version": str(
                manifest.get("selection_algorithm_version", "")
            ),
            "strategy_registry_digest": str(
                manifest.get("strategy_registry_digest", "")
            ),
            "ranking_v4_protocol_digest": str(
                manifest.get("ranking_v4_protocol_digest", "")
            ),
        },
        "reporting_policy": {
            "scope": "research_shadow",
            "scheduler": "local_paper_only",
            "forward_evidence_isolation": True,
            "checkpoints": list(CHECKPOINT_SESSIONS),
        },
    }
    return start_date, definition


def build_paper_forward_comparison(
    *,
    baseline: PaperResearchBaseline,
    ledger: PaperLedger,
    validation: PaperValidationResult,
    trades: list[PaperTradeRecord],
    market_sessions: list[date],
    market_calendar_source: str = "exchange_calendars:XSHG",
    source_contexts: dict[str, PaperTradeSourceContext],
) -> PaperForwardComparisonReport:
    sessions = sorted(
        {
            session
            for session in market_sessions
            if baseline.start_date <= session <= _report_date(ledger, baseline.start_date)
        }
    )
    calendar_source = market_calendar_source
    if not sessions:
        sessions = sorted(
            {
                point.date
                for point in ledger.curve
                if point.date >= baseline.start_date
            }
        )
        calendar_source = "ledger_curve_fallback"
    historical = _object(baseline.definition.get("historical_reference"))
    total_costs = float(ledger.summary.total_fees + ledger.summary.total_slippage)
    metrics = [
        _metric(
            "trade_count",
            "已成交样本",
            _integer(historical.get("trade_count")),
            ledger.summary.closed_trades + ledger.summary.open_trades,
            "笔",
            "历史为完整 Top 5 回放，前向仅统计真实触发成交。",
        ),
        _metric(
            "total_return_pct",
            "累计收益",
            _number(historical.get("total_return_pct")),
            ledger.summary.total_return_pct,
            "%",
            "前向收益含当前未实现盈亏和账户成本。",
        ),
        _metric(
            "average_return_pct",
            "平均单笔收益",
            _number(historical.get("out_of_sample_average_return_pct")),
            ledger.summary.average_return_pct,
            "%",
            "历史列采用样本外均值，避免用训练期结果作比较。",
        ),
        _metric(
            "win_rate",
            "胜率",
            _as_percent(_number(historical.get("win_rate"))),
            _as_percent(ledger.summary.win_rate),
            "%",
            "前向胜率只对已结束真实成交计算。",
        ),
        _metric(
            "max_drawdown_pct",
            "最大回撤",
            _number(historical.get("max_drawdown_pct")),
            ledger.summary.max_drawdown_pct,
            "%",
            "越接近 0 风险越低。",
        ),
        _metric(
            "turnover",
            "换手金额/比例",
            _number(historical.get("turnover_pct")),
            float(ledger.summary.turnover),
            "历史% / 前向元",
            "口径不同，仅用于识别成本压力，不直接比较高低。",
        ),
        _metric(
            "costs",
            "交易成本",
            _number(historical.get("total_costs")),
            round(total_costs, 2),
            "元",
            "均含手续费和滑点假设。",
        ),
    ]
    checkpoints = [
        _checkpoint(
            target_sessions=target,
            sessions=sessions,
            ledger=ledger,
        )
        for target in CHECKPOINT_SESSIONS
    ]
    factors = _group_forward_results(
        ledger.items,
        trades,
        source_contexts,
        dimension="factor",
    )
    regimes = _group_forward_results(
        ledger.items,
        trades,
        source_contexts,
        dimension="regime",
    )
    warnings = [
        "该报告是研究模拟盘诊断，不代表已验证推荐或正式发布。",
        "前向样本只使用本地模拟盘真实触发记录，不回填历史结果。",
    ]
    if calendar_source == "ledger_curve_fallback":
        warnings.append("交易所日历未形成有效检查点，进度暂按账本日期估算。")
    if ledger.summary.closed_trades < 20:
        warnings.append("已结束成交少于 20 笔，胜率和因子分组仍容易被少数交易主导。")
    return PaperForwardComparisonReport(
        as_of=_report_date(ledger, baseline.start_date),
        baseline=baseline,
        scope="research_shadow",
        headline=_headline(ledger, len(sessions)),
        observed_sessions=len(sessions),
        metrics=metrics,
        checkpoints=checkpoints,
        forward_factors=factors,
        market_regimes=regimes,
        warnings=warnings,
        data_health={
            "paper_forward_comparison": "ready",
            "paper_forward_scope": "research_shadow",
            "paper_forward_baseline_immutable": "true",
            "paper_forward_baseline_digest": baseline.definition_digest,
            "paper_forward_calendar_source": calendar_source,
            "paper_forward_sessions": str(len(sessions)),
            "paper_forward_closed_trades": str(ledger.summary.closed_trades),
            "paper_forward_factor_groups": str(len(factors)),
            "paper_forward_regime_groups": str(len(regimes)),
            "paper_forward_validation_verdict": validation.summary.verdict,
        },
    )


def build_paper_current_model_evaluation(
    *,
    cohort_id: str,
    feature_set_version: str,
    recommendation_policy: str,
    ledger: PaperLedger,
    trades: list[PaperTradeRecord],
    market_sessions: list[date],
    benchmark_bars: pd.DataFrame,
    scan_start_date: date,
    as_of_date: date | None = None,
) -> PaperCurrentModelEvaluation:
    """Evaluate only the active model cohort against a matched broad-market return."""
    as_of = min(_report_date(ledger, scan_start_date), as_of_date or _report_date(ledger, scan_start_date))
    entered_items = [
        item
        for item in ledger.items
        if item.entry_date is not None and item.entry_price is not None and item.return_pct is not None
    ]
    closed_items = [
        item
        for item in entered_items
        if item.status in EXECUTED_TERMINAL_STATUSES
        and item.exit_date is not None
        and item.exit_date <= as_of
    ]
    closed_returns = [item.return_pct for item in closed_items if item.return_pct is not None]
    frames = benchmark_frames_from_bars(benchmark_bars)
    primary = CN_BENCHMARKS[0]
    primary_frame = frames.get(primary.benchmark_id, pd.DataFrame())
    benchmark_returns: dict[str, float] = {}
    for item in closed_items:
        result = _matched_benchmark_return(primary_frame, item.entry_date, item.exit_date)
        if result is not None:
            benchmark_returns[item.trade_id] = result

    closed_excess = [
        item.return_pct - benchmark_returns[item.trade_id]
        for item in closed_items
        if item.return_pct is not None and item.trade_id in benchmark_returns
    ]
    closed_benchmark_returns = [
        benchmark_returns[item.trade_id]
        for item in closed_items
        if item.trade_id in benchmark_returns
    ]
    benchmark = (
        PaperCurrentModelBenchmark(
            benchmark_id=primary.benchmark_id,
            name=primary.name,
            compared_trades=len(closed_items),
            closed_compared_trades=len(closed_excess),
            coverage_pct=round(len(benchmark_returns) / len(closed_items) * 100, 2)
            if closed_items
            else 0.0,
            average_benchmark_return_pct=(
                round(mean(closed_benchmark_returns), 4) if closed_benchmark_returns else None
            ),
            average_excess_return_pct=round(mean(closed_excess), 4) if closed_excess else None,
            positive_excess_rate=(
                round(sum(value > 0 for value in closed_excess) / len(closed_excess), 4)
                if closed_excess
                else None
            ),
        )
        if entered_items
        else None
    )
    status = "ready" if len(closed_excess) >= 20 else "collecting"
    headline = _current_model_headline(
        closed_count=len(closed_items),
        benchmark=benchmark,
        status=status,
    )
    warnings = [
        "仅统计当前模型 cohort，不混入旧模型或未归类交易。",
        "基准比较只使用最近完成交易日前已结束成交的实际入场日至退出日，并以沪深300为参照。",
        "该报告用于研究模拟盘评估，不代表已验证推荐或正式发布。",
    ]
    if len(closed_items) < 20:
        warnings.append("已结束成交少于 20 笔，胜率和超额收益尚不稳定。")
    if benchmark is not None and benchmark.coverage_pct < 100:
        warnings.append("部分成交缺少同日期基准价格，未纳入超额收益计算。")
    return PaperCurrentModelEvaluation(
        as_of=as_of,
        status=status,
        headline=headline,
        cohort_id=cohort_id,
        feature_set_version=feature_set_version,
        recommendation_policy=recommendation_policy,
        observed_sessions=len(market_sessions),
        metrics=[
            PaperCurrentModelMetric(
                key="entered_trades",
                label="已成交样本",
                value=len(entered_items),
                unit="笔",
                note="只含已有实际入场价格的当前模型模拟成交。",
            ),
            PaperCurrentModelMetric(
                key="closed_trades",
                label="已结束样本（截至最近收盘）",
                value=len(closed_items),
                unit="笔",
                note="只含止盈、止损或时间退出的真实成交。",
            ),
            PaperCurrentModelMetric(
                key="win_rate",
                label="已结束胜率",
                value=(
                    round(sum(value > 0 for value in closed_returns) / len(closed_returns) * 100, 2)
                    if closed_returns
                    else None
                ),
                unit="%",
                note="按已结束成交的成本后收益计算。",
            ),
            PaperCurrentModelMetric(
                key="average_return_pct",
                label="平均已结束收益",
                value=round(mean(closed_returns), 4) if closed_returns else None,
                unit="%",
                note="按每笔实际执行后的收益计算。",
            ),
            PaperCurrentModelMetric(
                key="average_excess_return_pct",
                label="相对沪深300平均超额",
                value=benchmark.average_excess_return_pct if benchmark is not None else None,
                unit="%",
                note="同一持有区间内，成交收益减去沪深300收益。",
            ),
            PaperCurrentModelMetric(
                key="positive_excess_rate",
                label="跑赢沪深300比例",
                value=(
                    round(benchmark.positive_excess_rate * 100, 2)
                    if benchmark is not None and benchmark.positive_excess_rate is not None
                    else None
                ),
                unit="%",
                note="仅基准价格齐全的已结束成交计入。",
            ),
        ],
        benchmark=benchmark,
        checkpoints=[
            _checkpoint(target_sessions=target, sessions=market_sessions, ledger=ledger)
            for target in CHECKPOINT_SESSIONS
        ],
        warnings=warnings,
        data_health={
            "paper_current_model_evaluation": "ready",
            "paper_current_model_scope": "current_model_cohort",
            "paper_current_model_records": str(len(trades)),
            "paper_current_model_entered": str(len(entered_items)),
            "paper_current_model_closed": str(len(closed_items)),
            "paper_current_model_benchmark_id": primary.benchmark_id,
            "paper_current_model_benchmark_coverage": (
                f"{benchmark.coverage_pct:.2f}" if benchmark is not None else "0.00"
            ),
            "paper_current_model_calendar_source": "exchange_calendars:XSHG",
        },
    )


def _matched_benchmark_return(
    frame: pd.DataFrame,
    entry_date: date,
    end_date: date,
) -> float | None:
    if frame.empty or end_date < entry_date:
        return None
    normalized = frame.copy()
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
    adjusted = (
        normalized["adjusted_close"]
        if "adjusted_close" in normalized.columns
        else pd.Series(index=normalized.index, dtype="float64")
    )
    close = (
        normalized["close"]
        if "close" in normalized.columns
        else pd.Series(index=normalized.index, dtype="float64")
    )
    prices = normalized.assign(
        reference_price=adjusted.where(adjusted.notna(), close)
    ).set_index("trade_date")["reference_price"]
    entry = prices.get(entry_date)
    end = prices.get(end_date)
    if entry is None or end is None or pd.isna(entry) or pd.isna(end) or entry <= 0:
        return None
    return round((float(end) / float(entry) - 1) * 100, 4)


def _current_model_headline(
    *,
    closed_count: int,
    benchmark: PaperCurrentModelBenchmark | None,
    status: str,
) -> str:
    if closed_count == 0:
        return "当前模型已有持仓，但尚无已结束成交，暂不能评价推荐准确性。"
    if status != "ready":
        return f"当前模型已结束 {closed_count} 笔成交，样本仍在累积，暂不评价稳定准确性。"
    if benchmark is None or benchmark.average_excess_return_pct is None:
        return "当前模型样本已达到观察门槛，但基准数据不足，暂不能判断相对准确性。"
    if benchmark.average_excess_return_pct > 0:
        return "当前模型在已结束样本中平均跑赢沪深300，继续观察后续检查点的稳定性。"
    return "当前模型在已结束样本中未跑赢沪深300，应优先分析选股、择时与退出归因。"


def _checkpoint(
    *,
    target_sessions: int,
    sessions: list[date],
    ledger: PaperLedger,
) -> PaperResearchCheckpoint:
    observed = len(sessions)
    completed = observed >= target_sessions
    checkpoint_date = sessions[target_sessions - 1] if completed else None
    as_of = checkpoint_date or (sessions[-1] if sessions else None)
    items = [
        item
        for item in ledger.items
        if as_of is not None and item.signal_date <= as_of
    ]
    completed_items = [
        item
        for item in items
        if item.status in EXECUTED_TERMINAL_STATUSES
        and item.exit_date is not None
        and item.exit_date <= as_of
        and item.return_pct is not None
    ]
    returns = [item.return_pct for item in completed_items if item.return_pct is not None]
    curve = [point for point in ledger.curve if as_of is not None and point.date <= as_of]
    point = curve[-1] if curve else None
    return PaperResearchCheckpoint(
        target_sessions=target_sessions,
        observed_sessions=min(observed, target_sessions),
        progress_pct=round(min(observed / target_sessions, 1) * 100, 1),
        status="completed" if completed else "tracking",
        checkpoint_date=checkpoint_date,
        trade_count=sum(
            1
            for item in items
            if item.entry_date is not None and item.entry_date <= as_of
        )
        if as_of is not None
        else 0,
        closed_trade_count=len(completed_items),
        total_return_pct=(
            round(float(point.pnl / ledger.summary.initial_capital * 100), 4)
            if point is not None and ledger.summary.initial_capital
            else None
        ),
        max_drawdown_pct=(
            min((item.drawdown_pct for item in curve), default=None)
            if curve
            else None
        ),
        win_rate=(
            round(sum(1 for value in returns if value > 0) / len(returns), 4)
            if returns
            else None
        ),
        average_return_pct=round(mean(returns), 4) if returns else None,
    )


def _group_forward_results(
    ledger_items: list[PaperLedgerItem],
    trades: list[PaperTradeRecord],
    source_contexts: dict[str, PaperTradeSourceContext],
    *,
    dimension: str,
) -> list[PaperForwardFactorResult]:
    items_by_trade = {item.trade_id: item for item in ledger_items}
    grouped: dict[str, list[PaperLedgerItem]] = {}
    for trade in trades:
        item = items_by_trade.get(trade.trade_id)
        context = source_contexts.get(trade.trade_id)
        if item is None or context is None:
            continue
        keys = (
            context.factor_ids
            if dimension == "factor"
            else [context.market_regime or "unknown"]
        )
        for key in sorted(set(keys)):
            normalized = key.strip() or "unknown"
            grouped.setdefault(normalized, []).append(item)
    results: list[PaperForwardFactorResult] = []
    for key, items in grouped.items():
        completed = [
            item.return_pct
            for item in items
            if item.status in EXECUTED_TERMINAL_STATUSES and item.return_pct is not None
        ]
        results.append(
            PaperForwardFactorResult(
                key=key,
                label=_diagnostic_label(key),
                sample_count=len(items),
                completed_count=len(completed),
                win_rate=(
                    round(sum(1 for value in completed if value > 0) / len(completed), 4)
                    if completed
                    else None
                ),
                average_return_pct=round(mean(completed), 4) if completed else None,
                status="ready" if len(completed) >= 5 else "insufficient",
            )
        )
    results.sort(
        key=lambda item: (
            item.completed_count,
            item.average_return_pct if item.average_return_pct is not None else -999,
        ),
        reverse=True,
    )
    return results


def _headline(ledger: PaperLedger, sessions: int) -> str:
    if sessions < 20:
        return f"前向模拟已累计 {sessions} 个交易日，先观察触发和成本，暂不评价稳定性。"
    if ledger.summary.total_return_pct > 0:
        return (
            f"前向模拟已累计 {sessions} 个交易日且收益为正，"
            "继续检查样本扩展后的稳定性。"
        )
    return (
        f"前向模拟已累计 {sessions} 个交易日，当前收益为负，"
        "优先定位选股、择时和成本损失来源。"
    )


def _report_date(ledger: PaperLedger, fallback: date) -> date:
    return max((point.date for point in ledger.curve), default=fallback)


def _metric(
    key: str,
    label: str,
    historical: float | int | None,
    forward: float | int | None,
    unit: str,
    note: str,
) -> PaperResearchMetricPair:
    return PaperResearchMetricPair(
        key=key,
        label=label,
        historical=historical,
        forward=forward,
        unit=unit,
        note=note,
    )


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _number(value: object, fallback: float | None = None) -> float | None:
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _integer(value: object, fallback: int | None = None) -> int | None:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_percent(value: float | None) -> float | None:
    return round(value * 100, 4) if value is not None else None


def _diagnostic_label(key: str) -> str:
    labels = {
        "momentum": "动量",
        "trend_quality": "趋势质量",
        "quality": "质量",
        "liquidity": "流动性",
        "low_risk": "低波动",
        "risk_filter": "风险过滤",
        "valuation": "估值",
        "size": "市值",
        "reversal": "反转/回踩",
        "risk_on": "风险偏好",
        "risk_off": "风险规避",
        "neutral": "中性",
        "unknown": "未知状态",
    }
    return labels.get(key, key)
