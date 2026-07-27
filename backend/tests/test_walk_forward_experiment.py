from datetime import date
from pathlib import Path

import pytest

from qagent.backtesting import experiment


def test_runtime_only_revision_change_keeps_walk_forward_resume_compatible(monkeypatch):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    stored = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-b", False))
    current = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )

    assert stored.experiment_digest == current.experiment_digest
    assert (
        stored.selection_algorithm_version
        == "historical-shadow-recommendation-v6-ranking-v4-preregistered-candidate-pool50"
    )
    assert experiment.walk_forward_manifests_semantically_compatible(stored, current)

    resumed = experiment.record_walk_forward_runtime_revision(stored, current)

    assert resumed.experiment_digest == stored.experiment_digest
    assert resumed.runtime_revisions == ["revision-a", "revision-b"]
    assert resumed.execution_digest != stored.execution_digest
    assert experiment.walk_forward_manifest_digest_is_valid(resumed)


def test_current_manifest_execution_digest_detects_runtime_provenance_tampering(monkeypatch):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    manifest = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )

    assert experiment.walk_forward_manifest_digest_is_valid(manifest)
    assert not experiment.walk_forward_manifest_digest_is_valid(
        manifest.model_copy(update={"code_dirty": True})
    )
    assert not experiment.walk_forward_manifest_digest_is_valid(
        manifest.model_copy(update={"runtime_revisions": ["revision-a", "revision-b"]})
    )
    assert not experiment.walk_forward_manifest_digest_is_valid(
        manifest.model_copy(update={"execution_digest": "0" * 64})
    )


def test_research_source_digest_covers_selection_and_point_in_time_runtime_modules():
    package_root = Path(experiment.__file__).resolve().parents[1]
    covered = {
        path.relative_to(package_root).as_posix()
        for path in experiment._research_source_paths(package_root)
    }

    assert {
        "api/routes.py",
        "backtesting/walk_forward.py",
        "cards/generator.py",
        "execution/engine.py",
        "features/snapshots.py",
        "historical_evidence/providers.py",
        "jobs/daily_scan.py",
        "signals/engine.py",
        "storage/repository.py",
        "storage/replay_evidence.py",
        "strategy_data/providers.py",
    } <= covered
    repository_root = Path(experiment.__file__).resolve().parents[3]
    dependency_files = {
        path.relative_to(repository_root).as_posix()
        for path in experiment._research_dependency_paths(repository_root)
    }
    assert {"backend/pyproject.toml", "backend/uv.lock"} <= dependency_files


def test_dependency_lock_content_changes_research_digest(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    first = experiment._file_set_digest([lock], relative_to=tmp_path)

    lock.write_text("version = 2\n", encoding="utf-8")
    second = experiment._file_set_digest([lock], relative_to=tmp_path)

    assert first != second


def test_dirty_content_and_runtime_dependency_versions_change_execution_digest(
    monkeypatch,
):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", True))
    monkeypatch.setattr(experiment, "_dirty_worktree_digest", lambda: "dirty-a")
    monkeypatch.setattr(experiment, "_runtime_dependency_digest", lambda: "dependencies-a")
    first = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=10,
        lookback_days=400,
    )

    monkeypatch.setattr(experiment, "_dirty_worktree_digest", lambda: "dirty-b")
    dirty_changed = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=10,
        lookback_days=400,
    )
    monkeypatch.setattr(experiment, "_runtime_dependency_digest", lambda: "dependencies-b")
    dependency_changed = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=10,
        lookback_days=400,
    )

    assert first.experiment_digest == dirty_changed.experiment_digest
    assert first.execution_digest != dirty_changed.execution_digest
    assert dirty_changed.execution_digest != dependency_changed.execution_digest
    assert experiment.walk_forward_manifest_digest_is_valid(dependency_changed)


def test_research_source_change_changes_semantic_manifest_digest(monkeypatch):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    monkeypatch.setattr(experiment, "_research_source_digest", lambda: "source-a")
    first = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=10,
        lookback_days=400,
    )

    monkeypatch.setattr(experiment, "_research_source_digest", lambda: "source-b")
    second = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=10,
        lookback_days=400,
    )

    assert first.experiment_digest != second.experiment_digest
    assert not experiment.walk_forward_manifests_semantically_compatible(first, second)


def test_v3_validation_identity_change_rejects_resume_and_selection_snapshots(
    monkeypatch,
):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    protocol = experiment.build_ranking_v3_protocol()
    stored = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )

    changes = (
        {"protocol_digest": "changed-protocol"},
        {"candidate_ledger_implementation_version": "candidate-ledger-v-next"},
        {"statistics_implementation_version": "ranking-statistics-v-next"},
    )
    for update in changes:
        changed_protocol = protocol.model_copy(update=update)
        monkeypatch.setattr(
            experiment,
            "build_ranking_v3_protocol",
            lambda protocol=changed_protocol: protocol,
        )
        changed = experiment.build_walk_forward_experiment_manifest(
            provider_mode="free",
            dataset_revision=7,
            start_date=date(2021, 11, 1),
            end_date=date(2025, 12, 31),
            rebalance_step_sessions=5,
            lookback_days=400,
        )

        assert changed.experiment_digest != stored.experiment_digest
        assert not experiment.walk_forward_manifests_semantically_compatible(
            stored,
            changed,
        )
        assert not experiment.walk_forward_selection_manifests_semantically_compatible(
            stored,
            changed,
        )
        with pytest.raises(
            ValueError,
            match="selection definitions are not compatible",
        ):
            experiment.upgrade_walk_forward_execution_manifest(
                stored,
                changed,
            )


def test_v3_protocol_component_versions_are_frozen_into_manifest(monkeypatch):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    manifest = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    protocol = experiment.build_ranking_v3_protocol()

    assert manifest.ranking_v3_protocol_digest == protocol.protocol_digest
    assert (
        manifest.candidate_ledger_implementation_version
        == protocol.candidate_ledger_implementation_version
    )
    assert (
        manifest.ranking_v3_statistics_implementation_version
        == protocol.statistics_implementation_version
    )
    assert experiment.walk_forward_manifest_digest_is_valid(manifest)


def test_v4_protocol_component_versions_are_frozen_into_manifest(monkeypatch):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    manifest = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    protocol = experiment.build_ranking_v4_protocol()

    assert manifest.ranking_v4_protocol_digest == protocol.protocol_digest
    assert (
        manifest.ranking_v4_experiment_registry_digest
        == protocol.experiment_registry.registry_digest
    )
    assert (
        manifest.ranking_v4_candidate_implementation_version
        == protocol.candidate_definition.implementation_version
    )
    assert (
        manifest.ranking_v4_model_implementation_version
        == protocol.model_definition.implementation_version
    )
    assert (
        manifest.ranking_v4_portfolio_implementation_version
        == protocol.portfolio_definition.implementation_version
    )
    assert (
        manifest.ranking_v4_statistics_implementation_version
        == protocol.statistics_definition.implementation_version
    )
    assert experiment.walk_forward_manifest_digest_is_valid(manifest)


def test_v4_identity_change_rejects_resume_and_selection_snapshots(monkeypatch):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    protocol = experiment.build_ranking_v4_protocol()
    stored = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )

    changes = (
        {"protocol_digest": "changed-v4-protocol"},
        {
            "candidate_definition": protocol.candidate_definition.model_copy(
                update={"implementation_version": "ranking-v4-candidate-next"}
            )
        },
        {
            "model_definition": protocol.model_definition.model_copy(
                update={"implementation_version": "ranking-v4-model-next"}
            )
        },
        {
            "portfolio_definition": protocol.portfolio_definition.model_copy(
                update={"implementation_version": "ranking-v4-portfolio-next"}
            )
        },
        {
            "statistics_definition": protocol.statistics_definition.model_copy(
                update={"implementation_version": "ranking-v4-statistics-next"}
            )
        },
    )
    for update in changes:
        changed_protocol = protocol.model_copy(update=update)
        monkeypatch.setattr(
            experiment,
            "build_ranking_v4_protocol",
            lambda protocol=changed_protocol: protocol,
        )
        changed = experiment.build_walk_forward_experiment_manifest(
            provider_mode="free",
            dataset_revision=7,
            start_date=date(2021, 11, 1),
            end_date=date(2025, 12, 31),
            rebalance_step_sessions=5,
            lookback_days=400,
        )

        assert changed.experiment_digest != stored.experiment_digest
        assert not experiment.walk_forward_manifests_semantically_compatible(
            stored,
            changed,
        )
        assert not experiment.walk_forward_selection_manifests_semantically_compatible(
            stored,
            changed,
        )


def test_legacy_v3_manifest_cannot_reuse_ranking_v4_selection_snapshots(monkeypatch):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    current = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    legacy = current.model_copy(
        update={
            "schema_version": experiment.LEGACY_V3_EXPERIMENT_SCHEMA_VERSION,
            "selection_snapshot_schema_version": ("walk-forward-selection-snapshot-v1"),
            "selection_algorithm_version": (
                "historical-shadow-recommendation-v5-ranking-v3-candidate-pool50"
            ),
            "ranking_v4_protocol_digest": experiment.UNVERSIONED_V4_COMPONENT,
            "ranking_v4_experiment_registry_digest": (experiment.UNVERSIONED_V4_COMPONENT),
            "ranking_v4_candidate_implementation_version": (experiment.UNVERSIONED_V4_COMPONENT),
            "ranking_v4_model_implementation_version": (experiment.UNVERSIONED_V4_COMPONENT),
            "ranking_v4_portfolio_implementation_version": (experiment.UNVERSIONED_V4_COMPONENT),
            "ranking_v4_statistics_implementation_version": (experiment.UNVERSIONED_V4_COMPONENT),
        }
    )
    legacy = legacy.model_copy(
        update={
            "experiment_digest": experiment._digest(
                experiment._v3_semantic_manifest_digest_payload(legacy)
            )
        }
    )

    assert experiment.walk_forward_manifest_digest_is_valid(legacy)
    assert not experiment.walk_forward_manifests_semantically_compatible(
        legacy,
        current,
    )
    assert not experiment.walk_forward_selection_manifests_semantically_compatible(
        legacy,
        current,
    )


def test_legacy_manifest_cannot_reuse_ranking_v3_selection_snapshots(monkeypatch):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    current = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    legacy = current.model_copy(
        update={
            "schema_version": experiment.LEGACY_EXPERIMENT_SCHEMA_VERSION,
            "ranking_v3_protocol_digest": experiment.UNVERSIONED_V3_COMPONENT,
            "candidate_ledger_implementation_version": (experiment.UNVERSIONED_V3_COMPONENT),
            "ranking_v3_statistics_implementation_version": (experiment.UNVERSIONED_V3_COMPONENT),
        }
    )
    legacy = legacy.model_copy(
        update={
            "experiment_digest": experiment._digest(
                experiment._legacy_manifest_digest_payload(legacy)
            )
        }
    )

    assert experiment.walk_forward_manifest_digest_is_valid(legacy)
    assert not experiment.walk_forward_manifests_semantically_compatible(
        legacy,
        current,
    )
    assert not experiment.walk_forward_selection_manifests_semantically_compatible(
        legacy,
        current,
    )
    with pytest.raises(
        ValueError,
        match="selection definitions are not compatible",
    ):
        experiment.upgrade_walk_forward_execution_manifest(legacy, current)


def test_semantic_change_rejects_walk_forward_resume(monkeypatch):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    stored = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    changed = stored.model_copy(
        update={
            "selection_algorithm_version": "different-selection-definition",
        }
    )

    assert not experiment.walk_forward_manifests_semantically_compatible(
        stored,
        changed,
    )


def test_execution_only_change_can_reuse_selection_checkpoints(tmp_path, monkeypatch):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"revision":"old"}', encoding="utf-8")
    monkeypatch.setattr(experiment, "RULES_PATH", rules_path)
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    stored = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    rules_path.write_text('{"revision":"new"}', encoding="utf-8")
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-b", False))
    current = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )

    assert not experiment.walk_forward_manifests_semantically_compatible(
        stored,
        current,
    )
    assert experiment.walk_forward_selection_manifests_semantically_compatible(
        stored,
        current,
    )

    resumed = experiment.upgrade_walk_forward_execution_manifest(stored, current)

    assert resumed.experiment_digest == current.experiment_digest
    assert resumed.execution_rules_digest == current.execution_rules_digest
    assert resumed.runtime_revisions == ["revision-a", "revision-b"]
