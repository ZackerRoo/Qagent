from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
import hashlib
import json
import math
import re
from time import monotonic
from typing import Iterable

import pandas as pd
from sqlalchemy import desc, delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from qagent.storage.market_cache import MarketDataCacheRepository, TRUSTED_PAIRED_ADJUSTED_PROVIDERS
from qagent.research.suspension_evidence import load_suspension_evidence
from qagent.storage.tables import (
    ExactPriceRepairCursorRow, HistoricalInstrumentProfileRow, HistoricalTradabilityRow,
    utc_now,
)


EXACT_PRICE_REPAIR_BATCH_SIZE = 20


@dataclass
class ExactPriceRepairBudget:
    """Cooperative wall-clock and provider-call budget shared by one shadow stage."""

    max_provider_batches: int
    deadline_monotonic: float | None = None
    provider_batches_used: int = 0
    exhausted_reason: str | None = None

    @classmethod
    def bounded(
        cls,
        *,
        max_provider_batches: int,
        wall_clock_seconds: float | None,
    ) -> "ExactPriceRepairBudget":
        return cls(
            max_provider_batches=max(0, max_provider_batches),
            deadline_monotonic=(
                monotonic() + max(0.0, wall_clock_seconds)
                if wall_clock_seconds is not None
                else None
            ),
        )

    def claim_provider_batch(self) -> bool:
        if self.deadline_monotonic is not None and monotonic() >= self.deadline_monotonic:
            self.exhausted_reason = "wall_clock_deadline"
            return False
        if self.provider_batches_used >= self.max_provider_batches:
            self.exhausted_reason = "provider_batch_budget"
            return False
        self.provider_batches_used += 1
        return True

    def refund_provider_batch(self) -> None:
        """Refund a conservative claim when telemetry proves no provider I/O occurred."""

        self.provider_batches_used = max(0, self.provider_batches_used - 1)


@dataclass(frozen=True, order=True)
class ExactPriceRequirement:
    instrument_id: str
    trade_date: date
    field: str


@dataclass
class ExactPriceRepairResult:
    requested: int = 0
    cache_hits: int = 0
    skipped_after_recheck: int = 0
    provider_requested: int = 0
    provider_batches: int = 0
    repaired: int = 0
    suspended: int = 0
    not_listed: int = 0
    missing: int = 0
    errors: int = 0
    rejected_unsafe_provenance: int = 0
    deferred_by_budget: int = 0
    budget_exhausted_reason: str | None = None
    unresolved: list[ExactPriceRequirement] = field(default_factory=list)
    reasons: dict[str, int] = field(default_factory=dict)
    error_details: list[str] = field(default_factory=list)
    batch_trace: list[str] = field(default_factory=list)

    @property
    def retryable(self) -> int:
        return self.missing + self.errors

    def data_health(self, prefix: str) -> dict[str, str]:
        return {
            f"{prefix}_exact_price_requested": str(self.requested),
            f"{prefix}_exact_price_cache_hits": str(self.cache_hits),
            f"{prefix}_exact_price_skipped_after_recheck": str(
                self.skipped_after_recheck
            ),
            f"{prefix}_exact_price_provider_requested": str(self.provider_requested),
            f"{prefix}_exact_price_provider_batches": str(self.provider_batches),
            f"{prefix}_exact_price_repaired": str(self.repaired),
            f"{prefix}_exact_price_suspended": str(self.suspended),
            f"{prefix}_exact_price_not_listed": str(self.not_listed),
            f"{prefix}_exact_price_missing": str(self.missing),
            f"{prefix}_exact_price_errors": str(self.errors),
            f"{prefix}_exact_price_rejected_unsafe_provenance": str(
                self.rejected_unsafe_provenance
            ),
            f"{prefix}_exact_price_deferred_by_budget": str(self.deferred_by_budget),
            f"{prefix}_exact_price_budget_exhausted": str(
                self.budget_exhausted_reason is not None
            ).lower(),
            f"{prefix}_exact_price_budget_exhausted_reason": (
                self.budget_exhausted_reason or "none"
            ),
            f"{prefix}_exact_price_retryable": str(self.retryable),
            f"{prefix}_exact_price_unresolved": str(len(self.unresolved)),
            f"{prefix}_exact_price_reason_mix": _reason_mix(self.reasons),
            f"{prefix}_exact_price_error_details": " | ".join(self.error_details[:5]),
            f"{prefix}_exact_price_batch_trace": " | ".join(self.batch_trace[:20]),
        }


def repair_exact_daily_prices(
    cache: MarketDataCacheRepository,
    *,
    provider_mode: str,
    market_provider: object | None,
    requirements: Iterable[ExactPriceRequirement],
    cursor_requirements: Iterable[ExactPriceRequirement] | None = None,
    batch_size: int = EXACT_PRICE_REPAIR_BATCH_SIZE,
    work_budget: ExactPriceRepairBudget | None = None,
) -> ExactPriceRepairResult:
    """Fill exact shadow-price holes without trusting aggregate range coverage.

    ``cursor_requirements`` preserves slots for a consumer's completed outcomes;
    it never expands requested prices. New mature dates or other cohort changes
    still create a new scope. Stable slots can leave partially filled batches.
    """

    required_set = set(requirements)
    required = sorted(required_set)
    cursor_universe = sorted(required_set.union(cursor_requirements or ()))
    result = ExactPriceRepairResult(requested=len(required))
    missing = _missing_requirements(cache, provider_mode, required)
    result.cache_hits = len(required) - len(missing)
    if not missing:
        return result

    structural: dict[ExactPriceRequirement, str] = {}
    suspension_evidence = {} if provider_mode == "fixture" else load_suspension_evidence()
    for requirement in missing:
        reason = _structural_no_row_reason(
            cache, provider_mode, requirement, suspension_evidence
        )
        if reason is not None:
            structural[requirement] = reason

    repairable = [item for item in missing if item not in structural]
    provider_errors: set[ExactPriceRequirement] = set()
    unsafe_rejections: set[ExactPriceRequirement] = set()
    budget_deferred: set[ExactPriceRequirement] = set()
    if market_provider is not None:
        raw_provider = getattr(market_provider, "provider", market_provider)
        getter = getattr(raw_provider, "get_historical_daily_bars", None)
        if not callable(getter):
            getter = getattr(raw_provider, "get_daily_bars", None)
        if not callable(getter):
            provider_errors.update(repairable)
            result.error_details.append("provider does not implement exact daily bars")
        grouped: dict[date, list[ExactPriceRequirement]] = defaultdict(list)
        # Keep cursor membership stable while cache holes are filled. Hashing
        # only current gaps resets the cursor after every successful repair and
        # repeatedly puts old no-row batches ahead of unattempted later gaps.
        for requirement in cursor_universe if callable(getter) else []:
            grouped[requirement.trade_date].append(requirement)
        batches = []
        effective_batch_size = min(max(1, batch_size), 20)
        for trade_date, dated in sorted(grouped.items()):
            by_instrument: dict[str, set[ExactPriceRequirement]] = defaultdict(set)
            for item in dated:
                by_instrument[item.instrument_id].add(item)
            instruments = sorted(by_instrument)
            for offset in range(0, len(instruments), effective_batch_size):
                batch = instruments[offset : offset + effective_batch_size]
                batches.append((trade_date, set().union(*(by_instrument[item] for item in batch))))
        # Changes to the requested protocol start an independent scope; cache
        # hits and structural gaps retain their slots and are skipped below.
        scope = hashlib.sha256(json.dumps([
            "exact-price-v1", provider_mode, effective_batch_size,
            [[day.isoformat(), [[r.instrument_id, r.field] for r in sorted(items)]]
             for day, items in batches],
        ]).encode()).hexdigest()
        attempted: set[ExactPriceRequirement] = set()
        for offset in range(len(batches)):
            if work_budget is not None and not work_budget.claim_provider_batch():
                budget_deferred.update(set(repairable) - attempted)
                break
            index = offset
            if work_budget is not None:
                try:
                    index = _claim_repair_cursor(cache, scope, len(batches))
                except SQLAlchemyError:
                    # Metadata must not turn a research repair into a failing
                    # scheduler stage. Preserve the existing bounded fallback.
                    if "research repair cursor unavailable" not in result.error_details:
                        result.error_details.append("research repair cursor unavailable")
            trade_date, batch_requirements = batches[index]
            active_requirements = batch_requirements.intersection(required_set)
            if not active_requirements:
                if work_budget is not None:
                    work_budget.refund_provider_batch()
                continue
            current_missing = set(_missing_requirements(cache, provider_mode, active_requirements))
            result.skipped_after_recheck += len(active_requirements) - len(current_missing)
            still_missing = current_missing - structural.keys()
            if not still_missing:
                if work_budget is not None:
                    work_budget.refund_provider_batch()
                continue
            attempted.update(still_missing)
            requested_batch = sorted({item.instrument_id for item in still_missing})
            result.provider_requested += len(requested_batch)
            result.provider_batches += 1
            if len(result.batch_trace) < 20:
                result.batch_trace.append(
                    f"{scope[:12]}:{index}/{len(batches)}:{trade_date.isoformat()}:"
                    f"{','.join(requested_batch)}"
                )
            try:
                frame = getter(requested_batch, trade_date, trade_date)
                # Snapshot each call's telemetry before the next call clears it.
                for error in getattr(raw_provider, "last_errors", []) or []:
                    message = str(error)
                    named = {r for r in still_missing if message.startswith(r.instrument_id + ":")}
                    if not named and re.match(r"^[A-Z]+:[^:\s]+:", message):
                        # Some wrappers accumulate old symbol errors. They are
                        # not a batch-wide error for this request.
                        continue
                    provider_errors.update(named or still_missing)
                    if len(result.error_details) < 5:
                        result.error_details.append(f"{trade_date.isoformat()}:{_safe_error(message)}")
                if isinstance(frame, pd.DataFrame) and not frame.empty:
                    frame, rejected = _filter_unsafe_exact_rows(frame, still_missing)
                    unsafe_rejections.update(rejected)
                    result.rejected_unsafe_provenance += len(rejected)
                    cache.merge_missing_daily_bars(
                        provider_mode, frame,
                        allowed_keys={(instrument_id, trade_date) for instrument_id in requested_batch},
                    )
            except Exception as exc:
                provider_errors.update(still_missing)
                if len(result.error_details) < 5:
                    result.error_details.append(
                        f"{trade_date.isoformat()}:{','.join(requested_batch[:3])}:{_safe_error(str(exc))}"
                    )
    remaining = _missing_requirements(cache, provider_mode, missing)
    result.repaired = len(missing) - len(remaining)
    result.deferred_by_budget = len(budget_deferred & set(remaining))
    result.budget_exhausted_reason = (
        work_budget.exhausted_reason if work_budget is not None else None
    )
    for requirement in remaining:
        reason = structural.get(requirement)
        if reason is None:
            if requirement in budget_deferred:
                reason = "work_budget_exhausted"
            elif requirement in unsafe_rejections:
                reason = "unsafe_adjustment_provenance"
            elif market_provider is None:
                reason = "provider_unavailable"
            else:
                reason = (
                    "provider_error"
                    if requirement in provider_errors
                    else "provider_no_row"
                )
        result.unresolved.append(requirement)
        result.reasons[reason] = result.reasons.get(reason, 0) + 1
        if reason in {"suspended", "confirmed_suspended"}:
            result.suspended += 1
        elif reason == "not_listed":
            result.not_listed += 1
        elif reason == "provider_error":
            result.errors += 1
        else:
            result.missing += 1
    return result


def _safe_error(message: str) -> str:
    # Provider exceptions can include complete request URLs or auth headers.
    message = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", message)
    message = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", message)
    message = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", message)
    message = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|token|password|secret|authorization)\s*[:=]\s*)[^\s,;]+",
        r"\1[redacted]", message,
    )
    return message[:200]


def _claim_repair_cursor(cache, scope: str, batch_count: int) -> int:
    """Atomically reserve a batch before I/O; crashes advance, never reset priority."""
    now = utc_now()
    with cache.session_factory() as session:
        session.execute(delete(ExactPriceRepairCursorRow).where(
            ExactPriceRepairCursorRow.updated_at < now - timedelta(days=30)
        ))
        statement = sqlite_insert(ExactPriceRepairCursorRow).values(
            scope_hash=scope, next_batch=1, updated_at=now,
        ).on_conflict_do_update(
            index_elements=[ExactPriceRepairCursorRow.scope_hash],
            set_={"next_batch": ExactPriceRepairCursorRow.next_batch + 1, "updated_at": now},
        ).returning(ExactPriceRepairCursorRow.next_batch)
        reserved = session.execute(statement).scalar_one() - 1
        session.commit()
    return reserved % batch_count


def _missing_requirements(
    cache: MarketDataCacheRepository,
    provider_mode: str,
    requirements: Iterable[ExactPriceRequirement],
) -> list[ExactPriceRequirement]:
    required = list(requirements)
    grouped: dict[date, list[ExactPriceRequirement]] = defaultdict(list)
    for item in required:
        grouped[item.trade_date].append(item)
    missing: list[ExactPriceRequirement] = []
    for trade_date, dated in grouped.items():
        instrument_ids = sorted({item.instrument_id for item in dated})
        bars = cache.load_daily_bars(provider_mode, instrument_ids, trade_date, trade_date)
        for item in dated:
            rows = bars.loc[
                (bars["instrument_id"] == item.instrument_id)
                & (bars["trade_date"] == item.trade_date),
            ]
            values = rows[item.field]
            value = pd.to_numeric(values.iloc[0], errors="coerce") if len(values) == 1 else None
            if (
                value is None
                or pd.isna(value)
                or float(value) <= 0
                or (len(rows) == 1 and _unsafe_exact_row(rows.iloc[0], item.field))
            ):
                missing.append(item)
    return missing


def _filter_unsafe_exact_rows(
    frame: pd.DataFrame,
    requirements: set[ExactPriceRequirement],
) -> tuple[pd.DataFrame, set[ExactPriceRequirement]]:
    normalized = frame.copy()
    rejected_keys: set[tuple[str, date]] = set()
    rejected_requirements: set[ExactPriceRequirement] = set()
    for requirement in requirements:
        rows = normalized.loc[
            (normalized["instrument_id"] == requirement.instrument_id)
            & (normalized["trade_date"] == requirement.trade_date)
        ]
        if len(rows) == 1 and _unsafe_exact_row(rows.iloc[0], requirement.field):
            rejected_keys.add((requirement.instrument_id, requirement.trade_date))
            rejected_requirements.add(requirement)
    if not rejected_keys:
        return normalized, set()
    rejected_mask = normalized.apply(
        lambda row: (str(row.get("instrument_id")), row.get("trade_date")) in rejected_keys,
        axis=1,
    )
    for column in (
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "adjustment_factor",
        "adjustment_type",
    ):
        if column in normalized.columns:
            normalized.loc[rejected_mask, column] = None
    return normalized, rejected_requirements


def _unsafe_exact_row(row: pd.Series, field: str) -> bool:
    if not field.startswith("adjusted_"):
        return False
    provider = str(row.get("provider") or "").lower()
    adjustment_type = str(row.get("adjustment_type") or "").lower()
    if adjustment_type == "snapshot_qfq_anchor":
        return True
    if provider == "fuyao_realtime":
        adjusted_source = row.get("adjusted_source_provider")
        if adjusted_source not in TRUSTED_PAIRED_ADJUSTED_PROVIDERS:
            return True
        if adjustment_type not in {"qfq", "forward"}:
            return True
        values = {}
        for column in ("close", "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "adjustment_factor"):
            try:
                value = float(row.get(column))
            except (TypeError, ValueError):
                return True
            if not math.isfinite(value) or value <= 0:
                return True
            values[column] = value
        if not (
            values["adjusted_low"] <= min(values["adjusted_open"], values["adjusted_close"])
            and values["adjusted_high"] >= max(values["adjusted_open"], values["adjusted_close"])
        ):
            return True
        if not math.isclose(
            values["adjusted_close"],
            values["close"] * values["adjustment_factor"],
            rel_tol=0,
            abs_tol=0.000002 + values["close"] * 0.0000000001,
        ):
            return True
        return False
    return (
        provider == "fuyao_etf_unadjusted"
    )


def _structural_no_row_reason(
    cache: MarketDataCacheRepository,
    provider_mode: str,
    requirement: ExactPriceRequirement,
    suspension_evidence: dict | None = None,
) -> str | None:
    with cache.session_factory() as session:
        tradability = (
            session.query(HistoricalTradabilityRow)
            .filter(
                HistoricalTradabilityRow.provider_mode == provider_mode,
                HistoricalTradabilityRow.instrument_id == requirement.instrument_id,
                HistoricalTradabilityRow.trade_date == requirement.trade_date,
            )
            .order_by(
                desc(HistoricalTradabilityRow.dataset_revision),
                HistoricalTradabilityRow.source_provider,
            )
            .first()
        )
        if tradability is not None and tradability.trading_status == "suspended":
            return "suspended"
        profile = (
            session.query(HistoricalInstrumentProfileRow)
            .filter(
                HistoricalInstrumentProfileRow.provider_mode == provider_mode,
                HistoricalInstrumentProfileRow.instrument_id == requirement.instrument_id,
                HistoricalInstrumentProfileRow.snapshot_date <= requirement.trade_date,
            )
            .order_by(
                desc(HistoricalInstrumentProfileRow.snapshot_date),
                desc(HistoricalInstrumentProfileRow.dataset_revision),
            )
            .first()
        )
        if profile is not None and (
            (profile.listing_date is not None and profile.listing_date > requirement.trade_date)
            or (
                profile.delisting_date is not None
                and profile.delisting_date <= requirement.trade_date
            )
        ):
            return "not_listed"
        # Contrary explicit metadata fails closed; no trading-state import.
        if tradability is None and (requirement.instrument_id, requirement.trade_date) in (
            suspension_evidence or {}
        ):
            return "confirmed_suspended"
    return None


def _reason_mix(reasons: dict[str, int]) -> str:
    return ",".join(f"{key}={value}" for key, value in sorted(Counter(reasons).items()))
