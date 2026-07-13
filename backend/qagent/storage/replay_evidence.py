from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pydantic import BaseModel
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased, sessionmaker

from qagent.historical_evidence.models import (
    HistoricalCorporateAction,
    HistoricalCorporateActionCoverage,
    HistoricalEvidenceBundle,
    HistoricalFeeRule,
    HistoricalIndexMembership,
    HistoricalIndustrySnapshot,
    HistoricalInstrumentProfile,
    HistoricalInstrumentRuleMetadata,
    HistoricalLifecycleManifest,
    HistoricalReplayBar,
    HistoricalTerminalSettlement,
    HistoricalTradabilityPoint,
    HistoricalTradingRule,
    HistoricalUniverseManifest,
    normalize_and_validate_historical_profile,
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
    HistoricalInstrumentRuleMetadataRow,
    HistoricalLifecycleManifestRow,
    HistoricalReplayBarRow,
    HistoricalReplayUniverseMemberRow,
    HistoricalFeeRuleRow,
    HistoricalTerminalSettlementRow,
    HistoricalTradabilityRow,
    HistoricalTradingRuleRow,
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


ActionCoverageRecord = HistoricalCorporateActionCoverage


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
            [
                "provider_mode",
                "instrument_id",
                "trade_date",
                "source_provider",
                "dataset_revision",
            ],
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
            [
                "provider_mode",
                "instrument_id",
                "action_id",
                "source_provider",
                "dataset_revision",
            ],
            requested,
        )

    def upsert_fundamentals(
        self,
        snapshots: Sequence[FundamentalSnapshot],
        *,
        revision: int | None = None,
    ) -> int:
        now = self._now()
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
                "cached_at": now,
                "updated_at": now,
            }
            for item in snapshots
        ]
        return self._write_source_rows(
            FundamentalSnapshotRow,
            records,
            [
                "provider_mode",
                "instrument_id",
                "as_of_date",
                "source_provider",
                "dataset_revision",
            ],
            revision,
            ignored_retry_fields={"cached_at", "updated_at"},
        )

    def upsert_point_in_time_evidence(
        self,
        bundle: HistoricalEvidenceBundle,
        *,
        revision: int | None = None,
    ) -> dict[str, int]:
        _validate_ready_index_snapshot_bundle(bundle)
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
                [
                    "provider_mode",
                    "instrument_id",
                    "trade_date",
                    "source_provider",
                    "dataset_revision",
                ],
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
                [
                    "provider_mode",
                    "instrument_id",
                    "snapshot_date",
                    "source_provider",
                    "dataset_revision",
                ],
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
                [
                    "provider_mode",
                    "index_id",
                    "snapshot_date",
                    "source_provider",
                    "dataset_revision",
                ],
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
                [
                    "provider_mode",
                    "index_id",
                    "snapshot_date",
                    "instrument_id",
                    "source_provider",
                    "dataset_revision",
                ],
            ),
        ]
        names = [
            "tradability",
            "profiles",
            "industries",
            "index_snapshots",
            "index_memberships",
        ]
        if not any(records for _, records, _ in groups):
            return dict.fromkeys(names, 0)
        with self._immediate_session() as session:
            write_revision, is_retry = self._prepare_source_write(session, revision)
            result: dict[str, int] = {}
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
        identity_errors: list[str] = []
        normalized_profiles: list[HistoricalInstrumentProfile] = []
        for item in profiles:
            normalized, errors = normalize_and_validate_historical_profile(
                item,
                manifest.effective_through,
            )
            identity_errors.extend(errors)
            if not errors:
                normalized_profiles.append(normalized)
        records = [
            {
                "provider_mode": self.provider_mode,
                "instrument_id": item.instrument_id,
                "snapshot_date": item.snapshot_date,
                "listing_date": item.listing_date,
                "delisting_date": item.delisting_date,
                "security_type": (
                    item.security_type.strip() if item.security_type is not None else None
                ),
                "listing_status": item.listing_status,
                "source_provider": _normalize_provider(item.provider),
                "fetched_at": manifest.fetched_at,
            }
            for item in normalized_profiles
        ]
        with self._immediate_session() as session:
            write_revision, is_retry = self._prepare_source_write(session, manifest.source_revision)
            for record in records:
                record["dataset_revision"] = write_revision
            profile_keys = [
                "provider_mode",
                "instrument_id",
                "snapshot_date",
                "dataset_revision",
            ]
            deduplicated = _deduplicate(records, profile_keys)
            if is_retry:
                _verify_immutable_rows(
                    session,
                    HistoricalInstrumentProfileRow,
                    deduplicated,
                    profile_keys,
                    write_revision,
                )
            else:
                _upsert_chunks(
                    session,
                    HistoricalInstrumentProfileRow,
                    deduplicated,
                    profile_keys,
                )
            stored_count = (
                session.scalar(
                    select(
                        func.count(func.distinct(HistoricalInstrumentProfileRow.instrument_id))
                    ).where(
                        HistoricalInstrumentProfileRow.provider_mode == self.provider_mode,
                        HistoricalInstrumentProfileRow.dataset_revision == write_revision,
                    )
                )
                or 0
            )
            errors = [manifest.error] if manifest.error else []
            if manifest.status.strip().lower() != "ready" and not manifest.error:
                errors.append(f"provider manifest status is {manifest.status}")
            if manifest.expected_count is None:
                errors.append("expected_count is unknown")
            elif manifest.expected_count <= 0:
                errors.append("expected_count must be positive")
            elif manifest.expected_count != stored_count:
                errors.append(
                    f"count mismatch: expected={manifest.expected_count}, stored={stored_count}"
                )
            errors.extend(identity_errors)
            normalized_errors = list(dict.fromkeys(errors))
            status = "ready" if not normalized_errors else "partial"
            manifest_records = [
                {
                    **manifest.model_dump(
                        exclude={"provider_mode", "status", "stored_count", "error"}
                    ),
                    "provider_mode": self.provider_mode,
                    "status": status,
                    "stored_count": stored_count,
                    "error": "; ".join(normalized_errors) or None,
                }
            ]
            if is_retry:
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
            [
                "provider_mode",
                "instrument_id",
                "start_date",
                "end_date",
                "source_provider",
                "dataset_revision",
            ],
            revision,
            ignored_retry_fields={"fetched_at"},
        )

    def upsert_trading_rules(self, rules: Sequence[HistoricalTradingRule]) -> int:
        records = [item.model_dump() for item in rules]
        keys = ["rule_set_version", "limit_rule_key", "effective_from"]
        with self._immediate_session() as session:
            return _write_immutable_reference_rows(session, HistoricalTradingRuleRow, records, keys)

    def upsert_instrument_rule_metadata(
        self, metadata: Sequence[HistoricalInstrumentRuleMetadata]
    ) -> int:
        records = [
            {
                **item.model_dump(exclude={"provider_mode", "source_provider"}),
                "provider_mode": self.provider_mode,
                "source_provider": _normalize_provider(item.source_provider),
            }
            for item in metadata
            if self._matches_provider(item.provider_mode)
        ]
        keys = [
            "provider_mode",
            "instrument_id",
            "effective_from",
            "rule_set_version",
            "fee_schedule_version",
        ]
        with self._immediate_session() as session:
            return _write_immutable_reference_rows(
                session,
                HistoricalInstrumentRuleMetadataRow,
                records,
                keys,
                ignored_fields={"fetched_at"},
            )

    def upsert_fee_rules(self, rules: Sequence[HistoricalFeeRule]) -> int:
        records = [item.model_dump() for item in rules]
        keys = ["fee_schedule_version", "fee_rule_key", "effective_from", "side"]
        with self._immediate_session() as session:
            return _write_immutable_reference_rows(session, HistoricalFeeRuleRow, records, keys)

    def upsert_terminal_settlements(
        self,
        settlements: Sequence[HistoricalTerminalSettlement],
        *,
        revision: int | None = None,
    ) -> int:
        records = [
            {
                **item.model_dump(exclude={"provider_mode", "dataset_revision"}),
                "provider_mode": self.provider_mode,
                "source_provider": _normalize_provider(item.source_provider),
            }
            for item in settlements
            if self._matches_provider(item.provider_mode)
        ]
        requested = self._model_revision(settlements, revision)
        return self._write_source_rows(
            HistoricalTerminalSettlementRow,
            records,
            [
                "provider_mode",
                "instrument_id",
                "effective_date",
                "settlement_type",
            ],
            requested,
            ignored_retry_fields={"fetched_at"},
        )

    def trading_rule_on(
        self,
        *,
        rule_set_version: str,
        limit_rule_key: str,
        trade_date: date,
    ) -> HistoricalTradingRule:
        with self.session_factory() as session:
            row = session.scalar(
                select(HistoricalTradingRuleRow)
                .where(
                    HistoricalTradingRuleRow.rule_set_version == rule_set_version,
                    HistoricalTradingRuleRow.limit_rule_key == limit_rule_key,
                    HistoricalTradingRuleRow.effective_from <= trade_date,
                    (
                        HistoricalTradingRuleRow.effective_to.is_(None)
                        | (HistoricalTradingRuleRow.effective_to >= trade_date)
                    ),
                )
                .order_by(HistoricalTradingRuleRow.effective_from.desc())
                .limit(1)
            )
        if row is None:
            raise ReplayEvidenceUnavailable(
                f"trading rule {rule_set_version}/{limit_rule_key} is missing on "
                f"{trade_date.isoformat()}"
            )
        return HistoricalTradingRule.model_validate(_row_dict(row))

    def trading_rule_for(
        self,
        *,
        rule_set_version: str,
        market: str,
        board: str,
        security_type: str,
        is_st: bool,
        trade_date: date,
    ) -> HistoricalTradingRule:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(HistoricalTradingRuleRow)
                    .where(
                        HistoricalTradingRuleRow.rule_set_version
                        == rule_set_version,
                        HistoricalTradingRuleRow.market == market,
                        HistoricalTradingRuleRow.board == board,
                        HistoricalTradingRuleRow.security_type == security_type,
                        HistoricalTradingRuleRow.is_st == is_st,
                        HistoricalTradingRuleRow.effective_from <= trade_date,
                        (
                            HistoricalTradingRuleRow.effective_to.is_(None)
                            | (HistoricalTradingRuleRow.effective_to >= trade_date)
                        ),
                    )
                    .order_by(HistoricalTradingRuleRow.effective_from.desc())
                )
            )
        if len(rows) != 1:
            raise ReplayEvidenceUnavailable(
                "expected one trading rule for "
                f"{rule_set_version}/{market}/{board}/{security_type}/st={is_st} "
                f"on {trade_date.isoformat()}, found {len(rows)}"
            )
        return HistoricalTradingRule.model_validate(_row_dict(rows[0]))

    def fee_rules_on(
        self,
        *,
        fee_schedule_version: str,
        fee_rule_key: str,
        trade_date: date,
    ) -> list[HistoricalFeeRule]:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(HistoricalFeeRuleRow)
                    .where(
                        HistoricalFeeRuleRow.fee_schedule_version == fee_schedule_version,
                        HistoricalFeeRuleRow.fee_rule_key == fee_rule_key,
                        HistoricalFeeRuleRow.effective_from <= trade_date,
                        (
                            HistoricalFeeRuleRow.effective_to.is_(None)
                            | (HistoricalFeeRuleRow.effective_to >= trade_date)
                        ),
                    )
                    .order_by(HistoricalFeeRuleRow.side)
                )
            )
        if not rows:
            raise ReplayEvidenceUnavailable(
                f"fee rule {fee_schedule_version}/{fee_rule_key} is missing on "
                f"{trade_date.isoformat()}"
            )
        return [HistoricalFeeRule.model_validate(_row_dict(row)) for row in rows]

    def instrument_rule_metadata_on(
        self, instrument_id: str, trade_date: date
    ) -> HistoricalInstrumentRuleMetadata:
        with self.session_factory() as session:
            row = session.scalar(
                select(HistoricalInstrumentRuleMetadataRow)
                .where(
                    HistoricalInstrumentRuleMetadataRow.provider_mode == self.provider_mode,
                    HistoricalInstrumentRuleMetadataRow.instrument_id == instrument_id,
                    HistoricalInstrumentRuleMetadataRow.effective_from <= trade_date,
                    (
                        HistoricalInstrumentRuleMetadataRow.effective_to.is_(None)
                        | (HistoricalInstrumentRuleMetadataRow.effective_to >= trade_date)
                    ),
                )
                .order_by(HistoricalInstrumentRuleMetadataRow.effective_from.desc())
                .limit(1)
            )
        if row is None:
            raise ReplayEvidenceUnavailable(
                f"instrument rule metadata is missing for {instrument_id} on "
                f"{trade_date.isoformat()}"
            )
        return HistoricalInstrumentRuleMetadata.model_validate(_row_dict(row))

    def terminal_settlements(
        self,
        instrument_ids: Sequence[str],
        start: date,
        end: date,
        revision: int,
    ) -> list[HistoricalTerminalSettlement]:
        if not instrument_ids:
            return []
        with self.session_factory() as session:
            ranked = (
                select(
                    HistoricalTerminalSettlementRow,
                    func.row_number()
                    .over(
                        partition_by=(
                            HistoricalTerminalSettlementRow.instrument_id,
                            HistoricalTerminalSettlementRow.effective_date,
                            HistoricalTerminalSettlementRow.settlement_type,
                        ),
                        order_by=HistoricalTerminalSettlementRow.dataset_revision.desc(),
                    )
                    .label("revision_rank"),
                )
                .where(
                    HistoricalTerminalSettlementRow.provider_mode == self.provider_mode,
                    HistoricalTerminalSettlementRow.instrument_id.in_(instrument_ids),
                    HistoricalTerminalSettlementRow.effective_date >= start,
                    HistoricalTerminalSettlementRow.effective_date <= end,
                    HistoricalTerminalSettlementRow.dataset_revision <= revision,
                )
                .subquery()
            )
            row_alias = aliased(HistoricalTerminalSettlementRow, ranked)
            rows = list(
                session.scalars(
                    select(row_alias)
                    .where(ranked.c.revision_rank == 1)
                    .order_by(row_alias.effective_date, row_alias.instrument_id)
                )
            )
        return [HistoricalTerminalSettlement.model_validate(_row_dict(row)) for row in rows]

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
            ranked = (
                select(
                    HistoricalReplayBarRow,
                    func.row_number()
                    .over(
                        partition_by=(
                            HistoricalReplayBarRow.instrument_id,
                            HistoricalReplayBarRow.trade_date,
                        ),
                        order_by=(
                            HistoricalReplayBarRow.dataset_revision.desc(),
                            HistoricalReplayBarRow.source_provider,
                        ),
                    )
                    .label("revision_rank"),
                )
                .where(
                    HistoricalReplayBarRow.provider_mode == self.provider_mode,
                    HistoricalReplayBarRow.instrument_id.in_(instrument_ids),
                    HistoricalReplayBarRow.trade_date >= start,
                    HistoricalReplayBarRow.trade_date <= end,
                    HistoricalReplayBarRow.dataset_revision <= revision,
                )
                .subquery()
            )
            row_alias = aliased(HistoricalReplayBarRow, ranked)
            rows = list(
                session.scalars(
                    select(row_alias)
                    .where(ranked.c.revision_rank == 1)
                    .order_by(row_alias.trade_date, row_alias.instrument_id)
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
            ranked = (
                select(
                    FundamentalSnapshotRow,
                    func.row_number()
                    .over(
                        partition_by=FundamentalSnapshotRow.instrument_id,
                        order_by=(
                            FundamentalSnapshotRow.as_of_date.desc(),
                            FundamentalSnapshotRow.dataset_revision.desc(),
                            FundamentalSnapshotRow.source_provider,
                        ),
                    )
                    .label("revision_rank"),
                )
                .where(
                    FundamentalSnapshotRow.provider_mode == self.provider_mode,
                    FundamentalSnapshotRow.instrument_id.in_(instrument_ids),
                    FundamentalSnapshotRow.as_of_date <= decision_date,
                    FundamentalSnapshotRow.dataset_revision <= revision,
                )
                .subquery()
            )
            row_alias = aliased(FundamentalSnapshotRow, ranked)
            rows = list(session.scalars(select(row_alias).where(ranked.c.revision_rank == 1)))
        return {row.instrument_id: _fundamental_from_row(row) for row in rows}

    def industries_as_of(
        self,
        instrument_ids: Sequence[str],
        decision_date: date,
        revision: int,
    ) -> dict[str, HistoricalIndustrySnapshot]:
        if not instrument_ids:
            return {}
        with self.session_factory() as session:
            ranked = (
                select(
                    HistoricalIndustrySnapshotRow,
                    func.row_number()
                    .over(
                        partition_by=HistoricalIndustrySnapshotRow.instrument_id,
                        order_by=(
                            HistoricalIndustrySnapshotRow.snapshot_date.desc(),
                            HistoricalIndustrySnapshotRow.dataset_revision.desc(),
                            HistoricalIndustrySnapshotRow.source_provider,
                        ),
                    )
                    .label("revision_rank"),
                )
                .where(
                    HistoricalIndustrySnapshotRow.provider_mode == self.provider_mode,
                    HistoricalIndustrySnapshotRow.instrument_id.in_(instrument_ids),
                    HistoricalIndustrySnapshotRow.snapshot_date <= decision_date,
                    HistoricalIndustrySnapshotRow.dataset_revision <= revision,
                )
                .subquery()
            )
            row_alias = aliased(HistoricalIndustrySnapshotRow, ranked)
            rows = list(session.scalars(select(row_alias).where(ranked.c.revision_rank == 1)))
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
            ranked = (
                select(
                    HistoricalIndexSnapshotRow,
                    func.row_number()
                    .over(
                        partition_by=HistoricalIndexSnapshotRow.index_id,
                        order_by=(
                            HistoricalIndexSnapshotRow.snapshot_date.desc(),
                            HistoricalIndexSnapshotRow.dataset_revision.desc(),
                            HistoricalIndexSnapshotRow.source_provider,
                        ),
                    )
                    .label("revision_rank"),
                )
                .where(
                    HistoricalIndexSnapshotRow.provider_mode == self.provider_mode,
                    HistoricalIndexSnapshotRow.status == "ready",
                    HistoricalIndexSnapshotRow.snapshot_date <= decision_date,
                    HistoricalIndexSnapshotRow.dataset_revision <= revision,
                )
                .subquery()
            )
            snapshot_alias = aliased(HistoricalIndexSnapshotRow, ranked)
            snapshots = list(
                session.scalars(
                    select(snapshot_alias)
                    .where(ranked.c.revision_rank == 1)
                    .order_by(snapshot_alias.index_id)
                )
            )
            identities = [
                (
                    snapshot.index_id,
                    snapshot.snapshot_date,
                    snapshot.source_provider,
                    snapshot.dataset_revision,
                )
                for snapshot in snapshots
            ]
            if not identities:
                return result
            identity_columns = (
                HistoricalIndexMembershipRow.index_id,
                HistoricalIndexMembershipRow.snapshot_date,
                HistoricalIndexMembershipRow.source_provider,
                HistoricalIndexMembershipRow.dataset_revision,
            )
            stored_counts = {}
            for identity_chunk in _chunks(identities, 224):
                stored_counts.update(
                    {
                        (
                            index_id,
                            snapshot_date,
                            source_provider,
                            dataset_revision,
                        ): count
                        for index_id, snapshot_date, source_provider, dataset_revision, count in session.execute(
                            select(*identity_columns, func.count())
                            .where(
                                HistoricalIndexMembershipRow.provider_mode == self.provider_mode,
                                tuple_(*identity_columns).in_(identity_chunk),
                            )
                            .group_by(*identity_columns)
                        )
                    }
                )
            for snapshot, identity in zip(snapshots, identities, strict=True):
                stored = stored_counts.get(identity, 0)
                if stored != snapshot.member_count:
                    raise ReplayEvidenceUnavailable(
                        f"ready index snapshot {snapshot.index_id} is incomplete: "
                        f"member_count={snapshot.member_count}, stored={stored}"
                    )
            rows = []
            requested_ids = list(dict.fromkeys(instrument_ids))
            for instrument_chunk in _chunks(requested_ids, 400):
                identity_chunk_size = max(1, (899 - len(instrument_chunk)) // 4)
                for identity_chunk in _chunks(identities, identity_chunk_size):
                    rows.extend(
                        session.scalars(
                            select(HistoricalIndexMembershipRow)
                            .where(
                                HistoricalIndexMembershipRow.provider_mode == self.provider_mode,
                                tuple_(*identity_columns).in_(identity_chunk),
                                HistoricalIndexMembershipRow.instrument_id.in_(instrument_chunk),
                            )
                            .order_by(
                                HistoricalIndexMembershipRow.instrument_id,
                                HistoricalIndexMembershipRow.index_id,
                            )
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
            ranked = (
                select(
                    HistoricalTradabilityRow,
                    func.row_number()
                    .over(
                        partition_by=HistoricalTradabilityRow.instrument_id,
                        order_by=(
                            HistoricalTradabilityRow.dataset_revision.desc(),
                            HistoricalTradabilityRow.source_provider,
                        ),
                    )
                    .label("revision_rank"),
                )
                .where(
                    HistoricalTradabilityRow.provider_mode == self.provider_mode,
                    HistoricalTradabilityRow.instrument_id.in_(instrument_ids),
                    HistoricalTradabilityRow.trade_date == decision_date,
                    HistoricalTradabilityRow.dataset_revision <= revision,
                )
                .subquery()
            )
            row_alias = aliased(HistoricalTradabilityRow, ranked)
            rows = list(session.scalars(select(row_alias).where(ranked.c.revision_rank == 1)))
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

    def recoverable_lifecycle_profiles(
        self,
        effective_through: date,
    ) -> list[HistoricalInstrumentProfile]:
        """Return validated BaoStock lifecycle facts left by an older revision.

        These rows contain listing identity, not historical recommendations. They are
        revalidated as of the requested cutoff before a new immutable manifest is
        created, so current listing status is never backdated unchanged.
        """
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(HistoricalInstrumentProfileRow)
                    .where(
                        HistoricalInstrumentProfileRow.provider_mode
                        == self.provider_mode,
                        HistoricalInstrumentProfileRow.source_provider
                        == "baostock",
                    )
                    .order_by(
                        HistoricalInstrumentProfileRow.dataset_revision.desc(),
                        HistoricalInstrumentProfileRow.snapshot_date.desc(),
                    )
                )
            )
        latest_rows = {}
        for row in rows:
            latest_rows.setdefault(row.instrument_id, row)
        recovered = []
        for row in latest_rows.values():
            if row.listing_date is None or row.listing_date > effective_through:
                continue
            delisted_by_cutoff = (
                row.delisting_date is not None
                and row.delisting_date <= effective_through
            )
            profile, errors = normalize_and_validate_historical_profile(
                HistoricalInstrumentProfile(
                    instrument_id=row.instrument_id,
                    snapshot_date=effective_through,
                    listing_date=row.listing_date,
                    delisting_date=(
                        row.delisting_date if delisted_by_cutoff else None
                    ),
                    security_type=row.security_type,
                    listing_status="delisted" if delisted_by_cutoff else "active",
                    provider="baostock_cached_lifecycle_recovery",
                ),
                effective_through,
            )
            if not errors:
                recovered.append(profile)
        return sorted(recovered, key=lambda item: item.instrument_id)

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
                item.instrument_id
                for item in inventory
                if not item.security_type or not item.security_type.strip()
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
                        HistoricalReplayUniverseMemberRow.provider_mode == self.provider_mode,
                        HistoricalReplayUniverseMemberRow.snapshot_date == decision_date,
                        HistoricalReplayUniverseMemberRow.source_revision == revision,
                    )
                    .order_by(HistoricalReplayUniverseMemberRow.instrument_id)
                )
            )
            if existing_manifest is not None and existing_manifest.owner_run_id != owner_run_id:
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
                        HistoricalReplayUniverseMemberRow.provider_mode == self.provider_mode,
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
            ranked = (
                select(
                    HistoricalCorporateActionCoverageRow,
                    func.row_number()
                    .over(
                        partition_by=HistoricalCorporateActionCoverageRow.instrument_id,
                        order_by=(
                            HistoricalCorporateActionCoverageRow.dataset_revision.desc(),
                            HistoricalCorporateActionCoverageRow.source_provider,
                        ),
                    )
                    .label("revision_rank"),
                )
                .where(
                    HistoricalCorporateActionCoverageRow.provider_mode == self.provider_mode,
                    HistoricalCorporateActionCoverageRow.instrument_id.in_(instrument_ids),
                    HistoricalCorporateActionCoverageRow.start_date == start,
                    HistoricalCorporateActionCoverageRow.end_date == end,
                    HistoricalCorporateActionCoverageRow.dataset_revision <= revision,
                )
                .subquery()
            )
            row_alias = aliased(HistoricalCorporateActionCoverageRow, ranked)
            rows = list(session.scalars(select(row_alias).where(ranked.c.revision_rank == 1)))
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

    def acquire_dataset_lease(self, owner_run_id: str | None = None) -> DatasetLeaseRecord:
        owner = owner_run_id or self._require_owner()
        now = self._now()
        with self._immediate_session() as session:
            self._ensure_run_active(owner)
            revision = self._revision_row(session).revision
            lease = session.get(HistoricalDatasetLeaseRow, self.provider_mode)
            if lease is not None:
                owner_status = self._run_status_lookup(lease.owner_run_id)
            if lease is not None and lease.owner_run_id != owner:
                stale = lease.heartbeat_at <= now - STALE_AFTER
                if stale and owner_status in TERMINAL_RUN_STATUSES:
                    session.delete(lease)
                    session.flush()
                    lease = None
                else:
                    raise DatasetLeaseBusy(
                        f"dataset lease for {self.provider_mode} is owned by {lease.owner_run_id}"
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

    def renew_dataset_lease(self, owner_run_id: str | None = None) -> DatasetLeaseRecord:
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
                    f"dataset lease for {self.provider_mode} is owned by {lease.owner_run_id}"
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
        deduplicated = _deduplicate(
            records, [key for key in index_elements if key != "dataset_revision"]
        )
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

    def _prepare_source_write(self, session: Session, revision: int | None) -> tuple[int, bool]:
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
        self._ensure_run_active(owner_run_id)
        if lease.lease_expires_at <= self._now():
            raise DatasetLeaseBusy(f"dataset lease for {self.provider_mode} expired; reacquire it")
        expected = lease.revision if revision is None else revision
        if lease.revision != expected or current != expected:
            raise StaleCheckpointRevision(
                f"leased revision {lease.revision} no longer matches current revision {current}"
            )
        return lease

    def _ensure_run_active(self, owner_run_id: str) -> None:
        if self._run_status_lookup(owner_run_id) in TERMINAL_RUN_STATUSES:
            raise DatasetLeaseBusy(
                f"terminal run {owner_run_id} cannot acquire or renew the "
                f"{self.provider_mode} lease"
            )

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
                HistoricalLifecycleManifestRow.source_revision <= revision,
            )
            .order_by(HistoricalLifecycleManifestRow.source_revision.desc())
            .limit(1)
        )
        if manifest is None:
            raise ReplayEvidenceUnavailable(
                f"ready lifecycle inventory is missing for revision {revision}"
            )
        if manifest.status != "ready":
            detail = manifest.error or "manifest is not ready"
            raise ReplayEvidenceUnavailable(
                "lifecycle inventory is incomplete for revision "
                f"{manifest.source_revision}: {detail}"
            )
        if manifest.expected_count is None or manifest.expected_count != manifest.stored_count:
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
                    HistoricalInstrumentProfileRow.dataset_revision == manifest.source_revision,
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
    chunk_size = min(chunk_size, max(1, 900 // len(records[0])))
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


def _write_immutable_reference_rows(
    session: Session,
    model,
    records: list[dict[str, object]],
    index_elements: list[str],
    *,
    ignored_fields: set[str] | None = None,
) -> int:
    deduplicated = _deduplicate(records, index_elements)
    if not deduplicated:
        return 0
    identities = [tuple(item[key] for key in index_elements) for item in deduplicated]
    identity_columns = tuple(getattr(model, key) for key in index_elements)
    existing = {}
    chunk_size = max(1, 900 // len(index_elements))
    for identity_chunk in _chunks(identities, chunk_size):
        existing.update(
            {
                tuple(getattr(row, key) for key in index_elements): row
                for row in session.scalars(
                    select(model).where(tuple_(*identity_columns).in_(identity_chunk))
                )
            }
        )
    inserts = []
    ignored = ignored_fields or set()
    for record in deduplicated:
        identity = tuple(record[key] for key in index_elements)
        row = existing.get(identity)
        if row is None:
            inserts.append(record)
            continue
        for key, incoming in record.items():
            if key in ignored:
                continue
            if _canonical_value(getattr(row, key)) != _canonical_value(incoming):
                raise ImmutableRevisionConflict(
                    f"versioned reference data is immutable; payload differs for {key}"
                )
    _upsert_chunks(session, model, inserts, index_elements)
    return len(deduplicated)


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


def _chunks(values: Sequence, chunk_size: int) -> Iterator[Sequence]:
    for offset in range(0, len(values), chunk_size):
        yield values[offset : offset + chunk_size]


def _records_match(first: dict[str, object], second: dict[str, object]) -> bool:
    if first.keys() != second.keys():
        return False
    return all(_canonical_value(first[key]) == _canonical_value(second[key]) for key in first)


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
    identities = [tuple(record[key] for key in index_elements) for record in records]
    rows_by_identity = {}
    chunk_size = max(1, 900 // len(index_elements))
    identity_columns = tuple(getattr(model, key) for key in index_elements)
    for offset in range(0, len(identities), chunk_size):
        chunk = identities[offset : offset + chunk_size]
        rows = session.scalars(select(model).where(tuple_(*identity_columns).in_(chunk)))
        rows_by_identity.update(
            {tuple(getattr(row, key) for key in index_elements): row for row in rows}
        )
    for record in records:
        identity = tuple(record[key] for key in index_elements)
        row = rows_by_identity.get(identity)
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


def _validate_ready_index_snapshot_bundle(bundle: HistoricalEvidenceBundle) -> None:
    membership_ids: dict[tuple[str, date, str], set[str]] = {}
    for membership in bundle.index_memberships:
        key = (
            membership.index_id,
            membership.snapshot_date,
            _normalize_provider(membership.provider),
        )
        membership_ids.setdefault(key, set()).add(membership.instrument_id)
    for snapshot in bundle.index_snapshots:
        if snapshot.status != "ready":
            continue
        key = (
            snapshot.index_id,
            snapshot.snapshot_date,
            _normalize_provider(snapshot.provider),
        )
        stored = len(membership_ids.get(key, set()))
        if stored != snapshot.member_count:
            raise ReplayEvidenceUnavailable(
                f"ready index snapshot {snapshot.index_id} is incomplete: "
                f"member_count={snapshot.member_count}, stored={stored}"
            )
