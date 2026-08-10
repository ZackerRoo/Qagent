from __future__ import annotations

import math
import subprocess
from bisect import bisect_right
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from qagent.market.calendars import trading_day_offset, trading_sessions_in_range
from qagent.storage.factor_research import FactorResearchRepository
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import HistoricalIndustrySnapshotRow
from qagent.strategy_data.models import FundamentalSnapshot


FACTOR_RESEARCH_VERSION = "factor-research-v1-xshg-neutralized"
DEFAULT_BENCHMARK_ID = "CN:000300.IDX"
FEATURE_COLUMNS = (
    "momentum_20",
    "momentum_60",
    "momentum_120",
    "return_5",
    "trend_slope_60",
    "trend_r2_60",
    "volatility_20",
    "downside_risk_60",
    "max_drawdown_60",
    "turnover_log_20",
    "volume_ratio_5_20",
    "distance_ma20",
    "earnings_yield",
    "return_on_equity",
    "gross_margin",
    "revenue_growth",
    "earnings_growth",
)
BASELINE_SIGNS = {
    "momentum_20": 1.0,
    "momentum_60": 1.0,
    "momentum_120": 1.0,
    "return_5": -0.5,
    "trend_slope_60": 1.0,
    "trend_r2_60": 1.0,
    "volatility_20": -1.0,
    "downside_risk_60": -1.0,
    "max_drawdown_60": 1.0,
    "turnover_log_20": 0.5,
    "volume_ratio_5_20": 0.25,
    "distance_ma20": 0.25,
    "earnings_yield": 1.0,
    "return_on_equity": 1.0,
    "gross_margin": 0.5,
    "revenue_growth": 0.5,
    "earnings_growth": 0.5,
}


class FactorResearchConfig(BaseModel):
    provider_mode: str = "free"
    start_date: date = date(2021, 11, 1)
    end_date: date = date(2025, 12, 31)
    dataset_revision: int | None = None
    benchmark_id: str = DEFAULT_BENCHMARK_ID
    rebalance_step_sessions: int = Field(default=10, ge=5, le=60)
    horizon_sessions: int = Field(default=20, ge=5, le=60)
    minimum_history_sessions: int = Field(default=120, ge=60, le=260)
    top_fraction: float = Field(default=0.10, gt=0, le=0.30)
    round_trip_cost_bps: float = Field(default=10.0, ge=0, le=100)
    max_instruments: int | None = Field(default=None, ge=50)
    seeds: list[int] = Field(default_factory=lambda: [7, 19, 42], min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_window(self) -> "FactorResearchConfig":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class FactorResearchRunOutput(BaseModel):
    metrics: dict[str, Any]
    data_health: dict[str, Any]
    artifacts: dict[str, Any]


def current_code_revision() -> str:
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    revision = result.stdout.strip().lower()
    if len(revision) != 40:
        raise RuntimeError("factor research requires a full Git revision")
    return revision


def resolved_config(
    session_factory: sessionmaker[Session],
    config: FactorResearchConfig,
) -> FactorResearchConfig:
    revision = config.dataset_revision
    if revision is None:
        revision = ReplayEvidenceRepository(
            session_factory,
            config.provider_mode,
        ).current_revision()
    if revision <= 0:
        raise ValueError("factor research requires a frozen positive dataset revision")
    return config.model_copy(update={"dataset_revision": revision})


def execute_factor_research_experiment(
    session_factory: sessionmaker[Session],
    experiment_id: str,
    config: FactorResearchConfig,
) -> None:
    store = FactorResearchRepository(session_factory)
    store.mark_running(experiment_id)
    try:
        output = run_factor_research(session_factory, config)
        store.complete(
            experiment_id,
            metrics=output.metrics,
            data_health=output.data_health,
            artifacts=output.artifacts,
        )
    except Exception as error:
        store.fail(
            experiment_id,
            f"{type(error).__name__}: {error}",
            data_health={"factor_research": "failed"},
        )
        raise


def run_factor_research(
    session_factory: sessionmaker[Session],
    config: FactorResearchConfig,
) -> FactorResearchRunOutput:
    config = resolved_config(session_factory, config)
    frame, data_health = build_factor_research_dataset(session_factory, config)
    metrics, artifacts = compare_baseline_and_lightgbm(frame, config)
    data_health.update(
        {
            "factor_research": "ready",
            "feature_set_version": FACTOR_RESEARCH_VERSION,
            "feature_count": len(FEATURE_COLUMNS),
            "model_isolation": "research_only_no_paper_activation",
        }
    )
    return FactorResearchRunOutput(
        metrics=metrics,
        data_health=data_health,
        artifacts=artifacts,
    )


def build_factor_research_dataset(
    session_factory: sessionmaker[Session],
    config: FactorResearchConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if config.dataset_revision is None:
        raise ValueError("dataset_revision must be resolved before dataset construction")
    replay = ReplayEvidenceRepository(session_factory, config.provider_mode)
    repository = QagentRepository(session_factory)
    inventory = replay.lifecycle_inventory(config.dataset_revision, config.end_date)
    stock_ids = sorted(
        item.instrument_id
        for item in inventory
        if item.security_type in {"stock", "1"}
        and item.listing_date is not None
        and item.listing_date <= config.end_date
        and (item.delisting_date is None or item.delisting_date > config.start_date)
    )
    if config.max_instruments is not None:
        stock_ids = stock_ids[: config.max_instruments]
    if not stock_ids:
        raise ValueError("frozen lifecycle inventory contains no eligible stocks")

    sessions = trading_sessions_in_range(config.start_date, config.end_date)
    if len(sessions) <= config.horizon_sessions + 10:
        raise ValueError("research window has too few XSHG sessions")
    signal_sessions = sessions[: -config.horizon_sessions : config.rebalance_step_sessions]
    future_by_signal = {
        signal: sessions[index + config.horizon_sessions]
        for index, signal in enumerate(sessions)
        if signal in set(signal_sessions) and index + config.horizon_sessions < len(sessions)
    }
    history_start = trading_day_offset(
        config.start_date,
        -config.minimum_history_sessions - 5,
    )
    benchmark_rows = list(
        replay.replay_factor_bar_rows(
            [config.benchmark_id],
            config.start_date,
            config.end_date,
            config.dataset_revision,
        )
    )
    benchmark_close = {
        row.trade_date: float(row.adjusted_close)
        for row in benchmark_rows
        if row.adjusted_close is not None
    }
    benchmark_returns = {
        signal: (benchmark_close[future] / benchmark_close[signal] - 1) * 100
        for signal, future in future_by_signal.items()
        if signal in benchmark_close and future in benchmark_close and benchmark_close[signal] > 0
    }
    if len(benchmark_returns) < max(8, len(signal_sessions) // 2):
        raise ValueError("benchmark adjusted-close coverage is insufficient")

    industry_history = _load_industry_history(
        session_factory,
        config.provider_mode,
        stock_ids,
        config.end_date,
        config.dataset_revision,
    )
    raw_rows: list[dict[str, Any]] = []
    adjusted_bar_rows = 0
    rejected_missing_tradability = 0
    rejected_st_or_suspended = 0
    for offset in range(0, len(stock_ids), 200):
        batch_ids = stock_ids[offset : offset + 200]
        tradability = replay.tradability_on_dates(
            batch_ids,
            signal_sessions,
            config.dataset_revision,
        )
        fundamentals = repository.list_fundamental_snapshots(
            config.provider_mode,
            batch_ids,
            end=config.end_date,
            limit=50_000,
        )
        fundamental_history = _fundamental_history(fundamentals)
        factor_rows = list(
            replay.replay_factor_bar_rows(
                batch_ids,
                history_start,
                config.end_date,
                config.dataset_revision,
            )
        )
        adjusted_bar_rows += sum(row.adjusted_close is not None for row in factor_rows)
        rows_by_instrument: dict[str, list[Any]] = defaultdict(list)
        for row in factor_rows:
            if row.adjusted_close is not None:
                rows_by_instrument[row.instrument_id].append(row)
        for instrument_id, rows in rows_by_instrument.items():
            prepared = _prepare_instrument_rows(rows)
            if prepared is None:
                continue
            dates, closes, volumes, turnovers = prepared
            positions = {trade_date: index for index, trade_date in enumerate(dates)}
            for signal_date in signal_sessions:
                future_date = future_by_signal.get(signal_date)
                if future_date is None or signal_date not in benchmark_returns:
                    continue
                point = tradability.get(signal_date, {}).get(instrument_id)
                if point is None:
                    rejected_missing_tradability += 1
                    continue
                if point.trading_status != "trading" or point.is_st is True:
                    rejected_st_or_suspended += 1
                    continue
                signal_index = positions.get(signal_date)
                future_index = positions.get(future_date)
                if (
                    signal_index is None
                    or future_index is None
                    or signal_index < config.minimum_history_sessions
                    or closes[signal_index] <= 0
                ):
                    continue
                fundamental = _as_of(fundamental_history.get(instrument_id, []), signal_date)
                industry = _as_of_industry(
                    industry_history.get(instrument_id, []),
                    signal_date,
                )
                raw_rows.append(
                    _feature_row(
                        instrument_id=instrument_id,
                        signal_date=signal_date,
                        signal_index=signal_index,
                        future_index=future_index,
                        closes=closes,
                        volumes=volumes,
                        turnovers=turnovers,
                        benchmark_return_pct=benchmark_returns[signal_date],
                        industry=industry,
                        fundamental=fundamental,
                    )
                )
    if not raw_rows:
        raise ValueError("no point-in-time research samples survived data-quality filters")

    frame = pd.DataFrame(raw_rows)
    frame = _attach_excess_labels(frame)
    frame = neutralize_research_features(frame)
    frame = frame.dropna(subset=["target_excess_return_pct"])
    if frame["signal_date"].nunique() < 15:
        raise ValueError("fewer than 15 research cross-sections survived filtering")
    industry_labeled = int((frame["label_scope"] == "industry_excess").sum())
    data_health = {
        "calendar": "exchange_calendars:XSHG",
        "provider_mode": config.provider_mode,
        "dataset_revision": config.dataset_revision,
        "benchmark_id": config.benchmark_id,
        "universe_scope": "frozen_full_stock_inventory" if config.max_instruments is None else "bounded_audit",
        "inventory_stock_count": len(stock_ids),
        "adjusted_bar_rows": adjusted_bar_rows,
        "research_sample_rows": len(frame),
        "research_cross_sections": int(frame["signal_date"].nunique()),
        "industry_snapshot_instruments": len(industry_history),
        "industry_excess_rows": industry_labeled,
        "benchmark_excess_fallback_rows": int(len(frame) - industry_labeled),
        "industry_label_coverage_ratio": round(industry_labeled / len(frame), 6),
        "missing_tradability_rejections": rejected_missing_tradability,
        "st_or_suspended_rejections": rejected_st_or_suspended,
        "adjusted_price_policy": "adjusted_close_required",
        "tradability_policy": "missing_or_nontrading_or_st_fail_closed",
        "label_policy": "industry_excess_when_group_ge_5_else_hs300_excess",
        "neutralization_policy": "cross_sectional_winsorize_then_size_and_industry_residual",
    }
    return frame, data_health


def neutralize_research_features(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for _, indexes in normalized.groupby("signal_date").groups.items():
        group = normalized.loc[indexes]
        size = pd.to_numeric(group["log_market_cap"], errors="coerce")
        size = size.fillna(size.median() if size.notna().any() else 0.0)
        design_parts = [np.ones(len(group)), size.to_numpy(dtype="float64")]
        industries = group["industry"].fillna("unknown").astype(str)
        frequent = sorted(
            industry
            for industry, count in industries.value_counts().items()
            if industry != "unknown" and count >= 5
        )
        for industry in frequent[1:]:
            design_parts.append((industries == industry).astype(float).to_numpy())
        design = np.column_stack(design_parts)
        for feature in FEATURE_COLUMNS:
            values = pd.to_numeric(group[feature], errors="coerce")
            valid = values.notna()
            if valid.sum() < 5:
                normalized.loc[indexes, feature] = np.nan
                continue
            low, high = values[valid].quantile([0.01, 0.99])
            clipped = values.clip(lower=low, upper=high)
            coefficients, *_ = np.linalg.lstsq(
                design[valid.to_numpy()],
                clipped[valid].to_numpy(dtype="float64"),
                rcond=None,
            )
            residual = clipped[valid].to_numpy(dtype="float64") - (
                design[valid.to_numpy()] @ coefficients
            )
            scale = float(np.std(residual))
            normalized.loc[indexes, feature] = np.nan
            normalized.loc[group.index[valid], feature] = (
                residual / scale if scale > 1e-12 else np.zeros(len(residual))
            )
    return normalized


def compare_baseline_and_lightgbm(
    frame: pd.DataFrame,
    config: FactorResearchConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    dates = sorted(frame["signal_date"].unique())
    if len(dates) < 15:
        raise ValueError("at least 15 cross-sections are required")
    purge = max(1, math.ceil(config.horizon_sessions / config.rebalance_step_sessions))
    train_boundary = max(3, int(len(dates) * 0.60))
    valid_boundary = max(train_boundary + purge + 2, int(len(dates) * 0.80))
    train_dates = dates[: max(1, train_boundary - purge)]
    valid_dates = dates[train_boundary : max(train_boundary + 1, valid_boundary - purge)]
    test_dates = dates[valid_boundary:]
    if min(len(train_dates), len(valid_dates), len(test_dates)) < 2:
        raise ValueError("purged train/valid/test geometry is too small")

    train = frame[frame["signal_date"].isin(train_dates)].copy()
    valid = frame[frame["signal_date"].isin(valid_dates)].copy()
    test = frame[frame["signal_date"].isin(test_dates)].copy()
    for subset in (train, valid, test):
        subset["baseline_prediction"] = _baseline_prediction(subset)

    try:
        import lightgbm as lgb
    except ImportError as error:
        raise RuntimeError("lightgbm dependency is unavailable") from error

    predictions: list[np.ndarray] = []
    importance = np.zeros(len(FEATURE_COLUMNS), dtype="float64")
    best_iterations: list[int] = []
    for seed in config.seeds:
        train_data = lgb.Dataset(
            train[list(FEATURE_COLUMNS)],
            label=train["target_excess_return_pct"],
            feature_name=list(FEATURE_COLUMNS),
            free_raw_data=False,
        )
        valid_data = lgb.Dataset(
            valid[list(FEATURE_COLUMNS)],
            label=valid["target_excess_return_pct"],
            feature_name=list(FEATURE_COLUMNS),
            reference=train_data,
            free_raw_data=False,
        )
        model = lgb.train(
            {
                "objective": "regression_l1",
                "metric": "l1",
                "learning_rate": 0.03,
                "num_leaves": 31,
                "min_data_in_leaf": 40,
                "bagging_fraction": 0.85,
                "bagging_freq": 1,
                "feature_fraction": 0.85,
                "lambda_l1": 0.1,
                "lambda_l2": 0.2,
                "seed": seed,
                "feature_fraction_seed": seed,
                "bagging_seed": seed,
                "data_random_seed": seed,
                "num_threads": 2,
                "verbosity": -1,
            },
            train_data,
            num_boost_round=500,
            valid_sets=[valid_data],
            valid_names=["validation"],
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )
        best_iteration = int(model.best_iteration or 500)
        predictions.append(
            model.predict(test[list(FEATURE_COLUMNS)], num_iteration=best_iteration)
        )
        importance += model.feature_importance(importance_type="gain")
        best_iterations.append(best_iteration)
    test["lightgbm_prediction"] = np.mean(np.vstack(predictions), axis=0)
    baseline_metrics = _model_metrics(
        test,
        "baseline_prediction",
        config.top_fraction,
        config.round_trip_cost_bps,
    )
    challenger_metrics = _model_metrics(
        test,
        "lightgbm_prediction",
        config.top_fraction,
        config.round_trip_cost_bps,
    )
    stronger = bool(
        (challenger_metrics["mean_rank_ic"] or -999) > (baseline_metrics["mean_rank_ic"] or -999)
        and (challenger_metrics["net_top_bucket_excess_return_pct"] or -999)
        > (baseline_metrics["net_top_bucket_excess_return_pct"] or -999)
        and (challenger_metrics["mean_rank_ic"] or 0) > 0
    )
    metrics = {
        "baseline": baseline_metrics,
        "lightgbm_challenger": challenger_metrics,
        "challenger_stronger_on_frozen_test": stronger,
        "activation_allowed": False,
        "disposition": "research_candidate" if stronger else "keep_baseline",
    }
    feature_importance = sorted(
        (
            {
                "feature": feature,
                "importance": round(float(value / len(config.seeds)), 4),
            }
            for feature, value in zip(FEATURE_COLUMNS, importance)
        ),
        key=lambda item: (-item["importance"], item["feature"]),
    )
    artifacts = {
        "version": FACTOR_RESEARCH_VERSION,
        "split": {
            "train_start": str(train_dates[0]),
            "train_end": str(train_dates[-1]),
            "valid_start": str(valid_dates[0]),
            "valid_end": str(valid_dates[-1]),
            "test_start": str(test_dates[0]),
            "test_end": str(test_dates[-1]),
            "purge_cross_sections": purge,
            "train_rows": len(train),
            "valid_rows": len(valid),
            "test_rows": len(test),
        },
        "seeds": config.seeds,
        "best_iterations": best_iterations,
        "feature_importance": feature_importance,
        "paper_model_unchanged": True,
    }
    return _json_safe(metrics), _json_safe(artifacts)


def _model_metrics(
    frame: pd.DataFrame,
    prediction_column: str,
    top_fraction: float,
    round_trip_cost_bps: float,
) -> dict[str, Any]:
    daily_ic: list[float] = []
    daily_rank_ic: list[float] = []
    daily_top: list[float] = []
    holdings: list[set[str]] = []
    for _, group in frame.groupby("signal_date", sort=True):
        clean = group.dropna(subset=[prediction_column, "target_excess_return_pct"])
        if len(clean) < 5:
            continue
        ic = clean[prediction_column].corr(clean["target_excess_return_pct"])
        rank_ic = clean[prediction_column].rank().corr(
            clean["target_excess_return_pct"].rank()
        )
        if pd.notna(ic):
            daily_ic.append(float(ic))
        if pd.notna(rank_ic):
            daily_rank_ic.append(float(rank_ic))
        top_count = max(1, int(math.ceil(len(clean) * top_fraction)))
        selected = clean.nlargest(top_count, prediction_column)
        daily_top.append(float(selected["target_excess_return_pct"].mean()))
        holdings.append(set(selected["instrument_id"].astype(str)))
    turnovers = [
        1 - len(previous & current) / max(len(previous), len(current), 1)
        for previous, current in zip(holdings, holdings[1:])
    ]
    average_turnover = _mean(turnovers)
    gross = _mean(daily_top)
    cost_drag = (
        average_turnover * round_trip_cost_bps / 100
        if average_turnover is not None
        else None
    )
    net = gross - cost_drag if gross is not None and cost_drag is not None else None
    return {
        "sample_rows": len(frame),
        "cross_sections": int(frame["signal_date"].nunique()),
        "mean_ic": _rounded(_mean(daily_ic)),
        "mean_rank_ic": _rounded(_mean(daily_rank_ic)),
        "positive_rank_ic_rate": _rounded(
            sum(value > 0 for value in daily_rank_ic) / len(daily_rank_ic)
            if daily_rank_ic
            else None
        ),
        "gross_top_bucket_excess_return_pct": _rounded(gross),
        "average_turnover_rate": _rounded(average_turnover),
        "estimated_cost_drag_pct": _rounded(cost_drag),
        "net_top_bucket_excess_return_pct": _rounded(net),
        "top_bucket_max_drawdown_pct": _rounded(_maximum_drawdown(daily_top)),
    }


def _prepare_instrument_rows(
    rows: Sequence[Any],
) -> tuple[list[date], np.ndarray, np.ndarray, np.ndarray] | None:
    latest = {row.trade_date: row for row in rows if row.adjusted_close is not None}
    ordered = [latest[item] for item in sorted(latest)]
    if len(ordered) < 61:
        return None
    dates = [row.trade_date for row in ordered]
    closes = np.array([float(row.adjusted_close) for row in ordered], dtype="float64")
    volumes = np.array([float(row.volume) for row in ordered], dtype="float64")
    turnovers = np.array(
        [float(row.volume) * float(row.raw_close) for row in ordered],
        dtype="float64",
    )
    return dates, closes, volumes, turnovers


def _feature_row(
    *,
    instrument_id: str,
    signal_date: date,
    signal_index: int,
    future_index: int,
    closes: np.ndarray,
    volumes: np.ndarray,
    turnovers: np.ndarray,
    benchmark_return_pct: float,
    industry: str | None,
    fundamental: FundamentalSnapshot | None,
) -> dict[str, Any]:
    history = closes[: signal_index + 1]
    returns = pd.Series(history).pct_change().dropna().to_numpy(dtype="float64")
    trend_slope, trend_r2 = _trend(history[-60:])
    market_cap = _float(fundamental.market_cap if fundamental else None)
    pe = _positive_float(fundamental.pe_ratio if fundamental else None)
    forward_return = (closes[future_index] / closes[signal_index] - 1) * 100
    return {
        "signal_date": signal_date,
        "instrument_id": instrument_id,
        "industry": industry,
        "log_market_cap": math.log(market_cap) if market_cap and market_cap > 0 else np.nan,
        "raw_forward_return_pct": forward_return,
        "benchmark_return_pct": benchmark_return_pct,
        "momentum_20": _period_return(history, 20),
        "momentum_60": _period_return(history, 60),
        "momentum_120": _period_return(history, 120),
        "return_5": _period_return(history, 5),
        "trend_slope_60": trend_slope,
        "trend_r2_60": trend_r2,
        "volatility_20": float(np.std(returns[-20:])) if len(returns) >= 20 else np.nan,
        "downside_risk_60": (
            float(np.std(np.minimum(returns[-60:], 0))) if len(returns) >= 60 else np.nan
        ),
        "max_drawdown_60": _price_max_drawdown(history[-60:]),
        "turnover_log_20": math.log1p(float(np.mean(turnovers[signal_index - 19 : signal_index + 1]))),
        "volume_ratio_5_20": _safe_ratio(
            float(np.mean(volumes[signal_index - 4 : signal_index + 1])),
            float(np.mean(volumes[signal_index - 19 : signal_index + 1])),
        ),
        "distance_ma20": _safe_ratio(closes[signal_index], float(np.mean(history[-20:]))) - 1,
        "earnings_yield": 1 / pe if pe else np.nan,
        "return_on_equity": _fundamental_value(fundamental, "return_on_equity_pct"),
        "gross_margin": _fundamental_value(fundamental, "gross_margin_pct"),
        "revenue_growth": _fundamental_value(fundamental, "revenue_growth_pct"),
        "earnings_growth": _fundamental_value(fundamental, "earnings_growth_pct"),
    }


def _attach_excess_labels(frame: pd.DataFrame) -> pd.DataFrame:
    labeled = frame.copy()
    industry_median = labeled.groupby(["signal_date", "industry"], dropna=True)[
        "raw_forward_return_pct"
    ].transform("median")
    industry_count = labeled.groupby(["signal_date", "industry"], dropna=True)[
        "raw_forward_return_pct"
    ].transform("count")
    use_industry = labeled["industry"].notna() & industry_count.ge(5)
    labeled["target_excess_return_pct"] = np.where(
        use_industry,
        labeled["raw_forward_return_pct"] - industry_median,
        labeled["raw_forward_return_pct"] - labeled["benchmark_return_pct"],
    )
    labeled["label_scope"] = np.where(
        use_industry,
        "industry_excess",
        "benchmark_excess",
    )
    return labeled


def _baseline_prediction(frame: pd.DataFrame) -> pd.Series:
    components = []
    for feature, sign in BASELINE_SIGNS.items():
        percentile = frame.groupby("signal_date")[feature].rank(pct=True)
        components.append((percentile - 0.5) * 2 * sign)
    return pd.concat(components, axis=1).mean(axis=1, skipna=True)


def _load_industry_history(
    session_factory: sessionmaker[Session],
    provider_mode: str,
    instrument_ids: Sequence[str],
    end_date: date,
    revision: int,
) -> dict[str, list[tuple[date, str]]]:
    selected: dict[tuple[str, date], HistoricalIndustrySnapshotRow] = {}
    with session_factory() as session:
        for offset in range(0, len(instrument_ids), 500):
            rows = session.scalars(
                select(HistoricalIndustrySnapshotRow).where(
                    HistoricalIndustrySnapshotRow.provider_mode == provider_mode,
                    HistoricalIndustrySnapshotRow.instrument_id.in_(
                        instrument_ids[offset : offset + 500]
                    ),
                    HistoricalIndustrySnapshotRow.snapshot_date <= end_date,
                    HistoricalIndustrySnapshotRow.dataset_revision <= revision,
                )
            )
            for row in rows:
                key = (row.instrument_id, row.snapshot_date)
                current = selected.get(key)
                if current is None or (
                    row.dataset_revision,
                    row.source_provider,
                ) > (current.dataset_revision, current.source_provider):
                    selected[key] = row
    history: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for row in selected.values():
        history[row.instrument_id].append((row.snapshot_date, row.industry))
    return {instrument_id: sorted(items) for instrument_id, items in history.items()}


def _fundamental_history(
    items: Sequence[FundamentalSnapshot],
) -> dict[str, list[FundamentalSnapshot]]:
    history: dict[str, list[FundamentalSnapshot]] = defaultdict(list)
    for item in items:
        history[item.instrument_id].append(item)
    return {
        instrument_id: sorted(values, key=lambda item: item.as_of_date)
        for instrument_id, values in history.items()
    }


def _as_of(items: Sequence[FundamentalSnapshot], signal_date: date) -> FundamentalSnapshot | None:
    if not items:
        return None
    index = bisect_right([item.as_of_date for item in items], signal_date) - 1
    return items[index] if index >= 0 else None


def _as_of_industry(items: Sequence[tuple[date, str]], signal_date: date) -> str | None:
    if not items:
        return None
    index = bisect_right([item[0] for item in items], signal_date) - 1
    return items[index][1] if index >= 0 else None


def _period_return(values: np.ndarray, sessions: int) -> float:
    if len(values) <= sessions or values[-sessions - 1] <= 0:
        return np.nan
    return float(values[-1] / values[-sessions - 1] - 1)


def _trend(values: np.ndarray) -> tuple[float, float]:
    if len(values) < 20 or np.any(values <= 0):
        return np.nan, np.nan
    x = np.arange(len(values), dtype="float64")
    y = np.log(values)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    residual = float(np.sum((y - fitted) ** 2))
    total = float(np.sum((y - np.mean(y)) ** 2))
    return float(np.expm1(slope * 252)), 1 - residual / total if total > 0 else 0.0


def _price_max_drawdown(values: np.ndarray) -> float:
    if not len(values):
        return np.nan
    peaks = np.maximum.accumulate(values)
    return float(np.min(values / peaks - 1))


def _maximum_drawdown(returns_pct: Sequence[float]) -> float | None:
    if not returns_pct:
        return None
    equity = np.cumprod(1 + np.asarray(returns_pct, dtype="float64") / 100)
    peaks = np.maximum.accumulate(equity)
    return float(np.min(equity / peaks - 1) * 100)


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else np.nan


def _fundamental_value(item: FundamentalSnapshot | None, field: str) -> float:
    return _float(getattr(item, field, None)) if item is not None else np.nan


def _positive_float(value: Any) -> float | None:
    result = _float(value)
    return result if result is not None and result > 0 else None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _mean(values: Sequence[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None and math.isfinite(value) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    return value
