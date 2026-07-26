from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Protocol

from qagent.backtesting.experiment import (
    WalkForwardExperimentManifest,
    walk_forward_manifest_digest_is_valid,
)
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardIdentity,
    RankingV3HistoricalGatesInput,
    RankingV3PBOInput,
    stable_digest,
)
from qagent.backtesting.ranking_v3_pbo import (
    RankingV3DatedModelReturn,
    evaluate_ranking_v3_cscv_pbo,
)


class RankingV3WalkForwardRunRepository(Protocol):
    def get_walk_forward_run(self, run_id: str) -> object | None: ...


def ranking_v3_data_revision(run: object) -> str:
    ranking_v3 = _ranking_v3_payload(run)
    experiment_manifest = _experiment_manifest_payload(run)
    binding_digest = stable_digest(
        {
            "schema_version": "ranking-v3-data-revision-v2",
            "run_id": _text(getattr(run, "run_id", None)),
            "reproducibility_digest": _text(
                getattr(run, "reproducibility_digest", None)
            ),
            "artifact_digest": _text(
                ranking_v3.get("forward_scoring_artifact_digest")
            ),
            "experiment_digest": _text(experiment_manifest.get("experiment_digest")),
            "research_source_digest": _text(
                experiment_manifest.get("research_source_digest")
            ),
            "execution_rules_digest": _text(
                experiment_manifest.get("execution_rules_digest")
            ),
            "strategy_registry_digest": _text(
                experiment_manifest.get("strategy_registry_digest")
            ),
            "ranking_v3_protocol_digest": _text(
                experiment_manifest.get("ranking_v3_protocol_digest")
            ),
        }
    )
    return (
        "walk-forward-v3:"
        f"{_text(getattr(run, 'provider', None))}:"
        f"{int(getattr(run, 'dataset_revision', 0))}:"
        f"{binding_digest}"
    )


def ranking_v3_historical_gate_results(run: object) -> dict[str, str]:
    ranking_v3 = _ranking_v3_payload(run)
    criteria = ranking_v3.get("criteria") if ranking_v3 else None
    if not isinstance(criteria, list):
        return {}
    return {
        key: status
        for item in criteria
        if isinstance(item, Mapping)
        and (key := _text(item.get("key")))
        and key not in {"pbo", "prospective_shadow"}
        and (status := _text(item.get("status"))) in {"pass", "fail", "insufficient"}
    }


def ranking_v3_historical_source_digest(run: object) -> str:
    return stable_digest(
        {
            "schema_version": "ranking-v3-historical-authority-v1",
            "run_id": _text(getattr(run, "run_id", None)),
            "provider": _text(getattr(run, "provider", None)),
            "status": _text(getattr(run, "status", None)),
            "dataset_revision": int(getattr(run, "dataset_revision", 0)),
            "reproducibility_digest": _text(getattr(run, "reproducibility_digest", None)),
            "ranking_v3": _ranking_v3_payload(run),
            "gate_results": ranking_v3_historical_gate_results(run),
        }
    )


def ranking_v3_pbo_source_digest(run: object) -> str:
    ranking_v3 = _ranking_v3_payload(run)
    return stable_digest(
        {
            "schema_version": "ranking-v3-pbo-authority-v1",
            "run_id": _text(getattr(run, "run_id", None)),
            "provider": _text(getattr(run, "provider", None)),
            "status": _text(getattr(run, "status", None)),
            "dataset_revision": int(getattr(run, "dataset_revision", 0)),
            "reproducibility_digest": _text(getattr(run, "reproducibility_digest", None)),
            "pbo_evidence": (ranking_v3.get("pbo_evidence") if ranking_v3 else None),
        }
    )


class RankingV3RepositoryEvidenceAuthority:
    """Revalidate forward-gate evidence against the current server-owned run."""

    def __init__(self, repository: RankingV3WalkForwardRunRepository):
        self.repository = repository

    def verify_historical_gates(
        self,
        identity: RankingV3ForwardIdentity,
        evidence: RankingV3HistoricalGatesInput,
    ) -> bool:
        run = self.repository.get_walk_forward_run(evidence.validation_run_id)
        ranking_v3 = _ranking_v3_payload(run)
        if not _run_matches_identity(run, ranking_v3, identity):
            return False
        expected_results = ranking_v3_historical_gate_results(run)
        historical = ranking_v3.get("historical_validation")
        return (
            evidence.data_revision == ranking_v3_data_revision(run)
            and evidence.gate_results == expected_results
            and bool(expected_results)
            and all(status == "pass" for status in expected_results.values())
            and isinstance(historical, Mapping)
            and _text(historical.get("statistical_gate_status")) == "pass"
            and evidence.source_proof_digest == ranking_v3_historical_source_digest(run)
            and _same_timestamp(
                evidence.source_generated_at,
                getattr(run, "updated_at", None),
            )
        )

    def verify_pbo(
        self,
        identity: RankingV3ForwardIdentity,
        evidence: RankingV3PBOInput,
    ) -> bool:
        run = self.repository.get_walk_forward_run(evidence.validation_run_id)
        ranking_v3 = _ranking_v3_payload(run)
        if not _run_matches_identity(run, ranking_v3, identity):
            return False
        pbo = ranking_v3.get("pbo_evidence")
        verified_pbo = _recompute_pbo_evidence(pbo)
        return (
            isinstance(pbo, Mapping)
            and verified_pbo
            and evidence.data_revision == ranking_v3_data_revision(run)
            and str(evidence.probability) == str(pbo.get("probability"))
            and evidence.matrix_digest == _text(pbo.get("matrix_digest"))
            and evidence.fold_count == int(pbo.get("fold_count", 0))
            and evidence.method == _text(pbo.get("method"))
            and evidence.source_proof_digest == ranking_v3_pbo_source_digest(run)
            and _same_timestamp(
                evidence.source_generated_at,
                getattr(run, "updated_at", None),
            )
        )


def _run_matches_identity(
    run: object | None,
    ranking_v3: Mapping[str, object],
    identity: RankingV3ForwardIdentity,
) -> bool:
    if run is None or _text(getattr(run, "status", None)) != "succeeded":
        return False
    protocol = ranking_v3.get("protocol")
    manifest_payload = _experiment_manifest_payload(run)
    try:
        manifest = WalkForwardExperimentManifest.model_validate(manifest_payload)
    except (TypeError, ValueError):
        return False
    return (
        _text(ranking_v3.get("model_version")) == identity.model_version
        and isinstance(protocol, Mapping)
        and _text(protocol.get("protocol_id")) == identity.protocol_id
        and _text(protocol.get("protocol_digest")) == identity.protocol_digest
        and walk_forward_manifest_digest_is_valid(manifest)
        and manifest.ranking_v3_protocol_digest == identity.protocol_digest
        and manifest.research_source_digest not in {"", "unversioned"}
        and manifest.execution_rules_digest not in {"", "unversioned"}
        and manifest.strategy_registry_digest not in {"", "unversioned"}
    )


def _ranking_v3_payload(run: object | None) -> Mapping[str, object]:
    payload = getattr(run, "payload", None)
    ranking_v3 = payload.get("ranking_v3") if isinstance(payload, Mapping) else None
    return ranking_v3 if isinstance(ranking_v3, Mapping) else {}


def _experiment_manifest_payload(run: object | None) -> Mapping[str, object]:
    payload = getattr(run, "payload", None)
    manifest = payload.get("experiment_manifest") if isinstance(payload, Mapping) else None
    return manifest if isinstance(manifest, Mapping) else {}


def _recompute_pbo_evidence(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    matrix_payload = payload.get("model_return_matrix")
    if not isinstance(matrix_payload, Mapping):
        return False
    matrix: dict[str, list[RankingV3DatedModelReturn]] = {}
    try:
        for model_id, rows in matrix_payload.items():
            if not isinstance(model_id, str) or not isinstance(rows, list):
                return False
            matrix[model_id] = [
                RankingV3DatedModelReturn(
                    rebalance_date=date.fromisoformat(str(item["rebalance_date"])),
                    net_return=float(item["net_return"]),
                )
                for item in rows
                if isinstance(item, Mapping)
            ]
            if len(matrix[model_id]) != len(rows):
                return False
        recomputed = evaluate_ranking_v3_cscv_pbo(
            matrix,
            block_count=int(payload.get("block_count", 0)),
            purge_rebalance_cohorts=int(
                payload.get("purge_rebalance_cohorts", 0)
            ),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (
        recomputed.get("rejection_reason") is None
        and str(recomputed.get("probability")) == str(payload.get("probability"))
        and recomputed.get("matrix_digest") == payload.get("matrix_digest")
        and recomputed.get("fold_count") == payload.get("fold_count")
        and recomputed.get("method") == payload.get("method")
        and recomputed.get("model_count") == payload.get("model_count")
        and recomputed.get("date_count") == payload.get("date_count")
    )


def _same_timestamp(left: datetime, right: object) -> bool:
    return isinstance(right, datetime) and left == right


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
