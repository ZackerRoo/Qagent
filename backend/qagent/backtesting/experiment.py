from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from qagent.backtesting.a_share_rules import RULES_PATH, load_a_share_rule_schedule
from qagent.backtesting.ranking_v3_protocol import build_ranking_v3_protocol
from qagent.strategies.registry import default_strategy_registry


EXPERIMENT_SCHEMA_VERSION = "walk-forward-experiment-v3"
LEGACY_V2_EXPERIMENT_SCHEMA_VERSION = "walk-forward-experiment-v2"
LEGACY_EXPERIMENT_SCHEMA_VERSION = "walk-forward-experiment-v1"
SELECTION_SNAPSHOT_SCHEMA_VERSION = "walk-forward-selection-snapshot-v1"
UNVERSIONED_V3_COMPONENT = "unversioned"
SELECTION_ALGORITHM_VERSION = "historical-shadow-recommendation-v5-ranking-v3-candidate-pool50"


class WalkForwardExperimentManifest(BaseModel):
    schema_version: str
    selection_snapshot_schema_version: str = SELECTION_SNAPSHOT_SCHEMA_VERSION
    experiment_digest: str
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
    research_source_digest: str = UNVERSIONED_V3_COMPONENT
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
    registry_digest = _digest(registry_payload)
    schedule = load_a_share_rule_schedule()
    rules_digest = hashlib.sha256(RULES_PATH.read_bytes()).hexdigest()
    revision, dirty = _git_revision()
    ranking_v3_protocol = build_ranking_v3_protocol()
    research_source_digest = _research_source_digest()
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
        "research_source_digest": research_source_digest,
    }
    return WalkForwardExperimentManifest(
        **stable_payload,
        experiment_digest=_digest(stable_payload),
        code_revision=revision,
        created_at=datetime.now(timezone.utc),
        code_dirty=dirty,
        python_version=platform.python_version(),
        strategy_ids=[item["strategy_id"] for item in registry_payload],
        runtime_revisions=[revision],
    )


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
        "research_source_digest",
    )
    return all(getattr(stored, field) == getattr(current, field) for field in selection_fields)


def walk_forward_manifest_digest_is_valid(
    manifest: WalkForwardExperimentManifest,
) -> bool:
    if manifest.schema_version == LEGACY_EXPERIMENT_SCHEMA_VERSION:
        return manifest.experiment_digest == _digest(_legacy_manifest_digest_payload(manifest))
    if manifest.schema_version == LEGACY_V2_EXPERIMENT_SCHEMA_VERSION:
        return manifest.experiment_digest == _digest(
            _v2_semantic_manifest_digest_payload(manifest)
        )
    if manifest.schema_version != EXPERIMENT_SCHEMA_VERSION:
        return False
    return manifest.experiment_digest == _digest(_semantic_manifest_digest_payload(manifest))


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
        "research_source_digest": manifest.research_source_digest,
    }


def _v2_semantic_manifest_digest_payload(
    manifest: WalkForwardExperimentManifest,
) -> dict[str, object]:
    payload = _semantic_manifest_digest_payload(manifest)
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
    return stored.model_copy(
        update={
            "runtime_revisions": revisions,
            "code_dirty": stored.code_dirty or current.code_dirty,
        }
    )


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
    return current.model_copy(
        update={
            "runtime_revisions": revisions,
            "code_dirty": stored.code_dirty or current.code_dirty,
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
    included_roots = (
        package_root / "backtesting",
        package_root / "factors",
        package_root / "market",
        package_root / "recommendations",
        package_root / "strategies",
    )
    files = sorted(
        path
        for root in included_roots
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
