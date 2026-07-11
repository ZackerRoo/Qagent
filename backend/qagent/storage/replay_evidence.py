from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from qagent.historical_evidence.models import (
    HistoricalCorporateAction,
    HistoricalEvidenceBundle,
    HistoricalIndexMembership,
    HistoricalIndustrySnapshot,
    HistoricalInstrumentProfile,
    HistoricalLifecycleManifest,
    HistoricalReplayBar,
    HistoricalTradabilityPoint,
    HistoricalUniverseManifest,
)
from qagent.storage.tables import (
    FundamentalSnapshotRow,
    HistoricalCorporateActionCoverageRow,
    HistoricalCorporateActionRow,
    HistoricalDataRevisionRow,
    HistoricalDatasetLeaseRow,
    HistoricalIndexMembershipRow,
    HistoricalIndexSnapshotRow,
    HistoricalIndustrySnapshotRow,
    HistoricalInstrumentProfileRow,
    HistoricalLifecycleManifestRow,
    HistoricalReplayBarRow,
    HistoricalReplayUniverseMemberRow,
    HistoricalTradabilityRow,
    HistoricalUniverseManifestRow,
)
from qagent.strategy_data.models import FundamentalSnapshot


LEASE_DURATION = timedelta(minutes=5)
STALE_AFTER = timedelta(minutes=10)
TERMINAL_RUN_STATUSES = {"succeeded", "blocked_data", "failed", "cancelled"}


class DatasetLeaseBusy(RuntimeError):
    pass


class SourceWriteBlocked(RuntimeError):
    pass


class StaleCheckpointRevision(RuntimeError):
    pass


class ReplayEvidenceUnavailable(RuntimeError):
    pass


class ImmutableRevisionConflict(ValueError):
    pass


class DerivedUniverseOwnershipConflict(ValueError):
    pass


class DatasetLeaseRecord(BaseModel):
    provider_mode: str
    owner_run_id: str
    revision: int
    lease_expires_at: datetime
    heartbeat_at: datetime


class ActionCoverageRecord(BaseModel):
    instrument_id: str
    start_date: date
    end_date: date
    status: Literal["ready", "ready_none", "partial", "unsupported"]
    action_count: int
    source_provider: str


class UniverseMemberRecord(BaseModel):
    provider_mode: str
    snapshot_date: date
    source_revision: int
    instrument_id: str
    owner_run_id: str
    security_type: str
    listing_date: date | None
    delisting_date: date | None
    active: bool
    source_provider: str
    fetched_at: datetime


class UniverseMaterialization(BaseModel):
    manifest: HistoricalUniverseManifest
    members: list[UniverseMemberRecord]


class ReplayEvidenceRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider_mode: str = "free",
        *,
        owner_run_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
        run_status_lookup: Callable[[str], str | None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.provider_mode = _normalize_provider(provider_mode)
        self.owner_run_id = owner_run_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._run_status_lookup = run_status_lookup or (lambda _run_id: None)

    def current_revision(self) -> int:
        with self.session_factory() as session:
            row = session.get(HistoricalDataRevisionRow, self.provider_mode)
            return row.revision if row is not None else 0

    def upsert_replay_bars(
        self,
        bars: Sequence[HistoricalReplayBar],
        *,
        revision: int | None = None,
    ) -> int:
        records = [
            {
                **bar.model_dump(exclude={"provider_mode", "dataset_revision"}),
                "provider_mode": self.provider_mode,
                "source_provider": _normalize_provider(bar.source_provider),
            }
            for bar in bars
            if self._matches_provider(bar.provider_mode)
        ]
        requested = self._model_revision(bars, revision)
        return self._write_source_rows(
            HistoricalReplayBarRow,
            records,
            ["provider_mode", "instrument_id", "trade_date"],
            requested,
        )

    def upsert_corporate_actions(
        self,
        actions: Sequence[HistoricalCorporateAction],
        *,
        revision: int | None = None,
    ) -> int:
        records = [
            {
                **action.model_dump(exclude={"provider_mode", "dataset_revision"}),
                "provider_mode": self.provider_mode,
                "source_provider": _normalize_provider(action.source_provider),
            }
            for action in actions
            if self._matches_provider(action.provider_mode)
        ]
        requested = self._model_revision(actions, revision)
        return self._write_source_rows(
            HistoricalCorporateActionRow,
            records,
            ["provider_mode", "instrument_id", "action_id"],
            requested,
        )

    def upsert_fundamentals(
        self,
        snapshots: Sequence[FundamentalSnapshot],
        *,
        revision: int | None = None,
    ) -> int:
        records = [
            {
                "provider_mode": self.provider_mode,
                "instrument_id": item.instrument_id,
                "as_of_date": item.as_of_date,
                "source_provider": _normalize_provider(item.provider or "unknown"),
                "revenue_growth_pct": _decimal_or_none(item.revenue_growth_pct),
                "earnings_growth_pct": _decimal_or_none(item.earnings_growth_pct),
                "gross_margin_pct": _decimal_or_none(item.gross_margin_pct),
                "operating_margin_pct": _decimal_or_none(item.operating_margin_pct),
                "net_margin_pct": _decimal_or_none(item.net_margin_pct),
                "return_on_equity_pct": _decimal_or_none(item.return_on_equity_pct),
                "market_cap": _decimal_or_none(item.market_cap),
                "pe_ratio": _decimal_or_none(item.pe_ratio),
                "forward_pe": _decimal_or_none(item.forward_pe),
                "peg_ratio": _decimal_or_none(item.peg_ratio),
                "price_to_sales": _decimal_or_none(item.price_to_sales),
                "cached_at": self._now(),
                "updated_at": self._now(),
            }
            for item in snapshots
        ]
        return self._write_source_rows(
            FundamentalSnapshotRow,
            records,
            ["provider_mode", "instrument_id", "as_of_date", "source_provider"],
            revision,
            ignored_retry_fields={"cached_at", "updated_at"},
        )

    def upsert_point_in_time_evidence(
        self,
        bundle: HistoricalEvidenceBundle,
        *,
        revision: int | None = None,
    ) -> dict[str, int]:
        now = self._now()
        groups: list[tuple[object, list[dict[str, object]], list[str]]] = [
            (
                HistoricalTradabilityRow,
                [
                    {
                        "provider_mode": self.provider_mode,
                        "instrument_id": item.instrument_id,
                        "trade_date": item.trade_date,
                        "trading_status": item.trading_status,
                        "is_st": item.is_st,
                        "pct_change_pct": _decimal_or_none(item.pct_change_pct),
                        "source_provider": _normalize_provider(item.provider),
                        "fetched_at": now,
                    }
                    for item in bundle.tradability
                ],
                ["provider_mode", "instrument_id", "trade_date"],
            ),
            (
                HistoricalInstrumentProfileRow,
                [
                    {
                        "provider_mode": self.provider_mode,
                        "instrument_id": item.instrument_id,
                        "snapshot_date": item.snapshot_date,
                        "listing_date": item.listing_date,
                        "delisting_date": item.delisting_date,
                        "security_type": item.security_type,
                        "listing_status": item.listing_status,
                        "source_provider": _normalize_provider(item.provider),
                        "fetched_at": now,
                    }
                    for item in bundle.profiles
                ],
                [
                    "provider_mode",
                    "instrument_id",
                    "snapshot_date",
                    "dataset_revision",
                ],
            ),
            (
                HistoricalIndustrySnapshotRow,
                [
                    {
                        "provider_mode": self.provider_mode,
                        "instrument_id": item.instrument_id,
                        "snapshot_date": item.snapshot_date,
                        "source_provider": _normalize_provider(item.provider),
                        "industry": item.industry,
                        "classification": item.classification,
                        "fetched_at": now,
                    }
                    for item in bundle.industries
                ],
                ["provider_mode", "instrument_id", "snapshot_date", "source_provider"],
            ),
            (
                HistoricalIndexSnapshotRow,
                [
                    {
                        "provider_mode": self.provider_mode,
                        "index_id": item.index_id,
                        "snapshot_date": item.snapshot_date,
                        "status": item.status,
                        "member_count": item.member_count,
                        "source_provider": _normalize_provider(item.provider),
                        "error": item.error,
                        "fetched_at": now,
                    }
                    for item in bundle.index_snapshots
                ],
                ["provider_mode", "index_id", "snapshot_date"],
            ),
            (
                HistoricalIndexMembershipRow,
                [
                    {
                        "provider_mode": self.provider_mode,
                        "index_id": item.index_id,
                        "snapshot_date": item.snapshot_date,
                        "instrument_id": item.instrument_id,
                        "source_provider": _normalize_provider(item.provider),
                        "fetched_at": now,
                    }
                    for item in bundle.index_memberships
                ],
                ["provider_mode", "index_id", "snapshot_date", "instrument_id"],
            ),
        ]
        with self._immediate_session() as session:
            write_revision, is_retry = self._prepare_source_write(session, revision)
            result: dict[str, int] = {}
            names = [
                "tradability",
                "profiles",
                "industries",
                "index_snapshots",
                "index_memberships",
            ]
            for name, (model, records, keys) in zip(names, groups, strict=True):
                for record in records:
                    record["dataset_revision"] = write_revision
                deduplicated = _deduplicate(records, keys)
                if is_retry:
                    _verify_immutable_rows(
                        session,
                        model,
                        deduplicated,
                        keys,
                        write_revision,
                        ignored_fields={"fetched_at"},
                    )
                else:
                    _upsert_chunks(session, model, deduplicated, keys)
                result[name] = len(deduplicated)
            return result

    def upsert_lifecycle_inventory(
        self,
        profiles: Sequence[HistoricalInstrumentProfile],
        manifest: HistoricalLifecycleManifest,
    ) -> int:
        if _normalize_provider(manifest.provider_mode) != self.provider_mode:
            raise ValueError("lifecycle manifest provider does not match repository")
        records = [
            {
                "provider_mode": self.provider_mode,
                "instrument_id": item.instrument_id,
                "snapshot_date": item.snapshot_date,
                "listing_date": item.listing_date,
                "delisting_date": item.delisting_date,
                "security_type": item.security_type,
                "listing_status": item.listing_status,
                "source_provider": _normalize_provider(item.provider),
                "fetched_at": manifest.fetched_at,
            }
            for item in profiles
        ]
        with self._immediate_session() as session:
            write_revision, is_retry = self._prepare_source_write(
                session, manifest.source_revision
            )
            for record in records:
                record["dataset_revision"] = write_revision
            profile_keys = [
                "provider_mode",
                "instrument_id",
                "snapshot_date",
                "dataset_revision",
            ]
            deduplicated = _deduplicate(records, profile_keys)
            manifest_records = [
                {
                    **manifest.model_dump(exclude={"provider_mode"}),
                    "provider_mode": self.provider_mode,
                }
            ]
            if is_retry:
                _verify_immutable_rows(
                    session,
                    HistoricalInstrumentProfileRow,
                    deduplicated,
                    profile_keys,
                    write_revision,
                )
                _verify_immutable_rows(
                    session,
                    HistoricalLifecycleManifestRow,
                    manifest_records,
                    ["provider_mode", "source_revision"],
                    write_revision,
                )
            else:
                _upsert_chunks(
                    session,
                    HistoricalInstrumentProfileRow,
                    deduplicated,
                    profile_keys,
                )
                _upsert_chunks(
                    session,
                    HistoricalLifecycleManifestRow,
                    manifest_records,
                    ["provider_mode", "source_revision"],
                )
        return len(deduplicated)

    def upsert_action_coverage(
        self,
        coverage: Sequence[ActionCoverageRecord],
        *,
        revision: int | None = None,
    ) -> int:
        now = self._now()
        records = [
            {
                "provider_mode": self.provider_mode,
                **item.model_dump(),
                "source_provider": _normalize_provider(item.source_provider),
                "fetched_at": now,
            }
            for item in coverage
        ]
        return self._write_source_rows(
            HistoricalCorporateActionCoverageRow,
            records,
            ["provider_mode", "instrument_id", "start_date", "end_date"],
            revision,
            ignored_retry_fields={"fetched_at"},
        )

    def replay_bars(
        self,
        instrument_ids: Sequence[str],
        start: date,
        end: date,
        revision: int,
    ) -> list[HistoricalReplayBar]:
        if not instrument_ids:
            return []
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(HistoricalReplayBarRow)
                    .where(
                        HistoricalReplayBarRow.provider_mode == self.provider_mode,
                        HistoricalReplayBarRow.instrument_id.in_(instrument_ids),
                        HistoricalReplayBarRow.trade_date >= start,
                        HistoricalReplayBarRow.trade_date <= end,
                        HistoricalReplayBarRow.dataset_revision <= revision,
                    )
                    .order_by(
                        HistoricalReplayBarRow.trade_date,
                        HistoricalReplayBarRow.instrument_id,
                    )
                )
            )
        return [HistoricalReplayBar.model_validate(_row_dict(row)) for row in rows]

    def fundamentals_as_of(
        self,
        instrument_ids: Sequence[str],
        decision_date: date,
        revision: int,
    ) -> dict[str, FundamentalSnapshot]:
        if not instrument_ids:
            return {}
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(FundamentalSnapshotRow)
                    .where(
                        FundamentalSnapshotRow.provider_mode == self.provider_mode,
                        FundamentalSnapshotRow.instrument_id.in_(instrument_ids),
                        FundamentalSnapshotRow.as_of_date <= decision_date,
                        FundamentalSnapshotRow.dataset_revision <= revision,
                    )
                    .order_by(
                        FundamentalSnapshotRow.instrument_id,
                        FundamentalSnapshotRow.as_of_date.desc(),
                        FundamentalSnapshotRow.dataset_revision.desc(),
                        FundamentalSnapshotRow.source_provider,
                    )
                )
            )
        result: dict[str, FundamentalSnapshot] = {}
        for row in rows:
            result.setdefault(row.instrument_id, _fundamental_from_row(row))
        return result

    def industries_as_of(
        self,
        instrument_ids: Sequence[str],
        decision_date: date,
        revision: int,
    ) -> dict[str, HistoricalIndustrySnapshot]:
        if not instrument_ids:
            return {}
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(HistoricalIndustrySnapshotRow)
                    .where(
                        HistoricalIndustrySnapshotRow.provider_mode
                        == self.provider_mode,
                        HistoricalIndustrySnapshotRow.instrument_id.in_(instrument_ids),
                        HistoricalIndustrySnapshotRow.snapshot_date <= decision_date,
                        HistoricalIndustrySnapshotRow.dataset_revision <= revision,
                    )
                    .order_by(
                        HistoricalIndustrySnapshotRow.instrument_id,
                        HistoricalIndustrySnapshotRow.snapshot_date.desc(),
                        HistoricalIndustrySnapshotRow.dataset_revision.desc(),
                        HistoricalIndustrySnapshotRow.source_provider,
                    )
                )
            )
        result: dict[str, HistoricalIndustrySnapshot] = {}
        for row in rows:
            result.setdefault(
                row.instrument_id,
                HistoricalIndustrySnapshot(
                    instrument_id=row.instrument_id,
                    snapshot_date=row.snapshot_date,
                    industry=row.industry,
                    classification=row.classification,
                    provider=row.source_provider,
                ),
            )
        return result

    def memberships_as_of(
        self,
        instrument_ids: Sequence[str],
        decision_date: date,
        revision: int,
    ) -> dict[str, list[HistoricalIndexMembership]]:
        result = {instrument_id: [] for instrument_id in instrument_ids}
        if not instrument_ids:
            return result
        with self.session_factory() as session:
            snapshots = list(
                session.scalars(
                    select(HistoricalIndexSnapshotRow)
                    .where(
                        HistoricalIndexSnapshotRow.provider_mode == self.provider_mode,
                        HistoricalIndexSnapshotRow.status == "ready",
                        HistoricalIndexSnapshotRow.snapshot_date <= decision_date,
                        HistoricalIndexSnapshotRow.dataset_revision <= revision,
                    )
                    .order_by(
                        HistoricalIndexSnapshotRow.index_id,
                        HistoricalIndexSnapshotRow.snapshot_date.desc(),
                        HistoricalIndexSnapshotRow.dataset_revision.desc(),
                    )
                )
            )
            latest_by_index: dict[str, HistoricalIndexSnapshotRow] = {}
            for snapshot in snapshots:
                latest_by_index.setdefault(snapshot.index_id, snapshot)
            for snapshot in latest_by_index.values():
                rows = list(
                    session.scalars(
                        select(HistoricalIndexMembershipRow)
                        .where(
                            HistoricalIndexMembershipRow.provider_mode
                            == self.provider_mode,
                            HistoricalIndexMembershipRow.index_id == snapshot.index_id,
                            HistoricalIndexMembershipRow.snapshot_date
                            == snapshot.snapshot_date,
                            HistoricalIndexMembershipRow.instrument_id.in_(instrument_ids),
                            HistoricalIndexMembershipRow.dataset_revision
                            == snapshot.dataset_revision,
                        )
                        .order_by(HistoricalIndexMembershipRow.instrument_id)
                    )
                )
                for row in rows:
                    result[row.instrument_id].append(
                        HistoricalIndexMembership(
                            index_id=row.index_id,
                            snapshot_date=row.snapshot_date,
                            instrument_id=row.instrument_id,
                            provider=row.source_provider,
                        )
                    )
        for memberships in result.values():
            memberships.sort(key=lambda item: item.index_id)
        return result

    def tradability_on(
        self,
        instrument_ids: Sequence[str],
        decision_date: date,
        revision: int,
    ) -> dict[str, HistoricalTradabilityPoint]:
        if not instrument_ids:
            return {}
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(HistoricalTradabilityRow)
                    .where(
                        HistoricalTradabilityRow.provider_mode == self.provider_mode,
                        HistoricalTradabilityRow.instrument_id.in_(instrument_ids),
                        HistoricalTradabilityRow.trade_date == decision_date,
                        HistoricalTradabilityRow.dataset_revision <= revision,
                    )
                    .order_by(
                        HistoricalTradabilityRow.instrument_id,
                        HistoricalTradabilityRow.dataset_revision.desc(),
                    )
                )
            )
        return {
            row.instrument_id: HistoricalTradabilityPoint(
                instrument_id=row.instrument_id,
                trade_date=row.trade_date,
                trading_status=row.trading_status,
                is_st=row.is_st,
                pct_change_pct=(
                    float(row.pct_change_pct) if row.pct_change_pct is not None else None
                ),
                provider=row.source_provider,
            )
            for row in rows
        }

    def lifecycle_inventory(self, revision: int) -> list[HistoricalInstrumentProfile]:
        with self.session_factory() as session:
            return self._lifecycle_inventory(session, revision)

    def materialize_universe(
        self,
        decision_date: date,
        revision: int,
    ) -> UniverseMaterialization:
        owner_run_id = self._require_owner()
        with self._immediate_session() as session:
            self._verify_leased_revision(session, owner_run_id, revision)
            inventory = self._lifecycle_inventory(session, revision, decision_date)
            missing_listing_dates = [
                item.instrument_id for item in inventory if item.listing_date is None
            ]
            if missing_listing_dates:
                raise ReplayEvidenceUnavailable(
                    "lifecycle identity is incomplete; listing_date is missing for "
                    + ", ".join(missing_listing_dates[:10])
                )
            missing_security_types = [
                item.instrument_id for item in inventory if item.security_type is None
            ]
            if missing_security_types:
                raise ReplayEvidenceUnavailable(
                    "lifecycle identity is incomplete; security_type is missing for "
                    + ", ".join(missing_security_types[:10])
                )
            identity = (self.provider_mode, decision_date, revision)
            existing_manifest = session.get(HistoricalUniverseManifestRow, identity)
            existing_members = list(
                session.scalars(
                    select(HistoricalReplayUniverseMemberRow)
                    .where(
                        HistoricalReplayUniverseMemberRow.provider_mode
                        == self.provider_mode,
                        HistoricalReplayUniverseMemberRow.snapshot_date
                        == decision_date,
                        HistoricalReplayUniverseMemberRow.source_revision == revision,
                    )
                    .order_by(HistoricalReplayUniverseMemberRow.instrument_id)
                )
            )
            if (
                existing_manifest is not None
                and existing_manifest.owner_run_id != owner_run_id
            ):
                raise DerivedUniverseOwnershipConflict(
                    f"derived universe is owned by {existing_manifest.owner_run_id}"
                )
            if existing_manifest is None and existing_members:
                raise ReplayEvidenceUnavailable(
                    "derived universe members exist without their manifest"
                )
            fetched_at = (
                _as_utc_datetime(existing_manifest.fetched_at)
                if existing_manifest is not None
                else self._now()
            )
            active = [
                item
                for item in inventory
                if item.listing_date <= decision_date
                and (item.delisting_date is None or item.delisting_date > decision_date)
            ]
            records = [
                {
                    "provider_mode": self.provider_mode,
                    "snapshot_date": decision_date,
                    "source_revision": revision,
                    "instrument_id": item.instrument_id,
                    "owner_run_id": owner_run_id,
                    "security_type": item.security_type,
                    "listing_date": item.listing_date,
                    "delisting_date": item.delisting_date,
                    "active": True,
                    "source_provider": _normalize_provider(item.provider),
                    "fetched_at": fetched_at,
                }
                for item in active
            ]
            manifest_record = {
                "provider_mode": self.provider_mode,
                "snapshot_date": decision_date,
                "source_revision": revision,
                "owner_run_id": owner_run_id,
                "status": "ready",
                "expected_count": len(records),
                "stored_count": len(records),
                "error": None,
                "fetched_at": fetched_at,
            }
            member_keys = [
                "provider_mode",
                "snapshot_date",
                "source_revision",
                "instrument_id",
            ]
            manifest_keys = ["provider_mode", "snapshot_date", "source_revision"]
            if existing_manifest is not None:
                if len(existing_members) != len(records):
                    raise ImmutableRevisionConflict(
                        "derived universe revision is immutable; member set differs"
                    )
                _verify_immutable_rows(
                    session,
                    HistoricalReplayUniverseMemberRow,
                    records,
                    member_keys,
                    revision,
                )
                _verify_immutable_rows(
                    session,
                    HistoricalUniverseManifestRow,
                    [manifest_record],
                    manifest_keys,
                    revision,
                )
            else:
                _upsert_chunks(
                    session,
                    HistoricalReplayUniverseMemberRow,
                    records,
                    member_keys,
                )
                _upsert_chunks(
                    session,
                    HistoricalUniverseManifestRow,
                    [manifest_record],
                    manifest_keys,
                )
        return UniverseMaterialization(
            manifest=HistoricalUniverseManifest(**manifest_record),
            members=[UniverseMemberRecord(**record) for record in records],
        )

    def universe_members_on(
        self,
        decision_date: date,
        revision: int,
    ) -> list[UniverseMemberRecord]:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(HistoricalReplayUniverseMemberRow)
                    .where(
                        HistoricalReplayUniverseMemberRow.provider_mode
                        == self.provider_mode,
                        HistoricalReplayUniverseMemberRow.snapshot_date == decision_date,
                        HistoricalReplayUniverseMemberRow.source_revision == revision,
                    )
                    .order_by(HistoricalReplayUniverseMemberRow.instrument_id)
                )
            )
        return [UniverseMemberRecord.model_validate(_row_dict(row)) for row in rows]

    def action_coverage(
        self,
        instrument_ids: Sequence[str],
        start: date,
        end: date,
        revision: int,
    ) -> dict[str, ActionCoverageRecord]:
        if not instrument_ids:
            return {}
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(HistoricalCorporateActionCoverageRow)
                    .where(
                        HistoricalCorporateActionCoverageRow.provider_mode
                        == self.provider_mode,
                        HistoricalCorporateActionCoverageRow.instrument_id.in_(
                            instrument_ids
                        ),
                        HistoricalCorporateActionCoverageRow.start_date == start,
                        HistoricalCorporateActionCoverageRow.end_date == end,
                        HistoricalCorporateActionCoverageRow.dataset_revision <= revision,
                    )
                    .order_by(
                        HistoricalCorporateActionCoverageRow.instrument_id,
                        HistoricalCorporateActionCoverageRow.dataset_revision.desc(),
                    )
                )
            )
        return {
            row.instrument_id: ActionCoverageRecord(
                instrument_id=row.instrument_id,
                start_date=row.start_date,
                end_date=row.end_date,
                status=row.status,
                action_count=row.action_count,
                source_provider=row.source_provider,
            )
            for row in rows
        }

    def acquire_dataset_lease(
        self, owner_run_id: str | None = None
    ) -> DatasetLeaseRecord:
        owner = owner_run_id or self._require_owner()
        now = self._now()
        with self._immediate_session() as session:
            revision = self._revision_row(session).revision
            lease = session.get(HistoricalDatasetLeaseRow, self.provider_mode)
            if lease is not None and lease.owner_run_id != owner:
                stale = lease.heartbeat_at <= now - STALE_AFTER
                owner_status = self._run_status_lookup(lease.owner_run_id)
                if stale and owner_status in TERMINAL_RUN_STATUSES:
                    session.delete(lease)
                    session.flush()
                    lease = None
                else:
                    raise DatasetLeaseBusy(
                        f"dataset lease for {self.provider_mode} is owned by "
                        f"{lease.owner_run_id}"
                    )
            if lease is not None and lease.revision != revision:
                raise StaleCheckpointRevision(
                    "dataset revision changed while the lease was persisted"
                )
            if lease is None:
                lease = HistoricalDatasetLeaseRow(
                    provider_mode=self.provider_mode,
                    owner_run_id=owner,
                    revision=revision,
                    lease_expires_at=now + LEASE_DURATION,
                    heartbeat_at=now,
                )
                session.add(lease)
            else:
                lease.heartbeat_at = now
                lease.lease_expires_at = now + LEASE_DURATION
            session.flush()
            return _lease_from_row(lease)

    def renew_dataset_lease(
        self, owner_run_id: str | None = None
    ) -> DatasetLeaseRecord:
        owner = owner_run_id or self._require_owner()
        now = self._now()
        with self._immediate_session() as session:
            lease = self._verify_leased_revision(session, owner, None)
            lease.heartbeat_at = now
            lease.lease_expires_at = now + LEASE_DURATION
            session.flush()
            return _lease_from_row(lease)

    def release_dataset_lease(self, owner_run_id: str | None = None) -> bool:
        owner = owner_run_id or self._require_owner()
        with self._immediate_session() as session:
            lease = session.get(HistoricalDatasetLeaseRow, self.provider_mode)
            if lease is None:
                return False
            if lease.owner_run_id != owner:
                raise DatasetLeaseBusy(
                    f"dataset lease for {self.provider_mode} is owned by "
                    f"{lease.owner_run_id}"
                )
            session.delete(lease)
            return True

    def verify_checkpoint_revision(self, revision: int) -> None:
        owner = self._require_owner()
        with self._immediate_session() as session:
            self._verify_leased_revision(session, owner, revision)

    @contextmanager
    def checkpoint_transaction(self, revision: int) -> Iterator[Session]:
        owner = self._require_owner()
        with self._immediate_session() as session:
            self._verify_leased_revision(session, owner, revision)
            yield session

    def _write_source_rows(
        self,
        model,
        records: list[dict[str, object]],
        index_elements: list[str],
        revision: int | None,
        *,
        ignored_retry_fields: set[str] | None = None,
    ) -> int:
        if not records:
            return 0
        deduplicated = _deduplicate(records, index_elements)
        with self._immediate_session() as session:
            write_revision, is_retry = self._prepare_source_write(session, revision)
            for record in deduplicated:
                record["dataset_revision"] = write_revision
            if is_retry:
                _verify_immutable_rows(
                    session,
                    model,
                    deduplicated,
                    index_elements,
                    write_revision,
                    ignored_fields=ignored_retry_fields,
                )
            else:
                _upsert_chunks(session, model, deduplicated, index_elements)
        return len(deduplicated)

    def _prepare_source_write(
        self, session: Session, revision: int | None
    ) -> tuple[int, bool]:
        lease = session.get(HistoricalDatasetLeaseRow, self.provider_mode)
        if lease is not None:
            raise SourceWriteBlocked(
                f"source writes are blocked by lease owner {lease.owner_run_id}"
            )
        row = self._revision_row(session)
        write_revision = row.revision + 1 if revision is None else revision
        if write_revision not in {row.revision, row.revision + 1} or write_revision < 1:
            raise ValueError(
                f"historical revision must be monotonic: current={row.revision}, "
                f"requested={write_revision}"
            )
        is_retry = write_revision == row.revision
        if write_revision > row.revision:
            row.revision = write_revision
            row.updated_at = self._now()
        return write_revision, is_retry

    def _revision_row(self, session: Session) -> HistoricalDataRevisionRow:
        row = session.get(HistoricalDataRevisionRow, self.provider_mode)
        if row is None:
            row = HistoricalDataRevisionRow(
                provider_mode=self.provider_mode,
                revision=0,
                updated_at=self._now(),
            )
            session.add(row)
            session.flush()
        return row

    def _verify_leased_revision(
        self,
        session: Session,
        owner_run_id: str,
        revision: int | None,
    ) -> HistoricalDatasetLeaseRow:
        current = self._revision_row(session).revision
        lease = session.get(HistoricalDatasetLeaseRow, self.provider_mode)
        if lease is None or lease.owner_run_id != owner_run_id:
            raise DatasetLeaseBusy(
                f"run {owner_run_id} does not own the {self.provider_mode} lease"
            )
        if lease.lease_expires_at <= self._now():
            raise DatasetLeaseBusy(
                f"dataset lease for {self.provider_mode} expired; reacquire it"
            )
        expected = lease.revision if revision is None else revision
        if lease.revision != expected or current != expected:
            raise StaleCheckpointRevision(
                f"leased revision {lease.revision} no longer matches current "
                f"revision {current}"
            )
        return lease

    def _lifecycle_inventory(
        self,
        session: Session,
        revision: int,
        decision_date: date | None = None,
    ) -> list[HistoricalInstrumentProfile]:
        manifest = session.scalar(
            select(HistoricalLifecycleManifestRow)
            .where(
                HistoricalLifecycleManifestRow.provider_mode == self.provider_mode,
                HistoricalLifecycleManifestRow.status == "ready",
                HistoricalLifecycleManifestRow.source_revision <= revision,
            )
            .order_by(HistoricalLifecycleManifestRow.source_revision.desc())
            .limit(1)
        )
        if manifest is None:
            raise ReplayEvidenceUnavailable(
                f"ready lifecycle inventory is missing for revision {revision}"
            )
        if (
            manifest.expected_count is None
            or manifest.expected_count != manifest.stored_count
        ):
            raise ReplayEvidenceUnavailable(
                f"lifecycle inventory is incomplete for revision {manifest.source_revision}"
            )
        if decision_date is not None and manifest.effective_through < decision_date:
            raise ReplayEvidenceUnavailable(
                f"lifecycle inventory does not cover {decision_date.isoformat()}"
            )
        rows = list(
            session.scalars(
                select(HistoricalInstrumentProfileRow)
                .where(
                    HistoricalInstrumentProfileRow.provider_mode == self.provider_mode,
                    HistoricalInstrumentProfileRow.dataset_revision
                    == manifest.source_revision,
                )
                .order_by(
                    HistoricalInstrumentProfileRow.instrument_id,
                    HistoricalInstrumentProfileRow.snapshot_date.desc(),
                )
            )
        )
        latest_rows = {}
        for row in rows:
            latest_rows.setdefault(row.instrument_id, row)
        if len(latest_rows) != manifest.stored_count:
            raise ReplayEvidenceUnavailable(
                f"lifecycle rows do not match manifest revision {manifest.source_revision}"
            )
        return [
            HistoricalInstrumentProfile(
                instrument_id=row.instrument_id,
                snapshot_date=row.snapshot_date,
                listing_date=row.listing_date,
                delisting_date=row.delisting_date,
                security_type=row.security_type,
                listing_status=row.listing_status,
                provider=row.source_provider,
            )
            for row in latest_rows.values()
        ]

    def _matches_provider(self, provider_mode: str) -> bool:
        if _normalize_provider(provider_mode) != self.provider_mode:
            raise ValueError("evidence provider does not match repository")
        return True

    def _model_revision(self, rows: Sequence[object], revision: int | None) -> int | None:
        if revision is not None or not rows:
            return revision
        values = {getattr(row, "dataset_revision", None) for row in rows}
        values.discard(None)
        if len(values) > 1:
            raise ValueError("source batch contains multiple dataset revisions")
        return values.pop() if values else None

    def _require_owner(self) -> str:
        if not self.owner_run_id:
            raise ValueError("owner_run_id is required for dataset lease operations")
        return self.owner_run_id

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("repository clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    @contextmanager
    def _immediate_session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            try:
                if session.bind is not None and session.bind.dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise


def _upsert_chunks(
    session: Session,
    model,
    records: list[dict[str, object]],
    index_elements: list[str],
    chunk_size: int = 400,
) -> None:
    if not records:
        return
    update_columns = [key for key in records[0] if key not in index_elements]
    for offset in range(0, len(records), chunk_size):
        statement = sqlite_insert(model).values(records[offset : offset + chunk_size])
        excluded = statement.excluded
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[getattr(model, key) for key in index_elements],
                set_={key: getattr(excluded, key) for key in update_columns},
            )
        )


def _deduplicate(
    records: list[dict[str, object]], index_elements: list[str]
) -> list[dict[str, object]]:
    deduplicated: dict[tuple[object, ...], dict[str, object]] = {}
    for record in records:
        key = tuple(record[column] for column in index_elements)
        if key in deduplicated and not _records_match(deduplicated[key], record):
            raise ImmutableRevisionConflict(
                "source identity is immutable; duplicate batch payload differs"
            )
        deduplicated[key] = record
    return list(deduplicated.values())


def _records_match(
    first: dict[str, object], second: dict[str, object]
) -> bool:
    if first.keys() != second.keys():
        return False
    return all(
        _canonical_value(first[key]) == _canonical_value(second[key]) for key in first
    )


def _verify_immutable_rows(
    session: Session,
    model,
    records: list[dict[str, object]],
    index_elements: list[str],
    revision: int,
    *,
    ignored_fields: set[str] | None = None,
) -> None:
    ignored = ignored_fields or set()
    for record in records:
        row = session.scalar(
            select(model).where(
                *(getattr(model, key) == record[key] for key in index_elements)
            )
        )
        if row is None:
            raise ImmutableRevisionConflict(
                f"revision {revision} is immutable; identity does not exist"
            )
        for key, incoming in record.items():
            if key in ignored:
                continue
            if _canonical_value(getattr(row, key)) != _canonical_value(incoming):
                raise ImmutableRevisionConflict(
                    f"revision {revision} is immutable; payload differs for {key}"
                )


def _canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        return _as_utc_datetime(value)
    return value


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _row_dict(row) -> dict[str, object]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _fundamental_from_row(row: FundamentalSnapshotRow) -> FundamentalSnapshot:
    return FundamentalSnapshot(
        instrument_id=row.instrument_id,
        as_of_date=row.as_of_date,
        revenue_growth_pct=row.revenue_growth_pct,
        earnings_growth_pct=row.earnings_growth_pct,
        gross_margin_pct=row.gross_margin_pct,
        operating_margin_pct=row.operating_margin_pct,
        net_margin_pct=row.net_margin_pct,
        return_on_equity_pct=row.return_on_equity_pct,
        market_cap=row.market_cap,
        pe_ratio=row.pe_ratio,
        forward_pe=row.forward_pe,
        peg_ratio=row.peg_ratio,
        price_to_sales=row.price_to_sales,
        provider=row.source_provider,
    )


def _lease_from_row(row: HistoricalDatasetLeaseRow) -> DatasetLeaseRecord:
    return DatasetLeaseRecord(
        provider_mode=row.provider_mode,
        owner_run_id=row.owner_run_id,
        revision=row.revision,
        lease_expires_at=row.lease_expires_at,
        heartbeat_at=row.heartbeat_at,
    )


def _decimal_or_none(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _normalize_provider(value: str) -> str:
    return value.strip().lower()
