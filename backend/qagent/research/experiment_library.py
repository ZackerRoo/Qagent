from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from qagent.recommendations.strategy_configuration import parse_paper_strategy_configuration
from qagent.storage.factor_research import FactorResearchExperiment
from qagent.storage.paper import PaperResearchBaseline
from qagent.storage.repository import ScanRunRecord, WalkForwardRunRecord, paper_model_cohort_from_data_health


EXPERIMENT_LIBRARY_SCHEMA_VERSION = "qagent-experiment-library-v1"


class ExperimentLibraryArtifact(BaseModel):
    artifact_id: str
    artifact_type: Literal[
        "strategy_configuration",
        "paper_model_cohort",
        "factor_research",
        "walk_forward_validation",
        "paper_forward_baseline",
    ]
    label: str
    scope: Literal["current_paper", "research_shadow", "historical_development"]
    status: str
    created_at: datetime
    identity_digest: str | None = None
    dataset_revision: int | None = None
    code_revision: str | None = None
    evaluation_window: str | None = None
    evidence: dict[str, str] = Field(default_factory=dict)
    note: str


class ExperimentLibraryReport(BaseModel):
    schema_version: str = EXPERIMENT_LIBRARY_SCHEMA_VERSION
    scope: str = "research_only"
    provider: str
    generated_at: datetime
    artifacts: list[ExperimentLibraryArtifact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def build_experiment_library(
    *,
    provider: str,
    scan_runs: list[ScanRunRecord],
    factor_experiments: list[FactorResearchExperiment],
    walk_forward_runs: list[WalkForwardRunRecord],
    paper_baseline: PaperResearchBaseline | None,
    now: datetime | None = None,
) -> ExperimentLibraryReport:
    """Collect persisted research identities without making a promotion decision.

    The library intentionally leaves historical development, shadow research and
    current paper execution in separate scopes. It is a traceability index, not
    a leaderboard and it never changes simulation admission or model weights.
    """

    normalized_provider = provider.strip().lower()
    artifacts: list[ExperimentLibraryArtifact] = []
    completed_scans = [
        run
        for run in scan_runs
        if run.provider == normalized_provider
        and run.mode == "full_market_batch"
        and run.data_health.get("full_market_scan_complete") == "true"
    ]

    strategy_artifacts = _strategy_configuration_artifacts(completed_scans)
    artifacts.extend(strategy_artifacts)
    cohort = _current_cohort_artifact(completed_scans)
    if cohort is not None:
        artifacts.append(cohort)

    artifacts.extend(
        _factor_research_artifacts(
            experiment
            for experiment in factor_experiments
            if experiment.provider_mode == normalized_provider
        )
    )
    artifacts.extend(
        _walk_forward_artifacts(
            run for run in walk_forward_runs if run.provider == normalized_provider
        )
    )
    if paper_baseline is not None and paper_baseline.provider == normalized_provider:
        artifacts.append(_paper_baseline_artifact(paper_baseline))

    artifacts.sort(key=lambda artifact: artifact.created_at, reverse=True)
    return ExperimentLibraryReport(
        provider=normalized_provider,
        generated_at=now or datetime.now(timezone.utc),
        artifacts=artifacts,
        warnings=[
            "产物库仅组织已保存的研究证据，不会启动回测、改变因子权重或修改模拟盘交易。",
            "历史开发、研究影子与当前模拟盘的样本口径不同；只有同一日期、股票池和成本假设的结果才可横向比较。",
        ],
        data_health={
            "experiment_library_scope": "research_only",
            "experiment_library_source": "sqlite_persisted_artifacts",
            "experiment_library_scan_runs": str(len(completed_scans)),
            "experiment_library_artifacts": str(len(artifacts)),
            "experiment_library_changes_paper_execution": "false",
        },
    )


def _strategy_configuration_artifacts(
    scan_runs: list[ScanRunRecord],
) -> list[ExperimentLibraryArtifact]:
    artifacts: list[ExperimentLibraryArtifact] = []
    seen_digests: set[str] = set()
    for run in scan_runs:
        parsed = parse_paper_strategy_configuration(
            run.data_health.get("paper_strategy_configuration_json"),
            run.data_health.get("paper_strategy_configuration_digest"),
        )
        if parsed is None:
            continue
        configuration, digest = parsed
        if digest in seen_digests:
            continue
        seen_digests.add(digest)
        universe = _mapping(configuration.get("universe"))
        execution = _mapping(configuration.get("execution"))
        selection = _mapping(configuration.get("selection"))
        artifacts.append(
            ExperimentLibraryArtifact(
                artifact_id=f"strategy-configuration:{digest}",
                artifact_type="strategy_configuration",
                label="Frozen paper strategy configuration",
                scope="current_paper",
                status="frozen",
                created_at=_as_utc(run.completed_at or run.created_at),
                identity_digest=digest,
                evaluation_window=str(configuration.get("signal_date") or "unknown"),
                evidence={
                    "scan_run_id": run.run_id,
                    "symbols": str(universe.get("symbol_count") or run.scanned),
                    "etfs": _boolean_text(universe.get("include_etfs")),
                    "max_positions": str(execution.get("max_positions") or "unknown"),
                    "head_limit": str(selection.get("portfolio_head_limit") or "unknown"),
                    "holding_sessions": str(execution.get("max_holding_sessions") or "unknown"),
                },
                note="扫描完成时冻结的策略配方；后续交易来源可据此追溯。",
            )
        )
    return artifacts


def _current_cohort_artifact(
    scan_runs: list[ScanRunRecord],
) -> ExperimentLibraryArtifact | None:
    for run in scan_runs:
        cohort = paper_model_cohort_from_data_health(run.data_health)
        if cohort is None:
            continue
        return ExperimentLibraryArtifact(
            artifact_id=f"paper-model-cohort:{cohort.cohort_id}",
            artifact_type="paper_model_cohort",
            label="Current paper model cohort",
            scope="current_paper",
            status="collecting_forward_evidence",
            created_at=_as_utc(run.completed_at or run.created_at),
            identity_digest=cohort.cohort_id,
            evaluation_window=run.data_health.get("feature_as_of_date"),
            evidence={
                "scan_run_id": run.run_id,
                "feature_set": cohort.feature_set_version,
                "policy": cohort.recommendation_policy_entrypoint,
                "calibration": cohort.calibration_merge_policy,
            },
            note="当前模拟盘 cohort 的身份记录；其表现必须由后续真实纸面成交单独检验。",
        )
    return None


def _factor_research_artifacts(
    experiments: object,
) -> list[ExperimentLibraryArtifact]:
    artifacts: list[ExperimentLibraryArtifact] = []
    ordered = sorted(
        (
            experiment
            for experiment in list(experiments)
            if isinstance(experiment, FactorResearchExperiment)
        ),
        key=lambda experiment: (
            experiment.completed_at or experiment.started_at or experiment.created_at
        ),
        reverse=True,
    )[:10]
    lineage_positions: dict[str, list[FactorResearchExperiment]] = {}
    for experiment in ordered:
        lineage_positions.setdefault(_factor_lineage_key(experiment), []).append(experiment)
    for experiment in ordered:
        if not isinstance(experiment, FactorResearchExperiment):
            continue
        metrics = experiment.metrics or {}
        baseline = _mapping(metrics.get("baseline"))
        challenger = _mapping(metrics.get("lightgbm_challenger"))
        lineage = lineage_positions[_factor_lineage_key(experiment)]
        lineage_index = lineage.index(experiment)
        predecessor = lineage[lineage_index + 1] if lineage_index + 1 < len(lineage) else None
        is_current = lineage_index == 0
        reset_reason = _factor_reset_reason(experiment, predecessor) if is_current else "superseded_by_newer_experiment"
        artifacts.append(
            ExperimentLibraryArtifact(
                artifact_id=experiment.experiment_id,
                artifact_type="factor_research",
                label=experiment.experiment_name,
                scope="research_shadow",
                status=experiment.status,
                created_at=_as_utc(
                    experiment.completed_at or experiment.started_at or experiment.created_at
                ),
                identity_digest=experiment.config_digest,
                dataset_revision=experiment.dataset_revision,
                code_revision=experiment.code_revision,
                evaluation_window=f"{experiment.start_date} to {experiment.end_date}",
                evidence={
                    "model_family": experiment.model_family,
                    "benchmark": experiment.benchmark_id,
                    "cohort_state": "current" if is_current else "predecessor",
                    "predecessor_experiment": predecessor.experiment_id if predecessor else "none",
                    "reset_reason": reset_reason,
                    "evidence_policy": "experiment_scoped_no_carryover",
                    "baseline_rank_ic": _number_text(baseline.get("mean_rank_ic")),
                    "challenger_rank_ic": _number_text(challenger.get("mean_rank_ic")),
                    "activation": _boolean_text(metrics.get("activation_allowed")),
                },
                note="冻结历史上的因子研究结果，只能作为影子研究证据，不能自动替换模拟盘模型。",
            )
        )
    return artifacts


def _factor_lineage_key(experiment: FactorResearchExperiment) -> str:
    return "|".join(
        (experiment.provider_mode, experiment.model_family, experiment.benchmark_id)
    )


def _factor_reset_reason(
    current: FactorResearchExperiment,
    predecessor: FactorResearchExperiment | None,
) -> str:
    if predecessor is None:
        return "initial_experiment_cohort"
    if current.config_digest == predecessor.config_digest:
        return "experiment_restarted_same_config"
    changed = sorted(
        key
        for key in set(current.config) | set(predecessor.config)
        if current.config.get(key) != predecessor.config.get(key)
    )
    suffix = ",".join(changed) if changed else "config_digest"
    return f"new_config_cohort:{suffix}"


def _walk_forward_artifacts(runs: object) -> list[ExperimentLibraryArtifact]:
    artifacts: list[ExperimentLibraryArtifact] = []
    for run in list(runs)[:10]:
        if not isinstance(run, WalkForwardRunRecord):
            continue
        artifacts.append(
            ExperimentLibraryArtifact(
                artifact_id=run.run_id,
                artifact_type="walk_forward_validation",
                label="Walk-forward validation",
                scope="historical_development",
                status=run.status,
                created_at=_as_utc(run.updated_at),
                identity_digest=run.reproducibility_digest,
                dataset_revision=run.dataset_revision,
                evaluation_window=f"{run.start_date} to {run.end_date}",
                evidence={
                    "snapshots": str(run.snapshot_count),
                    "step_sessions": str(run.rebalance_step_sessions),
                    "lookback_days": str(run.lookback_days),
                    "top_5_gate": run.top_5_oos_gate,
                    "top_10_gate": run.top_10_oos_gate,
                },
                note="历史验证记录保留用于开发审计，不与部署后的纸面前向结果混合。",
            )
        )
    return artifacts


def _paper_baseline_artifact(baseline: PaperResearchBaseline) -> ExperimentLibraryArtifact:
    return ExperimentLibraryArtifact(
        artifact_id=baseline.baseline_id,
        artifact_type="paper_forward_baseline",
        label="Paper forward comparison baseline",
        scope="research_shadow",
        status="frozen",
        created_at=_as_utc(baseline.created_at),
        identity_digest=baseline.definition_digest,
        evaluation_window=f"from {baseline.start_date}",
        evidence={
            "paper_session": baseline.paper_session_id,
            "walk_forward_run": baseline.walk_forward_run_id,
            "definition_schema": str(baseline.definition.get("schema_version") or "unknown"),
        },
        note="前向比较的冻结起点；只累计其后的纸面真实成交与共同日期结果。",
    )


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _boolean_text(value: object) -> str:
    return "true" if value is True else "false" if value is False else "unknown"


def _number_text(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:.4f}"
    return "-"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
