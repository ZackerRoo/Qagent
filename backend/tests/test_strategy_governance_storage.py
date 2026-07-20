from concurrent.futures import ThreadPoolExecutor

import pytest

from qagent.db import create_session_factory, initialize_database
from qagent.storage.repository import QagentRepository
from qagent.strategies.governance import decide_state_transition
from qagent.strategies.models import StrategyPolicy, StrategyState
from qagent.strategies.registry import default_strategy_registry


def _policies() -> tuple[StrategyPolicy, StrategyPolicy]:
    previous = StrategyPolicy(
        strategy_id="trend_momentum_stage2",
        policy_version="trend-policy-v1",
        strategy_version="trend-v1",
        factor_version="factor-v1",
        parameter_version="params-v1",
        universe_version="cn-equity-v1",
        data_revision=11,
        state=StrategyState.RESEARCH,
    )
    current = StrategyPolicy(
        strategy_id="trend_momentum_stage2",
        policy_version="trend-policy-v2",
        strategy_version="trend-v2",
        factor_version="factor-v2",
        parameter_version="params-v2",
        universe_version="cn-equity-v2",
        data_revision=12,
        state=StrategyState.SHADOW,
        base_weight=0.20,
        rollback_policy_version=previous.policy_version,
    )
    return previous, current


def _make_repo(tmp_path) -> QagentRepository:
    database_url = f"sqlite:///{tmp_path / 'strategy-governance.db'}"
    initialize_database(database_url)
    return QagentRepository(create_session_factory(database_url))


def test_initialize_defaults_registers_builtin_strategies_in_research(tmp_path):
    repo = _make_repo(tmp_path)

    states = repo.initialize_strategy_defaults()

    assert [item.strategy_id for item in states] == sorted(
        default_strategy_registry().strategy_ids()
    )
    assert all(item.state is StrategyState.RESEARCH for item in states)
    assert all(item.current_deployment_id is None for item in states)
    assert len(repo.list_strategy_versions()) == len(states)
    assert repo.list_policy_deployments() == []


def test_initialize_defaults_is_additive_idempotent_and_snapshot_immutable(tmp_path):
    repo = _make_repo(tmp_path)
    previous, current = _policies()

    states = repo.initialize_strategy_governance_defaults(policies=[previous, current])
    repeated = repo.initialize_strategy_governance_defaults(policies=[previous, current])
    versions = repo.list_strategy_versions(current.strategy_id)
    deployments = repo.list_policy_deployments(current.strategy_id)

    assert states == repeated
    assert len(states) == 1
    assert states[0].state is StrategyState.SHADOW
    assert states[0].current_policy_version == current.policy_version
    assert states[0].previous_policy_version == previous.policy_version
    assert {item.strategy_version for item in versions} == {"trend-v1", "trend-v2"}
    assert {item.policy_version for item in deployments} == {
        previous.policy_version,
        current.policy_version,
    }
    current_deployment = next(
        item for item in deployments if item.policy_version == current.policy_version
    )
    previous_deployment = next(
        item for item in deployments if item.policy_version == previous.policy_version
    )
    assert current_deployment.previous_deployment_id == previous_deployment.deployment_id
    assert current_deployment.policy == current

    conflicting = current.model_copy(update={"base_weight": 0.30})
    with pytest.raises(ValueError, match="immutable"):
        repo.initialize_strategy_governance_defaults(policies=[previous, conflicting])

    assert repo.get_strategy_state(current.strategy_id) == states[0]
    assert len(repo.list_policy_deployments(current.strategy_id)) == 2


def test_record_governance_decision_is_atomic_and_idempotent(tmp_path):
    repo = _make_repo(tmp_path)
    previous, current = _policies()
    repo.initialize_strategy_governance_defaults(policies=[previous, current])
    decision = decide_state_transition(StrategyState.SHADOW, StrategyState.ADMITTED)
    evidence = {"sample_count": 48, "confidence_low_pct": 0.30}

    event = repo.record_governance_decision(
        current,
        decision,
        evidence,
        "admission-window-2026-07-17",
    )
    replay = repo.record_governance_decision(
        current,
        decision,
        evidence,
        "admission-window-2026-07-17",
    )
    state = repo.get_strategy_state(current.strategy_id)

    assert replay.event_id == event.event_id
    assert event.from_state is StrategyState.SHADOW
    assert event.to_state is StrategyState.ADMITTED
    assert event.effective_weight == pytest.approx(0.20)
    assert event.evidence == evidence
    assert event.decision["allowed"] is True
    assert state is not None
    assert state.state is StrategyState.ADMITTED
    assert state.revision == 1
    assert state.effective_weight == pytest.approx(0.20)
    assert len(repo.list_strategy_state_events(current.strategy_id)) == 1

    with pytest.raises(ValueError, match="idempotency_key"):
        repo.record_governance_decision(
            current,
            decision,
            {"sample_count": 49},
            "admission-window-2026-07-17",
        )
    with pytest.raises(ValueError, match="stale governance decision"):
        repo.record_governance_decision(
            current,
            decision,
            evidence,
            "admission-window-2026-07-18",
        )

    conflicting_policy = current.model_copy(update={"base_weight": 0.25})
    throttle = decide_state_transition(StrategyState.ADMITTED, StrategyState.THROTTLED)
    with pytest.raises(ValueError, match="immutable"):
        repo.record_governance_decision(
            conflicting_policy,
            throttle,
            {"breach": "soft"},
            "soft-breach-2026-07-17",
        )

    unchanged = repo.get_strategy_state(current.strategy_id)
    assert unchanged is not None
    assert unchanged.state is StrategyState.ADMITTED
    assert unchanged.revision == 1
    assert len(repo.list_strategy_state_events(current.strategy_id)) == 1


def test_concurrent_retries_commit_one_governance_event(tmp_path):
    repo = _make_repo(tmp_path)
    previous, current = _policies()
    repo.initialize_strategy_governance_defaults(policies=[previous, current])
    decision = decide_state_transition(StrategyState.SHADOW, StrategyState.ADMITTED)

    def record_once(_index: int) -> str:
        return repo.record_governance_decision(
            current,
            decision,
            {"window": "2026-07"},
            "concurrent-admission",
        ).event_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        event_ids = list(executor.map(record_once, range(8)))

    state = repo.get_strategy_state(current.strategy_id)
    assert len(set(event_ids)) == 1
    assert state is not None
    assert state.state is StrategyState.ADMITTED
    assert state.revision == 1
    assert len(repo.list_strategy_state_events(current.strategy_id)) == 1


def test_rollback_switches_to_previous_snapshot_and_is_idempotent(tmp_path):
    repo = _make_repo(tmp_path)
    previous, current = _policies()
    repo.initialize_strategy_governance_defaults(policies=[previous, current])
    admission = decide_state_transition(StrategyState.SHADOW, StrategyState.ADMITTED)
    repo.record_governance_decision(
        current,
        admission,
        {"gate": "pass"},
        "admit-before-rollback",
    )
    current_state = repo.get_strategy_state(current.strategy_id)
    assert current_state is not None
    failed_deployment_id = current_state.current_deployment_id

    event = repo.rollback_deployment(
        current.strategy_id,
        "hard-breach-rollback",
        "Hard breach disabled the current policy snapshot.",
        evidence={"severity": "hard"},
    )
    replay = repo.rollback_deployment(
        current.strategy_id,
        "hard-breach-rollback",
        "Hard breach disabled the current policy snapshot.",
        evidence={"severity": "hard"},
    )
    state = repo.get_strategy_state(current.strategy_id)

    assert replay.event_id == event.event_id
    assert event.policy_version == previous.policy_version
    assert event.to_state is StrategyState.RESEARCH
    assert event.previous_deployment_id == failed_deployment_id
    assert state is not None
    assert state.current_policy_version == previous.policy_version
    assert state.previous_policy_version == current.policy_version
    assert state.previous_deployment_id == failed_deployment_id
    assert state.state is StrategyState.RESEARCH
    assert state.effective_weight == 0.0
    assert state.revision == 2
    assert [item.sequence for item in repo.list_strategy_state_events(current.strategy_id)] == [
        1,
        2,
    ]

    with pytest.raises(LookupError, match="no previous deployment"):
        repo.rollback_deployment(
            current.strategy_id,
            "second-rollback",
            "There is no earlier policy snapshot.",
        )
    assert len(repo.list_strategy_state_events(current.strategy_id)) == 2
