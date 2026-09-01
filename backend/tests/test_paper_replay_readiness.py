from datetime import datetime, timedelta, timezone

from qagent.paper_trading.replay_readiness import build_execution_replay_readiness
from qagent.storage.paper import PaperReplayEvidenceAuditRecord

from test_paper_replay_evidence import OCCURRED_AT, _evidence


def _record(index: int, *, phase: str, **evidence_kwargs) -> PaperReplayEvidenceAuditRecord:
    occurred_at = OCCURRED_AT + timedelta(minutes=index)
    evidence = _evidence(phase=phase, occurred_at=occurred_at, **evidence_kwargs)
    return PaperReplayEvidenceAuditRecord(
        audit_digest=f"{index + 1:064x}",
        event_id=f"event-{index}",
        trade_id=f"trade-{index}",
        occurred_at=occurred_at,
        evidence=evidence,
    )


def _matched_records(buys: int, sells: int) -> list[PaperReplayEvidenceAuditRecord]:
    return [
        *[_record(index, phase="entry") for index in range(buys)],
        *[_record(100 + index, phase="exit") for index in range(sells)],
    ]


def test_replay_readiness_zero_samples_collects_without_promotion():
    readiness = build_execution_replay_readiness(
        [],
        generated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert readiness.gate == "collecting"
    assert readiness.progress_pct == 0
    assert readiness.automatic_promotion is False
    assert readiness.paper_ledger_mutated is False
    assert "等待新成交自然积累" in readiness.reason


def test_replay_readiness_requires_five_buy_and_three_sell_exact_matches():
    ready = build_execution_replay_readiness(_matched_records(5, 3))
    collecting = build_execution_replay_readiness(_matched_records(4, 3))

    assert ready.gate == "ready_for_shadow"
    assert ready.progress_pct == 100
    assert collecting.gate == "collecting"
    assert collecting.progress_pct == 87.5


def test_replay_readiness_blocks_unknown_or_build_failure_and_explained_is_not_matched():
    records = _matched_records(5, 3)
    records.append(_record(200, phase="entry", commission="6.00"))
    records.append(_record(201, phase="entry", expected_price="10.01"))
    records.append(
        PaperReplayEvidenceAuditRecord(
            audit_digest="f" * 64,
            event_id="event-build-failure",
            trade_id="trade-build-failure",
            occurred_at=OCCURRED_AT,
            issue_code="replay_evidence_status:build_failed_trade_continued",
            issue_detail="fixture",
        )
    )

    readiness = build_execution_replay_readiness(records)

    assert readiness.gate == "blocked"
    assert readiness.buy.matched == 5
    assert readiness.buy.explained_difference == 1
    assert readiness.buy.unknown == 1
    assert readiness.unknown_count == 2
    assert readiness.audit_build_failures == 1
    assert readiness.automatic_promotion is False
