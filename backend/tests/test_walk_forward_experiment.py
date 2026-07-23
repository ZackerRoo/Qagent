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
