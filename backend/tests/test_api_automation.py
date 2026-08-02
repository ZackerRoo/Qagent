import json
from types import SimpleNamespace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import qagent.api.routes as routes
import qagent.jobs.automation as research_automation
from qagent.app import create_app
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.automation_scheduler import AutomationScheduler, AutoProcessingSettings
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import (
    FullMarketScanJobRow,
    OpportunitySnapshotRow,
    PaperTradeRow,
    ScanRunRow,
)


def test_automation_reuses_same_market_day_cache_after_ttl(monkeypatch):
    now = datetime.now(timezone.utc)
    stale_same_day = now - timedelta(hours=6)
    market_day = stale_same_day.astimezone(ZoneInfo("Asia/Shanghai")).date()
    cache = SimpleNamespace(created_at=stale_same_day)

    class StubRepo:
        def __init__(self):
            self.max_ages: list[timedelta] = []

        def get_recent_scan_result_cache(self, *, cache_key, max_age):
            assert cache_key == "full_market_batch:free:true"
            self.max_ages.append(max_age)
            return cache if max_age >= timedelta(hours=6) else None

    repo = StubRepo()
    monkeypatch.setattr(routes, "_a_share_today", lambda: market_day)

    result, freshness = routes._automation_scan_result_cache(
        repo,
        cache_key="full_market_batch:free:true",
        max_age=timedelta(hours=4),
    )

    assert result is cache
    assert freshness == "same_market_day"
    assert repo.max_ages == [timedelta(hours=4), timedelta(days=1)]


def test_latest_completed_a_share_session_changes_after_close(monkeypatch):
    sessions = [date(2026, 7, 30), date(2026, 7, 31)]
    monkeypatch.setattr(
        routes,
        "trading_sessions_in_range",
        lambda *_: sessions,
    )

    before_close = routes._latest_completed_a_share_session(
        datetime(2026, 7, 31, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    after_close = routes._latest_completed_a_share_session(
        datetime(2026, 7, 31, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert before_close == date(2026, 7, 30)
    assert after_close == date(2026, 7, 31)


def test_automatic_full_scan_is_deferred_during_market_session(monkeypatch):
    monkeypatch.setattr(
        routes,
        "trading_sessions_in_range",
        lambda *_: [date(2026, 7, 31)],
    )

    market_open = routes._automatic_full_scan_window(
        datetime(2026, 7, 31, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    settlement_window = routes._automatic_full_scan_window(
        datetime(2026, 7, 31, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    after_close = routes._automatic_full_scan_window(
        datetime(2026, 7, 31, 15, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert market_open == (False, "market_session_open")
    assert settlement_window == (False, "market_session_open")
    assert after_close == (True, "ready")


def test_stale_automatic_full_scan_restarts_same_job_from_checkpoints(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'stale-scan-resume.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))
    job = repo.create_full_market_scan_job(
        provider="free",
        symbols=["CN:000001", "CN:000002"],
        batch_size=1,
        include_etfs=True,
        sync_if_empty=False,
    )
    stale_at = datetime.now(timezone.utc) - timedelta(hours=2)
    with repo.session_factory() as session:
        session.execute(
            FullMarketScanJobRow.__table__.update()
            .where(FullMarketScanJobRow.job_id == job.job_id)
            .values(
                status="running",
                scanned_symbols=1,
                completed_batches=1,
                updated_at=stale_at,
            )
        )
        session.commit()
    terminated = []
    submitted = []
    monkeypatch.setattr(
        routes,
        "_terminate_full_market_executor",
        lambda: terminated.append(job.job_id) or True,
    )
    monkeypatch.setattr(
        routes,
        "_submit_full_market_scan_job",
        lambda job_id: submitted.append(job_id) or True,
    )

    status, started, job_id = routes._maybe_start_automatic_full_scan(
        repo,
        AutoProcessingSettings(
            provider="free",
            interval_seconds=1800,
            scan_max_age_minutes=240,
        ),
    )

    assert (status, started, job_id) == ("resumed_stale", True, job.job_id)
    assert terminated == [job.job_id]
    assert submitted == [job.job_id]
    resumed = repo.get_full_market_scan_job(job.job_id)
    assert resumed is not None
    assert resumed.status == "queued"
    assert resumed.completed_batches == 1
    assert resumed.data_health["full_market_restart_recovery"] == (
        "stale_checkpoint_resume"
    )


def test_automation_blocks_stale_signal_cache_without_signal_day_fallback():
    cache = SimpleNamespace(
        created_at=datetime.now(timezone.utc),
        payload={"data_health": {"full_market_signal_date": "2026-07-29"}},
    )

    class StubRepo:
        def get_recent_scan_result_cache(self, *, cache_key, max_age):
            assert cache_key == "full_market_batch:free:true"
            return cache

        def list_latest_signal_opportunity_snapshots(self, **_):
            raise AssertionError("stale signal-day fallback must remain disabled")

    snapshots, health = routes._paper_seed_snapshots_from_recommendations(
        StubRepo(),
        mode="free",
        include_etfs=True,
        max_age=timedelta(hours=4),
        limit=5,
        expected_signal_date=date(2026, 7, 30),
    )

    assert snapshots == []
    assert health["paper_candidate_freshness_gate"] == "blocked"
    assert health["paper_candidate_expected_signal_date"] == "2026-07-30"
    assert health["automation_seed_cache_freshness"] == "stale_signal_retry_window"


def test_research_automation_passes_authoritative_repository_to_seed(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'research-automation-admission.db'}"
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))
    captured: dict[str, object] = {}

    def fake_seed(paper_repo, snapshots, **kwargs):
        captured["paper_repo"] = paper_repo
        captured["snapshots"] = snapshots
        captured.update(kwargs)
        return SimpleNamespace(created=0)

    monkeypatch.setattr(
        research_automation,
        "seed_paper_trades_from_snapshots",
        fake_seed,
    )

    research_automation.run_research_automation(
        repo=repo,
        provider=FixtureMarketDataProvider(),
        provider_mode="fixture",
        symbols=["US:TEST"],
        include_news=False,
        queue_brief=False,
        run_alerts=False,
        run_backtest=False,
        seed_paper=True,
        update_paper=False,
    )

    assert captured["admission_repo"] is repo
    assert len(captured["snapshots"]) == 1


def test_paper_candidate_requires_latest_price_for_entry_validation():
    snapshot = SimpleNamespace(
        trigger_price=Decimal("2.90"),
        latest_close=None,
        card={},
    )
    assert (
        routes._paper_candidate_price_basis_is_consistent(
            snapshot,
            latest_value=None,
        )
        is False
    )


def test_automation_cycle_publishes_post_cycle_risk_gate(monkeypatch):
    paper_repo = SimpleNamespace(list_trades=lambda **_: [])
    gates = iter(
        [
            (
                False,
                {
                    "paper_risk_gate_action": "capacity_full",
                    "paper_risk_gate_reason": "本轮开始时仓位已满",
                    "paper_risk_gate_max_new_entries": "0",
                },
            ),
            (
                True,
                {
                    "paper_risk_gate_action": "throttle_new_entries",
                    "paper_risk_gate_reason": "更新后释放一个名额",
                    "paper_risk_gate_max_new_entries": "1",
                },
            ),
        ]
    )
    monkeypatch.setattr(routes, "_repo", lambda: SimpleNamespace())
    monkeypatch.setattr(routes, "_paper_repo", lambda: paper_repo)
    monkeypatch.setattr(routes, "_paper_seed_risk_gate", lambda *_: next(gates))

    result = routes._run_auto_processing_cycle(
        AutoProcessingSettings(
            run_scan=False,
            seed_paper=False,
            update_paper=False,
            run_alerts=False,
        )
    )

    assert result.data_health["paper_risk_gate_action"] == "throttle_new_entries"
    assert result.data_health["paper_risk_gate_max_new_entries"] == "1"
    assert result.data_health["paper_risk_gate_applied_action"] == "capacity_full"
    assert result.data_health["paper_risk_gate_applied_max_new_entries"] == "0"


def test_paper_candidate_recovers_latest_price_from_card():
    snapshot = SimpleNamespace(
        latest_close=None,
        card={"trading_status": {"latest_close": "2.92"}},
    )

    assert routes._paper_snapshot_latest_value(snapshot) == Decimal("2.92")
    assert routes._paper_card_latest_value(snapshot.card) == Decimal("2.92")


def test_replacement_candidate_is_seeded_before_other_waiting_items():
    snapshots = [
        SimpleNamespace(instrument_id="CN:159560"),
        SimpleNamespace(instrument_id="CN:588080"),
        SimpleNamespace(instrument_id="CN:159599"),
    ]

    ordered = routes._prioritize_paper_replacement_candidate(
        snapshots,
        "CN:588080",
    )

    assert [snapshot.instrument_id for snapshot in ordered] == [
        "CN:588080",
        "CN:159560",
        "CN:159599",
    ]


def test_automation_run_api_saves_brief_and_queues_delivery(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'automation.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    client = TestClient(create_app())

    response = client.post(
        "/api/automation/run?provider=fixture&symbols=US:TEST&include_news=false&queue_brief=true&run_backtest=true"
    )
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["provider"] == "fixture"
    assert body["summary"]["cards"] == 1
    assert body["scan_run_id"].startswith("scan-")
    assert body["brief_id"].startswith("brief-")
    assert body["brief_delivery_id"].startswith("delivery-")
    assert body["backtest"]["summary"]["evaluated_signals"] >= 1
    assert repo.list_scan_runs(limit=5)
    assert repo.list_brief_runs(limit=5)
    assert repo.list_delivery_outbox(status="queued", limit=5)


def test_automation_scheduler_run_once_updates_paper_status(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'automation-scheduler-run-once.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    client = TestClient(create_app())
    card = client.get("/api/opportunities?provider=fixture&symbols=US:TEST").json()[
        "cards"
    ][0]
    created = client.post(
        "/api/paper-trades/from-opportunity",
        json={
            "card_id": card["card_id"],
            "provider": "fixture",
            "instrument_id": card["instrument_id"],
            "strategy_id": card["primary_strategy_id"],
            "trigger_price": card["entry_plan"]["trigger_price"],
            "initial_stop": card["exit_plan"]["initial_stop"],
            "target_1": card["exit_plan"]["target_1"],
            "rank_score": card["rank_score"],
            "action": "watch_trigger",
            "risk_status": "clear",
        },
    )
    assert created.status_code == 200

    response = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=fixture&symbols=US:TEST&run_scan=false&run_alerts=false"
        "&seed_paper=false&update_paper=true"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["last_result"]["provider"] == "fixture"
    assert body["last_result"]["paper_total"] == 1
    assert body["last_result"]["paper_closed"] >= 0
    assert body["run_count"] == 1
    assert body["next_run_at"] is None


def test_automation_scheduler_seeds_latest_signal_day_not_latest_inserted_rows(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'automation-latest-signal.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setattr(routes, "_automation_scheduler", AutomationScheduler())
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                ScanRunRow(
                    run_id="scan-latest-signal",
                    provider="free",
                    mode="full_market",
                    symbols=json.dumps(["CN:588850", "CN:588190"]),
                    scanned=2,
                    cards=2,
                    data_health="{}",
                    created_at=now - timedelta(minutes=10),
                ),
                ScanRunRow(
                    run_id="scan-old-inserted-last",
                    provider="free",
                    mode="full_market",
                    symbols=json.dumps(["CN:589300", "CN:589630"]),
                    scanned=2,
                    cards=2,
                    data_health="{}",
                    created_at=now,
                ),
            ]
        )
        for index, (run_id, instrument_id, signal_date, rank_score, created_at) in enumerate(
            [
                (
                    "scan-latest-signal",
                    "CN:588850",
                    date(2026, 7, 1),
                    Decimal("0.91"),
                    now - timedelta(minutes=10),
                ),
                (
                    "scan-latest-signal",
                    "CN:588190",
                    date(2026, 7, 1),
                    Decimal("0.88"),
                    now - timedelta(minutes=10),
                ),
                (
                    "scan-old-inserted-last",
                    "CN:589300",
                    date(2026, 6, 26),
                    Decimal("0.99"),
                    now,
                ),
                (
                    "scan-old-inserted-last",
                    "CN:589630",
                    date(2026, 6, 26),
                    Decimal("0.98"),
                    now,
                ),
            ],
            start=1,
        ):
            session.add(
                OpportunitySnapshotRow(
                    snapshot_id=f"{run_id}:card-{index}",
                    run_id=run_id,
                    card_id=f"card-{index}",
                    instrument_id=instrument_id,
                    market="CN",
                    status="setup_ready",
                    signal_date=signal_date,
                    latest_close=Decimal("2.00"),
                    primary_strategy_id="sector_rotation_relative_strength",
                    score=rank_score,
                    strategy_score=rank_score,
                    rank_score=rank_score,
                    trigger_price=Decimal("2.10"),
                    initial_stop=Decimal("1.95"),
                    target_1=Decimal("2.40"),
                    card_json=json.dumps(
                        {
                            "instrument_id": instrument_id,
                            "instrument_label": instrument_id,
                        },
                        sort_keys=True,
                    ),
                    created_at=created_at,
                )
            )
        session.commit()

    client = TestClient(create_app())
    response = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=free&run_scan=false&run_alerts=false&update_paper=false"
        "&seed_paper=true&seed_limit=2"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["last_result"]["paper_created"] == 2
    assert body["last_result"]["data_health"]["automation_seed_latest_signal_date"] == "2026-07-01"

    trades = client.get(
        "/api/paper-trades?limit=10&reporting_scope=legacy"
    ).json()["trades"]
    assert [trade["instrument_id"] for trade in trades] == ["CN:588190", "CN:588850"]
    assert {trade["signal_date"] for trade in trades} == {"2026-07-01"}


def test_automation_scheduler_seeds_from_cached_recommendation_order(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'automation-cache-recommendations.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setattr(routes, "_automation_scheduler", AutomationScheduler())
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    repo = QagentRepository(session_factory)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            ScanRunRow(
                run_id="scan-cache-seed",
                provider="free",
                mode="full_market",
                symbols=json.dumps(["CN:002747", "CN:688052", "CN:588850", "CN:588190"]),
                scanned=4,
                cards=4,
                data_health="{}",
                created_at=now,
            )
        )
        for index, (card_id, instrument_id, rank_score, factor_score, strategy_score) in enumerate(
            [
                ("card-stock-estun", "CN:002747", Decimal("0.62"), 0.66, 1.0),
                ("card-stock-naxin", "CN:688052", Decimal("0.64"), 0.69, 0.88),
                ("card-etf-machine", "CN:588850", Decimal("0.99"), 0.57, 0.82),
                ("card-etf-100", "CN:588190", Decimal("0.98"), 0.60, 0.86),
            ],
            start=1,
        ):
            card_payload = {
                "card_id": card_id,
                "instrument_id": instrument_id,
                "instrument_label": instrument_id,
                "rank_score": float(rank_score),
                "factor_score": factor_score,
                "strategy_score": strategy_score,
                "decision": {"risk_status": "warning", "components": {}},
            }
            session.add(
                OpportunitySnapshotRow(
                    snapshot_id=f"scan-cache-seed:card-{index}",
                    run_id="scan-cache-seed",
                    card_id=card_id,
                    instrument_id=instrument_id,
                    market="CN",
                    status="setup_ready",
                    signal_date=date(2026, 7, 1),
                    latest_close=Decimal("10.00"),
                    primary_strategy_id="trend_momentum_stage2",
                    score=rank_score,
                    strategy_score=Decimal(str(strategy_score)),
                    rank_score=rank_score,
                    trigger_price=Decimal("10.20"),
                    initial_stop=Decimal("9.80"),
                    target_1=Decimal("11.00"),
                    card_json=json.dumps(card_payload, sort_keys=True),
                    created_at=now,
                )
            )
        session.commit()
    repo.save_scan_result_cache(
        cache_key="full_market_batch:free:true",
        provider="free",
        mode="full_market_batch",
        symbols=["CN:002747", "CN:688052", "CN:588850", "CN:588190"],
        payload={
            "symbols": ["CN:002747", "CN:688052", "CN:588850", "CN:588190"],
            "cards": [
                {
                    "card_id": "card-stock-estun",
                    "instrument_id": "CN:002747",
                    "rank_score": 0.62,
                    "factor_score": 0.66,
                    "strategy_score": 1.0,
                    "decision": {"risk_status": "warning", "components": {}},
                    "entry_plan": {"trigger_price": "10.20"},
                },
                {
                    "card_id": "card-stock-naxin",
                    "instrument_id": "CN:688052",
                    "rank_score": 0.64,
                    "factor_score": 0.69,
                    "strategy_score": 0.88,
                    "decision": {"risk_status": "warning", "components": {}},
                    "entry_plan": {"trigger_price": "10.20"},
                },
                {
                    "card_id": "card-etf-machine",
                    "instrument_id": "CN:588850",
                    "rank_score": 0.99,
                    "factor_score": 0.1,
                    "strategy_score": 0.1,
                    "decision": {"risk_status": "warning", "components": {}},
                    "entry_plan": {"trigger_price": "10.20"},
                },
                {
                    "card_id": "card-etf-100",
                    "instrument_id": "CN:588190",
                    "rank_score": 0.98,
                    "factor_score": 0.1,
                    "strategy_score": 0.1,
                    "decision": {"risk_status": "warning", "components": {}},
                    "entry_plan": {"trigger_price": "10.20"},
                },
            ],
            "items": [],
            "strategy_health": [],
            "factor_rankings": [],
            "sector_strength": [],
            "portfolio_plan": {"profile": "balanced"},
            "data_health": {"provider": "free"},
        },
    )

    client = TestClient(create_app())
    response = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=free&include_etfs=true&run_scan=false&run_alerts=false"
        "&update_paper=false&seed_paper=true&seed_limit=2"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["last_result"]["paper_created"] == 2
    assert (
        body["last_result"]["data_health"]["automation_seed_source"]
        == "latest_recommendation_cache"
    )

    trades = client.get(
        "/api/paper-trades?limit=10&reporting_scope=legacy"
    ).json()["trades"]
    assert {trade["instrument_id"] for trade in trades} == {"CN:002747", "CN:688052"}
    assert {trade["signal_date"] for trade in trades} == {
        datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    }

    with session_factory() as session:
        session.add(
            ScanRunRow(
                run_id="scan-cache-seed-second",
                provider="free",
                mode="full_market",
                symbols=json.dumps(["CN:002747", "CN:688052"]),
                scanned=2,
                cards=2,
                data_health="{}",
                created_at=now + timedelta(minutes=1),
            )
        )
        for index, (card_id, instrument_id, rank_score, factor_score, strategy_score) in enumerate(
            [
                ("card-stock-estun-new", "CN:002747", Decimal("0.63"), 0.66, 1.0),
                ("card-stock-naxin-new", "CN:688052", Decimal("0.65"), 0.69, 0.88),
            ],
            start=1,
        ):
            session.add(
                OpportunitySnapshotRow(
                    snapshot_id=f"scan-cache-seed-second:card-{index}",
                    run_id="scan-cache-seed-second",
                    card_id=card_id,
                    instrument_id=instrument_id,
                    market="CN",
                    status="setup_ready",
                    signal_date=date(2026, 7, 1),
                    latest_close=Decimal("10.00"),
                    primary_strategy_id="trend_momentum_stage2",
                    score=rank_score,
                    strategy_score=Decimal(str(strategy_score)),
                    rank_score=rank_score,
                    trigger_price=Decimal("10.20"),
                    initial_stop=Decimal("9.80"),
                    target_1=Decimal("11.00"),
                    card_json=json.dumps(
                        {
                            "card_id": card_id,
                            "instrument_id": instrument_id,
                            "rank_score": float(rank_score),
                            "factor_score": factor_score,
                            "strategy_score": strategy_score,
                            "decision": {"risk_status": "warning", "components": {}},
                        },
                        sort_keys=True,
                    ),
                    created_at=now + timedelta(minutes=1),
                )
            )
        session.commit()
    repo.save_scan_result_cache(
        cache_key="full_market_batch:free:true",
        provider="free",
        mode="full_market_batch",
        symbols=["CN:002747", "CN:688052"],
        payload={
            "symbols": ["CN:002747", "CN:688052"],
            "cards": [
                {
                    "card_id": "card-stock-estun-new",
                    "instrument_id": "CN:002747",
                    "rank_score": 0.63,
                    "factor_score": 0.66,
                    "strategy_score": 1.0,
                    "decision": {"risk_status": "warning", "components": {}},
                    "entry_plan": {"trigger_price": "10.20"},
                },
                {
                    "card_id": "card-stock-naxin-new",
                    "instrument_id": "CN:688052",
                    "rank_score": 0.65,
                    "factor_score": 0.69,
                    "strategy_score": 0.88,
                    "decision": {"risk_status": "warning", "components": {}},
                    "entry_plan": {"trigger_price": "10.20"},
                },
            ],
            "items": [],
            "strategy_health": [],
            "factor_rankings": [],
            "sector_strength": [],
            "portfolio_plan": {"profile": "balanced"},
            "data_health": {"provider": "free"},
        },
    )

    second = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=free&include_etfs=true&run_scan=false&run_alerts=false"
        "&update_paper=false&seed_paper=true&seed_limit=2"
    )

    assert second.status_code == 200
    second_body = second.json()
    assert second_body["last_result"]["paper_created"] == 0
    assert second_body["last_result"]["paper_total"] == 2


def test_automation_scheduler_backfills_closed_paper_slot_from_deeper_cache_candidates(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'automation-cache-backfill.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setattr(routes, "_automation_scheduler", AutomationScheduler())
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    repo = QagentRepository(session_factory)
    now = datetime.now(timezone.utc)
    candidates = [
        ("card-a", "CN:688001", Decimal("0.90"), 1.0, 1.0),
        ("card-b", "CN:688002", Decimal("0.88"), 0.98, 0.98),
        ("card-c", "CN:688003", Decimal("0.86"), 0.96, 0.96),
    ]
    with session_factory() as session:
        session.add(
            ScanRunRow(
                run_id="scan-cache-backfill",
                provider="free",
                mode="full_market",
                symbols=json.dumps([instrument_id for _, instrument_id, *_ in candidates]),
                scanned=len(candidates),
                cards=len(candidates),
                data_health="{}",
                created_at=now,
            )
        )
        for index, (card_id, instrument_id, rank_score, factor_score, strategy_score) in enumerate(
            candidates,
            start=1,
        ):
            card_payload = {
                "card_id": card_id,
                "instrument_id": instrument_id,
                "rank_score": float(rank_score),
                "factor_score": factor_score,
                "strategy_score": strategy_score,
                "decision": {"risk_status": "clear", "components": {}},
            }
            session.add(
                OpportunitySnapshotRow(
                    snapshot_id=f"scan-cache-backfill:card-{index}",
                    run_id="scan-cache-backfill",
                    card_id=card_id,
                    instrument_id=instrument_id,
                    market="CN",
                    status="setup_ready",
                    signal_date=date(2026, 7, 1),
                    latest_close=Decimal("10.00"),
                    primary_strategy_id="trend_momentum_stage2",
                    score=rank_score,
                    strategy_score=Decimal(str(strategy_score)),
                    rank_score=rank_score,
                    trigger_price=Decimal("10.20"),
                    initial_stop=Decimal("9.80"),
                    target_1=Decimal("11.00"),
                    card_json=json.dumps(card_payload, sort_keys=True),
                    created_at=now,
                )
            )
        session.commit()
    repo.save_scan_result_cache(
        cache_key="full_market_batch:free:true",
        provider="free",
        mode="full_market_batch",
        symbols=[instrument_id for _, instrument_id, *_ in candidates],
        payload={
            "symbols": [instrument_id for _, instrument_id, *_ in candidates],
            "cards": [
                {
                    "card_id": card_id,
                    "instrument_id": instrument_id,
                    "rank_score": float(rank_score),
                    "factor_score": factor_score,
                    "strategy_score": strategy_score,
                    "decision": {"risk_status": "clear", "components": {}},
                    "entry_plan": {"trigger_price": "10.20"},
                }
                for card_id, instrument_id, rank_score, factor_score, strategy_score in candidates
            ],
            "items": [],
            "strategy_health": [],
            "factor_rankings": [],
            "sector_strength": [],
            "portfolio_plan": {"profile": "balanced"},
            "data_health": {"provider": "free"},
        },
    )

    client = TestClient(create_app())
    first = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=free&include_etfs=true&run_scan=false&run_alerts=false"
        "&update_paper=false&seed_paper=true&seed_limit=2"
    )
    assert first.status_code == 200
    assert first.json()["last_result"]["paper_created"] == 2

    with session_factory() as session:
        first_trade = (
            session.query(PaperTradeRow).filter(PaperTradeRow.instrument_id == "CN:688001").one()
        )
        first_trade.status = "missed_entry"
        session.commit()

    second = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=free&include_etfs=true&run_scan=false&run_alerts=false"
        "&update_paper=false&seed_paper=true&seed_limit=2"
    )

    assert second.status_code == 200
    assert second.json()["last_result"]["paper_created"] == 1
    trades = client.get(
        "/api/paper-trades?limit=10&reporting_scope=legacy"
    ).json()["trades"]
    active = {trade["instrument_id"] for trade in trades if trade["status"] in {"pending", "open"}}
    assert active == {"CN:688002", "CN:688003"}


def test_automation_scheduler_allows_one_recovery_probe_when_ledger_drawdown_is_high(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'automation-risk-pause.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setattr(routes, "_automation_scheduler", AutomationScheduler())
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            ScanRunRow(
                run_id="scan-risk-pause",
                provider="free",
                mode="full_market",
                symbols=json.dumps(["CN:688999"]),
                scanned=1,
                cards=1,
                data_health="{}",
                created_at=now,
            )
        )
        session.add(
            OpportunitySnapshotRow(
                snapshot_id="scan-risk-pause:card-1",
                run_id="scan-risk-pause",
                card_id="card-risk-pause",
                instrument_id="CN:688999",
                market="CN",
                status="setup_ready",
                signal_date=date(2026, 7, 7),
                latest_close=Decimal("10.00"),
                primary_strategy_id="trend_momentum_stage2",
                score=Decimal("0.95"),
                strategy_score=Decimal("0.95"),
                rank_score=Decimal("0.95"),
                trigger_price=Decimal("10.20"),
                initial_stop=Decimal("9.80"),
                target_1=Decimal("11.00"),
                card_json=json.dumps(
                    {
                        "card_id": "card-risk-pause",
                        "instrument_id": "CN:688999",
                        "entry_plan": {"trigger_price": "10.20"},
                        "decision": {"risk_status": "clear"},
                    },
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
        for index in range(6):
            session.add(
                PaperTradeRow(
                    trade_id=f"paper-risk-loss-{index}",
                    source_snapshot_id=f"manual-risk-loss-{index}",
                    provider="free",
                    instrument_id=f"CN:68800{index}",
                    strategy_id="trend_momentum_stage2",
                    status="stopped",
                    signal_date=date(2026, 7, 1),
                    trigger_price=Decimal("100"),
                    initial_stop=Decimal("95"),
                    target_1=Decimal("110"),
                    rank_score=Decimal("0.80"),
                    entry_date=date(2026, 7, 2),
                    entry_price=Decimal("100"),
                    exit_date=date(2026, 7, 3),
                    exit_price=Decimal("95"),
                    latest_date=date(2026, 7, 3),
                    latest_price=Decimal("95"),
                    realized_return_pct=Decimal("-5"),
                    holding_days=1,
                    notes="测试止损",
                )
            )
        session.commit()

    client = TestClient(create_app())
    response = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=free&include_etfs=true&run_scan=false&run_alerts=false"
        "&update_paper=false&seed_paper=true&seed_limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    health = body["last_result"]["data_health"]
    assert body["last_result"]["paper_created"] == 1
    assert health["paper_risk_gate_action"] == "throttle_new_entries"
    assert health["paper_risk_gate_max_new_entries"] == "1"
    assert health["paper_risk_gate_position_size_multiplier"] == "0.3500"
    trades = client.get(
        "/api/paper-trades?provider=free&limit=20&reporting_scope=legacy"
    ).json()["trades"]
    probe = next(trade for trade in trades if trade["instrument_id"] == "CN:688999")
    assert "风控恢复探针" in probe["notes"]

    second = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=free&include_etfs=true&run_scan=false&run_alerts=false"
        "&update_paper=false&seed_paper=true&seed_limit=1"
    )
    assert second.status_code == 200
    second_result = second.json()["last_result"]
    assert second_result["paper_created"] == 0
    assert second_result["data_health"]["paper_risk_gate_action"] == "throttle_new_entries"
    assert "automation_seed_skipped_by_risk_gate" not in second_result["data_health"]


def test_automation_scheduler_replaces_stale_pending_with_strong_candidate(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'automation-replacement.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setattr(routes, "_automation_scheduler", AutomationScheduler())
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    now = datetime.now(timezone.utc)
    today = routes._a_share_today()
    with session_factory() as session:
        session.add(
            ScanRunRow(
                run_id="scan-replacement",
                provider="free",
                mode="full_market",
                symbols=json.dumps(["CN:588000", "CN:588770", "CN:159558"]),
                scanned=3,
                cards=3,
                data_health="{}",
                created_at=now,
            )
        )
        session.add(
            OpportunitySnapshotRow(
                snapshot_id="scan-replacement:588000",
                run_id="scan-replacement",
                card_id="card-588000",
                instrument_id="CN:588000",
                market="CN",
                status="setup_ready",
                signal_date=date(2026, 7, 9),
                latest_close=Decimal("2.20"),
                primary_strategy_id="trend_momentum_stage2",
                score=Decimal("0.94"),
                strategy_score=Decimal("0.96"),
                rank_score=Decimal("0.93"),
                trigger_price=Decimal("2.22"),
                initial_stop=Decimal("2.10"),
                target_1=Decimal("2.45"),
                card_json=json.dumps(
                    {
                        "card_id": "card-588000",
                        "instrument_id": "CN:588000",
                        "instrument_label": "科创50ETF华夏 588000.SH",
                        "entry_plan": {"trigger_price": "2.22"},
                        "decision": {"risk_status": "clear", "action": "watch_trigger"},
                    },
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
        session.add(
            OpportunitySnapshotRow(
                snapshot_id="scan-replacement:588770",
                run_id="scan-replacement",
                card_id="card-588770",
                instrument_id="CN:588770",
                market="CN",
                status="setup_ready",
                signal_date=today,
                latest_close=Decimal("1.20"),
                primary_strategy_id="trend_momentum_stage2",
                score=Decimal("0.99"),
                strategy_score=Decimal("0.99"),
                rank_score=Decimal("0.99"),
                trigger_price=None,
                initial_stop=None,
                target_1=None,
                card_json=json.dumps(
                    {
                        "card_id": "card-588770",
                        "instrument_id": "CN:588770",
                        "instrument_label": "科创信息ETF摩根 588770.SH",
                        "entry_plan": {},
                        "decision": {"risk_status": "clear", "action": "watch_trigger"},
                    },
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
        session.add(
            OpportunitySnapshotRow(
                snapshot_id="scan-replacement:159558",
                run_id="scan-replacement",
                card_id="card-159558",
                instrument_id="CN:159558",
                market="CN",
                status="setup_ready",
                signal_date=date(2026, 7, 9),
                latest_close=Decimal("4.15"),
                primary_strategy_id="trend_momentum_stage2",
                score=Decimal("0.99"),
                strategy_score=Decimal("0.99"),
                rank_score=Decimal("0.99"),
                trigger_price=Decimal("4.15"),
                initial_stop=Decimal("3.98"),
                target_1=Decimal("4.49"),
                card_json=json.dumps(
                    {
                        "card_id": "card-159558",
                        "instrument_id": "CN:159558",
                        "instrument_label": "半导体设备ETF易方达 159558.SZ",
                        "entry_plan": {"trigger_price": "4.15"},
                        "decision": {"risk_status": "clear", "action": "watch_trigger"},
                    },
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
        for index in range(3):
            session.add(
                PaperTradeRow(
                    trade_id=f"paper-risk-loss-{index}",
                    source_snapshot_id=f"manual-risk-loss-{index}",
                    provider="free",
                    instrument_id=f"CN:68810{index}",
                    strategy_id="trend_momentum_stage2",
                    status="stopped",
                    signal_date=date(2026, 7, 1),
                    trigger_price=Decimal("100"),
                    initial_stop=Decimal("95"),
                    target_1=Decimal("110"),
                    rank_score=Decimal("0.80"),
                    entry_date=date(2026, 7, 2),
                    entry_price=Decimal("100"),
                    exit_date=date(2026, 7, 3),
                    exit_price=Decimal("95"),
                    latest_date=date(2026, 7, 3),
                    latest_price=Decimal("95"),
                    realized_return_pct=Decimal("-5"),
                    holding_days=1,
                    notes="测试止损",
                )
            )
        stale_pending = PaperTradeRow(
            trade_id="paper-stale-pending",
            source_snapshot_id="manual-stale-pending",
            provider="free",
            instrument_id="CN:159558",
            strategy_id="trend_momentum_stage2",
            status="pending",
            signal_date=today,
            trigger_price=Decimal("4.15"),
            initial_stop=Decimal("3.98"),
            target_1=Decimal("4.49"),
            rank_score=Decimal("0.75"),
            latest_date=today,
            latest_price=Decimal("1.35"),
            notes="等待触发",
        )
        open_trade = PaperTradeRow(
            trade_id="paper-open-keep",
            source_snapshot_id="manual-open-keep",
            provider="free",
            instrument_id="CN:560180",
            strategy_id="trend_momentum_stage2",
            status="open",
            signal_date=date(2026, 7, 4),
            trigger_price=Decimal("1.24"),
            initial_stop=Decimal("1.18"),
            target_1=Decimal("1.36"),
            rank_score=Decimal("0.72"),
            entry_date=date(2026, 7, 7),
            entry_price=Decimal("1.24"),
            latest_date=date(2026, 7, 9),
            latest_price=Decimal("1.25"),
            unrealized_return_pct=Decimal("0.8"),
            holding_days=2,
            notes="持仓观察",
        )
        extra_pending = [
            PaperTradeRow(
                trade_id=f"paper-extra-pending-{index}",
                source_snapshot_id=f"manual-extra-pending-{index}",
                provider="free",
                instrument_id=f"CN:58819{index}",
                strategy_id="trend_momentum_stage2",
                status="pending",
                signal_date=date(2026, 7, 7),
                trigger_price=Decimal("2.20"),
                initial_stop=Decimal("2.10"),
                target_1=Decimal("2.40"),
                rank_score=Decimal("0.74"),
                latest_date=date(2026, 7, 9),
                latest_price=Decimal("2.15"),
                notes="等待触发",
            )
            for index in range(3)
        ]
        session.add_all([stale_pending, open_trade, *extra_pending])
        session.commit()

    client = TestClient(create_app())
    response = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=free&include_etfs=true&run_scan=false&run_alerts=false"
        "&update_paper=false&seed_paper=true&seed_limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    health = body["last_result"]["data_health"]
    assert body["last_result"]["paper_created"] == 1
    assert health["paper_replacement_action"] == "replaced_pending"
    assert health["paper_replacement_candidate"] == "CN:588000"
    assert health["paper_candidate_pool_waiting_count"] == "1"
    pool_response = client.get(
        "/api/paper-trades/candidate-pool?provider=free&include_etfs=true&limit=10"
    )
    assert pool_response.status_code == 200
    pool = pool_response.json()
    assert pool["summary"]["active_count"] == 5
    assert pool["summary"]["market_adaptive_action"] == "theme_boost_enabled"
    assert any(
        item["instrument_id"] == "CN:588000"
        and item["status"] == "active_in_paper"
        and item["market_theme_boost"] > 0
        for item in pool["items"]
    )
    post_stale_item = next(item for item in pool["items"] if item["instrument_id"] == "CN:159558")
    assert post_stale_item["status"] == "blocked_by_data"
    assert post_stale_item["price_basis_consistent"] is False
    trades = client.get(
        "/api/paper-trades?provider=free&limit=20&reporting_scope=legacy"
    ).json()["trades"]
    by_id = {trade["trade_id"]: trade for trade in trades}
    assert by_id["paper-stale-pending"]["status"] == "replaced"
    assert "候补替换" in by_id["paper-stale-pending"]["notes"]
    assert "CN:588000" in {trade["instrument_id"] for trade in trades}


def test_automation_scheduler_start_and_stop_are_visible(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'automation-scheduler-start.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    client = TestClient(create_app())

    started = client.post(
        "/api/automation/scheduler/start"
        "?provider=fixture&symbols=US:TEST&interval_seconds=60&run_scan=false"
        "&run_alerts=false&seed_paper=false&update_paper=true"
    )
    state = client.get("/api/automation/scheduler")
    stopped = client.post("/api/automation/scheduler/stop")

    assert started.status_code == 200
    assert started.json()["enabled"] is True
    assert started.json()["settings"]["interval_seconds"] == 60
    assert started.json()["next_run_at"] is not None
    assert state.status_code == 200
    assert state.json()["enabled"] is True
    assert stopped.status_code == 200
    assert stopped.json()["enabled"] is False
    assert stopped.json()["next_run_at"] is None


def test_automation_scheduler_restores_enabled_state_after_app_restart(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'automation-scheduler-restore.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setattr(routes, "_automation_scheduler", AutomationScheduler())

    with TestClient(create_app()) as client:
        started = client.post(
            "/api/automation/scheduler/start"
            "?provider=fixture&symbols=US:TEST&interval_seconds=900"
            "&run_scan=false&seed_paper=false&update_paper=false"
            "&run_alerts=false&queue_alerts=false"
        )
        assert started.status_code == 200
        assert started.json()["enabled"] is True

    monkeypatch.setattr(routes, "_automation_scheduler", AutomationScheduler())
    with TestClient(create_app()) as restarted_client:
        restored = restarted_client.get("/api/automation/scheduler")

    assert restored.status_code == 200
    body = restored.json()
    assert body["enabled"] is True
    assert body["settings"]["provider"] == "fixture"
    assert body["settings"]["symbols"] == "US:TEST"
    assert body["settings"]["interval_seconds"] == 900
    assert body["settings"]["run_scan"] is False
    assert body["settings"]["seed_paper"] is False
    assert body["settings"]["update_paper"] is False
    assert body["next_run_at"] is not None


def test_automation_scheduler_restores_stopped_state_after_app_restart(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'automation-scheduler-stop-restore.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setattr(routes, "_automation_scheduler", AutomationScheduler())

    with TestClient(create_app()) as client:
        started = client.post(
            "/api/automation/scheduler/start"
            "?provider=fixture&symbols=US:TEST&interval_seconds=900"
            "&run_scan=false&seed_paper=false&update_paper=false"
            "&run_alerts=false&queue_alerts=false"
        )
        stopped = client.post("/api/automation/scheduler/stop")
        assert started.status_code == 200
        assert stopped.status_code == 200
        assert stopped.json()["enabled"] is False

    monkeypatch.setattr(routes, "_automation_scheduler", AutomationScheduler())
    with TestClient(create_app()) as restarted_client:
        restored = restarted_client.get("/api/automation/scheduler")

    assert restored.status_code == 200
    body = restored.json()
    assert body["enabled"] is False
    assert body["settings"]["provider"] == "fixture"
    assert body["settings"]["symbols"] == "US:TEST"
    assert body["next_run_at"] is None


def test_automation_scheduler_state_runs_overdue_cycle(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'automation-scheduler-overdue.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    scheduler = AutomationScheduler()
    overdue_at = datetime.now(timezone.utc) - timedelta(minutes=45)
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._status = "idle"
        scheduler._settings = AutoProcessingSettings(
            provider="fixture",
            symbols="US:TEST",
            interval_seconds=60,
            run_scan=False,
            seed_paper=False,
            update_paper=False,
            run_alerts=False,
        )
        scheduler._next_run_at = overdue_at
    monkeypatch.setattr(routes, "_automation_scheduler", scheduler)
    client = TestClient(create_app())

    response = client.get("/api/automation/scheduler")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["run_count"] == 1
    assert body["last_completed_at"] is not None
    assert datetime.fromisoformat(body["next_run_at"]) > datetime.now(timezone.utc)
