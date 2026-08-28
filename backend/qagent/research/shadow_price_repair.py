from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

import pandas as pd
from sqlalchemy import desc

from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.tables import HistoricalInstrumentProfileRow, HistoricalTradabilityRow


EXACT_PRICE_REPAIR_BATCH_SIZE = 20


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
    unresolved: list[ExactPriceRequirement] = field(default_factory=list)
    reasons: dict[str, int] = field(default_factory=dict)
    error_details: list[str] = field(default_factory=list)

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
            f"{prefix}_exact_price_retryable": str(self.retryable),
            f"{prefix}_exact_price_unresolved": str(len(self.unresolved)),
            f"{prefix}_exact_price_reason_mix": _reason_mix(self.reasons),
            f"{prefix}_exact_price_error_details": " | ".join(self.error_details[:5]),
        }


def repair_exact_daily_prices(
    cache: MarketDataCacheRepository,
    *,
    provider_mode: str,
    market_provider: object | None,
    requirements: Iterable[ExactPriceRequirement],
    batch_size: int = EXACT_PRICE_REPAIR_BATCH_SIZE,
) -> ExactPriceRepairResult:
    """Fill exact shadow-price holes without trusting aggregate range coverage."""

    required = sorted(set(requirements))
    result = ExactPriceRepairResult(requested=len(required))
    missing = _missing_requirements(cache, provider_mode, required)
    result.cache_hits = len(required) - len(missing)
    if not missing:
        return result

    structural: dict[ExactPriceRequirement, str] = {}
    for requirement in missing:
        reason = _structural_no_row_reason(cache, provider_mode, requirement)
        if reason is not None:
            structural[requirement] = reason

    repairable = [item for item in missing if item not in structural]
    provider_errors: set[ExactPriceRequirement] = set()
    unsafe_rejections: set[ExactPriceRequirement] = set()
    if market_provider is not None:
        raw_provider = getattr(market_provider, "provider", market_provider)
        getter = getattr(raw_provider, "get_historical_daily_bars", None)
        if not callable(getter):
            getter = getattr(raw_provider, "get_daily_bars", None)
        if not callable(getter):
            provider_errors.update(repairable)
            result.error_details.append("provider does not implement exact daily bars")
        grouped: dict[date, list[ExactPriceRequirement]] = defaultdict(list)
        for requirement in repairable if callable(getter) else []:
            grouped[requirement.trade_date].append(requirement)
        for trade_date, dated in sorted(grouped.items()):
            instruments = sorted({item.instrument_id for item in dated})
            for offset in range(0, len(instruments), min(max(1, batch_size), 20)):
                batch = instruments[offset : offset + min(max(1, batch_size), 20)]
                batch_requirements = {item for item in dated if item.instrument_id in batch}
                still_missing = set(
                    _missing_requirements(cache, provider_mode, batch_requirements)
                )
                skipped = len(batch_requirements) - len(still_missing)
                result.skipped_after_recheck += skipped
                if not still_missing:
                    continue
                requested_batch = sorted({item.instrument_id for item in still_missing})
                result.provider_requested += len(requested_batch)
                result.provider_batches += 1
                try:
                    frame = (
                        getter(requested_batch, trade_date, trade_date)
                        if callable(getter)
                        else None
                    )
                    if isinstance(frame, pd.DataFrame) and not frame.empty:
                        frame, rejected = _filter_unsafe_exact_rows(frame, still_missing)
                        unsafe_rejections.update(rejected)
                        result.rejected_unsafe_provenance += len(rejected)
                        cache.merge_missing_daily_bars(
                            provider_mode,
                            frame,
                            allowed_keys={
                                (instrument_id, trade_date)
                                for instrument_id in requested_batch
                            },
                        )
                except Exception as exc:
                    provider_errors.update(still_missing)
                    result.error_details.append(
                        f"{trade_date.isoformat()}:{','.join(requested_batch[:3])}:"
                        f"{str(exc)[:200]}"
                    )
    remaining = _missing_requirements(cache, provider_mode, missing)
    result.repaired = len(missing) - len(remaining)
    for requirement in remaining:
        reason = structural.get(requirement)
        if reason is None:
            # Provider errors may be exposed as last_errors without raising. A
            # no-row response with explicit errors stays retryable as provider_error.
            raw_provider = getattr(market_provider, "provider", market_provider)
            reported_errors = getattr(raw_provider, "last_errors", []) if raw_provider else []
            if requirement in unsafe_rejections:
                reason = "unsafe_adjustment_provenance"
            elif market_provider is None:
                reason = "provider_unavailable"
            else:
                reason = (
                    "provider_error"
                    if requirement in provider_errors or bool(reported_errors)
                    else "provider_no_row"
                )
        result.unresolved.append(requirement)
        result.reasons[reason] = result.reasons.get(reason, 0) + 1
        if reason == "suspended":
            result.suspended += 1
        elif reason == "not_listed":
            result.not_listed += 1
        elif reason == "provider_error":
            result.errors += 1
        else:
            result.missing += 1
    return result


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
    return (
        provider == "fuyao_etf_unadjusted"
        or provider == "fuyao_realtime"
        or adjustment_type == "snapshot_qfq_anchor"
    )


def _structural_no_row_reason(
    cache: MarketDataCacheRepository,
    provider_mode: str,
    requirement: ExactPriceRequirement,
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
    return None


def _reason_mix(reasons: dict[str, int]) -> str:
    return ",".join(f"{key}={value}" for key, value in sorted(Counter(reasons).items()))
