from datetime import date

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
        == "historical-shadow-recommendation-v5-ranking-v3-candidate-pool50"
    )
    assert experiment.walk_forward_manifests_semantically_compatible(stored, current)

    resumed = experiment.record_walk_forward_runtime_revision(stored, current)

    assert resumed.experiment_digest == stored.experiment_digest
    assert resumed.runtime_revisions == ["revision-a", "revision-b"]


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
