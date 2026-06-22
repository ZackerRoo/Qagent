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
