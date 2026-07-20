from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from enum import StrEnum
from math import isfinite

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from qagent.recommendations.models import (
    PortfolioConstraintPolicyAudit,
    PortfolioConstraintResult,
)


class PortfolioConstraintCode(StrEnum):
    ACTION_NOT_ADMITTED = "action_not_admitted"
    OBSERVATION_ONLY = "observation_weight_zero"
    DUPLICATE_INSTRUMENT = "duplicate_instrument"
    INVALID_INSTRUMENT = "invalid_instrument"
    INVALID_TARGET_WEIGHT = "invalid_target_weight"
    ZERO_TARGET_WEIGHT = "zero_target_weight"
    INVALID_RISK_BUDGET = "invalid_risk_budget"
    ZERO_RISK_BUDGET = "zero_risk_budget"
    RISK_BLOCKED = "risk_blocked"
    TRADABILITY_BLOCKED = "tradability_blocked"
    TRADING_STATUS_BLOCKED = "trading_status_blocked"
    PRE_TRADE_RISK_BLOCKED = "pre_trade_risk_blocked"
    DATA_QUALITY_BLOCKED = "data_quality_blocked"
    MAX_POSITIONS = "max_positions"
    SINGLE_POSITION_CAP = "single_position_cap"
    TOTAL_RISK_BUDGET = "total_risk_budget"
    CASH_RESERVE = "cash_reserve"
    INDUSTRY_POSITION_CAP = "industry_position_cap"
    INDUSTRY_WEIGHT_CAP = "industry_weight_cap"
    THEME_WEIGHT_CAP = "theme_weight_cap"
    SAME_THEME_CONCENTRATION = "same_theme_concentration"
    ETF_OVERLAP = "etf_overlap"
    ETF_OVERLAP_WEIGHT_CAP = "etf_overlap_weight_cap"
    MARKET_STATE_MULTIPLIER = "market_state_multiplier"
    UNKNOWN_MARKET_STATE = "unknown_market_state"
    MARKET_STATE_BLOCKED = "market_state_blocked"
    BELOW_MINIMUM_ALLOCATION = "below_minimum_allocation"


DEFAULT_MARKET_STATE_MULTIPLIERS = {
    "risk_on": 1.10,
    "constructive": 1.00,
    "balanced": 1.00,
    "normal": 1.00,
    "neutral": 1.00,
    "mixed": 0.75,
    "risk_off": 0.50,
    "thin": 0.50,
}


class PortfolioCandidate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    candidate_id: str | None = None
    instrument_id: str = Field(validation_alias=AliasChoices("instrument_id", "symbol"))
    action: str
    requested_weight: float = Field(
        default=0.0,
        validation_alias=AliasChoices(
            "requested_weight",
            "weight",
            "target_weight",
            "weight_pct",
            "proposed_weight",
            "proposed_weight_pct",
        ),
    )
    requested_risk_budget: float = Field(
        default=0.0,
        validation_alias=AliasChoices(
            "requested_risk_budget",
            "risk",
            "risk_budget",
            "risk_budget_pct",
            "proposed_risk_budget_pct",
        ),
    )
    max_position_pct: float | None = None
    industry: str | None = Field(
        default=None,
        validation_alias=AliasChoices("industry", "sector"),
    )
    themes: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("themes", "theme"),
    )
    asset_type: str = "stock"
    etf_overlap_keys: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices(
            "etf_overlap_keys",
            "overlap_keys",
            "overlap_groups",
            "etf_overlap_group",
        ),
    )
    hard_constraint_codes: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("hard_constraint_codes", "pre_constraint_codes"),
    )
    priority: float = Field(
        default=0.0,
        validation_alias=AliasChoices("priority", "rank_score", "score"),
    )
    secondary_priority: float = 0.0

    @field_validator("themes", "etf_overlap_keys", "hard_constraint_codes", mode="before")
    @classmethod
    def _coerce_tuple(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return value


class PortfolioConstraintConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    max_positions: int = Field(default=3, ge=0)
    max_single_position_pct: float = Field(
        default=12.0,
        ge=0.0,
        le=100.0,
        validation_alias=AliasChoices(
            "max_single_position_pct",
            "single_position_limit_pct",
            "single_name_limit_pct",
        ),
    )
    total_risk_budget_pct: float = Field(
        default=3.0,
        ge=0.0,
        le=100.0,
        validation_alias=AliasChoices("total_risk_budget_pct", "total_risk_budget"),
    )
    min_cash_reserve_pct: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        validation_alias=AliasChoices("min_cash_reserve_pct", "cash_reserve_pct"),
    )
    max_industry_positions: int | None = Field(default=2, ge=0)
    max_industry_weight_pct: float | None = Field(
        default=24.0,
        ge=0.0,
        le=100.0,
        validation_alias=AliasChoices("max_industry_weight_pct", "industry_limit_pct"),
    )
    max_same_theme_positions: int | None = Field(
        default=2,
        ge=0,
        validation_alias=AliasChoices("max_same_theme_positions", "max_theme_positions"),
    )
    max_theme_weight_pct: float | None = Field(
        default=24.0,
        ge=0.0,
        le=100.0,
        validation_alias=AliasChoices("max_theme_weight_pct", "theme_limit_pct"),
    )
    max_etf_overlap_positions: int | None = Field(
        default=1,
        ge=0,
        validation_alias=AliasChoices("max_etf_overlap_positions", "max_overlapping_etfs"),
    )
    max_etf_overlap_weight_pct: float | None = Field(
        default=12.0,
        ge=0.0,
        le=100.0,
        validation_alias=AliasChoices(
            "max_etf_overlap_weight_pct",
            "etf_overlap_limit_pct",
        ),
    )
    admitted_actions: tuple[str, ...] = ("candidate_entry",)
    observation_actions: tuple[str, ...] = (
        "watch_trigger",
        "wait_pullback",
        "watch",
        "observe",
    )
    market_state_multipliers: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_MARKET_STATE_MULTIPLIERS)
    )
    default_market_state_multiplier: float = Field(default=0.50, ge=0.0)
    minimum_allocation_pct: float = Field(default=0.01, ge=0.0)
    minimum_risk_budget_pct: float = Field(default=0.01, ge=0.0)

    @field_validator("admitted_actions", "observation_actions")
    @classmethod
    def _normalize_actions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(_normalize_token(value) for value in values if str(value).strip())
        )

    @field_validator("market_state_multipliers")
    @classmethod
    def _normalize_market_multipliers(cls, value: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for key, multiplier in value.items():
            parsed = float(multiplier)
            if not isfinite(parsed) or parsed < 0:
                raise ValueError("market-state multipliers must be finite and non-negative")
            normalized[_normalize_token(key)] = parsed
        return normalized


class PortfolioConstraintEngine:
    """Pure, deterministic sequential portfolio constraint evaluator."""

    def __init__(
        self,
        config: PortfolioConstraintConfig | None = None,
        **config_overrides: object,
    ) -> None:
        payload = config.model_dump() if config is not None else {}
        payload.update(config_overrides)
        self.config = PortfolioConstraintConfig.model_validate(payload)

    def evaluate(
        self,
        candidates: Sequence[PortfolioCandidate | Mapping[str, object]],
        *,
        market_state: str = "neutral",
        market_state_multiplier: float | None = None,
    ) -> list[PortfolioConstraintResult]:
        items = [
            item
            if isinstance(item, PortfolioCandidate)
            else PortfolioCandidate.model_validate(item)
            for item in candidates
        ]
        state, multiplier, known_state = self._market_sizing(
            market_state,
            market_state_multiplier,
        )
        downscale = min(multiplier, Decimal("1"))
        total_risk_limit = _decimal(self.config.total_risk_budget_pct) * downscale
        invested_weight_limit = (
            Decimal("100") - _decimal(self.config.min_cash_reserve_pct)
        ) * downscale
        industry_weight_limit = _scaled_optional(
            self.config.max_industry_weight_pct,
            downscale,
        )
        theme_weight_limit = _scaled_optional(
            self.config.max_theme_weight_pct,
            downscale,
        )
        etf_overlap_weight_limit = _scaled_optional(
            self.config.max_etf_overlap_weight_pct,
            downscale,
        )

        winners = self._dedupe_winners(items)
        order = sorted(
            range(len(items)), key=lambda index: _allocation_sort_key(items[index], index)
        )
        accepted_count = 0
        allocated_weight = Decimal("0")
        allocated_risk = Decimal("0")
        industry_counts: dict[str, int] = defaultdict(int)
        industry_weights: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        theme_counts: dict[str, int] = defaultdict(int)
        theme_weights: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        etf_overlap_counts: dict[str, int] = defaultdict(int)
        etf_overlap_weights: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        results: list[PortfolioConstraintResult] = []

        for index in order:
            candidate = items[index]
            action = _normalize_token(candidate.action)
            codes = _normalize_codes(candidate.hard_constraint_codes)
            canonical_id = _normalize_instrument(candidate.instrument_id)

            if not canonical_id:
                _append_code(codes, PortfolioConstraintCode.INVALID_INSTRUMENT)
                results.append(self._result(index, candidate, state, multiplier, False, codes))
                continue
            if winners.get(canonical_id) != index:
                _append_code(codes, PortfolioConstraintCode.DUPLICATE_INSTRUMENT)
                results.append(self._result(index, candidate, state, multiplier, False, codes))
                continue
            if action in self.config.observation_actions:
                _append_code(codes, PortfolioConstraintCode.OBSERVATION_ONLY)
                results.append(self._result(index, candidate, state, multiplier, False, codes))
                continue
            if action not in self.config.admitted_actions:
                _append_code(codes, PortfolioConstraintCode.ACTION_NOT_ADMITTED)
                results.append(self._result(index, candidate, state, multiplier, False, codes))
                continue
            if codes:
                results.append(self._result(index, candidate, state, multiplier, False, codes))
                continue

            requested_weight = _finite_decimal(candidate.requested_weight)
            requested_risk = _finite_decimal(candidate.requested_risk_budget)
            if requested_weight is None or requested_weight < 0:
                _append_code(codes, PortfolioConstraintCode.INVALID_TARGET_WEIGHT)
            elif requested_weight == 0:
                _append_code(codes, PortfolioConstraintCode.ZERO_TARGET_WEIGHT)
            if requested_risk is None or requested_risk < 0:
                _append_code(codes, PortfolioConstraintCode.INVALID_RISK_BUDGET)
            elif requested_risk == 0:
                _append_code(codes, PortfolioConstraintCode.ZERO_RISK_BUDGET)
            if codes:
                results.append(self._result(index, candidate, state, multiplier, False, codes))
                continue

            industry = _normalize_group(candidate.industry)
            themes = _normalized_groups(candidate.themes)
            overlap_keys = self._etf_overlap_keys(candidate, themes)
            blocking_codes: list[str] = []
            if accepted_count >= self.config.max_positions:
                _append_code(blocking_codes, PortfolioConstraintCode.MAX_POSITIONS)
            if (
                industry
                and self.config.max_industry_positions is not None
                and industry_counts[industry] >= self.config.max_industry_positions
            ):
                _append_code(blocking_codes, PortfolioConstraintCode.INDUSTRY_POSITION_CAP)
            if self._is_etf(candidate) and self.config.max_etf_overlap_positions is not None:
                if any(
                    etf_overlap_counts[key] >= self.config.max_etf_overlap_positions
                    for key in overlap_keys
                ):
                    _append_code(blocking_codes, PortfolioConstraintCode.ETF_OVERLAP)
            if self.config.max_same_theme_positions is not None:
                if any(
                    theme_counts[theme] >= self.config.max_same_theme_positions for theme in themes
                ):
                    _append_code(
                        blocking_codes,
                        PortfolioConstraintCode.SAME_THEME_CONCENTRATION,
                    )
            if blocking_codes:
                codes.extend(blocking_codes)
                results.append(self._result(index, candidate, state, multiplier, False, codes))
                continue

            weight = requested_weight
            risk = requested_risk
            candidate_cap = _finite_decimal(candidate.max_position_pct)
            if candidate.max_position_pct is not None and (
                candidate_cap is None or candidate_cap < 0
            ):
                _append_code(codes, PortfolioConstraintCode.INVALID_TARGET_WEIGHT)
                results.append(self._result(index, candidate, state, multiplier, False, codes))
                continue
            if candidate_cap is not None:
                weight, risk = _cap_pair(
                    weight,
                    risk,
                    candidate_cap,
                    codes,
                    PortfolioConstraintCode.SINGLE_POSITION_CAP,
                )
            weight, risk = _cap_pair(
                weight,
                risk,
                _decimal(self.config.max_single_position_pct),
                codes,
                PortfolioConstraintCode.SINGLE_POSITION_CAP,
            )

            if multiplier != Decimal("1"):
                weight *= multiplier
                risk *= multiplier
                _append_code(codes, PortfolioConstraintCode.MARKET_STATE_MULTIPLIER)
            if not known_state:
                _append_code(codes, PortfolioConstraintCode.UNKNOWN_MARKET_STATE)
            if multiplier == 0:
                _append_code(codes, PortfolioConstraintCode.MARKET_STATE_BLOCKED)
            if candidate_cap is not None:
                weight, risk = _cap_pair(
                    weight,
                    risk,
                    candidate_cap,
                    codes,
                    PortfolioConstraintCode.SINGLE_POSITION_CAP,
                )
            weight, risk = _cap_pair(
                weight,
                risk,
                _decimal(self.config.max_single_position_pct),
                codes,
                PortfolioConstraintCode.SINGLE_POSITION_CAP,
            )

            weight, risk = _cap_pair(
                weight,
                risk,
                max(Decimal("0"), invested_weight_limit - allocated_weight),
                codes,
                PortfolioConstraintCode.CASH_RESERVE,
            )
            if industry and industry_weight_limit is not None:
                weight, risk = _cap_pair(
                    weight,
                    risk,
                    max(Decimal("0"), industry_weight_limit - industry_weights[industry]),
                    codes,
                    PortfolioConstraintCode.INDUSTRY_WEIGHT_CAP,
                )
            if theme_weight_limit is not None:
                for theme in themes:
                    weight, risk = _cap_pair(
                        weight,
                        risk,
                        max(Decimal("0"), theme_weight_limit - theme_weights[theme]),
                        codes,
                        PortfolioConstraintCode.THEME_WEIGHT_CAP,
                    )
            if self._is_etf(candidate) and etf_overlap_weight_limit is not None:
                for key in overlap_keys:
                    weight, risk = _cap_pair(
                        weight,
                        risk,
                        max(
                            Decimal("0"),
                            etf_overlap_weight_limit - etf_overlap_weights[key],
                        ),
                        codes,
                        PortfolioConstraintCode.ETF_OVERLAP_WEIGHT_CAP,
                    )
            weight, risk = _cap_risk_pair(
                weight,
                risk,
                max(Decimal("0"), total_risk_limit - allocated_risk),
                codes,
                PortfolioConstraintCode.TOTAL_RISK_BUDGET,
            )

            target_weight = _quantize_pct(weight)
            risk_budget = _quantize_pct(risk)
            if target_weight < _decimal(
                self.config.minimum_allocation_pct
            ) or risk_budget < _decimal(self.config.minimum_risk_budget_pct):
                _append_code(codes, PortfolioConstraintCode.BELOW_MINIMUM_ALLOCATION)
                results.append(self._result(index, candidate, state, multiplier, False, codes))
                continue

            accepted_count += 1
            allocated_weight += target_weight
            allocated_risk += risk_budget
            if industry:
                industry_counts[industry] += 1
                industry_weights[industry] += target_weight
            for theme in themes:
                theme_counts[theme] += 1
                theme_weights[theme] += target_weight
            if self._is_etf(candidate):
                for key in overlap_keys:
                    etf_overlap_counts[key] += 1
                    etf_overlap_weights[key] += target_weight
            results.append(
                self._result(
                    index,
                    candidate,
                    state,
                    multiplier,
                    True,
                    codes,
                    target_weight,
                    risk_budget,
                )
            )

        return results

    def apply(
        self,
        candidates: Sequence[PortfolioCandidate | Mapping[str, object]],
        *,
        market_state: str = "neutral",
        market_state_multiplier: float | None = None,
    ) -> list[PortfolioConstraintResult]:
        return self.evaluate(
            candidates,
            market_state=market_state,
            market_state_multiplier=market_state_multiplier,
        )

    def run(
        self,
        candidates: Sequence[PortfolioCandidate | Mapping[str, object]],
        *,
        market_state: str = "neutral",
        market_state_multiplier: float | None = None,
    ) -> list[PortfolioConstraintResult]:
        return self.evaluate(
            candidates,
            market_state=market_state,
            market_state_multiplier=market_state_multiplier,
        )

    def policy_audit(
        self,
        *,
        market_state: str = "neutral",
        market_state_multiplier: float | None = None,
    ) -> PortfolioConstraintPolicyAudit:
        state, multiplier, _ = self._market_sizing(market_state, market_state_multiplier)
        downscale = min(multiplier, Decimal("1"))
        return PortfolioConstraintPolicyAudit(
            admitted_actions=list(self.config.admitted_actions),
            observation_actions=list(self.config.observation_actions),
            max_positions=self.config.max_positions,
            max_single_position_pct=self.config.max_single_position_pct,
            total_risk_budget_pct=self.config.total_risk_budget_pct,
            effective_risk_budget_pct=float(
                _quantize_pct(_decimal(self.config.total_risk_budget_pct) * downscale)
            ),
            min_cash_reserve_pct=self.config.min_cash_reserve_pct,
            max_invested_weight_pct=float(
                _quantize_pct(
                    (Decimal("100") - _decimal(self.config.min_cash_reserve_pct)) * downscale
                )
            ),
            max_industry_positions=self.config.max_industry_positions,
            max_industry_weight_pct=_scaled_float(
                self.config.max_industry_weight_pct,
                downscale,
            ),
            max_same_theme_positions=self.config.max_same_theme_positions,
            max_theme_weight_pct=_scaled_float(self.config.max_theme_weight_pct, downscale),
            max_etf_overlap_positions=self.config.max_etf_overlap_positions,
            max_etf_overlap_weight_pct=_scaled_float(
                self.config.max_etf_overlap_weight_pct,
                downscale,
            ),
            market_state=state,
            market_state_multiplier=float(multiplier),
        )

    def _dedupe_winners(self, candidates: list[PortfolioCandidate]) -> dict[str, int]:
        grouped: dict[str, list[int]] = defaultdict(list)
        for index, candidate in enumerate(candidates):
            canonical_id = _normalize_instrument(candidate.instrument_id)
            if canonical_id:
                grouped[canonical_id].append(index)
        return {
            instrument_id: min(
                indices,
                key=lambda index: self._dedupe_sort_key(candidates[index], index),
            )
            for instrument_id, indices in grouped.items()
        }

    def _dedupe_sort_key(self, candidate: PortfolioCandidate, index: int) -> tuple[object, ...]:
        action = _normalize_token(candidate.action)
        if action in self.config.admitted_actions and not candidate.hard_constraint_codes:
            admission_rank = 0
        elif action in self.config.observation_actions:
            admission_rank = 1
        else:
            admission_rank = 2
        return (
            admission_rank,
            -_sort_number(candidate.priority),
            -_sort_number(candidate.secondary_priority),
            -_sort_number(candidate.requested_weight),
            -_sort_number(candidate.requested_risk_budget),
            _normalize_token(candidate.candidate_id or ""),
            index,
        )

    def _market_sizing(
        self,
        market_state: str,
        explicit_multiplier: float | None,
    ) -> tuple[str, Decimal, bool]:
        state = _normalize_token(market_state) or "neutral"
        known_state = state in self.config.market_state_multipliers
        raw_multiplier = (
            explicit_multiplier
            if explicit_multiplier is not None
            else self.config.market_state_multipliers.get(
                state,
                self.config.default_market_state_multiplier,
            )
        )
        multiplier = _finite_decimal(raw_multiplier)
        if multiplier is None or multiplier < 0:
            raise ValueError("market_state_multiplier must be finite and non-negative")
        return state, multiplier, known_state

    @staticmethod
    def _is_etf(candidate: PortfolioCandidate) -> bool:
        return _normalize_token(candidate.asset_type) == "etf"

    def _etf_overlap_keys(
        self,
        candidate: PortfolioCandidate,
        themes: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not self._is_etf(candidate):
            return ()
        explicit = _normalized_groups(candidate.etf_overlap_keys)
        return explicit or themes

    @staticmethod
    def _result(
        index: int,
        candidate: PortfolioCandidate,
        market_state: str,
        multiplier: Decimal,
        accepted: bool,
        codes: list[str],
        target_weight: Decimal = Decimal("0"),
        risk_budget: Decimal = Decimal("0"),
    ) -> PortfolioConstraintResult:
        return PortfolioConstraintResult(
            candidate_index=index,
            candidate_id=candidate.candidate_id,
            instrument_id=candidate.instrument_id,
            action=_normalize_token(candidate.action),
            accepted=accepted,
            target_weight=float(target_weight),
            risk_budget=float(risk_budget),
            constraint_codes=list(dict.fromkeys(codes)),
            requested_weight=_safe_float(candidate.requested_weight),
            requested_risk_budget=_safe_float(candidate.requested_risk_budget),
            industry=candidate.industry,
            themes=list(
                dict.fromkeys(
                    str(theme).strip() for theme in candidate.themes if str(theme).strip()
                )
            ),
            asset_type=candidate.asset_type,
            market_state=market_state,
            market_state_multiplier=float(multiplier),
        )


def _allocation_sort_key(candidate: PortfolioCandidate, index: int) -> tuple[object, ...]:
    return (
        -_sort_number(candidate.priority),
        -_sort_number(candidate.secondary_priority),
        _normalize_instrument(candidate.instrument_id),
        index,
    )


def _normalize_token(value: object) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_instrument(value: object) -> str:
    return str(value).strip().upper()


def _normalize_group(value: object) -> str:
    return str(value).strip().casefold() if value is not None else ""


def _normalized_groups(values: Sequence[object]) -> tuple[str, ...]:
    return tuple(
        sorted({normalized for value in values if (normalized := _normalize_group(value))})
    )


def _normalize_codes(values: Sequence[object]) -> list[str]:
    return list(dict.fromkeys(_normalize_token(value) for value in values if str(value).strip()))


def _append_code(codes: list[str], code: PortfolioConstraintCode | str) -> None:
    value = code.value if isinstance(code, PortfolioConstraintCode) else str(code)
    if value not in codes:
        codes.append(value)


def _decimal(value: float | int | Decimal) -> Decimal:
    return Decimal(str(value))


def _finite_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _sort_number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if isfinite(parsed) else 0.0


def _safe_float(value: object) -> float:
    parsed = _sort_number(value)
    return parsed


def _quantize_pct(value: Decimal) -> Decimal:
    return max(Decimal("0"), value).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _cap_pair(
    weight: Decimal,
    risk: Decimal,
    weight_cap: Decimal,
    codes: list[str],
    code: PortfolioConstraintCode,
) -> tuple[Decimal, Decimal]:
    cap = max(Decimal("0"), weight_cap)
    if weight <= cap:
        return weight, risk
    if weight <= 0:
        return Decimal("0"), Decimal("0")
    ratio = cap / weight
    _append_code(codes, code)
    return cap, risk * ratio


def _cap_risk_pair(
    weight: Decimal,
    risk: Decimal,
    risk_cap: Decimal,
    codes: list[str],
    code: PortfolioConstraintCode,
) -> tuple[Decimal, Decimal]:
    cap = max(Decimal("0"), risk_cap)
    if risk <= cap:
        return weight, risk
    if risk <= 0:
        return Decimal("0"), Decimal("0")
    ratio = cap / risk
    _append_code(codes, code)
    return weight * ratio, cap


def _scaled_optional(value: float | None, multiplier: Decimal) -> Decimal | None:
    return None if value is None else _decimal(value) * multiplier


def _scaled_float(value: float | None, multiplier: Decimal) -> float | None:
    scaled = _scaled_optional(value, multiplier)
    return None if scaled is None else float(_quantize_pct(scaled))
