from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from qagent.backtesting.a_share_rules import RULES_PATH, load_a_share_rule_schedule
from qagent.strategies.registry import default_strategy_registry


EXPERIMENT_SCHEMA_VERSION = "walk-forward-experiment-v1"
SELECTION_ALGORITHM_VERSION = "historical-shadow-recommendation-v1"


class WalkForwardExperimentManifest(BaseModel):
    schema_version: str
    experiment_digest: str
    created_at: datetime
    code_revision: str
    code_dirty: bool
    python_version: str
    provider_mode: str
    dataset_revision: int
    start_date: date
    end_date: date
    rebalance_step_sessions: int
    lookback_days: int
    selection_algorithm_version: str
    strategy_registry_digest: str
    strategy_ids: list[str]
    execution_rule_set_version: str
    fee_schedule_version: str
    execution_rules_digest: str


def build_walk_forward_experiment_manifest(
    *,
    provider_mode: str,
    dataset_revision: int,
    start_date: date,
    end_date: date,
    rebalance_step_sessions: int,
    lookback_days: int,
) -> WalkForwardExperimentManifest:
    registry_payload = sorted(
        (
            definition.model_dump(mode="json")
            for definition in default_strategy_registry().all()
        ),
        key=lambda item: item["strategy_id"],
    )
    registry_digest = _digest(registry_payload)
    schedule = load_a_share_rule_schedule()
    rules_digest = hashlib.sha256(RULES_PATH.read_bytes()).hexdigest()
    revision, dirty = _git_revision()
    stable_payload = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "code_revision": revision,
        "provider_mode": provider_mode,
        "dataset_revision": dataset_revision,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "rebalance_step_sessions": rebalance_step_sessions,
        "lookback_days": lookback_days,
        "selection_algorithm_version": SELECTION_ALGORITHM_VERSION,
        "strategy_registry_digest": registry_digest,
        "execution_rule_set_version": schedule.rule_set_version,
        "fee_schedule_version": schedule.fee_schedule_version,
        "execution_rules_digest": rules_digest,
    }
    return WalkForwardExperimentManifest(
        **stable_payload,
        experiment_digest=_digest(stable_payload),
        created_at=datetime.now(timezone.utc),
        code_dirty=dirty,
        python_version=platform.python_version(),
        strategy_ids=[item["strategy_id"] for item in registry_payload],
    )


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_revision() -> tuple[str, bool]:
    repository_root = Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=repository_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        )
        return revision, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", False
