from datetime import date, timedelta

import pytest

from qagent.market.a_share_state import (
    AShareMarketState,
    AShareStateObservation,
    AShareStatePolicy,
    AShareStateSnapshot,
    AShareTransitionReason,
    advance_a_share_state,
    apply_a_share_state_observations,
    initial_a_share_state,
)


def _observation(
    day: date,
    state: AShareMarketState | str,
    *,
    confidence: float = 0.8,
    missing_rate: float = 0.1,
    reason: str = "daily breadth and trend composite",
) -> AShareStateObservation:
    return AShareStateObservation(
        as_of=day,
        state=state,
        confidence=confidence,
        missing_rate=missing_rate,
        reason=reason,
    )


def test_states_are_the_persisted_public_vocabulary():
    assert {state.value for state in AShareMarketState} == {
        "unknown",
        "stress",
        "weak",
        "mixed",
        "constructive",
        "strong",
    }


def test_non_stress_transition_requires_two_consecutive_daily_observations():
    start = date(2026, 7, 13)
    initial = initial_a_share_state(start)

    pending = advance_a_share_state(
        initial,
        _observation(
            start + timedelta(days=1),
            "constructive",
            confidence=0.72,
            missing_rate=0.08,
            reason="breadth improved",
        ),
    )

    assert pending.state is AShareMarketState.UNKNOWN
    assert pending.observed_state is AShareMarketState.CONSTRUCTIVE
    assert pending.pending_state is AShareMarketState.CONSTRUCTIVE
    assert pending.pending_days == 1
    assert pending.transition_reason is AShareTransitionReason.AWAITING_CONFIRMATION
    assert pending.confidence == 0.72
    assert pending.missing_rate == 0.08
    assert pending.observation_reason == "breadth improved"

    persisted = pending.model_dump(mode="json")
    restored = AShareStateSnapshot.model_validate(persisted)
    confirmed = advance_a_share_state(
        restored,
        _observation(
            start + timedelta(days=2),
            "constructive",
            confidence=0.81,
            missing_rate=0.05,
        ),
    )

    assert confirmed.state is AShareMarketState.CONSTRUCTIVE
    assert confirmed.previous_state is AShareMarketState.UNKNOWN
    assert confirmed.pending_state is None
    assert confirmed.pending_days == 0
    assert confirmed.dwell_days == 1
    assert confirmed.state_since == start + timedelta(days=2)
    assert confirmed.transitioned is True
    assert confirmed.transition_reason is AShareTransitionReason.CONFIRMED_TRANSITION
    assert confirmed.confidence == 0.81
    assert confirmed.missing_rate == 0.05


def test_stress_enters_immediately_even_during_confirmation_and_dwell_lock():
    start = date(2026, 7, 13)
    policy = AShareStatePolicy(confirmation_days=2, min_dwell_days=5)
    records = apply_a_share_state_observations(
        [
            _observation(start, "strong"),
            _observation(start + timedelta(days=1), "strong"),
            _observation(start + timedelta(days=2), "weak"),
            _observation(
                start + timedelta(days=3),
                "stress",
                confidence=0.95,
                missing_rate=0.02,
                reason="limit-down breadth breached emergency threshold",
            ),
        ],
        policy=policy,
    )

    assert records[1].state is AShareMarketState.STRONG
    assert records[2].state is AShareMarketState.STRONG
    assert records[2].pending_state is AShareMarketState.WEAK
    stress = records[3]
    assert stress.state is AShareMarketState.STRESS
    assert stress.transitioned is True
    assert stress.transition_reason is AShareTransitionReason.STRESS_IMMEDIATE
    assert stress.pending_state is None
    assert stress.dwell_days == 1
    assert stress.confidence == 0.95
    assert stress.missing_rate == 0.02


def test_minimum_dwell_holds_a_confirmed_candidate_until_state_is_old_enough():
    start = date(2026, 7, 13)
    policy = AShareStatePolicy(confirmation_days=2, min_dwell_days=4)
    records = apply_a_share_state_observations(
        [
            _observation(start, "strong"),
            _observation(start + timedelta(days=1), "strong"),
            _observation(start + timedelta(days=2), "weak"),
            _observation(start + timedelta(days=3), "weak"),
            _observation(start + timedelta(days=4), "weak"),
        ],
        policy=policy,
    )

    held = records[3]
    assert held.state is AShareMarketState.STRONG
    assert held.pending_state is AShareMarketState.WEAK
    assert held.pending_days == 2
    assert held.dwell_days == 3
    assert held.transition_reason is AShareTransitionReason.MINIMUM_DWELL

    transitioned = records[4]
    assert transitioned.state is AShareMarketState.WEAK
    assert transitioned.transitioned is True
    assert transitioned.transition_reason is AShareTransitionReason.CONFIRMED_TRANSITION
    assert transitioned.pending_state is None


def test_interrupted_candidate_must_restart_confirmation_count():
    start = date(2026, 7, 13)
    records = apply_a_share_state_observations(
        [
            _observation(start, "mixed"),
            _observation(start + timedelta(days=1), "strong"),
            _observation(start + timedelta(days=2), "constructive"),
            _observation(start + timedelta(days=3), "constructive"),
        ],
        policy=AShareStatePolicy(min_dwell_days=1),
    )

    assert records[1].pending_state is AShareMarketState.STRONG
    assert records[1].pending_days == 1
    assert records[2].pending_state is AShareMarketState.CONSTRUCTIVE
    assert records[2].pending_days == 1
    assert records[3].state is AShareMarketState.CONSTRUCTIVE


def test_out_of_order_or_duplicate_observation_is_rejected():
    current = initial_a_share_state(date(2026, 7, 13))

    with pytest.raises(ValueError, match="later than"):
        advance_a_share_state(current, _observation(date(2026, 7, 13), "mixed"))


def test_stress_can_be_the_first_persisted_observation():
    result = advance_a_share_state(
        None,
        _observation(date(2026, 7, 13), "stress"),
    )

    assert result.state is AShareMarketState.STRESS
    assert result.transition_reason is AShareTransitionReason.STRESS_IMMEDIATE
    assert result.transitioned is True
