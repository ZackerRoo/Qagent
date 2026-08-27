from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from qagent.backtesting.experiment import (
    WalkForwardExperimentManifest,
    build_walk_forward_experiment_manifest,
    walk_forward_selection_manifests_semantically_compatible,
)
from qagent.jobs.full_market import full_market_batch_cache_key
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import (
    RankingV3ForwardCandidateRow,
    RankingV3ForwardGateEvidenceRow,
    RankingV3ForwardLedgerRow,
    RankingV3ForwardReleaseProofRow,
    RankingV3ForwardSessionRow,
    RankingV4EvidenceDefinitionRow,
    RankingV4EvidenceInventoryRow,
    RankingV4EvidenceProofRow,
    RankingV4EvidenceReturnRow,
    RankingV4ProspectiveExecutionSummaryRow,
    RankingV4ProspectiveReleasePolicyRow,
    RankingV4ProspectiveReleaseProofRow,
)

VALIDATION_REPORT_MAX_AGE_DAYS = 7
_A_SHARE_TIMEZONE = ZoneInfo("Asia/Shanghai")


def build_validation_center(
    repo: QagentRepository,
    *,
    provider: str,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """Build the read-only status of every validation track.

    This intentionally reads persisted evidence only. It never resolves outcomes,
    starts a validation job, or writes ranking/paper state.
    """

    current_time = generated_at or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_revision = ReplayEvidenceRepository(
        repo.session_factory,
        provider,
    ).current_revision()
    cache = repo.get_recent_scan_result_cache(
        cache_key=full_market_batch_cache_key(provider, True),
        max_age=timedelta(days=3650),
    )
    payload = cache.payload if cache is not None else {}
    factor_shadow = _mapping(payload.get("factor_shadow"))
    paper_calibration = _mapping(payload.get("paper_calibration_shadow"))
    latest_run = _latest_walk_forward_run(repo, provider)
    counts = _evidence_counts(repo)

    tracks = [
        _current_shadow_track(factor_shadow, current_revision, current_time),
        _paper_calibration_track(
            paper_calibration,
            cache.created_at if cache else None,
            current_time,
        ),
        _walk_forward_track(latest_run, current_revision),
        _legacy_v3_track(counts),
        _preregistered_v4_track(counts),
    ]
    walk_forward = next(track for track in tracks if track["key"] == "walk_forward")
    return {
        "schema_version": "validation-center-v1",
        "generated_at": current_time.isoformat(),
        "provider": provider,
        "current_dataset_revision": current_revision,
        "current_path": ["current_shadow", "paper_calibration", "walk_forward"],
        "tracks": tracks,
        "manual_rerun": {
            "available": current_revision > 0,
            "automatic": False,
            "recommended": walk_forward["freshness"] in {"stale", "missing"},
            "method": "POST",
            "path": "/api/walk-forward/jobs",
            "start": latest_run.start_date.isoformat() if latest_run else "2021-11-01",
            "end": latest_run.end_date.isoformat() if latest_run else "2025-12-31",
            "step_sessions": latest_run.rebalance_step_sessions if latest_run else 10,
            "lookback_days": latest_run.lookback_days if latest_run else 400,
            "reason": (
                "rerun_required_for_current_model_or_data"
                if walk_forward["freshness"] == "stale"
                else "no_current_walk_forward_evidence"
                if walk_forward["freshness"] == "missing"
                else "current_walk_forward_evidence_is_fresh"
            ),
        },
        "side_effects": {
            "ranking": "none",
            "selection": "none",
            "allocation": "none",
            "orders": "none",
            "paper_trading": "none",
        },
    }


def _latest_walk_forward_run(repo: QagentRepository, provider: str):
    try:
        runs = repo.list_walk_forward_runs(provider=provider, limit=1)
    except (TypeError, ValueError):
        return None
    return runs[0] if runs else None


def _current_shadow_track(
    report: dict[str, Any],
    current_revision: int,
    generated_at: datetime,
) -> dict[str, object]:
    run = _mapping(report.get("run"))
    health = _mapping(report.get("data_health"))
    run_revision = _integer(run.get("dataset_revision"))
    status = str(health.get("factor_shadow_status") or "unavailable")
    as_of = run.get("signal_date")
    if not report:
        freshness = "missing"
        reason = "full_market_shadow_not_cached"
    elif run_revision is not None and run_revision != current_revision:
        freshness = "stale"
        reason = "dataset_revision_changed"
    elif _is_stale_as_of(as_of, generated_at):
        freshness = "stale"
        reason = "stale_as_of"
    else:
        freshness = "fresh"
        reason = "current_factor_shadow"
    return {
        "key": "current_shadow",
        "role": "current_shadow_ranking_research",
        "active_path": True,
        "status": status,
        "freshness": freshness,
        "sample_count": _integer(run.get("scored_instruments")) or 0,
        "sample_label": "scored_instruments",
        "as_of": as_of,
        "dataset_revision": run_revision,
        "reason": reason,
        "next_action": (
            "wait_for_next_full_market_shadow_report"
            if freshness == "stale"
            else "collect_outcomes_without_changing_formal_ranking"
            if report
            else "run_full_market_scan_to_record_shadow_report"
        ),
    }


def _paper_calibration_track(
    report: dict[str, Any],
    cached_at: datetime | None,
    generated_at: datetime,
) -> dict[str, object]:
    ready = report.get("model_ready") is True
    count = _integer(report.get("benchmark_matched_trade_count")) or 0
    as_of = report.get("decision_date") or (cached_at.isoformat() if cached_at else None)
    stale_as_of = bool(report) and _is_stale_as_of(as_of, generated_at)
    status = (
        "unavailable"
        if not report
        else "stale"
        if stale_as_of
        else "ready"
        if ready
        else "collecting"
    )
    return {
        "key": "paper_calibration",
        "role": "current_model_closed_trade_calibration_shadow",
        "active_path": True,
        "status": status,
        "freshness": "stale" if stale_as_of else "fresh" if report else "missing",
        "sample_count": count,
        "sample_label": "benchmark_matched_closed_trades",
        "minimum_sample_count": _integer(report.get("minimum_training_samples")) or 40,
        "as_of": as_of,
        "reason": (
            "stale_as_of"
            if stale_as_of
            else str(report.get("reason") or "paper_calibration_not_cached")
        ),
        "next_action": (
            "wait_for_next_full_market_shadow_report"
            if stale_as_of
            else "continue_shadow_monitoring"
            if ready
            else "collect_current_cohort_closed_trades"
            if report
            else "wait_for_next_full_market_shadow_report"
        ),
    }


def _walk_forward_track(latest_run, current_revision: int) -> dict[str, object]:
    if latest_run is None:
        return {
            "key": "walk_forward",
            "role": "historical_out_of_sample_validation",
            "active_path": True,
            "status": "inactive",
            "freshness": "missing",
            "sample_count": 0,
            "sample_label": "historical_snapshots",
            "as_of": None,
            "dataset_revision": None,
            "reason": "no_walk_forward_run",
            "next_action": "manual_rerun_after_historical_data_is_ready",
        }

    reason = "fresh"
    freshness = "fresh"
    if latest_run.dataset_revision != current_revision:
        freshness = "stale"
        reason = "dataset_revision_changed"
    else:
        stored_payload = latest_run.payload.get("experiment_manifest")
        try:
            stored = WalkForwardExperimentManifest.model_validate(stored_payload)
            current = build_walk_forward_experiment_manifest(
                provider_mode=latest_run.provider,
                dataset_revision=current_revision,
                start_date=latest_run.start_date,
                end_date=latest_run.end_date,
                rebalance_step_sessions=latest_run.rebalance_step_sessions,
                lookback_days=latest_run.lookback_days,
            )
            if not walk_forward_selection_manifests_semantically_compatible(stored, current):
                freshness = "stale"
                reason = "model_or_selection_revision_changed"
        except (TypeError, ValueError):
            freshness = "stale"
            reason = "legacy_or_invalid_experiment_manifest"
    return {
        "key": "walk_forward",
        "role": "historical_out_of_sample_validation",
        "active_path": True,
        "status": "stale" if freshness == "stale" else latest_run.status,
        "freshness": freshness,
        "sample_count": latest_run.snapshot_count,
        "sample_label": "historical_snapshots",
        "as_of": latest_run.updated_at.isoformat(),
        "run_id": latest_run.run_id,
        "dataset_revision": latest_run.dataset_revision,
        "reason": reason,
        "next_action": (
            "manual_rerun_for_current_model_and_data"
            if freshness == "stale"
            else "retain_as_current_historical_evidence"
        ),
    }


def _legacy_v3_track(counts: dict[str, int]) -> dict[str, object]:
    session_count = counts["v3_sessions"]
    evidence_ready = all(
        counts[key] > 0 for key in ("v3_sessions", "v3_candidates", "v3_evidence", "v3_proofs")
    )
    return {
        "key": "legacy_v3",
        "role": "legacy_forward_ranking_archive",
        "active_path": False,
        "status": "archived" if evidence_ready else "inactive",
        "freshness": "historical" if evidence_ready else "missing",
        "sample_count": session_count,
        "sample_label": "forward_sessions",
        "counts": {
            "ledgers": counts["v3_ledgers"],
            "sessions": session_count,
            "candidates": counts["v3_candidates"],
            "gate_evidence": counts["v3_evidence"],
            "release_proofs": counts["v3_proofs"],
        },
        "reason": "legacy_evidence_available" if evidence_ready else "no_forward_evidence",
        "next_action": "retain_for_audit_only",
    }


def _preregistered_v4_track(counts: dict[str, int]) -> dict[str, object]:
    completed = all(
        counts[key] > 0
        for key in ("v4_returns", "v4_execution_summaries", "v4_policies", "v4_release_proofs")
    )
    status = "archived" if completed else "collecting" if counts["v4_definitions"] else "inactive"
    return {
        "key": "preregistered_v4",
        "role": "preregistered_prospective_shadow_archive",
        "active_path": False,
        "status": status,
        "freshness": "historical"
        if completed
        else "incomplete"
        if counts["v4_definitions"]
        else "missing",
        "sample_count": counts["v4_returns"],
        "sample_label": "common_date_returns",
        "counts": {
            "definitions": counts["v4_definitions"],
            "inventories": counts["v4_inventories"],
            "returns": counts["v4_returns"],
            "evidence_proofs": counts["v4_evidence_proofs"],
            "release_policies": counts["v4_policies"],
            "execution_summaries": counts["v4_execution_summaries"],
            "release_proofs": counts["v4_release_proofs"],
        },
        "reason": "prospective_evidence_complete"
        if completed
        else "prospective_returns_or_release_evidence_missing",
        "next_action": "continue_preregistered_collection_only"
        if counts["v4_definitions"]
        else "retain_inactive",
    }


def _evidence_counts(repo: QagentRepository) -> dict[str, int]:
    models = {
        "v3_ledgers": RankingV3ForwardLedgerRow,
        "v3_sessions": RankingV3ForwardSessionRow,
        "v3_candidates": RankingV3ForwardCandidateRow,
        "v3_evidence": RankingV3ForwardGateEvidenceRow,
        "v3_proofs": RankingV3ForwardReleaseProofRow,
        "v4_definitions": RankingV4EvidenceDefinitionRow,
        "v4_inventories": RankingV4EvidenceInventoryRow,
        "v4_returns": RankingV4EvidenceReturnRow,
        "v4_evidence_proofs": RankingV4EvidenceProofRow,
        "v4_policies": RankingV4ProspectiveReleasePolicyRow,
        "v4_execution_summaries": RankingV4ProspectiveExecutionSummaryRow,
        "v4_release_proofs": RankingV4ProspectiveReleaseProofRow,
    }
    with repo.session_factory() as session:
        return {
            key: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for key, model in models.items()
        }


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_stale_as_of(value: object, generated_at: datetime) -> bool:
    as_of = _as_of_date(value)
    if as_of is None:
        return False
    market_date = generated_at.astimezone(_A_SHARE_TIMEZONE).date()
    return (market_date - as_of).days > VALIDATION_REPORT_MAX_AGE_DAYS


def _as_of_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(_A_SHARE_TIMEZONE).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if "T" in text or " " in text:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.astimezone(_A_SHARE_TIMEZONE).date() if parsed.tzinfo else parsed.date()
        return date.fromisoformat(text)
    except ValueError:
        return None
