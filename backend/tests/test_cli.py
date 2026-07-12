from datetime import date

from qagent.cli import main
from qagent.db import create_session_factory, initialize_database
from qagent.storage.repository import QagentRepository


class _WalkForwardCliResult:
    owner_run_id = "manual-validation-v1"
    provider_mode = "free"
    start_date = date(2023, 1, 3)
    end_date = date(2025, 12, 31)
    rebalance_step_sessions = 10
    top_5_metrics = type("Metrics", (), {"trade_count": 3, "total_return_pct": 4.25})()
    top_10_metrics = type("Metrics", (), {"trade_count": 5, "total_return_pct": 6.5})()
    dataset_revision = 7
    snapshots = [object(), object()]
    reproducibility_digest = "fixture-digest"
    top_5_portfolio = type(
        "Portfolio",
        (),
        {"summary": type("Summary", (), {"trade_count": 3, "total_return_pct": 4.25})()},
    )()
    top_10_portfolio = type(
        "Portfolio",
        (),
        {"summary": type("Summary", (), {"trade_count": 5, "total_return_pct": 6.5})()},
    )()
    data_health = {
        "walk_forward_lookback_days": "400",
        "walk_forward_top_5_oos_trades": "32",
        "walk_forward_top_10_oos_trades": "35",
        "walk_forward_stress_top_5_return_pct": "2.1",
        "walk_forward_stress_top_10_return_pct": "1.8",
        "walk_forward_equal_weight_benchmark": "ready",
    }

    def model_dump_json(self, indent=None):
        return '{"dataset_revision": 7}'

    def model_dump(self, mode=None):
        return {
            "owner_run_id": self.owner_run_id,
            "provider_mode": self.provider_mode,
            "dataset_revision": self.dataset_revision,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "rebalance_step_sessions": self.rebalance_step_sessions,
            "snapshots": [],
            "top_5_metrics": {"trade_count": 3, "total_return_pct": 4.25},
            "top_10_metrics": {"trade_count": 5, "total_return_pct": 6.5},
            "reproducibility_digest": self.reproducibility_digest,
            "data_health": self.data_health,
        }


def test_cli_daily_brief_can_save_queue_and_print_markdown(tmp_path, monkeypatch, capsys):
    database_url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)

    exit_code = main(
        [
            "daily-brief",
            "--provider",
            "fixture",
            "--no-news",
            "--save",
            "--queue",
            "--print-markdown",
        ]
    )
    output = capsys.readouterr().out
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))
    deliveries = repo.list_delivery_outbox(status="queued", limit=5)

    assert exit_code == 0
    assert "# Qagent Daily Brief" in output
    assert deliveries
    assert deliveries[0].status == "queued"
    assert deliveries[0].markdown.startswith("# Qagent Daily Brief")


def test_cli_send_outbox_writes_files_and_marks_sent(tmp_path, monkeypatch, capsys):
    database_url = f"sqlite:///{tmp_path / 'cli-send.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))
    delivery = repo.enqueue_delivery(
        subject="CLI Send",
        markdown="# CLI Send\n",
        channel="markdown",
        recipient="local",
    )

    exit_code = main(
        [
            "send-outbox",
            "--channel",
            "markdown",
            "--output-dir",
            str(tmp_path / "sent"),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert f"sent {delivery.delivery_id}" in output
    assert repo.list_delivery_outbox(status="sent", limit=5)[0].delivery_id == delivery.delivery_id
    assert list((tmp_path / "sent").glob("*.md"))


def test_cli_run_all_saves_research_artifacts(tmp_path, monkeypatch, capsys):
    database_url = f"sqlite:///{tmp_path / 'cli-run-all.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)

    exit_code = main(
        [
            "run-all",
            "--provider",
            "fixture",
            "--symbols",
            "US:TEST",
            "--no-news",
            "--queue-brief",
            "--run-backtest",
        ]
    )
    output = capsys.readouterr().out
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))

    assert exit_code == 0
    assert "automation provider=fixture symbols=1 cards=1" in output
    assert repo.list_scan_runs(limit=5)
    assert repo.list_brief_runs(limit=5)
    assert repo.list_delivery_outbox(status="queued", limit=5)


def test_cli_backfill_history_runs_bounded_fixture_job(tmp_path, monkeypatch, capsys):
    database_url = f"sqlite:///{tmp_path / 'cli-history.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)

    manifest_path = tmp_path / "history-manifest.json"
    exit_code = main(
        [
            "backfill-history",
            "--provider",
            "fixture",
            "--symbols",
            "CN:000001",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-09",
            "--batch-size",
            "25",
            "--commission-bps",
            "2.5",
            "--minimum-commission",
            "5",
            "--manifest-output",
            str(manifest_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "history-backfill status=succeeded" in output
    assert "symbols=1" in output
    assert "coverage=" in output
    assert '"provider_mode": "fixture"' in manifest_path.read_text()


def test_cli_backfill_history_reports_provider_failure_without_traceback(
    tmp_path,
    monkeypatch,
    capsys,
):
    class FailingEvidenceProvider:
        def get_evidence(self, instrument_ids, start, end):
            raise TimeoutError("provider deadline exceeded")

    database_url = f"sqlite:///{tmp_path / 'cli-history-failed.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setattr(
        "qagent.cli.build_historical_evidence_provider",
        lambda _mode: FailingEvidenceProvider(),
    )
    manifest_path = tmp_path / "failed-manifest.json"

    exit_code = main(
        [
            "backfill-history",
            "--provider",
            "fixture",
            "--symbols",
            "CN:000001",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-09",
            "--manifest-output",
            str(manifest_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "history-backfill status=failed" in output
    assert "provider deadline exceeded" in manifest_path.read_text()


def test_cli_walk_forward_runs_manually_and_exports_result(tmp_path, monkeypatch, capsys):
    database_url = f"sqlite:///{tmp_path / 'cli-walk-forward.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    captured = {}

    def fake_run(repository, **kwargs):
        captured.update(kwargs)
        captured["provider_mode"] = repository.provider_mode
        return _WalkForwardCliResult()

    monkeypatch.setattr("qagent.cli.run_full_market_walk_forward_selection", fake_run)
    output_path = tmp_path / "walk-forward.json"

    exit_code = main(
        [
            "walk-forward",
            "--start",
            "2023-01-03",
            "--end",
            "2025-12-31",
            "--step-sessions",
            "10",
            "--lookback-days",
            "400",
            "--run-id",
            "manual-validation-v1",
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured["provider_mode"] == "free"
    assert captured["owner_run_id"] == "manual-validation-v1"
    assert captured["rebalance_step_sessions"] == 10
    assert "top5_trades=3" in output
    assert "top5_oos=32/30" in output
    assert "stress_top5=2.1%" in output
    assert "equal_weight=ready" in output
    assert "persisted=manual-validation-v1" in output
    assert "digest=fixture-digest" in output
    assert output_path.read_text() == '{"dataset_revision": 7}'
