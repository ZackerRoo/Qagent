from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from qagent.market.benchmarks import CN_BENCHMARKS, benchmark_frames_from_bars
from qagent.market.calendars import trading_sessions_in_range
from qagent.storage.paper import PaperTradeRecord
from qagent.storage.repository import OpportunitySnapshotRecord


DEFAULT_HORIZONS = (5, 10, 20)
ReportingScope = Literal["current_model_cohort", "all_history"]


class DualTrackMetric(BaseModel):
    sample_count: int
    evaluated_count: int
    win_count: int
    win_rate: float | None
    average_return_pct: float | None
    best_return_pct: float | None
    worst_return_pct: float | None


class DualTrackBenchmarkMetric(BaseModel):
    benchmark_id: str
    name: str
    selection_sample_count: int
    selection_return_pct: float | None
    selection_excess_pct: float | None
    calibrated_sample_count: int
    calibrated_return_pct: float | None
    calibrated_excess_pct: float | None
    execution_sample_count: int
    execution_return_pct: float | None
    execution_excess_pct: float | None


class DualTrackWindow(BaseModel):
    window_days: int
    selection: DualTrackMetric
    calibrated: DualTrackMetric
    execution: DualTrackMetric
    benchmarks: list[DualTrackBenchmarkMetric]
    timing_sample_count: int
    timing_effect_pct: float | None
    calibration_effect_pct: float | None
    verdict: str
    explanation: str


class DualTrackSample(BaseModel):
    snapshot_id: str
    instrument_id: str
    instrument_label: str
    signal_date: date
    strategy_id: str | None
    rank_score: float
    calibrated_eligible: bool
    calibrated_reason: str
    selection_entry_date: date | None
    selection_entry_price: Decimal | None
    selection_return_5d: float | None
    selection_return_10d: float | None
    selection_return_20d: float | None
    execution_status: str
    execution_entry_date: date | None
    execution_entry_price: Decimal | None
    execution_return_5d: float | None
    execution_return_10d: float | None
    execution_return_20d: float | None
    attribution: str


class DualTrackDataQualityIssue(BaseModel):
    snapshot_id: str
    instrument_id: str
    track: Literal["selection", "execution"]
    horizon_days: int
    reason: str
    discontinuity_date: date
    overnight_gap_pct: float


class DualTrackSummary(BaseModel):
    recommendation_days: int
    recommendations: int
    selection_started: int
    calibrated_admitted: int
    calibrated_filter_rate: float | None
    execution_admitted: int
    execution_filled: int
    execution_fill_rate: float | None
    calibration_effect_pct: float | None
    primary_window_days: int
    verdict: str
    headline: str
    explanation: str


class DualTrackReport(BaseModel):
    as_of: date
    reporting_scope: ReportingScope = "current_model_cohort"
    cohort_id: str | None = None
    excluded_other_cohort: int = 0
    excluded_unclassified: int = 0
    excluded_anomalous_horizons: int = 0
    excluded_pre_listing_bars: int = 0
    summary: DualTrackSummary
    windows: list[DualTrackWindow]
    samples: list[DualTrackSample]
    data_quality_issues: list[DualTrackDataQualityIssue] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def select_daily_top_recommendations(
    snapshots: list[OpportunitySnapshotRecord],
    *,
    top_n: int = 5,
    as_of: date | None = None,
) -> list[OpportunitySnapshotRecord]:
    if top_n <= 0:
        return []
    cutoff = as_of or date.today()
    grouped: dict[date, list[OpportunitySnapshotRecord]] = defaultdict(list)
    for snapshot in snapshots:
        if snapshot.signal_date is None or snapshot.signal_date > cutoff:
            continue
        grouped[snapshot.signal_date].append(snapshot)

    selected: list[OpportunitySnapshotRecord] = []
    for signal_date in sorted(grouped, reverse=True):
        ranked = sorted(
            grouped[signal_date],
            key=lambda item: (
                _number(item.rank_score),
                _number(item.strategy_score),
                _number(item.score),
                item.snapshot_id,
            ),
            reverse=True,
        )
        seen: set[str] = set()
        for snapshot in ranked:
            if snapshot.instrument_id in seen:
                continue
            selected.append(snapshot)
            seen.add(snapshot.instrument_id)
            if len(seen) >= top_n:
                break
    return selected


def build_dual_track_report(
    *,
    snapshots: list[OpportunitySnapshotRecord],
    trades: list[PaperTradeRecord],
    instrument_bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame,
    as_of: date,
    top_n: int = 5,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    transaction_cost_bps: float = 5.0,
    slippage_bps: float = 5.0,
    reporting_scope: ReportingScope = "current_model_cohort",
    cohort_id: str | None = None,
    excluded_other_cohort: int = 0,
    excluded_unclassified: int = 0,
    listing_dates: dict[str, date] | None = None,
    mixed_cohort: bool = False,
) -> DualTrackReport:
    selected = select_daily_top_recommendations(snapshots, top_n=top_n, as_of=as_of)
    filtered_bars, pre_listing_bar_count = _filter_pre_listing_bars(
        instrument_bars,
        listing_dates or {},
    )
    frames = _frames_by_instrument(filtered_bars)
    benchmark_frames = benchmark_frames_from_bars(benchmark_bars)
    trades_by_source = {trade.source_snapshot_id: trade for trade in trades}
    round_trip_cost_pct = 2 * (transaction_cost_bps + slippage_bps) / 100

    raw_samples: list[dict[str, object]] = []
    selection_benchmarks: dict[int, dict[str, list[tuple[float, float]]]] = {
        horizon: defaultdict(list) for horizon in horizons
    }
    execution_benchmarks: dict[int, dict[str, list[tuple[float, float]]]] = {
        horizon: defaultdict(list) for horizon in horizons
    }
    calibrated_benchmarks: dict[int, dict[str, list[tuple[float, float]]]] = {
        horizon: defaultdict(list) for horizon in horizons
    }
    for snapshot in selected:
        frame = frames.get(snapshot.instrument_id, pd.DataFrame())
        (
            selection_entry_date,
            selection_entry_price,
            selection_returns,
            selection_issues,
        ) = _selection_track_returns(
            frame,
            instrument_id=snapshot.instrument_id,
            listing_date=(listing_dates or {}).get(snapshot.instrument_id),
            signal_date=snapshot.signal_date,
            horizons=horizons,
            round_trip_cost_pct=round_trip_cost_pct,
        )
        trade = trades_by_source.get(snapshot.snapshot_id)
        execution_returns, execution_issues = _execution_track_returns(
            frame,
            instrument_id=snapshot.instrument_id,
            listing_date=(listing_dates or {}).get(snapshot.instrument_id),
            trade=trade,
            horizons=horizons,
            round_trip_cost_pct=round_trip_cost_pct,
        )
        calibrated_eligible, calibrated_reason = _calibrated_snapshot_eligibility(snapshot)
        for horizon in horizons:
            selection_return = selection_returns.get(horizon)
            if selection_return is not None:
                for definition in CN_BENCHMARKS:
                    value = _benchmark_hold_return(
                        benchmark_frames.get(definition.benchmark_id, pd.DataFrame()),
                        start_after=snapshot.signal_date,
                        horizon=horizon,
                        round_trip_cost_pct=round_trip_cost_pct,
                    )
                    if value is not None:
                        selection_benchmarks[horizon][definition.benchmark_id].append(
                            (selection_return, value)
                        )
                        if calibrated_eligible:
                            calibrated_benchmarks[horizon][definition.benchmark_id].append(
                                (selection_return, value)
                            )
            execution_return = execution_returns.get(horizon)
            if trade and trade.entry_date and execution_return is not None:
                for definition in CN_BENCHMARKS:
                    value = _benchmark_hold_return(
                        benchmark_frames.get(definition.benchmark_id, pd.DataFrame()),
                        start_on=trade.entry_date,
                        horizon=horizon,
                        round_trip_cost_pct=round_trip_cost_pct,
                    )
                    if value is not None:
                        execution_benchmarks[horizon][definition.benchmark_id].append(
                            (execution_return, value)
                        )

        raw_samples.append(
            {
                "snapshot": snapshot,
                "trade": trade,
                "selection_entry_date": selection_entry_date,
                "selection_entry_price": selection_entry_price,
                "selection_returns": selection_returns,
                "selection_issues": selection_issues,
                "calibrated_eligible": calibrated_eligible,
                "calibrated_reason": calibrated_reason,
                "execution_returns": execution_returns,
                "execution_issues": execution_issues,
            }
        )

    windows = []
    for horizon in horizons:
        selection_values = [
            value
            for item in raw_samples
            if (value := item["selection_returns"].get(horizon)) is not None
        ]
        execution_values = [
            value
            for item in raw_samples
            if (value := item["execution_returns"].get(horizon)) is not None
        ]
        calibrated_items = [item for item in raw_samples if item["calibrated_eligible"]]
        calibrated_values = [
            value
            for item in calibrated_items
            if (value := item["selection_returns"].get(horizon)) is not None
        ]
        selection_metric = _metric(len(raw_samples), selection_values)
        calibrated_metric = _metric(len(calibrated_items), calibrated_values)
        admitted = sum(1 for item in raw_samples if item["trade"] is not None)
        execution_metric = _metric(admitted, execution_values)
        timing_pairs = [
            (selection_value, execution_value)
            for item in raw_samples
            if (selection_value := item["selection_returns"].get(horizon)) is not None
            and (execution_value := item["execution_returns"].get(horizon)) is not None
        ]
        timing_effect = _average(
            [execution_value - selection_value for selection_value, execution_value in timing_pairs]
        )
        benchmark_metrics = []
        for definition in CN_BENCHMARKS:
            selection_pairs = selection_benchmarks[horizon].get(definition.benchmark_id, [])
            execution_pairs = execution_benchmarks[horizon].get(definition.benchmark_id, [])
            calibrated_pairs = calibrated_benchmarks[horizon].get(definition.benchmark_id, [])
            selection_benchmark = _average([benchmark for _, benchmark in selection_pairs])
            execution_benchmark = _average([benchmark for _, benchmark in execution_pairs])
            benchmark_metrics.append(
                DualTrackBenchmarkMetric(
                    benchmark_id=definition.benchmark_id,
                    name=definition.name,
                    selection_sample_count=len(selection_pairs),
                    selection_return_pct=selection_benchmark,
                    selection_excess_pct=_average(
                        [selection - benchmark for selection, benchmark in selection_pairs]
                    ),
                    calibrated_sample_count=len(calibrated_pairs),
                    calibrated_return_pct=_average(
                        [benchmark for _, benchmark in calibrated_pairs]
                    ),
                    calibrated_excess_pct=_average(
                        [selection - benchmark for selection, benchmark in calibrated_pairs]
                    ),
                    execution_sample_count=len(execution_pairs),
                    execution_return_pct=execution_benchmark,
                    execution_excess_pct=_average(
                        [execution - benchmark for execution, benchmark in execution_pairs]
                    ),
                )
            )
        horizon_has_anomaly = any(
            horizon in item["selection_issues"] or horizon in item["execution_issues"]
            for item in raw_samples
        )
        verdict, explanation = _window_verdict(
            selection_metric,
            calibrated_metric,
            execution_metric,
            benchmark_metrics,
            reporting_scope=reporting_scope,
            mixed_cohort=mixed_cohort,
            data_quality_blocked=horizon_has_anomaly,
        )
        windows.append(
            DualTrackWindow(
                window_days=horizon,
                selection=selection_metric,
                calibrated=calibrated_metric,
                execution=execution_metric,
                benchmarks=benchmark_metrics,
                timing_sample_count=len(timing_pairs),
                timing_effect_pct=timing_effect,
                calibration_effect_pct=_difference(
                    calibrated_metric.average_return_pct,
                    selection_metric.average_return_pct,
                ),
                verdict=verdict,
                explanation=explanation,
            )
        )

    primary = _primary_window(windows)
    samples = [
        _sample_payload(item, horizons=horizons, primary_window=primary) for item in raw_samples
    ]
    samples.sort(
        key=lambda item: (
            item.selection_return_10d is not None,
            item.selection_return_5d is not None,
            item.signal_date,
            item.rank_score,
        ),
        reverse=True,
    )
    execution_admitted = sum(1 for item in raw_samples if item["trade"] is not None)
    execution_filled = sum(
        1
        for item in raw_samples
        if item["trade"] is not None and item["trade"].entry_date is not None
    )
    quality_issues = [
        DualTrackDataQualityIssue(
            snapshot_id=item["snapshot"].snapshot_id,
            instrument_id=item["snapshot"].instrument_id,
            track=track,
            horizon_days=horizon,
            reason="unadjusted_discontinuity",
            discontinuity_date=issue["discontinuity_date"],
            overnight_gap_pct=issue["overnight_gap_pct"],
        )
        for item in raw_samples
        for track, issues in (
            ("selection", item["selection_issues"]),
            ("execution", item["execution_issues"]),
        )
        for horizon, issue in issues.items()
    ]
    summary_verdict, headline, summary_explanation = _summary_verdict(primary)
    report = DualTrackReport(
        as_of=as_of,
        reporting_scope=reporting_scope,
        cohort_id=cohort_id,
        excluded_other_cohort=excluded_other_cohort,
        excluded_unclassified=excluded_unclassified,
        excluded_anomalous_horizons=len(quality_issues),
        excluded_pre_listing_bars=pre_listing_bar_count,
        summary=DualTrackSummary(
            recommendation_days=len({snapshot.signal_date for snapshot in selected}),
            recommendations=len(selected),
            selection_started=sum(
                1 for item in raw_samples if item["selection_entry_date"] is not None
            ),
            calibrated_admitted=sum(1 for item in raw_samples if item["calibrated_eligible"]),
            calibrated_filter_rate=_ratio(
                sum(1 for item in raw_samples if not item["calibrated_eligible"]),
                len(raw_samples),
            ),
            execution_admitted=execution_admitted,
            execution_filled=execution_filled,
            execution_fill_rate=_ratio(execution_filled, execution_admitted),
            calibration_effect_pct=primary.calibration_effect_pct if primary else None,
            primary_window_days=primary.window_days if primary else 10,
            verdict=summary_verdict,
            headline=headline,
            explanation=summary_explanation,
        ),
        windows=windows,
        samples=samples[:20],
        data_quality_issues=quality_issues[:20],
        data_health={
            "dual_track_source": "recommendation_snapshots_and_paper_ledger",
            "dual_track_entry_rule": "next_trading_day_open",
            "dual_track_execution_rule": "paper_trigger_stop_target_t1",
            "dual_track_calibrated_rule": "point_in_time_quality_and_risk_filter",
            "dual_track_top_n_per_day": str(top_n),
            "dual_track_horizons": ",".join(str(value) for value in horizons),
            "dual_track_selected": str(len(selected)),
            "dual_track_reporting_scope": reporting_scope,
            "dual_track_cohort_id": cohort_id or "unavailable",
            "dual_track_excluded_other_cohort": str(excluded_other_cohort),
            "dual_track_excluded_unclassified": str(excluded_unclassified),
            "dual_track_excluded_anomalous_horizons": str(len(quality_issues)),
            "dual_track_excluded_pre_listing_bars": str(pre_listing_bar_count),
            "dual_track_bar_rows": str(len(filtered_bars)),
            "dual_track_benchmark_rows": str(len(benchmark_bars)),
            "dual_track_round_trip_cost_pct": f"{round_trip_cost_pct:.4f}",
        },
    )
    return report


def _selection_track_returns(
    frame: pd.DataFrame,
    *,
    instrument_id: str,
    listing_date: date | None,
    signal_date: date | None,
    horizons: tuple[int, ...],
    round_trip_cost_pct: float,
) -> tuple[
    date | None,
    Decimal | None,
    dict[int, float | None],
    dict[int, dict[str, object]],
]:
    empty = {horizon: None for horizon in horizons}
    ordered = _ordered_bars(frame)
    if signal_date is None or ordered.empty:
        return None, None, empty, {}
    future = ordered.loc[ordered["trade_date"] > signal_date].reset_index(drop=True)
    if future.empty:
        return None, None, empty, {}
    entry_price = _bar_price(future.iloc[0], "open")
    if entry_price is None or entry_price <= 0:
        return None, None, empty, {}
    prior = ordered.loc[ordered["trade_date"] <= signal_date]
    prior_row = prior.iloc[-1] if not prior.empty else None
    issues = {
        horizon: issue
        for horizon in horizons
        if (
            issue := _unadjusted_discontinuity(
                future,
                horizon=horizon,
                prior_row=prior_row,
                instrument_id=instrument_id,
                listing_date=listing_date,
            )
        )
        is not None
    }
    returns = {
        horizon: None
        if horizon in issues
        else _hold_return(
            future,
            entry_price=entry_price,
            horizon=horizon,
            round_trip_cost_pct=round_trip_cost_pct,
        )
        for horizon in horizons
    }
    return future.iloc[0]["trade_date"], Decimal(str(entry_price)), returns, issues


def _execution_track_returns(
    frame: pd.DataFrame,
    *,
    instrument_id: str,
    listing_date: date | None,
    trade: PaperTradeRecord | None,
    horizons: tuple[int, ...],
    round_trip_cost_pct: float,
) -> tuple[dict[int, float | None], dict[int, dict[str, object]]]:
    values = {horizon: None for horizon in horizons}
    if trade is None or trade.entry_date is None or trade.entry_price is None:
        return values, {}
    ordered = _ordered_bars(frame)
    if ordered.empty:
        return values, {}
    execution = ordered.loc[ordered["trade_date"] >= trade.entry_date].reset_index(drop=True)
    if execution.empty:
        return values, {}
    prior = ordered.loc[ordered["trade_date"] < trade.entry_date]
    prior_row = prior.iloc[-1] if not prior.empty else None
    issues: dict[int, dict[str, object]] = {}
    exit_index = None
    if trade.exit_date is not None:
        matches = execution.index[execution["trade_date"] >= trade.exit_date].tolist()
        exit_index = matches[0] if matches else None
    for horizon in horizons:
        uses_realized_exit = (
            exit_index is not None
            and exit_index < horizon
            and trade.exit_price is not None
            and trade.exit_price > 0
        )
        quality_horizon = exit_index + 1 if uses_realized_exit else horizon
        issue = _unadjusted_discontinuity(
            execution,
            horizon=quality_horizon,
            prior_row=prior_row,
            instrument_id=instrument_id,
            listing_date=listing_date,
        )
        if issue is not None:
            issues[horizon] = issue
            continue
        if uses_realized_exit:
            values[horizon] = _net_return(
                float(trade.entry_price),
                float(trade.exit_price),
                round_trip_cost_pct,
            )
            continue
        values[horizon] = _hold_return(
            execution,
            entry_price=float(trade.entry_price),
            horizon=horizon,
            round_trip_cost_pct=round_trip_cost_pct,
        )
    return values, issues


def _benchmark_hold_return(
    frame: pd.DataFrame,
    *,
    horizon: int,
    round_trip_cost_pct: float,
    start_after: date | None = None,
    start_on: date | None = None,
) -> float | None:
    ordered = _ordered_bars(frame)
    if ordered.empty:
        return None
    if start_after is not None:
        window = ordered.loc[ordered["trade_date"] > start_after].reset_index(drop=True)
    elif start_on is not None:
        window = ordered.loc[ordered["trade_date"] >= start_on].reset_index(drop=True)
    else:
        return None
    if window.empty:
        return None
    entry = _bar_price(window.iloc[0], "open")
    if entry is None:
        return None
    return _hold_return(
        window,
        entry_price=entry,
        horizon=horizon,
        round_trip_cost_pct=round_trip_cost_pct,
    )


def _hold_return(
    frame: pd.DataFrame,
    *,
    entry_price: float,
    horizon: int,
    round_trip_cost_pct: float,
) -> float | None:
    target_index = horizon - 1
    if horizon <= 0 or target_index >= len(frame):
        return None
    exit_price = _bar_price(frame.iloc[target_index], "close")
    if exit_price is None:
        return None
    return _net_return(entry_price, exit_price, round_trip_cost_pct)


def _net_return(entry_price: float, exit_price: float, round_trip_cost_pct: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((exit_price / entry_price - 1) * 100 - round_trip_cost_pct, 4)


def _metric(sample_count: int, values: list[float]) -> DualTrackMetric:
    return DualTrackMetric(
        sample_count=sample_count,
        evaluated_count=len(values),
        win_count=sum(1 for value in values if value > 0),
        win_rate=_ratio(sum(1 for value in values if value > 0), len(values)),
        average_return_pct=_average(values),
        best_return_pct=max(values) if values else None,
        worst_return_pct=min(values) if values else None,
    )


def _sample_payload(
    raw: dict[str, object],
    *,
    horizons: tuple[int, ...],
    primary_window: DualTrackWindow | None,
) -> DualTrackSample:
    snapshot = raw["snapshot"]
    trade = raw["trade"]
    selection = raw["selection_returns"]
    execution = raw["execution_returns"]
    primary_days = primary_window.window_days if primary_window else 10
    attribution = _sample_attribution(
        selection.get(primary_days), execution.get(primary_days), trade
    )
    return DualTrackSample(
        snapshot_id=snapshot.snapshot_id,
        instrument_id=snapshot.instrument_id,
        instrument_label=_snapshot_label(snapshot),
        signal_date=snapshot.signal_date,
        strategy_id=snapshot.primary_strategy_id,
        rank_score=_number(snapshot.rank_score),
        calibrated_eligible=bool(raw["calibrated_eligible"]),
        calibrated_reason=str(raw["calibrated_reason"]),
        selection_entry_date=raw["selection_entry_date"],
        selection_entry_price=raw["selection_entry_price"],
        selection_return_5d=selection.get(5),
        selection_return_10d=selection.get(10),
        selection_return_20d=selection.get(20),
        execution_status=trade.status if trade else "not_admitted",
        execution_entry_date=trade.entry_date if trade else None,
        execution_entry_price=trade.entry_price if trade else None,
        execution_return_5d=execution.get(5),
        execution_return_10d=execution.get(10),
        execution_return_20d=execution.get(20),
        attribution=attribution,
    )


def _window_verdict(
    selection: DualTrackMetric,
    calibrated: DualTrackMetric,
    execution: DualTrackMetric,
    benchmarks: list[DualTrackBenchmarkMetric],
    *,
    reporting_scope: ReportingScope,
    mixed_cohort: bool,
    data_quality_blocked: bool,
) -> tuple[str, str]:
    if reporting_scope == "all_history" and mixed_cohort:
        return "mixed_cohort", "全历史包含不同模型 cohort，不能据此归因为当前模型选股偏弱。"
    if data_quality_blocked:
        return (
            "data_quality_blocked",
            "行情窗口存在未被复权消除的严重断点，已排除异常 horizon，暂不做选股归因。",
        )
    if selection.evaluated_count < 5:
        return "waiting", "选股轨道尚未形成至少 5 个成熟样本。"
    calibration_effect = _difference(
        calibrated.average_return_pct,
        selection.average_return_pct,
    )
    if calibrated.evaluated_count >= 5 and calibration_effect is not None:
        if calibration_effect >= 1:
            return "calibration_helped", "质量与风险过滤版明显优于原始推荐，应该继续保留过滤门槛。"
        if calibration_effect <= -1:
            return "calibration_hurt", "质量过滤版反而落后，当前过滤条件需要重新校准。"
    primary_benchmark = benchmarks[0] if benchmarks else None
    selection_excess = primary_benchmark.selection_excess_pct if primary_benchmark else None
    if selection.average_return_pct is not None and selection.average_return_pct < 0:
        if selection.evaluated_count < 10:
            return "selection_warning", "早期样本整体为负，先视为选股偏弱预警，等待更多样本确认。"
        return "selection_weak", "推荐后直接持有收益为负，当前主要问题在选股。"
    if execution.evaluated_count == 0:
        return "selection_only", "选股已有结果，但择时轨道尚未形成可评价成交。"
    timing_effect = _difference(execution.average_return_pct, selection.average_return_pct)
    if timing_effect is not None and timing_effect <= -1:
        return "timing_drag", "同批推荐直接持有表现更好，当前买点或退出规则拖累收益。"
    if timing_effect is not None and timing_effect >= 1:
        return "timing_helped", "择时执行优于直接持有，买点与风控产生了正贡献。"
    if selection_excess is not None and selection_excess > 0:
        return "selection_effective", "选股跑赢主要指数，择时贡献暂时接近中性。"
    return "aligned", "选股与择时表现接近，继续积累样本观察稳定性。"


def _summary_verdict(
    primary: DualTrackWindow | None,
) -> tuple[str, str, str]:
    if primary is None:
        return "waiting", "等待双轨样本", "还没有可计算的推荐与行情数据。"
    labels = {
        "data_quality_blocked": "行情质量阻断归因",
        "mixed_cohort": "历史 cohort 混合",
        "selection_weak": "选股需要调整",
        "selection_warning": "选股短期偏弱预警",
        "selection_only": "选股已有结果，等待成交",
        "timing_drag": "选股有效，择时拖累",
        "timing_helped": "择时产生正贡献",
        "selection_effective": "推荐存在超额收益",
        "calibration_helped": "质量过滤改善推荐",
        "calibration_hurt": "过滤条件需要调整",
        "aligned": "选股与择时基本一致",
        "waiting": "等待双轨样本成熟",
    }
    return primary.verdict, labels.get(primary.verdict, "双轨继续验证"), primary.explanation


def _primary_window(windows: list[DualTrackWindow]) -> DualTrackWindow | None:
    if not windows:
        return None
    blocked_ten_day = next(
        (
            item
            for item in windows
            if item.window_days == 10 and item.verdict in {"data_quality_blocked", "mixed_cohort"}
        ),
        None,
    )
    if blocked_ten_day is not None:
        return blocked_ten_day
    return next(
        (
            item
            for item in windows
            if item.window_days == 10 and item.selection.evaluated_count >= 5
        ),
        max(windows, key=lambda item: item.selection.evaluated_count),
    )


def _sample_attribution(
    selection_return: float | None,
    execution_return: float | None,
    trade: PaperTradeRecord | None,
) -> str:
    if selection_return is None:
        return "等待选股窗口成熟"
    if trade is None:
        return "推荐未进入择时模拟盘"
    if trade.entry_date is None:
        return "选股已开始验证，买点尚未触发"
    if execution_return is None:
        return "已成交，等待择时窗口成熟"
    difference = execution_return - selection_return
    if difference >= 1:
        return "择时提升收益"
    if difference <= -1:
        return "择时拖累收益"
    return "选股与择时接近"


def _calibrated_snapshot_eligibility(
    snapshot: OpportunitySnapshotRecord,
) -> tuple[bool, str]:
    if snapshot.status in {"risk_elevated", "invalidated", "closed", "postmortem_done"}:
        return False, "推荐当时已被风险状态阻断"
    if _number(snapshot.rank_score) < 0.55:
        return False, "推荐总分低于 55%"

    card = snapshot.card if isinstance(snapshot.card, dict) else {}
    quality = card.get("recommendation_quality")
    if isinstance(quality, dict) and quality.get("tier") in {
        "risk_filtered",
        "low_quality",
    }:
        return False, "推荐质量门禁未通过"

    decision = card.get("decision")
    if isinstance(decision, dict):
        if decision.get("action") in {"avoid", "no_trade", "blocked"}:
            return False, "当时决策不允许买入"
        if decision.get("risk_status") in {"blocked", "veto"}:
            return False, "盘前风险检查阻断"

    flags = {str(value) for value in card.get("factor_flags", []) if isinstance(value, str)}
    hard_flags = {
        "low_liquidity",
        "liquidity_risk",
        "deep_drawdown_risk",
        "price_limit_risk",
    }
    if flags.intersection(hard_flags):
        return False, "流动性或极端风险因子未通过"
    if {"high_volatility", "overextended"}.issubset(flags):
        return False, "高波动与短线过热同时出现"

    calibration = card.get("strategy_calibration")
    if isinstance(calibration, dict):
        sample_count = _optional_number(calibration.get("sample_count")) or 0
        win_rate = _optional_number(calibration.get("win_rate_10d"))
        average_return = _optional_number(calibration.get("avg_return_10d"))
        if sample_count >= 20 and (
            (win_rate is not None and win_rate < 45)
            or (average_return is not None and average_return < 0)
        ):
            return False, "策略历史胜率或平均收益未通过"

    data_quality = card.get("data_quality_audit")
    if isinstance(data_quality, dict):
        if data_quality.get("can_recommend") is False or data_quality.get("status") == "blocked":
            return False, "当时数据质量门禁未通过"
        quality_score = _optional_number(data_quality.get("score"))
        if quality_score is not None and quality_score < 0.5:
            return False, "当时数据质量低于 50%"
    return True, "通过当时可见的质量、风险和策略门禁"


def _frames_by_instrument(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if frame.empty or "instrument_id" not in frame.columns:
        return {}
    return {
        str(instrument_id): group.copy()
        for instrument_id, group in frame.groupby("instrument_id", sort=False)
    }


def _filter_pre_listing_bars(
    frame: pd.DataFrame,
    listing_dates: dict[str, date],
) -> tuple[pd.DataFrame, int]:
    if frame.empty or not listing_dates:
        return frame.copy(), 0
    if "instrument_id" not in frame.columns or "trade_date" not in frame.columns:
        return frame.copy(), 0
    filtered = frame.copy()
    trade_dates = pd.to_datetime(filtered["trade_date"], errors="coerce").dt.date
    listing_series = filtered["instrument_id"].astype(str).map(listing_dates)
    invalid = listing_series.notna() & trade_dates.notna() & (trade_dates < listing_series)
    return filtered.loc[~invalid].copy(), int(invalid.sum())


def _ordered_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "trade_date" not in frame.columns:
        return pd.DataFrame()
    ordered = frame.copy()
    ordered["trade_date"] = pd.to_datetime(ordered["trade_date"], errors="coerce").dt.date
    return ordered.dropna(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)


def _unadjusted_discontinuity(
    frame: pd.DataFrame,
    *,
    horizon: int,
    prior_row: pd.Series | None,
    instrument_id: str,
    listing_date: date | None,
) -> dict[str, object] | None:
    if horizon <= 0 or horizon > len(frame):
        return None
    window = frame.iloc[:horizon]
    no_limit_dates = _initial_listing_trade_dates(listing_date)
    gap_threshold = 0.30 if _is_bse_instrument(instrument_id) else 0.25
    previous = prior_row
    for _, current in window.iterrows():
        if previous is not None and current["trade_date"] not in no_limit_dates:
            raw_previous = _positive_number(previous.get("close"))
            raw_current = _positive_number(current.get("open"))
            adjusted_previous = _positive_number(previous.get("adjusted_close"))
            adjusted_current = _positive_number(current.get("adjusted_open"))
            previous_factor = _optional_number(previous.get("adjustment_factor"))
            current_factor = _optional_number(current.get("adjustment_factor"))
            if (
                raw_previous is not None
                and raw_current is not None
                and adjusted_previous is not None
                and adjusted_current is not None
                and previous_factor is not None
                and current_factor is not None
                and abs(previous_factor - current_factor) <= 1e-12
            ):
                raw_gap = raw_current / raw_previous - 1
                adjusted_gap = adjusted_current / adjusted_previous - 1
                if abs(raw_gap) > gap_threshold + 1e-9 and abs(adjusted_gap - raw_gap) <= 0.01:
                    return {
                        "discontinuity_date": current["trade_date"],
                        "overnight_gap_pct": round(raw_gap * 100, 4),
                    }
        previous = current
    return None


def _initial_listing_trade_dates(listing_date: date | None) -> set[date]:
    if listing_date is None:
        return set()
    sessions = trading_sessions_in_range(listing_date, listing_date + timedelta(days=31))
    return set(sessions[:5])


def _is_bse_instrument(instrument_id: str) -> bool:
    symbol = instrument_id.removeprefix("CN:").split(".", 1)[0]
    return symbol.startswith(("4", "8", "920"))


def _bar_price(row: pd.Series, field: str) -> float | None:
    adjusted = row.get(f"adjusted_{field}")
    value = adjusted if pd.notna(adjusted) else row.get(field)
    if value is None or pd.isna(value):
        return None
    result = float(value)
    return result if result > 0 else None


def _positive_number(value: object) -> float | None:
    number = _optional_number(value)
    if number is None or number <= 0:
        return None
    return number


def _snapshot_label(snapshot: OpportunitySnapshotRecord) -> str:
    label = snapshot.card.get("instrument_label") if isinstance(snapshot.card, dict) else None
    return str(label).strip() if label else snapshot.instrument_id


def _number(value: Decimal | float | int | None) -> float:
    return float(value or 0)


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None
