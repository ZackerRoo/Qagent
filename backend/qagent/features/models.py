from collections.abc import Mapping
from datetime import date, datetime
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator


class FeatureSnapshot(BaseModel):
    """Immutable point-in-time inputs and cross-sectional feature scores."""

    model_config = ConfigDict(frozen=True)

    as_of: date | datetime
    feature_set_version: str
    dataset_revision: int | str
    universe_digest: str
    input_digest: str
    coverage: Mapping[str, float]
    raw_scores: Mapping[str, Mapping[str, float | None]]
    cross_sectional_scores: Mapping[str, Mapping[str, float]]

    @field_validator("coverage", mode="after")
    @classmethod
    def _freeze_coverage(cls, value: Mapping[str, float]) -> Mapping[str, float]:
        return MappingProxyType(dict(value))

    @field_validator("raw_scores", mode="after")
    @classmethod
    def _freeze_raw_scores(
        cls,
        value: Mapping[str, Mapping[str, float | None]],
    ) -> Mapping[str, Mapping[str, float | None]]:
        return MappingProxyType(
            {instrument_id: MappingProxyType(dict(scores)) for instrument_id, scores in value.items()}
        )

    @field_validator("cross_sectional_scores", mode="after")
    @classmethod
    def _freeze_cross_sectional_scores(
        cls,
        value: Mapping[str, Mapping[str, float]],
    ) -> Mapping[str, Mapping[str, float]]:
        return MappingProxyType(
            {instrument_id: MappingProxyType(dict(scores)) for instrument_id, scores in value.items()}
        )

    @field_serializer("coverage")
    def _serialize_coverage(self, value: Mapping[str, float]) -> dict[str, float]:
        return dict(value)

    @field_serializer("raw_scores")
    def _serialize_raw_scores(
        self,
        value: Mapping[str, Mapping[str, float | None]],
    ) -> dict[str, dict[str, float | None]]:
        return {instrument_id: dict(scores) for instrument_id, scores in value.items()}

    @field_serializer("cross_sectional_scores")
    def _serialize_cross_sectional_scores(
        self,
        value: Mapping[str, Mapping[str, float]],
    ) -> dict[str, dict[str, float]]:
        return {instrument_id: dict(scores) for instrument_id, scores in value.items()}
