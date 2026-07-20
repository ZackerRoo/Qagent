from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class AShareMarketState(StrEnum):
    UNKNOWN = "unknown"
    STRESS = "stress"
    WEAK = "weak"
    MIXED = "mixed"
    CONSTRUCTIVE = "constructive"
    STRONG = "strong"


class AShareTransitionReason(StrEnum):
    INITIALIZED = "initialized"
    STATE_CONFIRMED = "state_confirmed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    MINIMUM_DWELL = "minimum_dwell"
    CONFIRMED_TRANSITION = "confirmed_transition"
    STRESS_IMMEDIATE = "stress_immediate"


class AShareStatePolicy(BaseModel):
    """Persistence-independent transition policy measured in daily observations."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    confirmation_days: int = Field(default=2, ge=1)
    min_dwell_days: int = Field(
        default=3,
        ge=1,
        validation_alias=AliasChoices("min_dwell_days", "minimum_dwell_days"),
    )


class AShareStateObservation(BaseModel):
    """One pre-classified daily market-state observation."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    as_of: date
    state: AShareMarketState = Field(
        validation_alias=AliasChoices("state", "candidate_state", "observed_state")
    )
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    missing_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    reason: str = "market_state_observation"

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be empty")
        return normalized


class AShareStateSnapshot(BaseModel):
    """Flat state-machine record suitable for JSON or row persistence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: date
    state: AShareMarketState
    observed_state: AShareMarketState
    previous_state: AShareMarketState
    state_since: date
    dwell_days: int = Field(ge=1)
    pending_state: AShareMarketState | None = None
    pending_days: int = Field(default=0, ge=0)
    transitioned: bool
    transition_reason: AShareTransitionReason
    transition_detail: str
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    missing_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    observation_reason: str

    @model_validator(mode="after")
    def validate_state_record(self) -> Self:
        if self.state_since > self.as_of:
            raise ValueError("state_since must not be after as_of")
        if self.pending_state is None and self.pending_days != 0:
            raise ValueError("pending_days must be zero without pending_state")
        if self.pending_state is not None:
            if self.pending_days == 0:
                raise ValueError("pending_days must be positive with pending_state")
            if self.pending_state is self.state:
                raise ValueError("pending_state must differ from effective state")
        return self


def initial_a_share_state(
    as_of: date,
    *,
    confidence: float = 0.0,
    missing_rate: float = 1.0,
    reason: str = "no_prior_market_state",
) -> AShareStateSnapshot:
    """Create an explicit unknown baseline before applying daily observations."""

    return advance_a_share_state(
        None,
        AShareStateObservation(
            as_of=as_of,
            state=AShareMarketState.UNKNOWN,
            confidence=confidence,
            missing_rate=missing_rate,
            reason=reason,
        ),
    )


def advance_a_share_state(
    previous: AShareStateSnapshot | Mapping[str, object] | None,
    observation: AShareStateObservation | Mapping[str, object],
    *,
    policy: AShareStatePolicy | Mapping[str, object] | None = None,
) -> AShareStateSnapshot:
    """Advance the state machine by one daily observation without side effects."""

    current_observation = _observation(observation)
    current_policy = _policy(policy)
    if previous is None:
        return _first_snapshot(current_observation, current_policy)

    prior = _snapshot(previous)
    if current_observation.as_of <= prior.as_of:
        raise ValueError("observation as_of must be later than the previous snapshot")

    if current_observation.state is prior.state:
        return _record(
            prior=prior,
            observation=current_observation,
            state=prior.state,
            state_since=prior.state_since,
            dwell_days=prior.dwell_days + 1,
            reason=AShareTransitionReason.STATE_CONFIRMED,
            detail=f"effective state {prior.state.value} confirmed by the daily observation",
        )

    if current_observation.state is AShareMarketState.STRESS:
        return _record(
            prior=prior,
            observation=current_observation,
            state=AShareMarketState.STRESS,
            state_since=current_observation.as_of,
            dwell_days=1,
            transitioned=True,
            reason=AShareTransitionReason.STRESS_IMMEDIATE,
            detail=f"stress entered immediately from {prior.state.value}",
        )

    pending_days = (
        prior.pending_days + 1
        if prior.pending_state is current_observation.state
        else 1
    )
    next_dwell_days = prior.dwell_days + 1
    if pending_days < current_policy.confirmation_days:
        return _record(
            prior=prior,
            observation=current_observation,
            state=prior.state,
            state_since=prior.state_since,
            dwell_days=next_dwell_days,
            pending_state=current_observation.state,
            pending_days=pending_days,
            reason=AShareTransitionReason.AWAITING_CONFIRMATION,
            detail=(
                f"candidate {current_observation.state.value} observed "
                f"{pending_days}/{current_policy.confirmation_days} days"
            ),
        )

    # Unknown is a data-availability state, so it does not impose an artificial dwell lock.
    if (
        prior.state is not AShareMarketState.UNKNOWN
        and next_dwell_days < current_policy.min_dwell_days
    ):
        return _record(
            prior=prior,
            observation=current_observation,
            state=prior.state,
            state_since=prior.state_since,
            dwell_days=next_dwell_days,
            pending_state=current_observation.state,
            pending_days=pending_days,
            reason=AShareTransitionReason.MINIMUM_DWELL,
            detail=(
                f"effective state {prior.state.value} has dwelled "
                f"{next_dwell_days}/{current_policy.min_dwell_days} days"
            ),
        )

    return _record(
        prior=prior,
        observation=current_observation,
        state=current_observation.state,
        state_since=current_observation.as_of,
        dwell_days=1,
        transitioned=True,
        reason=AShareTransitionReason.CONFIRMED_TRANSITION,
        detail=(
            f"candidate {current_observation.state.value} confirmed for "
            f"{pending_days} consecutive days"
        ),
    )


def apply_a_share_state_observations(
    observations: Iterable[AShareStateObservation | Mapping[str, object]],
    *,
    initial: AShareStateSnapshot | Mapping[str, object] | None = None,
    policy: AShareStatePolicy | Mapping[str, object] | None = None,
) -> list[AShareStateSnapshot]:
    """Apply an ordered observation sequence and return every persisted record."""

    records: list[AShareStateSnapshot] = []
    current = _snapshot(initial) if initial is not None else None
    for observation in observations:
        current = advance_a_share_state(current, observation, policy=policy)
        records.append(current)
    return records


def _first_snapshot(
    observation: AShareStateObservation,
    policy: AShareStatePolicy,
) -> AShareStateSnapshot:
    state = AShareMarketState.UNKNOWN
    pending_state: AShareMarketState | None = None
    pending_days = 0
    transitioned = False
    reason = AShareTransitionReason.INITIALIZED
    detail = "market state initialized as unknown"

    if observation.state is AShareMarketState.STRESS:
        state = observation.state
        transitioned = True
        reason = AShareTransitionReason.STRESS_IMMEDIATE
        detail = "stress entered immediately without a prior persisted state"
    elif observation.state is not AShareMarketState.UNKNOWN:
        if policy.confirmation_days == 1:
            state = observation.state
            transitioned = True
            reason = AShareTransitionReason.CONFIRMED_TRANSITION
            detail = f"candidate {state.value} satisfied one-day confirmation policy"
        else:
            pending_state = observation.state
            pending_days = 1
            reason = AShareTransitionReason.AWAITING_CONFIRMATION
            detail = (
                f"candidate {observation.state.value} observed "
                f"1/{policy.confirmation_days} days"
            )

    return AShareStateSnapshot(
        as_of=observation.as_of,
        state=state,
        observed_state=observation.state,
        previous_state=AShareMarketState.UNKNOWN,
        state_since=observation.as_of,
        dwell_days=1,
        pending_state=pending_state,
        pending_days=pending_days,
        transitioned=transitioned,
        transition_reason=reason,
        transition_detail=detail,
        confidence=observation.confidence,
        missing_rate=observation.missing_rate,
        observation_reason=observation.reason,
    )


def _record(
    *,
    prior: AShareStateSnapshot,
    observation: AShareStateObservation,
    state: AShareMarketState,
    state_since: date,
    dwell_days: int,
    reason: AShareTransitionReason,
    detail: str,
    pending_state: AShareMarketState | None = None,
    pending_days: int = 0,
    transitioned: bool = False,
) -> AShareStateSnapshot:
    return AShareStateSnapshot(
        as_of=observation.as_of,
        state=state,
        observed_state=observation.state,
        previous_state=prior.state,
        state_since=state_since,
        dwell_days=dwell_days,
        pending_state=pending_state,
        pending_days=pending_days,
        transitioned=transitioned,
        transition_reason=reason,
        transition_detail=detail,
        confidence=observation.confidence,
        missing_rate=observation.missing_rate,
        observation_reason=observation.reason,
    )


def _policy(
    policy: AShareStatePolicy | Mapping[str, object] | None,
) -> AShareStatePolicy:
    if policy is None:
        return AShareStatePolicy()
    return policy if isinstance(policy, AShareStatePolicy) else AShareStatePolicy.model_validate(policy)


def _observation(
    observation: AShareStateObservation | Mapping[str, object],
) -> AShareStateObservation:
    if isinstance(observation, AShareStateObservation):
        return observation
    return AShareStateObservation.model_validate(observation)


def _snapshot(
    snapshot: AShareStateSnapshot | Mapping[str, object],
) -> AShareStateSnapshot:
    if isinstance(snapshot, AShareStateSnapshot):
        return snapshot
    return AShareStateSnapshot.model_validate(snapshot)


# Concise aliases for callers that already operate inside the A-share market package.
MarketState = AShareMarketState
AShareState = AShareMarketState
MarketStatePolicy = AShareStatePolicy
MarketStateObservation = AShareStateObservation
MarketStateSignal = AShareStateObservation
MarketStateSnapshot = AShareStateSnapshot
MarketStateTransition = AShareStateSnapshot
transition_a_share_state = advance_a_share_state
transition_market_state = advance_a_share_state
update_a_share_state = advance_a_share_state
update_market_state = advance_a_share_state


__all__ = [
    "AShareMarketState",
    "AShareState",
    "AShareStateObservation",
    "AShareStatePolicy",
    "AShareStateSnapshot",
    "AShareTransitionReason",
    "MarketState",
    "MarketStateObservation",
    "MarketStatePolicy",
    "MarketStateSignal",
    "MarketStateSnapshot",
    "MarketStateTransition",
    "advance_a_share_state",
    "apply_a_share_state_observations",
    "initial_a_share_state",
    "transition_a_share_state",
    "transition_market_state",
    "update_a_share_state",
    "update_market_state",
]
