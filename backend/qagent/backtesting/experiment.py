from __future__ import annotations

import hashlib
import hmac
import json
import platform
import subprocess
from datetime import date, datetime, timezone
from importlib import metadata
from pathlib import Path

from pydantic import BaseModel, Field

from qagent.backtesting.a_share_rules import RULES_PATH, load_a_share_rule_schedule
from qagent.backtesting.ranking_v3_protocol import build_ranking_v3_protocol
from qagent.backtesting.ranking_v4_protocol import build_ranking_v4_protocol
from qagent.strategies.registry import default_strategy_registry


EXPERIMENT_SCHEMA_VERSION = "walk-forward-experiment-v4"
LEGACY_V3_EXPERIMENT_SCHEMA_VERSION = "walk-forward-experiment-v3"
LEGACY_V2_EXPERIMENT_SCHEMA_VERSION = "walk-forward-experiment-v2"
LEGACY_EXPERIMENT_SCHEMA_VERSION = "walk-forward-experiment-v1"
SELECTION_SNAPSHOT_SCHEMA_VERSION = "walk-forward-selection-snapshot-v2"
UNVERSIONED_V3_COMPONENT = "unversioned"
UNVERSIONED_V4_COMPONENT = "unversioned-v4"
SELECTION_ALGORITHM_VERSION = (
    "historical-shadow-recommendation-v6-ranking-v4-preregistered-candidate-pool50"
)
RESEARCH_SOURCE_DIRECTORIES = (
    "api",
    "backtesting",
    "cards",
    "execution",
    "factors",
    "features",
    "historical_evidence",
    "market",
    "recommendations",
    "signals",
    "storage",
    "strategies",
    "strategy_data",
)
RESEARCH_SOURCE_FILES = (
    "jobs/daily_scan.py",
)
RESEARCH_DEPENDENCY_FILES = (
    "backend/pyproject.toml",
    "backend/uv.lock",
)
RUNTIME_DEPENDENCY_PACKAGES = (
    "akshare",
    "baostock",
    "exchange-calendars",
    "fastapi",
    "httpx",
    "numpy",
    "pandas",
    "pydantic",
    "pydantic-settings",
    "python-dateutil",
    "sqlalchemy",
    "uvicorn",
    "yfinance",
)


class WalkForwardExperimentManifest(BaseModel):
    schema_version: str
    selection_snapshot_schema_version: str = SELECTION_SNAPSHOT_SCHEMA_VERSION
    experiment_digest: str
    execution_digest: str = UNVERSIONED_V4_COMPONENT
    created_at: datetime
    code_revision: str
    code_dirty: bool
    python_version: str
    provider_mode: str
    dataset_revision: int
    start_date: date
    end_date: date
    rebalance_step_sessions: int
    lookback_days: int
    selection_algorithm_version: str
    strategy_registry_digest: str
    strategy_ids: list[str]
    execution_rule_set_version: str
    fee_schedule_version: str
    execution_rules_digest: str
    ranking_v3_protocol_digest: str = UNVERSIONED_V3_COMPONENT
    candidate_ledger_implementation_version: str = UNVERSIONED_V3_COMPONENT
    ranking_v3_statistics_implementation_version: str = UNVERSIONED_V3_COMPONENT
    ranking_v4_protocol_digest: str = UNVERSIONED_V4_COMPONENT
    ranking_v4_experiment_registry_digest: str = UNVERSIONED_V4_COMPONENT
    ranking_v4_candidate_implementation_version: str = UNVERSIONED_V4_COMPONENT
    ranking_v4_model_implementation_version: str = UNVERSIONED_V4_COMPONENT
    ranking_v4_portfolio_implementation_version: str = UNVERSIONED_V4_COMPONENT
    ranking_v4_statistics_implementation_version: str = UNVERSIONED_V4_COMPONENT
    research_source_digest: str = UNVERSIONED_V3_COMPONENT
    runtime_dependency_digest: str = UNVERSIONED_V4_COMPONENT
    dirty_worktree_digest: str = UNVERSIONED_V4_COMPONENT
    runtime_revisions: list[str] = Field(default_factory=list)


def build_walk_forward_experiment_manifest(
    *,
    provider_mode: str,
    dataset_revision: int,
    start_date: date,
    end_date: date,
    rebalance_step_sessions: int,
    lookback_days: int,
) -> WalkForwardExperimentManifest:
    registry_payload = sorted(
        (definition.model_dump(mode="json") for definition in default_strategy_registry().all()),
        key=lambda item: item["strategy_id"],
    )
    strategy_ids = [item["strategy_id"] for item in registry_payload]
    registry_digest = _digest(registry_payload)
    schedule = load_a_share_rule_schedule()
    rules_digest = hashlib.sha256(RULES_PATH.read_bytes()).hexdigest()
    revision, dirty = _git_revision()
    ranking_v3_protocol = build_ranking_v3_protocol()
    ranking_v4_protocol = build_ranking_v4_protocol()
    research_source_digest = _research_source_digest()
    runtime_dependency_digest = _runtime_dependency_digest()
    dirty_worktree_digest = (
        _dirty_worktree_digest() if dirty else _digest({"worktree": "clean"})
    )
    stable_payload = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "selection_snapshot_schema_version": SELECTION_SNAPSHOT_SCHEMA_VERSION,
        "provider_mode": provider_mode,
        "dataset_revision": dataset_revision,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "rebalance_step_sessions": rebalance_step_sessions,
        "lookback_days": lookback_days,
        "selection_algorithm_version": SELECTION_ALGORITHM_VERSION,
        "strategy_registry_digest": registry_digest,
        "strategy_ids": strategy_ids,
        "execution_rule_set_version": schedule.rule_set_version,
        "fee_schedule_version": schedule.fee_schedule_version,
        "execution_rules_digest": rules_digest,
        "ranking_v3_protocol_digest": ranking_v3_protocol.protocol_digest,
        "candidate_ledger_implementation_version": (
            ranking_v3_protocol.candidate_ledger_implementation_version
        ),
        "ranking_v3_statistics_implementation_version": (
            ranking_v3_protocol.statistics_implementation_version
        ),
        "ranking_v4_protocol_digest": ranking_v4_protocol.protocol_digest,
        "ranking_v4_experiment_registry_digest": (
            ranking_v4_protocol.experiment_registry.registry_digest
        ),
        "ranking_v4_candidate_implementation_version": (
            ranking_v4_protocol.candidate_definition.implementation_version
        ),
        "ranking_v4_model_implementation_version": (
            ranking_v4_protocol.model_definition.implementation_version
        ),
        "ranking_v4_portfolio_implementation_version": (
            ranking_v4_protocol.portfolio_definition.implementation_version
        ),
        "ranking_v4_statistics_implementation_version": (
            ranking_v4_protocol.statistics_definition.implementation_version
        ),
        "research_source_digest": research_source_digest,
    }
    manifest = WalkForwardExperimentManifest(
        **stable_payload,
        experiment_digest=_digest(stable_payload),
        code_revision=revision,
        created_at=datetime.now(timezone.utc),
        code_dirty=dirty,
        python_version=platform.python_version(),
        runtime_dependency_digest=runtime_dependency_digest,
        dirty_worktree_digest=dirty_worktree_digest,
        runtime_revisions=[revision],
    )
    return _with_execution_digest(manifest)


def walk_forward_manifests_semantically_compatible(
    stored: WalkForwardExperimentManifest,
    current: WalkForwardExperimentManifest,
) -> bool:
    """Allow runtime-only upgrades while keeping every research input fixed."""

    if not (
        walk_forward_manifest_digest_is_valid(stored)
        and walk_forward_manifest_digest_is_valid(current)
    ):
        return False
    semantic_fields = (
        "schema_version",
        "provider_mode",
        "dataset_revision",
        "start_date",
        "end_date",
        "rebalance_step_sessions",
        "lookback_days",
        "selection_algorithm_version",
        "strategy_registry_digest",
        "strategy_ids",
        "execution_rule_set_version",
        "fee_schedule_version",
        "execution_rules_digest",
        "ranking_v3_protocol_digest",
        "candidate_ledger_implementation_version",
        "ranking_v3_statistics_implementation_version",
        "ranking_v4_protocol_digest",
        "ranking_v4_experiment_registry_digest",
        "ranking_v4_candidate_implementation_version",
        "ranking_v4_model_implementation_version",
        "ranking_v4_portfolio_implementation_version",
        "ranking_v4_statistics_implementation_version",
        "research_source_digest",
    )
    return all(getattr(stored, field) == getattr(current, field) for field in semantic_fields)


def walk_forward_selection_manifests_semantically_compatible(
    stored: WalkForwardExperimentManifest,
    current: WalkForwardExperimentManifest,
) -> bool:
    """Allow persisted selection snapshots to survive execution-only upgrades."""

    if not (
        walk_forward_manifest_digest_is_valid(stored)
        and walk_forward_manifest_digest_is_valid(current)
    ):
        return False
    selection_fields = (
        "selection_snapshot_schema_version",
        "provider_mode",
        "dataset_revision",
        "start_date",
        "end_date",
        "rebalance_step_sessions",
        "lookback_days",
        "selection_algorithm_version",
        "strategy_registry_digest",
        "strategy_ids",
        "ranking_v3_protocol_digest",
        "candidate_ledger_implementation_version",
        "ranking_v3_statistics_implementation_version",
        "ranking_v4_protocol_digest",
        "ranking_v4_experiment_registry_digest",
        "ranking_v4_candidate_implementation_version",
        "ranking_v4_model_implementation_version",
        "ranking_v4_portfolio_implementation_version",
        "ranking_v4_statistics_implementation_version",
        "research_source_digest",
    )
    return all(getattr(stored, field) == getattr(current, field) for field in selection_fields)


def walk_forward_manifest_digest_is_valid(
    manifest: WalkForwardExperimentManifest,
) -> bool:
    if manifest.schema_version == LEGACY_EXPERIMENT_SCHEMA_VERSION:
        return manifest.experiment_digest == _digest(_legacy_manifest_digest_payload(manifest))
    if manifest.schema_version == LEGACY_V2_EXPERIMENT_SCHEMA_VERSION:
        return manifest.experiment_digest == _digest(_v2_semantic_manifest_digest_payload(manifest))
    if manifest.schema_version == LEGACY_V3_EXPERIMENT_SCHEMA_VERSION:
        return manifest.experiment_digest == _digest(_v3_semantic_manifest_digest_payload(manifest))
    if manifest.schema_version != EXPERIMENT_SCHEMA_VERSION:
        return False
    semantic_valid = hmac.compare_digest(
        manifest.experiment_digest,
        _digest(_semantic_manifest_digest_payload(manifest)),
    )
    execution_valid = hmac.compare_digest(
        manifest.execution_digest,
        _digest(_execution_manifest_digest_payload(manifest)),
    )
    return semantic_valid and execution_valid


def _semantic_manifest_digest_payload(
    manifest: WalkForwardExperimentManifest,
) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "selection_snapshot_schema_version": (manifest.selection_snapshot_schema_version),
        "provider_mode": manifest.provider_mode,
        "dataset_revision": manifest.dataset_revision,
        "start_date": manifest.start_date.isoformat(),
        "end_date": manifest.end_date.isoformat(),
        "rebalance_step_sessions": manifest.rebalance_step_sessions,
        "lookback_days": manifest.lookback_days,
        "selection_algorithm_version": manifest.selection_algorithm_version,
        "strategy_registry_digest": manifest.strategy_registry_digest,
        "strategy_ids": manifest.strategy_ids,
        "execution_rule_set_version": manifest.execution_rule_set_version,
        "fee_schedule_version": manifest.fee_schedule_version,
        "execution_rules_digest": manifest.execution_rules_digest,
        "ranking_v3_protocol_digest": manifest.ranking_v3_protocol_digest,
        "candidate_ledger_implementation_version": (
            manifest.candidate_ledger_implementation_version
        ),
        "ranking_v3_statistics_implementation_version": (
            manifest.ranking_v3_statistics_implementation_version
        ),
        "ranking_v4_protocol_digest": manifest.ranking_v4_protocol_digest,
        "ranking_v4_experiment_registry_digest": (manifest.ranking_v4_experiment_registry_digest),
        "ranking_v4_candidate_implementation_version": (
            manifest.ranking_v4_candidate_implementation_version
        ),
        "ranking_v4_model_implementation_version": (
            manifest.ranking_v4_model_implementation_version
        ),
        "ranking_v4_portfolio_implementation_version": (
            manifest.ranking_v4_portfolio_implementation_version
        ),
        "ranking_v4_statistics_implementation_version": (
            manifest.ranking_v4_statistics_implementation_version
        ),
        "research_source_digest": manifest.research_source_digest,
    }


def _v3_semantic_manifest_digest_payload(
    manifest: WalkForwardExperimentManifest,
) -> dict[str, object]:
    payload = _semantic_manifest_digest_payload(manifest)
    for field in (
        "strategy_ids",
        "ranking_v4_protocol_digest",
        "ranking_v4_experiment_registry_digest",
        "ranking_v4_candidate_implementation_version",
        "ranking_v4_model_implementation_version",
        "ranking_v4_portfolio_implementation_version",
        "ranking_v4_statistics_implementation_version",
    ):
        payload.pop(field)
    return payload


def _v2_semantic_manifest_digest_payload(
    manifest: WalkForwardExperimentManifest,
) -> dict[str, object]:
    payload = _v3_semantic_manifest_digest_payload(manifest)
    payload.pop("research_source_digest")
    return payload


def _legacy_manifest_digest_payload(
    manifest: WalkForwardExperimentManifest,
) -> dict[str, object]:
    stable_payload = {
        "schema_version": manifest.schema_version,
        "code_revision": manifest.code_revision,
        "provider_mode": manifest.provider_mode,
        "dataset_revision": manifest.dataset_revision,
        "start_date": manifest.start_date.isoformat(),
        "end_date": manifest.end_date.isoformat(),
        "rebalance_step_sessions": manifest.rebalance_step_sessions,
        "lookback_days": manifest.lookback_days,
        "selection_algorithm_version": manifest.selection_algorithm_version,
        "strategy_registry_digest": manifest.strategy_registry_digest,
        "execution_rule_set_version": manifest.execution_rule_set_version,
        "fee_schedule_version": manifest.fee_schedule_version,
        "execution_rules_digest": manifest.execution_rules_digest,
    }
    return stable_payload


def record_walk_forward_runtime_revision(
    stored: WalkForwardExperimentManifest,
    current: WalkForwardExperimentManifest,
) -> WalkForwardExperimentManifest:
    if not walk_forward_manifests_semantically_compatible(stored, current):
        raise ValueError("walk-forward experiment definitions are not compatible")
    revisions = list(
        dict.fromkeys(
            [
                stored.code_revision,
                *stored.runtime_revisions,
                current.code_revision,
                *current.runtime_revisions,
            ]
        )
    )
    updated = stored.model_copy(
        update={
            "runtime_revisions": revisions,
            "code_dirty": stored.code_dirty or current.code_dirty,
            "python_version": current.python_version,
            "runtime_dependency_digest": current.runtime_dependency_digest,
            "dirty_worktree_digest": current.dirty_worktree_digest,
        }
    )
    return _with_execution_digest(updated)


def upgrade_walk_forward_execution_manifest(
    stored: WalkForwardExperimentManifest,
    current: WalkForwardExperimentManifest,
) -> WalkForwardExperimentManifest:
    if not walk_forward_selection_manifests_semantically_compatible(stored, current):
        raise ValueError("walk-forward selection definitions are not compatible")
    revisions = list(
        dict.fromkeys(
            [
                stored.code_revision,
                *stored.runtime_revisions,
                current.code_revision,
                *current.runtime_revisions,
            ]
        )
    )
    updated = current.model_copy(
        update={
            "runtime_revisions": revisions,
            "code_dirty": stored.code_dirty or current.code_dirty,
        }
    )
    return _with_execution_digest(updated)


def _execution_manifest_digest_payload(
    manifest: WalkForwardExperimentManifest,
) -> dict[str, object]:
    return {
        "experiment_digest": manifest.experiment_digest,
        "code_revision": manifest.code_revision,
        "code_dirty": manifest.code_dirty,
        "python_version": manifest.python_version,
        "research_source_digest": manifest.research_source_digest,
        "runtime_dependency_digest": manifest.runtime_dependency_digest,
        "dirty_worktree_digest": manifest.dirty_worktree_digest,
        "runtime_revisions": manifest.runtime_revisions,
    }


def _with_execution_digest(
    manifest: WalkForwardExperimentManifest,
) -> WalkForwardExperimentManifest:
    return manifest.model_copy(
        update={
            "execution_digest": _digest(_execution_manifest_digest_payload(manifest)),
        }
    )


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_revision() -> tuple[str, bool]:
    repository_root = Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=repository_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        )
        return revision, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", False


def _research_source_digest() -> str:
    package_root = Path(__file__).resolve().parents[1]
    repository_root = Path(__file__).resolve().parents[3]
    files = [
        *_research_source_paths(package_root),
        *_research_dependency_paths(repository_root),
    ]
    return _file_set_digest(files, relative_to=repository_root)


def _file_set_digest(
    files: list[Path],
    *,
    relative_to: Path,
) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(relative_to).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _research_source_paths(package_root: Path) -> list[Path]:
    directory_files = [
        path
        for directory in RESEARCH_SOURCE_DIRECTORIES
        for path in (package_root / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    explicit_files = [package_root / relative_path for relative_path in RESEARCH_SOURCE_FILES]
    missing = [path for path in explicit_files if not path.is_file()]
    if missing:
        names = ", ".join(path.relative_to(package_root).as_posix() for path in missing)
        raise FileNotFoundError(f"walk-forward research source files missing: {names}")
    return sorted(set([*directory_files, *explicit_files]))


def _research_dependency_paths(repository_root: Path) -> list[Path]:
    files = [repository_root / relative_path for relative_path in RESEARCH_DEPENDENCY_FILES]
    missing = [path for path in files if not path.is_file()]
    if missing:
        names = ", ".join(path.relative_to(repository_root).as_posix() for path in missing)
        raise FileNotFoundError(f"walk-forward dependency lock files missing: {names}")
    return sorted(files)


def _runtime_dependency_digest() -> str:
    versions: dict[str, str] = {}
    for package in RUNTIME_DEPENDENCY_PACKAGES:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "missing"
    return _digest(versions)


def _dirty_worktree_digest() -> str:
    repository_root = Path(__file__).resolve().parents[3]
    try:
        tracked_diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
        untracked_output = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return _digest({"worktree": "unavailable"})

    digest = hashlib.sha256()
    digest.update(tracked_diff)
    digest.update(b"\0")
    for raw_name in sorted(name for name in untracked_output.split(b"\0") if name):
        relative_name = raw_name.decode("utf-8", errors="surrogateescape")
        path = repository_root / relative_name
        digest.update(raw_name)
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
