from datetime import date

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

    assert stored.experiment_digest != current.experiment_digest
    assert (
        stored.selection_algorithm_version
        == "historical-shadow-recommendation-v3-balanced"
    )
    assert experiment.walk_forward_manifests_semantically_compatible(stored, current)

    resumed = experiment.record_walk_forward_runtime_revision(stored, current)

    assert resumed.experiment_digest == stored.experiment_digest
    assert resumed.runtime_revisions == ["revision-a", "revision-b"]


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
