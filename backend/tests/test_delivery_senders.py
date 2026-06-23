from qagent.delivery.senders import send_pending_deliveries
from qagent.storage.repository import QagentRepository

from test_state_repository import make_repo


def test_send_pending_deliveries_writes_markdown_files_and_marks_sent(tmp_path):
    repo = make_repo(tmp_path)
    delivery = repo.enqueue_delivery(
        subject="Qagent Alerts: 1 triggered",
        markdown="# Alert\n\n- US:TEST triggered\n",
        channel="markdown",
        recipient="local",
        payload={"kind": "alert_run"},
    )

    result = send_pending_deliveries(
        repo=repo,
        output_dir=tmp_path / "outbox",
        channel="markdown",
    )
    sent = repo.list_delivery_outbox(status="sent", limit=5)
    files = list((tmp_path / "outbox").glob("*.md"))

    assert result.sent == 1
    assert result.failed == 0
    assert result.items[0].delivery_id == delivery.delivery_id
    assert sent[0].delivery_id == delivery.delivery_id
    assert files
    assert files[0].read_text().startswith("# Alert")


def test_send_pending_deliveries_dry_run_does_not_mark_sent(tmp_path):
    repo: QagentRepository = make_repo(tmp_path)
    repo.enqueue_delivery(
        subject="Dry Run",
        markdown="# Dry Run\n",
        channel="markdown",
        recipient="local",
    )

    result = send_pending_deliveries(
        repo=repo,
        output_dir=tmp_path / "outbox",
        channel="markdown",
        dry_run=True,
    )

    assert result.sent == 0
    assert result.dry_run == 1
    assert repo.list_delivery_outbox(status="queued", limit=5)
