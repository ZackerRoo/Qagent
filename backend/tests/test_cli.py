from qagent.cli import main
from qagent.db import create_session_factory, initialize_database
from qagent.storage.repository import QagentRepository


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
